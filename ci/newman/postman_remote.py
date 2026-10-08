"""Narrow Postman API adapter for public documentation artifacts only."""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from publication_contract import (validate_public_collection_payload,
                                  validate_public_environment_payload)


API_ORIGIN = "https://api.postman.com"


class PostmanError(ValueError):
    pass


class PostmanClient:
    def __init__(self, api_key: str, *, transport=None):
        if not api_key or not api_key.strip():
            raise PostmanError("POSTMAN_API_KEY is required for remote operations")
        self._api_key = api_key
        self._transport = transport or urlopen

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        parsed_path = urlsplit(path)
        resource = parsed_path.path.split("/", 2)[1] if parsed_path.path.startswith("/") else ""
        if parsed_path.scheme or parsed_path.netloc or resource not in {"apis", "collections", "environments", "workspaces"}:
            raise PostmanError("Unsupported Postman API path")
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(API_ORIGIN + path, data=payload, method=method,
                          headers={"X-API-Key": self._api_key,
                                   "User-Agent": "ai-harness-ws/0.1",
                                   "Accept": ("application/vnd.api.v10+json" if path.startswith("/apis")
                                              else "application/json"),
                                   "Content-Type": "application/json"})
        for attempt in range(3):
            try:
                with self._transport(request, timeout=20) as response:
                    data = json.load(response)
                break
            except HTTPError as exc:
                # Retry transient server failures only for reads. A write may
                # have succeeded remotely even when its response is a 5xx.
                exc.close()
                if method == "GET" and 500 <= exc.code < 600 and attempt < 2:
                    time.sleep((0.2, 0.5)[attempt])
                    continue
                # Never echo response bodies: they can include remote values or key-bearing URLs.
                raise PostmanError(f"Postman API returned HTTP {exc.code} for {method} {path.split('?')[0]}") from None
            except (URLError, TimeoutError) as exc:
                raise PostmanError(f"Postman API connection failed for {method} {path.split('?')[0]}") from None
        if not isinstance(data, dict):
            raise PostmanError("Postman API returned an unexpected response")
        return data


def _one(entries: list[dict], name: str, kind: str) -> dict | None:
    matches = [entry for entry in entries if entry.get("name") == name]
    if len(matches) > 1:
        raise PostmanError(f"More than one {kind} named {name}; resolve in Postman before syncing")
    return matches[0] if matches else None


def _id(entry: dict) -> str:
    value = entry.get("uid") or entry.get("id")
    if not isinstance(value, str) or not value:
        raise PostmanError("Postman API response omitted resource ID")
    return value


def _workspace_contains_environment(workspace: dict, uid: str, expected_name: str) -> bool:
    """Independently verify a pinned environment when the filtered list omits it."""
    entries = workspace.get("environments")
    if not isinstance(entries, list):
        raise PostmanError("Workspace response cannot verify the bound public environment")
    resource_id = uid.partition("-")[2]
    present = False
    for entry in entries:
        if not isinstance(entry, dict):
            raise PostmanError("Workspace returned an invalid environment entry")
        if (entry.get("name") == expected_name
                and entry.get("uid", entry.get("id")) not in {uid, resource_id}):
            raise PostmanError("Public environment name belongs to a different workspace UID")
        if entry.get("uid") == uid or entry.get("id") in {uid, resource_id}:
            present = True
    return present


def _bound_environment_identity(remote: dict, uid: str) -> None:
    owner_id, _, resource_id = uid.partition("-")
    if (remote.get("uid") not in (None, uid)
            or remote.get("id") not in (resource_id, uid)
            or (remote.get("owner") is not None and str(remote["owner"]) != owner_id)):
        raise PostmanError("Bound public environment ID does not match its direct response")


def list_workspaces(client: PostmanClient) -> list[dict]:
    """Read every workspace page so choosing a dedicated workspace is reliable."""
    entries: list[dict] = []
    cursor = None
    while True:
        query = {"limit": 100}
        if cursor:
            query["cursor"] = cursor
        response = client.request("GET", "/workspaces?" + urlencode(query))
        page = response.get("workspaces")
        meta = response.get("meta")
        if not isinstance(page, list) or not isinstance(meta, dict):
            raise PostmanError("Postman API did not return a complete workspace page")
        entries.extend(page)
        cursor = meta.get("nextCursor")
        if cursor is None:
            return entries
        if not isinstance(cursor, str) or not cursor:
            raise PostmanError("Invalid Postman workspace pagination cursor")


