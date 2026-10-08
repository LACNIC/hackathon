#!/usr/bin/env python3
"""Narrow Uptime Kuma 2.2.1 Socket.IO client for bounded deployment windows."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


class KumaError(Exception):
    pass


class KumaClient:
    def __init__(self, url: str, username: str, password: str):
        if not url.startswith("https://") or not username or not password:
            raise KumaError("Kuma requires HTTPS and Jenkins username/password")
        self.url = url.rstrip("/") + "/socket.io/"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        handshake = self._request("GET", {"EIO": 4, "transport": "polling", "t": time.monotonic_ns()})
        if not handshake.startswith("0"):
            raise KumaError("unexpected Engine.IO handshake")
        self.sid = json.loads(handshake[1:].split("\x1e")[0])["sid"]
        self.params = {"EIO": 4, "transport": "polling", "sid": self.sid}
        self.sequence = 1
        self.acks: dict[int, object] = {}
        self.events: dict[str, list] = {}
        self.post("40")
        self.poll()
        self.call("login", {"username": username, "password": password})

    def _request(self, method: str, params: dict, data: str | None = None) -> str:
        request = Request(self.url + "?" + urlencode(params),
                          data=data.encode() if data is not None else None,
                          method=method)
        if data is not None:
            request.add_header("Content-Type", "text/plain;charset=UTF-8")
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except Exception as exc:
            # HTTP error bodies can contain credentials or session tokens.
            raise KumaError(f"Kuma {method} transport failed ({type(exc).__name__})") from None

    def post(self, packet: str) -> None:
        self._request("POST", self.params, packet)

    def poll(self) -> None:
        for packet in self._request("GET", self.params).split("\x1e"):
            if packet == "2":
                self.post("3")
            elif packet.startswith("43"):
                match = re.fullmatch(r"43(\d+)(.*)", packet, re.S)
                if match:
                    self.acks[int(match.group(1))] = json.loads(match.group(2))
            elif packet.startswith("42"):
                match = re.fullmatch(r"42(?:\d+)?(\[.*\])", packet, re.S)
                if match:
                    event = json.loads(match.group(1))
                    self.events[event[0]] = event[1:]

    def call(self, event: str, *args):
        ident = self.sequence
        self.sequence += 1
        self.post("42" + str(ident) + json.dumps([event, *args], separators=(",", ":")))
        deadline = time.monotonic() + 45
        while ident not in self.acks:
            if time.monotonic() > deadline:
                raise KumaError(f"{event} timed out; inspect the unique window before retrying")
            self.poll()
        values = self.acks.pop(ident)
        result = values[0] if isinstance(values, list) and len(values) == 1 else values
        if event == "login" and isinstance(result, dict) and result.get("tokenRequired"):
            raise KumaError("Kuma account requires 2FA; this Jenkins client supports username/password login only")
        if not isinstance(result, dict) or not result.get("ok"):
            raise KumaError(f"{event} was rejected")
        return result

    def monitor(self, ident: int) -> dict:
        return self.call("getMonitor", ident)["monitor"]

    def maintenance(self, ident: int) -> dict:
        return self.call("getMaintenance", ident)["maintenance"]

    def maintenances(self) -> list[dict]:
        self.events.pop("maintenanceList", None)
        self.call("getMaintenanceList")
        values = self.events["maintenanceList"][0]
        return list(values.values()) if isinstance(values, dict) else list(values)

    def linked_monitors(self, ident: int) -> set[int]:
        return {row["id"] for row in self.call("getMonitorMaintenance", ident)["monitors"]}

    def check_group(self, ident: int, expected_path: list[str]) -> None:
        path = []
        visited = set()
        while ident is not None:
            if ident in visited:
                raise KumaError("group ancestry contains a cycle")
            visited.add(ident)
            try:
                monitor = self.monitor(ident)
            except KumaError as exc:
                if str(exc) != "getMonitor was rejected":
                    raise
                raise KumaError("Kuma account cannot read a deployment group or ancestor; verify ownership") from None
            if monitor["type"] != "group":
                raise KumaError("maintenance target is not a group")
            path.insert(0, monitor["name"])
            ident = monitor.get("parent")
        if path != expected_path or not path or path[0] != "Dev-Gamma":
            raise KumaError("Kuma group path differs from local deployment config")

    def begin(self, group_id: int, group_path: list[str], title: str, minutes: int) -> int:
        self.check_group(group_id, group_path)
        if any(row["title"] == title for row in self.maintenances()):
            raise KumaError("deployment window title already exists")
        now = datetime.now(timezone.utc)
        start = (now - timedelta(seconds=60)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        end = (now + timedelta(minutes=minutes)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload = {"title": title, "description": "AI Harness deployment attempt",
                   "strategy": "single", "intervalDay": 1, "timezoneOption": "UTC",
                   "active": True, "dateRange": [start, end],
                   "timeRange": [None, None], "weekdays": [], "daysOfMonth": []}
        ident = self.call("addMaintenance", payload)["maintenanceID"]
        try:
            self.call("addMonitorMaintenance", ident, [{"id": group_id}])
            if self.linked_monitors(ident) != {group_id}:
                raise KumaError("group association failed verification")
            result = self.maintenance(ident)
            if result["title"] != title or result["strategy"] != "single" or result["status"] != "under-maintenance":
                raise KumaError("maintenance window is not active")
        except Exception:
            # The unique window may already exist after a timeout. Keep its
            # bounded expiry and report it; never guess another window's ID.
            raise KumaError(f"window {ident} could not be confirmed; inspect it before retrying") from None
        return ident

    def end(self, ident: int, group_id: int, title: str) -> None:
        maintenance = self.maintenance(ident)
        if (maintenance["title"] != title or maintenance["strategy"] != "single"
                or self.linked_monitors(ident) != {group_id}):
            raise KumaError("window identity or group differs; refusing to close")
        self.call("deleteMaintenance", ident)
        if any(row["id"] == ident for row in self.maintenances()):
            raise KumaError("window closure failed verification")


def from_environment(url: str) -> KumaClient:
    return KumaClient(url, os.environ.get("KUMA_USERNAME", ""), os.environ.get("KUMA_PASSWORD", ""))
