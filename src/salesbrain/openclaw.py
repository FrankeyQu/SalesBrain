from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import SalesBrainConfig


BUSINESS_CRON_KEYWORDS = [
    "提醒",
    "跟进",
    "回看",
    "复盘",
    "日报",
    "早报",
    "午报",
    "晚报",
    "客户",
    "商机",
    "销售",
    "任务",
    "推进",
    "回款",
    "预算",
    "预测",
    "拜访",
    "承诺",
    "待办",
]

SYSTEM_CRON_KEYWORDS = [
    "监控",
    "健康",
    "日志",
    "备份",
    "清理",
    "守护",
    "拉起",
    "重启",
    "部署",
    "巡检",
    "维护",
]


def _load_json_text(text: str) -> Any:
    text = text.strip()
    if not text:
        return {}
    return json.loads(text)


def _dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _run_command(command: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    env = os.environ.copy()
    env["SALESBRAIN_PAYLOAD_JSON"] = json.dumps(payload, ensure_ascii=False)
    env["SALESBRAIN_WAKE_KIND"] = str(payload.get("kind", ""))
    proc = subprocess.run(
        command,
        shell=True,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"openclaw_command_failed: {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    stdout = proc.stdout.strip()
    if not stdout:
        return {"ok": True, "raw_output": ""}
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": True, "raw_output": stdout}
    if isinstance(parsed, dict):
        return parsed
    return {"ok": True, "data": parsed}


def _parse_cron_jobs_from_data(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("jobs", "items", "data", "records"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _cron_job_text(job: dict[str, Any]) -> str:
    pieces = [
        str(job.get("id", "")),
        str(job.get("job_id", "")),
        str(job.get("name", "")),
        str(job.get("prompt", "")),
        str(job.get("schedule_display", "")),
        str(job.get("deliver", "")),
        str(job.get("state", "")),
    ]
    schedule = job.get("schedule")
    if isinstance(schedule, dict):
        pieces.extend(
            str(schedule.get(key, ""))
            for key in ("expr", "display", "kind")
        )
    return " ".join(piece for piece in pieces if piece).lower()


def _is_business_cron(job: dict[str, Any]) -> bool:
    text = _cron_job_text(job)
    if any(keyword.lower() in text for keyword in SYSTEM_CRON_KEYWORDS):
        return False
    return any(keyword.lower() in text for keyword in BUSINESS_CRON_KEYWORDS)


def normalize_cron_job(job: dict[str, Any]) -> dict[str, Any]:
    schedule = job.get("schedule")
    schedule_display = job.get("schedule_display")
    if schedule_display is None and isinstance(schedule, dict):
        schedule_display = schedule.get("display") or schedule.get("expr")
    if schedule_display is None:
        schedule_display = job.get("schedule")
    return {
        "id": str(job.get("id") or job.get("job_id") or ""),
        "name": job.get("name") or job.get("title") or "",
        "prompt": job.get("prompt") or "",
        "schedule_display": schedule_display or "",
        "next_run_at": job.get("next_run_at"),
        "last_run_at": job.get("last_run_at"),
        "state": job.get("state") or ("scheduled" if job.get("enabled", True) else "paused"),
        "enabled": job.get("enabled", True),
        "deliver": job.get("deliver"),
        "script": job.get("script"),
        "raw": job,
        "business_candidate": _is_business_cron(job),
    }


@dataclass(slots=True)
class OpenClawWakeResult:
    ok: bool
    raw: dict[str, Any]

    @property
    def summary(self) -> str:
        if isinstance(self.raw.get("summary"), str):
            return self.raw["summary"]
        if isinstance(self.raw.get("raw_output"), str):
            return self.raw["raw_output"]
        return ""


class OpenClawAdapter:
    def __init__(self, config: SalesBrainConfig):
        self.config = config

    def wake(self, kind: str, payload: dict[str, Any]) -> OpenClawWakeResult:
        body = dict(payload)
        body["kind"] = kind
        body["salesbrain_home"] = str(self.config.home)

        if self.config.openclaw_mode == "command" and self.config.openclaw_wake_command:
            raw = _run_command(self.config.openclaw_wake_command, body, timeout=self.config.wake_timeout_seconds)
            return OpenClawWakeResult(ok=bool(raw.get("ok", True)), raw=raw)

        outbox = self.config.home / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        path = outbox / f"{kind}-{body.get('run_id', 'wake')}.json"
        path.write_text(_dump_json(body), encoding="utf-8")
        return OpenClawWakeResult(ok=True, raw={"ok": True, "delivery": "file", "path": str(path)})

    def list_cron_jobs(self) -> list[dict[str, Any]]:
        if self.config.openclaw_mode == "command" and self.config.openclaw_cron_list_command:
            raw = _run_command(self.config.openclaw_cron_list_command, {"kind": "cron_list"}, timeout=self.config.wake_timeout_seconds)
            jobs = _parse_cron_jobs_from_data(raw)
            return [normalize_cron_job(job) for job in jobs]

        path = self.config.openclaw_cron_jobs_path
        if not path.exists():
            return []
        data = _load_json_text(path.read_text(encoding="utf-8"))
        jobs = _parse_cron_jobs_from_data(data)
        return [normalize_cron_job(job) for job in jobs]

    def remove_cron_job(self, job_id: str) -> bool:
        if self.config.openclaw_mode == "command" and self.config.openclaw_cron_remove_command:
            raw = _run_command(
                self.config.openclaw_cron_remove_command,
                {"kind": "cron_remove", "job_id": job_id},
                timeout=self.config.wake_timeout_seconds,
            )
            return bool(raw.get("ok", True))

        path = self.config.openclaw_cron_jobs_path
        if not path.exists():
            return False
        original_text = path.read_text(encoding="utf-8")
        data = _load_json_text(original_text)
        removed = False
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            before = len(data["jobs"])
            data["jobs"] = [
                job for job in data["jobs"]
                if str(job.get("id") or job.get("job_id") or "") != job_id
            ]
            removed = len(data["jobs"]) != before
        elif isinstance(data, list):
            before = len(data)
            data = [
                job for job in data
                if not isinstance(job, dict) or str(job.get("id") or job.get("job_id") or "") != job_id
            ]
            removed = len(data) != before
        if removed:
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(path.parent)) as fh:
                fh.write(_dump_json(data))
                temp_name = fh.name
            Path(temp_name).replace(path)
        return removed

    def filter_business_cron_jobs(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [job for job in jobs if job.get("business_candidate")]
