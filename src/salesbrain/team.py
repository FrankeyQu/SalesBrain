from __future__ import annotations

import hmac
import hashlib
import ipaddress
import json
import random
import socket
import threading
import time
import uuid
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import SalesBrainConfig
from .db import SalesBrainStore
from .timeutil import iso_now


NODE_ID_STATE_KEY = "team_node_id"
TEAM_MESSAGE_TYPE = "salesbrain.team.v1"
TEAM_LAST_SCAN_AT_KEY = "team_last_scan_at_monotonic"


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_json_columns(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in list(out.keys()):
        if key.endswith("_json"):
            out[key] = _loads(out[key], [] if key == "source_task_ids_json" else {})
    return out


def _sign(payload: dict[str, Any], secret: str) -> str:
    body = {k: v for k, v in payload.items() if k != "signature"}
    return hmac.new(secret.encode("utf-8"), _json_bytes(body), hashlib.sha256).hexdigest()


def _stable_event_id(*parts: Any) -> str:
    body = "|".join(str(part) for part in parts)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _verify_signature(payload: dict[str, Any], secret: str) -> bool:
    if not secret:
        return True
    signature = str(payload.get("signature") or "")
    if not signature:
        return False
    return hmac.compare_digest(signature, _sign(payload, secret))


def _best_effort_host() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def _workflow_payload(row: dict[str, Any]) -> dict[str, Any]:
    item = _decode_json_columns(row)
    return {
        "id": item["id"],
        "title": item["title"],
        "pattern_type": item["pattern_type"],
        "summary": item["summary"],
        "example_json": item.get("example_json") or {},
        "source_task_ids_json": item.get("source_task_ids_json") or [],
        "sync_status": item.get("sync_status") or "ready",
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def _with_member_source(member: dict[str, Any], *, source: str, source_endpoint: str | None = None) -> dict[str, Any]:
    row = dict(member)
    payload = row.get("payload_json")
    if not isinstance(payload, dict):
        payload = _loads(payload, {})
    if not isinstance(payload, dict):
        payload = {}
    payload["source"] = source
    if source_endpoint:
        payload["source_endpoint"] = source_endpoint
    row["payload_json"] = payload
    return row


@dataclass(slots=True)
class TeamSyncResult:
    endpoint: str
    pulled: int
    pushed: int
    errors: list[str]


class TeamService:
    def __init__(self, config: SalesBrainConfig, store: SalesBrainStore):
        self.config = config
        self.store = store

    def node_id(self) -> str:
        configured = self.config.team_node_id.strip()
        if configured:
            return configured
        existing = self.store.get_state(NODE_ID_STATE_KEY)
        if existing:
            return existing
        node_id = uuid.uuid4().hex
        self.store.set_state(NODE_ID_STATE_KEY, node_id, now_iso=iso_now(self.config.timezone))
        return node_id

    def endpoint(self) -> str:
        host = self.config.team_advertise_host or _best_effort_host()
        return f"http://{host}:{self.config.team_http_port}"

    def member_id(self, node_id: str | None = None) -> str:
        return f"{self.config.team_name}:{node_id or self.node_id()}"

    def local_member(self, *, status: str = "online") -> dict[str, Any]:
        now = iso_now(self.config.timezone)
        version = int(time.time() * 1000)
        node_id = self.node_id()
        return {
            "member_id": self.member_id(node_id),
            "team_name": self.config.team_name,
            "node_id": node_id,
            "real_name": self.config.sales_name or node_id,
            "endpoint": self.endpoint(),
            "role": self.config.team_role,
            "status": status,
            "version": version,
            "last_seen_at": now,
            "updated_at": now,
            "payload_json": {
                "source": "local",
                "http_port": self.config.team_http_port,
            },
        }

    def build_event(
        self,
        *,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        version: int | None = None,
    ) -> dict[str, Any]:
        return {
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "team_name": self.config.team_name,
            "origin_node_id": self.node_id(),
            "version": int(version or int(time.time() * 1000)),
            "created_at": iso_now(self.config.timezone),
            "payload_json": payload,
        }

    def record_event(self, event: dict[str, Any], *, source: str = "peer", source_endpoint: str | None = None) -> bool:
        inserted = self.store.insert_team_sync_event(event)
        if inserted:
            self.apply_event_payload(event, source=source, source_endpoint=source_endpoint)
        return inserted

    def apply_event_payload(self, event: dict[str, Any], *, source: str = "peer", source_endpoint: str | None = None) -> None:
        payload = event.get("payload_json") or {}
        if str(event.get("entity_type")) == "team_member":
            member = payload.get("member") if isinstance(payload, dict) else None
            if isinstance(member, dict):
                self.store.upsert_team_member(_with_member_source(member, source=source, source_endpoint=source_endpoint))
        elif str(event.get("entity_type")) == "workflow_item":
            item = payload.get("workflow_item") if isinstance(payload, dict) else None
            if isinstance(item, dict):
                self.store.upsert_workflow_inbox_item(
                    {
                        **item,
                        "id": str(event.get("event_id") or item.get("id") or uuid.uuid4().hex),
                        "source_node_id": event.get("origin_node_id"),
                        "source_event_id": event.get("event_id"),
                        "now_iso": iso_now(self.config.timezone),
                        "payload_json": {"team_event": event},
                    }
                )

    def announce_self(self, *, status: str = "online") -> dict[str, Any]:
        member = self.local_member(status=status)
        saved = self.store.upsert_team_member(member)
        event = self.build_event(
            event_type="upsert",
            entity_type="team_member",
            entity_id=member["member_id"],
            payload={"member": member},
            version=member["version"],
        )
        event["event_id"] = _stable_event_id(
            self.config.team_name,
            "team_member",
            member["member_id"],
            member.get("version") or "",
            member.get("updated_at") or "",
        )
        self.store.insert_team_sync_event(event)
        return _decode_json_columns(saved)

    def record_workflow_event(self, workflow_row: dict[str, Any]) -> bool:
        item = _workflow_payload(workflow_row)
        if item.get("sync_status") in {"local_only", "ignored"}:
            return False
        event = self.build_event(
            event_type="upsert",
            entity_type="workflow_item",
            entity_id=item["id"],
            payload={"workflow_item": item},
        )
        event["event_id"] = _stable_event_id(
            self.config.team_name,
            "workflow_item",
            item["id"],
            item.get("updated_at") or "",
        )
        return self.store.insert_team_sync_event(event)

    def publish_workflow_items(self, *, limit: int = 200) -> int:
        count = 0
        for row in self.store.list_workflow_items(limit=limit):
            if self.record_workflow_event(row):
                count += 1
        return count

    def export_events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return [
            _decode_json_columns(row)
            for row in self.store.list_team_sync_events(team_name=self.config.team_name, limit=limit)
        ]

    def import_events(
        self,
        events: list[dict[str, Any]],
        *,
        source: str = "peer",
        source_endpoint: str | None = None,
    ) -> int:
        imported = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            if str(event.get("team_name")) != self.config.team_name:
                continue
            if self.record_event(event, source=source, source_endpoint=source_endpoint):
                imported += 1
        return imported

    def peers(self) -> list[dict[str, Any]]:
        return [
            _decode_json_columns(row)
            for row in self.store.list_team_members(team_name=self.config.team_name)
        ]

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "enabled": self.config.team_enabled,
            "team_name": self.config.team_name,
            "node_id": self.node_id(),
            "endpoint": self.endpoint(),
            "members": self.peers(),
        }

    def broadcast_hello(self, *, status: str = "online") -> dict[str, Any]:
        member = self.local_member(status=status)
        payload: dict[str, Any] = {
            "type": TEAM_MESSAGE_TYPE,
            "action": "hello",
            "team_name": self.config.team_name,
            "member": member,
        }
        if self.config.team_secret:
            payload["signature"] = _sign(payload, self.config.team_secret)
        data = _json_bytes(payload)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(data, (self.config.team_broadcast_host, self.config.team_broadcast_port))
        return {"ok": True, "sent": True, "member": member}

    def handle_broadcast_payload(self, payload: dict[str, Any]) -> bool:
        if payload.get("type") != TEAM_MESSAGE_TYPE:
            return False
        if payload.get("team_name") != self.config.team_name:
            return False
        if not _verify_signature(payload, self.config.team_secret):
            return False
        member = payload.get("member")
        if not isinstance(member, dict):
            return False
        if str(member.get("node_id")) == self.node_id():
            return False
        member = _with_member_source(member, source="broadcast")
        self.store.upsert_team_member(member)
        event = self.build_event(
            event_type="upsert",
            entity_type="team_member",
            entity_id=str(member["member_id"]),
            payload={"member": member},
            version=int(member.get("version") or 1),
        )
        event["event_id"] = _stable_event_id(
            self.config.team_name,
            "team_member",
            member["member_id"],
            member.get("version") or "",
            member.get("updated_at") or "",
        )
        self.store.insert_team_sync_event(event)
        return True

    def sync_with_endpoint(self, endpoint: str, *, limit: int = 200, source: str = "peer") -> TeamSyncResult:
        base = endpoint.rstrip("/")
        errors: list[str] = []
        pulled = 0
        pushed = 0
        try:
            url = f"{base}/team/events?{urllib.parse.urlencode({'limit': str(limit)})}"
            req = urllib.request.Request(url, method="GET", headers=self._headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            events = payload.get("events") if isinstance(payload, dict) else []
            if isinstance(events, list):
                pulled = self.import_events(events, source=source, source_endpoint=base)
        except Exception as exc:
            errors.append(f"pull_failed: {exc}")

        try:
            body = json.dumps({"events": self.export_events(limit=limit)}, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/team/events",
                method="POST",
                data=body,
                headers={**self._headers(), "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            if isinstance(payload, dict):
                pushed = int(payload.get("imported") or 0)
        except Exception as exc:
            errors.append(f"push_failed: {exc}")

        return TeamSyncResult(endpoint=endpoint, pulled=pulled, pushed=pushed, errors=errors)

    def _peer_endpoints(self) -> list[str]:
        endpoints = []
        for peer in self.peers():
            endpoint = str(peer.get("endpoint") or "").rstrip("/")
            if not endpoint or str(peer.get("node_id")) == self.node_id():
                continue
            if str(peer.get("status") or "unknown") == "offline":
                continue
            endpoints.append(endpoint)
        return sorted(set(endpoints))

    def _seed_endpoints(self) -> list[str]:
        local = self.endpoint().rstrip("/")
        return sorted({endpoint.rstrip("/") for endpoint in self.config.team_seed_endpoints if endpoint and endpoint.rstrip("/") != local})

    def _endpoint_health_ok(self, endpoint: str, *, timeout: float = 0.75) -> bool:
        try:
            req = urllib.request.Request(f"{endpoint.rstrip('/')}/health", method="GET", headers=self._headers())
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 200 <= int(resp.status) < 300
        except Exception:
            return False

    def _scan_due(self) -> bool:
        raw = self.store.get_state(TEAM_LAST_SCAN_AT_KEY)
        try:
            last_scan = float(raw or "0")
        except ValueError:
            last_scan = 0.0
        return time.monotonic() - last_scan >= max(60, self.config.team_scan_interval_seconds)

    def scan_networks(self, *, limit: int = 200) -> list[TeamSyncResult]:
        if not self.config.team_scan_enabled:
            return []
        now_iso = iso_now(self.config.timezone)
        self.store.set_state(TEAM_LAST_SCAN_AT_KEY, str(time.monotonic()), now_iso=now_iso)
        endpoints: list[str] = []
        port = self.config.team_http_port
        for cidr in self.config.team_scan_cidrs:
            try:
                network = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                continue
            endpoints.extend(f"http://{host}:{port}" for host in network.hosts())
        local = self.endpoint().rstrip("/")
        known = set(self._peer_endpoints()) | set(self._seed_endpoints()) | {local}
        candidates = [endpoint for endpoint in endpoints if endpoint not in known]
        results: list[TeamSyncResult] = []
        if not candidates:
            return results
        with ThreadPoolExecutor(max_workers=max(1, self.config.team_scan_concurrency)) as pool:
            future_map = {
                pool.submit(self._endpoint_health_ok, endpoint, timeout=0.35): endpoint
                for endpoint in candidates
            }
            for future in as_completed(future_map):
                endpoint = future_map[future]
                try:
                    ok = future.result()
                except Exception:
                    ok = False
                if not ok:
                    continue
                result = self.sync_with_endpoint(endpoint, limit=limit, source="scan")
                results.append(result)
        return results

    def sync_known_peers(self, *, limit: int = 200) -> list[TeamSyncResult]:
        results: list[TeamSyncResult] = []
        self.announce_self(status="online")
        self.publish_workflow_items(limit=limit)
        seed_results = [
            self.sync_with_endpoint(endpoint, limit=limit, source="seed")
            for endpoint in self._seed_endpoints()
        ]
        results.extend(seed_results)

        peer_endpoints = self._peer_endpoints()
        if peer_endpoints:
            sample_size = max(1, min(self.config.team_peer_heartbeat_count, len(peer_endpoints)))
            peer_results: list[TeamSyncResult] = []
            for endpoint in random.sample(peer_endpoints, sample_size):
                result = self.sync_with_endpoint(endpoint, limit=limit, source="peer")
                peer_results.append(result)
                results.append(result)
            if any(not result.errors for result in peer_results):
                return results

        try:
            self.broadcast_hello(status="online")
        except Exception as exc:
            results.append(TeamSyncResult(endpoint="broadcast", pulled=0, pushed=0, errors=[f"broadcast_failed: {exc}"]))

        if not self._peer_endpoints() and self._scan_due():
            results.extend(self.scan_networks(limit=limit))
        return results

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "SalesBrain-Team",
            "X-SalesBrain-Team": self.config.team_name,
            "X-SalesBrain-Node": self.node_id(),
        }
        if self.config.team_secret:
            headers["X-SalesBrain-Signature"] = _sign(
                {"team_name": self.config.team_name, "node_id": self.node_id()},
                self.config.team_secret,
            )
        return headers

    def format_member(self, member: dict[str, Any]) -> str:
        payload = _loads(member.get("payload_json"), {})
        source = payload.get("source") if isinstance(payload, dict) else ""
        return "\n".join(
            [
                f"姓名：{member.get('real_name') or ''}",
                f"角色：{member.get('role') or ''}",
                f"节点：{member.get('node_id') or ''}",
                f"地址：{member.get('endpoint') or ''}",
                f"状态：{member.get('status') or ''}",
                f"最后在线：{member.get('last_seen_at') or ''}",
                f"来源：{source or 'unknown'}",
            ]
        )


class TeamHTTPServer:
    def __init__(self, service: TeamService):
        self.service = service
        self.server = self._build_server()

    def _build_server(self) -> ThreadingHTTPServer:
        team_service = self.service

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if not self._request_allowed():
                    self._write({"ok": False, "error": "unauthorized"}, status=401)
                    return
                if self.path.startswith("/health"):
                    self._write({"ok": True, "team": team_service.status()})
                    return
                if self.path.startswith("/team/events"):
                    query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    limit = int((query.get("limit") or ["200"])[0])
                    self._write({"ok": True, "events": team_service.export_events(limit=limit)})
                    return
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                if not self._request_allowed():
                    self._write({"ok": False, "error": "unauthorized"}, status=401)
                    return
                if not self.path.startswith("/team/events"):
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8", errors="replace") if length else "{}"
                payload = json.loads(body or "{}")
                events = payload.get("events") if isinstance(payload, dict) else []
                imported = team_service.import_events(events if isinstance(events, list) else [], source="peer")
                self._write({"ok": True, "imported": imported})

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def _request_allowed(self) -> bool:
                if self.headers.get("X-SalesBrain-Team") != team_service.config.team_name:
                    return False
                if not team_service.config.team_secret:
                    return True
                node_id = self.headers.get("X-SalesBrain-Node") or ""
                signature = self.headers.get("X-SalesBrain-Signature") or ""
                expected = _sign(
                    {"team_name": team_service.config.team_name, "node_id": node_id},
                    team_service.config.team_secret,
                )
                return bool(node_id and hmac.compare_digest(signature, expected))

            def _write(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return ThreadingHTTPServer((self.service.config.team_http_host, self.service.config.team_http_port), Handler)

    def serve_forever(self) -> None:
        self.server.serve_forever()

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class TeamBroadcastListener:
    def __init__(self, service: TeamService):
        self.service = service
        self._stopped = threading.Event()

    def run_forever(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.bind(("", self.service.config.team_broadcast_port))
            sock.settimeout(1.0)
            while not self._stopped.is_set():
                try:
                    data, _addr = sock.recvfrom(65535)
                    payload = json.loads(data.decode("utf-8", errors="replace"))
                except socket.timeout:
                    continue
                except Exception:
                    continue
                if isinstance(payload, dict):
                    self.service.handle_broadcast_payload(payload)

    def stop(self) -> None:
        self._stopped.set()


class TeamRuntime:
    def __init__(self, service: TeamService):
        self.service = service
        self.http = TeamHTTPServer(service)
        self.listener = TeamBroadcastListener(service)
        self._stopped = threading.Event()

    def run_forever(self) -> None:
        self.service.announce_self(status="online")
        threads = [
            threading.Thread(target=self.http.serve_forever, daemon=True),
            threading.Thread(target=self.listener.run_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        next_hello_at = 0.0
        next_sync_at = 0.0
        try:
            while not self._stopped.is_set():
                now = time.monotonic()
                if now >= next_hello_at:
                    self.service.announce_self(status="online")
                    next_hello_at = now + max(5, self.service.config.team_broadcast_interval_seconds)
                if now >= next_sync_at:
                    try:
                        self.service.sync_known_peers()
                    except Exception:
                        pass
                    next_sync_at = now + max(5, self.service.config.team_sync_interval_seconds)
                self._stopped.wait(1.0)
        finally:
            self.listener.stop()
            self.http.shutdown()
            self.service.announce_self(status="offline")

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stopped.set()
