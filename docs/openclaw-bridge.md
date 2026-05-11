# Openclaw Bridge Contract

SalesBrain does not hardcode a specific Openclaw install path.
Instead, it calls a user-provided command and expects JSON on stdout.

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
- `work_followup`: 08:30, 13:30, and 19:30 sales follow-up push
- `daily_report_review`: 22:00 review of the latest daily report
- `weekly_summary`: weekly summary and next-week task planning
- `due_task_scan`: due task review
- `workflow_reflection`: local method extraction and cron migration
- `github_update_check`: ask whether to update SalesBrain

Recommended response shape:

```json
{
  "ok": true,
  "summary": "short result",
  "tasks_to_create": [],
  "tasks_to_update": [],
  "review_suggestions": [],
  "workflow_items": [],
  "cron_jobs_to_remove": [],
  "cron_jobs_to_keep": []
}
```

For `github_update_check`, the `summary` should be a direct question asking the user whether to update SalesBrain. The bridge must not update code without user confirmation.
For normal sales analysis wakes, Openclaw should create or update tasks directly in JSON and should not ask the sales person for confirmation first.
For `workflow_reflection`, avoid normal sales reminders and return local learnings as `workflow_items`.

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
- Openclaw handles analysis and structured decisions.
- The bridge can be a script, a skill wrapper, or a CLI command.
