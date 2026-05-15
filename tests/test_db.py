from __future__ import annotations

import json

from salesbrain.db import SalesBrainStore


def test_tasks_preserve_payload_and_due_queries(tmp_path):
    store = SalesBrainStore(tmp_path / "salesbrain.sqlite")
    store.init_schema()

    created = store.create_task(
        {
            "title": "Call customer",
            "description": "Follow up on the proposal",
            "status": "pending",
            "priority": "high",
            "due_at": "2026-05-11T10:00:00+08:00",
            "remind_at": "2026-05-11T09:30:00+08:00",
            "source_type": "morning_analysis",
            "payload_json": {"origin": "eboss"},
            "now_iso": "2026-05-11T08:00:00+08:00",
        }
    )
    store.create_task(
        {
            "title": "Future task",
            "status": "pending",
            "priority": "normal",
            "due_at": "2026-05-12T10:00:00+08:00",
            "now_iso": "2026-05-11T08:00:00+08:00",
        }
    )
    store.create_task(
        {
            "title": "Due task",
            "status": "pending",
            "priority": "normal",
            "due_at": "2026-05-11T09:30:00+08:00",
            "remind_at": "2026-05-11T09:00:00+08:00",
            "now_iso": "2026-05-11T08:00:00+08:00",
        }
    )

    assert json.loads(created["payload_json"]) == {"origin": "eboss"}
    updated = store.update_task(created["id"], {"status": "done"}, now_iso="2026-05-11T09:00:00+08:00")
    assert updated["status"] == "done"
    assert json.loads(updated["payload_json"]) == {"origin": "eboss"}

    due = store.list_due_tasks("2026-05-11T10:30:00+08:00")
    assert [row["title"] for row in due] == ["Due task"]
    store.close()


def test_latest_raw_records_by_type_uses_insert_order(tmp_path):
    store = SalesBrainStore(tmp_path / "salesbrain.sqlite")
    store.init_schema()
    run_id = store.insert_sync_run(run_type="eboss_sync", started_at="2026-05-11T02:00:00+08:00")

    store.insert_raw_records(
        sync_run_id=run_id,
        api_id="get-project-list",
        object_type="project",
        records=[
            {"id": "9", "name": "Older high id"},
            {"id": "1", "name": "Newer low id"},
        ],
        fetched_at="2026-05-11T02:00:00+08:00",
    )

    records = store.latest_raw_records_by_type("project", limit=2)

    assert [record["object_name"] for record in records] == ["Newer low id", "Older high id"]
    store.close()


def test_repair_raw_record_summaries_reads_nested_eboss_data(tmp_path):
    store = SalesBrainStore(tmp_path / "salesbrain.sqlite")
    store.init_schema()
    run_id = store.insert_sync_run(run_type="eboss_sync", started_at="2026-05-11T02:00:00+08:00")
    store.insert_raw_record(
        sync_run_id=run_id,
        api_id="get-opportunity-detail",
        object_type="opportunity_detail",
        object_id=None,
        object_name=None,
        payload={
            "code": 200,
            "success": True,
            "data": {
                "id": "4914",
                "optName": "2026年宁夏联通全平台管控感知平台",
                "customer": {"id": "c1"},
            },
        },
        fetched_at="2026-05-11T02:00:00+08:00",
    )

    result = store.repair_raw_record_summaries(object_type="opportunity_detail")
    records = store.latest_raw_records_by_type("opportunity_detail", limit=1)

    assert result["repaired"] == 1
    assert records[0]["object_id"] == "4914"
    assert records[0]["object_name"] == "2026年宁夏联通全平台管控感知平台"
    store.close()


def test_get_state_tolerates_tuple_rows(tmp_path):
    store = SalesBrainStore(tmp_path / "salesbrain.sqlite")
    store.init_schema()
    store.set_state("key", "value", now_iso="2026-05-11T02:00:00+08:00")

    store.conn.row_factory = None

    assert store.get_state("key") == "value"
    store.close()


def test_team_members_and_events_are_idempotent(tmp_path):
    store = SalesBrainStore(tmp_path / "salesbrain.sqlite")
    store.init_schema()

    member = {
        "member_id": "SalesBrain:node-a",
        "team_name": "SalesBrain",
        "node_id": "node-a",
        "real_name": "Alice",
        "endpoint": "http://10.0.0.8:37611",
        "role": "sales",
        "status": "online",
        "version": 1,
        "last_seen_at": "2026-05-11T08:00:00+08:00",
        "updated_at": "2026-05-11T08:00:00+08:00",
        "payload_json": {"source": "test"},
    }
    store.upsert_team_member(member)
    stale = dict(member)
    stale["real_name"] = "Old Alice"
    stale["version"] = 0
    stale["updated_at"] = "2026-05-10T08:00:00+08:00"
    store.upsert_team_member(stale)

    assert store.get_team_member("SalesBrain:node-a")["real_name"] == "Alice"

    event = {
        "event_id": "event-1",
        "event_type": "upsert",
        "entity_type": "team_member",
        "entity_id": "SalesBrain:node-a",
        "team_name": "SalesBrain",
        "origin_node_id": "node-a",
        "version": 1,
        "created_at": "2026-05-11T08:00:00+08:00",
        "payload_json": {"member": member},
    }
    assert store.insert_team_sync_event(event) is True
    assert store.insert_team_sync_event(event) is False
    assert len(store.list_team_sync_events(team_name="SalesBrain", limit=10)) == 1
    store.close()