def _workspace_collections(client: PostmanClient, workspace_id: str) -> list[dict]:
    entries: list[dict] = []
    offset = 0
    while True:
        query = urlencode({"workspace": workspace_id, "limit": 100, "offset": offset})
        response = client.request("GET", "/collections?" + query)
        page = response.get("collections")
        if not isinstance(page, list):
            raise PostmanError("Postman API did not return workspace collections")
        entries.extend(page)
        meta = response.get("meta")
        if not isinstance(meta, dict):
            raise PostmanError("Postman API did not confirm complete collection inventory")
        total = meta.get("total")
        if not isinstance(total, int) or total < 0:
            raise PostmanError("Postman API returned invalid collection count")
        if len(entries) >= total:
            return entries
        if not page:
            raise PostmanError("Incomplete Postman collection inventory")
        offset += len(page)


def _collection_linked_to_api(client: PostmanClient, workspace_id: str,
                              collection_uid: str | None) -> bool:
    if collection_uid is None:
        return False
    cursor = None
    while True:
        query = {"workspaceId": workspace_id, "limit": 100}
        if cursor:
            query["cursor"] = cursor
        response = client.request("GET", "/apis?" + urlencode(query))
        apis = response.get("apis")
        if not isinstance(apis, list):
            raise PostmanError("Postman API did not return API Builder inventory")
        for api in apis:
            if not isinstance(api, dict) or not isinstance(api.get("id"), str):
                raise PostmanError("Postman API returned an invalid API Builder entry")
            path = "/apis/" + quote(api["id"], safe="") + "?include=collections"
            details = client.request("GET", path)
            linked = details.get("collections")
            if not isinstance(linked, list):
                raise PostmanError("Cannot verify collection linkage in API Builder")
            target_id = collection_uid.rsplit("-", 1)[-1]
            for entry in linked:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                    raise PostmanError("Invalid API Builder collection linkage")
                if entry["id"] in {collection_uid, target_id} or collection_uid.endswith("-" + entry["id"]):
                    return True
        meta = response.get("meta") or {}
        cursor = meta.get("nextCursor")
        if cursor is None:
            if len(apis) >= 100 and not meta:
                raise PostmanError("Cannot confirm complete API Builder inventory")
            return False
        if not isinstance(cursor, str) or not cursor:
            raise PostmanError("Invalid API Builder pagination cursor")


def _strip_server_ids(value):
    if isinstance(value, list):
        return [_strip_server_ids(item) for item in value]
    if isinstance(value, dict):
        result = {}
        file_value = (value.get("src") if "src" in value else value.get("value"))
        for key, item in value.items():
            if key in {"id", "uid", "_postman_id", "owner", "createdAt", "updatedAt",
                       "lastUpdatedBy", "isPublic", "_postman_variable_scope",
                       "_postman_exported_at", "_postman_exported_using"}:
                continue
            # Postman drops explicit false flags and adds empty optional arrays
            # when a collection is stored and read back.
            if key == "disabled" and item is False:
                continue
            if key in {"response", "header"} and item == []:
                continue
            if key == "_postman_previewlanguage":
                continue
            if (key == "cookie" and item == []) or (key == "responseTime" and item is None):
                continue
            # File form fields round-trip as value instead of src.
            if value.get("type") == "file" and key in {"src", "value"}:
                continue
            result[key] = _strip_server_ids(item)
        if value.get("type") == "file" and ("src" in value or "value" in value):
            result["src"] = _strip_server_ids(file_value)
        return result
    return value


def _same_public_payload(expected: dict, actual: dict, *, environment: bool = False) -> bool:
    """Extra remote fields are drift, except IDs added by Postman itself."""
    clean_expected = _strip_server_ids(expected)
    clean_actual = _strip_server_ids(actual)
    if environment and isinstance(clean_actual.get("values"), list):
        for value in clean_actual["values"]:
            if isinstance(value, dict) and value.get("description") == "":
                value.pop("description")
    return clean_expected == clean_actual


