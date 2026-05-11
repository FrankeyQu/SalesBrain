from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import sys
import getpass
from pathlib import Path
from typing import Any, Callable

from .config import default_config_path, load_config, write_default_config
from .scheduler import SalesBrainScheduler
from .service import SalesBrainService


def _dump(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _open_service(config_path: str | None) -> SalesBrainService:
    return SalesBrainService(load_config(config_path))


def _run_with_service(
    config_path: str | None,
    handler: Callable[[SalesBrainService], Any],
    *,
    bootstrap: bool = True,
) -> Any:
    service = _open_service(config_path)
    try:
        if bootstrap:
            service.bootstrap()
        return handler(service)
    finally:
        service.close()


def _parse_updates(values: list[str]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid_update: {value!r}")
        key, raw = value.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"invalid_update: {value!r}")
        if raw.lower() in {"null", "none"}:
            updates[key] = None
            continue
        if raw.lower() in {"true", "false"}:
            updates[key] = raw.lower() == "true"
            continue
        try:
            updates[key] = json.loads(raw)
        except Exception:
            updates[key] = raw
    return updates


def _prompt_eboss_api_key() -> str:
    if not sys.stdin.isatty():
        raise RuntimeError("missing_eboss_api_key")
    value = getpass.getpass("请输入 EBOSS API Key (api-key header): ").strip()
    if not value:
        raise RuntimeError("missing_eboss_api_key")
    return value


def cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config) if args.config else default_config_path()
    if config_path.exists() and not args.force:
        raise FileExistsError(f"config_exists: {config_path}")
    sales_name = args.sales_name or "unknown"
    eboss_api_key = (args.eboss_api_key or "").strip() or os.getenv("EBOSS_API_KEY", "").strip()
    if not eboss_api_key:
        eboss_api_key = _prompt_eboss_api_key()
    write_default_config(
        config_path,
        sales_name=sales_name,
        timezone=args.timezone,
        eboss_base_url=args.eboss_base_url,
    )
    if eboss_api_key:
        secret_file = config_path.parent / "secrets" / "eboss-api-key.txt"
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(eboss_api_key, encoding="utf-8")
    result = _run_with_service(str(config_path), lambda service: service.first_run(), bootstrap=False)
    result["config_path"] = str(config_path.resolve())
    return result


def cmd_bootstrap(args: argparse.Namespace) -> dict[str, Any]:
    return _run_with_service(args.config, lambda service: service.bootstrap(), bootstrap=False)


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    def _run(service: SalesBrainService) -> dict[str, Any]:
        return {
            "profile": service.get_profile(),
            "monitor_state": service.get_monitor_state(),
            "scheduler_jobs": service.list_scheduler_jobs(),
            "pending_tasks": service.list_tasks(status="pending", limit=20),
            "due_tasks": service.list_due_tasks(limit=20),
            "review_suggestions": service.list_review_suggestions(status="open", limit=20),
            "workflow_items": service.list_workflow_items(limit=20),
            "wake_runs": service.list_wake_runs(limit=20),
            "latest_sync": service.store.latest_sync_run(),
        }

    return _run_with_service(args.config, _run)


def cmd_daemon(args: argparse.Namespace) -> dict[str, Any]:
    def _run(service: SalesBrainService) -> dict[str, Any]:
        scheduler = SalesBrainScheduler(service, service.store, service.config)
        scheduler.seed_default_jobs()
        if args.once:
            return {
                "ok": True,
                "results": [asdict(result) for result in scheduler.run_due_jobs()],
            }
        try:
            scheduler.run_forever()
        except KeyboardInterrupt:
            return {"ok": True, "stopped": True}
        return {"ok": True}

    return _run_with_service(args.config, _run)


def cmd_monitor(args: argparse.Namespace) -> dict[str, Any]:
    def _run(service: SalesBrainService) -> dict[str, Any]:
        return service.monitor_scheduler(repair=not args.report_only)

    return _run_with_service(args.config, _run)


def cmd_wake(args: argparse.Namespace) -> dict[str, Any]:
    def _run(service: SalesBrainService) -> dict[str, Any]:
        if args.kind == "initial":
            return service.initial_analysis()
        if args.kind == "morning":
            return service.morning_analysis()
        if args.kind == "followup":
            return service.work_followup()
        if args.kind == "review":
            return service.daily_report_review()
        if args.kind == "weekly":
            return service.weekly_summary()
        if args.kind == "due":
            return service.scan_due_tasks()
        if args.kind == "workflow":
            return service.workflow_reflection()
        raise ValueError(f"unknown_wake_kind: {args.kind}")

    return _run_with_service(args.config, _run)


