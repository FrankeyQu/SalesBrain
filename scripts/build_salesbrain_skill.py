from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "salesbrain"
DIST = ROOT / "dist"
STAGING = DIST / "salesbrain-package"
PACKAGE_ROOT = STAGING / "salesbrain"


def _git_sha() -> str:
    try:
        sha = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL)
            .strip()
        )
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        return f"{sha}-dirty" if dirty else sha
    except Exception:
        return "unknown"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.egg-info", ".git", "dist", ".venv"),
    )


def _write_install_script(target: Path) -> None:
    script = textwrap.dedent(
        """\
        from __future__ import annotations

        import argparse
        import datetime
        import json
        import shutil
        import subprocess
        import sys
        from pathlib import Path


        INSTALL_STEPS = [
            {
                "id": 1,
                "name": "检查本地 SalesBrain 程序和配置",
                "command": "python <skill_root>/scripts/install.py --install-code",
                "progress": "⏳ 检查本地程序和配置...",
                "success": "✅ 完成",
                "status": "required",
            },
            {
                "id": 2,
                "name": "读取 EBOSS API Key 并创建本地配置",
                "command": "python3 -m salesbrain init --no-first-run --sales-name <销售姓名> --eboss-api-key <EBOSS_API_KEY>",
                "progress": "⏳ 读取 EBOSS API Key...",
                "success": "✅ 完成（已配置）",
                "status": "requires_user_input",
            },
            {
                "id": 3,
                "name": "首次全量同步 EBOSS 本年度项目、商机、日报和关联数据",
                "command": "python3 -m salesbrain first-run sync",
                "progress": "⏳ 首次全量同步 EBOSS 本年度数据...",
                "success": "✅ 完成",
                "status": "required",
            },
            {
                "id": 4,
                "name": "检查 Openclaw 业务定时任务并迁移到 SalesBrain",
                "inspect_command": "python3 -m salesbrain first-run cron-inspect",
                "migrate_all_command": "python3 -m salesbrain first-run cron-migrate --mode all",
                "skip_command": "python3 -m salesbrain first-run cron-migrate --mode none",
                "migrate_selected_command": "python3 -m salesbrain first-run cron-migrate --mode selected --job-id <job_id>",
                "progress": "⏳ 检查 Openclaw 定时任务...",
                "choices": ["迁移", "不迁移", "选择性迁移"],
                "status": "requires_user_confirmation",
            },
            {
                "id": 5,
                "name": "唤醒 Openclaw 做首次整体工作分析",
                "command": "python3 -m salesbrain first-run analyze",
                "progress": "⏳ 唤醒 Openclaw 做首次分析...",
                "sub_progress": "⏳ 正在分析你的商机、客户、线索...",
                "success": "✅ 完成",
                "status": "required",
            },
            {
                "id": 6,
                "name": "安装 SalesBrain Linux cron watchdog 并启动长期调度",
                "command": "python3 -m salesbrain service install --mode auto --start",
                "progress": "⏳ 安装 SalesBrain 保活 watchdog 并启动长期调度...",
                "success": "✅ 完成",
                "status": "run_after_init",
            },
        ]


        def get_install_steps() -> dict:
            return {
                "ok": True,
                "steps": INSTALL_STEPS,
                "total": len(INSTALL_STEPS),
                "notes": [
                    "首次安装代码来自 GitHub，不从 skill zip 携带源码。",
                    "salesbrain init --no-first-run 只创建配置和本地表，不会静默执行迁移和分析。",
                    "步骤 4 必须把 cron-inspect 结果展示给用户，并让用户选择迁移、不迁移或选择性迁移。",
                    "步骤 5 必须单独执行 first-run analyze，并把 analysis_report.formatted_report 发给用户。",
                    "步骤 6 使用 Linux cron watchdog 保活 SalesBrain daemon，不使用 Openclaw cron。",
                    "业务定时不要再交给 Openclaw cron，后续由 SalesBrain daemon 调度。",
                ],
            }


        def run(cmd: list[str], *, cwd: Path | None = None) -> str:
            proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=False)
            if proc.returncode != 0:
                detail = proc.stderr.strip() or proc.stdout.strip()
                raise RuntimeError(f"command_failed: {' '.join(cmd)}: {detail}")
            return proc.stdout.strip()


        def backup_existing(path: Path) -> Path:
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
            target = path.with_name(f"{path.name}.backup-{stamp}")
            shutil.move(str(path), str(target))
            return target


        def install_code() -> dict:
            skill_root = Path(__file__).resolve().parents[1]
            manifest = json.loads((skill_root / "manifest.json").read_text(encoding="utf-8"))
            repo_url = str(manifest.get("github_repo") or "https://github.com/FrankeyQu/SalesBrain.git")
            branch = str(manifest.get("github_branch") or "main")
            local_root = Path.home() / ".openclaw" / "SalesBrain"
            local_root.parent.mkdir(parents=True, exist_ok=True)

            backup_path = None
            if local_root.exists() and not (local_root / ".git").exists():
                backup_path = backup_existing(local_root)

            if (local_root / ".git").exists():
                run(["git", "fetch", "origin", branch], cwd=local_root)
                run(["git", "checkout", branch], cwd=local_root)
                run(["git", "pull", "--ff-only", "origin", branch], cwd=local_root)
            else:
                run(["git", "clone", "--branch", branch, "--single-branch", repo_url, str(local_root)])

            subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(local_root)], check=True)
            revision = run(["git", "rev-parse", "HEAD"], cwd=local_root)
            meta = {
                "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "source": "github",
                "repo": repo_url,
                "branch": branch,
                "revision": revision,
                "skill_version": manifest.get("version", "unknown"),
                "backup_path": str(backup_path) if backup_path else None,
            }
            (local_root / ".salesbrain-source.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            runtime_home = Path.home() / ".openclaw" / "workspace" / "salesbrain"
            runtime_home.mkdir(parents=True, exist_ok=True)
            (runtime_home / ".salesbrain-source.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"ok": True, **meta}


        def check_install() -> dict:
            local_root = Path.home() / ".openclaw" / "SalesBrain"
            source_meta = local_root / ".salesbrain-source.json"
            installed = local_root.exists() and (local_root / ".git").exists()
            revision = None
            if installed:
                try:
                    revision = run(["git", "rev-parse", "HEAD"], cwd=local_root)
                except Exception:
                    revision = None
            return {
                "ok": True,
                "installed": installed,
                "local_root": str(local_root),
                "revision": revision,
                "source_meta_exists": source_meta.exists(),
            }


        def run_install() -> dict:
            return install_code()


        def main() -> int:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            parser = argparse.ArgumentParser(description="Install SalesBrain from GitHub for Openclaw.")
            parser.add_argument("--steps", action="store_true", help="Print install steps without executing.")
            parser.add_argument("--check", action="store_true", help="Check current local installation.")
            parser.add_argument("--install-code", action="store_true", help="Clone/pull GitHub repo and install package.")
            args = parser.parse_args()
            try:
                if args.steps:
                    result = get_install_steps()
                elif args.check:
                    result = check_install()
                else:
                    result = run_install()
            except Exception as exc:
                result = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 1


        if __name__ == "__main__":
            raise SystemExit(main())
        """
    )
    _write_text(target, script)


def build() -> Path:
    if STAGING.exists():
        shutil.rmtree(STAGING)
    PACKAGE_ROOT.mkdir(parents=True, exist_ok=True)

    for filename in ("SKILL.md", "README.md", "manifest.json", "_meta.json"):
        shutil.copy2(SKILL_ROOT / filename, PACKAGE_ROOT / filename)

    _write_install_script(PACKAGE_ROOT / "scripts" / "install.py")

    manifest_path = PACKAGE_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "bundle_revision": _git_sha(),
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_path = PACKAGE_ROOT / "_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update({"version": manifest.get("version", meta.get("version", "0.2.0"))})
    meta["built_at"] = manifest["built_at"]
    meta["bundle_revision"] = manifest["bundle_revision"]
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    DIST.mkdir(parents=True, exist_ok=True)
    zip_path = DIST / "salesbrain.zip"
    skill_path = DIST / "salesbrain.skill"
    for target in (zip_path, skill_path):
        if target.exists():
            target.unlink()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in PACKAGE_ROOT.rglob("*"):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(STAGING))
    return zip_path


if __name__ == "__main__":
    path = build()
    print(path)
