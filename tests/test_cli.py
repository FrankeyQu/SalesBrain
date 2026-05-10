from __future__ import annotations

from salesbrain.cli import main


def test_cli_init_and_status(tmp_path, capsys):
    config_path = tmp_path / "config.toml"

    assert main(["init", "--config", str(config_path), "--sales-name", "Alice"]) == 0
    capsys.readouterr()

    assert main(["--config", str(config_path), "status"]) == 0
    output = capsys.readouterr().out
    assert "Alice" in output
    assert "scheduler_jobs" in output
