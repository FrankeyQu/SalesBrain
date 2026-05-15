from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from salesbrain.config import load_config, write_default_config
from salesbrain.eboss import EbossResponse
from salesbrain.github import GitHubCommitInfo
from salesbrain.openclaw import OpenClawWakeResult
from salesbrain.scheduler import SalesBrainScheduler
from salesbrain.service import SalesBrainService


class DummyOpenClaw:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def wake(self, kind: str, payload: dict[str, object]) -> OpenClawWakeResult:
        raw = {
            "ok": True,
            "summary": f"{kind} summary",
            "user_message": f"{kind} mentor message",
            "analysis_summary": f"{kind} analysis",
            "message_sent": True,
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


class YearFilteredEbossClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, api_id: str, params: dict[str, object] | None = None) -> EbossResponse:
        params = params or {}
        self.calls.append((api_id, dict(params)))
        if api_id == "get-user-by-name":
            payload = {"data": {"records": [{"id": "u1", "realName": "Alice"}]}}
        elif api_id == "get-daily-report-self":
            query_date = str(params.get("queryDate"))
            payload = {"data": {"queryDate": query_date, "content": f"daily report {query_date}"}}
        else:
            parent_id = str(params.get("id") or params.get("projectId") or params.get("taskObjId") or params.get("followObjId") or params.get("attachObjId") or params.get("optId") or api_id)
            payload = {
                "data": {
                    "records": [
                        {"id": f"{api_id}-current", "name": f"{api_id} current", "createTime": "2026-05-01 09:00:00", "parentId": parent_id},
                        {"id": f"{api_id}-old", "name": f"{api_id} old", "createTime": "2025-12-31 09:00:00", "parentId": parent_id},
                    ]
                }
            }
        return EbossResponse(api_id=api_id, status=200, raw=payload)

    def call_paginated(self, api_id: str, params: dict[str, object] | None = None, *, max_pages: int = 20):
        params = params or {}
        self.calls.append((api_id, dict(params)))
        if api_id == "get-project-list":
            return [
                {
                    "id": "p-current",
                    "projectName": "Current Project",
                    "createTime": "2026-02-01 10:00:00",
                    "history": [
                        {"year": "2025", "note": "old nested"},
                        {"year": "2026", "note": "current nested"},
                    ],
                },
                {"id": "p-old", "projectName": "Old Project", "createTime": "2025-11-01 10:00:00"},
            ]
        if api_id == "get-opportunity-list":
            return [
                {"id": "o-current", "optName": "Current Opportunity", "createTime": "2026-03-01 10:00:00"},
                {"id": "o-old", "optName": "Old Opportunity", "createTime": "2025-11-01 10:00:00"},
            ]
        if api_id == "get-customer-list":
            return [
                {"id": "c-current", "custName": "Current Customer", "createTime": "2026-04-01 10:00:00"},
                {"id": "c-old", "custName": "Old Customer", "createTime": "2025-04-01 10:00:00"},
            ]
        if api_id == "get-lead-list":
            return [
                {"id": "l-current", "leadName": "Current Lead", "createTime": "2026-04-02 10:00:00"},
                {"id": "l-old", "leadName": "Old Lead", "createTime": "2025-04-02 10:00:00"},
            ]
        if api_id == "get-task-list":
            return [
                {"id": "t-current", "taskName": "Current Task", "planStartTime": "2026-04-03 10:00:00"},
                {"id": "t-old", "taskName": "Old Task", "planStartTime": "2025-04-03 10:00:00"},
            ]
        return self.call(api_id, params).records


class CronOpenClaw(DummyOpenClaw):
    def list_cron_jobs(self):
        return [
            {
                "id": "biz-1",
                "name": "每日复盘",
                "prompt": "提醒我复盘客户跟进",
                "business_candidate": True,
            },
            {
                "id": "sys-1",
                "name": "健康检查",
                "prompt": "系统巡检",
                "business_candidate": False,
            },
        ]

    def filter_business_cron_jobs(self, jobs):
        return [job for job in jobs if job.get("business_candidate")]