def _safe_remote_environment(remote: dict, expected: dict, publication_id: str) -> None:
    """Refuse to replace an environment that may carry a shared credential."""
    if remote.get("name") != expected.get("name"):
        raise PostmanError("Remote environment name changed; refusing to replace it")
    values = remote.get("values")
    if not isinstance(values, list):
        raise PostmanError("Remote environment has no inspectable values")
    expected_keys = {entry["key"] for entry in expected["values"]}
    keys = [entry.get("key") for entry in values if isinstance(entry, dict)]
    if (len(keys) != len(values) or len(set(keys)) != len(keys)
            or set(keys) not in (expected_keys, {"base_url"})):
        raise PostmanError("Remote public environment has unexpected variables; refusing to replace it")
    if any(entry.get("secret") or entry.get("source") or entry.get("type") == "secret"
           for entry in values):
        raise PostmanError("Remote public environment has secret-backed variables; refusing to replace it")
    try:
        # Existing public environments predate the test gate. Permit exactly
        # their public base_url so sync can add a disabled gate in one update.
        candidate = copy.deepcopy(remote)
        if set(keys) == {"base_url"}:
            candidate["values"].append(copy.deepcopy(expected["values"][1]))
        validate_public_environment_payload(candidate, None, publication_id)
    except ValueError:
        raise PostmanError("Remote public environment failed the publication safety check") from None


def _validate_outgoing(publication) -> None:
    try:
        validate_public_collection_payload(publication.collection)
        validate_public_environment_payload(publication.environment, publication.production_url,
                                            publication.publication_id)
    except ValueError:
        raise PostmanError("Outgoing public payload failed the publication safety check") from None


def _preserve_collection_ids(desired: dict, remote: dict) -> dict:
    """Keep stable Postman item IDs for operations that still exist."""
    result = copy.deepcopy(desired)
    remote_info = remote.get("info", {})
    if isinstance(remote_info, dict) and remote_info.get("_postman_id"):
        result["info"]["_postman_id"] = remote_info["_postman_id"]

    def identity(item: dict) -> tuple | None:
        name = item.get("name")
        if not isinstance(name, str):
            return None
        request = item.get("request")
        if isinstance(request, dict):
            method = request.get("method")
            if not isinstance(method, str):
                return None
            # Public item labels used to include the method. Normalize only
            # that prefix so the rename retains each Postman item ID.
            label = name.removeprefix(method + " ")
            return ("request", method, label)
        return ("folder" if isinstance(item.get("item"), list) else "response", name)

    def copy_item_ids(wanted_items: list, old_items: list) -> None:
        old_by_identity = {}
        for old in old_items:
            if isinstance(old, dict):
                key = identity(old)
                if key is not None:
                    old_by_identity.setdefault(key, []).append(old)
        wanted_keys = [identity(item) if isinstance(item, dict) else None for item in wanted_items]
        for wanted in wanted_items:
            if not isinstance(wanted, dict):
                continue
            key = identity(wanted)
            matches = old_by_identity.get(key, [])
            if key is None or wanted_keys.count(key) != 1 or len(matches) != 1:
                continue
            old = matches[0]
            if old.get("id"):
                wanted["id"] = old["id"]
            if isinstance(wanted.get("item"), list) and isinstance(old.get("item"), list):
                copy_item_ids(wanted["item"], old["item"])
            if isinstance(wanted.get("response"), list) and isinstance(old.get("response"), list):
                copy_item_ids(wanted["response"], old["response"])

    copy_item_ids(result.get("item", []), remote.get("item", []))
    return result


@dataclass(frozen=True)
class RemotePlan:
    workspace_id: str
    collection_action: str
    environment_action: str
    collection_uid: str | None
    environment_uid: str | None
    remote_collection: dict | None = None

    def public_summary(self) -> dict:
        return {"workspaceId": self.workspace_id,
                "collection": {"action": self.collection_action, "uid": self.collection_uid},
                "environment": {"action": self.environment_action, "uid": self.environment_uid},
                "documentationPublication": "not requested"}


