from __future__ import annotations

import time
from datetime import timedelta
from dataclasses import asdict, dataclass
from typing import Any

from .config import SalesBrainConfig
from .db import SalesBrainStore
from .timeutil import iso_now, next_daily_run, next_interval_run, next_weekly_run, now_in_zone, parse_iso_datetime


DEFAULT_JOB_SPECS = [
    {
        "job_name": "eboss_sync",
        "handler_name": "sync_eboss",
        "schedule_kind": "daily_time",
        "schedule_value": "02:00",
    },
    {
        "job_name": "morning_analysis",
        "handler_name": "morning_analysis",
        "schedule_kind": "daily_time",
        "schedule_value": "06:00",
    },
    {
        "job_name": "due_task_scan",
        "handler_name": "scan_due_tasks",
        "schedule_kind": "interval_minutes",
        "schedule_value": "5",
    },
    {
        "job_name": "work_followup_0830",
        "handler_name": "work_followup",
        "schedule_kind": "daily_time",
        "schedule_value": "08:30",
    },
    {
        "job_name": "work_followup_1330",
        "handler_name": "work_followup",
        "schedule_kind": "daily_time",
        "schedule_value": "13:30",
    },
    {
        "job_name": "work_followup_1930",
        "handler_name": "work_followup",
        "schedule_kind": "daily_time",
        "schedule_value": "19:30",
    },
    {
        "job_name": "daily_report_review",
        "handler_name": "daily_report_review",
        "schedule_kind": "daily_time",
        "schedule_value": "22:00",
    },
    {
        "job_name": "workflow_reflection",
        "handler_name": "workflow_reflection",
        "schedule_kind": "daily_time",
        "schedule_value": "23:30",
    },
    {
        "job_name": "weekly_summary",
        "handler_name": "weekly_summary",
        "schedule_kind": "weekly_day_time",
        "schedule_value": "fri 17:30",
    },
    {
        "job_name": "salesbrain_update_check",
        "handler_name": "salesbrain_update_check",
        "schedule_kind": "daily_time",
        "schedule_value": "09:00",
    },
    {
        "job_name": "workflow_inbox_review",
        "handler_name": "workflow_inbox_review",
        "schedule_kind": "interval_minutes",
        "schedule_value": "5",
    },
]


MANAGED_FIXED_JOB_NAMES = {
    spec["job_name"]
    for spec in DEFAULT_JOB_SPECS
    if not spec["job_name"].startswith("work_followup_")
}


def _is_managed_job(job_name: str) -> bool:
    return job_name in MANAGED_FIXED_JOB_NAMES or job_name.startswith("work_followup_")


def job_specs_from_config(config: SalesBrainConfig) -> list[dict[str, str]]:
    followup_specs = []
    for index, schedule_value in enumerate(config.work_followup_times, start=1):
        normalized = schedule_value.replace(":", "")
        followup_specs.append(
            {
                "job_name": f"work_followup_{normalized or index}",
                "handler_name": "work_followup",
                "schedule_kind": "daily_time",
                "schedule_value": schedule_value,
            }
        )
    return [
        {**DEFAULT_JOB_SPECS[0], "schedule_value": config.daily_sync_time},
        {**DEFAULT_JOB_SPECS[1], "schedule_value": config.morning_analysis_time},
        {**DEFAULT_JOB_SPECS[2], "schedule_value": str(config.due_task_scan_minutes)},
        *followup_specs,
        {**DEFAULT_JOB_SPECS[6], "schedule_value": config.daily_report_review_time},
        {**DEFAULT_JOB_SPECS[7], "schedule_value": config.workflow_reflection_time},
        {**DEFAULT_JOB_SPECS[8], "schedule_value": config.weekly_summary_time},
        {**DEFAULT_JOB_SPECS[9], "schedule_value": config.github_update_check_time},
        DEFAULT_JOB_SPECS[10],
    ]


def compute_next_run(now, schedule_kind: str, schedule_value: str):
    if schedule_kind == "daily_time":
        return next_daily_run(now, schedule_value)
    if schedule_kind == "interval_minutes":
        return next_interval_run(now, int(schedule_value))
    if schedule_kind == "weekly_day_time":
        return next_weekly_run(now, schedule_value)
    if schedule_kind == "one_shot_at":
        return parse_iso_datetime(schedule_value)
    raise ValueError(f"unsupported_schedule_kind: {schedule_kind}")


def _loads_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    import json

    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(slots=True)
class SchedulerRunResult:
    job_name: str
    handler_name: str
    status: str
    started_at: str
    finished_at: str
    next_run_at: str
    detail: dict[str, Any]