def cmd_eboss_sync(args: argparse.Namespace) -> dict[str, Any]:
    return _run_with_service(args.config, lambda service: service.sync_eboss())


def cmd_eboss_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    return _run_with_service(args.config, lambda service: service.get_eboss_snapshot(limit=args.limit))


def cmd_eboss_search(args: argparse.Namespace) -> dict[str, Any]:
    return _run_with_service(args.config, lambda service: service.search_eboss(args.keyword, limit=args.limit))


def cmd_tasks_list(args: argparse.Namespace) -> dict[str, Any]:
    return _run_with_service(args.config, lambda service: {"tasks": service.list_tasks(status=args.status, limit=args.limit)})


def cmd_tasks_due(args: argparse.Namespace) -> dict[str, Any]:
    return _run_with_service(args.config, lambda service: {"tasks": service.list_due_tasks(limit=args.limit)})


def cmd_tasks_add(args: argparse.Namespace) -> dict[str, Any]:
    def _run(service: SalesBrainService) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": args.title,
            "description": args.description,
            "due_at": args.due_at,
            "remind_at": args.remind_at,
            "priority": args.priority,
            "status": args.status,
            "source_type": args.source_type,
            "source_ref": args.source_ref,
            "payload_json": _parse_updates(args.payload or []),
        }
        return service.create_task(payload)

    return _run_with_service(args.config, _run)


def cmd_tasks_update(args: argparse.Namespace) -> dict[str, Any]:
    return _run_with_service(
        args.config,
        lambda service: service.update_task(args.task_id, _parse_updates(args.set or [])),
    )


def cmd_github_check(args: argparse.Namespace) -> dict[str, Any]:
    return _run_with_service(args.config, lambda service: service.github_update_check())


def cmd_github_mark_installed(args: argparse.Namespace) -> dict[str, Any]:
    def _run(service: SalesBrainService) -> dict[str, Any]:
        return service.github_mark_installed(args.sha or None)

    return _run_with_service(args.config, _run)