def plan_sync(client: PostmanClient, workspace_id: str, publication) -> RemotePlan:
    if not workspace_id or not workspace_id.strip():
        raise PostmanError("POSTMAN_WS_WORKSPACE_ID is required")
    workspace_id = workspace_id.strip()
    bound_uid = getattr(publication, "public_environment_uid", None)
    workspace_path = "/workspaces/" + quote(workspace_id, safe="")
    if bound_uid:
        # The default workspace detail may omit environments even while the
        # explicit elements view lists them. Never infer ownership from a
        # direct environment GET, which has no workspace field.
        workspace_path += "?include=environments"
    workspace = client.request("GET", workspace_path).get("workspace")
    if not isinstance(workspace, dict) or workspace.get("id") != workspace_id:
        raise PostmanError("Workspace ID was not confirmed by Postman")
    if workspace.get("visibility") not in {"team", "private"}:
        raise PostmanError("Documentation workspace must be shared internally (private or team)")
    collections = _workspace_collections(client, workspace_id)
    query = urlencode({"workspace": workspace_id})
    environments_response = client.request("GET", "/environments?" + query)
    environments = environments_response.get("environments")
    if not isinstance(environments, list):
        raise PostmanError("Postman API did not return workspace inventory")
    expected_name = publication.collection["info"]["name"]
    collection_entry = _one(collections, expected_name, "collection")
    environment_entry = _one(environments, publication.environment["name"], "environment")
    remote_bound_environment = None
    if bound_uid:
        if environment_entry is not None and _id(environment_entry) != bound_uid:
            raise PostmanError("Public environment name belongs to a different UID")
        filtered_membership = environment_entry is not None
        if not isinstance(workspace.get("environments"), list) and not filtered_membership:
            # Postman intermittently omits the environments array even with
            # explicit include. Retry only absent inventory, never a returned
            # list that disproves membership. The scoped environment list is
            # an independent positive proof when it contains the bound UID.
            for delay in (0.2, 0.5, 1.0, 2.0):
                time.sleep(delay)
                workspace = client.request("GET", workspace_path).get("workspace")
                if not isinstance(workspace, dict) or workspace.get("id") != workspace_id:
                    raise PostmanError("Workspace ID was not confirmed by Postman")
                if workspace.get("visibility") not in {"team", "private"}:
                    raise PostmanError("Documentation workspace must be shared internally (private or team)")
                if isinstance(workspace.get("environments"), list):
                    break
        workspace_membership = (_workspace_contains_environment(
            workspace, bound_uid, publication.environment["name"])
            if isinstance(workspace.get("environments"), list) else filtered_membership)
        if not workspace_membership:
            if not isinstance(workspace.get("environments"), list):
                raise PostmanError("Workspace response cannot verify the bound public environment")
            raise PostmanError("Bound public environment is absent from the selected workspace")
        if environment_entry is None:
            remote_bound_environment = client.request(
                "GET", "/environments/" + quote(bound_uid, safe="")).get("environment")
            if not isinstance(remote_bound_environment, dict):
                raise PostmanError("Cannot inspect bound public environment")
            environment_entry = {"uid": bound_uid}
    marker = "ai-harness-ws-publication:" + publication.publication_id
    if collection_entry is None:
        # A collection may have been renamed in Postman. Locate our marker
        # before deciding to create, so a rename cannot duplicate the docs.
        marked = []
        for entry in collections:
            remote = client.request("GET", "/collections/" + quote(_id(entry), safe="")).get("collection")
            if not isinstance(remote, dict):
                raise PostmanError("Cannot inspect a workspace collection before creating documentation")
            if marker in str(remote.get("info", {}).get("description", "")):
                marked.append(entry)
        if len(marked) > 1:
            raise PostmanError("Multiple collections carry this publication ID")
        collection_entry = marked[0] if marked else None
    if environment_entry is not None and collection_entry is None:
        raise PostmanError("Public environment name exists without its managed collection")
    collection_uid = None
    environment_uid = None
    collection_action = "create"
    environment_action = "create"
    remote_collection = None
    if collection_entry:
        collection_uid = _id(collection_entry)
        if _collection_linked_to_api(client, workspace_id, collection_uid):
            raise PostmanError("Public documentation collection is linked to an API Builder API")
        remote = client.request("GET", "/collections/" + quote(collection_uid, safe="")).get("collection")
        if not isinstance(remote, dict) or marker not in str(remote.get("info", {}).get("description", "")):
            raise PostmanError("Collection name is already used by an unmanaged collection")
        try:
            validate_public_collection_payload(remote)
        except ValueError:
            raise PostmanError("Remote collection failed the publication safety check") from None
        remote_collection = remote
        collection_action = "unchanged" if _same_public_payload(publication.collection, remote) else "update"
    if environment_entry:
        environment_uid = _id(environment_entry)
        remote = (remote_bound_environment if remote_bound_environment is not None else
                  client.request("GET", "/environments/" + quote(environment_uid, safe="")).get("environment"))
        if not isinstance(remote, dict):
            raise PostmanError("Cannot inspect remote environment")
        if bound_uid:
            _bound_environment_identity(remote, bound_uid)
        _safe_remote_environment(remote, publication.environment, publication.publication_id)
        environment_action = ("unchanged" if _same_public_payload(publication.environment, remote,
                                                                  environment=True) else "update")
    return RemotePlan(workspace_id, collection_action, environment_action,
                      collection_uid, environment_uid, remote_collection)


