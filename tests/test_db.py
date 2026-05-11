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
