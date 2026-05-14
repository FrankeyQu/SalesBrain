from __future__ import annotations

from types import SimpleNamespace

from salesbrain.github import GitHubCommitInfo
from salesbrain.cli import main


def _fake_commit() -> GitHubCommitInfo:
    return GitHubCommitInfo(
        repo="FrankeyQu/SalesBrain",
        branch="main",
        sha="abc123",
        html_url="https://github.com/FrankeyQu/SalesBrain/commit/abc123",
        commit_url="https://api.github.com/repos/FrankeyQu/SalesBrain/commits/abc123",
        message="init",
        author="FrankeyQu",
    )


def test_cli_init_and_status(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("salesbrain.cli.sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("salesbrain.cli.getpass.getpass", lambda prompt: "secret-key")
    monkeypatch.setattr(
        "salesbrain.cli.SalesBrainService.first_run",
        lambda self, *args, **kwargs: {"ok": True, "sync_result": {"ok": True}, "analysis_result": {"ok": True}},
    )
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())

    assert main(["init", "--config", str(config_path), "--sales-name", "Alice"]) == 0
    capsys.readouterr()

    assert main(["--config", str(config_path), "status"]) == 0
    output = capsys.readouterr().out
    assert "Alice" in output
    assert "scheduler_jobs" in output
    assert "monitor_state" in output


def test_cli_init_prompts_for_eboss_key(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("salesbrain.cli.sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("salesbrain.cli.getpass.getpass", lambda prompt: "prompted-key")
    monkeypatch.setattr(
        "salesbrain.cli.SalesBrainService.first_run",
        lambda self, *args, **kwargs: {"ok": True, "sync_result": {"ok": True}, "analysis_result": {"ok": True}},
    )
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())

    assert main(["init", "--config", str(config_path), "--sales-name", "Alice"]) == 0
    capsys.readouterr()
    assert (config_path.parent / "secrets" / "eboss-api-key.txt").read_text(encoding="utf-8") == "prompted-key"


def test_cli_init_no_first_run_bootstraps_only(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("salesbrain.cli.sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("salesbrain.cli.getpass.getpass", lambda prompt: "prompted-key")
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    monkeypatch.setattr(
        "salesbrain.cli.SalesBrainService.first_run",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(AssertionError("first_run should not be called")),
    )

    assert main(["init", "--config", str(config_path), "--sales-name", "Alice", "--no-first-run"]) == 0
    output = capsys.readouterr().out
    assert "first_run_skipped" in output
    assert "scheduler_jobs" in output


def test_cli_first_run_commands_are_available(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    monkeypatch.setattr(
        "salesbrain.cli.SalesBrainService.inspect_openclaw_cron",
        lambda self: {"ok": True, "business_candidate_count": 0, "business_cron_candidates": []},
    )
    monkeypatch.setattr(
        "salesbrain.cli.SalesBrainService.complete_first_cron_migration",
        lambda self, *args, **kwargs: {"ok": True, "skipped": True},
    )
    monkeypatch.setattr(
        "salesbrain.cli.SalesBrainService.complete_initial_analysis",
        lambda self, *args, **kwargs: {"ok": True, "analysis_report": {"formatted_report": "首次分析报告"}},
    )

    assert main(["--config", str(config_path), "first-run", "cron-inspect"]) == 0
    assert "business_candidate_count" in capsys.readouterr().out
    assert main(["--config", str(config_path), "first-run", "cron-migrate", "--mode", "none"]) == 0
    assert "skipped" in capsys.readouterr().out
    assert main(["--config", str(config_path), "first-run", "analyze"]) == 0
    assert "首次分析报告" in capsys.readouterr().out


def test_cli_monitor_command(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("salesbrain.cli.sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("salesbrain.cli.getpass.getpass", lambda prompt: "prompted-key")
    monkeypatch.setattr(
        "salesbrain.cli.SalesBrainService.first_run",
        lambda self, *args, **kwargs: {"ok": True, "sync_result": {"ok": True}, "analysis_result": {"ok": True}},
    )
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())
    monkeypatch.setattr(
        "salesbrain.cli.SalesBrainService.monitor_scheduler",
        lambda self, *args, **kwargs: {"ok": True, "health_level": "healthy", "job_health": []},
    )

    assert main(["init", "--config", str(config_path), "--sales-name", "Alice"]) == 0
    capsys.readouterr()

    assert main(["--config", str(config_path), "monitor", "--report-only"]) == 0
    output = capsys.readouterr().out
    assert "health_level" in output


def test_cli_service_install_dry_run_uses_linux_cron_watchdog(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())

    assert main(["--config", str(config_path), "service", "install", "--dry-run", "--no-start"]) == 0

    output = capsys.readouterr().out
    assert "salesbrain-watchdog.sh" in output
    assert "service ensure-running" in output
    assert '"mode": "cron"' in output
    assert "crontab" in output


def test_cli_service_scripts_can_be_rendered_without_writing(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())

    assert main(["--config", str(config_path), "service", "scripts", "--no-team"]) == 0

    output = capsys.readouterr().out
    assert "launcher_content" in output
    assert "python" in output
    assert "salesbrain" in output
    assert "daemon" in output
    assert "--no-team" in output


def test_cli_service_status_reports_environment(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())

    assert main(["--config", str(config_path), "service", "status"]) == 0

    output = capsys.readouterr().out
    assert "running" in output
    assert "heartbeat" in output
    assert "environment" in output


def test_cli_returns_nonzero_when_handler_reports_not_ok(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(
        "salesbrain.cli.SalesBrainService.sync_eboss",
        lambda self, *args, **kwargs: {"ok": False, "status": "failed", "error": "boom"},
    )
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())

    assert main(["--config", str(config_path), "eboss", "sync"]) == 1
    output = capsys.readouterr().out
    assert '"ok": false' in output


def test_cli_team_status(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())

    assert main(["--config", str(config_path), "team", "status"]) == 0
    output = capsys.readouterr().out
    assert "team_name" in output
    assert "node_id" in output
