---
name: salesbrain
description: "Use SalesBrain to run Openclaw sales mentoring. Use when SalesBrain wakes you for morning analysis, due task follow-up, workflow reflection, EBOSS sync review, GitHub update checks, or cron migration. Return structured JSON only."
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

- `morning_analysis`
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

## First Use Bootstrap

When this skill is installed but the `salesbrain` command is missing, deploy SalesBrain from GitHub before continuing.

Use the official repo:

```bash
git clone https://github.com/FrankeyQu/SalesBrain.git ~/.openclaw/SalesBrain
cd ~/.openclaw/SalesBrain
python -m pip install -e .
```

If the directory already exists:

```bash
cd ~/.openclaw/SalesBrain
git pull
python -m pip install -e .
```

Then initialize it:

```bash
salesbrain init --sales-name "<sales name>"
```

During initialization, ask the user for the EBOSS API key if SalesBrain prompts for it.
This is the value used in the EBOSS `api-key` HTTP header.

After a successful install or update, mark the installed revision:

```bash
salesbrain github mark-installed
```

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
- If nothing needs action, return `ok: true` and empty arrays.
- For cron migration, only return business-user-task cron job ids in `cron_jobs_to_remove`.
- Never remove system health, backup, maintenance, or platform cron jobs.

## Behavior by Wake Kind

### `morning_analysis`

Review the EBOSS snapshot, pending tasks, and recent workflow context.

Return:

- new follow-up tasks that should be created
- task updates for items already in flight
- review suggestions for the human
- reusable workflow items worth keeping

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
