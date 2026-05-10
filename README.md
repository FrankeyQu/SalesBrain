# SalesBrain

SalesBrain is a local-first scheduler and state layer for Openclaw-based sales mentoring.

What it does:

- syncs EBOSS data into SQLite
- tracks follow-up tasks, review suggestions, and workflow patterns
- wakes Openclaw on a fixed schedule
- applies Openclaw's structured decisions
- can inspect Openclaw cron jobs and migrate business tasks into SalesBrain

SalesBrain is intentionally not an AI model. Openclaw does the thinking.

The repo does not hardcode any specific Openclaw install path. The bridge command is configurable.

## Quick start

```bash
python -m pip install -e .
salesbrain init --sales-name "张三"
salesbrain daemon
```

Or create a local config from the included template:

```bash
cp config.example.toml config.toml
```

Then edit the Openclaw bridge commands in `config.toml`.

## Common commands

```bash
salesbrain status
salesbrain eboss sync
salesbrain wake morning
salesbrain wake due
salesbrain wake workflow
salesbrain tasks list
salesbrain tasks due
```

## Layout

- `src/salesbrain/` - CLI, SQLite store, EBOSS client, scheduler, Openclaw adapter
- `tests/` - unit tests
- `skills/salesbrain/SKILL.md` - Openclaw-facing skill contract
- `docs/openclaw-bridge.md` - bridge command contract
- `README.md` - high-level usage

## Openclaw integration

Configure the Openclaw wake/list/remove commands or a cron JSON path in
`config.toml` or environment variables.
