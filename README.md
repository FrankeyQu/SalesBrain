# SalesBrain

SalesBrain is a local-first scheduler and state layer for Openclaw-based sales mentoring.

What it does:

- syncs EBOSS data into SQLite
- backfills the last 30 days of EBOSS daily reports on first successful sync
- runs a first-use EBOSS sync and full Openclaw analysis during `salesbrain init`
- tracks follow-up tasks, review suggestions, and workflow patterns
- wakes Openclaw on a fixed schedule: 02:00 sync, 06:00 analysis, 08:30/13:30/19:30 follow-up, 22:00 report review, 23:30 workflow reflection, and weekly summary
- can run a one-shot health monitor that repairs due jobs and records scheduler health
- applies Openclaw's structured decisions
- can inspect Openclaw cron jobs and migrate business tasks into SalesBrain
- checks GitHub daily and wakes Openclaw to ask before updating SalesBrain
- forms a LAN team network through broadcast discovery and peer-to-peer incremental sync

SalesBrain is intentionally not an AI model. Openclaw does the thinking.
Openclaw decides which tasks to create and SalesBrain applies those structured decisions directly.

The repo does not hardcode any specific Openclaw install path. The bridge command is configurable.

## Quick start

```bash
python -m pip install -e .
salesbrain init --sales-name "张三"
salesbrain daemon
```

`salesbrain init` prompts for the EBOSS `api-key` value if it is not supplied by `--eboss-api-key` or `EBOSS_API_KEY`.
After writing the config it immediately syncs EBOSS, backfills 30 days of daily reports, and wakes Openclaw once for a full baseline analysis.

Or create a local config from the included template:

```bash
cp config.example.toml config.toml
```

Then edit the Openclaw bridge commands in `config.toml`.

## Common commands

```bash
salesbrain status
salesbrain eboss sync
salesbrain wake initial
salesbrain wake morning
salesbrain wake followup
salesbrain wake review
salesbrain wake weekly
salesbrain wake due
salesbrain wake workflow
salesbrain monitor
salesbrain github check
salesbrain github mark-installed
salesbrain tasks list
salesbrain tasks due
salesbrain team status
salesbrain team peers
salesbrain team announce
salesbrain team sync
```

On Linux, the most stable pattern is to run `salesbrain monitor` on a fixed interval through your process manager or timer, and use `--strict` if you want unhealthy runs to return a non-zero exit code.
`salesbrain daemon` starts both the local scheduler and the LAN team node by default. Use `salesbrain daemon --no-team` only when another process is already running `salesbrain team serve` on the same instance.

## LAN team sync

SalesBrain V1 does not require a dedicated team server. Each installed instance keeps its own SQLite database and joins the internal network with UDP broadcast discovery plus HTTP peer sync.

Team-shared tables:

- `team_members`: real name, node id, endpoint, role, status, last seen time, version, and update time
- `workflow_sync_items`: reusable methods, playbooks, review conclusions, product notes, and cross-team collaboration experience

Only workflow items with `sync_status` of `ready` or `synced` are published to peers. Use `local_only` or `ignored` for notes that should stay on one instance.

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
secret = ""
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
