"""Encrypted repository secrets used to derive private Postman environments.

The committed GPG file contains values only; versioned profiles remain the
source of variable names, URLs, types, and Postman environment identities.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
from pathlib import Path

from postman_internal import VAULT_REFERENCE, load_profiles
from publication_contract import default_tests_enabled
from state_root import resolve_state_root


VAULT_NAME = re.compile(r"[A-Za-z0-9_.-]+")
SCHEMA_VERSION = 1


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _checked_path(root: Path, path: Path, parent: Path) -> Path:
    root = root.resolve()
    path = path if path.is_absolute() else root / path
    parent = parent.resolve()
    if not _inside(path, parent) or path.is_symlink() or path.resolve() != path:
        raise ValueError("Vault path must be a regular path in its expected directory")
    return path


def _ignored(root: Path, path: Path) -> None:
    result = subprocess.run(["git", "check-ignore", "-q", "--", str(path)],
                            cwd=root, capture_output=True, check=False)
    if result.returncode != 0:
        raise ValueError("Private vault key or environment output must be ignored by Git")


def _private_path(root: Path, path: Path, state_root: Path | None = None) -> Path:
    state = resolve_state_root(root, state_root)
    result = _checked_path(state, path, state / "ai-harness-local")
    _ignored(state, result)
    return result


def _cipher_path(root: Path, path: Path) -> Path:
    return _checked_path(root, path, root / "postman")


def _env_key(path: Path, *, allow_missing: bool = False) -> str | None:
    if not path.exists() and allow_missing:
        return None
    if not path.is_file() or path.is_symlink():
        raise ValueError("Local vault environment is missing or is not a regular file")
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Local vault environment must be owned by the user with mode 0600")
    matches = [line.split("=", 1)[1] for line in path.read_text(encoding="utf-8").splitlines()
               if line.startswith("POSTMAN_VAULT_KEY=")]
    if not matches and allow_missing:
        return None
    if len(matches) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{64}", matches[0]):
        raise ValueError("POSTMAN_VAULT_KEY is missing, duplicated, or invalid")
    return matches[0]


def _gpg(operation: str, key: str, payload: bytes) -> bytes:
    # A short, disposable homedir avoids Unix socket path limits on macOS and
    # keeps user GPG configuration and agent state out of the operation.
    short_tmp = "/private/tmp" if Path("/private/tmp").is_dir() else "/tmp"
    try:
        with tempfile.TemporaryDirectory(prefix="pv-gpg-", dir=short_tmp) as home:
            passphrase_fd, writer_fd = os.pipe()
            args = ["gpg", "--homedir", home, "--batch", "--yes", "--quiet", "--no-tty",
                    "--pinentry-mode", "loopback", "--passphrase-fd", str(passphrase_fd),
                    "--output", "-"]
            if operation == "encrypt":
                args += ["--symmetric", "--cipher-algo", "AES256"]
            else:
                args += ["--decrypt"]
            process = None
            try:
                process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, pass_fds=(passphrase_fd,))
                os.close(passphrase_fd)
                passphrase_fd = -1
                os.write(writer_fd, (key + "\n").encode("ascii"))
                os.close(writer_fd)
                writer_fd = -1
                try:
                    stdout, _ = process.communicate(payload, timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    raise
                status = process.returncode
            finally:
                if passphrase_fd >= 0:
                    os.close(passphrase_fd)
                if writer_fd >= 0:
                    os.close(writer_fd)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("GPG is unavailable or timed out") from None
    if status != 0:
        raise ValueError("Vault encryption or authentication failed")
    return stdout


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".postman-vault-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def init(root: Path, vault_file: Path, env_file: Path, *, state_root: Path | None = None) -> dict:
    root = root.resolve()
    vault_file = _cipher_path(root, vault_file)
    env_file = _private_path(root, env_file, state_root)
    if vault_file.exists() or _env_key(env_file, allow_missing=True) is not None:
        raise ValueError("Vault or local unlock key already exists; refusing to replace it")
    key = secrets.token_urlsafe(48)
    document = {"schemaVersion": SCHEMA_VERSION, "secrets": {}}
    ciphertext = _gpg("encrypt", key, json.dumps(document).encode("utf-8"))
    prior = env_file.read_bytes() if env_file.exists() else b""
    if prior and not prior.endswith(b"\n"):
        prior += b"\n"
    try:
        _atomic_write(env_file, prior + ("POSTMAN_VAULT_KEY=" + key + "\n").encode("ascii"))
        _atomic_write(vault_file, ciphertext)
    except BaseException:
        if prior:
            _atomic_write(env_file, prior)
        else:
            env_file.unlink(missing_ok=True)
        raise
    return {"created": True, "secretNames": []}


def _read(root: Path, vault_file: Path, env_file: Path,
          state_root: Path | None = None) -> tuple[Path, dict]:
    root = root.resolve()
    vault_file = _cipher_path(root, vault_file)
    env_file = _private_path(root, env_file, state_root)
    key = _env_key(env_file)
    try:
        decoded = json.loads(_gpg("decrypt", key, vault_file.read_bytes()))
    except (OSError, ValueError, json.JSONDecodeError):
        raise ValueError("Vault cannot be decrypted or validated") from None
    if (not isinstance(decoded, dict) or decoded.get("schemaVersion") != SCHEMA_VERSION
            or not isinstance(decoded.get("secrets"), dict)
            or any(not isinstance(k, str) or not VAULT_NAME.fullmatch(k)
                   or not isinstance(v, str) or not v for k, v in decoded["secrets"].items())):
        raise ValueError("Vault has an invalid schema")
    return vault_file, decoded


def inspect(root: Path, vault_file: Path, env_file: Path, *, state_root: Path | None = None) -> dict:
    _, document = _read(root, vault_file, env_file, state_root)
    return {"secretNames": sorted(document["secrets"])}


def coverage(root: Path, vault_file: Path, env_file: Path, *, state_root: Path | None = None) -> dict:
    """Report alias coverage without returning any plaintext secret values."""
    root = root.resolve()
    vault_file = _cipher_path(root, vault_file)
    if not vault_file.is_file():
        return {"state": "not-configured", "missingSecretNames": [], "unusedSecretNames": []}
    profiles = load_profiles(root)
    referenced = {entry["value"][8:-2] for profile in profiles
                  for entry in profile.payload["values"]
                  if entry.get("type") == "secret"
                  and VAULT_REFERENCE.fullmatch(entry.get("value", ""))}
    env_file = _private_path(root, env_file, state_root)
    if _env_key(env_file, allow_missing=True) is None:
        return {"state": "locked", "missingSecretNames": [], "unusedSecretNames": []}
    _, document = _read(root, vault_file, env_file, state_root)
    present = set(document["secrets"])
    missing = sorted(referenced - present)
    unused = sorted(present - referenced)
    return {"state": "complete" if not missing and not unused else "incomplete",
            "missingSecretNames": missing, "unusedSecretNames": unused}


def _source_value(source: Path, env_key: str | None) -> str:
    if not source.is_file() or source.is_symlink():
        raise ValueError("Secret source must be a regular file")
    info = source.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Secret source must be owned by the user with mode 0600")
    data = source.read_text(encoding="utf-8")
    if env_key:
        matches = [line.split("=", 1)[1] for line in data.splitlines()
                   if line.startswith(env_key + "=")]
        if len(matches) != 1:
            raise ValueError("Secret source must define the requested key exactly once")
        value = matches[0]
    else:
        value = data.removesuffix("\n").removesuffix("\r")
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Secret source contains an invalid value")
    return value


def set_secret(root: Path, vault_file: Path, env_file: Path, name: str,
               source: Path, env_key: str | None = None, *,
               state_root: Path | None = None) -> dict:
    if not VAULT_NAME.fullmatch(name):
        raise ValueError("Invalid vault secret name")
    profiles = load_profiles(root)
    referenced = {entry["value"][8:-2] for profile in profiles
                  for entry in profile.payload["values"]
                  if entry.get("type") == "secret"
                  and VAULT_REFERENCE.fullmatch(entry.get("value", ""))}
    if name not in referenced:
        raise ValueError("Vault secret must be referenced by a versioned internal profile")
    value = _source_value(source, env_key)
    vault_file, document = _read(root, vault_file, env_file, state_root)
    document["secrets"][name] = value
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _atomic_write(vault_file, _gpg("encrypt", _env_key(_private_path(root.resolve(), env_file, state_root)), encoded))
    return {"updated": name, "secretNames": sorted(document["secrets"])}


def materialize(root: Path, vault_file: Path, env_file: Path, role: str,
                output: Path, *, allow_missing: bool = False,
                enable_tests: bool = False, state_root: Path | None = None) -> dict:
    if enable_tests and not default_tests_enabled(role):
        raise ValueError("--enable-tests is unavailable for production profiles")
    profiles = {profile.id: profile for profile in load_profiles(root)}
    if role not in profiles:
        raise ValueError("Requested internal profile does not exist")
    _, document = _read(root, vault_file, env_file, state_root)
    payload = copy.deepcopy(profiles[role].payload)
    missing = []
    for entry in payload["values"]:
        reference = VAULT_REFERENCE.fullmatch(entry.get("value", ""))
        if reference:
            name = entry["value"][8:-2]
            if name not in document["secrets"]:
                missing.append(name)
                entry["value"] = ""
            else:
                entry["value"] = document["secrets"][name]
    if enable_tests:
        gates = [entry for entry in payload["values"] if entry.get("key") == "ws_tests_enabled"]
        if len(gates) != 1 or gates[0].get("value") != "true":
            raise ValueError("The versioned profile must declare ws_tests_enabled=true")
        gates[0]["value"] = "true"
        gates[0]["enabled"] = True
    if missing and not allow_missing:
        raise ValueError("Vault lacks referenced secrets: " + ", ".join(sorted(missing)))
    output = _private_path(root.resolve(), output, state_root)
    _atomic_write(output, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"role": role, "output": str(output), "missingSecretNames": sorted(missing),
            "testsEnabled": any(entry.get("key") == "ws_tests_enabled"
                                and entry.get("value") == "true" for entry in payload["values"])}