class SalesBrainScheduler:
    def __init__(self, service, store: SalesBrainStore, config: SalesBrainConfig):
        self.service = service
        self.store = store
        self.config = config

    def touch_heartbeat(self, *, now=None) -> str:
        now = now or now_in_zone(self.config.timezone)
        now_iso = now.isoformat(timespec="seconds")
        self.store.set_state("scheduler_daemon_heartbeat_at", now_iso, now_iso=now_iso)
        return now_iso

    def seed_default_jobs(self, *, force: bool = False) -> None:
        now = now_in_zone(self.config.timezone)
        desired_specs = job_specs_from_config(self.config)
        desired_names = {spec["job_name"] for spec in desired_specs}

        for spec in desired_specs:
            existing = self.store.get_scheduler_job(spec["job_name"])
            spec_changed = bool(
                existing is not None
                and (
                    str(existing.get("handler_name", "")) != spec["handler_name"]
                    or str(existing.get("schedule_kind", "")) != spec["schedule_kind"]
                    or str(existing.get("schedule_value", "")) != spec["schedule_value"]
                    or int(existing.get("enabled", 1)) != 1
                )
            )
            if existing is not None and not force and not spec_changed:
                continue
            next_run = compute_next_run(now, spec["schedule_kind"], spec["schedule_value"])
            self.store.upsert_scheduler_job(
                job_name=spec["job_name"],
                handler_name=spec["handler_name"],
                schedule_kind=spec["schedule_kind"],
                schedule_value=spec["schedule_value"],
                next_run_at=next_run.isoformat(timespec="seconds"),
                enabled=True,
                payload_json={},
            )

        for job in self.store.list_scheduler_jobs():
            job_name = str(job.get("job_name", ""))
            if job_name in desired_names or not _is_managed_job(job_name):
                continue
            if int(job.get("enabled", 1)) != 1:
                continue
            self.store.set_scheduler_job_enabled(
                job_name,
                False,
                payload_json={
                    "disabled_by": "salesbrain_config_reconcile",
                    "disabled_at": now.isoformat(timespec="seconds"),
                },
            )

    def run_due_jobs(self, *, now=None) -> list[SchedulerRunResult]:
        now = now or now_in_zone(self.config.timezone)
        now_iso = now.isoformat(timespec="seconds")
        results: list[SchedulerRunResult] = []
        due_jobs = self.store.list_due_scheduler_jobs(now_iso)
        for job in due_jobs:
            started_at = now.isoformat(timespec="seconds")
            started_perf = time.perf_counter()
            try:
                handler_name = str(job["handler_name"])
                handler = getattr(self.service, handler_name)
                if handler_name == "planned_wake":
                    detail = handler(now=now, scheduler_job=job)
                else:
                    detail = handler(now=now)
                status = "success"
                if not isinstance(detail, dict):
                    status = "failed"
                elif detail.get("ok") is False:
                    status = "failed"
                elif isinstance(detail.get("applied"), dict) and detail["applied"].get("errors"):
                    status = "failed"
                elif isinstance(detail.get("wake_run"), dict) and detail["wake_run"].get("status") == "failed":
                    status = "failed"
            except Exception as exc:
                handler_name = str(job["handler_name"])
                detail = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
                status = "failed"
            finished_at = now_in_zone(self.config.timezone).isoformat(timespec="seconds")
            duration_seconds = round(max(0.0, time.perf_counter() - started_perf), 3)
            schedule_kind = str(job["schedule_kind"])
            schedule_value = str(job["schedule_value"])
            if schedule_kind == "one_shot_at" and status != "success":
                payload = _loads_dict(job.get("payload_json"))
                retry_minutes = int(payload.get("retry_minutes") or 5)
                next_run = now + timedelta(minutes=max(1, retry_minutes))
            else:
                next_run = compute_next_run(now, schedule_kind, schedule_value)
            self.store.update_scheduler_job_run(
                str(job["job_name"]),
                last_run_at=finished_at,
                next_run_at=next_run.isoformat(timespec="seconds"),
                payload_json={
                    "status": status,
                    "detail": detail,
                },
            )
            if schedule_kind == "one_shot_at" and status == "success":
                payload = _loads_dict(job.get("payload_json"))
                payload.update(
                    {
                        "completed_at": finished_at,
                        "auto_disabled": True,
                    }
                )
                self.store.set_scheduler_job_enabled(str(job["job_name"]), False, payload_json=payload)
            self.store.record_scheduler_job_run(
                job_name=str(job["job_name"]),
                handler_name=handler_name,
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                duration_seconds=duration_seconds,
                error=None if status == "success" else str(detail.get("error") or detail.get("message") or ""),
                detail_json=detail if isinstance(detail, dict) else {"raw": detail},
                payload_json={
                    "scheduled_next_run_at": str(job.get("next_run_at", "")),
                    "schedule_kind": schedule_kind,
                    "schedule_value": schedule_value,
                },
            )
            results.append(
                SchedulerRunResult(
                    job_name=str(job["job_name"]),
                    handler_name=handler_name,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    next_run_at=next_run.isoformat(timespec="seconds"),
                    detail=detail,
                )
            )
        return results

    def run_forever(self, *, sleep_seconds: int | None = None) -> None:
        sleep_seconds = sleep_seconds or self.config.scheduler_tick_seconds
        while True:
            self.touch_heartbeat()
            self.run_due_jobs()
            time.sleep(max(1, sleep_seconds))
