#!/usr/bin/env python3
"""Serve only the versioned public website, never the repository itself."""
import argparse
import io
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "hackathon-local"
CONTAINER = PROJECT + "-app"
IMAGE = "lacnic-local/hackathon:dev"
BASE = "nginx:1.28-alpine"
PUBLIC_ROOTS = {"2017", "2019", "2024", "2025", "2026", "17 MVD"}
EXTENSIONS = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".pptx", ".key", ".woff", ".woff2", ".ttf"}


def run(args, *, capture=False, **kwargs):
    env = {k: v for k, v in os.environ.items() if not k.startswith("HACKATHON_")}
    env["HACKATHON_PROJECT_ROOT"] = str(ROOT)
    return subprocess.run(args, cwd=ROOT, env=env, check=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None, **kwargs)


def harness(action, *args):
    result = run(["python3", "-B", str(ROOT / "ai-harness/harness/local_runtime.py"), action,
                  "--project-root", str(ROOT), "--config", "dockers/local-runtime.json", *args], capture=True)
    output = result.stdout.decode().strip()
    if output.startswith("{"):
        return json.loads(output)
    print(output, flush=True)


def compose(*args):
    return run(["docker", "compose", "-p", PROJECT, "-f", "dockers/docker-compose.local.yml", *args])


def public_files(root=ROOT):
    root = root.resolve()
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    result = []
    for name in tracked:
        path = PurePosixPath(name)
        if not name or any(part.startswith(".") for part in path.parts):
            continue
        if name != "index.html" and (path.parts[0] not in PUBLIC_ROOTS or path.suffix.lower() not in EXTENSIONS):
            continue
        source = root / name
        if any(parent.is_symlink() for parent in [source, *source.parents] if parent != root.parent):
            raise RuntimeError("Symlink rechazado en contenido público: " + name)
        if not source.is_file() or not source.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("Archivo público ausente o fuera del proyecto: " + name)
        result.append(name)
    if "index.html" not in result:
        raise RuntimeError("Falta index.html público versionado.")
    return sorted(result)


def public_digest(root=ROOT):
    digest = hashlib.sha256()
    for name in public_files(root):
        digest.update(name.encode() + b"\0")
        file_hash = hashlib.sha256()
        with (root / name).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                file_hash.update(chunk)
        digest.update(file_hash.digest())
    return digest.hexdigest()


def reconcile_public_image(decision, image, digest):
    labels = (image or {}).get("Config", {}).get("Labels") or {}
    if decision["action"] == "reuse" and labels.get("ai.harness.public-content") != digest:
        return {**decision, "action": "fresh", "reason": "public-file-selection-or-content-changed"}
    return decision


def write_context(output, root=ROOT):
    with tarfile.open(fileobj=output, mode="w|") as bundle:
        for relative in public_files(root):
            # Explicit regular bytes avoid tar following symlinks or adding directories.
            source = root / relative
            if source.is_symlink():
                raise RuntimeError("Symlink público rechazado.")
            content = source.read_bytes()
            info = tarfile.TarInfo("site/" + relative)
            info.size, info.mode = len(content), 0o644
            bundle.addfile(info, io.BytesIO(content))
        for relative, target in [("dockers/Dockerfile.local", "Dockerfile"), ("dockers/nginx.conf", "nginx.conf")]:
            source = root / relative
            if source.is_symlink():
                raise RuntimeError("Symlink de infraestructura rechazado.")
            content = source.read_bytes()
            info = tarfile.TarInfo(target)
            info.size, info.mode = len(content), 0o644
            bundle.addfile(info, io.BytesIO(content))


def inspect(kind, name):
    try:
        return json.loads(run(["docker", kind, "inspect", name], capture=True).stdout)[0]
    except subprocess.CalledProcessError:
        return None


def assert_ownership():
    ids = run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT], capture=True).stdout.decode().split()
    resources = [("container", value) for value in set(ids + [CONTAINER])]
    resources.append(("network", PROJECT + "-network"))
    for kind, name in resources:
        info = inspect(kind, name)
        if info is None:
            continue
        labels = info.get("Config", info).get("Labels") or {}
        if labels.get("ai.harness.project-root") != str(ROOT) or labels.get("com.docker.compose.project") != PROJECT:
            raise RuntimeError("Ownership ajeno; no se modificó " + name)
        if kind == "container" and labels.get("com.docker.compose.service") != "app":
            raise RuntimeError("Servicio ajeno bajo etiquetas Compose; no se modificó.")


