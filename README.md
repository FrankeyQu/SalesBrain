# SalesBrain

SalesBrain is a local-first scheduler and state layer for Openclaw-based sales mentoring.

What it does:

- syncs EBOSS data into SQLite
- limits EBOSS full sync data to the current calendar year, including first sync and later full syncs
- backfills the last 30 days of EBOSS daily reports on first successful sync, capped to the current calendar year
- builds a semantic business fact layer so Openclaw uses standardized IDs, names, amounts, stages, and follow-up dates instead of guessing from raw EBOSS JSON
- runs a first-use EBOSS sync and full Openclaw analysis during `salesbrain init`
- tracks follow-up tasks, review suggestions, and workflow patterns
- wakes Openclaw on deterministic schedules and lets each analysis return adaptive `next_wake_plans` for the next sales follow-up
- requires user-facing wakes to include a mentor-style `user_message` and `message_sent=true`; otherwise the wake is recorded as failed
- can run a one-shot health monitor that repairs due jobs and records scheduler health
- can install a Linux cron watchdog that keeps the SalesBrain daemon running in Openclaw Docker containers
- applies Openclaw's structured decisions
- can inspect Openclaw cron jobs and migrate business tasks into SalesBrain
- checks company SkillHub first and GitHub second, then wakes Openclaw to ask before updating SalesBrain
- forms a LAN team network through seed, peer, broadcast, and scan discovery with peer-to-peer incremental sync

SalesBrain is intentionally not an AI model. Openclaw does the thinking.
Openclaw decides which tasks to create and SalesBrain applies those structured decisions directly.

The repo does not hardcode any specific Openclaw install path. The bridge command is configurable.

## Quick start

```bash
python -m pip install -e .
python3 -m salesbrain init --sales-name "张三" --openclaw-wake-command "<openclaw bridge wake command>"
python3 -m salesbrain bridge doctor
python3 -m salesbrain bridge test
python3 -m salesbrain service install --mode auto --start
```

`salesbrain init` prompts for the EBOSS `api-key` value if it is not supplied by `--eboss-api-key` or `EBOSS_API_KEY`.
After writing the config it immediately runs the first-use flow: EBOSS full sync, Openclaw business cron migration, and then a full baseline analysis.
The Openclaw wake bridge is mandatory for scheduled work. If `[openclaw].wake_command` is empty, SalesBrain now records the wake as failed instead of writing an outbox file and pretending success.
Run `salesbrain bridge test` before enabling the daemon; it wakes Openclaw and requires the bridge to confirm that a visible user message was sent.

For company SkillHub distribution, build `dist/salesbrain.zip` and upload it. The zip is lightweight and does not include the SalesBrain source tree. Openclaw should first run `scripts/install.py` from the installed skill package, which clones or pulls `https://github.com/FrankeyQu/SalesBrain.git` into `~/.openclaw/SalesBrain` and installs it locally. `scripts/install.py --steps` returns the install plan for Openclaw progress display.

For a visible first setup, Openclaw should use `python3 -m salesbrain init --no-first-run`, then `python3 -m salesbrain bridge doctor`, `python3 -m salesbrain bridge test`, `python3 -m salesbrain first-run sync`, `python3 -m salesbrain first-run cron-inspect`, `python3 -m salesbrain first-run cron-migrate`, and `python3 -m salesbrain first-run analyze`. This keeps bridge verification, cron migration, and the initial analysis report visible to the user.

## Adaptive follow-up

SalesBrain no longer depends on only the three fixed follow-up slots. After each useful wake, Openclaw can return `next_wake_plans` with a concrete `due_at`, and SalesBrain stores that as a one-shot `planned_wake` scheduler job. When it fires, SalesBrain wakes Openclaw again and the new analysis can schedule the next one.

The 08:30, 13:30, and 19:30 jobs remain fallback anchors. The primary follow-up rhythm is the adaptive chain created from the latest EBOSS state, daily reports, pending tasks, and Openclaw's own analysis.

Every user-facing wake is a mentor review, not a plain task push. Openclaw should send `user_message` in a mentor-assistant tone: current-state analysis, what to do now, why it matters, and when SalesBrain will check again. SalesBrain treats missing `user_message` or missing `message_sent=true` as a failed wake.

