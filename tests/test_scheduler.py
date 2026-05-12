from __future__ import annotations

from datetime import datetime
from dataclasses import replace
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from salesbrain.config import load_config, write_default_config
from salesbrain.db import SalesBrainStore
from salesbrain.scheduler import SalesBrainScheduler, compute_next_run


def test_scheduler_runs_due_jobs_and_reschedules(tmp_path):
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    store = SalesBrainStore(tmp_path / "salesbrain.sqlite")
    store.init_schema()

    service = SimpleNamespace(
        sync_eboss=lambda *, now=None: {"ok": True, "now": now.isoformat(timespec="seconds") if now else None}
    )
    scheduler = SalesBrainScheduler(service, store, cfg)

    fixed_now = datetime(2026, 5, 11, 2, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    store.upsert_scheduler_job(
        job_name="eboss_sync",
        handler_name="sync_eboss",
        schedule_kind="daily_time",
        schedule_value="02:00",
        next_run_at="2026-05-11T01:59:00+08:00",
        enabled=True,
        payload_json={},
    )

    results = scheduler.run_due_jobs(now=fixed_now)
    assert len(results) == 1
    assert results[0].status == "success"
    assert results[0].detail["ok"] is True
    assert store.get_scheduler_job("eboss_sync")["next_run_at"] == "2026-05-12T02:00:00+08:00"


def test_compute_next_run_for_daily_and_interval():
    fixed_now = datetime(2026, 5, 11, 6, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert compute_next_run(fixed_now, "daily_time", "07:00").isoformat(timespec="seconds") == "2026-05-11T07:00:00+08:00"
    assert compute_next_run(fixed_now, "daily_time", "05:00").isoformat(timespec="seconds") == "2026-05-12T05:00:00+08:00"
    assert compute_next_run(fixed_now, "interval_minutes", "5").isoformat(timespec="seconds") == "2026-05-11T06:05:00+08:00"
    assert compute_next_run(fixed_now, "one_shot_at", "2026-05-11T09:30:00+08:00").isoformat(timespec="seconds") == "2026-05-11T09:30:00+08:00"


def test_compute_next_run_for_weekly():
    fixed_now = datetime(2026, 5, 11, 6, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert compute_next_run(fixed_now, "weekly_day_time", "fri 17:30").isoformat(timespec="seconds") == "2026-05-15T17:30:00+08:00"


def test_scheduler_seeds_followup_review_and_weekly_jobs(tmp_path):
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    store = SalesBrainStore(tmp_path / "salesbrain.sqlite")
    store.init_schema()

    scheduler = SalesBrainScheduler(SimpleNamespace(), store, cfg)
    scheduler.seed_default_jobs()

    jobs = {job["job_name"]: job for job in store.list_scheduler_jobs()}
    assert "work_followup_0830" in jobs
    assert "work_followup_1330" in jobs
    assert "work_followup_1930" in jobs
    assert "daily_report_review" in jobs
    assert "weekly_summary" in jobs
    assert "salesbrain_update_check" in jobs
    assert "workflow_inbox_review" in jobs
    assert jobs["salesbrain_update_check"]["handler_name"] == "salesbrain_update_check"
    assert jobs["weekly_summary"]["schedule_kind"] == "weekly_day_time"
    assert jobs["workflow_inbox_review"]["schedule_kind"] == "interval_minutes"
    assert jobs["workflow_inbox_review"]["schedule_value"] == "5"


def test_seed_default_jobs_preserves_existing_next_run(tmp_path):
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    store = SalesBrainStore(tmp_path / "salesbrain.sqlite")
    store.init_schema()
    store.upsert_scheduler_job(
        job_name="morning_analysis",
        handler_name="morning_analysis",
        schedule_kind="daily_time",
        schedule_value="06:00",
        next_run_at="2026-05-11T06:05:00+08:00",
        enabled=True,
        payload_json={},
    )

    scheduler = SalesBrainScheduler(SimpleNamespace(), store, cfg)
    scheduler.seed_default_jobs()

    assert store.get_scheduler_job("morning_analysis")["next_run_at"] == "2026-05-11T06:05:00+08:00"


def test_seed_default_jobs_reconciles_config_changes(tmp_path):
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cfg = load_config(config_path)
    store = SalesBrainStore(tmp_path / "salesbrain.sqlite")
    store.init_schema()

    scheduler = SalesBrainScheduler(SimpleNamespace(), store, cfg)
    scheduler.seed_default_jobs()

    changed_cfg = replace(
        load_config(config_path),
        morning_analysis_time="07:00",
        work_followup_times=("09:00", "15:00"),
    )
    changed_scheduler = SalesBrainScheduler(SimpleNamespace(), store, changed_cfg)
    changed_scheduler.seed_default_jobs()

    jobs = {job["job_name"]: job for job in store.list_scheduler_jobs()}
    assert jobs["morning_analysis"]["schedule_value"] == "07:00"
    assert jobs["work_followup_0900"]["enabled"] == 1
    assert jobs["work_followup_1500"]["enabled"] == 1
    assert jobs["work_followup_0830"]["enabled"] == 0
    assert jobs["work_followup_1330"]["enabled"] == 0
    assert jobs["work_followup_1930"]["enabled"] == 0
