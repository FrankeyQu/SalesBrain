from __future__ import annotations

import threading
from dataclasses import replace

from salesbrain.config import load_config, write_default_config
from salesbrain.db import SalesBrainStore
from salesbrain.team import TEAM_MESSAGE_TYPE, TeamHTTPServer, TeamService, TeamSyncResult


def _team(tmp_path, *, sales_name: str, node_id: str, port: int = 0):
    config_path = write_default_config(tmp_path / f"{node_id}.toml", sales_name=sales_name)
    cfg = replace(
        load_config(config_path),
        team_node_id=node_id,
        team_http_port=port,
        team_advertise_host="127.0.0.1",
        team_secret="",
    )
    store = SalesBrainStore(tmp_path / f"{node_id}.sqlite")
    store.init_schema()
    service = TeamService(cfg, store)
    return cfg, store, service


def test_team_broadcast_payload_adds_member(tmp_path):
    _cfg_a, _store_a, team_a = _team(tmp_path, sales_name="Alice", node_id="node-a")
    _cfg_b, store_b, team_b = _team(tmp_path, sales_name="Bob", node_id="node-b")

    payload = {
        "type": TEAM_MESSAGE_TYPE,
        "action": "hello",
        "team_name": "SalesBrain",
        "member": team_a.local_member(status="online"),
    }

    assert team_b.handle_broadcast_payload(payload) is True
    assert team_b.handle_broadcast_payload(payload) is True
    assert store_b.get_team_member("SalesBrain:node-a")["real_name"] == "Alice"
    assert len(store_b.list_team_sync_events(team_name="SalesBrain", limit=10)) == 1

    store_b.close()
    _store_a.close()


def test_team_http_sync_moves_workflow_items(tmp_path):
    _cfg_a, store_a, team_a = _team(tmp_path, sales_name="Alice", node_id="node-a")
    _cfg_b, store_b, team_b = _team(tmp_path, sales_name="Bob", node_id="node-b")

    workflow = store_a.record_workflow(
        {
            "id": "workflow-1",
            "title": "Daily follow-up checklist",
            "pattern_type": "routine",
            "summary": "Use a short checklist before noon.",
            "sync_status": "ready",
            "now_iso": "2026-05-11T08:00:00+08:00",
        }
    )
    assert team_a.record_workflow_event(workflow) is True
    assert team_a.record_workflow_event(workflow) is False

    http = TeamHTTPServer(team_b)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    port = http.server.server_address[1]
    try:
        result = team_a.sync_with_endpoint(f"http://127.0.0.1:{port}")
    finally:
        http.shutdown()

    assert result.errors == []
    assert result.pushed == 1
    assert store_b.get_workflow_item("workflow-1") is None
    inbox = store_b.list_workflow_inbox_items(review_status="pending", limit=10)
    assert inbox[0]["title"] == "Daily follow-up checklist"
    assert inbox[0]["source_node_id"] == "node-a"

    store_a.close()
    store_b.close()


def test_team_workflow_inbox_marks_duplicates(tmp_path):
    _cfg_a, store_a, team_a = _team(tmp_path, sales_name="Alice", node_id="node-a")
    _cfg_b, store_b, team_b = _team(tmp_path, sales_name="Bob", node_id="node-b")
    store_b.record_workflow(
        {
            "id": "local-workflow",
            "title": "Daily follow-up checklist",
            "pattern_type": "routine",
            "summary": "Use a short checklist before noon.",
            "sync_status": "ready",
            "now_iso": "2026-05-11T08:00:00+08:00",
        }
    )
    workflow = store_a.record_workflow(
        {
            "id": "workflow-1",
            "title": "Daily follow-up checklist",
            "pattern_type": "routine",
            "summary": "Use a short checklist before noon.",
            "sync_status": "ready",
            "now_iso": "2026-05-11T08:00:00+08:00",
        }
    )
    team_a.record_workflow_event(workflow)

    assert team_b.import_events(team_a.export_events(), source="peer") == 1
    inbox = store_b.list_workflow_inbox_items(review_status="pending", limit=10)
    assert inbox[0]["dedupe_status"] == "duplicate"
    assert store_b.get_workflow_item("workflow-1") is None

    store_a.close()
    store_b.close()


def test_team_does_not_publish_local_only_workflow_items(tmp_path):
    _cfg, store, team = _team(tmp_path, sales_name="Alice", node_id="node-a")
    workflow = store.record_workflow(
        {
            "id": "workflow-private",
            "title": "Private note cleanup",
            "pattern_type": "routine",
            "summary": "Keep this one local.",
            "sync_status": "local_only",
            "now_iso": "2026-05-11T08:00:00+08:00",
        }
    )

    assert team.record_workflow_event(workflow) is False
    assert store.list_team_sync_events(team_name="SalesBrain", limit=10) == []

    store.close()


def test_team_sync_uses_seed_then_broadcast_and_scan_when_no_peer(tmp_path):
    cfg, store, team = _team(tmp_path, sales_name="Alice", node_id="node-a")
    team.config = replace(
        cfg,
        team_seed_endpoints=("http://10.50.3.37:37611",),
        team_scan_enabled=True,
        team_scan_interval_seconds=60,
    )
    calls: list[tuple[str, str]] = []

    def sync_with_endpoint(endpoint: str, *, limit: int = 200, source: str = "peer") -> TeamSyncResult:
        calls.append((endpoint, source))
        return TeamSyncResult(endpoint=endpoint, pulled=0, pushed=0, errors=["unreachable"])

    team.sync_with_endpoint = sync_with_endpoint  # type: ignore[method-assign]
    team.broadcast_hello = lambda *, status="online": calls.append(("broadcast", "broadcast")) or {"ok": True}  # type: ignore[method-assign]
    team.scan_networks = lambda *, limit=200: calls.append(("scan", "scan")) or []  # type: ignore[method-assign]

    team.sync_known_peers()

    assert calls == [
        ("http://10.50.3.37:37611", "seed"),
        ("broadcast", "broadcast"),
        ("scan", "scan"),
    ]
    store.close()


def test_team_sync_uses_peer_without_broadcast_when_peer_is_healthy(tmp_path):
    cfg, store, team = _team(tmp_path, sales_name="Alice", node_id="node-a")
    team.config = replace(cfg, team_seed_endpoints=())
    store.upsert_team_member(
        {
            "member_id": "SalesBrain:node-b",
            "team_name": "SalesBrain",
            "node_id": "node-b",
            "real_name": "Bob",
            "endpoint": "http://127.0.0.1:37611",
            "role": "sales",
            "status": "online",
            "version": 1,
            "last_seen_at": "2026-05-11T08:00:00+08:00",
            "updated_at": "2026-05-11T08:00:00+08:00",
            "payload_json": {},
        }
    )
    calls: list[tuple[str, str]] = []

    def sync_with_endpoint(endpoint: str, *, limit: int = 200, source: str = "peer") -> TeamSyncResult:
        calls.append((endpoint, source))
        return TeamSyncResult(endpoint=endpoint, pulled=0, pushed=0, errors=[])

    team.sync_with_endpoint = sync_with_endpoint  # type: ignore[method-assign]
    team.broadcast_hello = lambda *, status="online": calls.append(("broadcast", "broadcast")) or {"ok": True}  # type: ignore[method-assign]

    team.sync_known_peers()

    assert calls == [("http://127.0.0.1:37611", "peer")]
    store.close()
