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
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())

    assert main(["init", "--config", str(config_path), "--sales-name", "Alice"]) == 0
    capsys.readouterr()

    assert main(["--config", str(config_path), "status"]) == 0
    output = capsys.readouterr().out
    assert "Alice" in output
    assert "scheduler_jobs" in output


def test_cli_init_prompts_for_eboss_key(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("salesbrain.cli.sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("salesbrain.cli.getpass.getpass", lambda prompt: "prompted-key")
    monkeypatch.setattr("salesbrain.service.fetch_remote_commit", lambda repo, branch, timeout=30: _fake_commit())

    assert main(["init", "--config", str(config_path), "--sales-name", "Alice"]) == 0
    capsys.readouterr()
    assert (config_path.parent / "secrets" / "eboss-api-key.txt").read_text(encoding="utf-8") == "prompted-key"
