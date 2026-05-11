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
You are authorized to create and update SalesBrain tasks directly. Do not ask the sales person for confirmation before returning tasks_to_create or tasks_to_update.

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
- When you identify a useful working method, return it in workflow_items so SalesBrain can keep it locally.
- When you see a weak, vague, or stalled report, create a concrete follow-up task instead of only commenting on it.
""".strip()


def build_initial_analysis_prompt(context: dict[str, Any]) -> str:
    prompt = _base_prompt("first-run full analysis after initial EBOSS sync", context)
    return (
        prompt
        + "\n\nFocus:\n"
        + "- this is the first use after installation, so do a full baseline analysis immediately\n"
        + "- apply the 5.1 to 5.4 logic from the start: follow-up reminders, vague reports, unrealistic timelines, and weekly planning\n"
        + "- analyze the last 20 days of daily reports for concrete follow-up opportunities\n"
        + "- identify vague reports, stalled progress, and timelines that look unrealistic\n"
        + "- create tasks directly when a next action is clear\n"
    )


def build_morning_analysis_prompt(context: dict[str, Any]) -> str:
    prompt = _base_prompt("06:00 morning work analysis and new task generation", context)
    return (
        prompt
        + "\n\nFocus:\n"
        + "- apply the 5.1 to 5.4 logic: follow-up reminders, weak reports, timeline checks, and weekly planning\n"
        + "- analyze the last 20 days of daily reports for explicit next-step follow-ups and missed follow-ups\n"
        + "- identify vague reports or weak progress and create concrete follow-up tasks\n"
        + "- identify unrealistic project timelines or stage delays and create concrete follow-up tasks\n"
        + "- use the EBOSS snapshot and pending tasks to decide what to do next\n"
    )


def build_work_followup_prompt(context: dict[str, Any]) -> str:
    prompt = _base_prompt("sales follow-up and督促 session", context)
    return (
        prompt
        + "\n\nFocus:\n"
        + "- this is one of the daily follow-up wake-ups at 08:30, 13:30, or 19:30\n"
        + "- remind the sales person about concrete next actions that should be pushed now\n"
        + "- create or update tasks directly when a follow-up is needed\n"
        + "- prefer short, specific, execution-ready task titles\n"
    )


def build_daily_report_review_prompt(context: dict[str, Any]) -> str:
    prompt = _base_prompt("22:00 daily report review", context)
    return (
        prompt
        + "\n\nFocus:\n"
        + "- review the most recent daily report quality, completeness, and actionability\n"
        + "- note whether the report has clear next steps, real progress, and explicit follow-up items\n"
        + "- create tasks for missing follow-ups or stalled items\n"
        + "- give the human a concise review suggestion when the report is too vague or too optimistic\n"
    )


def build_weekly_summary_prompt(context: dict[str, Any]) -> str:
    prompt = _base_prompt("weekly sales summary and next-week planning", context)
    return (
        prompt
        + "\n\nFocus:\n"
        + "- this is the 5.4 weekly wrap-up and next-week planning pass\n"
        + "- summarize this week's progress and turn it into next week's follow-up checklist\n"
        + "- create concrete tasks for next week without asking the sales person first\n"
        + "- identify priority changes, missing follow-ups, and stalled opportunities\n"
        + "- include reusable workflow_items for any useful working method you notice\n"
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
        + "- do not create sales reminder tasks in this wake; keep it local unless a cron migration is needed\n"
        + "- inspect your own cron jobs and identify any business-user-task cron that should live in SalesBrain instead\n"
        + "- return cron_jobs_to_remove for cron jobs to migrate away from Openclaw\n"
        + "- extract reusable working methods from the day, including the patterns behind 5.1 to 5.4\n"
        + "- also extract any new product or feature directions, plus front-end and back-end coordination ideas\n"
        + "- keep these items local in SalesBrain by returning them in workflow_items\n"
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