## Business facts

SalesBrain derives `business_facts` from EBOSS raw records before waking Openclaw. These facts standardize:

- object identity: `object_type` + `object_id`
- object name
- amount: `amount_yuan`, `amount_display`, `amount_source`, `amount_confidence`
- stage and stage source
- last follow-up time and source

Openclaw should use `business_facts` and `work_state` as the source of truth. Raw EBOSS payloads remain available as fallback context only. If Openclaw creates a task tied to an EBOSS object, SalesBrain validates `source_type`, `source_ref`, and `amount_yuan_used` against the fact layer before saving it.

Or create a local config from the included template:

```bash
cp config.example.toml config.toml
```

Then edit the Openclaw bridge commands in `config.toml`.

## Common commands

```bash
salesbrain status
salesbrain eboss sync
salesbrain eboss repair-summaries
salesbrain wake initial
salesbrain wake morning
salesbrain wake followup
salesbrain wake review
salesbrain wake weekly
salesbrain wake due
salesbrain wake workflow
salesbrain bridge doctor
salesbrain bridge test
salesbrain monitor
salesbrain service status
salesbrain service install --mode auto --start
salesbrain service ensure-running
salesbrain github check
salesbrain github mark-installed
salesbrain tasks list
salesbrain tasks due
salesbrain team status
salesbrain team peers
salesbrain team announce
salesbrain team sync
```

On Openclaw Docker containers, the stable pattern is `python3 -m salesbrain service install --mode auto --start`. It writes launcher/watchdog scripts under the SalesBrain home directory, registers a Linux cron watchdog that runs `python3 -m salesbrain service ensure-running` every minute, and starts the daemon as a detached process. This is Linux system cron, not Openclaw business cron.
`salesbrain daemon` starts both the local scheduler and the LAN team node by default. Use `salesbrain daemon --no-team` only when another process is already running `salesbrain team serve` on the same instance.

## LAN team sync

SalesBrain V1 does not require a dedicated team server. Each installed instance keeps its own SQLite database and joins the internal network by trying seed nodes first, then known peers, then UDP broadcast, then a low-frequency scan fallback.

Team-shared tables:

- `team_members`: real name, node id, endpoint, role, status, last seen time, version, and update time
- `workflow_sync_items`: accepted reusable methods, playbooks, review conclusions, product notes, and cross-team collaboration experience
- `workflow_inbox_items`: team methods waiting for local dedupe and Openclaw confirmation

Only workflow items with `sync_status` of `ready` or `synced` are published to peers. Incoming workflow items always enter `workflow_inbox_items` first. SalesBrain checks this inbox every 5 minutes; when it finds new team methods, it wakes Openclaw to proactively ask the user whether to adopt, merge, ignore, or mark them duplicate. Only user-confirmed `accept` or `merge` decisions from Openclaw move them into the formal workflow table. Use `local_only` or `ignored` for notes that should stay on one instance.

Always local-only:

- personal follow-up tasks and reminders
- EBOSS raw records and API keys
- daily report original content
- Openclaw private conversation memory

Recommended team config lives in `[team]`:

```toml
[team]
enabled = true
name = "SalesBrain"
role = "sales"
http_port = 37611
broadcast_port = 37610
broadcast_interval_seconds = 30
sync_interval_seconds = 60
secret = "salesbrain-team-v1"
seed_endpoints = ["http://10.50.3.37:37611"]
scan_cidrs = ["10.50.0.0/16"]
scan_enabled = true
```

If the internal network needs basic trust protection, set the same `secret` on every team member. GitHub remains the cold-start and backup source for code; it is not a central runtime server.

## Layout

- `src/salesbrain/` - CLI, SQLite store, EBOSS client, scheduler, Openclaw adapter
- `tests/` - unit tests
- `skills/salesbrain/SKILL.md` - Openclaw-facing skill contract
- `docs/openclaw-bridge.md` - bridge command contract
- `README.md` - high-level usage

## Openclaw integration

Configure the Openclaw wake/list/remove commands or a cron JSON path in
`config.toml` or environment variables.
