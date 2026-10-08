#!/usr/bin/env python3
"""Build, deploy and verify one consumer with a bounded Kuma maintenance window."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kuma"))
from kuma_client import KumaError, from_environment
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify import deployment_config


class DeployError(Exception):
    pass


WINDOW_MARGIN_SECONDS = 120
KUMA_ACTIVATION_BUDGET_SECONDS = 240
DOWN_TIMEOUT_SECONDS = 120
UP_TIMEOUT_SECONDS = 180
READINESS_TIMEOUT_SECONDS = 180
READINESS_INTERVAL_SECONDS = 5
KUMA_DURATION_MINUTES = 30
STANDARD_VERSION_FILE = "/opt/jboss/wildfly/standalone/configuration/deployed-version.txt"


def required(value, name, pattern=None):
    if not isinstance(value, str) or not value or (pattern and not re.fullmatch(pattern, value)):
        raise DeployError(f"invalid {name}")
    return value


def config_file(workspace: Path) -> dict | None:
    try:
        return deployment_config(workspace / "ci")
    except ValueError as error:
        raise DeployError(str(error)) from None


def endpoint(value: str | None, name: str, *, https_only: bool = False) -> str:
    parsed = urlsplit(required(value, name))
    if (parsed.scheme not in ({"https"} if https_only else {"http", "https"}) or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        scheme = "HTTPS" if https_only else "HTTP(S)"
        raise DeployError(f"{name} must be an {scheme} URL without credentials or query")
    return value


def run(*args: str, capture: bool = False, timeout: float | None = None) -> str:
    try:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE if capture else None,
                                stderr=subprocess.PIPE if capture else None, check=False,
                                timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DeployError(f"command timed out: {args[0]}") from None
    if result.returncode:
        raise DeployError(f"command failed: {args[0]} (exit {result.returncode})")
    return result.stdout.strip() if capture else ""


def remote(target: str, command: str, capture: bool = False,
           timeout: float | None = None) -> str:
    return run("ssh", target, command, capture=capture, timeout=timeout)


def compose_command(directory: str, action: str) -> str:
    return f"cd {shlex.quote(directory)} && docker compose -f docker-compose.yml {action}"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def ready(url: str, token: str, tag: str, timeout: float) -> bool:
    request = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            if response.status != 200:
                return False
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                return False
            service = json.loads(body).get("service", {})
            return (service.get("availability") == "UP"
                    and service.get("deployedVersion") == tag)
    except HTTPError as error:
        error.close()
        return False
    except Exception:
        return False


def verify_release(target: str, directory: str, tag: str,
                   status_url: str | None, monitor_token: str | None) -> None:
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    while True:
        try:
            command = compose_command(directory,
                                      f"exec -T app cat {shlex.quote(STANDARD_VERSION_FILE)}")
            remaining = deadline - time.monotonic()
            if remaining > 0 and remote(target, command, capture=True,
                                        timeout=min(10, remaining)).strip() == tag:
                remaining = deadline - time.monotonic()
                if status_url is None or (remaining > 0 and ready(status_url, monitor_token, tag,
                                                                   min(8, remaining))):
                    return
        except DeployError:
            pass  # Container may still be starting; retry within the bound.
        if time.monotonic() >= deadline:
            raise DeployError("deployed version or monitored status did not match before timeout")
        time.sleep(min(READINESS_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))


def deploy(registry: str, app: str, environment: str, target: str, workspace: Path) -> str:
    required(registry, "registry", r"[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9_.-]+)+")
    required(app, "appname", r"[A-Za-z0-9][A-Za-z0-9_.-]*")
    required(environment, "environment", r"[A-Za-z0-9][A-Za-z0-9_.-]*")
    required(target, "remote target", r"(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9.-]+")
    if app == "api-registro-v4":
        raise DeployError("API Registro uses dockers/docker-jenkins.sh; its new deploy is a separate task")
    config = config_file(workspace)
    if os.environ.get("DEPLOY_HEALTH_URL"):
        raise DeployError("DEPLOY_HEALTH_URL is obsolete; use DEPLOY_STATUS_URL with /status/items")
    status_url = os.environ.get("DEPLOY_STATUS_URL") or None
    monitor_token = os.environ.get("DEPLOY_MONITOR_TOKEN") or None
    if status_url:
        endpoint(status_url, "DEPLOY_STATUS_URL", https_only=environment == "prod")
        parsed_status = urlsplit(status_url)
        if parsed_status.scheme == "http" and parsed_status.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise DeployError("DEPLOY_STATUS_URL must use HTTPS outside loopback")
        if not parsed_status.path.endswith("/status/items"):
            raise DeployError("DEPLOY_STATUS_URL must end in /status/items")
        if not monitor_token:
            raise DeployError("DEPLOY_MONITOR_TOKEN is required with DEPLOY_STATUS_URL")
    elif monitor_token:
        raise DeployError("DEPLOY_STATUS_URL is required with DEPLOY_MONITOR_TOKEN")
    use_kuma = environment == "prod" and config is not None
    if use_kuma:
        kuma_url = endpoint(os.environ.get("KUMA_URL"), "KUMA_URL", https_only=True)
        if not os.environ.get("KUMA_USERNAME") or not os.environ.get("KUMA_PASSWORD"):
            raise DeployError("Jenkins Kuma username/password credentials are required")
        required_window_seconds = (DOWN_TIMEOUT_SECONDS + UP_TIMEOUT_SECONDS
                                   + READINESS_TIMEOUT_SECONDS + WINDOW_MARGIN_SECONDS
                                   + KUMA_ACTIVATION_BUDGET_SECONDS)
        if KUMA_DURATION_MINUTES * 60 < required_window_seconds:
            raise DeployError("Kuma duration must cover deployment and verification")
    if not (workspace / "dockers" / "Dockerfile").is_file() or not (workspace / "dockers" / "docker-compose.yml").is_file():
        raise DeployError("Dockerfile or Compose file is missing")
    tag = f"{environment}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    directory = f"/usr/local/properties/{app}"
    print(f"Deploying {app} {tag} to {target}", flush=True)
    (workspace / "dockers" / "deployed-version.txt").write_text(tag + "\n", encoding="utf-8")
    run("docker", "volume", "create", "maven-repo")
    maven = ["docker", "run", "--rm", "--name", f"maven-{app}-{uuid.uuid4().hex[:8]}"]
    maven += ["-v", "maven-repo:/root/.m2", "-v", f"{workspace}:/app", "-w", "/app"]
    maven += ["maven:3.9.9-eclipse-temurin-17-focal", "mvn", "clean", "package", "-DskipTests"]
    run(*maven)
    registry_host = registry.split("/", 1)[0]
    run("docker", "login", f"https://{registry_host}/")
    precise = f"{registry}/{app}:{tag}"
    alias = f"{registry}/{app}:{environment}"
    run("docker", "build", "-f", "dockers/Dockerfile", "--tag", precise, ".")
    run("docker", "tag", precise, alias)
    run("docker", "push", precise)
    run("docker", "push", alias)
    remote(target, f"mkdir -p {shlex.quote(directory)}")
    run("scp", "dockers/docker-compose.yml", f"{target}:{directory}/docker-compose.yml")
    images = remote(target, compose_command(directory, "config --images"), capture=True).splitlines()
    if alias not in images:
        raise DeployError("remote Compose VERSION/image does not match the expected deployment alias")
    remote(target, f"docker pull {shlex.quote(precise)}")
    remote(target, compose_command(directory, "pull"))
    precise_id = remote(target, f"docker image inspect {shlex.quote(precise)} --format '{{{{.Id}}}}'", capture=True)
    expected_id = remote(target, f"docker image inspect {shlex.quote(alias)} --format '{{{{.Id}}}}'", capture=True)
    if not precise_id or precise_id != expected_id:
        raise DeployError("remote image digest differs from the precise tag")
    attempt = uuid.uuid4().hex
    title = f"AI Harness {app} {environment} {attempt}"
    maintenance_id = None
    if use_kuma:
        kuma_cfg = config["kuma"]
        print(f"Requesting Kuma window: {title}", flush=True)
        client = from_environment(kuma_url)
        window_requested_at = time.monotonic()
        maintenance_id = client.begin(kuma_cfg["groupId"], kuma_cfg["groupPath"], title,
                                      KUMA_DURATION_MINUTES)
        print(f"Kuma maintenance {maintenance_id} active; stopping services", flush=True)
    try:
        if use_kuma:
            remaining = KUMA_DURATION_MINUTES * 60 - (time.monotonic() - window_requested_at)
            needed = (DOWN_TIMEOUT_SECONDS + UP_TIMEOUT_SECONDS
                      + READINESS_TIMEOUT_SECONDS + WINDOW_MARGIN_SECONDS)
            if remaining < needed:
                raise DeployError("Kuma activation took too long; refusing to interrupt services")
        remote(target, compose_command(directory, "down"),
               timeout=DOWN_TIMEOUT_SECONDS)
        remote(target, compose_command(directory, "up -d"),
               timeout=UP_TIMEOUT_SECONDS)
        verify_release(target, directory, tag, status_url, monitor_token)
    except Exception:
        if maintenance_id is not None:
            print(f"Deployment failed; maintenance {maintenance_id} remains until its automatic expiry", file=sys.stderr)
        raise
    if maintenance_id is not None:
        from_environment(kuma_url).end(maintenance_id, kuma_cfg["groupId"], title)
        print(f"Deploy verified; Kuma maintenance {maintenance_id} closed", flush=True)
    else:
        print("Deploy verified", flush=True)
    return tag


def main() -> int:
    if len(sys.argv) != 5:
        print("usage: docker-jenkins-harness.sh <registry> <appname> <environment> <remote>", file=sys.stderr)
        return 2
    workspace = Path(os.environ.get("WORKSPACE", os.getcwd())).resolve()
    try:
        os.chdir(workspace)
        deploy(*sys.argv[1:], workspace)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, DeployError, KumaError) as exc:
        print(f"deployment failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
