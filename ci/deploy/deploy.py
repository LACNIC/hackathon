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
API_REGISTRO_VERSION_FILE = "/opt/api-registro-v4/deployed-version.txt"


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


def compose_command(directory: str, profile: str, registry: str, app: str, tag: str, action: str) -> str:
    variables = ""
    if profile == "api-registro":
        variables = " ".join(f"{name}={shlex.quote(value)}" for name, value in
                             (("REGISTRY", registry), ("APPNAME", app), ("VERSION", tag))) + " "
    return f"cd {shlex.quote(directory)} && {variables}docker compose -f docker-compose.yml {action}"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def ready(url: str) -> bool:
    request = Request(url)
    try:
        with build_opener(NoRedirect()).open(request, timeout=8) as response:
            return 200 <= response.status < 300
    except HTTPError as error:
        error.close()
        return False
    except Exception:
        return False


def verify_release(target: str, directory: str, profile: str, registry: str, app: str,
                   tag: str, health_url: str) -> None:
    service = app if profile == "api-registro" else "app"
    version_file = API_REGISTRO_VERSION_FILE if profile == "api-registro" else STANDARD_VERSION_FILE
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    while True:
        try:
            if ready(health_url):
                command = compose_command(directory, profile, registry, app, tag,
                                          f"exec -T {shlex.quote(service)} cat {shlex.quote(version_file)}")
                remaining = deadline - time.monotonic()
                if remaining > 0 and remote(target, command, capture=True,
                                            timeout=min(10, remaining)).strip() == tag:
                    return
        except DeployError:
            pass  # Container may still be starting; retry within the bound.
        if time.monotonic() >= deadline:
            raise DeployError("readiness and deployed version did not match before timeout")
        time.sleep(min(READINESS_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))


def deploy(registry: str, app: str, environment: str, target: str, workspace: Path) -> str:
    required(registry, "registry", r"[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9_.-]+)+")
    required(app, "appname", r"[A-Za-z0-9][A-Za-z0-9_.-]*")
    required(environment, "environment", r"[A-Za-z0-9][A-Za-z0-9_.-]*")
    required(target, "remote target", r"(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9.-]+")
    config = config_file(workspace)
    profile = "api-registro" if app == "api-registro-v4" else "standard"
    health_url = endpoint(os.environ.get("DEPLOY_HEALTH_URL"), "DEPLOY_HEALTH_URL",
                          https_only=environment == "prod")
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
    if profile == "api-registro" and not (workspace / "lib" / "xml-serializer-wildfly.jar").is_file():
        raise DeployError("API Registro xml-serializer-wildfly.jar is missing")
    tag = f"{environment}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    directory = f"/usr/local/properties/{app}"
    print(f"Deploying {app} {tag} to {target}", flush=True)
    (workspace / "dockers" / "deployed-version.txt").write_text(tag + "\n", encoding="utf-8")
    run("docker", "volume", "create", "maven-repo")
    maven = ["docker", "run", "--rm", "--name", f"maven-{app}-{uuid.uuid4().hex[:8]}"]
    if profile == "api-registro":
        maven += ["--security-opt", "seccomp=unconfined"]
    maven += ["-v", "maven-repo:/root/.m2", "-v", f"{workspace}:/app", "-w", "/app"]
    image = "maven:3.9.9-eclipse-temurin-21-jammy" if profile == "api-registro" else "maven:3.9.9-eclipse-temurin-17-focal"
    if profile == "api-registro":
        maven += [image, "sh", "-lc", "mvn install:install-file -Dfile=lib/xml-serializer-wildfly.jar -DgroupId=net.lacnic -DartifactId=xml-serializer-wildfly -Dversion=1.0.0 -Dpackaging=jar && mvn clean package -DskipTests"]
    else:
        maven += [image, "mvn", "clean", "package", "-DskipTests"]
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
    if profile == "api-registro":
        remote(target, f"mkdir -p {shlex.quote(directory + '/logs')} {shlex.quote(directory + '/epp')} && (test -f {shlex.quote(directory + '/.env')} || touch {shlex.quote(directory + '/.env')})")
    run("scp", "dockers/docker-compose.yml", f"{target}:{directory}/docker-compose.yml")
    expected = precise if profile == "api-registro" else alias
    images = remote(target, compose_command(directory, profile, registry, app, tag, "config --images"), capture=True).splitlines()
    if expected not in images:
        raise DeployError("remote Compose VERSION/image does not match the expected deployment alias")
    remote(target, f"docker pull {shlex.quote(precise)}")
    remote(target, compose_command(directory, profile, registry, app, tag, "pull"))
    precise_id = remote(target, f"docker image inspect {shlex.quote(precise)} --format '{{{{.Id}}}}'", capture=True)
    expected_id = remote(target, f"docker image inspect {shlex.quote(expected)} --format '{{{{.Id}}}}'", capture=True)
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
        remote(target, compose_command(directory, profile, registry, app, tag, "down"),
               timeout=DOWN_TIMEOUT_SECONDS)
        remote(target, compose_command(directory, profile, registry, app, tag, "up -d"),
               timeout=UP_TIMEOUT_SECONDS)
        verify_release(target, directory, profile, registry, app, tag, health_url)
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
