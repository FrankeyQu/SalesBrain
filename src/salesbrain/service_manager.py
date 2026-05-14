from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import SalesBrainConfig
from .db import SalesBrainStore
from .timeutil import now_in_zone, parse_iso_datetime


CRON_BEGIN = "# BEGIN SalesBrain service watchdog"
CRON_END = "# END SalesBrain service watchdog"
DEFAULT_RESTART_STALE_SECONDS = 1800


@dataclass(slots=True)
class ServicePaths:
    service_dir: Path
    pid_file: Path
    launcher_script: Path
    watchdog_script: Path
    daemon_log: Path
    watchdog_log: Path
    watchdog_lock: Path


def service_paths(config: SalesBrainConfig) -> ServicePaths:
    service_dir = config.home / "service"
    return ServicePaths(
        service_dir=service_dir,
        pid_file=service_dir / "salesbrain-daemon.pid",
        launcher_script=service_dir / "salesbrain-daemon.sh",
        watchdog_script=service_dir / "salesbrain-watchdog.sh",
        daemon_log=config.logs_dir / "daemon.log",
        watchdog_log=config.logs_dir / "watchdog.log",
        watchdog_lock=service_dir / "salesbrain-watchdog.lock",
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _pid1() -> dict[str, str]:
    comm = _read_text(Path("/proc/1/comm")).strip()
    cmdline = ""
    try:
        cmdline = Path("/proc/1/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    return {"comm": comm, "cmdline": cmdline}


def detect_environment() -> dict[str, Any]:
    pid1 = _pid1()
    return {
        "docker": Path("/.dockerenv").exists(),
        "containerenv": Path("/run/.containerenv").exists(),
        "pid1": pid1,
        "systemctl": shutil.which("systemctl"),
        "supervisorctl": shutil.which("supervisorctl"),
        "crontab": shutil.which("crontab"),
        "recommended_mode": "cron",
        "reason": "systemd/supervisor are often unavailable in Openclaw containers; Linux cron can run the SalesBrain watchdog outside Openclaw business cron.",
    }


def _quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def _python_command() -> str:
    return sys.executable or "python3"


def _daemon_args(config: SalesBrainConfig, *, no_team: bool = False) -> list[str]:
    args = [_python_command(), "-m", "salesbrain", "--config", str(config.config_path), "daemon"]
    if no_team:
        args.append("--no-team")
    return args


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except Exception:
        return None


def _proc_cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _is_salesbrain_daemon(pid: int, config: SalesBrainConfig) -> bool:
    if not _pid_alive(pid):
        return False
    cmdline = _proc_cmdline(pid)
    if not cmdline:
        return not Path("/proc").exists()
    config_text = str(config.config_path)
    return "salesbrain" in cmdline and "daemon" in cmdline and (config_text in cmdline or "--config" not in cmdline)


def find_daemon_pid(config: SalesBrainConfig) -> int | None:
    paths = service_paths(config)
    pid = _read_pid(paths.pid_file)
    if pid and _is_salesbrain_daemon(pid, config):
        return pid
    proc = Path("/proc")
    if proc.exists():
        for child in proc.iterdir():
            if not child.name.isdigit():
                continue
            candidate = int(child.name)
            if _is_salesbrain_daemon(candidate, config):
                return candidate
    return None


def _heartbeat_state(config: SalesBrainConfig) -> dict[str, Any]:
    store = SalesBrainStore(config.db_path)
    try:
        try:
            daemon_heartbeat = store.get_state("scheduler_daemon_heartbeat_at")
            monitor_run = store.get_state("scheduler_monitor_last_run_at")
        except Exception:
            daemon_heartbeat = None
            monitor_run = None
    finally:
        store.close()

    now = now_in_zone(config.timezone)
    heartbeat_age = None
    if daemon_heartbeat:
        try:
            heartbeat_age = max(0, int((now - parse_iso_datetime(str(daemon_heartbeat))).total_seconds()))
        except Exception:
            heartbeat_age = None
    return {
        "daemon_last_heartbeat_at": daemon_heartbeat,
        "monitor_last_run_at": monitor_run,
        "heartbeat_age_seconds": heartbeat_age,
    }


def service_status(config: SalesBrainConfig) -> dict[str, Any]:
    paths = service_paths(config)
    pid = find_daemon_pid(config)
    heartbeat = _heartbeat_state(config)
    heartbeat_age = heartbeat.get("heartbeat_age_seconds")
    daemon_stale = heartbeat_age is not None and heartbeat_age > DEFAULT_RESTART_STALE_SECONDS
    return {
        "ok": True,
        "running": pid is not None,
        "pid": pid,
        "pid_file": str(paths.pid_file),
        "daemon_stale": daemon_stale,
        "paths": {key: str(value) for key, value in asdict(paths).items()},
        "heartbeat": heartbeat,
        "environment": detect_environment(),
    }


def _script_header() -> str:
    return "#!/bin/sh\nset -eu\n"


def build_launcher_script(config: SalesBrainConfig, *, no_team: bool = False) -> str:
    paths = service_paths(config)
    args = " ".join(_quote(arg) for arg in _daemon_args(config, no_team=no_team))
    return (
        _script_header()
        + f"mkdir -p {_quote(paths.service_dir)} {_quote(config.logs_dir)}\n"
        + f"cd {_quote(config.home)}\n"
        + f"echo $$ > {_quote(paths.pid_file)}\n"
        + f"exec {args} >> {_quote(paths.daemon_log)} 2>&1\n"
    )


def build_watchdog_script(config: SalesBrainConfig, *, no_team: bool = False) -> str:
    paths = service_paths(config)
    args = [
        _python_command(),
        "-m",
        "salesbrain",
        "--config",
        str(config.config_path),
        "service",
        "ensure-running",
        "--quiet",
    ]
    if no_team:
        args.append("--no-team")
    command = " ".join(_quote(arg) for arg in args)
    return (
        _script_header()
        + f"mkdir -p {_quote(paths.service_dir)} {_quote(config.logs_dir)}\n"
        + f"LOCK={_quote(paths.watchdog_lock)}\n"
        + 'if command -v flock >/dev/null 2>&1; then\n'
        + '  exec 9>"$LOCK"\n'
        + "  flock -n 9 || exit 0\n"
        + "else\n"
        + '  if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then exit 0; fi\n'
        + '  echo $$ > "$LOCK"\n'
        + "  trap 'rm -f \"$LOCK\"' EXIT\n"
        + "fi\n"
        + f"{command} >> {_quote(paths.watchdog_log)} 2>&1\n"
    )


def write_service_scripts(config: SalesBrainConfig, *, no_team: bool = False) -> dict[str, str]:
    paths = service_paths(config)
    paths.service_dir.mkdir(parents=True, exist_ok=True)
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    launcher = build_launcher_script(config, no_team=no_team)
    watchdog = build_watchdog_script(config, no_team=no_team)
    paths.launcher_script.write_text(launcher, encoding="utf-8")
    paths.watchdog_script.write_text(watchdog, encoding="utf-8")
    paths.launcher_script.chmod(0o755)
    paths.watchdog_script.chmod(0o755)
    return {
        "launcher_script": str(paths.launcher_script),
        "watchdog_script": str(paths.watchdog_script),
    }


def start_daemon(config: SalesBrainConfig, *, no_team: bool = False, force: bool = False) -> dict[str, Any]:
    existing = find_daemon_pid(config)
    if existing and not force:
        return {"ok": True, "started": False, "already_running": True, "pid": existing}
    if existing and force:
        stop_daemon(config)
    paths = service_paths(config)
    write_service_scripts(config, no_team=no_team)
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    log_fh = paths.daemon_log.open("ab")
    try:
        proc = subprocess.Popen(
            _daemon_args(config, no_team=no_team),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(config.home),
            close_fds=True,
            start_new_session=True,
        )
    finally:
        log_fh.close()
    paths.pid_file.write_text(str(proc.pid), encoding="utf-8")
    time.sleep(0.2)
    running = _pid_alive(proc.pid)
    return {
        "ok": running,
        "started": running,
        "pid": proc.pid,
        "daemon_log": str(paths.daemon_log),
        "message": "started" if running else "daemon_exited_immediately",
    }


def stop_daemon(config: SalesBrainConfig, *, timeout_seconds: int = 10) -> dict[str, Any]:
    paths = service_paths(config)
    pid = find_daemon_pid(config)
    if not pid:
        if paths.pid_file.exists():
            paths.pid_file.unlink(missing_ok=True)
        return {"ok": True, "stopped": False, "already_stopped": True}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        paths.pid_file.unlink(missing_ok=True)
        return {"ok": True, "stopped": False, "already_stopped": True}
    deadline = time.time() + max(1, timeout_seconds)
    while time.time() < deadline:
        if not _pid_alive(pid):
            paths.pid_file.unlink(missing_ok=True)
            return {"ok": True, "stopped": True, "pid": pid}
        time.sleep(0.2)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    paths.pid_file.unlink(missing_ok=True)
    return {"ok": True, "stopped": True, "killed": True, "pid": pid}


def restart_daemon(config: SalesBrainConfig, *, no_team: bool = False) -> dict[str, Any]:
    stop_result = stop_daemon(config)
    start_result = start_daemon(config, no_team=no_team, force=True)
    return {"ok": bool(start_result.get("ok")), "stop": stop_result, "start": start_result}


def _run_monitor(config: SalesBrainConfig) -> dict[str, Any]:
    from .service import SalesBrainService

    service = SalesBrainService(config)
    try:
        service.bootstrap()
        return service.monitor_scheduler(repair=True)
    finally:
        service.close()


def ensure_running(
    config: SalesBrainConfig,
    *,
    no_team: bool = False,
    restart_stale_after_seconds: int = DEFAULT_RESTART_STALE_SECONDS,
) -> dict[str, Any]:
    monitor = _run_monitor(config)
    status_before = service_status(config)
    action = "none"
    start_result: dict[str, Any] | None = None
    heartbeat_age = status_before.get("heartbeat", {}).get("heartbeat_age_seconds")
    stale = heartbeat_age is not None and heartbeat_age > restart_stale_after_seconds
    if not status_before["running"]:
        action = "start"
        start_result = start_daemon(config, no_team=no_team)
    elif stale:
        action = "restart_stale"
        start_result = restart_daemon(config, no_team=no_team)
    status_after = service_status(config)
    return {
        "ok": bool(status_after["running"]),
        "action": action,
        "monitor": monitor,
        "status_before": status_before,
        "start_result": start_result,
        "status_after": status_after,
    }


def _cron_block(config: SalesBrainConfig) -> str:
    paths = service_paths(config)
    return "\n".join(
        [
            CRON_BEGIN,
            f"* * * * /bin/sh {_quote(paths.watchdog_script)}",
            CRON_END,
        ]
    )


def _read_crontab() -> str:
    proc = subprocess.run(["crontab", "-l"], text=True, capture_output=True, check=False)
    if proc.returncode == 0:
        return proc.stdout.rstrip()
    if "no crontab" in proc.stderr.lower() or "no crontab" in proc.stdout.lower():
        return ""
    return proc.stdout.rstrip()


def _replace_cron_block(existing: str, block: str | None) -> str:
    lines = existing.splitlines()
    output: list[str] = []
    in_block = False
    for line in lines:
        if line.strip() == CRON_BEGIN:
            in_block = True
            continue
        if line.strip() == CRON_END:
            in_block = False
            continue
        if not in_block:
            output.append(line)
    if block:
        if output and output[-1].strip():
            output.append("")
        output.extend(block.splitlines())
    return "\n".join(output).rstrip() + "\n"


def install_watchdog(
    config: SalesBrainConfig,
    *,
    mode: str = "auto",
    dry_run: bool = False,
    start: bool = True,
    no_team: bool = False,
) -> dict[str, Any]:
    if mode not in {"auto", "cron"}:
        raise ValueError(f"unsupported_service_mode: {mode}")
    paths = service_paths(config)
    scripts = {
        "launcher_script": str(paths.launcher_script),
        "watchdog_script": str(paths.watchdog_script),
    }
    cron_block = _cron_block(config)
    existing_crontab = ""
    new_crontab = _replace_cron_block(existing_crontab, cron_block)
    if not dry_run:
        if not shutil.which("crontab"):
            raise RuntimeError("crontab_not_found")
        scripts = write_service_scripts(config, no_team=no_team)
        existing_crontab = _read_crontab()
        new_crontab = _replace_cron_block(existing_crontab, cron_block)
        proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "crontab_install_failed")
    else:
        scripts.update(
            {
                "launcher_content": build_launcher_script(config, no_team=no_team),
                "watchdog_content": build_watchdog_script(config, no_team=no_team),
            }
        )
    start_result = None
    if start and not dry_run:
        start_result = start_daemon(config, no_team=no_team)
    return {
        "ok": True,
        "mode": "cron",
        "dry_run": dry_run,
        "installed": not dry_run,
        "scripts": scripts,
        "cron_entry": cron_block,
        "crontab": new_crontab,
        "start_result": start_result,
        "environment": detect_environment(),
    }


def uninstall_watchdog(config: SalesBrainConfig, *, dry_run: bool = False) -> dict[str, Any]:
    existing_crontab = _read_crontab() if shutil.which("crontab") else ""
    new_crontab = _replace_cron_block(existing_crontab, None)
    if not dry_run:
        if not shutil.which("crontab"):
            raise RuntimeError("crontab_not_found")
        proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "crontab_uninstall_failed")
    return {"ok": True, "dry_run": dry_run, "removed": not dry_run, "crontab": new_crontab}