def sync_public(client: PostmanClient, plan: RemotePlan, publication) -> dict:
    """Apply one reviewed plan; caller must explicitly request this operation."""
    _validate_outgoing(publication)
    workspace_query = "?" + urlencode({"workspace": plan.workspace_id})
    if plan.collection_action == "create":
        response = client.request("POST", "/collections" + workspace_query,
                                  {"collection": publication.collection})
        collection_uid = _id(response.get("collection", {}))
    elif plan.collection_action == "update":
        payload = _preserve_collection_ids(publication.collection, plan.remote_collection or {})
        client.request("PUT", "/collections/" + quote(plan.collection_uid, safe=""),
                       {"collection": payload})
        collection_uid = plan.collection_uid
    else:
        collection_uid = plan.collection_uid
    if plan.environment_action == "create":
        response = client.request("POST", "/environments" + workspace_query,
                                  {"environment": publication.environment})
        environment_uid = _id(response.get("environment", {}))
    elif plan.environment_action == "update":
        client.request("PUT", "/environments/" + quote(plan.environment_uid, safe=""),
                       {"environment": publication.environment})
        environment_uid = plan.environment_uid
    else:
        environment_uid = plan.environment_uid
    return {"workspaceId": plan.workspace_id, "collectionUid": collection_uid,
            "environmentUid": environment_uid, "documentationPublication": "not requested"}


def publication_payload(publication, environment_uid: str) -> dict:
    """Minimal documented request for one public environment on one collection."""
    if not environment_uid:
        raise PostmanError("Public environment UID is required for documentation publication")
    colors = {"highlight": "245B8F", "rightSidebar": "F4F6F8", "topBar": "FFFFFF"}
    return {
        "customColor": colors,
        "customization": {
            "metaTags": [
                {"name": "title", "value": publication.collection["info"]["name"]},
                {"name": "description", "value": "Documentación pública del web service."},
            ],
            "appearance": {"default": "light", "themes": [
                {"name": "light", "colors": colors},
                {"name": "dark", "colors": colors},
            ]},
        },
        "environmentUid": environment_uid,
        "documentationLayout": "classic-single-column",
    }


def publish_public(client: PostmanClient, plan: RemotePlan, publication) -> dict:
    """Explicitly expose the synced collection; never called by sync_public."""
    _validate_outgoing(publication)
    if (plan.collection_action != "unchanged" or plan.environment_action != "unchanged"
            or not plan.collection_uid or not plan.environment_uid):
        raise PostmanError("Sync public artifacts first; publication requires an unchanged remote plan")
    endpoint = "/collections/" + quote(plan.collection_uid, safe="") + "/public-documentations"
    result = client.request("PUT", endpoint, publication_payload(publication, plan.environment_uid))
    if result.get("published") is not True:
        raise PostmanError("Postman did not confirm documentation publication")
    return {"collectionUid": plan.collection_uid, "environmentUid": plan.environment_uid,
            "published": True, "publicUrl": result.get("publicUrl")}


def bootstrap_public(client: PostmanClient, initial_plan: RemotePlan, publication) -> dict:
    """First publication only: create both assets, verify, then publish once.

    An existing collection or environment may already have publication settings
    that the documented Postman API cannot read. We never overwrite them here.
    """
    if initial_plan.collection_action != "create" or initial_plan.environment_action != "create":
        raise PostmanError("Bootstrap requires new public assets; review existing publication in Postman")
    synced = sync_public(client, initial_plan, publication)
    checked = plan_sync(client, initial_plan.workspace_id, publication)
    if (checked.collection_uid != synced["collectionUid"]
            or checked.environment_uid != synced["environmentUid"]):
        raise PostmanError("Created Postman resources could not be verified; documentation remains unpublished")
    return publish_public(client, checked, publication)
