"""Validate the only artifacts eligible for public Postman publication.

No network access, credentials, or project-local internal environments are read.
The caller receives copies of the public payloads; canonical files stay intact.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from http_evidence import safe_documentation_location


PUBLICATION_MARKER = "ai-harness-ws-publication:"
CONTENT_MARKER = "ai-harness-ws-content-sha256:"
PUBLICATION_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
POSTMAN_UID = re.compile(r"^[0-9]+-[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
VARIABLE = re.compile(r"^\{\{[A-Za-z][A-Za-z0-9_-]*\}\}$")
SENSITIVE_FRAGMENTS = ("token", "secret", "password", "passwd", "apikey",
                       "authorization", "credential", "privatekey", "cookie", "sessionid")


def _sensitive_name(name: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", name.lower())
    return any(fragment in compact for fragment in SENSITIVE_FRAGMENTS)


def production_environment_name(publication_id: str) -> str:
    service = publication_id[:-3] if publication_id.endswith("-ws") else publication_id
    return f"{service}-production"


def internal_environment_name(publication_id: str, role: str) -> str:
    """Use the same service prefix for public and private environments."""
    service = production_environment_name(publication_id).removesuffix("-production")
    return f"{service}-{role}"


def default_tests_enabled(role: str) -> bool:
    """Only production environments disable collection runs by default."""
    return role not in {"production", "production-internal"}


@dataclass(frozen=True)
class PublicationPlan:
    publication_id: str
    production_url: str
    demo_url: str | None
    collection_path: Path
    environment_path: Path
    collection: dict
    environment: dict
    public_environment_uid: str | None = None


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid JSON at {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object at {path}")
    return data


def collection_content_sha256(collection: dict) -> str:
    """Stable digest of the canonical public collection, without Postman IDs."""
    payload = json.dumps(collection, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def publishable_collection(collection: dict) -> dict:
    """Keep executable documentation, omit local harness selection metadata."""
    def project(value: object) -> object:
        if isinstance(value, list):
            return [project(entry) for entry in value]
        if isinstance(value, dict):
            return {key: project(entry) for key, entry in value.items()
                    if not key.startswith("x-ai-harness-ws-")}
        return value
    return project(collection)


def _public_url(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a nonempty HTTPS URL")
    parsed = urlparse(value)
    if (any(char.isspace() or ord(char) < 32 for char in value)
            or parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or "{{" in value):
        raise ValueError(f"{label} must be an HTTPS base URL without credentials, query or fragment")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"{label} must not point to localhost")
    return value.rstrip("/")


def _safe_value(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if value and not VARIABLE.fullmatch(value):
        raise ValueError(f"{label} must be empty or one Postman variable reference")


def _scan_collection(node: object, path: str = "collection", reject_event: bool = True) -> None:
    if isinstance(node, list):
        for index, child in enumerate(node):
            _scan_collection(child, f"{path}[{index}]", reject_event)
        return
    if not isinstance(node, dict):
        return
    if reject_event and "event" in node:
        raise ValueError(f"Public collection contains executable scripts at {path}")
    if isinstance(node.get("key"), str) and _sensitive_name(node["key"]):
        _safe_value(node.get("value", ""), f"{path}.{node['key']}")
    for key, value in node.items():
        if key in {"authorization", "password", "token", "secret", "apiKey", "api_key"}:
            if isinstance(value, str):
                _safe_value(value, f"{path}.{key}")
        if key == "raw" and isinstance(value, str) and value.lstrip().startswith("{"):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                _scan_collection(parsed, f"{path}.raw", False)
        _scan_collection(value, f"{path}.{key}", reject_event)


def _requests(items: object):
    if not isinstance(items, list):
        raise ValueError("Public collection item must be a list")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Public collection item is invalid")
        if "request" in item:
            yield item
        if "item" in item:
            yield from _requests(item["item"])


def validate_public_collection_payload(collection: dict) -> None:
    """Allow generated guarded tests, but reject unguarded or unsafe payloads."""
    if not isinstance(collection, dict) or not isinstance(collection.get("info"), dict) or not collection.get("item"):
        raise ValueError("Public collection is missing info or requests")
    description = collection.get("info", {}).get("description", "")
    generated = (collection.get("x-ai-harness-ws-generated") == 1
                 or (isinstance(description, str) and PUBLICATION_MARKER in description
                     and CONTENT_MARKER in description and bool(collection.get("event"))))
    _scan_collection(collection, reject_event=not generated)
    if generated:
        guard = collection.get("event", [])
        try:
            guard_source = "\n".join(guard[0]["script"]["exec"])
        except (IndexError, KeyError, TypeError):
            guard_source = ""
        if (not guard or guard[0].get("listen") != "prerequest"
                or 'pm.environment.get("ws_tests_enabled")' not in guard_source
                or "pm.execution.skipRequest()" not in guard_source):
            raise ValueError("Generated collection needs a ws_tests_enabled pre-request guard")
        for event in guard:
            _validate_script(event)
    variables = collection.get("variable", [])
    if not isinstance(variables, list):
        raise ValueError("Public collection variables are invalid")
    for entry in variables:
        if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
            raise ValueError("Public collection variable is invalid")
        if entry["key"] == "ws_tests_enabled" and entry.get("value") == "false":
            continue
        _safe_value(entry.get("value", ""), f"collection variable {entry['key']}")
    names = [entry["key"] for entry in variables]
    if len(set(names)) != len(names):
        raise ValueError("Public collection variables must have unique names")
    if "base_url" not in names:
        raise ValueError("Public collection must use base_url")
    if generated and "ws_tests_enabled" not in names:
        raise ValueError("Generated collection must declare ws_tests_enabled")
    count = 0
    seen = set()
    for item in _requests(collection["item"]):
        request = item["request"]
        if not isinstance(request, dict):
            raise ValueError("Public request is invalid")
        url = request.get("url")
        raw = url.get("raw") if isinstance(url, dict) else url
        if not isinstance(raw, str) or not raw.startswith("{{base_url}}/"):
            raise ValueError("Every public request must use the base_url variable")
        route = raw.split("?", 1)[0]
        key = (request.get("method"), route)
        if generated and key in seen:
            raise ValueError("Generated collection must have one request per method and route")
        seen.add(key)
        for name, value in parse_qsl(raw.partition("?")[2], keep_blank_values=True):
            if _sensitive_name(name):
                _safe_value(value, f"request query {name}")
        if generated:
            for event in item.get("event", []):
                _validate_script(event)
            for scenario in item.get("x-ai-harness-ws-scenarios", []):
                variant = scenario.get("request", {})
                variant_raw = variant.get("url", {}).get("raw")
                if (variant.get("method") != request.get("method")
                        or not isinstance(variant_raw, str)
                        or variant_raw.split("?", 1)[0] != route):
                    raise ValueError("Test scenario must target its documented operation")
        count += 1
    if not count:
        raise ValueError("Public collection contains no requests")


def _validate_script(event: dict) -> None:
    if (not isinstance(event, dict) or event.get("listen") not in {"prerequest", "test"}
            or not isinstance(event.get("script"), dict)
            or event["script"].get("type") != "text/javascript"
            or not isinstance(event["script"].get("exec"), list)
            or not all(isinstance(line, str) for line in event["script"]["exec"])):
        raise ValueError("Generated Postman test script has an invalid shape")
    source = "\n".join(event["script"]["exec"])
    if re.search(r"\b(?:pm\.sendRequest|pm\.execution\.runRequest|fetch|XMLHttpRequest|eval|require)\s*\(", source):
        raise ValueError("Generated Postman scripts cannot make auxiliary requests or execute dynamic code")


def validate_public_environment_payload(environment: dict, production_url: str | None,
                                        publication_id: str) -> None:
    """Check public environment content, allowing only remote Postman metadata."""
    if not isinstance(environment, dict):
        raise ValueError("Public environment is invalid")
    allowed = {"name", "values", "id", "uid", "owner", "createdAt", "updatedAt",
               "isPublic", "_postman_variable_scope", "_postman_exported_at",
               "_postman_exported_using"}
    if set(environment) - allowed or not {"name", "values"} <= set(environment):
        raise ValueError("Public environment may only contain name, values and Postman metadata")
    if "isPublic" in environment and not isinstance(environment["isPublic"], bool):
        raise ValueError("Public environment visibility metadata is invalid")
    if environment["name"] != production_environment_name(publication_id):
        raise ValueError("Public environment name must match publicationId")
    values = environment["values"]
    if (not isinstance(values, list) or len(values) != 2 or not isinstance(values[0], dict)
            or not {"key", "value", "type", "enabled"} <= set(values[0])
            or set(values[0]) - {"key", "value", "type", "enabled", "id", "description"}
            or values[0].get("key") != "base_url" or values[0].get("type") != "default"
            or values[0].get("enabled") is not True):
        raise ValueError("Public environment must contain only the approved production base_url and disabled tests")
    tests = values[1]
    if (not isinstance(tests, dict) or set(tests) - {"key", "value", "type", "enabled", "id", "description"}
            or tests.get("key") != "ws_tests_enabled" or tests.get("value") != "false"
            or tests.get("type") != "default" or tests.get("enabled") is not True):
        raise ValueError("Public environment must set ws_tests_enabled=false")
    if "description" in values[0] and not isinstance(values[0]["description"], str):
        raise ValueError("Public base_url description must be text")
    actual_url = _public_url(values[0]["value"], "public base_url")
    if actual_url != values[0]["value"] or (production_url is not None and actual_url != production_url):
        raise ValueError("Public environment must contain only the approved production base_url and disabled tests")


def validate_publication(module_root: Path, contract_path: Path) -> PublicationPlan:
    """Return publication-ready payloads after a strict, offline allowlist check.

    ``module_root`` is the web-service module containing ``postman/``. The
    contract may only select its canonical generated collection and its public
    environment. The workspace ID and Postman API key are supplied to the
    remote CLI via separate process credentials.
    """
    root = module_root.resolve(strict=True)
    expected_contract = root / "postman" / "publication.json"
    if contract_path.resolve() != expected_contract:
        raise ValueError("Publication contract must be postman/publication.json in the module")
    config = _read_json(expected_contract)
    allowed = {"schemaVersion", "publicationId", "publicCollection", "publicEnvironment",
               "productionUrl", "demoUrl", "documentationUrl", "publicEnvironmentUid"}
    if set(config) - allowed or set(config) & {"workspaceId", "apiKey", "token"}:
        raise ValueError("Publication contract contains unsupported fields")
    if config.get("schemaVersion") != 1:
        raise ValueError("Unsupported publication schemaVersion")
    publication_id = config.get("publicationId")
    if not isinstance(publication_id, str) or not PUBLICATION_ID.fullmatch(publication_id):
        raise ValueError("publicationId must be a stable lowercase slug")
    public_environment_uid = config.get("publicEnvironmentUid")
    if public_environment_uid is not None and (not isinstance(public_environment_uid, str)
                                               or not POSTMAN_UID.fullmatch(public_environment_uid)):
        raise ValueError("publicEnvironmentUid must be a Postman environment UID")
    if config.get("publicCollection") != "collection.json":
        raise ValueError("Only postman/collection.json can be published")
    if config.get("publicEnvironment") != "environments/public.json":
        raise ValueError("Only postman/environments/public.json can be published")
    production_url = _public_url(config.get("productionUrl"), "productionUrl")
    demo_url = (_public_url(config["demoUrl"], "demoUrl")
                if config.get("demoUrl") is not None else None)
    if "documentationUrl" in config and not safe_documentation_location(config["documentationUrl"]):
        raise ValueError("documentationUrl must be a credential-free public Postman Documenter URL")
    collection_path = root / "postman" / "collection.json"
    environment_path = root / "postman" / "environments" / "public.json"
    if not collection_path.resolve(strict=True).is_relative_to(root):
        raise ValueError("Public artifact cannot escape the web-service module")
    collection = _read_json(collection_path)
    validate_public_collection_payload(collection)
    environment = {"name": production_environment_name(publication_id), "values": [
        {"key": "base_url", "value": production_url, "type": "default", "enabled": True},
        {"key": "ws_tests_enabled", "value": "false", "type": "default", "enabled": True},
    ]}
    validate_public_environment_payload(environment, production_url, publication_id)
    published = publishable_collection(collection)
    original = published["info"].get("description", "")
    if not isinstance(original, str):
        raise ValueError("Collection info.description must be text")
    if PUBLICATION_MARKER in original or CONTENT_MARKER in original:
        raise ValueError("Canonical collection must not contain a prior publication marker")
    intro = (f"## Ambientes públicos\n\nURL base de producción: `{production_url}`."
             + (f"\n\nURL base de demo: `{demo_url}`." if demo_url else ""))
    published["info"]["description"] = (
        original.rstrip() + "\n\n" + intro + "\n\n" + PUBLICATION_MARKER + publication_id
        + "\n\n" + CONTENT_MARKER + collection_content_sha256(collection)
    )
    return PublicationPlan(publication_id, production_url, demo_url, collection_path,
                           environment_path, published, copy.deepcopy(environment),
                           public_environment_uid)
