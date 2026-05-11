---
name: salesbrain
description: "Use SalesBrain to run Openclaw sales mentoring and bootstrap the local SalesBrain checkout from GitHub. Use when SalesBrain wakes you for morning analysis, due task follow-up, workflow reflection, EBOSS sync review, GitHub update checks, or cron migration. Return structured JSON only."
allowed-tools: Bash
metadata:
  clawdbot:
    emoji: "🧠"
    requires:
      bins: ["salesbrain", "git", "python"]
      os: ["linux", "darwin", "win32"]
    configPaths:
      - "~/.openclaw/"
    files:
      - "config.toml"
      - "salesbrain.sqlite"
---

# SalesBrain

SalesBrain is the scheduling and persistence layer for Openclaw-based sales mentoring.
Openclaw does the thinking. SalesBrain stores state, runs schedules, and applies your structured decisions.

## When to Use

Use this skill whenever SalesBrain wakes you with one of these kinds:

- `initial_analysis`
- `morning_analysis`
- `work_followup`
- `daily_report_review`
- `weekly_summary`
- `due_task_scan`
- `workflow_reflection`
- `github_update_check`
- `manual`

Use it whenever you need to:

- review EBOSS snapshots and local follow-up tasks
- decide what to remind, snooze, complete, or keep
- extract reusable working patterns
- identify business cron jobs that should move out of Openclaw
- ask the user whether to update SalesBrain when GitHub has a newer commit

## First Use Bootstrap and GitHub Sync

The first step after this skill is installed is always to sync the SalesBrain code from GitHub.
Do not analyze anything, do not initialize anything, and do not assume the local checkout exists until this sync is done.

If the repo does not exist yet, clone it from the official source:

```bash
git clone https://github.com/FrankeyQu/SalesBrain.git ~/.openclaw/SalesBrain
```

If the repo already exists, update it before anything else:

```bash
cd ~/.openclaw/SalesBrain
git pull --ff-only
```

Then install or refresh the local package:

```bash
python -m pip install -e .
```

Then initialize SalesBrain:

```bash
salesbrain init --sales-name "<sales name>"
```

During initialization, ask the user for the EBOSS API key if SalesBrain prompts for it.
This is the value used in the EBOSS `api-key` HTTP header.
`salesbrain init` immediately syncs EBOSS, backfills 30 days of daily reports, and runs one full `initial_analysis` wake-up.

After a successful install or any later update, mark the installed revision:

```bash
salesbrain github mark-installed
```

When the user later asks for an update, repeat the same sync-first sequence:

1. `cd ~/.openclaw/SalesBrain`
2. `git pull --ff-only`
3. `python -m pip install -e .`
4. `salesbrain github mark-installed`

## Required Inputs

SalesBrain sends a JSON payload on stdin and also sets:

- `SALESBRAIN_PAYLOAD_JSON`
- `SALESBRAIN_WAKE_KIND`

The payload usually contains:

- `run_id`
- `kind`
- `prompt`
- `context`
- `salesbrain_home`

If stdin is empty, read `SALESBRAIN_PAYLOAD_JSON`.

## Required Output

Return JSON only. No markdown. No code fences. No extra commentary.

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

## Core Rules

- Do not write to EBOSS directly.
- Do not schedule yourself inside Openclaw for business follow-ups.
- Do not invent facts that are not present in the payload context.
- Keep every task concrete, dated, and actionable.
- When SalesBrain asks you to create or update tasks, do it directly in the JSON response. Do not ask the sales person for confirmation first.
- If nothing needs action, return `ok: true` and empty arrays.
- For cron migration, only return business-user-task cron job ids in `cron_jobs_to_remove`.
- Never remove system health, backup, maintenance, or platform cron jobs.

## Behavior by Wake Kind

### `initial_analysis`

This runs once after first installation and first EBOSS sync.

Analyze the last 20 days of daily reports plus EBOSS project/opportunity/task data.
Apply the 5.1 to 5.4 logic:

- find promised next steps and missed follow-ups
- find vague or weak daily reports
- find unrealistic project timelines and stage delays
- create the first follow-up tasks directly

### `morning_analysis`

Review the EBOSS snapshot, pending tasks, and recent workflow context.

Return:

- new follow-up tasks that should be created
- task updates for items already in flight
- review suggestions for the human
- reusable workflow items worth keeping

Apply these preset logic passes:

- 5.1: review the last 20 days of reports for next-step commitments and missing follow-through
- 5.2: identify vague reports or hollow progress and create concrete improvement tasks
- 5.3: check stage timing against rough assumptions: 需求沟通 2-3 个月, 立项约 3 个月, 采购约 1 个月, 合同流程约 1 个月
- 5.4: when weekly context is present, turn the weekly summary into next-week tasks

### `work_followup`

This runs at least three times per day, normally 08:30, 13:30, and 19:30.

Push the sales person forward with concrete, timely actions.
Create or update SalesBrain tasks directly when a follow-up is needed.
Keep task titles short and executable.

### `daily_report_review`

This runs at 22:00.

Review the most recent daily report for quality, completeness, next actions, real progress, and vagueness.
Create missing follow-up tasks directly.
Return review suggestions when the daily report needs improvement.

### `weekly_summary`

Summarize the current week, plan next week, and create next-week follow-up tasks directly.
Return reusable methods as `workflow_items` when the week reveals a good habit, checklist, or project-push pattern.

### `due_task_scan`

Review due and overdue tasks.

Return one of these actions per task:

- keep as-is
- snooze with a new reminder time
- mark done
- keep pending if there is no clear action

Keep the response short and practical.

### `workflow_reflection`

Summarize useful ways of working that should be kept and reused.

Return:

- workflow items worth learning
- business cron jobs that should move to SalesBrain
- any tasks that should be preserved or updated before the cron is removed

Do not create normal sales reminder tasks in this wake.
Keep working methods, product/function directions, and front-end/back-end coordination ideas local in `workflow_items`.

### `github_update_check`

SalesBrain found a newer commit in the GitHub repository.

Return a direct user-facing question in `summary`, for example:

```json
{
  "ok": true,
  "summary": "SalesBrain 有新版本，是否现在更新？",
  "tasks_to_create": [],
  "tasks_to_update": [],
  "review_suggestions": [],
  "workflow_items": [],
  "cron_jobs_to_remove": [],
  "cron_jobs_to_keep": [],
  "notes": "Wait for user confirmation before updating."
}
```

Do not run `git pull` or update automatically unless the user explicitly agrees.
If the user agrees, update the local SalesBrain checkout and then run `salesbrain github mark-installed`.

## Response Shape Notes

- `tasks_to_create`: new tasks with `title`, `description`, `due_at`, optional `remind_at`, `priority`, `source_type`, `source_ref`
- `tasks_to_update`: existing task ids plus fields to update
- `review_suggestions`: short mentoring suggestions for the human
- `workflow_items`: reusable methods, habits, or process patterns
- `cron_jobs_to_remove`: ids only
- `cron_jobs_to_keep`: ids only

## Failure Handling

If the context is insufficient, return a conservative empty response rather than guessing.
If the payload is malformed, return:

```json
{
  "ok": false,
  "summary": "invalid payload",
  "notes": "missing or unreadable context"
}
```

## Mental Model

SalesBrain owns:

- schedule timing
- persistence
- task queues
- EBOSS sync
- reminder triggering

Openclaw owns:

- analysis
- judgment
- task decisions
- workflow learning

The boundary is intentional. Stay on your side of it.
