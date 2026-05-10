from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sales_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  sales_name TEXT NOT NULL,
  eboss_user_id TEXT,
  eboss_real_name TEXT,
  timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eboss_sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_type TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial_failed', 'failed')),
  error TEXT,
  counts_json TEXT NOT NULL DEFAULT '{}',
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS eboss_raw_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sync_run_id INTEGER NOT NULL,
  api_id TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT,
  object_name TEXT,
  payload_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  FOREIGN KEY(sync_run_id) REFERENCES eboss_sync_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_eboss_raw_records_api_type
  ON eboss_raw_records(api_id, object_type);
CREATE INDEX IF NOT EXISTS idx_eboss_raw_records_object
  ON eboss_raw_records(object_type, object_id);

CREATE TABLE IF NOT EXISTS followup_tasks (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'snoozed', 'done', 'cancelled')),
  priority TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
  due_at TEXT NOT NULL,
  remind_at TEXT,
  source_type TEXT,
  source_ref TEXT,
  created_by TEXT NOT NULL DEFAULT 'openclaw',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_wake_at TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_followup_tasks_due
  ON followup_tasks(status, due_at);
CREATE INDEX IF NOT EXISTS idx_followup_tasks_remind
  ON followup_tasks(status, remind_at);

CREATE TABLE IF NOT EXISTS review_suggestions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  suggestion TEXT NOT NULL,
  source_type TEXT,
  source_ref TEXT,
  status TEXT NOT NULL CHECK (status IN ('open', 'accepted', 'dismissed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS workflow_sync_items (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  pattern_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  example_json TEXT NOT NULL DEFAULT '{}',
  source_task_ids_json TEXT NOT NULL DEFAULT '[]',
  sync_status TEXT NOT NULL CHECK (sync_status IN ('local_only', 'ready', 'synced', 'ignored')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wake_runs (
  id TEXT PRIMARY KEY,
  wake_type TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running', 'success', 'skipped', 'failed')),
  input_summary TEXT,
  result_summary TEXT,
  error TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS scheduler_jobs (
  job_name TEXT PRIMARY KEY,
  handler_name TEXT NOT NULL,
  schedule_kind TEXT NOT NULL,
  schedule_value TEXT NOT NULL,
  last_run_at TEXT,
  next_run_at TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
"""


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


class SalesBrainStore:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def init_schema(self) -> None:
        with self.transaction():
            self.conn.executescript(SCHEMA_SQL)

    def ensure_profile(self, *, sales_name: str, timezone: str, now_iso: str) -> dict[str, Any]:
        with self.transaction():
            row = self.conn.execute("SELECT * FROM sales_profile WHERE id = 1").fetchone()
            if row is None:
                self.conn.execute(
                    """
                    INSERT INTO sales_profile (id, sales_name, timezone, created_at, updated_at)
                    VALUES (1, ?, ?, ?, ?)
                    """,
                    (sales_name, timezone, now_iso, now_iso),
                )
            else:
                self.conn.execute(
                    """
                    UPDATE sales_profile
                    SET sales_name = ?, timezone = ?, updated_at = ?
                    WHERE id = 1
                    """,
                    (sales_name, timezone, now_iso),
                )
        return self.get_profile() or {}

    def update_profile_eboss(self, *, eboss_user_id: str, eboss_real_name: str, now_iso: str) -> None:
        with self.transaction():
            self.conn.execute(
                """
                UPDATE sales_profile
                SET eboss_user_id = ?, eboss_real_name = ?, updated_at = ?
                WHERE id = 1
                """,
                (eboss_user_id, eboss_real_name, now_iso),
            )

    def get_profile(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM sales_profile WHERE id = 1").fetchone()
        return row_to_dict(row)

    def insert_sync_run(
        self,
        *,
        run_type: str,
        started_at: str,
        status: str = "running",
        error: str | None = None,
        counts_json: dict[str, Any] | None = None,
        payload_json: dict[str, Any] | None = None,
    ) -> int:
        with self.transaction():
            cur = self.conn.execute(
                """
                INSERT INTO eboss_sync_runs (run_type, started_at, status, error, counts_json, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_type,
                    started_at,
                    status,
                    error,
                    json.dumps(counts_json or {}, ensure_ascii=False),
                    json.dumps(payload_json or {}, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def update_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        finished_at: str,
        error: str | None = None,
        counts_json: dict[str, Any] | None = None,
        payload_json: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction():
            self.conn.execute(
                """
                UPDATE eboss_sync_runs
                SET status = ?, finished_at = ?, error = ?, counts_json = ?, payload_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    finished_at,
                    error,
                    json.dumps(counts_json or {}, ensure_ascii=False),
                    json.dumps(payload_json or {}, ensure_ascii=False),
                    run_id,
                ),
            )

    def latest_sync_run(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM eboss_sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row_to_dict(row)

    def list_sync_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM eboss_sync_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return rows_to_dicts(rows)

    def insert_raw_record(
        self,
        *,
        sync_run_id: int,
        api_id: str,
        object_type: str,
        object_id: str | None,
        object_name: str | None,
        payload: dict[str, Any],
        fetched_at: str,
    ) -> None:
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO eboss_raw_records
                  (sync_run_id, api_id, object_type, object_id, object_name, payload_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sync_run_id,
                    api_id,
                    object_type,
                    object_id,
                    object_name,
                    json.dumps(payload, ensure_ascii=False),
                    fetched_at,
                ),
            )

    def insert_raw_records(
        self,
        *,
        sync_run_id: int,
        api_id: str,
        object_type: str,
        records: list[dict[str, Any]],
        fetched_at: str,
    ) -> int:
        inserted = 0
        with self.transaction():
            for record in records:
                object_id = (
                    record.get("id")
                    or record.get("projectId")
                    or record.get("optId")
                    or record.get("opportunityId")
                    or record.get("custId")
                    or record.get("customerId")
                    or record.get("leadId")
                    or record.get("taskId")
                )
                object_name = (
                    record.get("name")
                    or record.get("projectName")
                    or record.get("optName")
                    or record.get("opportunityName")
                    or record.get("custName")
                    or record.get("customerName")
                    or record.get("leadName")
                    or record.get("taskName")
                    or record.get("title")
                )
                self.conn.execute(
                    """
                    INSERT INTO eboss_raw_records
                      (sync_run_id, api_id, object_type, object_id, object_name, payload_json, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sync_run_id,
                        api_id,
                        object_type,
                        None if object_id is None else str(object_id),
                        None if object_name is None else str(object_name),
                        json.dumps(record, ensure_ascii=False),
                        fetched_at,
                    ),
                )
                inserted += 1
        return inserted

    def latest_raw_records(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM eboss_raw_records ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return rows_to_dicts(rows)

    def search_raw_records(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        like = f"%{keyword}%"
        rows = self.conn.execute(
            """
            SELECT *
            FROM eboss_raw_records
            WHERE object_name LIKE ? OR payload_json LIKE ? OR api_id LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (like, like, like, limit),
        ).fetchall()
        return rows_to_dicts(rows)

    def create_task(self, task: dict[str, Any]) -> dict[str, Any]:
        task_id = str(task.get("id") or uuid.uuid4().hex)
        now_iso = str(task["now_iso"])
        row = {
            "id": task_id,
            "title": str(task["title"]).strip(),
            "description": task.get("description"),
            "status": str(task.get("status", "pending")),
            "priority": str(task.get("priority", "normal")),
            "due_at": str(task["due_at"]),
            "remind_at": task.get("remind_at"),
            "source_type": task.get("source_type"),
            "source_ref": task.get("source_ref"),
            "created_by": str(task.get("created_by", "openclaw")),
            "created_at": now_iso,
            "updated_at": now_iso,
            "last_wake_at": task.get("last_wake_at"),
            "payload_json": json.dumps(task.get("payload_json") or {}, ensure_ascii=False),
        }
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO followup_tasks
                  (id, title, description, status, priority, due_at, remind_at,
                   source_type, source_ref, created_by, created_at, updated_at, last_wake_at, payload_json)
                VALUES
                  (:id, :title, :description, :status, :priority, :due_at, :remind_at,
                   :source_type, :source_ref, :created_by, :created_at, :updated_at, :last_wake_at, :payload_json)
                """,
                row,
            )
        return self.get_task(task_id) or row

    def update_task(self, task_id: str, updates: dict[str, Any], *, now_iso: str) -> dict[str, Any] | None:
        allowed = {
            "title",
            "description",
            "status",
            "priority",
            "due_at",
            "remind_at",
            "source_type",
            "source_ref",
            "created_by",
            "last_wake_at",
            "payload_json",
        }
        fields = {k: updates[k] for k in updates.keys() & allowed}
        if not fields:
            return self.get_task(task_id)
        fields["updated_at"] = now_iso
        if "payload_json" in fields:
            fields["payload_json"] = json.dumps(updates["payload_json"] or {}, ensure_ascii=False)
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        fields["id"] = task_id
        with self.transaction():
            self.conn.execute(
                f"UPDATE followup_tasks SET {assignments} WHERE id = :id",
                fields,
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM followup_tasks WHERE id = ?", (task_id,)).fetchone()
        return row_to_dict(row)

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                """
                SELECT *
                FROM followup_tasks
                WHERE status = ?
                ORDER BY
                  CASE priority
                    WHEN 'urgent' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'normal' THEN 3
                    ELSE 4
                  END,
                  due_at ASC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM followup_tasks
                ORDER BY
                  CASE priority
                    WHEN 'urgent' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'normal' THEN 3
                    ELSE 4
                  END,
                  due_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return rows_to_dicts(rows)

    def list_due_tasks(self, now_iso: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM followup_tasks
            WHERE status IN ('pending', 'snoozed')
              AND (due_at <= ? OR (remind_at IS NOT NULL AND remind_at <= ?))
            ORDER BY
              CASE priority
                WHEN 'urgent' THEN 1
                WHEN 'high' THEN 2
                WHEN 'normal' THEN 3
                ELSE 4
              END,
              due_at ASC
            LIMIT ?
            """,
            (now_iso, now_iso, limit),
        ).fetchall()
        return rows_to_dicts(rows)

    def create_review_suggestion(self, suggestion: dict[str, Any]) -> dict[str, Any]:
        item_id = str(suggestion.get("id") or uuid.uuid4().hex)
        now_iso = str(suggestion["now_iso"])
        row = {
            "id": item_id,
            "title": str(suggestion["title"]).strip(),
            "suggestion": str(suggestion["suggestion"]).strip(),
            "source_type": suggestion.get("source_type"),
            "source_ref": suggestion.get("source_ref"),
            "status": str(suggestion.get("status", "open")),
            "created_at": now_iso,
            "updated_at": now_iso,
            "payload_json": json.dumps(suggestion.get("payload_json") or {}, ensure_ascii=False),
        }
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO review_suggestions
                  (id, title, suggestion, source_type, source_ref, status, created_at, updated_at, payload_json)
                VALUES
                  (:id, :title, :suggestion, :source_type, :source_ref, :status, :created_at, :updated_at, :payload_json)
                """,
                row,
            )
        return self.get_review_suggestion(item_id) or row

    def get_review_suggestion(self, suggestion_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM review_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        return row_to_dict(row)

    def list_review_suggestions(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM review_suggestions WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM review_suggestions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return rows_to_dicts(rows)

    def record_workflow(self, item: dict[str, Any]) -> dict[str, Any]:
        item_id = str(item.get("id") or uuid.uuid4().hex)
        now_iso = str(item["now_iso"])
        row = {
            "id": item_id,
            "title": str(item["title"]).strip(),
            "pattern_type": str(item["pattern_type"]).strip(),
            "summary": str(item["summary"]).strip(),
            "example_json": json.dumps(item.get("example_json") or {}, ensure_ascii=False),
            "source_task_ids_json": json.dumps(item.get("source_task_ids_json") or [], ensure_ascii=False),
            "sync_status": str(item.get("sync_status", "local_only")),
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO workflow_sync_items
                  (id, title, pattern_type, summary, example_json, source_task_ids_json, sync_status, created_at, updated_at)
                VALUES
                  (:id, :title, :pattern_type, :summary, :example_json, :source_task_ids_json, :sync_status, :created_at, :updated_at)
                """,
                row,
            )
        return self.get_workflow_item(item_id) or row

    def get_workflow_item(self, item_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM workflow_sync_items WHERE id = ?", (item_id,)).fetchone()
        return row_to_dict(row)

    def list_workflow_items(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM workflow_sync_items ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return rows_to_dicts(rows)

    def record_wake_run(self, wake: dict[str, Any]) -> dict[str, Any]:
        wake_id = str(wake.get("id") or uuid.uuid4().hex)
        started_at = str(wake["started_at"])
        row = {
            "id": wake_id,
            "wake_type": str(wake["wake_type"]),
            "started_at": started_at,
            "finished_at": wake.get("finished_at"),
            "status": str(wake.get("status", "running")),
            "input_summary": wake.get("input_summary"),
            "result_summary": wake.get("result_summary"),
            "error": wake.get("error"),
            "payload_json": json.dumps(wake.get("payload_json") or {}, ensure_ascii=False),
        }
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO wake_runs
                  (id, wake_type, started_at, finished_at, status, input_summary, result_summary, error, payload_json)
                VALUES
                  (:id, :wake_type, :started_at, :finished_at, :status, :input_summary, :result_summary, :error, :payload_json)
                """,
                row,
            )
        return self.get_wake_run(wake_id) or row

    def update_wake_run(self, wake_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "finished_at",
            "status",
            "input_summary",
            "result_summary",
            "error",
            "payload_json",
        }
        fields = {k: updates[k] for k in updates.keys() & allowed}
        if not fields:
            return self.get_wake_run(wake_id)
        if "payload_json" in fields:
            fields["payload_json"] = json.dumps(fields["payload_json"] or {}, ensure_ascii=False)
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        fields["id"] = wake_id
        with self.transaction():
            self.conn.execute(
                f"UPDATE wake_runs SET {assignments} WHERE id = :id",
                fields,
            )
        return self.get_wake_run(wake_id)

    def get_wake_run(self, wake_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM wake_runs WHERE id = ?", (wake_id,)).fetchone()
        return row_to_dict(row)

    def list_wake_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM wake_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return rows_to_dicts(rows)

    def upsert_scheduler_job(
        self,
        *,
        job_name: str,
        handler_name: str,
        schedule_kind: str,
        schedule_value: str,
        next_run_at: str,
        enabled: bool = True,
        payload_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO scheduler_jobs
                  (job_name, handler_name, schedule_kind, schedule_value, next_run_at, enabled, payload_json)
                VALUES
                  (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_name) DO UPDATE SET
                  handler_name = excluded.handler_name,
                  schedule_kind = excluded.schedule_kind,
                  schedule_value = excluded.schedule_value,
                  next_run_at = excluded.next_run_at,
                  enabled = excluded.enabled,
                  payload_json = excluded.payload_json
                """,
                (
                    job_name,
                    handler_name,
                    schedule_kind,
                    schedule_value,
                    next_run_at,
                    1 if enabled else 0,
                    json.dumps(payload_json or {}, ensure_ascii=False),
                ),
            )
        return self.get_scheduler_job(job_name) or {}

    def list_scheduler_jobs(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM scheduler_jobs ORDER BY next_run_at ASC, job_name ASC"
        ).fetchall()
        return rows_to_dicts(rows)

    def get_scheduler_job(self, job_name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM scheduler_jobs WHERE job_name = ?",
            (job_name,),
        ).fetchone()
        return row_to_dict(row)

    def list_due_scheduler_jobs(self, now_iso: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM scheduler_jobs
            WHERE enabled = 1 AND next_run_at <= ?
            ORDER BY next_run_at ASC, job_name ASC
            """,
            (now_iso,),
        ).fetchall()
        return rows_to_dicts(rows)

    def update_scheduler_job_run(
        self,
        job_name: str,
        *,
        last_run_at: str,
        next_run_at: str,
        payload_json: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction():
            self.conn.execute(
                """
                UPDATE scheduler_jobs
                SET last_run_at = ?, next_run_at = ?, payload_json = ?
                WHERE job_name = ?
                """,
                (
                    last_run_at,
                    next_run_at,
                    json.dumps(payload_json or {}, ensure_ascii=False),
                    job_name,
                ),
            )
