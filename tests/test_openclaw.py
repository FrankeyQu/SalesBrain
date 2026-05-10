from __future__ import annotations

import json
from dataclasses import replace

from salesbrain.config import load_config, write_default_config
from salesbrain.openclaw import OpenClawAdapter


def test_business_cron_detection_and_removal(tmp_path):
    config_path = write_default_config(tmp_path / "config.toml", sales_name="Alice")
    cron_path = tmp_path / "cron.json"
    cron_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {"id": "biz-1", "name": "日报提醒", "prompt": "提醒客户跟进", "schedule_display": "0 7 * * *", "enabled": True},
                    {"id": "sys-1", "name": "健康检查", "prompt": "系统巡检", "schedule_display": "0 22 * * *", "enabled": True},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cfg = replace(
        load_config(config_path),
        openclaw_mode="file",
        openclaw_cron_jobs_path=cron_path,
        openclaw_wake_command="",
        openclaw_cron_list_command="",
        openclaw_cron_remove_command="",
    )
    adapter = OpenClawAdapter(cfg)

    jobs = adapter.list_cron_jobs()
    assert [job["id"] for job in jobs] == ["biz-1", "sys-1"]
    assert adapter.filter_business_cron_jobs(jobs) == [jobs[0]]
    assert adapter.remove_cron_job("biz-1") is True
    remaining = json.loads(cron_path.read_text(encoding="utf-8"))
    assert remaining["jobs"] == [
        {"id": "sys-1", "name": "健康检查", "prompt": "系统巡检", "schedule_display": "0 22 * * *", "enabled": True}
    ]
