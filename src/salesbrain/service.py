from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .config import SalesBrainConfig
from .db import SalesBrainStore
from .eboss import EbossClient
from .github import compare_commit_ids, fetch_remote_commit
from .openclaw import OpenClawAdapter
from .prompts import (
    build_daily_report_review_prompt,
    build_github_update_prompt,
    build_initial_analysis_prompt,
    build_due_task_prompt,
    build_morning_analysis_prompt,
    build_weekly_summary_prompt,
    build_work_followup_prompt,
    build_workflow_reflection_prompt,
)
from .team import TeamRuntime, TeamService
from .timeutil import iso_now, now_in_zone, parse_iso_datetime


TASK_STATUSES = {"pending", "snoozed", "done", "cancelled"}
TASK_PRIORITIES = {"low", "normal", "high", "urgent"}
GITHUB_INSTALLED_REVISION_KEY = "github_installed_revision"
GITHUB_LAST_CHECKED_REVISION_KEY = "github_last_checked_revision"
GITHUB_LAST_CHECKED_AT_KEY = "github_last_checked_at"
GITHUB_LAST_PROMPTED_REVISION_KEY = "github_last_prompted_revision"
GITHUB_BACKFILL_DONE_KEY = "eboss_daily_report_backfill_done"
FIRST_RUN_DONE_KEY = "salesbrain_first_run_done"
DAEMON_HEARTBEAT_KEY = "scheduler_daemon_heartbeat_at"
MONITOR_LAST_RUN_KEY = "scheduler_monitor_last_run_at"
MONITOR_LAST_REPORT_KEY = "scheduler_monitor_last_report_json"
MONITOR_LAST_ALERT_SIGNATURE_KEY = "scheduler_monitor_last_alert_signature"
MONITOR_STALE_SECONDS = 180


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _decode_json_columns(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in list(out.keys()):
        if key.endswith("_json") and isinstance(out[key], str):
            out[key] = _loads(out[key], {} if key != "source_task_ids_json" else [])
    return out


def _first(record: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _compact_records(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for record in records[:limit]:
        payload = _loads(record.get("payload_json"), {})
        compact.append(
            {
                "api_id": record.get("api_id"),
                "object_type": record.get("object_type"),
                "object_id": record.get("object_id"),
                "object_name": record.get("object_name"),
                "fetched_at": record.get("fetched_at"),
                "payload": payload,
            }
        )
    return compact


def _latest_iso(values: list[str | None]) -> str | None:
    parsed: list[datetime] = []
    for value in values:
        if not value:
            continue
        try:
            parsed.append(parse_iso_datetime(str(value)))
        except Exception:
            continue
    if not parsed:
        return None
    return max(parsed).isoformat(timespec="seconds")


def _seconds_since(older_iso: str | None, newer: datetime) -> int | None:
    if not older_iso:
        return None
    try:
        delta = newer - parse_iso_datetime(str(older_iso))
    except Exception:
        return None
    return max(0, int(delta.total_seconds()))


def normalize_decision(raw: dict[str, Any]) -> dict[str, Any]:
    if "decision" in raw and isinstance(raw["decision"], dict):
        raw = raw["decision"]
    elif "raw_output" in raw and isinstance(raw["raw_output"], str):
        parsed = _loads(raw["raw_output"], None)
        if isinstance(parsed, dict):
            raw = parsed
    decision = dict(raw)
    for key in (
        "tasks_to_create",
        "tasks_to_update",
        "review_suggestions",
        "workflow_items",
        "cron_jobs_to_remove",
        "cron_jobs_to_keep",
    ):
        value = decision.get(key)
        if not isinstance(value, list):
            decision[key] = []
    if "summary" not in decision:
        decision["summary"] = str(raw.get("message") or raw.get("raw_output") or "")
    return decision


class SalesBrainService:
    def __init__(
        self,
        config: SalesBrainConfig,
        *,
        store: SalesBrainStore | None = None,
        eboss_client: EbossClient | None = None,
        openclaw_adapter: OpenClawAdapter | None = None,
    ):
        self.config = config
        self.store = store or SalesBrainStore(config.db_path)
        self._eboss_client = eboss_client
        self.openclaw = openclaw_adapter or OpenClawAdapter(config)
        self._team_service: TeamService | None = None

    def close(self) -> None:
        self.store.close()

    def now_iso(self, now: datetime | None = None) -> str:
        if now is not None:
            return now.isoformat(timespec="seconds")
        return iso_now(self.config.timezone)

    def _recent_records_by_type(self, object_type: str, limit: int = 20) -> list[dict[str, Any]]:
        return _compact_records(self.store.latest_raw_records_by_type(object_type, limit), limit)

    def _analysis_context(self, *, now: datetime | None = None, daily_report_limit: int = 20) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        latest_sync = self.store.latest_sync_run()
        if latest_sync:
            latest_sync = _decode_json_columns(latest_sync)
        recent_daily_reports = self._recent_records_by_type("daily_report", daily_report_limit)
        latest_daily_report = recent_daily_reports[0] if recent_daily_reports else None
        analysis_rules = {
            "follow_up_rules": [
                "scan the last 20 days of daily reports for explicit next steps",
                "find items that were promised previously but never followed through",
                "turn each concrete next step into a task with a date and reminder",
            ],
            "vague_report_rules": [
                "flag reports with only vague adjectives, no numbers, and no next action",
                "create a task that forces the report to become specific on the next day",
            ],
            "timeline_rules": [
                "watch for projects stuck too long in需求沟通, 立项, 采购, or 合同流程",
                "use rough planning assumptions: 需求沟通 2-3 个月, 立项约 3 个月, 采购约 1 个月, 合同流程约 1 个月",
                "if the timeline is unrealistic, create a follow-up task to push the bottleneck forward",
            ],
            "weekly_rules": [
                "summarize the week and create next-week tasks directly",
                "capture repeatable workflow patterns locally",
            ],
            "product_rules": [
                "extract any new product, feature, or direction mentioned in the reports",
                "capture front-end and back-end coordination ideas as reusable workflow items",
            ],
        }
        return {
            "now": self.now_iso(now),
            "profile": self.store.get_profile(),
            "latest_sync": latest_sync,
            "latest_daily_report": latest_daily_report,
            "recent_daily_reports": recent_daily_reports,
            "recent_projects": self._recent_records_by_type("project", 20),
            "recent_opportunities": self._recent_records_by_type("opportunity", 20),
            "recent_customers": self._recent_records_by_type("customer", 20),
            "recent_leads": self._recent_records_by_type("lead", 20),
            "recent_tasks": self._recent_records_by_type("eboss_task", 20),
            "recent_follow_records": self._recent_records_by_type("follow_record", 20),
            "pending_tasks": self.list_tasks(status="pending", limit=50),
            "due_tasks": self.list_due_tasks(now=now, limit=20),
            "open_review_suggestions": self.list_review_suggestions(status="open", limit=20),
            "workflow_items": self.list_workflow_items(limit=20),
            "analysis_rules": analysis_rules,
        }

    def _weekly_analysis_context(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        week_start = now - timedelta(days=now.weekday())
        next_week_start = week_start + timedelta(days=7)
        context = self._analysis_context(now=now, daily_report_limit=7)
        context.update(
            {
                "week_start": week_start.date().isoformat(),
                "week_end": now.date().isoformat(),
                "next_week_start": next_week_start.date().isoformat(),
                "next_week_end": (next_week_start + timedelta(days=6)).date().isoformat(),
                "weekly_focus": [
                    "summarize the current week",
                    "arrange next week's follow-up checklist",
                    "turn reusable methods into local workflow_items",
                ],
            }
        )
        return context

    def bootstrap(self) -> dict[str, Any]:
        self.config.ensure_dirs()
        self.store.init_schema()
        now_iso = self.now_iso()
        self.store.ensure_profile(
            sales_name=self.config.sales_name or "unknown",
            timezone=self.config.timezone,
            now_iso=now_iso,
        )
        from .scheduler import SalesBrainScheduler

        scheduler = SalesBrainScheduler(self, self.store, self.config)
        scheduler.seed_default_jobs()
        github_state = self.ensure_github_baseline()
        team_state = None
        if self.config.team_enabled:
            team_state = self.team().announce_self(status="online")
        return {
            "ok": True,
            "home": str(self.config.home),
            "db_path": str(self.config.db_path),
            "profile": self.store.get_profile(),
            "scheduler_jobs": self.store.list_scheduler_jobs(),
            "github_state": github_state,
            "team_state": team_state,
        }

    def first_run(self, *, now: datetime | None = None) -> dict[str, Any]:
        self.bootstrap()
        now = now or now_in_zone(self.config.timezone)
        now_iso = self.now_iso(now)
        if self.store.get_state(FIRST_RUN_DONE_KEY) == "1":
            return {
                "ok": True,
                "skipped": True,
                "reason": "first_run_already_completed",
            }
        sync_result = self.sync_eboss(now=now)
        analysis_result = self.initial_analysis(now=now)
        ok = bool(sync_result.get("ok")) and bool(analysis_result.get("ok", True))
        if ok:
            self.store.set_state(FIRST_RUN_DONE_KEY, "1", now_iso=now_iso)
        return {
            "ok": ok,
            "sync_result": sync_result,
            "analysis_result": analysis_result,
            "first_run_done": ok,
        }

    def initial_analysis(self, *, now: datetime | None = None) -> dict[str, Any]:
        context = self._analysis_context(now=now, daily_report_limit=20)
        context["run_mode"] = "first_run"
        prompt = build_initial_analysis_prompt(context)
        return self._wake_openclaw(kind="initial_analysis", prompt=prompt, context=context, now=now)

    def _eboss(self) -> EbossClient:
        if self._eboss_client is None:
            if not self.config.eboss_api_key:
                raise RuntimeError("missing_eboss_api_key")
            self._eboss_client = EbossClient(
                self.config.eboss_base_url,
                self.config.eboss_api_key,
                timeout=self.config.eboss_timeout_seconds,
            )
        return self._eboss_client

    def team(self) -> TeamService:
        if self._team_service is None:
            self._team_service = TeamService(self.config, self.store)
        return self._team_service

    def get_profile(self) -> dict[str, Any]:
        profile = self.store.get_profile() or {}
        return {
            "profile": profile,
            "config": {
                "home": str(self.config.home),
                "db_path": str(self.config.db_path),
                "timezone": self.config.timezone,
                "eboss_base_url": self.config.eboss_base_url,
                "openclaw_mode": self.config.openclaw_mode,
                "github_repo": self.config.github_repo,
                "github_branch": self.config.github_branch,
            },
            "github_state": self.get_github_state(),
            "team_state": self.team().status() if self.config.team_enabled else {"enabled": False},
        }

    def get_github_state(self) -> dict[str, Any]:
        return {
            "repo": self.config.github_repo,
            "branch": self.config.github_branch,
            "update_check_time": self.config.github_update_check_time,
            "installed_revision": self.store.get_state(GITHUB_INSTALLED_REVISION_KEY),
            "last_checked_revision": self.store.get_state(GITHUB_LAST_CHECKED_REVISION_KEY),
            "last_checked_at": self.store.get_state(GITHUB_LAST_CHECKED_AT_KEY),
            "last_prompted_revision": self.store.get_state(GITHUB_LAST_PROMPTED_REVISION_KEY),
        }

    def get_monitor_state(self) -> dict[str, Any]:
        report_raw = self.store.get_state(MONITOR_LAST_REPORT_KEY)
        report: dict[str, Any] | None = None
        if report_raw:
            report = _loads(report_raw, None)
        return {
            "daemon_last_heartbeat_at": self.store.get_state(DAEMON_HEARTBEAT_KEY),
            "last_run_at": self.store.get_state(MONITOR_LAST_RUN_KEY),
            "last_alert_signature": self.store.get_state(MONITOR_LAST_ALERT_SIGNATURE_KEY),
            "last_report": report,
        }

    def _fetch_github_commit(self) -> dict[str, Any]:
        commit = fetch_remote_commit(
            self.config.github_repo,
            self.config.github_branch,
            timeout=self.config.github_timeout_seconds,
        )
        return {
            "repo": commit.repo,
            "branch": commit.branch,
            "sha": commit.sha,
            "html_url": commit.html_url,
            "commit_url": commit.commit_url,
            "message": commit.message,
            "author": commit.author,
        }

    def ensure_github_baseline(self, *, now: datetime | None = None) -> dict[str, Any]:
        now_iso = self.now_iso(now)
        installed = self.store.get_state(GITHUB_INSTALLED_REVISION_KEY)
        if installed:
            return {
                "ok": True,
                "baseline_missing": False,
                "installed_revision": installed,
            }
        try:
            commit = self._fetch_github_commit()
        except Exception as exc:
            return {
                "ok": False,
                "baseline_missing": True,
                "error": str(exc),
            }
        self.store.set_state(GITHUB_INSTALLED_REVISION_KEY, commit["sha"], now_iso=now_iso)
        self.store.set_state(GITHUB_LAST_CHECKED_REVISION_KEY, commit["sha"], now_iso=now_iso)
        self.store.set_state(GITHUB_LAST_CHECKED_AT_KEY, now_iso, now_iso=now_iso)
        return {
            "ok": True,
            "baseline_missing": True,
            "installed_revision": commit["sha"],
            "remote_revision": commit["sha"],
        }

    def sync_eboss(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        started_at = self.now_iso(now)
        run_id = self.store.insert_sync_run(
            run_type="eboss_sync",
            started_at=started_at,
            status="running",
            payload_json={"sales_name": self.config.sales_name},
        )
        counts: dict[str, int] = {}
        errors: list[dict[str, str]] = []
        client = self._eboss()

        def insert(api_id: str, object_type: str, records: list[dict[str, Any]]) -> None:
            count = self.store.insert_raw_records(
                sync_run_id=run_id,
                api_id=api_id,
                object_type=object_type,
                records=records,
                fetched_at=self.now_iso(now),
            )
            counts[object_type] = counts.get(object_type, 0) + count

        def upsert_daily_report(query_date: str, payload: dict[str, Any]) -> None:
            inserted = self.store.upsert_raw_record_by_object(
                sync_run_id=run_id,
                api_id="get-daily-report-self",
                object_type="daily_report",
                object_id=query_date,
                object_name=query_date,
                payload=payload,
                fetched_at=self.now_iso(now),
            )
            if inserted:
                counts["daily_report"] = counts.get("daily_report", 0) + 1

        try:
            user_resp = client.call(
                "get-user-by-name",
                {
                    "realName": self.config.sales_name,
                    "current": "1",
                    "size": "10",
                    "status": "1",
                },
            )
            users = user_resp.records
            if not users:
                raise RuntimeError(f"eboss_user_not_found: {self.config.sales_name}")
            user = users[0]
            user_id = _first(user, ["id", "userId", "user_id"])
            real_name = _first(user, ["realName", "name", "userName"]) or self.config.sales_name
            if not user_id:
                raise RuntimeError("eboss_user_id_missing_in_get-user-by-name")
            self.store.update_profile_eboss(
                eboss_user_id=user_id,
                eboss_real_name=real_name,
                now_iso=self.now_iso(now),
            )
            insert("get-user-by-name", "user", users)
        except Exception as exc:
            finished_at = self.now_iso()
            self.store.update_sync_run(
                run_id,
                status="failed",
                finished_at=finished_at,
                error=str(exc),
                counts_json=counts,
                payload_json={"errors": [{"api_id": "get-user-by-name", "error": str(exc)}]},
            )
            return {
                "ok": False,
                "run_id": run_id,
                "status": "failed",
                "error": str(exc),
                "counts": counts,
            }

        today_query_date = now.date().isoformat()
        page_size = str(self.config.eboss_page_size)
        sync_plan = [
            (
                "get-project-list",
                "project",
                {
                    "current": "1",
                    "size": page_size,
                    "projectStatusList": "1,10,11,12",
                    "sheet": "3",
                    "projectManagerIds": user_id,
                },
                True,
            ),
            (
                "get-opportunity-list",
                "opportunity",
                {
                    "current": "1",
                    "size": page_size,
                    "optStateList": "1,2",
                    "optTypeList": "1,2,3",
                    "opportunity": "3",
                    "chargerOpIdList": user_id,
                },
                True,
            ),
            (
                "get-customer-list",
                "customer",
                {
                    "current": "1",
                    "size": page_size,
                    "customerLifecycle": "1,2,3,4,5",
                    "cust": "7",
                    "chargeIdStr": user_id,
                },
                True,
            ),
            (
                "get-lead-list",
                "lead",
                {
                    "current": "1",
                    "size": page_size,
                    "leadStates": "1,2,3,6,7,8,9",
                    "lead": "2",
                    "assignUserIds": user_id,
                },
                True,
            ),
            (
                "get-task-list",
                "eboss_task",
                {
                    "current": "1",
                    "size": page_size,
                    "tabType": "1",
                    "groupType": "1",
                    "groupTypeIds": "2,1,7,8,9",
                    "statusTypes": "2,1,7,8,9",
                    "statuss": "5,1",
                    "taskUserIds": user_id,
                },
                True,
            ),
        ]

        successful = 0
        for api_id, object_type, params, paginated in sync_plan:
            try:
                if paginated:
                    records = client.call_paginated(
                        api_id,
                        params,
                        max_pages=self.config.eboss_max_pages,
                    )
                else:
                    records = client.call(api_id, params).records
                insert(api_id, object_type, records)
                successful += 1
            except Exception as exc:
                errors.append({"api_id": api_id, "error": str(exc)})

        try:
            response = client.call("get-daily-report-self", {"queryDate": today_query_date})
            records = response.records
            if not records:
                records = [dict(response.raw)]
            for record in records:
                payload = dict(record)
                payload["salesbrain_query_date"] = today_query_date
                upsert_daily_report(today_query_date, payload)
        except Exception as exc:
            errors.append({"api_id": "get-daily-report-self", "queryDate": today_query_date, "error": str(exc)})

        if self.store.get_state(GITHUB_BACKFILL_DONE_KEY) != "1":
            backfill_errors = 0
            backfill_success = 0
            for offset in range(29, -1, -1):
                query_date = (now - timedelta(days=offset)).date().isoformat()
                try:
                    response = client.call("get-daily-report-self", {"queryDate": query_date})
                    records = response.records
                    if not records:
                        records = [dict(response.raw)]
                    for record in records:
                        payload = dict(record)
                        payload["salesbrain_query_date"] = query_date
                        upsert_daily_report(query_date, payload)
                    backfill_success += 1
                except Exception as exc:
                    backfill_errors += 1
                    errors.append({"api_id": "get-daily-report-self", "queryDate": query_date, "error": str(exc)})
            if backfill_errors == 0 and backfill_success == 30:
                self.store.set_state(GITHUB_BACKFILL_DONE_KEY, "1", now_iso=self.now_iso(now))

        finished_at = self.now_iso()
        status = "success"
        if errors and successful:
            status = "partial_failed"
        elif errors and not successful:
            status = "failed"
        self.store.update_sync_run(
            run_id,
            status=status,
            finished_at=finished_at,
            error=json.dumps(errors, ensure_ascii=False) if errors else None,
            counts_json=counts,
            payload_json={"errors": errors, "user_id": user_id, "real_name": real_name},
        )
        return {
            "ok": status != "failed",
            "run_id": run_id,
            "status": status,
            "counts": counts,
            "errors": errors,
        }

    def github_update_check(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        now_iso = self.now_iso(now)
        try:
            remote = self._fetch_github_commit()
        except Exception as exc:
            wake = self.record_wake_result(
                {
                    "wake_type": "github_update_check",
                    "status": "failed",
                    "input_summary": "github update check failed",
                    "result_summary": "github update check failed",
                    "error": str(exc),
                    "payload_json": {"repo": self.config.github_repo, "branch": self.config.github_branch},
                },
                now=now,
            )
            return {"ok": False, "wake_run": wake, "error": str(exc)}

        installed = self.store.get_state(GITHUB_INSTALLED_REVISION_KEY)
        comparison = compare_commit_ids(installed, remote["sha"])
        self.store.set_state(GITHUB_LAST_CHECKED_REVISION_KEY, remote["sha"], now_iso=now_iso)
        self.store.set_state(GITHUB_LAST_CHECKED_AT_KEY, now_iso, now_iso=now_iso)

        if comparison["baseline_missing"]:
            self.store.set_state(GITHUB_INSTALLED_REVISION_KEY, remote["sha"], now_iso=now_iso)
            wake = self.record_wake_result(
                {
                    "wake_type": "github_update_check",
                    "status": "skipped",
                    "input_summary": "baseline established",
                    "result_summary": "baseline established",
                    "payload_json": {
                        "repo": self.config.github_repo,
                        "branch": self.config.github_branch,
                        "remote_revision": remote["sha"],
                    },
                },
                now=now,
            )
            return {
                "ok": True,
                "up_to_date": True,
                "baseline_missing": True,
                "wake_run": wake,
                "github": remote,
            }

        if not comparison["update_available"]:
            wake = self.record_wake_result(
                {
                    "wake_type": "github_update_check",
                    "status": "skipped",
                    "input_summary": "already up to date",
                    "result_summary": "SalesBrain is already up to date.",
                    "payload_json": {
                        "repo": self.config.github_repo,
                        "branch": self.config.github_branch,
                        "remote_revision": remote["sha"],
                        "installed_revision": installed,
                    },
                },
                now=now,
            )
            return {
                "ok": True,
                "up_to_date": True,
                "wake_run": wake,
                "github": remote,
            }

        context = {
            "repo": self.config.github_repo,
            "branch": self.config.github_branch,
            "installed_revision": installed,
            "remote_revision": remote["sha"],
            "remote_commit": remote,
        }
        prompt = build_github_update_prompt(context)
        result = self._wake_openclaw(kind="github_update_check", prompt=prompt, context=context, now=now)
        self.store.set_state(GITHUB_LAST_PROMPTED_REVISION_KEY, remote["sha"], now_iso=now_iso)
        return {
            "ok": result["ok"],
            "update_available": True,
            "wake_run": result["wake_run"],
            "openclaw_result": result["openclaw_result"],
            "applied": result["applied"],
            "github": remote,
        }

    def github_mark_installed(self, sha: str | None = None, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        now_iso = self.now_iso(now)
        remote = None
        if sha is None:
            remote = self._fetch_github_commit()
            sha = remote["sha"]
        self.store.set_state(GITHUB_INSTALLED_REVISION_KEY, sha, now_iso=now_iso)
        if remote is not None:
            self.store.set_state(GITHUB_LAST_CHECKED_REVISION_KEY, remote["sha"], now_iso=now_iso)
            self.store.set_state(GITHUB_LAST_CHECKED_AT_KEY, now_iso, now_iso=now_iso)
        return {
            "ok": True,
            "installed_revision": sha,
            "github": remote,
        }

    def get_eboss_snapshot(self, limit: int = 50) -> dict[str, Any]:
        latest_sync = self.store.latest_sync_run()
        if latest_sync:
            latest_sync = _decode_json_columns(latest_sync)
        return {
            "profile": self.store.get_profile(),
            "latest_sync": latest_sync,
            "records": _compact_records(self.store.latest_raw_records(limit), limit),
            "local_tasks": [_decode_json_columns(item) for item in self.store.list_tasks(status="pending", limit=50)],
            "open_review_suggestions": [
                _decode_json_columns(item)
                for item in self.store.list_review_suggestions(status="open", limit=20)
            ],
            "workflow_items": [
                _decode_json_columns(item)
                for item in self.store.list_workflow_items(limit=20)
            ],
        }

    def search_eboss(self, keyword: str, limit: int = 20) -> dict[str, Any]:
        return {
            "keyword": keyword,
            "records": _compact_records(self.store.search_raw_records(keyword, limit), limit),
        }

    def _validate_task_payload(self, task: dict[str, Any]) -> None:
        if not str(task.get("title", "")).strip():
            raise ValueError("task.title is required")
        if not str(task.get("due_at", "")).strip():
            raise ValueError("task.due_at is required")
        parse_iso_datetime(str(task["due_at"]))
        if task.get("remind_at"):
            parse_iso_datetime(str(task["remind_at"]))
        status = str(task.get("status", "pending"))
        priority = str(task.get("priority", "normal"))
        if status not in TASK_STATUSES:
            raise ValueError(f"invalid_task_status: {status}")
        if priority not in TASK_PRIORITIES:
            raise ValueError(f"invalid_task_priority: {priority}")

    def create_task(self, task: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        payload = dict(task)
        payload.setdefault("status", "pending")
        payload.setdefault("priority", "normal")
        payload.setdefault("created_by", "openclaw")
        payload["now_iso"] = self.now_iso(now)
        self._validate_task_payload(payload)
        return _decode_json_columns(self.store.create_task(payload))

    def update_task(self, task_id: str, updates: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
        if "due_at" in updates and updates["due_at"]:
            parse_iso_datetime(str(updates["due_at"]))
        if "remind_at" in updates and updates["remind_at"]:
            parse_iso_datetime(str(updates["remind_at"]))
        if "status" in updates and updates["status"] not in TASK_STATUSES:
            raise ValueError(f"invalid_task_status: {updates['status']}")
        if "priority" in updates and updates["priority"] not in TASK_PRIORITIES:
            raise ValueError(f"invalid_task_priority: {updates['priority']}")
        result = self.store.update_task(task_id, updates, now_iso=self.now_iso(now))
        return _decode_json_columns(result) if result else None

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return [_decode_json_columns(item) for item in self.store.list_tasks(status=status, limit=limit)]

    def list_review_suggestions(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return [_decode_json_columns(item) for item in self.store.list_review_suggestions(status=status, limit=limit)]

    def list_workflow_items(self, limit: int = 100) -> list[dict[str, Any]]:
        return [_decode_json_columns(item) for item in self.store.list_workflow_items(limit=limit)]

    def list_wake_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        return [_decode_json_columns(item) for item in self.store.list_wake_runs(limit=limit)]

    def list_scheduler_jobs(self) -> list[dict[str, Any]]:
        return [_decode_json_columns(item) for item in self.store.list_scheduler_jobs()]

    def list_due_tasks(self, *, now: datetime | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [
            _decode_json_columns(item)
            for item in self.store.list_due_tasks(self.now_iso(now), limit=limit)
        ]

    def create_review_suggestion(self, suggestion: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        payload = dict(suggestion)
        payload["now_iso"] = self.now_iso(now)
        if not str(payload.get("title", "")).strip():
            raise ValueError("review_suggestion.title is required")
        if not str(payload.get("suggestion", "")).strip():
            raise ValueError("review_suggestion.suggestion is required")
        return _decode_json_columns(self.store.create_review_suggestion(payload))

    def record_workflow(self, item: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        payload = dict(item)
        payload["now_iso"] = self.now_iso(now)
        if self.config.team_enabled and "sync_status" not in payload:
            payload["sync_status"] = "ready"
        for key in ("title", "pattern_type", "summary"):
            if not str(payload.get(key, "")).strip():
                raise ValueError(f"workflow.{key} is required")
        workflow = _decode_json_columns(self.store.record_workflow(payload))
        if self.config.team_enabled:
            self.team().record_workflow_event(workflow)
        return workflow

    def team_status(self) -> dict[str, Any]:
        return self.team().status()

    def team_peers(self) -> dict[str, Any]:
        return {"ok": True, "members": self.team().peers()}

    def team_announce(self) -> dict[str, Any]:
        member = self.team().announce_self(status="online")
        broadcast = self.team().broadcast_hello(status="online")
        return {"ok": True, "member": member, "broadcast": broadcast}

    def team_sync(self) -> dict[str, Any]:
        from dataclasses import asdict

        results = [asdict(result) for result in self.team().sync_known_peers()]
        return {
            "ok": all(not result["errors"] for result in results),
            "results": results,
            "members": self.team().peers(),
        }

    def team_serve(self) -> dict[str, Any]:
        runtime = TeamRuntime(self.team())
        try:
            runtime.run_forever()
        except KeyboardInterrupt:
            return {"ok": True, "stopped": True}
        return {"ok": True}

    def record_wake_result(self, wake: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        payload = dict(wake)
        payload.setdefault("started_at", self.now_iso(now))
        payload.setdefault("finished_at", self.now_iso(now))
        payload.setdefault("status", "success")
        payload.setdefault("wake_type", "manual")
        return _decode_json_columns(self.store.record_wake_run(payload))

    def apply_openclaw_decision(
        self,
        decision: dict[str, Any],
        *,
        source_kind: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_decision(decision)
        created_tasks: list[dict[str, Any]] = []
        updated_tasks: list[dict[str, Any] | None] = []
        review_suggestions: list[dict[str, Any]] = []
        workflow_items: list[dict[str, Any]] = []
        removed_cron_jobs: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        for task in normalized["tasks_to_create"]:
            if not isinstance(task, dict):
                continue
            payload = dict(task)
            payload.setdefault("source_type", source_kind)
            try:
                created_tasks.append(self.create_task(payload, now=now))
            except Exception as exc:
                errors.append({"action": "create_task", "error": str(exc), "payload": json.dumps(payload, ensure_ascii=False)})

        for task_update in normalized["tasks_to_update"]:
            if not isinstance(task_update, dict) or not task_update.get("id"):
                continue
            task_id = str(task_update["id"])
            updates = {k: v for k, v in task_update.items() if k != "id"}
            try:
                updated_tasks.append(self.update_task(task_id, updates, now=now))
            except Exception as exc:
                errors.append({"action": "update_task", "error": str(exc), "task_id": task_id})

        for suggestion in normalized["review_suggestions"]:
            if not isinstance(suggestion, dict):
                continue
            payload = dict(suggestion)
            payload.setdefault("source_type", source_kind)
            try:
                review_suggestions.append(self.create_review_suggestion(payload, now=now))
            except Exception as exc:
                errors.append({"action": "create_review_suggestion", "error": str(exc)})

        for item in normalized["workflow_items"]:
            if not isinstance(item, dict):
                continue
            try:
                workflow_items.append(self.record_workflow(item, now=now))
            except Exception as exc:
                errors.append({"action": "record_workflow", "error": str(exc)})

        for job_id in normalized["cron_jobs_to_remove"]:
            job_id = str(job_id)
            if not job_id:
                continue
            try:
                removed = self.openclaw.remove_cron_job(job_id)
                removed_cron_jobs.append({"job_id": job_id, "removed": removed})
            except Exception as exc:
                errors.append({"action": "remove_cron_job", "error": str(exc), "job_id": job_id})

        return {
            "summary": normalized.get("summary", ""),
            "created_tasks": created_tasks,
            "updated_tasks": updated_tasks,
            "review_suggestions": review_suggestions,
            "workflow_items": workflow_items,
            "removed_cron_jobs": removed_cron_jobs,
            "errors": errors,
        }

    def _wake_openclaw(
        self,
        *,
        kind: str,
        prompt: str,
        context: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        wake = self.store.record_wake_run(
            {
                "wake_type": kind,
                "started_at": self.now_iso(now),
                "status": "running",
                "input_summary": kind,
                "payload_json": {"context_preview_keys": sorted(context.keys())},
            }
        )
        try:
            result = self.openclaw.wake(
                kind,
                {
                    "run_id": wake["id"],
                    "prompt": prompt,
                    "context": context,
                },
            )
            applied = self.apply_openclaw_decision(result.raw, source_kind=kind, now=now)
            finished = self.store.update_wake_run(
                wake["id"],
                {
                    "finished_at": self.now_iso(),
                    "status": "success" if result.ok and not applied["errors"] else "failed",
                    "result_summary": applied.get("summary") or result.summary,
                    "error": json.dumps(applied["errors"], ensure_ascii=False) if applied["errors"] else None,
                    "payload_json": {
                        "openclaw_result": result.raw,
                        "applied": applied,
                    },
                },
            )
            return {
                "ok": result.ok,
                "wake_run": _decode_json_columns(finished or wake),
                "openclaw_result": result.raw,
                "applied": applied,
            }
        except Exception as exc:
            finished = self.store.update_wake_run(
                wake["id"],
                {
                    "finished_at": self.now_iso(),
                    "status": "failed",
                    "error": str(exc),
                    "payload_json": {"error_type": type(exc).__name__},
                },
            )
            return {"ok": False, "wake_run": _decode_json_columns(finished or wake), "error": str(exc)}

    def morning_analysis(self, *, now: datetime | None = None) -> dict[str, Any]:
        context = self._analysis_context(now=now, daily_report_limit=20)
        context["run_mode"] = "morning_analysis"
        prompt = build_morning_analysis_prompt(context)
        return self._wake_openclaw(kind="morning_analysis", prompt=prompt, context=context, now=now)

    def work_followup(self, *, now: datetime | None = None) -> dict[str, Any]:
        context = self._analysis_context(now=now, daily_report_limit=20)
        context["run_mode"] = "work_followup"
        context["target_times"] = list(self.config.work_followup_times)
        prompt = build_work_followup_prompt(context)
        return self._wake_openclaw(kind="work_followup", prompt=prompt, context=context, now=now)

    def daily_report_review(self, *, now: datetime | None = None) -> dict[str, Any]:
        context = self._analysis_context(now=now, daily_report_limit=20)
        context["run_mode"] = "daily_report_review"
        prompt = build_daily_report_review_prompt(context)
        return self._wake_openclaw(kind="daily_report_review", prompt=prompt, context=context, now=now)

    def weekly_summary(self, *, now: datetime | None = None) -> dict[str, Any]:
        context = self._weekly_analysis_context(now=now)
        context["run_mode"] = "weekly_summary"
        prompt = build_weekly_summary_prompt(context)
        return self._wake_openclaw(kind="weekly_summary", prompt=prompt, context=context, now=now)

    def scan_due_tasks(self, *, now: datetime | None = None) -> dict[str, Any]:
        due_tasks = self.list_due_tasks(now=now, limit=20)
        if not due_tasks:
            wake = self.record_wake_result(
                {
                    "wake_type": "due_task_scan",
                    "status": "skipped",
                    "input_summary": "no due tasks",
                    "result_summary": "no due tasks",
                    "payload_json": {},
                },
                now=now,
            )
            return {"ok": True, "skipped": True, "wake_run": wake}
        context = {
            "profile": self.store.get_profile(),
            "due_tasks": due_tasks,
            "snapshot": self.get_eboss_snapshot(limit=30),
        }
        prompt = build_due_task_prompt(context)
        return self._wake_openclaw(kind="due_task_scan", prompt=prompt, context=context, now=now)

    def workflow_reflection(self, *, now: datetime | None = None) -> dict[str, Any]:
        cron_jobs = self.openclaw.list_cron_jobs()
        context = self._analysis_context(now=now, daily_report_limit=20)
        context.update(
            {
                "run_mode": "workflow_reflection",
                "openclaw_cron_jobs": cron_jobs,
                "business_cron_candidates": self.openclaw.filter_business_cron_jobs(cron_jobs),
                "existing_workflow_items": [
                    _decode_json_columns(item)
                    for item in self.store.list_workflow_items(limit=50)
                ],
            }
        )
        prompt = build_workflow_reflection_prompt(context)
        return self._wake_openclaw(kind="workflow_reflection", prompt=prompt, context=context, now=now)

    def monitor_scheduler(self, *, now: datetime | None = None, repair: bool = True) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        now_iso = self.now_iso(now)
        from .scheduler import SalesBrainScheduler

        scheduler = SalesBrainScheduler(self, self.store, self.config)
        scheduler.seed_default_jobs()

        previous_monitor_run_at = self.store.get_state(MONITOR_LAST_RUN_KEY)
        daemon_last_heartbeat_at = self.store.get_state(DAEMON_HEARTBEAT_KEY)
        engine_last_activity_before = _latest_iso([previous_monitor_run_at, daemon_last_heartbeat_at])
        stale_before_monitor_seconds = _seconds_since(engine_last_activity_before, now)
        has_previous_activity = engine_last_activity_before is not None
        stale_before_monitor = bool(
            has_previous_activity
            and (stale_before_monitor_seconds is None or stale_before_monitor_seconds > MONITOR_STALE_SECONDS)
        )

        due_jobs_on_entry = [
            _decode_json_columns(job)
            for job in self.store.list_due_scheduler_jobs(now_iso)
        ]
        due_job_names = {str(job["job_name"]) for job in due_jobs_on_entry}

        run_results: list[dict[str, Any]] = []
        if repair and due_jobs_on_entry:
            from dataclasses import asdict

            run_results = [asdict(result) for result in scheduler.run_due_jobs(now=now)]

        jobs = self.list_scheduler_jobs()
        job_health: list[dict[str, Any]] = []
        failed_jobs: list[dict[str, Any]] = []
        overdue_jobs: list[dict[str, Any]] = []
        repaired_jobs: list[dict[str, Any]] = []
        for job in jobs:
            job_name = str(job.get("job_name", ""))
            latest_run = self.store.latest_scheduler_job_run(job_name)
            latest_run = _decode_json_columns(latest_run) if latest_run else None
            recent_runs = [
                _decode_json_columns(item)
                for item in self.store.list_scheduler_job_runs(job_name, limit=5)
            ]
            consecutive_failures = 0
            for run in recent_runs:
                if str(run.get("status")) == "failed":
                    consecutive_failures += 1
                else:
                    break

            next_run_at = str(job.get("next_run_at", ""))
            overdue_seconds = _seconds_since(next_run_at, now)
            due_on_entry = job_name in due_job_names
            latest_status = str(latest_run.get("status") if latest_run else "never")
            latest_error = latest_run.get("error") if latest_run else None
            job_state = "healthy"
            if latest_status == "failed":
                job_state = "failed"
            elif consecutive_failures >= 3:
                job_state = "unstable"
            elif due_on_entry and repair:
                job_state = "repaired"
            elif due_on_entry:
                job_state = "overdue"

            health_row = {
                "job_name": job_name,
                "handler_name": job.get("handler_name"),
                "schedule_kind": job.get("schedule_kind"),
                "schedule_value": job.get("schedule_value"),
                "next_run_at": next_run_at,
                "last_run_at": latest_run.get("started_at") if latest_run else job.get("last_run_at"),
                "latest_status": latest_status,
                "latest_error": latest_error,
                "consecutive_failures": consecutive_failures,
                "overdue_seconds": overdue_seconds,
                "due_on_entry": due_on_entry,
                "state": job_state,
            }
            job_health.append(health_row)
            if job_state in {"failed", "unstable"}:
                failed_jobs.append(health_row)
            if due_on_entry and not repair:
                overdue_jobs.append(health_row)
            if due_on_entry and repair and latest_status == "success":
                repaired_jobs.append(health_row)

        current_monitor_run_at = now_iso
        self.store.set_state(MONITOR_LAST_RUN_KEY, current_monitor_run_at, now_iso=now_iso)

        engine_last_activity_at = _latest_iso([current_monitor_run_at, daemon_last_heartbeat_at])
        engine_age_seconds = _seconds_since(engine_last_activity_at, now)

        health_level = "healthy"
        if failed_jobs or overdue_jobs or bool(
            stale_before_monitor
        ):
            health_level = "degraded"

        report = {
            "ok": True,
            "health_level": health_level,
            "now": now_iso,
            "repair_enabled": repair,
            "daemon_last_heartbeat_at": daemon_last_heartbeat_at,
            "monitor_last_run_at_before": previous_monitor_run_at,
            "monitor_last_run_at": current_monitor_run_at,
            "engine_last_activity_before_monitor": engine_last_activity_before,
            "engine_stale_before_monitor": stale_before_monitor,
            "engine_stale_before_monitor_seconds": stale_before_monitor_seconds,
            "engine_last_activity_at": engine_last_activity_at,
            "engine_age_seconds": engine_age_seconds,
            "due_jobs_on_entry": due_jobs_on_entry,
            "run_results": run_results,
            "job_health": job_health,
            "failed_jobs": failed_jobs,
            "overdue_jobs": overdue_jobs,
            "repaired_jobs": repaired_jobs,
            "monitor_tick_seconds": self.config.scheduler_tick_seconds,
        }

        alert_signature = json.dumps(
            {
                "health_level": health_level,
                "failed_jobs": sorted(job["job_name"] for job in failed_jobs),
                "overdue_jobs": sorted(job["job_name"] for job in overdue_jobs),
                "engine_stale_before_monitor": stale_before_monitor,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        alert_created = False
        alert_error: str | None = None
        if health_level == "healthy":
            self.store.delete_state(MONITOR_LAST_ALERT_SIGNATURE_KEY)
        elif self.store.get_state(MONITOR_LAST_ALERT_SIGNATURE_KEY) != alert_signature:
            try:
                self.create_review_suggestion(
                    {
                        "title": "SalesBrain 运行监控告警",
                        "suggestion": "监控发现调度出现异常，请检查失败的 job、Openclaw 返回值、daemon 心跳和最近的补跑情况。",
                        "source_type": "scheduler_monitor",
                        "source_ref": now.date().isoformat(),
                        "payload_json": report,
                        "now_iso": now_iso,
                    }
                )
                self.store.set_state(MONITOR_LAST_ALERT_SIGNATURE_KEY, alert_signature, now_iso=now_iso)
                alert_created = True
            except Exception as exc:
                alert_error = str(exc)

        if alert_error is not None:
            report["alert_error"] = alert_error
        report["alert_created"] = alert_created
        self.store.set_state(MONITOR_LAST_REPORT_KEY, json.dumps(report, ensure_ascii=False), now_iso=now_iso)
        return report