def validate():
    harness("validate", "--check-files")
    names = public_files()
    print("Sitio estático válido:", len(names), "archivos públicos; no requiere Java, base ni secretos.")


def request(path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = "http://127.0.0.1:8107" + urllib.parse.quote(path, safe="/%")
    try:
        with opener.open(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def check_site():
    positive = ["/index.html", "/2026/es/index.html", "/2026/en/index.html", "/2026/pt/index.html",
                "/2026/es/", "/2017/es/index.html", "/2019/es/index.html", "/2024/es/index.html", "/2025/es/index.html",
                "/2026/css/hackathon.css", "/2026/img/hackathon-2026.svg", "/2026/img/favicon.png",
                "/2019/Memorias-HCK-2019_Optimize.pdf", "/17 MVD/diapositivas/resultados/API para MiLACNIC.pdf"]
    for path in positive:
        status, body = request(path)
        relative = path.lstrip("/") + ("index.html" if path.endswith("/") else "")
        if status != 200 or body != (ROOT / relative).read_bytes():
            raise RuntimeError("Contenido HTTP distinto del sitio versionado: " + path)
    root_status, root_body = request("/")
    if root_status != 200 or root_body != (ROOT / "index.html").read_bytes():
        raise RuntimeError("Root HTTP no coincide con index.html.")
    negative = ["/.git/config", "/%2egit/config", "/.env", "/ai-harness/AGENTS.md",
                "/ai-harness-local/feature_list.json", "/dockers/nginx.conf", "/AGENTS.md", "/CNAME",
                "/17 MVD/entrebagles/RIPE Atlas/flask_server.py", "/2026/img/", "/missing.html",
                "/2026/../.git/config", "/2026/%2e%2e/.git/config"]
    for path in negative:
        status, _ = request(path)
        if status != 404:
            raise RuntimeError("Ruta privada, listing o ausente no devuelve 404: " + path)
    print(json.dumps({"publicPathsExact": len(positive) + 1, "privateOrMissing404": len(negative), "languages2026": ["es", "en", "pt"]}), flush=True)


def up(reset=False):
    validate()
    run(["docker", "info"], capture=True)
    assert_ownership()
    if inspect("container", CONTAINER) is None:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", 8107))
    decision = harness("decide", *(["--require-clean"] if reset else []))
    digest = public_digest()
    image = inspect("image", IMAGE)
    decision = reconcile_public_image(decision, image, digest)
    print("Decisión:", json.dumps(decision), flush=True)
    if decision["action"] == "fresh":
        harness("mark-initializing")
    if not image or decision["action"] == "fresh":
        if not inspect("image", BASE):
            raise RuntimeError("Falta imagen base declarada: aprovisionar " + BASE + " antes de up.")
        with tempfile.TemporaryFile() as context:
            write_context(context)
            context.seek(0)
            run(["docker", "build", "--pull=false", "--label", "ai.harness.public-content=" + digest,
                 "--label", "ai.harness.project-root=" + str(ROOT), "-t", IMAGE, "-"], stdin=context)
    if decision["action"] == "fresh":
        compose("down", "--timeout", "10")
    compose("up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "45")
    deadline = time.monotonic() + 45
    while True:
        try:
            check_site()
            break
        except (OSError, RuntimeError) as error:
            if time.monotonic() >= deadline:
                raise RuntimeError("Readiness estático falló: " + str(error))
            time.sleep(1)
    harness("mark-ready", "--readiness-confirmed")
    print("Hackathon listo: http://127.0.0.1:8107/")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate", "up", "down", "logs", "reset", "check"])
    action = parser.parse_args().action
    if action == "validate":
        validate()
    elif action in ("up", "reset"):
        up(reset=action == "reset")
    elif action == "check":
        check_site()
    else:
        assert_ownership()
        compose(*(["down", "--timeout", "10"] if action == "down" else ["logs", "--no-color", "--tail", "80"]))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit("ERROR: " + str(error))
