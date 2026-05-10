# SalesBrain

SalesBrain is the local scheduling and state layer for Openclaw-based sales mentoring.

When you are woken by SalesBrain:

- read the JSON payload from stdin or `SALESBRAIN_PAYLOAD_JSON`
- return JSON only
- do not write to EBOSS directly
- do not schedule yourself
- keep decisions concrete and date-specific

## Required response shape

```json
{
  "ok": true,
  "summary": "short result",
  "tasks_to_create": [],
  "tasks_to_update": [],
  "review_suggestions": [],
  "workflow_items": [],
  "cron_jobs_to_remove": [],
  "cron_jobs_to_keep": [],
  "notes": ""
}
```

## Behavior

- `morning_analysis`: review EBOSS snapshot + pending tasks, then create new follow-ups if needed
- `due_task_scan`: decide whether to remind, snooze, complete, or ignore due tasks
- `workflow_reflection`: summarize good recurring working methods and flag business cron jobs that should move into SalesBrain

## Rules

- If information is missing, keep the result conservative.
- If a cron job is a business task, return its id in `cron_jobs_to_remove`.
- Return empty arrays when nothing needs action.
