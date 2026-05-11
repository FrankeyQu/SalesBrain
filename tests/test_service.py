from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from salesbrain.config import load_config, write_default_config
from salesbrain.eboss import EbossResponse
from salesbrain.github import GitHubCommitInfo
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


class FakeEbossClient:
    def call(self, api_id: str, params: dict[str, object] | None = None) -> EbossResponse:
        params = params or {}
        if api_id == "get-user-by-name":
            payload = {"data": {"records": [{"id": "u1", "realName": "Alice"}]}}
        elif api_id == "get-daily-report-self":
            query_date = str(params.get("queryDate"))
            payload = {"data": {"queryDate": query_date, "content": f"daily report {query_date}"}}
        else:
            payload = {"data": {"records": [{"id": f"{api_id}-1", "name": api_id}]}}
        return EbossResponse(api_id=api_id, status=200, raw=payload)

    def call_paginated(self, api_id: str, params: dict[str, object] | None = None, *, max_pages: int = 20):
        return self.call(api_id, params).records


def _fake_commit(sha: str = "abc123") -> GitHubCommitInfo:
    return GitHubCommitInfo(
        repo="FrankeyQu/SalesBrain",
        branch="main",
        sha=sha,
        html_url=f"https://github.com/FrankeyQu/SalesBrain/commit/{sha}",
        commit_url=f"https://api.github.com/repos/FrankeyQu/SalesBrain/commits/{sha}",
        message="change",
        author="FrankeyQu",
    )


def test_morning_analysis_creates_updates_and_reflects(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
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


def test_first_eboss_sync_backfills_30_daily_reports(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, eboss_client=FakeEbossClient(), openclaw_adapter=DummyOpenClaw())
    service.bootstrap()

    result = service.sync_eboss(now=datetime(2026, 5, 11, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["ok"] is True
    assert service.store.count_raw_records("daily_report") == 30
    assert service.store.get_state("eboss_daily_report_backfill_done") == "1"
    service.close()


def test_github_update_check_wakes_openclaw_when_remote_is_newer(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit("new-sha"))
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, openclaw_adapter=DummyOpenClaw())
    service.bootstrap()
    service.store.set_state("github_installed_revision", "old-sha", now_iso="2026-05-11T08:00:00+08:00")

    result = service.github_update_check(now=datetime(2026, 5, 11, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["ok"] is True
    assert result["update_available"] is True
    assert result["github"]["sha"] == "new-sha"
    assert service.store.get_state("github_last_prompted_revision") == "new-sha"
    assert service.list_wake_runs(limit=1)[0]["wake_type"] == "github_update_check"
    service.close()
