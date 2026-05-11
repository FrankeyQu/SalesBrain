from __future__ import annotations

from salesbrain.config import load_config, write_default_config


def test_write_and_load_config_uses_local_paths(tmp_path, monkeypatch):
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    secret_file = tmp_path / "secrets" / "eboss-api-key.txt"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text("secret-key", encoding="utf-8")

    monkeypatch.delenv("SALESBRAIN_HOME", raising=False)
    monkeypatch.delenv("SALESBRAIN_DB_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_CRON_JOBS_PATH", raising=False)
    monkeypatch.delenv("EBOSS_API_KEY", raising=False)
    monkeypatch.delenv("EBOSS_API_KEY_FILE", raising=False)

    cfg = load_config(config_path)

    assert cfg.home == tmp_path.resolve()
    assert cfg.config_path == config_path.resolve()
    assert cfg.db_path == (tmp_path / "salesbrain.sqlite").resolve()
    assert cfg.logs_dir == (tmp_path / "logs").resolve()
    assert cfg.openclaw_cron_jobs_path == (tmp_path / "cron" / "jobs.json").resolve()
    assert cfg.eboss_api_key == "secret-key"
    assert cfg.eboss_api_key_file == secret_file.resolve()
    assert cfg.github_repo == "FrankeyQu/SalesBrain"
    assert cfg.github_branch == "main"
    assert cfg.github_update_check_time == "09:00"
