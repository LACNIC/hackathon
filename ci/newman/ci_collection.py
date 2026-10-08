"""Select versioned, read-only Newman scenarios for CI and local review."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

SCENARIOS = "x-ai-harness-ws-scenarios"


def _same_http_endpoint(first: str, second: str) -> bool:
    """Compare a published URL with a CI target including implicit default ports."""
    try:
        a, b = urlsplit(first), urlsplit(second)
        def identity(url):
            scheme = url.scheme.lower()
            return (scheme, (url.hostname or "").casefold(),
                    url.port or (443 if scheme == "https" else 80 if scheme == "http" else None),
                    url.path.rstrip("/") or "/", url.query)
        return identity(a) == identity(b)
    except ValueError:
        return False


def _operation_items(collection: dict) -> list[dict]:
    def visit(items: list[dict]) -> list[dict]:
        found = []
        for item in items:
            if "request" in item:
                found.append(item)
            else:
                found.extend(visit(item.get("item", [])))
        return found
    return visit(collection.get("item", []))


def _under_path_prefix(path: str, prefix: str) -> bool:
    base = prefix.rstrip("/") or "/"
    path = path.split("?", 1)[0].split("#", 1)[0]
    if base == "/":
        return True
    if prefix.endswith("/"):
        return path.startswith(base + "/")
    return path == base or path.startswith(base + "/")


def _scenario_collection(collection: dict, folder: str,
                         path_prefix: str | None,
                         environment_role: str | None = None) -> tuple[dict, list[dict]]:
    """Derive Newman-only scenario requests from the one published operation item."""
    selected = []
    for operation in _operation_items(collection):
        for scenario in operation.get(SCENARIOS, []):
            if str(scenario.get("suite", "")).casefold() != folder.casefold():
                continue
            request = scenario.get("request")
            script = scenario.get("script")
            if not isinstance(request, dict) or not isinstance(script, list):
                raise ValueError("canonical collection has an invalid test scenario")
            raw = request.get("url", {}).get("raw", "")
            path = re.sub(r"^\{\{base_url\}\}", "", raw)
            if path_prefix and not _under_path_prefix(path, path_prefix):
                continue
            if (environment_role is not None and environment_role != "local"
                    and scenario.get("allowedEnvironments") == ["local"]):
                continue
            name = scenario.get("name") or operation.get("name", "")
            item = {"name": name, "request": request,
                    "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": script}}],
                    "x-ai-harness-ws-test-environments": scenario.get("allowedEnvironments", [])}
            if scenario.get("protocolProfileBehavior"):
                item["protocolProfileBehavior"] = scenario["protocolProfileBehavior"]
            selected.append(item)
    if len({item["name"] for item in selected}) != len(selected):
        raise ValueError("canonical collection repeats a test scenario name in one suite")
    derived = {key: value for key, value in collection.items() if key != "item"}
    derived["item"] = [{"name": folder, "item": selected}]
    return derived, selected


def suite_collection(collection: dict, folder: str,
                     path_prefix: str | None = None,
                     environment_role: str | None = None) -> tuple[dict, list[dict], bool]:
    """Select a suite, supporting old receipts while migrating to scenario metadata."""
    scenario_mode = any(SCENARIOS in item for item in _operation_items(collection))
    if scenario_mode:
        derived, selected = _scenario_collection(collection, folder, path_prefix, environment_role)
        return derived, selected, True
    selected_folders = [group for group in collection.get("item", [])
                        if group.get("name") == folder]
    selected = selected_folders[0].get("item", []) if len(selected_folders) == 1 else []
    if path_prefix is not None:
        selected = [item for item in selected
                    if _under_path_prefix(
                        re.sub(r"^\{\{base_url\}\}", "", item.get("request", {}).get("url", {}).get("raw", "")),
                        path_prefix)]
    if environment_role is not None and environment_role != "local":
        selected = [item for item in selected
                    if item.get("x-ai-harness-ws-test-environments") != ["local"]]
    derived = dict(collection)
    derived["item"] = [{"name": folder, "item": selected}]
    return derived, selected, False


def junit_counts(path: Path) -> dict:
    if not path.is_file():
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {key: sum(int(suite.get(key, "0")) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")}
