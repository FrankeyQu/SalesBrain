from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from salesbrain.config import load_config, write_default_config
from salesbrain.openclaw import OpenClawWakeResult
from salesbrain.service import SalesBrainService


class DummyOpenClaw:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def wake(self, kind: str, payload: dict[str, object]) -> OpenClawWakeResult:
        raw = {
            "ok": True,
            "summary": f"{kind} summary",
            "tasks_to_create": [
                {
                    "title": "Follow up with customer",
                    "description": "Call back after the demo",
                    "due_at": "2026-05-11T18:00:00+08:00",
                    "remind_at": "2026-05-11T17:30:00+08:00",
                    "priority": "high",
                    "payload_json": {"source": kind},
                }
            ],
            "tasks_to_update": [
                {
                    "id": "task-1",
                    "status": "done",
                }
            ],
            "review_suggestions": [
                {
                    "title": "Good habit",
                    "suggestion": "Keep the daily follow-up cadence",
                }
            ],
            "workflow_items": [
                {
                    "title": "Daily follow-up checklist",
                    "pattern_type": "routine",
                    "summary": "A short list used before midday",
                }
            ],
            "cron_jobs_to_remove": ["biz-1"],
            "cron_jobs_to_keep": [],
        }
        return OpenClawWakeResult(ok=True, raw=raw)

    def remove_cron_job(self, job_id: str) -> bool:
        self.removed.append(job_id)
        return True


def test_morning_analysis_creates_updates_and_reflects(tmp_path):
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, openclaw_adapter=DummyOpenClaw())
    service.bootstrap()

    service.create_task(
        {
            "id": "task-1",
            "title": "Old task",
            "description": "Needs follow-up",
            "status": "pending",
            "priority": "normal",
            "due_at": "2026-05-11T12:00:00+08:00",
            "now_iso": "2026-05-11T08:00:00+08:00",
        }
    )

    result = service.morning_analysis(now=datetime(2026, 5, 11, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["ok"] is True
    assert result["applied"]["created_tasks"][0]["title"] == "Follow up with customer"
    assert result["applied"]["updated_tasks"][0]["status"] == "done"
    assert result["applied"]["review_suggestions"][0]["title"] == "Good habit"
    assert result["applied"]["workflow_items"][0]["title"] == "Daily follow-up checklist"
    assert result["applied"]["removed_cron_jobs"] == [{"job_id": "biz-1", "removed": True}]
    assert service.list_tasks(status="done", limit=10)[0]["id"] == "task-1"
    assert service.list_review_suggestions(status="open", limit=10)[0]["title"] == "Good habit"
    assert service.list_workflow_items(limit=10)[0]["title"] == "Daily follow-up checklist"
    assert service.openclaw.removed == ["biz-1"]
    service.close()