class InboxReviewOpenClaw:
    def __init__(self) -> None:
        self.wakes: list[tuple[str, dict[str, object]]] = []

    def wake(self, kind: str, payload: dict[str, object]) -> OpenClawWakeResult:
        self.wakes.append((kind, payload))
        return OpenClawWakeResult(
            ok=True,
            raw={
                "ok": True,
                "summary": "发现团队同步来的新方法，需要销售确认是否采纳。",
                "user_message": "我发现团队同步来一个新方法，需要你确认是否采纳。",
                "analysis_summary": "团队方法进入 inbox，需要用户确认。",
                "message_sent": True,
                "workflow_inbox_decisions": [],
            },
        )


class AdaptiveOpenClaw:
    def __init__(self) -> None:
        self.wakes: list[str] = []

    def wake(self, kind: str, payload: dict[str, object]) -> OpenClawWakeResult:
        self.wakes.append(kind)
        if kind == "morning_analysis":
            raw = {
                "ok": True,
                "summary": "morning summary",
                "user_message": "上午先处理最高优先级事项，我会稍后再检查推进结果。",
                "analysis_summary": "晨间分析后需要继续跟进。",
                "message_sent": True,
                "next_wake_plans": [
                    {
                        "kind": "work_followup",
                        "due_at": "2026-05-11T10:00:00+08:00",
                        "reason": "Check the morning priority push.",
                    }
                ],
            }
        elif kind == "work_followup":
            raw = {
                "ok": True,
                "summary": "followup summary",
                "user_message": "现在检查上午安排的跟进动作，并继续推进下一步。",
                "analysis_summary": "跟进链路仍然活跃。",
                "message_sent": True,
                "next_wake_plans": [
                    {
                        "kind": "work_followup",
                        "due_at": "2026-05-11T12:00:00+08:00",
                        "reason": "Keep the follow-up chain alive.",
                    }
                ],
            }
        else:
            raw = {
                "ok": True,
                "summary": f"{kind} summary",
                "user_message": f"{kind} mentor message",
                "analysis_summary": f"{kind} analysis",
                "message_sent": True,
            }
        return OpenClawWakeResult(ok=True, raw=raw)


class SilentOpenClaw:
    def wake(self, kind: str, payload: dict[str, object]) -> OpenClawWakeResult:
        return OpenClawWakeResult(ok=True, raw={"ok": True, "summary": "only summary"})


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
    assert service.list_workflow_items(limit=10)[0]["sync_status"] == "ready"
    assert any(
        item["entity_type"] == "workflow_item"
        for item in service.store.list_team_sync_events(team_name="SalesBrain", limit=10)
    )
    assert service.openclaw.removed == ["biz-1"]
    service.close()