def cmd_tasks_set_status(status: str) -> Callable[[argparse.Namespace], dict[str, Any]]:
    def _runner(args: argparse.Namespace) -> dict[str, Any]:
        updates: dict[str, Any] = {"status": status}
        if args.due_at:
            updates["due_at"] = args.due_at
        if args.remind_at:
            updates["remind_at"] = args.remind_at
        if args.priority:
            updates["priority"] = args.priority
        if args.description is not None:
            updates["description"] = args.description
        return _run_with_service(args.config, lambda service: service.update_task(args.task_id, updates))

    return _runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="salesbrain", description="SalesBrain local mentor sidecar")
    parser.add_argument("--config", help="Path to config.toml")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="Path to config.toml", default=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", parents=[common], help="Create default config and bootstrap storage")
    init.add_argument("--sales-name", default="", help="Sales person name")
    init.add_argument("--timezone", default="Asia/Shanghai", help="Timezone name")
    init.add_argument("--eboss-base-url", default="http://10.21.14.4:30010/api", help="EBOSS API base URL")
    init.add_argument("--eboss-api-key", default="", help="EBOSS api-key header value")
    init.add_argument("--force", action="store_true", help="Overwrite existing config")
    init.set_defaults(func=cmd_init)

    bootstrap = sub.add_parser("bootstrap", parents=[common], help="Create tables and seed scheduler jobs")
    bootstrap.set_defaults(func=cmd_bootstrap)

    status = sub.add_parser("status", parents=[common], help="Show current local state")
    status.set_defaults(func=cmd_status)

    daemon = sub.add_parser("daemon", parents=[common], help="Run the scheduler loop")
    daemon.add_argument("--once", action="store_true", help="Run due jobs once and exit")
    daemon.set_defaults(func=cmd_daemon)

    monitor = sub.add_parser("monitor", parents=[common], help="Run a health check and repair due jobs once")
    monitor.add_argument("--report-only", action="store_true", help="Do not run due jobs, only report health")
    monitor.add_argument("--strict", action="store_true", help="Exit non-zero when the monitor reports degradation")
    monitor.set_defaults(func=cmd_monitor)

    wake = sub.add_parser("wake", parents=[common], help="Wake Openclaw for a specific review pass")
    wake_sub = wake.add_subparsers(dest="kind", required=True)
    for kind in ("initial", "morning", "followup", "review", "weekly", "due", "workflow"):
        wake_sub.add_parser(kind, parents=[common], help=f"Run {kind} wake-up").set_defaults(func=cmd_wake, kind=kind)

    eboss = sub.add_parser("eboss", parents=[common], help="EBOSS operations")
    eboss_sub = eboss.add_subparsers(dest="eboss_command", required=True)
    eboss_sub.add_parser("sync", parents=[common], help="Pull EBOSS data once").set_defaults(func=cmd_eboss_sync)
    eboss_snapshot = eboss_sub.add_parser("snapshot", parents=[common], help="Show local EBOSS snapshot")
    eboss_snapshot.add_argument("--limit", type=int, default=50)
    eboss_snapshot.set_defaults(func=cmd_eboss_snapshot)
    eboss_search = eboss_sub.add_parser("search", parents=[common], help="Search synced EBOSS records")
    eboss_search.add_argument("keyword")
    eboss_search.add_argument("--limit", type=int, default=20)
    eboss_search.set_defaults(func=cmd_eboss_search)

    tasks = sub.add_parser("tasks", parents=[common], help="Follow-up task operations")
    tasks_sub = tasks.add_subparsers(dest="tasks_command", required=True)
    tasks_list = tasks_sub.add_parser("list", parents=[common], help="List tasks")
    tasks_list.add_argument("--status", default=None)
    tasks_list.add_argument("--limit", type=int, default=100)
    tasks_list.set_defaults(func=cmd_tasks_list)
    tasks_due = tasks_sub.add_parser("due", parents=[common], help="List due tasks")
    tasks_due.add_argument("--limit", type=int, default=20)
    tasks_due.set_defaults(func=cmd_tasks_due)
    tasks_add = tasks_sub.add_parser("add", parents=[common], help="Create a task")
    tasks_add.add_argument("--title", required=True)
    tasks_add.add_argument("--description", default=None)
    tasks_add.add_argument("--due-at", dest="due_at", required=True)
    tasks_add.add_argument("--remind-at", dest="remind_at", default=None)
    tasks_add.add_argument("--priority", default="normal", choices=["low", "normal", "high", "urgent"])
    tasks_add.add_argument("--status", default="pending", choices=["pending", "snoozed", "done", "cancelled"])
    tasks_add.add_argument("--source-type", dest="source_type", default=None)
    tasks_add.add_argument("--source-ref", dest="source_ref", default=None)
    tasks_add.add_argument("--payload", action="append", default=[], help="Extra key=value entries saved in payload_json")
    tasks_add.set_defaults(func=cmd_tasks_add)
    tasks_update = tasks_sub.add_parser("update", parents=[common], help="Update a task with key=value pairs")
    tasks_update.add_argument("task_id")
    tasks_update.add_argument("--set", action="append", default=[], help="key=value update entries")
    tasks_update.set_defaults(func=cmd_tasks_update)
    for name, status in (("done", "done"), ("snooze", "snoozed"), ("cancel", "cancelled")):
        task_status = tasks_sub.add_parser(name, parents=[common], help=f"Mark task as {status}")
        task_status.add_argument("task_id")
        task_status.add_argument("--due-at", dest="due_at", default=None)
        task_status.add_argument("--remind-at", dest="remind_at", default=None)
        task_status.add_argument("--priority", default=None, choices=["low", "normal", "high", "urgent"])
        task_status.add_argument("--description", default=None)
        task_status.set_defaults(func=cmd_tasks_set_status(status))

    github = sub.add_parser("github", parents=[common], help="GitHub update workflow")
    github_sub = github.add_subparsers(dest="github_command", required=True)
    github_check = github_sub.add_parser("check", parents=[common], help="Check whether GitHub has a newer SalesBrain commit")
    github_check.set_defaults(func=cmd_github_check)
    github_mark = github_sub.add_parser("mark-installed", parents=[common], help="Mark the currently installed commit as up to date")
    github_mark.add_argument("--sha", default=None, help="Explicit commit SHA to record")
    github_mark.set_defaults(func=cmd_github_mark_installed)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except Exception as exc:  # pragma: no cover - exercised via CLI smoke checks
        _dump({"ok": False, "error": type(exc).__name__, "message": str(exc)})
        return 1
    _dump(result)
    if isinstance(result, dict) and result.get("ok") is False:
        return 1
    if getattr(args, "command", "") == "monitor" and getattr(args, "strict", False):
        if isinstance(result, dict) and result.get("health_level") != "healthy":
            return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
