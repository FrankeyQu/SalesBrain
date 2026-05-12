# Openclaw Bridge Contract

SalesBrain does not hardcode a specific Openclaw install path.
Instead, it calls a user-provided command and expects JSON on stdout.

This reference can live in the SalesBrain GitHub repository.
The company skill package is lightweight and does not carry the SalesBrain source tree.

## First install sequence

When this skill is installed on a fresh machine, the first step is to fetch SalesBrain from GitHub before any analysis:

1. Run `python <skill_root>/scripts/install.py`
2. Run `salesbrain init --no-first-run --sales-name "<sales name>" --eboss-api-key "<key>"`
3. Run `salesbrain first-run sync`
4. Run `salesbrain first-run cron-inspect`, show the result, then ask whether to migrate, skip, or selectively migrate
5. Run `salesbrain first-run cron-migrate --mode all|none|selected`
6. Run `salesbrain first-run analyze` and send `analysis_report.formatted_report` to the user
7. Start the long-running scheduler: `salesbrain daemon`

`salesbrain daemon` also starts LAN team discovery and peer sync unless `--no-team` is passed.
For the first install, GitHub is the source of code. For later update reminders, Openclaw should still compare the company SkillHub command `安装 SalesBrain` and GitHub `FrankeyQu/SalesBrain`.

## Wake command

SalesBrain sends a JSON payload on stdin and also sets:

- `SALESBRAIN_PAYLOAD_JSON`
- `SALESBRAIN_WAKE_KIND`

The command should:

1. Read the JSON payload
2. Wake the local Openclaw mentor runtime
3. Return JSON on stdout

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

Recommended response shape:

```json
{
  "ok": true,
  "summary": "short result",
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
