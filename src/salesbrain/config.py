from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib  # type: ignore


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_time_list(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        return default
    parsed = tuple(item for item in items if item)
    return parsed or default


@dataclass(slots=True)
class SalesBrainConfig:
    home: Path
    config_path: Path
    db_path: Path
    logs_dir: Path
    sales_name: str = ""
    timezone: str = "Asia/Shanghai"
    eboss_base_url: str = "http://10.21.14.4:30010/api"
    eboss_api_key: str = ""
    eboss_api_key_file: Path = Path.home() / ".openclaw" / "workspace" / "secrets" / "eboss-api-key.txt"
    eboss_timeout_seconds: int = 240
    eboss_page_size: int = 50
    eboss_max_pages: int = 20
    openclaw_mode: str = "command"
    openclaw_wake_command: str = ""
    openclaw_cron_list_command: str = ""
    openclaw_cron_remove_command: str = ""
    openclaw_cron_jobs_path: Path = Path.home() / ".openclaw" / "cron" / "jobs.json"
    wake_timeout_seconds: int = 120
    daily_sync_time: str = "02:00"
    morning_analysis_time: str = "06:00"
    work_followup_times: tuple[str, ...] = ("08:30", "13:30", "19:30")
    daily_report_review_time: str = "22:00"
    due_task_scan_minutes: int = 5
    workflow_reflection_time: str = "23:30"
    weekly_summary_time: str = "fri 17:30"
    github_update_check_time: str = "09:00"
    github_repo: str = "FrankeyQu/SalesBrain"
    github_branch: str = "main"
    github_timeout_seconds: int = 30
    scheduler_tick_seconds: int = 30

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def with_resolved_paths(self) -> "SalesBrainConfig":
        return SalesBrainConfig(
            home=_expand(self.home),
            config_path=_expand(self.config_path),
            db_path=_expand(self.db_path),
            logs_dir=_expand(self.logs_dir),
            sales_name=self.sales_name,
            timezone=self.timezone,
            eboss_base_url=self.eboss_base_url.rstrip("/"),
            eboss_api_key=self.eboss_api_key,
            eboss_api_key_file=_expand(self.eboss_api_key_file),
            eboss_timeout_seconds=self.eboss_timeout_seconds,
            eboss_page_size=self.eboss_page_size,
            eboss_max_pages=self.eboss_max_pages,
            openclaw_mode=self.openclaw_mode,
            openclaw_wake_command=self.openclaw_wake_command,
            openclaw_cron_list_command=self.openclaw_cron_list_command,
            openclaw_cron_remove_command=self.openclaw_cron_remove_command,
            openclaw_cron_jobs_path=_expand(self.openclaw_cron_jobs_path),
            wake_timeout_seconds=self.wake_timeout_seconds,
            daily_sync_time=self.daily_sync_time,
            morning_analysis_time=self.morning_analysis_time,
            work_followup_times=tuple(self.work_followup_times),
            daily_report_review_time=self.daily_report_review_time,
            due_task_scan_minutes=self.due_task_scan_minutes,
            workflow_reflection_time=self.workflow_reflection_time,
            weekly_summary_time=self.weekly_summary_time,
            github_update_check_time=self.github_update_check_time,
            github_repo=self.github_repo,
            github_branch=self.github_branch,
            github_timeout_seconds=self.github_timeout_seconds,
            scheduler_tick_seconds=self.scheduler_tick_seconds,
        )


def default_home() -> Path:
    root = _env("SALESBRAIN_HOME")
    if root:
        return _expand(root)
    return (Path.home() / ".openclaw" / "workspace" / "salesbrain").resolve()


def default_config_path() -> Path:
    return default_home() / "config.toml"


def default_db_path() -> Path:
    return default_home() / "salesbrain.sqlite"


def _read_config_file(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    with config_path.open("rb") as fh:
        return tomllib.load(fh)


def _resolve_eboss_api_key(config_data: dict[str, Any], home: Path) -> str:
    env_key = _env("EBOSS_API_KEY")
    if env_key:
        return env_key

    eboss_section = config_data.get("eboss", {})
    key_file = _env("EBOSS_API_KEY_FILE")
    if key_file:
        path = _expand(key_file)
    else:
        path = _expand(
            eboss_section.get("api_key_file")
            or (home / "secrets" / "eboss-api-key.txt")
        )
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def load_config(config_path: str | Path | None = None) -> SalesBrainConfig:
    config_path = _expand(config_path or default_config_path())
    data = _read_config_file(config_path)
    profile = data.get("profile", {})
    eboss = data.get("eboss", {})
    openclaw = data.get("openclaw", {})
    schedule = data.get("schedule", {})
    github = data.get("github", {})
    runtime = data.get("runtime", {})

    home = _expand(
        _env("SALESBRAIN_HOME")
        or runtime.get("home")
        or config_path.parent
    )
    db_path = _env("SALESBRAIN_DB_PATH") or runtime.get("db_path") or (home / "salesbrain.sqlite")
    logs_dir = runtime.get("logs_dir") or (home / "logs")
    cron_jobs_path = _env("OPENCLAW_CRON_JOBS_PATH") or openclaw.get("cron_jobs_path") or (home / "cron" / "jobs.json")
    eboss_api_key_file = _env("EBOSS_API_KEY_FILE") or eboss.get("api_key_file") or (home / "secrets" / "eboss-api-key.txt")

    cfg = SalesBrainConfig(
        home=home,
        config_path=config_path,
        db_path=_expand(db_path),
        logs_dir=_expand(logs_dir),
        sales_name=_env("SALESBRAIN_SALES_NAME") or str(profile.get("sales_name", "")).strip(),
        timezone=_env("SALESBRAIN_TIMEZONE") or str(profile.get("timezone", "Asia/Shanghai")).strip() or "Asia/Shanghai",
        eboss_base_url=_env("EBOSS_BASE_URL") or str(eboss.get("base_url", "http://10.21.14.4:30010/api")).strip() or "http://10.21.14.4:30010/api",
        eboss_api_key=_resolve_eboss_api_key(data, home),
        eboss_api_key_file=_expand(eboss_api_key_file),
        eboss_timeout_seconds=_parse_int(_env("EBOSS_TIMEOUT_SECONDS") or eboss.get("timeout_seconds"), 240),
        eboss_page_size=_parse_int(_env("EBOSS_PAGE_SIZE") or eboss.get("page_size"), 50),
        eboss_max_pages=_parse_int(_env("EBOSS_MAX_PAGES") or eboss.get("max_pages"), 20),
        openclaw_mode=_env("OPENCLAW_MODE") or str(openclaw.get("mode", "command")).strip() or "command",
        openclaw_wake_command=_env("OPENCLAW_WAKE_COMMAND") or str(openclaw.get("wake_command", "")).strip(),
        openclaw_cron_list_command=_env("OPENCLAW_CRON_LIST_COMMAND") or str(openclaw.get("cron_list_command", "")).strip(),
        openclaw_cron_remove_command=_env("OPENCLAW_CRON_REMOVE_COMMAND") or str(openclaw.get("cron_remove_command", "")).strip(),
        openclaw_cron_jobs_path=_expand(cron_jobs_path),
        wake_timeout_seconds=_parse_int(_env("OPENCLAW_WAKE_TIMEOUT_SECONDS") or openclaw.get("wake_timeout_seconds"), 120),
        daily_sync_time=str(schedule.get("eboss_daily_sync", "02:00")).strip() or "02:00",
        morning_analysis_time=str(schedule.get("morning_work_analysis", "06:00")).strip() or "06:00",
        work_followup_times=_parse_time_list(
            _env("SALESBRAIN_WORK_FOLLOWUP_TIMES") or schedule.get("work_followup_times"),
            ("08:30", "13:30", "19:30"),
        ),
        daily_report_review_time=_env("SALESBRAIN_DAILY_REPORT_REVIEW_TIME")
        or str(schedule.get("daily_report_review", "22:00")).strip()
        or "22:00",
        due_task_scan_minutes=_parse_int(_env("SALESBRAIN_DUE_TASK_SCAN_MINUTES") or schedule.get("due_task_scan_minutes"), 5),
        workflow_reflection_time=str(schedule.get("workflow_reflection", "23:30")).strip() or "23:30",
        weekly_summary_time=_env("SALESBRAIN_WEEKLY_SUMMARY_TIME")
        or str(schedule.get("weekly_summary", "fri 17:30")).strip()
        or "fri 17:30",
        github_update_check_time=_env("SALESBRAIN_GITHUB_UPDATE_CHECK_TIME") or str(github.get("update_check_time", "09:00")).strip() or "09:00",
        github_repo=_env("SALESBRAIN_GITHUB_REPO") or str(github.get("repo", "FrankeyQu/SalesBrain")).strip() or "FrankeyQu/SalesBrain",
        github_branch=_env("SALESBRAIN_GITHUB_BRANCH") or str(github.get("branch", "main")).strip() or "main",
        github_timeout_seconds=_parse_int(_env("SALESBRAIN_GITHUB_TIMEOUT_SECONDS") or github.get("timeout_seconds"), 30),
        scheduler_tick_seconds=_parse_int(_env("SALESBRAIN_SCHEDULER_TICK_SECONDS") or schedule.get("scheduler_tick_seconds"), 30),
    )
    return cfg.with_resolved_paths()


def write_default_config(
    path: str | Path,
    *,
    sales_name: str,
    timezone: str = "Asia/Shanghai",
    eboss_base_url: str = "http://10.21.14.4:30010/api",
) -> Path:
    target = _expand(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    home = target.parent.resolve()
    text = f"""[profile]
sales_name = {sales_name!r}
timezone = {timezone!r}

[eboss]
base_url = {eboss_base_url!r}
api_key_file = {str((target.parent / "secrets" / "eboss-api-key.txt").resolve())!r}
timeout_seconds = 240
page_size = 50
max_pages = 20

[openclaw]
mode = "command"
wake_command = ""
cron_list_command = ""
cron_remove_command = ""
cron_jobs_path = {str((home / "cron" / "jobs.json").resolve())!r}
wake_timeout_seconds = 120

[schedule]
eboss_daily_sync = "02:00"
morning_work_analysis = "06:00"
work_followup_times = ["08:30", "13:30", "19:30"]
daily_report_review = "22:00"
due_task_scan_minutes = 5
workflow_reflection = "23:30"
weekly_summary = "fri 17:30"
scheduler_tick_seconds = 30

[github]
repo = "FrankeyQu/SalesBrain"
branch = "main"
update_check_time = "09:00"
timeout_seconds = 30

[runtime]
home = {str(home)!r}
db_path = {str((home / "salesbrain.sqlite").resolve())!r}
logs_dir = {str((home / "logs").resolve())!r}
"""
    target.write_text(text, encoding="utf-8")
    return target
