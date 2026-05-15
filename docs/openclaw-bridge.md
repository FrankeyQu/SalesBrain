# Openclaw Bridge Contract

SalesBrain does not hardcode a specific Openclaw install path.
Instead, it calls a user-provided command and expects JSON on stdout.

This reference can live in the SalesBrain GitHub repository.
The company skill package is lightweight and does not carry the SalesBrain source tree.

## First install sequence

When this skill is installed on a fresh machine, the first step is to fetch SalesBrain from GitHub before any analysis:

1. Run `python <skill_root>/scripts/install.py`
2. Run `python3 -m salesbrain init --no-first-run --sales-name "<sales name>" --eboss-api-key "<key>" --openclaw-wake-command "<bridge command>"`
3. Run `python3 -m salesbrain bridge doctor`; if it fails, fix `[openclaw].wake_command` before continuing
4. Run `python3 -m salesbrain bridge test`; Openclaw must visibly send the test message and return `{"ok": true, "message_sent": true}`
5. Run `python3 -m salesbrain first-run sync`
6. Run `python3 -m salesbrain first-run cron-inspect`, show the result, then ask whether to migrate, skip, or selectively migrate
7. Run `python3 -m salesbrain first-run cron-migrate --mode all|none|selected`
8. Run `python3 -m salesbrain first-run analyze` and send `analysis_report.formatted_report` to the user
9. Install the Linux cron watchdog and start the scheduler: `python3 -m salesbrain service install --mode auto --start`

`salesbrain daemon` also starts LAN team discovery and peer sync unless `--no-team` is passed.
On Openclaw-hosted Docker containers, do not use Openclaw business cron to keep SalesBrain alive. The `service install` command registers a Linux system cron watchdog that runs `python3 -m salesbrain service ensure-running` every minute, repairs due jobs, and starts the daemon again if the process is gone.
For the first install, GitHub is the source of code. For later update reminders, Openclaw should still compare the company SkillHub command `安装 SalesBrain` and GitHub `FrankeyQu/SalesBrain`.

## Wake command

SalesBrain sends a JSON payload on stdin and also sets:

- `SALESBRAIN_PAYLOAD_JSON`
- `SALESBRAIN_WAKE_KIND`

The command should:

1. Read the JSON payload
2. Wake the local Openclaw mentor runtime
3. Let Openclaw send any user-facing message when the wake kind requires a reminder, question, or report
4. Return JSON on stdout

The bridge command is mandatory in command mode. If `[openclaw].wake_command` or `OPENCLAW_WAKE_COMMAND` is empty, SalesBrain records scheduled wakes as failed. It no longer silently writes an outbox file and marks the job successful.

Before enabling the daemon, run:

```bash
python3 -m salesbrain bridge doctor
python3 -m salesbrain bridge test
```

`bridge test` uses wake kind `salesbrain_bridge_test`. The bridge must send a visible message to the current user and return:

```json
{"ok": true, "message_sent": true}
```

Common wake kinds:

- `initial_analysis`: first-use full analysis after EBOSS sync
- `morning_analysis`: 06:00 work-state analysis and task generation
- `work_followup`: adaptive sales follow-up push; the fixed 08:30, 13:30, and 19:30 slots are fallback anchors
- `daily_report_review`: 22:00 review of the latest daily report
- `weekly_summary`: weekly summary and next-week task planning
- `due_task_scan`: due task review
- `workflow_reflection`: local method extraction and cron migration
- `workflow_inbox_review`: review team workflow candidates before accepting them locally
- `salesbrain_update_check`: ask whether to update SalesBrain, preferring company SkillHub before GitHub
- `salesbrain_bridge_test`: visible bridge test; send the test message and return `message_sent: true`

Recommended response shape:

```json
{
  "ok": true,
  "summary": "short result",
  "user_message": "导师助理发给销售的完整消息：先分析当前工作状态，再说明现在该做什么、为什么、下一次什么时候检查。",
  "analysis_summary": "本次判断依据，引用 business_facts / work_state 中的对象 ID、金额、阶段和跟进状态。",
  "message_sent": true,
  "tasks_to_create": [],
  "tasks_to_update": [],
  "review_suggestions": [],
  "workflow_items": [],
  "workflow_inbox_decisions": [],
  "next_wake_plans": [
    {
      "kind": "work_followup",
      "due_at": "2026-05-12T14:30:00+08:00",
      "reason": "Customer promised an update before 14:00; check whether it happened.",
      "priority": "normal",
      "replace_existing": true,
      "payload_json": {}
    }
  ],
  "cron_jobs_to_remove": [],
  "cron_jobs_to_keep": []
}
```

For all user-facing wakes, `user_message` and `message_sent=true` are required. SalesBrain marks the wake as failed if Openclaw only returns tasks but does not confirm that the mentor message was sent.

SalesBrain now sends a semantic data layer in the context:

- `semantic_contract`: rules for using IDs, amounts, and confidence
- `business_facts`: standardized EBOSS objects with `object_type`, `object_id`, `name`, `amount_yuan`, `amount_display`, `amount_source`, `amount_confidence`, `stage`, and `last_follow_at`
- `work_state`: curated current-state lists such as high-value opportunities, stale objects, and amount conflicts

Use `business_facts` as the source of truth. Do not infer opportunity or project amounts from raw EBOSS payloads. If a task refers to an EBOSS object, return the exact `source_type` and `source_ref`; when a task mentions an amount, include `amount_yuan_used`.

For `salesbrain_update_check`, the `summary` should be a direct question asking the user whether to update SalesBrain. The bridge must not update code without user confirmation.
For normal sales analysis wakes, Openclaw should create or update tasks directly in JSON and should not ask the sales person for confirmation first.
For adaptive follow-up, return `next_wake_plans` whenever the current analysis suggests another check-in later. SalesBrain stores each plan as a one-shot `planned_wake` job and wakes Openclaw at that exact time; the next wake can then schedule the following one. Use fixed daily follow-up times only as fallback context.
For `workflow_reflection`, avoid normal sales reminders and return local learnings as `workflow_items`.
Workflow items may sync to the internal team table, so they must contain reusable methods rather than personal tasks, EBOSS raw records, API keys, daily report originals, or private Openclaw memory.
Set `sync_status` to `local_only` when a workflow item should stay on the current instance.
Team workflow items received from peers enter `workflow_inbox_items` first. SalesBrain checks that inbox and wakes Openclaw when new candidates need review. For `workflow_inbox_review`, Openclaw must proactively ask the user whether to adopt, merge, ignore, or mark each item duplicate. Without explicit user confirmation, return an empty `workflow_inbox_decisions` array. After the user confirms, return `accept`, `merge`, `ignore`, or `duplicate`; only `accept` and `merge` move the item into the formal local workflow table.

## Cron listing command

Return one of these on stdout:

```json
{"jobs":[...]}
```

or:

```json
[{...}]
```

Each job should include at least:

- `id`
- `name`
- `prompt`
- `schedule_display`
- `enabled`

## Cron removal command

Accept JSON with `job_id` and return:

```json
{"ok": true}
```

## Notes

- SalesBrain handles scheduling, persistence, and EBOSS sync.
- SalesBrain handles LAN team member discovery and workflow-item sync.
- Openclaw handles analysis and structured decisions.
- The bridge can be a script, a skill wrapper, or a CLI command.