def test_missing_openclaw_bridge_fails_wake_run_instead_of_file_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg)
    service.bootstrap()

    result = service.morning_analysis(now=datetime(2026, 5, 11, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["ok"] is False
    assert "openclaw_wake_command_missing" in result["error"]
    assert result["wake_run"]["status"] == "failed"
    assert not (cfg.home / "outbox").exists()
    service.close()


def test_visible_wake_requires_mentor_message_and_delivery_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, openclaw_adapter=SilentOpenClaw())
    service.bootstrap()

    result = service.work_followup(now=datetime(2026, 5, 11, 13, 30, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["ok"] is False
    assert result["wake_run"]["status"] == "failed"
    errors = result["applied"]["errors"]
    assert any(error["error"] == "user_message_required" for error in errors)
    assert any(error["error"] == "message_sent_required" for error in errors)
    service.close()


def test_next_wake_plan_schedules_adaptive_planned_wake(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, openclaw_adapter=AdaptiveOpenClaw())
    service.bootstrap()

    result = service.morning_analysis(now=datetime(2026, 5, 11, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["ok"] is True
    scheduled = result["applied"]["scheduled_wakes"][0]
    assert scheduled["handler_name"] == "planned_wake"
    assert scheduled["schedule_kind"] == "one_shot_at"
    assert scheduled["next_run_at"] == "2026-05-11T10:00:00+08:00"
    assert scheduled["payload_json"]["wake_kind"] == "work_followup"
    service.close()


def test_planned_wake_executes_and_chains_next_wake(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    openclaw = AdaptiveOpenClaw()
    service = SalesBrainService(cfg, openclaw_adapter=openclaw)
    service.bootstrap()
    service.morning_analysis(now=datetime(2026, 5, 11, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    scheduler = SalesBrainScheduler(service, service.store, service.config)

    results = scheduler.run_due_jobs(now=datetime(2026, 5, 11, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert any(result.handler_name == "planned_wake" and result.status == "success" for result in results)
    assert openclaw.wakes == ["morning_analysis", "work_followup"]
    planned_jobs = [
        job for job in service.list_scheduler_jobs()
        if job["handler_name"] == "planned_wake" and int(job["enabled"]) == 1
    ]
    assert len(planned_jobs) == 1
    assert planned_jobs[0]["next_run_at"] == "2026-05-11T12:00:00+08:00"
    assert planned_jobs[0]["payload_json"]["wake_kind"] == "work_followup"
    service.close()


def test_work_followup_without_explicit_plan_uses_adaptive_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, openclaw_adapter=DummyOpenClaw())
    service.bootstrap()

    result = service.work_followup(now=datetime(2026, 5, 11, 13, 30, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["ok"] is True
    scheduled = result["applied"]["scheduled_wakes"][0]
    assert scheduled["next_run_at"] == "2026-05-11T15:30:00+08:00"
    assert scheduled["payload_json"]["wake_kind"] == "work_followup"
    assert scheduled["payload_json"]["payload_json"]["fallback"] is True
    service.close()


def test_business_facts_use_standard_amount_source_and_validate_task_amounts(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, openclaw_adapter=DummyOpenClaw())
    service.bootstrap()
    run_id = service.store.insert_sync_run(run_type="eboss_sync", started_at="2026-05-11T02:00:00+08:00")
    service.store.insert_raw_records(
        sync_run_id=run_id,
        api_id="get-opportunity-list",
        object_type="opportunity",
        records=[
            {
                "id": "4914",
                "optName": "宁夏联通全平台管控感知平台",
                "optAmount": "144486",
                "createTime": "2026-05-01 09:00:00",
            }
        ],
        fetched_at="2026-05-11T02:00:00+08:00",
    )
    service.store.insert_raw_records(
        sync_run_id=run_id,
        api_id="get-opportunity-detail",
        object_type="opportunity_detail",
        records=[
            {
                "id": "4914",
                "optName": "宁夏联通全平台管控感知平台",
                "expectedAmount": "129585",
                "stageName": "需求沟通",
            }
        ],
        fetched_at="2026-05-11T02:00:00+08:00",
    )

    context = service._analysis_context(now=datetime(2026, 5, 11, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    fact = context["facts_by_ref"]["opportunity:4914"]
    assert fact["amount_yuan"] == 129585
    assert fact["amount_display"] == "12.96万"
    assert fact["amount_source"].startswith("opportunity_detail.")
    assert fact["stage"] == "需求沟通"

    mismatch = service.apply_openclaw_decision(
        {
            "tasks_to_create": [
                {
                    "title": "跟进宁夏联通商机",
                    "due_at": "2026-05-11T18:00:00+08:00",
                    "source_type": "opportunity",
                    "source_ref": "4914",
                    "amount_yuan_used": 144486,
                }
            ]
        },
        source_kind="work_followup",
        now=datetime(2026, 5, 11, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        source_context=context,
    )
    assert mismatch["created_tasks"] == []
    assert "amount_yuan_used mismatch" in mismatch["errors"][0]["error"]

    applied = service.apply_openclaw_decision(
        {
            "tasks_to_create": [
                {
                    "title": "跟进宁夏联通商机",
                    "due_at": "2026-05-11T18:00:00+08:00",
                    "source_type": "opportunity",
                    "source_ref": "4914",
                    "amount_yuan_used": 129585,
                }
            ]
        },
        source_kind="work_followup",
        now=datetime(2026, 5, 11, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        source_context=context,
    )
    assert applied["errors"] == []
    assert applied["created_tasks"][0]["source_ref"] == "4914"
    service.close()


def test_business_facts_prefer_currency_money_and_expose_high_value_stale_opportunities(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, openclaw_adapter=DummyOpenClaw())
    service.bootstrap()
    run_id = service.store.insert_sync_run(run_type="eboss_sync", started_at="2026-05-11T02:00:00+08:00")
    service.store.insert_raw_records(
        sync_run_id=run_id,
        api_id="get-opportunity-list",
        object_type="opportunity",
        records=[
            {
                "id": "opp-1",
                "optName": "陕西移动 IPOSS 扩容",
                "projectEffAmount": "144486",
                "currencyMoney": "1000000",
                "followTime": "2026-04-20 10:00:00",
                "createTime": "2026-01-20 10:00:00",
            },
            {
                "id": "opp-2",
                "optName": "低金额商机",
                "currencyMoney": "90000",
                "followTime": "2026-04-01 10:00:00",
                "createTime": "2026-01-20 10:00:00",
            },
            {
                "id": "opp-3",
                "optName": "刚跟进高金额商机",
                "currencyMoney": "300000",
                "followTime": "2026-05-10 10:00:00",
                "createTime": "2026-01-20 10:00:00",
            },
        ],
        fetched_at="2026-05-11T02:00:00+08:00",
    )

    context = service._analysis_context(now=datetime(2026, 5, 11, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    fact = context["facts_by_ref"]["opportunity:opp-1"]
    assert fact["amount_yuan"] == 1_000_000
    assert fact["amount_display"] == "100万"
    assert fact["amount_source"] == "opportunity.currencyMoney"
    assert fact["last_follow_at"] == "2026-04-20T10:00:00"
    assert fact["days_since_last_follow"] == 20
    stale = context["work_state"]["high_value_stale_opportunities"]
    assert [item["object_id"] for item in stale] == ["opp-1"]

    report = service.build_initial_analysis_report()
    assert report["opportunities"]["amount_total"] == 1_390_000
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


def test_first_eboss_sync_fetches_project_and_opportunity_details(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, eboss_client=FakeEbossClient(), openclaw_adapter=DummyOpenClaw())
    service.bootstrap()

    result = service.sync_eboss(now=datetime(2026, 5, 11, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai")), force_full=True)

    assert result["ok"] is True
    assert result["sync_mode"] == "first_full"
    for object_type in (
        "project",
        "project_detail",
        "project_stage",
        "project_task",
        "project_follow_record",
        "project_budget",
        "project_forecast",
        "project_actual",
        "project_requirement",
        "project_document",
        "opportunity",
        "opportunity_detail",
        "opportunity_stage",
        "opportunity_task",
        "opportunity_follow_record",
        "opportunity_budget",
        "opportunity_forecast",
        "opportunity_actual",
    ):
        assert service.store.count_raw_records(object_type) > 0
    service.close()


def test_full_eboss_sync_limits_business_records_to_current_year(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    eboss = YearFilteredEbossClient()
    service = SalesBrainService(cfg, eboss_client=eboss, openclaw_adapter=DummyOpenClaw())
    service.bootstrap()

    result = service.sync_eboss(now=datetime(2026, 5, 11, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai")), force_full=True)

    assert result["ok"] is True
    assert result["sync_year"] == 2026
    assert service.store.count_raw_records("project") == 1
    assert service.store.count_raw_records("opportunity") == 1
    assert service.store.count_raw_records("customer") == 1
    assert service.store.count_raw_records("lead") == 1
    assert service.store.count_raw_records("eboss_task") == 1
    assert service.store.count_raw_records("project_detail") == 1
    assert service.store.count_raw_records("opportunity_follow_record") == 1
    assert all(
        "old" not in json.loads(record["payload_json"]).get("name", "").lower()
        for record in service.store.latest_raw_records(limit=200)
    )
    assert not any(
        api_id == "get-project-detail" and params.get("id") == "p-old"
        for api_id, params in eboss.calls
    )
    assert any(
        api_id == "get-project-list" and str(params.get("createTime", "")).startswith("2026-01-01")
        for api_id, params in eboss.calls
    )
    assert any(
        api_id == "get-customer-list" and str(params.get("createTime", "")).startswith("2026-01-01")
        for api_id, params in eboss.calls
    )
    project_payload = json.loads(service.store.latest_raw_records_by_type("project", limit=1)[0]["payload_json"])
    assert project_payload["history"] == [{"year": "2026", "note": "current nested"}]
    service.close()


def test_first_eboss_daily_report_backfill_skips_previous_year_days(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    eboss = FakeEbossClient()
    service = SalesBrainService(cfg, eboss_client=eboss, openclaw_adapter=DummyOpenClaw())
    service.bootstrap()

    result = service.sync_eboss(now=datetime(2026, 1, 10, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai")), force_full=True)

    assert result["ok"] is True
    assert service.store.count_raw_records("daily_report") == 10
    query_dates = {
        json.loads(record["payload_json"])["salesbrain_query_date"]
        for record in service.store.latest_raw_records_by_type("daily_report", limit=20)
    }
    assert min(query_dates) == "2026-01-01"
    assert max(query_dates) == "2026-01-10"
    assert all(date.startswith("2026-") for date in query_dates)
    assert service.store.get_state("eboss_daily_report_backfill_done") == "1"
    service.close()


def test_first_run_syncs_then_runs_initial_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, eboss_client=FakeEbossClient(), openclaw_adapter=DummyOpenClaw())

    result = service.first_run(now=datetime(2026, 5, 11, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["ok"] is True
    assert result["first_run_done"] is True
    assert service.store.count_raw_records("daily_report") == 30
    assert service.store.get_state("salesbrain_first_run_done") == "1"
    assert any(run["wake_type"] == "initial_analysis" for run in service.list_wake_runs(limit=10))
    assert service.list_tasks(status="pending", limit=10)[0]["title"] == "Follow up with customer"
    service.close()


def test_monitor_scheduler_reports_and_records_failed_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, eboss_client=FakeEbossClient(), openclaw_adapter=DummyOpenClaw())
    service.bootstrap()

    fixed_now = datetime(2026, 5, 11, 13, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    service.store.upsert_scheduler_job(
        job_name="work_followup_0830",
        handler_name="work_followup",
        schedule_kind="daily_time",
        schedule_value="08:30",
        next_run_at="2026-05-11T13:29:00+08:00",
        enabled=True,
        payload_json={},
    )
    service.work_followup = lambda *, now=None: {"ok": False, "wake_run": {"status": "failed"}}  # type: ignore[method-assign]

    report = service.monitor_scheduler(now=fixed_now)

    assert report["health_level"] == "degraded"
    assert report["due_jobs_on_entry"][0]["job_name"] == "work_followup_0830"
    assert report["run_results"][0]["status"] == "failed"
    assert report["failed_jobs"][0]["job_name"] == "work_followup_0830"
    assert service.store.list_scheduler_job_runs("work_followup_0830", limit=1)[0]["status"] == "failed"
    assert service.get_monitor_state()["last_run_at"] == "2026-05-11T13:30:00+08:00"
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
    assert service.list_wake_runs(limit=1)[0]["wake_type"] == "salesbrain_update_check"
    service.close()


def test_workflow_inbox_accept_moves_item_to_formal_workflows(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, openclaw_adapter=DummyOpenClaw())
    service.bootstrap()
    inbox = service.store.upsert_workflow_inbox_item(
        {
            "id": "inbox-1",
            "source_node_id": "node-b",
            "source_event_id": "event-1",
            "title": "Project date back-planning",
            "pattern_type": "project",
            "summary": "Back-plan project stages from contract date.",
            "sync_status": "ready",
            "now_iso": "2026-05-11T08:00:00+08:00",
        }
    )

    applied = service.apply_openclaw_decision(
        {
            "workflow_inbox_decisions": [
                {
                    "id": inbox["id"],
                    "action": "accept",
                }
            ]
        },
        source_kind="workflow_inbox_review",
        now=datetime(2026, 5, 11, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert applied["errors"] == []
    assert service.store.get_workflow_inbox_item("inbox-1")["review_status"] == "accepted"
    assert service.list_workflow_items(limit=10)[0]["title"] == "Project date back-planning"
    service.close()


def test_workflow_inbox_review_wakes_openclaw_for_user_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    openclaw = InboxReviewOpenClaw()
    service = SalesBrainService(cfg, openclaw_adapter=openclaw)
    service.bootstrap()
    service.store.upsert_workflow_inbox_item(
        {
            "id": "inbox-1",
            "source_node_id": "node-b",
            "source_event_id": "event-1",
            "title": "日报下一步承诺跟进法",
            "pattern_type": "report",
            "summary": "从日报里提取下一步承诺，并在后续日报里追踪是否兑现。",
            "sync_status": "ready",
            "now_iso": "2026-05-11T08:00:00+08:00",
        }
    )

    result = service.workflow_inbox_review(now=datetime(2026, 5, 11, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["ok"] is True
    assert openclaw.wakes[0][0] == "workflow_inbox_review"
    payload = openclaw.wakes[0][1]
    context = payload["context"]
    assert context["requires_user_confirmation"] is True
    assert context["confirmation_policy"]["do_not_accept_without_user_confirmation"] is True
    assert context["workflow_inbox_items"][0]["title"] == "日报下一步承诺跟进法"
    assert "explicit user confirmation" in payload["prompt"]
    assert service.store.get_state("workflow_inbox_last_prompt_signature") == "inbox-1"
    assert service.store.get_workflow_inbox_item("inbox-1")["review_status"] == "pending"
    service.close()


def test_workflow_inbox_review_does_not_repeat_same_prompt_unless_forced(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    openclaw = InboxReviewOpenClaw()
    service = SalesBrainService(cfg, openclaw_adapter=openclaw)
    service.bootstrap()
    service.store.upsert_workflow_inbox_item(
        {
            "id": "inbox-1",
            "source_node_id": "node-b",
            "source_event_id": "event-1",
            "title": "日报下一步承诺跟进法",
            "pattern_type": "report",
            "summary": "从日报里提取下一步承诺，并在后续日报里追踪是否兑现。",
            "sync_status": "ready",
            "now_iso": "2026-05-11T08:00:00+08:00",
        }
    )

    first = service.workflow_inbox_review(now=datetime(2026, 5, 11, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
    second = service.workflow_inbox_review(now=datetime(2026, 5, 11, 10, 35, tzinfo=ZoneInfo("Asia/Shanghai")))
    forced = service.workflow_inbox_review(
        now=datetime(2026, 5, 11, 10, 40, tzinfo=ZoneInfo("Asia/Shanghai")),
        force=True,
    )

    assert first["ok"] is True
    assert second["skipped"] is True
    assert second["already_prompted"] is True
    assert forced["ok"] is True
    assert len(openclaw.wakes) == 2
    service.close()


def test_visible_first_run_cron_inspect_and_skip_marks_state(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, openclaw_adapter=CronOpenClaw())
    service.bootstrap()

    inspect_result = service.inspect_openclaw_cron()
    skip_result = service.complete_first_cron_migration(mode="none")

    assert inspect_result["business_candidate_count"] == 1
    assert inspect_result["business_cron_candidates"][0]["name"] == "每日复盘"
    assert skip_result["skipped"] is True
    assert service.store.get_state("salesbrain_first_cron_migration_done") == "1"
    assert service.openclaw.removed == []
    service.close()


def test_visible_first_run_initial_analysis_returns_report(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, eboss_client=FakeEbossClient(), openclaw_adapter=DummyOpenClaw())
    service.bootstrap()
    service.complete_first_eboss_sync(now=datetime(2026, 5, 11, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    result = service.complete_initial_analysis(now=datetime(2026, 5, 11, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["ok"] is True
    assert service.store.get_state("salesbrain_first_initial_analysis_done") == "1"
    assert result["analysis_report"]["opportunities"]["count"] > 0
    assert "首次分析报告" in result["analysis_report"]["formatted_report"]
    service.close()


def test_first_run_resumes_missing_steps_without_repeating_sync(tmp_path, monkeypatch):
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    service = SalesBrainService(cfg, eboss_client=FakeEbossClient(), openclaw_adapter=DummyOpenClaw())
    service.bootstrap()
    service.store.set_state("salesbrain_first_eboss_full_sync_done", "1", now_iso="2026-05-11T02:00:00+08:00")

    result = service.first_run(now=datetime(2026, 5, 11, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert result["ok"] is True
    assert result["sync_result"]["skipped"] is True
    assert result["cron_migration_result"]["ok"] is True
    assert result["analysis_result"]["ok"] is True
    assert result["first_run_state"]["salesbrain_first_run_done"] is True
    service.close()
