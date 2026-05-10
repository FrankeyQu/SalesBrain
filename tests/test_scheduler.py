from __future__ import annotations

from datetime import datetime
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
