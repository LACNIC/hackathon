"""Explicit, read-only HTTP observations bound to the current WS sources."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def source_fingerprint(inventory: dict) -> str:
    payload = json.dumps(inventory["sources"], sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def clean_base_url(value: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or "@" in parsed.netloc or parsed.query or parsed.fragment
            or "%" in parsed.netloc
            or any(character.isspace() or ord(character) < 32 for character in value)
            or "\\" in value):
        raise ValueError("Base URL must be an HTTP(S) origin and path without credentials, query or fragment")
    # Accessing port rejects malformed numeric ports before any request is made.
    _ = parsed.port
    prefix = parsed.path.rstrip("/")
    if prefix and (not prefix.startswith("/") or "//" in prefix or "/../" in prefix
                   or prefix.endswith("/..") or "/./" in prefix or prefix.endswith("/.")):
        raise ValueError("Base URL has an ambiguous deployment path")
    return urlunsplit((parsed.scheme, parsed.netloc, prefix, "", "")), prefix


def safe_documentation_location(value: str | None) -> bool:
    """Accept only a credential-free public Postman Documenter URL.

    A broad HTTPS check would permit an unrelated target and could persist a
    secret-bearing path in a receipt. This shape records only the public
    Documenter view identifier, never query parameters or arbitrary paths.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(parsed.scheme == "https"
                and parsed.netloc == "documenter.getpostman.com"
                and re.fullmatch(r"/view/[0-9]+/[A-Za-z0-9_-]+/?", parsed.path)
                and not parsed.query and not parsed.fragment
                and not any(character.isspace() or ord(character) < 32 for character in value))


def request_headers(base_url: str, path: str) -> dict:
    parsed = urlsplit(base_url)
    connection_type = (http.client.HTTPSConnection if parsed.scheme == "https"
                       else http.client.HTTPConnection)
    connection = connection_type(parsed.hostname, parsed.port, timeout=10)
    try:
        connection.request("GET", (parsed.path or "") + path)
        response = connection.getresponse()
        return {"status": response.status,
                "contentType": response.getheader("Content-Type"),
                "location": response.getheader("Location")}
    finally:
        connection.close()


def probe_plan(inventory: dict, base_url: str, probe_path: str) -> dict:
    clean_url, prefix = clean_base_url(base_url)
    if (not probe_path.startswith("/") or probe_path.startswith("//")
            or any(character in probe_path for character in "?#\\\r\n")
            or "{" in probe_path or "}" in probe_path):
        raise ValueError("Probe path must be one concrete source GET path without parameters")
    routes = {(item["method"], item["path"]) for item in inventory["operations"]}
    if ("GET", probe_path) not in routes:
        raise ValueError(f"GET {probe_path} is absent from the source inventory")
    return {"baseUrl": clean_url, "pathPrefix": prefix, "probePath": probe_path,
            "documentationPath": "/info/doc" if ("GET", "/info/doc") in routes else None}


def execute_probe(root: Path, inventory: dict, plan: dict, output: Path) -> tuple[dict, bool]:
    probe = request_headers(plan["baseUrl"], plan["probePath"])
    receipt = {"schemaVersion": 1, "kind": "http-deployment",
               "observedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "projectRoot": str(root.resolve()),
               "sourceSha256": source_fingerprint(inventory),
               "baseUrl": plan["baseUrl"], "probePath": plan["probePath"],
               "probe": {"status": probe["status"], "contentType": probe["contentType"]}}
    if plan["documentationPath"]:
        doc = request_headers(plan["baseUrl"], plan["documentationPath"])
        safe = safe_documentation_location(doc["location"])
        receipt["documentation"] = {"status": doc["status"], "locationSafe": safe}
        if safe:
            receipt["documentation"]["location"] = doc["location"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent,
                                     prefix=".http-receipt-", delete=False) as temporary:
        json.dump(receipt, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, output)
    doc = receipt.get("documentation")
    passed = receipt["probe"]["status"] == 200 and (doc is None or
             (doc["status"] in {301, 302, 303, 307, 308} and doc["locationSafe"]))
    return receipt, passed


def validate_probe_receipt(receipt: dict, root: Path, inventory: dict) -> tuple[bool, str, str | None]:
    try:
        plan = probe_plan(inventory, receipt["baseUrl"], receipt["probePath"])
        probe = receipt["probe"]
        if (receipt.get("schemaVersion") != 1 or receipt.get("kind") != "http-deployment"
                or receipt.get("projectRoot") != str(root.resolve())
                or receipt.get("sourceSha256") != source_fingerprint(inventory)
                or type(probe.get("status")) is not int
                or ("documentation" in receipt and
                    (not isinstance(receipt["documentation"], dict)
                     or type(receipt["documentation"].get("status")) is not int
                     or type(receipt["documentation"].get("locationSafe")) is not bool
                     or (receipt["documentation"]["locationSafe"]
                         != safe_documentation_location(receipt["documentation"].get("location")))
                     or (not receipt["documentation"]["locationSafe"]
                         and "location" in receipt["documentation"])))):
            return False, "Receipt does not match current sources or its observation is malformed", None
        return True, "Receipt matches current sources", plan["pathPrefix"]
    except (KeyError, TypeError, ValueError):
        return False, "Receipt does not match current sources or its observation is malformed", None
