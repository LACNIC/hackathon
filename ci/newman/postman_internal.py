"""Validate and sync versioned, credential-free internal Postman environments."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from postman_remote import PostmanClient, PostmanError
from publication_contract import default_tests_enabled, internal_environment_name, validate_publication


SENSITIVE = re.compile(r"token|secret|password|passwd|api[_-]?key|authorization|credential|cookie|session", re.I)
VAULT_REFERENCE = re.compile(r"\{\{vault:[A-Za-z0-9_.-]+\}\}")
REQUIRED_ROLES = ("local", "testing", "production-internal")


def safe_secret_value(value: object) -> bool:
    return value in (None, "") or (isinstance(value, str)
                                   and VAULT_REFERENCE.fullmatch(value) is not None)


@dataclass(frozen=True)
class InternalProfile:
    id: str
    payload: dict
    path: Path
    managed_uid: str | None


def _values(payload: dict, *, source: str) -> dict[str, dict]:
    entries = payload.get("values")
    if not isinstance(entries, list) or not entries:
        raise PostmanError(f"{source} has no Postman values array")
    result: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("key"), str) or not entry["key"]:
            raise PostmanError(f"{source} contains an invalid variable")
        key = entry["key"]
        if key in result:
            raise PostmanError(f"{source} repeats a variable")
        result[key] = entry
    return result


def safe_base_url(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path.startswith("/"):
        return False
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def load_profiles(module_root: Path) -> list[InternalProfile]:
    root = module_root.resolve()
    publication = validate_publication(root, root / "postman" / "publication.json")
    folder = root / "postman" / "environments"
    paths = sorted(folder.glob("*.profile.json")) if folder.is_dir() else []
    if not paths:
        raise PostmanError("No versioned postman/environments/*.profile.json profiles were found")
    profiles: list[InternalProfile] = []
    for path in paths:
        profile_id = path.name.removesuffix(".profile.json")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", profile_id):
            raise PostmanError(f"Invalid internal profile filename: {path.name}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise PostmanError(f"Cannot read valid internal profile: {path.name}") from None
        if not isinstance(payload, dict):
            raise PostmanError(f"Invalid internal profile: {path.name}")
        name = internal_environment_name(publication.publication_id, profile_id)
        managed_uid = payload.pop("managedUid", None)
        if managed_uid is not None and (not isinstance(managed_uid, str) or not managed_uid.strip()):
            raise PostmanError(f"{path.name} has an invalid managedUid binding")
        if payload.get("name") != name:
            raise PostmanError(f"{path.name} must declare the managed environment name")
        if name == publication.environment["name"]:
            raise PostmanError("Internal profile cannot replace the public production environment")
        values = _values(payload, source=path.name)
        if not safe_base_url(values.get("base_url", {}).get("value")):
            raise PostmanError(f"{path.name} needs a confirmed HTTPS or loopback base_url")
        expected_gate = "true" if default_tests_enabled(profile_id) else "false"
        if (values.get("ws_tests_enabled", {}).get("value") != expected_gate
                or values["ws_tests_enabled"].get("type", "default") != "default"
                or values["ws_tests_enabled"].get("enabled", True) is not True):
            raise PostmanError(f"{path.name} must set ws_tests_enabled={expected_gate}")
        for key, entry in values.items():
            if not isinstance(entry.get("value"), str) or entry.get("type", "default") not in {"default", "secret"}:
                raise PostmanError(f"{path.name} has an invalid variable value or type")
            if VAULT_REFERENCE.fullmatch(entry["value"]) and entry.get("type") != "secret":
                raise PostmanError(f"{path.name} must mark vault references as secret")
            if SENSITIVE.search(key) and (entry.get("type") != "secret"
                                          or not safe_secret_value(entry["value"])):
                raise PostmanError(f"{path.name} cannot contain a literal or non-secret credential variable")
        profiles.append(InternalProfile(profile_id, payload, path, managed_uid))
    return profiles


def _normalized(payload: dict) -> tuple:
    values = _values(payload, source="remote environment")
    return (payload.get("name"),
            tuple(sorted((key, entry.get("value") or "", entry.get("type", "default"),
                          entry.get("enabled", True)) for key, entry in values.items())))


def plan_internal(client: PostmanClient, workspace_id: str,
                  profiles: list[InternalProfile]) -> list[dict]:
    workspace = client.request("GET", "/workspaces/" + quote(workspace_id, safe="")).get("workspace")
    if not isinstance(workspace, dict) or workspace.get("id") != workspace_id or workspace.get("visibility") not in {"private", "team"}:
        raise PostmanError("A confirmed private or team workspace is required")
    entries = client.request("GET", "/environments?" + urlencode({"workspace": workspace_id})).get("environments")
    if not isinstance(entries, list):
        raise PostmanError("Postman did not return the workspace environment inventory")
    plans: list[dict] = []
    for profile in profiles:
        matching = [entry for entry in entries if isinstance(entry, dict)
                    and entry.get("name") == profile.payload["name"]]
        if len(matching) > 1:
            raise PostmanError("More than one remote environment has the managed profile name")
        if not matching:
            if profile.managed_uid:
                raise PostmanError("A bound internal environment is missing or was renamed; refusing duplicate creation")
            plans.append({"profile": profile, "action": "create", "uid": None})
            continue
        uid = matching[0].get("uid") or matching[0].get("id")
        if not isinstance(uid, str) or not uid:
            raise PostmanError("Postman omitted an internal environment ID")
        remote = client.request("GET", "/environments/" + quote(uid, safe="")).get("environment")
        if not isinstance(remote, dict) or profile.managed_uid != uid:
            raise PostmanError("Remote environment UID is not bound to this versioned profile")
        values = _values(remote, source="remote environment")
        if any(not safe_secret_value(entry.get("value"))
               for key, entry in values.items() if SENSITIVE.search(key)):
            raise PostmanError("Remote environment has stored credentials; clear them before managed sync")
        plans.append({"profile": profile,
                      "action": "unchanged" if _normalized(remote) == _normalized(profile.payload) else "update",
                      "uid": uid})
    return plans


def verify_internal(plans: list[dict]) -> dict:
    result = [{"name": item["profile"].payload["name"], "status": item["action"]}
              for item in plans]
    return {"ready": bool(result) and all(item["status"] == "unchanged" for item in result),
            "profiles": result, "networkAccess": True}


def apply_internal(client: PostmanClient, workspace_id: str, plans: list[dict]) -> list[dict]:
    results: list[dict] = []
    for item in plans:
        profile = item["profile"]
        if item["action"] == "create":
            response = client.request("POST", "/environments?" + urlencode({"workspace": workspace_id}),
                                      {"environment": profile.payload})
            uid = response.get("environment", {}).get("uid") or response.get("environment", {}).get("id")
            if not isinstance(uid, str) or not uid:
                raise PostmanError("Postman did not confirm internal environment creation")
            saved = {**profile.payload, "managedUid": uid}
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=profile.path.parent,
                                             prefix=".profile-", delete=False) as temporary:
                json.dump(saved, temporary, ensure_ascii=False, indent=2)
                temporary.write("\n")
                temporary_path = Path(temporary.name)
            temporary_path.replace(profile.path)
        elif item["action"] == "update":
            uid = item["uid"]
            client.request("PUT", "/environments/" + quote(uid, safe=""), {"environment": profile.payload})
        else:
            uid = item["uid"]
        results.append({"name": profile.payload["name"], "action": item["action"], "uid": uid})
    return results
