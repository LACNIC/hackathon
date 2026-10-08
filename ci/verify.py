#!/usr/bin/env python3
"""Verify every installed CI component and its registered Newman collections."""

from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path

FILES = {
    "README.md", "verify.py", "deploy/docker-jenkins-harness.sh", "deploy/deploy.py",
    "kuma/kuma_client.py", "newman/ci_collection.py", "newman/http_evidence.py",
    "newman/postman_internal.py", "newman/postman_remote.py",
    "newman/postman_vault.py", "newman/publication_contract.py",
    "newman/state_root.py", "newman/ws-ci", "newman/run-ci-tests.sh",
}
EXCLUDED = {".git", "ai-harness", "ai-harness-local", "target", "node_modules", "ci"}


def collections_for(project: Path) -> dict[str, str]:
    collections = {}
    for path in project.rglob("postman/collection.json"):
        relative = path.relative_to(project)
        if any(part in EXCLUDED for part in relative.parts):
            continue
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            raise ValueError(f"linked or invalid Newman collection: {relative}")
        collections[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(collections.items()))


def verify(root: Path) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("CI directory is missing or linked")
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("CI manifest is missing or linked")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digests = manifest.get("files")
    collections = manifest.get("collections")
    if (manifest.get("schemaVersion") != 1 or not isinstance(digests, dict)
            or set(digests) != FILES or not isinstance(collections, dict)):
        raise ValueError("invalid CI manifest")
    content = {"files": digests, "collections": collections}
    version = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    if manifest.get("bundleVersion") != version:
        raise ValueError("invalid CI bundle version")
    for name, digest in digests.items():
        path = root / name
        if (not isinstance(digest, str) or len(digest) != 64 or path.is_symlink()
                or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError(f"missing, linked or modified CI file: {name}")
    for name in ("deploy", "kuma", "newman"):
        directory = root / name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"missing or linked CI directory: {name}")
    config = root / "deploy/config.json"
    if config.is_symlink() or (config.exists() and not config.is_file()):
        raise ValueError("linked or invalid deployment configuration")
    actual = {str(path.relative_to(root)) for path in root.rglob("*")
              if path.is_file() or path.is_symlink()}
    directories = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_dir()}
    allowed = FILES | {"manifest.json"} | ({"deploy/config.json"} if config.exists() else set())
    if actual != allowed or directories != {"deploy", "kuma", "newman"}:
        raise ValueError("unexpected or missing CI files")
    for name in ("deploy/docker-jenkins-harness.sh", "newman/run-ci-tests.sh", "newman/ws-ci"):
        if not stat.S_IMODE((root / name).stat().st_mode) & 0o111:
            raise ValueError(f"CI entrypoint is not executable: {name}")
    for name, digest in collections.items():
        relative = Path(name)
        if (relative.is_absolute() or ".." in relative.parts or relative.name != "collection.json"
                or relative.parent.name != "postman" or not isinstance(digest, str) or len(digest) != 64):
            raise ValueError("invalid Newman collection entry")
    if collections_for(root.parent) != collections:
        raise ValueError("Newman collections changed after CI installation")
    return manifest


def main() -> int:
    try:
        manifest = verify(Path(__file__).resolve().parent)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"CI verification failed: {error}", file=sys.stderr)
        return 2
    print(f"CI runtime OK: {manifest['bundleVersion'][:12]} ({len(manifest['files'])} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
