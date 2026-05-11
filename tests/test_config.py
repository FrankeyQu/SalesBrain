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
    assert cfg.work_followup_times == ("08:30", "13:30", "19:30")
    assert cfg.daily_report_review_time == "22:00"
    assert cfg.weekly_summary_time == "fri 17:30"
    assert cfg.team_enabled is True
    assert cfg.team_name == "SalesBrain"
    assert cfg.team_http_port == 37611
    assert cfg.team_broadcast_port == 37610


def test_relative_config_paths_resolve_from_config_directory(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    secret_file = tmp_path / "secrets" / "eboss-api-key.txt"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text("relative-secret", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    config_path.write_text(
        """
[profile]
sales_name = "Alice"
timezone = "Asia/Shanghai"

[eboss]
base_url = "http://example.com/api"
api_key_file = "./secrets/eboss-api-key.txt"

[openclaw]
cron_jobs_path = "./cron/jobs.json"

[runtime]
home = "./salesbrain"
db_path = "./salesbrain/salesbrain.sqlite"
logs_dir = "./salesbrain/logs"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path / "elsewhere")
    monkeypatch.delenv("SALESBRAIN_HOME", raising=False)
    monkeypatch.delenv("SALESBRAIN_DB_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_CRON_JOBS_PATH", raising=False)
    monkeypatch.delenv("EBOSS_API_KEY", raising=False)
    monkeypatch.delenv("EBOSS_API_KEY_FILE", raising=False)

    cfg = load_config(config_path)

    assert cfg.home == (tmp_path / "salesbrain").resolve()
    assert cfg.db_path == (tmp_path / "salesbrain" / "salesbrain.sqlite").resolve()
    assert cfg.logs_dir == (tmp_path / "salesbrain" / "logs").resolve()
    assert cfg.openclaw_cron_jobs_path == (tmp_path / "cron" / "jobs.json").resolve()
    assert cfg.eboss_api_key_file == secret_file.resolve()
    assert cfg.eboss_api_key == "relative-secret"


def test_team_config_can_be_overridden(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[profile]
sales_name = "Alice"

[team]
enabled = false
name = "North Team"
role = "manager"
node_id = "node-a"
advertise_host = "10.0.0.8"
http_port = 39001
broadcast_port = 39000
secret = "shared"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.delenv("SALESBRAIN_TEAM_ENABLED", raising=False)
    monkeypatch.delenv("SALESBRAIN_TEAM_NAME", raising=False)

    cfg = load_config(config_path)

    assert cfg.team_enabled is False
    assert cfg.team_name == "North Team"
    assert cfg.team_role == "manager"
    assert cfg.team_node_id == "node-a"
    assert cfg.team_advertise_host == "10.0.0.8"
    assert cfg.team_http_port == 39001
    assert cfg.team_broadcast_port == 39000
    assert cfg.team_secret == "shared"
