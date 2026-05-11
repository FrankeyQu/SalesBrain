from __future__ import annotations

import json
from typing import Any


RESPONSE_SCHEMA = {
    "ok": True,
    "summary": "short human-readable summary",
    "tasks_to_create": [
        {
            "title": "string",
            "description": "string",
            "due_at": "2026-05-12T09:30:00+08:00",
            "remind_at": "2026-05-12T09:00:00+08:00",
            "priority": "normal",
            "source_type": "morning_analysis",
            "source_ref": "optional",
            "payload_json": {},
        }
    ],
    "tasks_to_update": [
        {
            "id": "task_id",
            "status": "pending",
            "title": "optional",
            "description": "optional",
            "due_at": "optional",
            "remind_at": "optional",
            "priority": "optional",
            "source_type": "optional",
            "source_ref": "optional",
            "payload_json": {},
        }
    ],
    "review_suggestions": [
        {
            "title": "string",
            "suggestion": "string",
            "source_type": "morning_analysis",
            "source_ref": "optional",
            "payload_json": {},
        }
    ],
    "workflow_items": [
        {
            "title": "string",
            "pattern_type": "string",
            "summary": "string",
            "example_json": {},
            "source_task_ids_json": [],
            "sync_status": "local_only",
        }
    ],
    "cron_jobs_to_remove": ["cron_job_id"],
    "cron_jobs_to_keep": ["cron_job_id"],
    "notes": "string",
}


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _base_prompt(title: str, context: dict[str, Any]) -> str:
    return f"""
You are Openclaw, the only AI mentor.

SalesBrain is a pure-code sidecar. It will apply your structured decisions.
Return JSON only. No markdown. No code fences. No extra commentary.

You must follow this response schema:
{_json_block(RESPONSE_SCHEMA)}

Task title: {title}

Context:
{_json_block(context)}

Rules:
- All tasks you create must be concrete, actionable, and date-specific.
- Do not invent EBOSS facts not present in the context.
- Do not directly write EBOSS. SalesBrain handles storage and execution.
- If nothing needs action, return ok=true with empty arrays.
- For cron migration, only mark business-user-task cron jobs for removal.
""".strip()


def build_morning_analysis_prompt(context: dict[str, Any]) -> str:
    return _base_prompt(
        "06:00 morning work analysis and new task generation",
        context,
    )


def build_due_task_prompt(context: dict[str, Any]) -> str:
    prompt = _base_prompt("due task review", context)
    return (
        prompt
        + "\n\nFocus:\n"
        + "- decide whether to remind, snooze, complete, or ignore each due task\n"
        + "- keep reminders short and human\n"
    )


def build_workflow_reflection_prompt(context: dict[str, Any]) -> str:
    prompt = _base_prompt("23:30 workflow reflection and cron migration", context)
    return (
        prompt
        + "\n\nFocus:\n"
        + "- inspect your own cron jobs and identify any business-user-task cron that should live in SalesBrain instead\n"
        + "- return cron_jobs_to_remove for cron jobs to migrate away from Openclaw\n"
        + "- also return workflow_items for reusable methods worth keeping\n"
    )


def build_github_update_prompt(context: dict[str, Any]) -> str:
    prompt = _base_prompt("SalesBrain GitHub update check", context)
    return (
        prompt
        + "\n\nFocus:\n"
        + "- if the remote GitHub revision is newer than the installed revision, ask the user whether to update now\n"
        + "- keep the summary as the exact user-facing question when an update is available\n"
        + "- if already up to date, say so briefly in the summary\n"
        + "- do not create tasks unless an update workflow itself needs tracking\n"
    )
