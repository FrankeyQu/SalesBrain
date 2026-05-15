from __future__ import annotations

import json
from typing import Any


RESPONSE_SCHEMA = {
    "ok": True,
    "summary": "short human-readable summary",
    "user_message": "导师助理要发给销售的完整消息，必须是可直接发送给用户的口吻",
    "analysis_summary": "本次判断依据，说明你为什么这样提醒和安排",
    "message_sent": True,
    "tasks_to_create": [
        {
            "title": "string",
            "description": "string",
            "due_at": "2026-05-12T09:30:00+08:00",
            "remind_at": "2026-05-12T09:00:00+08:00",
            "priority": "normal",
            "source_type": "morning_analysis",
            "source_ref": "optional",
            "amount_yuan_used": "optional integer; required when the task mentions an EBOSS object amount",
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
            "pattern_type": "followup|report|project|opportunity|product|collaboration|other",
            "summary": "string",
            "example_json": {
                "when_to_use": "string",
                "steps": ["string"],
                "signals": ["string"],
                "avoid": ["string"],
            },
            "source_task_ids_json": [],
            "sync_status": "local_only",
        }
    ],
    "workflow_inbox_decisions": [
        {
            "id": "workflow_inbox_item_id",
            "action": "accept|merge|ignore|duplicate",
            "merged_item": {
                "title": "optional merged title",
                "pattern_type": "optional",
                "summary": "optional",
                "example_json": {},
                "source_task_ids_json": [],
                "sync_status": "ready",
            },
        }
    ],
    "next_wake_plans": [
        {
            "kind": "work_followup|morning_analysis|daily_report_review|weekly_summary|workflow_reflection|initial_analysis",
            "due_at": "2026-05-12T14:30:00+08:00",
            "reason": "string",
            "priority": "normal",
            "replace_existing": True,
            "payload_json": {},
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
- Use semantic_contract, business_facts, and work_state as the source of truth for EBOSS object IDs, names, stages, amounts, and follow-up dates.
- Do not infer opportunity or project amounts from raw EBOSS payloads. Use business_facts.amount_yuan and business_facts.amount_display only.
- If amount_confidence is conflict or missing, say that the amount口径 is uncertain instead of presenting it as definite.
- When a task refers to an EBOSS opportunity, project, customer, lead, or EBOSS task, set source_type to that object type and source_ref to the exact object_id.
- When you mention an EBOSS amount in a task, include amount_yuan_used with the exact business_facts.amount_yuan.
- For user-facing wakes, write user_message in a mentor-assistant tone, send it to the user, and return message_sent=true only after it is actually sent.
- The user_message must analyze the current work state and tell the sales person what to do now, why it matters, and when you will check again.
- All tasks you create must be concrete, actionable, and date-specific.
- Do not invent EBOSS facts not present in the context.
- Do not directly write EBOSS. SalesBrain handles storage and execution.
- If nothing needs action, return ok=true with empty arrays.
- For cron migration, only mark business-user-task cron jobs for removal.
- When you identify a useful working method, return it in workflow_items so SalesBrain can keep it locally.
- When the current wake suggests another check-in later, return a next_wake_plans item with a concrete future due_at.
- Prefer adaptive next wake planning over fixed daily slots whenever the business state is still active.
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
        + "- if the work should be checked again soon, return a next_wake_plans item with a concrete adaptive due_at instead of relying only on fixed slots\n"
        + "- use migrated cron summary and team workflow inbox candidates when they help, but avoid duplicate workflow items\n"
        + "- after analysis, mention in summary that the sales person can adjust analysis and follow-up times later\n"
    )


def build_first_cron_migration_prompt(context: dict[str, Any]) -> str:
    prompt = _base_prompt("first-run Openclaw cron migration before baseline analysis", context)
    return (
        prompt
        + "\n\nFocus:\n"
        + "- first explain in summary why SalesBrain should migrate business cron jobs: Openclaw cron may miss runs after restarts, while SalesBrain is deterministic and records heartbeat, job status, and failures\n"
        + "- inspect openclaw_cron_jobs and business_cron_candidates\n"
        + "- respect migration_mode and selected_job_ids from the context; if migration_mode is selected, only migrate those selected jobs\n"
        + "- only return cron_jobs_to_remove for clear sales/business follow-up cron jobs\n"
        + "- never remove system health, backup, platform maintenance, GitHub update, or SalesBrain own jobs\n"
        + "- when a removed cron contains useful business intent, preserve it as tasks_to_create or workflow_items\n"
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
        + "- if the business state still needs another check-in, include next_wake_plans with a concrete adaptive due_at\n"
    )


def build_work_followup_prompt(context: dict[str, Any]) -> str:
    prompt = _base_prompt("sales follow-up and督促 session", context)
    return (
        prompt
        + "\n\nFocus:\n"
        + "- this is an adaptive sales follow-up wake, not a fixed clock-driven reminder\n"
        + "- do not only list pending tasks; first analyze the current work state like a mentor, then tell the sales person what to do now\n"
        + "- remind the sales person about concrete next actions that should be pushed now, with the reason and expected outcome\n"
        + "- create or update tasks directly when a follow-up is needed\n"
        + "- prefer short, specific, execution-ready task titles\n"
        + "- if the account still needs another touch, return a next_wake_plans item with a concrete future due_at and expected outcome in payload_json\n"
        + "- use the fixed daily anchor times only as fallback context, not as the primary schedule choice\n"
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
        + "- if the report suggests another check-in later, return next_wake_plans with a concrete due_at\n"
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
        + "- if a next check-in should happen before the default weekly cadence, return next_wake_plans with a concrete due_at\n"
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
        + "- prefer the company SkillHub command named 安装 SalesBrain when it has the newest version\n"
        + "- compare local_version, company_skillhub_version, and github_version when present\n"
        + "- if GitHub is newer but SkillHub is not, recommend publishing or waiting for the company SkillHub package first\n"
        + "- keep the summary as the exact user-facing question when an update is available\n"
        + "- if already up to date, say so briefly in the summary\n"
        + "- do not create tasks unless an update workflow itself needs tracking\n"
    )


def build_workflow_inbox_review_prompt(context: dict[str, Any]) -> str:
    prompt = _base_prompt("team workflow inbox user confirmation", context)
    return (
        prompt
        + "\n\nFocus:\n"
        + "- SalesBrain found team workflow items synced from peers and woke you proactively\n"
        + "- you must ask the user whether to adopt, merge, ignore, or mark each candidate duplicate\n"
        + "- before asking, briefly compare each candidate with existing workflow_items and explain likely duplicates\n"
        + "- if the current context does not include an explicit user confirmation, return an empty workflow_inbox_decisions array\n"
        + "- if the user has explicitly confirmed, return workflow_inbox_decisions: accept, merge, ignore, or duplicate\n"
        + "- use merge when the team item is useful but overlaps with an existing local method\n"
        + "- do not create normal sales reminder tasks in this wake\n"
        + "- make summary a concise user-facing confirmation question\n"
    )
