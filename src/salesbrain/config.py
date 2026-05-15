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


def _expand_from(path: str | Path, base: Path) -> Path:
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = base / target
    return target.resolve()


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
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


def _parse_str_list(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    return _parse_time_list(value, default)


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
    eboss_first_full_sync: bool = True
    eboss_full_sync_page_size: int = 100
    eboss_full_sync_max_pages: int = 1000
    eboss_daily_active_sync_max_pages: int = 300
    eboss_detail_sync_concurrency: int = 6
    eboss_detail_sync_timeout_seconds: int = 240
    eboss_current_year_only: bool = True
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
    team_enabled: bool = True
    team_name: str = "SalesBrain"
    team_role: str = "sales"
    team_node_id: str = ""
    team_advertise_host: str = ""
    team_http_host: str = "0.0.0.0"
    team_http_port: int = 37611
    team_broadcast_host: str = "255.255.255.255"
    team_broadcast_port: int = 37610
    team_broadcast_interval_seconds: int = 30
    team_sync_interval_seconds: int = 60
    team_secret: str = "salesbrain-team-v1"
    team_seed_endpoints: tuple[str, ...] = ("http://10.50.3.37:37611",)
    team_scan_cidrs: tuple[str, ...] = ("10.50.0.0/16",)
    team_scan_enabled: bool = True
    team_scan_interval_seconds: int = 1800
    team_scan_concurrency: int = 32
    team_peer_heartbeat_count: int = 2
    company_skillhub_install_command: str = "安装 SalesBrain"

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
            eboss_first_full_sync=self.eboss_first_full_sync,
            eboss_full_sync_page_size=self.eboss_full_sync_page_size,
            eboss_full_sync_max_pages=self.eboss_full_sync_max_pages,
            eboss_daily_active_sync_max_pages=self.eboss_daily_active_sync_max_pages,
            eboss_detail_sync_concurrency=self.eboss_detail_sync_concurrency,
            eboss_detail_sync_timeout_seconds=self.eboss_detail_sync_timeout_seconds,
            eboss_current_year_only=self.eboss_current_year_only,
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
            team_enabled=self.team_enabled,
            team_name=self.team_name,
            team_role=self.team_role,
            team_node_id=self.team_node_id,
            team_advertise_host=self.team_advertise_host,
            team_http_host=self.team_http_host,
            team_http_port=self.team_http_port,
            team_broadcast_host=self.team_broadcast_host,
            team_broadcast_port=self.team_broadcast_port,
            team_broadcast_interval_seconds=self.team_broadcast_interval_seconds,
            team_sync_interval_seconds=self.team_sync_interval_seconds,
            team_secret=self.team_secret,
            team_seed_endpoints=tuple(self.team_seed_endpoints),
            team_scan_cidrs=tuple(self.team_scan_cidrs),
            team_scan_enabled=self.team_scan_enabled,
            team_scan_interval_seconds=self.team_scan_interval_seconds,
            team_scan_concurrency=self.team_scan_concurrency,
            team_peer_heartbeat_count=self.team_peer_heartbeat_count,
            company_skillhub_install_command=self.company_skillhub_install_command,
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


def _resolve_eboss_api_key(config_data: dict[str, Any], home: Path, config_dir: Path) -> str:
    env_key = _env("EBOSS_API_KEY")
    if env_key:
        return env_key

    eboss_section = config_data.get("eboss", {})
    key_file = _env("EBOSS_API_KEY_FILE")
    if key_file:
        path = _expand(key_file)
    elif eboss_section.get("api_key_file"):
        path = _expand_from(eboss_section["api_key_file"], config_dir)
    else:
        path = _expand(home / "secrets" / "eboss-api-key.txt")
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def load_config(config_path: str | Path | None = None) -> SalesBrainConfig:
    config_path = _expand(config_path or default_config_path())
    config_dir = config_path.parent
    data = _read_config_file(config_path)
    profile = data.get("profile", {})
    eboss = data.get("eboss", {})
    openclaw = data.get("openclaw", {})
    schedule = data.get("schedule", {})
    github = data.get("github", {})
    runtime = data.get("runtime", {})
    team = data.get("team", {})
    company = data.get("company", {})

    home_env = _env("SALESBRAIN_HOME")
    home = _expand(home_env) if home_env else _expand_from(runtime.get("home") or config_path.parent, config_dir)

    db_path_env = _env("SALESBRAIN_DB_PATH")
    if db_path_env:
        db_path = _expand(db_path_env)
    elif runtime.get("db_path"):
        db_path = _expand_from(runtime["db_path"], config_dir)
    else:
        db_path = home / "salesbrain.sqlite"

    if runtime.get("logs_dir"):
        logs_dir = _expand_from(runtime["logs_dir"], config_dir)
    else:
        logs_dir = home / "logs"

    cron_jobs_path_env = _env("OPENCLAW_CRON_JOBS_PATH")
    if cron_jobs_path_env:
        cron_jobs_path = _expand(cron_jobs_path_env)
    elif openclaw.get("cron_jobs_path"):
        cron_jobs_path = _expand_from(openclaw["cron_jobs_path"], config_dir)
    else:
        cron_jobs_path = home / "cron" / "jobs.json"

    eboss_api_key_file_env = _env("EBOSS_API_KEY_FILE")
    if eboss_api_key_file_env:
        eboss_api_key_file = _expand(eboss_api_key_file_env)
    elif eboss.get("api_key_file"):
        eboss_api_key_file = _expand_from(eboss["api_key_file"], config_dir)
    else:
        eboss_api_key_file = home / "secrets" / "eboss-api-key.txt"

    cfg = SalesBrainConfig(
        home=home,
        config_path=config_path,
        db_path=_expand(db_path),
        logs_dir=_expand(logs_dir),
        sales_name=_env("SALESBRAIN_SALES_NAME") or str(profile.get("sales_name", "")).strip(),
        timezone=_env("SALESBRAIN_TIMEZONE") or str(profile.get("timezone", "Asia/Shanghai")).strip() or "Asia/Shanghai",
        eboss_base_url=_env("EBOSS_BASE_URL") or str(eboss.get("base_url", "http://10.21.14.4:30010/api")).strip() or "http://10.21.14.4:30010/api",
        eboss_api_key=_resolve_eboss_api_key(data, home, config_dir),
        eboss_api_key_file=_expand(eboss_api_key_file),
        eboss_timeout_seconds=_parse_int(_env("EBOSS_TIMEOUT_SECONDS") or eboss.get("timeout_seconds"), 240),
        eboss_page_size=_parse_int(_env("EBOSS_PAGE_SIZE") or eboss.get("page_size"), 50),
        eboss_max_pages=_parse_int(_env("EBOSS_MAX_PAGES") or eboss.get("max_pages"), 20),
        eboss_first_full_sync=_parse_bool(_env("EBOSS_FIRST_FULL_SYNC") or eboss.get("first_full_sync"), True),
        eboss_full_sync_page_size=_parse_int(_env("EBOSS_FULL_SYNC_PAGE_SIZE") or eboss.get("full_sync_page_size"), 100),
        eboss_full_sync_max_pages=_parse_int(_env("EBOSS_FULL_SYNC_MAX_PAGES") or eboss.get("full_sync_max_pages"), 1000),
        eboss_daily_active_sync_max_pages=_parse_int(
            _env("EBOSS_DAILY_ACTIVE_SYNC_MAX_PAGES") or eboss.get("daily_active_sync_max_pages"),
            300,
        ),
        eboss_detail_sync_concurrency=_parse_int(_env("EBOSS_DETAIL_SYNC_CONCURRENCY") or eboss.get("detail_sync_concurrency"), 6),
        eboss_detail_sync_timeout_seconds=_parse_int(
            _env("EBOSS_DETAIL_SYNC_TIMEOUT_SECONDS") or eboss.get("detail_sync_timeout_seconds"),
            240,
        ),
        eboss_current_year_only=_parse_bool(_env("EBOSS_CURRENT_YEAR_ONLY") or eboss.get("current_year_only"), True),
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
        team_enabled=_parse_bool(_env("SALESBRAIN_TEAM_ENABLED") or team.get("enabled"), True),
        team_name=_env("SALESBRAIN_TEAM_NAME") or str(team.get("name", "SalesBrain")).strip() or "SalesBrain",
        team_role=_env("SALESBRAIN_TEAM_ROLE") or str(team.get("role", "sales")).strip() or "sales",
        team_node_id=_env("SALESBRAIN_TEAM_NODE_ID") or str(team.get("node_id", "")).strip(),
        team_advertise_host=_env("SALESBRAIN_TEAM_ADVERTISE_HOST") or str(team.get("advertise_host", "")).strip(),
        team_http_host=_env("SALESBRAIN_TEAM_HTTP_HOST") or str(team.get("http_host", "0.0.0.0")).strip() or "0.0.0.0",
        team_http_port=_parse_int(_env("SALESBRAIN_TEAM_HTTP_PORT") or team.get("http_port"), 37611),
        team_broadcast_host=_env("SALESBRAIN_TEAM_BROADCAST_HOST") or str(team.get("broadcast_host", "255.255.255.255")).strip() or "255.255.255.255",
        team_broadcast_port=_parse_int(_env("SALESBRAIN_TEAM_BROADCAST_PORT") or team.get("broadcast_port"), 37610),
        team_broadcast_interval_seconds=_parse_int(_env("SALESBRAIN_TEAM_BROADCAST_INTERVAL_SECONDS") or team.get("broadcast_interval_seconds"), 30),
        team_sync_interval_seconds=_parse_int(_env("SALESBRAIN_TEAM_SYNC_INTERVAL_SECONDS") or team.get("sync_interval_seconds"), 60),
        team_secret=_env("SALESBRAIN_TEAM_SECRET") or str(team.get("secret", "salesbrain-team-v1")).strip() or "salesbrain-team-v1",
        team_seed_endpoints=_parse_str_list(
            _env("SALESBRAIN_TEAM_SEED_ENDPOINTS") or team.get("seed_endpoints"),
            ("http://10.50.3.37:37611",),
        ),
        team_scan_cidrs=_parse_str_list(
            _env("SALESBRAIN_TEAM_SCAN_CIDRS") or team.get("scan_cidrs"),
            ("10.50.0.0/16",),
        ),
        team_scan_enabled=_parse_bool(_env("SALESBRAIN_TEAM_SCAN_ENABLED") or team.get("scan_enabled"), True),
        team_scan_interval_seconds=_parse_int(_env("SALESBRAIN_TEAM_SCAN_INTERVAL_SECONDS") or team.get("scan_interval_seconds"), 1800),
        team_scan_concurrency=_parse_int(_env("SALESBRAIN_TEAM_SCAN_CONCURRENCY") or team.get("scan_concurrency"), 32),
        team_peer_heartbeat_count=_parse_int(_env("SALESBRAIN_TEAM_PEER_HEARTBEAT_COUNT") or team.get("peer_heartbeat_count"), 2),
        company_skillhub_install_command=_env("SALESBRAIN_COMPANY_SKILLHUB_INSTALL_COMMAND")
        or str(company.get("skillhub_install_command", "安装 SalesBrain")).strip()
        or "安装 SalesBrain",
    )
    return cfg.with_resolved_paths()


def write_default_config(
    path: str | Path,
    *,
    sales_name: str,
    timezone: str = "Asia/Shanghai",
    eboss_base_url: str = "http://10.21.14.4:30010/api",
    openclaw_wake_command: str = "",
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
first_full_sync = true
full_sync_page_size = 100
full_sync_max_pages = 1000
daily_active_sync_max_pages = 300
detail_sync_concurrency = 6
detail_sync_timeout_seconds = 240
current_year_only = true

[openclaw]
mode = "command"
wake_command = {openclaw_wake_command!r}
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

[company]
skillhub_install_command = "安装 SalesBrain"

[team]
enabled = true
name = "SalesBrain"
role = "sales"
node_id = ""
advertise_host = ""
http_host = "0.0.0.0"
http_port = 37611
broadcast_host = "255.255.255.255"
broadcast_port = 37610
broadcast_interval_seconds = 30
sync_interval_seconds = 60
secret = "salesbrain-team-v1"
seed_endpoints = ["http://10.50.3.37:37611"]
scan_cidrs = ["10.50.0.0/16"]
scan_enabled = true
scan_interval_seconds = 1800
scan_concurrency = 32
peer_heartbeat_count = 2

[runtime]
home = {str(home)!r}
db_path = {str((home / "salesbrain.sqlite").resolve())!r}
logs_dir = {str((home / "logs").resolve())!r}
"""
    target.write_text(text, encoding="utf-8")
    return target
