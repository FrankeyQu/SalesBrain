from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from .config import SalesBrainConfig
from .db import SalesBrainStore
from .eboss import EbossClient
from .facts import BUSINESS_OBJECT_TYPES, build_business_fact_package, fact_index_from_context, parse_amount_yuan
from .github import compare_commit_ids, fetch_remote_commit
from .openclaw import OpenClawAdapter
from .prompts import (
    build_daily_report_review_prompt,
    build_first_cron_migration_prompt,
    build_github_update_prompt,
    build_initial_analysis_prompt,
    build_due_task_prompt,
    build_morning_analysis_prompt,
    build_weekly_summary_prompt,
    build_work_followup_prompt,
    build_workflow_reflection_prompt,
    build_workflow_inbox_review_prompt,
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
FIRST_EBOSS_FULL_SYNC_DONE_KEY = "salesbrain_first_eboss_full_sync_done"
FIRST_CRON_MIGRATION_DONE_KEY = "salesbrain_first_cron_migration_done"
FIRST_INITIAL_ANALYSIS_DONE_KEY = "salesbrain_first_initial_analysis_done"
FIRST_CRON_MIGRATION_SUMMARY_KEY = "salesbrain_first_cron_migration_summary_json"
FIRST_INITIAL_ANALYSIS_REPORT_KEY = "salesbrain_first_initial_analysis_report_json"
DAEMON_HEARTBEAT_KEY = "scheduler_daemon_heartbeat_at"
MONITOR_LAST_RUN_KEY = "scheduler_monitor_last_run_at"
MONITOR_LAST_REPORT_KEY = "scheduler_monitor_last_report_json"
MONITOR_LAST_ALERT_SIGNATURE_KEY = "scheduler_monitor_last_alert_signature"
MONITOR_STALE_SECONDS = 180
WORKFLOW_INBOX_LAST_PROMPT_SIGNATURE_KEY = "workflow_inbox_last_prompt_signature"
PLANNED_WAKE_JOB_PREFIX = "planned_wake__"
PLANNED_WAKE_KINDS = {
    "initial_analysis",
    "morning_analysis",
    "work_followup",
    "daily_report_review",
    "weekly_summary",
    "workflow_reflection",
    "workflow_inbox_review",
}
PLANNED_WAKE_MIN_DELAY_MINUTES = 1
PLANNED_WAKE_RETRY_MINUTES = 5
MENTOR_MESSAGE_REQUIRED_WAKE_KINDS = {
    "initial_analysis",
    "morning_analysis",
    "work_followup",
    "daily_report_review",
    "weekly_summary",
    "due_task_scan",
    "workflow_inbox_review",
    "salesbrain_update_check",
}
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
COMPACT_DATE_RE = re.compile(r"^(19\d{2}|20\d{2})\d{4}(?:\D|$)")
DATE_LIKE_KEY_PARTS = (
    "date",
    "time",
    "year",
    "month",
    "create",
    "created",
    "update",
    "updated",
    "assign",
    "follow",
    "start",
    "end",
    "finish",
    "complete",
    "plan",
    "estimated",
)
DATE_KEY_EXCLUDE_SUFFIXES = ("id", "ids")


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


def _first_payload_value(payload: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


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


def _year_range(now: datetime) -> dict[str, str]:
    year = now.year
    return {
        "year": str(year),
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "start_datetime": f"{year}-01-01 00:00:00",
        "end_datetime": f"{year}-12-31 23:59:59",
        "create_time": f"{year}-01-01 00:00:00,{year}-12-31 23:59:59",
    }


def _looks_like_date_key(key: str) -> bool:
    text = str(key).strip().lower()
    if not text or text.endswith(DATE_KEY_EXCLUDE_SUFFIXES):
        return False
    return any(part in text for part in DATE_LIKE_KEY_PARTS)


def _years_from_date_value(value: Any) -> set[int]:
    if value in (None, ""):
        return set()
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        number = int(value)
        if 1900 <= number <= 2100:
            return {number}
        if 1_000_000_000_000 <= number <= 4_102_444_800_000:
            try:
                return {datetime.fromtimestamp(number / 1000).year}
            except Exception:
                return set()
        if 1_000_000_000 <= number <= 4_102_444_800:
            try:
                return {datetime.fromtimestamp(number).year}
            except Exception:
                return set()
        return set()
    text = str(value).strip()
    if not text:
        return set()
    compact_match = COMPACT_DATE_RE.match(text)
    if compact_match:
        return {int(compact_match.group(1))}
    years = {int(match) for match in YEAR_RE.findall(text)}
    if years:
        return years
    try:
        return {parse_iso_datetime(text).year}
    except Exception:
        return set()


def _record_date_years(value: Any, *, parent_key: str = "") -> set[int]:
    years: set[int] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            if _looks_like_date_key(key_text):
                years.update(_years_from_date_value(nested))
            if isinstance(nested, (dict, list)):
                years.update(_record_date_years(nested, parent_key=key_text))
        return years
    if isinstance(value, list):
        for item in value:
            years.update(_record_date_years(item, parent_key=parent_key))
        return years
    if parent_key and _looks_like_date_key(parent_key):
        years.update(_years_from_date_value(value))
    return years


def _record_matches_year(record: dict[str, Any], year: int, *, keep_without_date: bool = True) -> bool:
    years = _record_date_years(record)
    if not years:
        return keep_without_date
    return year in years


def _prune_value_to_year(value: Any, year: int) -> Any:
    if isinstance(value, list):
        pruned: list[Any] = []
        for item in value:
            if isinstance(item, dict):
                if _record_matches_year(item, year, keep_without_date=True):
                    pruned.append(_prune_record_to_year(item, year))
            elif isinstance(item, list):
                pruned.append(_prune_value_to_year(item, year))
            else:
                pruned.append(item)
        return pruned
    if isinstance(value, dict):
        return _prune_record_to_year(value, year)
    return value


def _prune_record_to_year(record: dict[str, Any], year: int) -> dict[str, Any]:
    return {key: _prune_value_to_year(value, year) for key, value in record.items()}


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
        "workflow_inbox_decisions",
        "next_wake_plans",
        "cron_jobs_to_remove",
        "cron_jobs_to_keep",
    ):
        value = decision.get(key)
        if not isinstance(value, list):
            decision[key] = []
    if "summary" not in decision:
        decision["summary"] = str(raw.get("message") or raw.get("raw_output") or "")
    if "user_message" not in decision:
        decision["user_message"] = str(raw.get("mentor_message") or raw.get("message") or "")
    if "analysis_summary" not in decision:
        decision["analysis_summary"] = str(raw.get("analysis") or raw.get("reasoning_summary") or "")
    return decision


def _coerce_next_wake_plans(decision: dict[str, Any]) -> list[dict[str, Any]]:
    value = decision.get("next_wake_plans")
    if not value:
        value = decision.get("next_wake_plan")
    if not value:
        value = decision.get("wake_plans")
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


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
        fact_package = build_business_fact_package(self.store, now=now)
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
            "semantic_contract": fact_package["semantic_contract"],
            "business_facts": fact_package["facts"],
            "facts_by_ref": fact_package["facts_by_ref"],
            "work_state": fact_package["work_state"],
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
            "workflow_inbox_items": self.list_workflow_inbox_items(review_status="pending", limit=50),
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

    def first_run_state(self) -> dict[str, bool]:
        return {
            "salesbrain_first_run_done": self.store.get_state(FIRST_RUN_DONE_KEY) == "1",
            "salesbrain_first_eboss_full_sync_done": self.store.get_state(FIRST_EBOSS_FULL_SYNC_DONE_KEY) == "1",
            "salesbrain_first_cron_migration_done": self.store.get_state(FIRST_CRON_MIGRATION_DONE_KEY) == "1",
            "salesbrain_first_initial_analysis_done": self.store.get_state(FIRST_INITIAL_ANALYSIS_DONE_KEY) == "1",
        }

    def first_run_complete(self) -> bool:
        state = self.first_run_state()
        return all(state.values())

    def _mark_first_run_done_if_complete(self, *, now: datetime | None = None) -> None:
        now = now or now_in_zone(self.config.timezone)
        state = self.first_run_state()
        if (
            state["salesbrain_first_eboss_full_sync_done"]
            and state["salesbrain_first_cron_migration_done"]
            and state["salesbrain_first_initial_analysis_done"]
        ):
            self.store.set_state(FIRST_RUN_DONE_KEY, "1", now_iso=self.now_iso(now))

    def first_run(self, *, now: datetime | None = None) -> dict[str, Any]:
        self.bootstrap()
        now = now or now_in_zone(self.config.timezone)
        now_iso = self.now_iso(now)
        initial_state = self.first_run_state()
        if all(initial_state.values()):
            if self.store.get_state(FIRST_RUN_DONE_KEY) != "1":
                self.store.set_state(FIRST_RUN_DONE_KEY, "1", now_iso=now_iso)
            return {
                "ok": True,
                "skipped": True,
                "reason": "first_run_already_completed",
                "first_run_state": self.first_run_state(),
            }
        steps: list[dict[str, Any]] = []
        steps.append({"step": "prepare", "status": "success", "message": "[1/6] 已完成本地 SalesBrain 程序和配置检查。"})

        eboss_config_ok = bool(self.config.eboss_api_key or self._eboss_client is not None)
        steps.append(
            {
                "step": "eboss_config",
                "status": "success" if eboss_config_ok else "failed",
                "message": "[2/6] EBOSS API Key 已准备。" if eboss_config_ok else "[2/6] EBOSS API Key 缺失，首次同步无法开始。",
            }
        )
        if not eboss_config_ok:
            return {
                "ok": False,
                "progress_message": (
                    "SalesBrain 首次初始化流程：检查配置、同步 EBOSS、迁移 Openclaw cron、"
                    "首次整体分析、准备长期调度。"
                ),
                "steps": steps,
                "error": "missing_eboss_api_key",
                "first_run_state": self.first_run_state(),
                "first_run_done": False,
            }

        if self.store.get_state(FIRST_EBOSS_FULL_SYNC_DONE_KEY) == "1":
            sync_result = {"ok": True, "skipped": True, "reason": "first_eboss_full_sync_already_completed"}
        else:
            sync_result = self.sync_eboss(now=now, force_full=True)
        steps.append(
            {
                "step": "eboss_full_sync",
                "status": "success" if sync_result.get("ok") else "failed",
                "message": (
                    "[3/6] EBOSS 首次全量同步已完成。"
                    if sync_result.get("skipped")
                    else "[3/6] EBOSS 首次全量同步完成。"
                    if sync_result.get("ok")
                    else "[3/6] EBOSS 首次全量同步失败。"
                ),
                "result": sync_result,
            }
        )
        if sync_result.get("ok"):
            self.store.set_state(FIRST_EBOSS_FULL_SYNC_DONE_KEY, "1", now_iso=now_iso)
        else:
            return {
                "ok": False,
                "progress_message": (
                    "SalesBrain 首次初始化流程：检查配置、同步 EBOSS、迁移 Openclaw cron、"
                    "首次整体分析、准备长期调度。"
                ),
                "steps": steps,
                "sync_result": sync_result,
                "first_run_state": self.first_run_state(),
                "first_run_done": False,
            }

        if self.store.get_state(FIRST_CRON_MIGRATION_DONE_KEY) == "1":
            cron_result = {"ok": True, "skipped": True, "reason": "first_cron_migration_already_completed"}
        else:
            cron_result = self.first_cron_migration(now=now)
        steps.append(
            {
                "step": "cron_migration",
                "status": "success" if cron_result.get("ok") else "failed",
                "message": (
                    "[4/6] Openclaw cron 检查和迁移已完成。"
                    if cron_result.get("skipped")
                    else "[4/6] Openclaw cron 检查和迁移完成。"
                    if cron_result.get("ok")
                    else "[4/6] Openclaw cron 检查和迁移失败。"
                ),
                "result": cron_result,
            }
        )
        if cron_result.get("ok"):
            self.store.set_state(FIRST_CRON_MIGRATION_DONE_KEY, "1", now_iso=now_iso)
            self.store.set_state(FIRST_CRON_MIGRATION_SUMMARY_KEY, json.dumps(cron_result, ensure_ascii=False), now_iso=now_iso)
        else:
            return {
                "ok": False,
                "progress_message": (
                    "SalesBrain 首次初始化流程：检查配置、同步 EBOSS、迁移 Openclaw cron、"
                    "首次整体分析、准备长期调度。"
                ),
                "steps": steps,
                "sync_result": sync_result,
                "cron_migration_result": cron_result,
                "first_run_state": self.first_run_state(),
                "first_run_done": False,
            }

        if self.store.get_state(FIRST_INITIAL_ANALYSIS_DONE_KEY) == "1":
            analysis_result = {"ok": True, "skipped": True, "reason": "first_initial_analysis_already_completed"}
        else:
            analysis_result = self.initial_analysis(now=now)
        steps.append(
            {
                "step": "initial_analysis",
                "status": "success" if analysis_result.get("ok") else "failed",
                "message": (
                    "[5/6] 首次整体分析已完成。"
                    if analysis_result.get("skipped")
                    else "[5/6] 首次整体分析完成。"
                    if analysis_result.get("ok")
                    else "[5/6] 首次整体分析失败。"
                ),
                "result": analysis_result,
            }
        )
        if analysis_result.get("ok"):
            self.store.set_state(FIRST_INITIAL_ANALYSIS_DONE_KEY, "1", now_iso=now_iso)

        first_run_state = self.first_run_state()
        steps.append(
            {
                "step": "start_scheduler",
                "status": "success",
                "message": "[6/6] SalesBrain 长期调度和团队同步已准备启动。你可以随时让我修改每日分析、跟进督促、日报审阅和工作方法沉淀的时间。",
            }
        )
        ok = all(
            [
                bool(sync_result.get("ok")),
                bool(cron_result.get("ok")),
                bool(analysis_result.get("ok")),
                first_run_state["salesbrain_first_eboss_full_sync_done"],
                first_run_state["salesbrain_first_cron_migration_done"],
                first_run_state["salesbrain_first_initial_analysis_done"],
            ]
        )
        if ok:
            self.store.set_state(FIRST_RUN_DONE_KEY, "1", now_iso=now_iso)
        return {
            "ok": ok,
            "progress_message": (
                "SalesBrain 首次初始化流程：检查配置、同步 EBOSS、迁移 Openclaw cron、"
                "首次整体分析、准备长期调度。"
            ),
            "steps": steps,
            "sync_result": sync_result,
            "cron_migration_result": cron_result,
            "analysis_result": analysis_result,
            "first_run_state": self.first_run_state(),
            "first_run_done": ok,
        }

    def initial_analysis(self, *, now: datetime | None = None) -> dict[str, Any]:
        context = self._analysis_context(now=now, daily_report_limit=20)
        context["run_mode"] = "first_run"
        context["first_cron_migration"] = _loads(self.store.get_state(FIRST_CRON_MIGRATION_SUMMARY_KEY), {})
        prompt = build_initial_analysis_prompt(context)
        return self._wake_openclaw(kind="initial_analysis", prompt=prompt, context=context, now=now)

    def inspect_openclaw_cron(self) -> dict[str, Any]:
        list_cron_jobs = getattr(self.openclaw, "list_cron_jobs", lambda: [])
        filter_business = getattr(self.openclaw, "filter_business_cron_jobs", lambda jobs: [])
        cron_jobs = list_cron_jobs()
        business_candidates = filter_business(cron_jobs)
        return {
            "ok": True,
            "total": len(cron_jobs),
            "business_candidate_count": len(business_candidates),
            "cron_jobs": cron_jobs,
            "business_cron_candidates": business_candidates,
            "message": (
                f"发现 {len(business_candidates)} 个可能需要迁移的 Openclaw 遗留业务定时任务。"
                if business_candidates
                else "未发现需要迁移的 Openclaw 遗留业务定时任务。"
            ),
        }

    def first_cron_migration(
        self,
        *,
        now: datetime | None = None,
        migration_mode: str = "all",
        selected_job_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        cron_state = self.inspect_openclaw_cron()
        cron_jobs = cron_state["cron_jobs"]
        business_candidates = cron_state["business_cron_candidates"]
        selected_ids = {str(item) for item in (selected_job_ids or []) if str(item).strip()}
        if migration_mode == "selected":
            business_candidates = [
                job for job in business_candidates
                if str(job.get("id") or job.get("job_id") or "") in selected_ids
            ]
        context = self._analysis_context(now=now, daily_report_limit=20)
        context.update(
            {
                "run_mode": "first_cron_migration",
                "migration_mode": migration_mode,
                "selected_job_ids": sorted(selected_ids),
                "migration_reason": (
                    "Openclaw 自带 cron 可能因为进程重启或运行环境问题漏执行；"
                    "SalesBrain 是确定性程序调度，会持续记录心跳、任务状态和失败记录，"
                    "更适合承接销售跟进、日报审阅、工作分析这类关键定时任务。"
                ),
                "openclaw_cron_jobs": cron_jobs,
                "business_cron_candidates": business_candidates,
            }
        )
        prompt = build_first_cron_migration_prompt(context)
        return self._wake_openclaw(kind="first_cron_migration", prompt=prompt, context=context, now=now)

    def complete_first_cron_migration(
        self,
        *,
        mode: str = "all",
        selected_job_ids: list[str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        now_iso = self.now_iso(now)
        mode = mode.strip().lower()
        if mode not in {"all", "none", "selected"}:
            raise ValueError(f"unsupported_cron_migration_mode: {mode}")
        inspect_result = self.inspect_openclaw_cron()
        if mode == "none":
            result = {
                "ok": True,
                "skipped": True,
                "mode": mode,
                "inspect_result": inspect_result,
                "summary": "用户选择不迁移 Openclaw 遗留业务定时任务。",
            }
        else:
            result = self.first_cron_migration(
                now=now,
                migration_mode=mode,
                selected_job_ids=selected_job_ids or [],
            )
            result["mode"] = mode
            result["inspect_result"] = inspect_result
        if result.get("ok"):
            self.store.set_state(FIRST_CRON_MIGRATION_DONE_KEY, "1", now_iso=now_iso)
            self.store.set_state(FIRST_CRON_MIGRATION_SUMMARY_KEY, json.dumps(result, ensure_ascii=False), now_iso=now_iso)
            self._mark_first_run_done_if_complete(now=now)
        return result

    def complete_initial_analysis(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        result = self.initial_analysis(now=now)
        report = self.build_initial_analysis_report(result)
        if result.get("ok"):
            now_iso = self.now_iso(now)
            self.store.set_state(FIRST_INITIAL_ANALYSIS_DONE_KEY, "1", now_iso=now_iso)
            self.store.set_state(FIRST_INITIAL_ANALYSIS_REPORT_KEY, json.dumps(report, ensure_ascii=False), now_iso=now_iso)
            self._mark_first_run_done_if_complete(now=now)
        result["analysis_report"] = report
        return result

    def complete_first_eboss_sync(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        result = self.sync_eboss(now=now, force_full=True)
        if result.get("ok"):
            self.store.set_state(FIRST_EBOSS_FULL_SYNC_DONE_KEY, "1", now_iso=self.now_iso(now))
            self._mark_first_run_done_if_complete(now=now)
        return result

    def build_initial_analysis_report(self, analysis_result: dict[str, Any] | None = None) -> dict[str, Any]:
        opportunities = self._recent_records_by_type("opportunity", 1000)
        customers = self._recent_records_by_type("customer", 1000)
        leads = self._recent_records_by_type("lead", 1000)
        tasks = self._recent_records_by_type("eboss_task", 1000)
        daily_reports = self._recent_records_by_type("daily_report", 30)
        recent_follow_records = (
            self._recent_records_by_type("project_follow_record", 1000)
            + self._recent_records_by_type("opportunity_follow_record", 1000)
            + self._recent_records_by_type("follow_record", 1000)
        )
        fact_package = build_business_fact_package(self.store, now=now_in_zone(self.config.timezone))
        opportunity_facts = [
            fact for fact in fact_package["facts"]
            if fact.get("object_type") == "opportunity"
        ]

        def payloads(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            values: list[dict[str, Any]] = []
            for record in records:
                payload = _loads(record.get("payload_json"), {})
                if isinstance(payload, dict):
                    values.append(payload)
            return values

        opportunity_payloads = payloads(opportunities)
        customer_payloads = payloads(customers)
        lead_payloads = payloads(leads)
        follow_payloads = payloads(recent_follow_records)

        status_distribution: dict[str, int] = {}
        amount_values = [
            int(fact["amount_yuan"])
            for fact in opportunity_facts
            if fact.get("amount_yuan") is not None
        ]
        amount_total = sum(amount_values)
        amount_seen = bool(amount_values)
        key_opportunities = [
            str(fact.get("name") or fact.get("object_id"))
            for fact in opportunity_facts[:5]
            if fact.get("name") or fact.get("object_id")
        ]
        for payload in opportunity_payloads:
            status = _first_payload_value(payload, ["optState", "optStateName", "status", "statusName", "stageName"])
            if status is not None:
                status_text = str(status)
                status_distribution[status_text] = status_distribution.get(status_text, 0) + 1

        overdue_customers: list[dict[str, Any]] = []
        for payload in customer_payloads:
            name = str(_first_payload_value(payload, ["custName", "customerName", "name"]) or "未命名客户")
            last_follow = _first_payload_value(payload, ["lastFollowTime", "lastFollowDate", "followTime", "updatedAt"])
            no_follow_days = _first_payload_value(payload, ["noFollowDays", "unFollowDays", "daysSinceLastFollow"])
            item = {"name": name, "last_follow": last_follow, "no_follow_days": no_follow_days}
            if no_follow_days not in (None, ""):
                try:
                    if int(float(str(no_follow_days))) >= 7:
                        overdue_customers.append(item)
                except ValueError:
                    overdue_customers.append(item)
            elif len(overdue_customers) < 3:
                overdue_customers.append(item)

        new_leads_this_week = 0
        for payload in lead_payloads:
            created = _first_payload_value(payload, ["createTime", "createdAt", "assignTime"])
            if created:
                new_leads_this_week += 1

        applied = (analysis_result or {}).get("applied")
        if not isinstance(applied, dict):
            applied = {}
        suggestions: list[str] = []
        for task in applied.get("created_tasks") or []:
            if isinstance(task, dict) and task.get("title"):
                suggestions.append(str(task["title"]))
        for suggestion in applied.get("review_suggestions") or []:
            if isinstance(suggestion, dict) and suggestion.get("title"):
                suggestions.append(str(suggestion["title"]))
        suggestions = suggestions[:3]
        if not suggestions:
            suggestions = [
                "优先跟进金额高、阶段停滞或近期承诺过下一步的商机。",
                "检查超过 7 天未联系的客户，并补齐下一步动作。",
                "梳理新线索来源和转化路径，给每条线索设置下一步跟进时间。",
            ]

        report = {
            "opportunities": {
                "count": len(opportunities),
                "amount_total": amount_total if amount_seen else None,
                "status_distribution": status_distribution,
                "key_items": key_opportunities[:3],
                "priority_followup_count": len(suggestions),
            },
            "customers": {
                "count": len(customers),
                "overdue_followup_count": len(overdue_customers),
                "overdue_examples": overdue_customers[:3],
                "follow_record_count": len(recent_follow_records),
            },
            "leads": {
                "count": len(leads),
                "new_this_week_estimate": min(new_leads_this_week, len(leads)),
            },
            "tasks": {
                "eboss_task_count": len(tasks),
                "local_pending_count": len(self.list_tasks(status="pending", limit=1000)),
            },
            "daily_reports": {
                "synced_days": len(daily_reports),
            },
            "top_3_recommendations": suggestions,
        }
        lines = [
            "═══════════════════════════════════",
            "首次分析报告",
            "═══════════════════════════════════",
            f"商机：{report['opportunities']['count']} 个",
        ]
        if report["opportunities"]["amount_total"] is not None:
            lines.append(f"商机金额合计：{report['opportunities']['amount_total']}")
        if status_distribution:
            status_text = "、".join(f"{key}:{value}" for key, value in status_distribution.items())
            lines.append(f"状态分布：{status_text}")
        if key_opportunities:
            lines.append("重点关注：" + "、".join(key_opportunities[:3]))
        lines.append(f"建议优先跟进：{len(suggestions)} 个")
        lines.append(f"客户：{report['customers']['count']} 个")
        lines.append(f"待跟进客户：{report['customers']['overdue_followup_count']} 个")
        for customer in overdue_customers[:3]:
            detail = customer["name"]
            if customer.get("no_follow_days") not in (None, ""):
                detail += f"（{customer['no_follow_days']} 天未跟进）"
            elif customer.get("last_follow"):
                detail += f"（最近跟进：{customer['last_follow']}）"
            lines.append(f"客户待跟进：{detail}")
        lines.append(f"线索：{report['leads']['count']} 个")
        lines.append(f"本周新增线索估算：{report['leads']['new_this_week_estimate']} 个")
        lines.append(f"EBOSS 任务：{report['tasks']['eboss_task_count']} 个")
        lines.append(f"日报：已同步最近 {report['daily_reports']['synced_days']} 天")
        lines.append("建议：")
        for index, suggestion in enumerate(suggestions, start=1):
            lines.append(f"{index}. {suggestion}")
        lines.append("═══════════════════════════════════")
        report["formatted_report"] = "\n".join(lines)
        return report

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

    def openclaw_bridge_doctor(self) -> dict[str, Any]:
        doctor = getattr(self.openclaw, "doctor", None)
        if callable(doctor):
            return doctor()
        return {
            "ok": True,
            "mode": "custom_adapter",
            "can_wake_openclaw": True,
            "can_confirm_message_delivery": False,
            "warnings": ["custom_openclaw_adapter_has_no_doctor_method"],
            "errors": [],
        }

    def openclaw_bridge_test(self, *, now: datetime | None = None) -> dict[str, Any]:
        doctor = self.openclaw_bridge_doctor()
        if not doctor.get("ok"):
            return {
                "ok": False,
                "bridge": doctor,
                "error": "openclaw_bridge_not_ready",
            }
        now = now or now_in_zone(self.config.timezone)
        prompt = (
            "SalesBrain 正在做 Openclaw 唤醒和消息发送测试。"
            "请立刻向当前用户发送一条可见消息：SalesBrain 唤醒测试成功。"
            "然后只返回 JSON，必须包含 {\"ok\": true, \"message_sent\": true}。"
        )
        context = {
            "run_mode": "salesbrain_bridge_test",
            "requires_visible_user_message": True,
            "expected_user_message": "SalesBrain 唤醒测试成功",
            "expected_json_fields": ["ok", "message_sent"],
        }
        result = self._wake_openclaw(
            kind="salesbrain_bridge_test",
            prompt=prompt,
            context=context,
            now=now,
        )
        raw = result.get("openclaw_result") if isinstance(result, dict) else {}
        message_sent = bool(raw.get("message_sent")) if isinstance(raw, dict) else False
        if result.get("ok") and message_sent:
            result["bridge"] = doctor
            result["message_sent"] = True
            return result
        wake_run = result.get("wake_run") if isinstance(result, dict) else None
        if isinstance(wake_run, dict) and wake_run.get("id"):
            updated = self.store.update_wake_run(
                str(wake_run["id"]),
                {
                    "finished_at": self.now_iso(),
                    "status": "failed",
                    "error": "bridge_test_message_not_confirmed",
                    "payload_json": {
                        "openclaw_result": raw,
                        "bridge": doctor,
                        "message_sent": message_sent,
                    },
                },
            )
            result["wake_run"] = _decode_json_columns(updated or wake_run)
        result["ok"] = False
        result["bridge"] = doctor
        result["message_sent"] = False
        result["error"] = result.get("error") or "bridge_test_message_not_confirmed"
        return result

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

    def sync_eboss(self, *, now: datetime | None = None, force_full: bool = False) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        is_first_full = bool(force_full or (self.config.eboss_first_full_sync and self.store.get_state(FIRST_EBOSS_FULL_SYNC_DONE_KEY) != "1"))
        sync_mode = "first_full" if is_first_full else "active"
        started_at = self.now_iso(now)
        run_id = self.store.insert_sync_run(
            run_type="eboss_sync",
            started_at=started_at,
            status="running",
            payload_json={"sales_name": self.config.sales_name, "sync_mode": sync_mode},
        )
        counts: dict[str, int] = {}
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        try:
            client = self._eboss()
        except Exception as exc:
            finished_at = self.now_iso(now)
            error = {"api_id": "eboss_client", "error": str(exc)}
            self.store.update_sync_run(
                run_id,
                status="failed",
                finished_at=finished_at,
                error=str(exc),
                counts_json=counts,
                payload_json={"errors": [error], "sync_mode": sync_mode},
            )
            return {
                "ok": False,
                "run_id": run_id,
                "status": "failed",
                "sync_mode": sync_mode,
                "error": str(exc),
                "counts": counts,
                "errors": [error],
                "warnings": warnings,
            }

        def insert(api_id: str, object_type: str, records: list[dict[str, Any]]) -> None:
            count = self.store.insert_raw_records(
                sync_run_id=run_id,
                api_id=api_id,
                object_type=object_type,
                records=records,
                fetched_at=self.now_iso(now),
            )
            counts[object_type] = counts.get(object_type, 0) + count

        def upsert_object(api_id: str, object_type: str, object_id: str, object_name: str, payload: dict[str, Any]) -> None:
            inserted = self.store.upsert_raw_record_by_object(
                sync_run_id=run_id,
                api_id=api_id,
                object_type=object_type,
                object_id=object_id,
                object_name=object_name,
                payload=payload,
                fetched_at=self.now_iso(now),
            )
            if inserted:
                counts[object_type] = counts.get(object_type, 0) + 1

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

        def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            seen: set[str] = set()
            unique: list[dict[str, Any]] = []
            for record in records:
                object_id, object_name = EbossClient.extract_object_summary(record)
                key = object_id or object_name or json.dumps(record, ensure_ascii=False, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(record)
            return unique

        sync_year = now.year
        year_range = _year_range(now)

        def filter_current_year(object_type: str, records: list[dict[str, Any]], *, keep_without_date: bool = True) -> list[dict[str, Any]]:
            if not self.config.eboss_current_year_only:
                return records
            filtered = []
            for record in records:
                if _record_matches_year(record, sync_year, keep_without_date=keep_without_date):
                    filtered.append(_prune_record_to_year(record, sync_year))
            dropped = len(records) - len(filtered)
            if dropped:
                warnings.append(
                    {
                        "api_id": "year_filter",
                        "object_type": object_type,
                        "warning": f"filtered_out_non_current_year_records:{dropped}",
                        "year": str(sync_year),
                    }
                )
            return filtered

        def with_year_create_time(params: dict[str, Any]) -> dict[str, Any]:
            if not self.config.eboss_current_year_only:
                return dict(params)
            return {**params, "createTime": year_range["create_time"]}

        def fetch_paginated_variants(
            api_id: str,
            object_type: str,
            variants: list[dict[str, Any]],
            *,
            max_pages: int,
            fallback_variants: list[dict[str, Any]] | None = None,
        ) -> list[dict[str, Any]]:
            records: list[dict[str, Any]] = []
            active_variants = variants
            try:
                for params in active_variants:
                    records.extend(client.call_paginated(api_id, params, max_pages=max_pages))
            except Exception as exc:
                if not fallback_variants:
                    errors.append({"api_id": api_id, "object_type": object_type, "error": str(exc)})
                    return dedupe_records(records)
                warnings.append({"api_id": api_id, "object_type": object_type, "warning": f"primary_params_failed_fallback_used: {exc}"})
                for params in fallback_variants:
                    try:
                        records.extend(client.call_paginated(api_id, params, max_pages=max_pages))
                    except Exception as fallback_exc:
                        errors.append({"api_id": api_id, "object_type": object_type, "error": str(fallback_exc)})
            return dedupe_records(records)

        def records_or_raw(api_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
            response = client.call(api_id, params)
            records = response.records
            return records if records else [dict(response.raw)]

        def insert_related(
            *,
            api_id: str,
            object_type: str,
            params: dict[str, Any],
            parent_id: str,
            parent_name: str,
        ) -> None:
            try:
                records = records_or_raw(api_id, params)
                records = filter_current_year(object_type, records)
                enriched = []
                for record in records:
                    payload = dict(record)
                    payload["salesbrain_parent_id"] = parent_id
                    payload["salesbrain_parent_name"] = parent_name
                    enriched.append(payload)
                insert(api_id, object_type, enriched)
            except Exception as exc:
                errors.append({"api_id": api_id, "object_type": object_type, "object_id": parent_id, "error": str(exc)})

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
        page_size = str(self.config.eboss_full_sync_page_size if is_first_full else self.config.eboss_page_size)
        max_pages = self.config.eboss_full_sync_max_pages if is_first_full else self.config.eboss_daily_active_sync_max_pages
        project_statuses = "1,2,3,4,10,11,12,13,14" if is_first_full else "1,10,11,12"
        project_sheets = ("1", "2", "3", "4", "5", "6") if is_first_full else ("3",)
        opportunity_ranges = ("1", "2", "3", "4", "5", "6") if is_first_full else ("3",)
        opportunity_states = "1,2,3,4,5,6,7,8,9" if is_first_full else "1,2"

        project_variants = [
            {
                "current": "1",
                "size": page_size,
                "projectStatusList": project_statuses,
                "sheet": sheet,
                **({"projectManagerIds": user_id} if sheet == "3" else {}),
            }
            for sheet in project_sheets
        ]
        project_records = fetch_paginated_variants(
            "get-project-list",
            "project",
            [with_year_create_time(params) for params in project_variants],
            max_pages=max_pages,
            fallback_variants=project_variants if self.config.eboss_current_year_only else None,
        )
        project_records = filter_current_year("project", project_records)
        insert("get-project-list", "project", project_records)

        opportunity_variants = [
            {
                "current": "1",
                "size": page_size,
                "optStateList": opportunity_states,
                "optTypeList": "1,2,3",
                "opportunity": scope,
                **({"chargerOpIdList": user_id} if scope == "3" else {}),
            }
            for scope in opportunity_ranges
        ]
        opportunity_fallback = [
            {
                "current": "1",
                "size": page_size,
                "optStateList": "1,2",
                "optTypeList": "1,2,3",
                "opportunity": scope,
                **({"chargerOpIdList": user_id} if scope == "3" else {}),
            }
            for scope in opportunity_ranges
        ]
        opportunity_primary = [with_year_create_time(params) for params in opportunity_variants]
        opportunity_fallback_with_year = [with_year_create_time(params) for params in opportunity_fallback]
        opportunity_records = fetch_paginated_variants(
            "get-opportunity-list",
            "opportunity",
            opportunity_primary,
            max_pages=max_pages,
            fallback_variants=(
                opportunity_fallback_with_year + opportunity_variants + opportunity_fallback
                if is_first_full
                else opportunity_variants
                if self.config.eboss_current_year_only
                else None
            ),
        )
        opportunity_records = filter_current_year("opportunity", opportunity_records)
        insert("get-opportunity-list", "opportunity", opportunity_records)

        successful = int(bool(project_records)) + int(bool(opportunity_records))
        for api_id, object_type, params in [
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
            ),
        ]:
            try:
                request_params = with_year_create_time(params) if api_id in {"get-customer-list", "get-lead-list"} else params
                try:
                    records = client.call_paginated(api_id, request_params, max_pages=max_pages)
                except Exception:
                    if request_params == params:
                        raise
                    warnings.append(
                        {
                            "api_id": api_id,
                            "object_type": object_type,
                            "warning": "year_params_failed_fallback_used",
                            "year": str(sync_year),
                        }
                    )
                    records = client.call_paginated(api_id, params, max_pages=max_pages)
                records = filter_current_year(object_type, records)
                insert(api_id, object_type, dedupe_records(records))
                successful += 1
            except Exception as exc:
                errors.append({"api_id": api_id, "object_type": object_type, "error": str(exc)})

        for project in project_records:
            project_id, project_name = EbossClient.extract_object_summary(project)
            if not project_id:
                continue
            project_name = project_name or project_id
            for api_id, object_type, params in [
                ("get-project-detail", "project_detail", {"id": project_id}),
                ("get-stage-list", "project_stage", {"projectId": project_id, "taskObj": "21", "ifOrderMs": "true"}),
                ("get-task-by-project-opp", "project_task", {"taskObj": "21", "taskObjId": project_id}),
                ("get-follow-record", "project_follow_record", {"followObj": "21", "followObjId": project_id, "current": "1", "size": page_size, "dateTag": "0"}),
                ("get-project-budget", "project_budget", {"projectId": project_id}),
                ("get-project-forecast", "project_forecast", {"projectId": project_id}),
                ("get-project-actual", "project_actual", {"projectId": project_id}),
                ("get-requirement-list", "project_requirement", {"projectId": project_id, "projectIds": project_id, "current": "1", "size": page_size, "queryType": "0", "statuss": "1,2"}),
                ("get-project-doc-list", "project_document", {"attachObj": "21", "attachObjId": project_id}),
            ]:
                insert_related(api_id=api_id, object_type=object_type, params=params, parent_id=project_id, parent_name=project_name)

        for opportunity in opportunity_records:
            opportunity_id, opportunity_name = EbossClient.extract_object_summary(opportunity)
            if not opportunity_id:
                continue
            opportunity_name = opportunity_name or opportunity_id
            for api_id, object_type, params in [
                ("get-opportunity-detail", "opportunity_detail", {"id": opportunity_id}),
                ("get-stage-list", "opportunity_stage", {"projectId": opportunity_id, "taskObj": "4", "ifOrderMs": "true"}),
                ("get-task-by-project-opp", "opportunity_task", {"taskObj": "4", "taskObjId": opportunity_id}),
                ("get-follow-record", "opportunity_follow_record", {"followObj": "4", "followObjId": opportunity_id, "current": "1", "size": page_size, "dateTag": "0"}),
                ("get-opportunity-budget", "opportunity_budget", {"optId": opportunity_id}),
                ("get-opportunity-forecast", "opportunity_forecast", {"optId": opportunity_id}),
                ("get-opportunity-actual", "opportunity_actual", {"optId": opportunity_id}),
            ]:
                insert_related(api_id=api_id, object_type=object_type, params=params, parent_id=opportunity_id, parent_name=opportunity_name)

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
            attempted_backfill_days = 0
            backfill_errors = 0
            backfill_success = 0
            for offset in range(29, -1, -1):
                query_date = (now - timedelta(days=offset)).date().isoformat()
                if self.config.eboss_current_year_only and not query_date.startswith(f"{sync_year}-"):
                    continue
                attempted_backfill_days += 1
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
            if attempted_backfill_days and backfill_errors == 0 and backfill_success == attempted_backfill_days:
                self.store.set_state(GITHUB_BACKFILL_DONE_KEY, "1", now_iso=self.now_iso(now))

        if is_first_full and not errors:
            self.store.set_state(FIRST_EBOSS_FULL_SYNC_DONE_KEY, "1", now_iso=self.now_iso(now))

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
            payload_json={
                "errors": errors,
                "warnings": warnings,
                "user_id": user_id,
                "real_name": real_name,
                "sync_mode": sync_mode,
                "sync_year": sync_year if self.config.eboss_current_year_only else None,
                "year_range": year_range if self.config.eboss_current_year_only else None,
                "project_count": len(project_records),
                "opportunity_count": len(opportunity_records),
            },
        )
        return {
            "ok": status != "failed",
            "run_id": run_id,
            "status": status,
            "sync_mode": sync_mode,
            "sync_year": sync_year if self.config.eboss_current_year_only else None,
            "counts": counts,
            "errors": errors,
            "warnings": warnings,
        }

    def github_update_check(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        now_iso = self.now_iso(now)
        try:
            remote = self._fetch_github_commit()
        except Exception as exc:
            wake = self.record_wake_result(
                {
                    "wake_type": "salesbrain_update_check",
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
                    "wake_type": "salesbrain_update_check",
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

        base_context = {
            "local_version": self._local_version_context(installed_revision=installed),
            "company_skillhub": {
                "install_command": self.config.company_skillhub_install_command,
                "priority": 1,
                "check_required": True,
            },
            "github": {
                "repo": self.config.github_repo,
                "branch": self.config.github_branch,
                "priority": 2,
                "version": remote["sha"],
                "commit": remote,
            },
            "repo": self.config.github_repo,
            "branch": self.config.github_branch,
            "installed_revision": installed,
            "remote_revision": remote["sha"],
            "remote_commit": remote,
            "github_update_available": comparison["update_available"],
        }

        if not comparison["update_available"]:
            prompt = build_github_update_prompt(base_context)
            result = self._wake_openclaw(kind="salesbrain_update_check", prompt=prompt, context=base_context, now=now)
            return {
                "ok": result["ok"],
                "up_to_date": True,
                "wake_run": result["wake_run"],
                "openclaw_result": result["openclaw_result"],
                "applied": result["applied"],
                "github": remote,
            }

        context = base_context
        prompt = build_github_update_prompt(context)
        result = self._wake_openclaw(kind="salesbrain_update_check", prompt=prompt, context=context, now=now)
        self.store.set_state(GITHUB_LAST_PROMPTED_REVISION_KEY, remote["sha"], now_iso=now_iso)
        return {
            "ok": result["ok"],
            "update_available": True,
            "wake_run": result["wake_run"],
            "openclaw_result": result["openclaw_result"],
            "applied": result["applied"],
            "github": remote,
        }

    def _local_version_context(self, *, installed_revision: str | None = None) -> dict[str, Any]:
        try:
            from . import __version__
        except Exception:
            __version__ = "unknown"
        bundle_meta_path = self.config.home / ".salesbrain-bundle.json"
        bundle_meta = None
        if bundle_meta_path.exists():
            bundle_meta = _loads(bundle_meta_path.read_text(encoding="utf-8"), {})
        return {
            "package_version": __version__,
            "installed_revision": installed_revision or self.store.get_state(GITHUB_INSTALLED_REVISION_KEY),
            "bundle_meta": bundle_meta,
        }

    def salesbrain_update_check(self, *, now: datetime | None = None) -> dict[str, Any]:
        return self.github_update_check(now=now)

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
        fact_package = build_business_fact_package(self.store, now=now_in_zone(self.config.timezone))
        return {
            "profile": self.store.get_profile(),
            "latest_sync": latest_sync,
            "semantic_contract": fact_package["semantic_contract"],
            "business_facts": fact_package["facts"][:limit],
            "work_state": fact_package["work_state"],
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
            "workflow_inbox_items": self.list_workflow_inbox_items(review_status="pending", limit=20),
        }

    def search_eboss(self, keyword: str, limit: int = 20) -> dict[str, Any]:
        return {
            "keyword": keyword,
            "records": _compact_records(self.store.search_raw_records(keyword, limit), limit),
        }

    def repair_eboss_object_summaries(self, *, object_type: str | None = None, limit: int | None = None) -> dict[str, Any]:
        return self.store.repair_raw_record_summaries(object_type=object_type, limit=limit)

    def _validate_task_business_fact(self, task: dict[str, Any], source_context: dict[str, Any] | None) -> None:
        source_type = str(task.get("source_type") or "").strip()
        source_ref = str(task.get("source_ref") or "").strip()
        if not source_type and not source_ref:
            return
        fact_index = fact_index_from_context(source_context)
        if source_type in BUSINESS_OBJECT_TYPES:
            if not source_ref:
                raise ValueError(f"task.source_ref is required when source_type is {source_type}")
            fact = fact_index.get(f"{source_type}:{source_ref}")
            if not fact:
                raise ValueError(f"task.source_ref not found in business_facts: {source_type}:{source_ref}")
        else:
            fact = fact_index.get(f"{source_type}:{source_ref}") if source_ref else None
        if not fact:
            return
        amount_value = task.get("amount_yuan_used")
        payload_json = task.get("payload_json") if isinstance(task.get("payload_json"), dict) else {}
        if amount_value in (None, "") and isinstance(payload_json, dict):
            amount_value = payload_json.get("amount_yuan_used")
        if amount_value in (None, ""):
            return
        expected = fact.get("amount_yuan")
        expected_amount = parse_amount_yuan(expected)
        used_amount = parse_amount_yuan(amount_value)
        if expected_amount is None or used_amount is None:
            return
        tolerance = max(1, int(abs(expected_amount) * 0.01))
        if abs(expected_amount - used_amount) > tolerance:
            raise ValueError(
                "task.amount_yuan_used mismatch: "
                f"{source_type}:{source_ref} expected {expected_amount}, got {used_amount}"
            )

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

    def list_workflow_inbox_items(self, review_status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
        return [
            _decode_json_columns(item)
            for item in self.store.list_workflow_inbox_items(review_status=review_status, limit=limit)
        ]

    def list_wake_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        return [_decode_json_columns(item) for item in self.store.list_wake_runs(limit=limit)]

    def list_scheduler_jobs(self) -> list[dict[str, Any]]:
        return [_decode_json_columns(item) for item in self.store.list_scheduler_jobs()]

    def _planned_wake_jobs(self, *, wake_kind: str | None = None) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for job in self.list_scheduler_jobs():
            if str(job.get("handler_name") or "") != "planned_wake":
                continue
            payload = job.get("payload_json")
            if not isinstance(payload, dict):
                payload = _loads(payload, {})
            if wake_kind and str(payload.get("wake_kind") or "") != wake_kind:
                continue
            jobs.append(job)
        return jobs

    def _disable_existing_planned_wakes(self, wake_kind: str, *, now: datetime | None = None) -> None:
        now_iso = self.now_iso(now)
        for job in self._planned_wake_jobs(wake_kind=wake_kind):
            if int(job.get("enabled", 1)) != 1:
                continue
            self.store.set_scheduler_job_enabled(
                str(job["job_name"]),
                False,
                payload_json={
                    "disabled_by": "adaptive_next_wake_replaced",
                    "disabled_at": now_iso,
                    "wake_kind": wake_kind,
                },
            )

    def _adaptive_followup_due_at(self, now: datetime) -> datetime:
        candidate = (now + timedelta(hours=2)).replace(second=0, microsecond=0)
        business_cutoff = now.replace(hour=19, minute=30, second=0, microsecond=0)
        if now.weekday() < 5 and candidate <= business_cutoff:
            return candidate

        next_day = now + timedelta(days=1)
        candidate = next_day.replace(hour=9, minute=30, second=0, microsecond=0)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    def _has_active_followup_signal(self, context: dict[str, Any] | None) -> bool:
        if not isinstance(context, dict):
            return False
        for key in ("due_tasks", "pending_tasks", "open_review_suggestions"):
            value = context.get(key)
            if isinstance(value, list) and value:
                return True
        latest_sync = context.get("latest_sync")
        return bool(latest_sync)

    def _fallback_next_wake_plan(
        self,
        *,
        source_kind: str,
        now: datetime,
        source_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if source_kind == "work_followup" or (
            source_kind in {"initial_analysis", "morning_analysis", "daily_report_review"}
            and self._has_active_followup_signal(source_context)
        ):
            due_at = self._adaptive_followup_due_at(now)
            return {
                "kind": "work_followup",
                "due_at": due_at.isoformat(timespec="seconds"),
                "reason": "SalesBrain fallback adaptive follow-up because Openclaw did not return an explicit next_wake_plans item.",
                "priority": "normal",
                "replace_existing": True,
                "payload_json": {
                    "fallback": True,
                    "source_kind": source_kind,
                },
            }
        return None

    def schedule_next_wake(
        self,
        plan: dict[str, Any],
        *,
        source_kind: str,
        now: datetime | None = None,
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        wake_kind = str(plan.get("kind") or plan.get("wake_kind") or "").strip()
        if wake_kind not in PLANNED_WAKE_KINDS:
            raise ValueError(f"unsupported_next_wake_kind: {wake_kind or '<missing>'}")
        due_raw = str(plan.get("due_at") or plan.get("wake_at") or "").strip()
        if not due_raw:
            raise ValueError("next_wake_plan.due_at is required")
        due_at = parse_iso_datetime(due_raw)
        min_due_at = now + timedelta(minutes=PLANNED_WAKE_MIN_DELAY_MINUTES)
        if due_at < min_due_at:
            due_at = min_due_at
        due_at_iso = due_at.isoformat(timespec="seconds")
        replace_existing = bool(plan.get("replace_existing", True))
        if replace_existing:
            self._disable_existing_planned_wakes(wake_kind, now=now)
        payload_extra = plan.get("payload_json") if isinstance(plan.get("payload_json"), dict) else {}
        payload = {
            "wake_kind": wake_kind,
            "source_kind": source_kind,
            "source_run_id": source_run_id,
            "reason": str(plan.get("reason") or ""),
            "priority": str(plan.get("priority") or "normal"),
            "replace_existing": replace_existing,
            "retry_minutes": int(plan.get("retry_minutes") or PLANNED_WAKE_RETRY_MINUTES),
            "scheduled_at": self.now_iso(now),
            "payload_json": payload_extra,
        }
        job_name = f"{PLANNED_WAKE_JOB_PREFIX}{wake_kind}__{uuid.uuid4().hex[:12]}"
        scheduled = self.store.upsert_scheduler_job(
            job_name=job_name,
            handler_name="planned_wake",
            schedule_kind="one_shot_at",
            schedule_value=due_at_iso,
            next_run_at=due_at_iso,
            enabled=True,
            payload_json=payload,
        )
        return _decode_json_columns(scheduled)

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
        members = self.team().peers()
        return {
            "ok": True,
            "members": members,
            "formatted_members": [self.team().format_member(member) for member in members],
        }

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
        source_run_id: str | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_decision(decision)
        created_tasks: list[dict[str, Any]] = []
        updated_tasks: list[dict[str, Any] | None] = []
        review_suggestions: list[dict[str, Any]] = []
        workflow_items: list[dict[str, Any]] = []
        workflow_inbox_decisions: list[dict[str, Any]] = []
        scheduled_wakes: list[dict[str, Any]] = []
        removed_cron_jobs: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        for task in normalized["tasks_to_create"]:
            if not isinstance(task, dict):
                continue
            payload = dict(task)
            payload.setdefault("source_type", source_kind)
            try:
                self._validate_task_business_fact(payload, source_context)
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

        for inbox_decision in normalized["workflow_inbox_decisions"]:
            if not isinstance(inbox_decision, dict) or not inbox_decision.get("id"):
                continue
            inbox_id = str(inbox_decision["id"])
            action = str(inbox_decision.get("action") or "ignore").strip().lower()
            try:
                inbox_item = self.store.get_workflow_inbox_item(inbox_id)
                if not inbox_item:
                    workflow_inbox_decisions.append({"id": inbox_id, "action": action, "applied": False, "reason": "not_found"})
                    continue
                decoded = _decode_json_columns(inbox_item)
                if action in {"accept", "merge"}:
                    merged = inbox_decision.get("merged_item") if isinstance(inbox_decision.get("merged_item"), dict) else {}
                    workflow_payload = {
                        "title": merged.get("title") or decoded["title"],
                        "pattern_type": merged.get("pattern_type") or decoded["pattern_type"],
                        "summary": merged.get("summary") or decoded["summary"],
                        "example_json": merged.get("example_json") or decoded.get("example_json") or {},
                        "source_task_ids_json": merged.get("source_task_ids_json") or decoded.get("source_task_ids_json") or [],
                        "sync_status": merged.get("sync_status") or "ready",
                    }
                    workflow = self.record_workflow(workflow_payload, now=now)
                    status = "merged" if action == "merge" else "accepted"
                    self.store.update_workflow_inbox_item(inbox_id, {"review_status": status}, now_iso=self.now_iso(now))
                    workflow_inbox_decisions.append({"id": inbox_id, "action": action, "applied": True, "workflow_item": workflow})
                elif action in {"duplicate", "ignore", "ignored"}:
                    status = "duplicate" if action == "duplicate" else "ignored"
                    self.store.update_workflow_inbox_item(inbox_id, {"review_status": status}, now_iso=self.now_iso(now))
                    workflow_inbox_decisions.append({"id": inbox_id, "action": action, "applied": True})
                else:
                    workflow_inbox_decisions.append({"id": inbox_id, "action": action, "applied": False, "reason": "unsupported_action"})
            except Exception as exc:
                errors.append({"action": "workflow_inbox_decision", "error": str(exc), "id": inbox_id})

        for wake_plan in _coerce_next_wake_plans(normalized):
            try:
                scheduled_wakes.append(
                    self.schedule_next_wake(
                        wake_plan,
                        source_kind=source_kind,
                        source_run_id=source_run_id,
                        now=now,
                    )
                )
            except Exception as exc:
                errors.append({"action": "schedule_next_wake", "error": str(exc)})

        if not scheduled_wakes:
            fallback_plan = self._fallback_next_wake_plan(
                source_kind=source_kind,
                now=now or now_in_zone(self.config.timezone),
                source_context=source_context,
            )
            if fallback_plan:
                try:
                    scheduled_wakes.append(
                        self.schedule_next_wake(
                            fallback_plan,
                            source_kind=source_kind,
                            source_run_id=source_run_id,
                            now=now,
                        )
                    )
                except Exception as exc:
                    errors.append({"action": "schedule_next_wake_fallback", "error": str(exc)})

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
            "user_message": normalized.get("user_message", ""),
            "analysis_summary": normalized.get("analysis_summary", ""),
            "created_tasks": created_tasks,
            "updated_tasks": updated_tasks,
            "review_suggestions": review_suggestions,
            "workflow_items": workflow_items,
            "workflow_inbox_decisions": workflow_inbox_decisions,
            "scheduled_wakes": scheduled_wakes,
            "removed_cron_jobs": removed_cron_jobs,
            "errors": errors,
        }

    def _validate_mentor_delivery(
        self,
        *,
        kind: str,
        raw: dict[str, Any],
        applied: dict[str, Any],
    ) -> list[dict[str, str]]:
        if kind not in MENTOR_MESSAGE_REQUIRED_WAKE_KINDS:
            return []
        normalized = normalize_decision(raw)
        user_message = str(normalized.get("user_message") or "").strip()
        message_sent = raw.get("message_sent")
        if isinstance(message_sent, str):
            message_sent = message_sent.strip().lower() in {"1", "true", "yes", "sent", "ok"}
        errors: list[dict[str, str]] = []
        if not user_message:
            errors.append(
                {
                    "action": "mentor_delivery",
                    "error": "user_message_required",
                    "kind": kind,
                }
            )
        if message_sent is not True:
            errors.append(
                {
                    "action": "mentor_delivery",
                    "error": "message_sent_required",
                    "kind": kind,
                }
            )
        if kind in {"initial_analysis", "morning_analysis", "work_followup", "daily_report_review"} and not applied.get("scheduled_wakes"):
            errors.append(
                {
                    "action": "mentor_delivery",
                    "error": "next_wake_plan_required",
                    "kind": kind,
                }
            )
        return errors

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
            applied = self.apply_openclaw_decision(
                result.raw,
                source_kind=kind,
                now=now,
                source_run_id=str(wake["id"]),
                source_context=context,
            )
            delivery_errors = self._validate_mentor_delivery(kind=kind, raw=result.raw, applied=applied)
            if delivery_errors:
                applied["errors"].extend(delivery_errors)
            overall_ok = bool(result.ok and not applied["errors"])
            finished = self.store.update_wake_run(
                wake["id"],
                {
                    "finished_at": self.now_iso(),
                    "status": "success" if overall_ok else "failed",
                    "result_summary": applied.get("summary") or result.summary,
                    "error": json.dumps(applied["errors"], ensure_ascii=False) if applied["errors"] else None,
                    "payload_json": {
                        "openclaw_result": result.raw,
                        "applied": applied,
                    },
                },
            )
            return {
                "ok": overall_ok,
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

    def planned_wake(
        self,
        *,
        now: datetime | None = None,
        scheduler_job: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = scheduler_job.get("payload_json") if isinstance(scheduler_job, dict) else {}
        if not isinstance(payload, dict):
            payload = _loads(payload, {})
        wake_kind = str(payload.get("wake_kind") or "").strip()
        if wake_kind == "initial_analysis":
            return self.initial_analysis(now=now)
        if wake_kind == "morning_analysis":
            return self.morning_analysis(now=now)
        if wake_kind == "work_followup":
            return self.work_followup(now=now)
        if wake_kind == "daily_report_review":
            return self.daily_report_review(now=now)
        if wake_kind == "weekly_summary":
            return self.weekly_summary(now=now)
        if wake_kind == "workflow_reflection":
            return self.workflow_reflection(now=now)
        if wake_kind == "workflow_inbox_review":
            return self.workflow_inbox_review(now=now)
        return {
            "ok": False,
            "error": f"unsupported_planned_wake_kind: {wake_kind or '<missing>'}",
            "scheduler_job": _decode_json_columns(scheduler_job or {}),
        }

    def morning_analysis(self, *, now: datetime | None = None) -> dict[str, Any]:
        context = self._analysis_context(now=now, daily_report_limit=20)
        context["run_mode"] = "morning_analysis"
        prompt = build_morning_analysis_prompt(context)
        return self._wake_openclaw(kind="morning_analysis", prompt=prompt, context=context, now=now)

    def work_followup(self, *, now: datetime | None = None) -> dict[str, Any]:
        context = self._analysis_context(now=now, daily_report_limit=20)
        context["run_mode"] = "work_followup"
        context["followup_mode"] = "adaptive_first"
        context["fallback_anchor_times"] = list(self.config.work_followup_times)
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

    def workflow_inbox_review(self, *, now: datetime | None = None, force: bool = False) -> dict[str, Any]:
        now = now or now_in_zone(self.config.timezone)
        inbox_items = self.list_workflow_inbox_items(review_status="pending", limit=50)
        if not inbox_items:
            wake = self.record_wake_result(
                {
                    "wake_type": "workflow_inbox_review",
                    "status": "skipped",
                    "input_summary": "no pending team workflow inbox items",
                    "result_summary": "No pending team workflow inbox items.",
                    "payload_json": {},
                },
                now=now,
            )
            return {"ok": True, "skipped": True, "wake_run": wake}
        signature = "|".join(sorted(str(item.get("id") or "") for item in inbox_items))
        if not force and signature and self.store.get_state(WORKFLOW_INBOX_LAST_PROMPT_SIGNATURE_KEY) == signature:
            wake = self.record_wake_result(
                {
                    "wake_type": "workflow_inbox_review",
                    "status": "skipped",
                    "input_summary": "pending team workflow inbox items already prompted",
                    "result_summary": "Pending team workflow inbox items were already sent to Openclaw for user confirmation.",
                    "payload_json": {
                        "pending_inbox_ids": [item.get("id") for item in inbox_items],
                        "signature": signature,
                    },
                },
                now=now,
            )
            return {"ok": True, "skipped": True, "already_prompted": True, "wake_run": wake}
        context = self._analysis_context(now=now, daily_report_limit=10)
        context["run_mode"] = "workflow_inbox_review"
        context["workflow_inbox_items"] = inbox_items
        context["requires_user_confirmation"] = True
        context["confirmation_policy"] = {
            "proactive_message_required": True,
            "do_not_accept_without_user_confirmation": True,
            "if_not_yet_confirmed": "ask the user in summary and return empty workflow_inbox_decisions",
            "allowed_after_user_confirmation": ["accept", "merge", "ignore", "duplicate"],
        }
        prompt = build_workflow_inbox_review_prompt(context)
        result = self._wake_openclaw(kind="workflow_inbox_review", prompt=prompt, context=context, now=now)
        if signature:
            self.store.set_state(WORKFLOW_INBOX_LAST_PROMPT_SIGNATURE_KEY, signature, now_iso=self.now_iso(now))
        return result

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
