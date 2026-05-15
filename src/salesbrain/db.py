from __future__ import annotations

import json
import re
import sqlite3
import threading
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

CREATE TABLE IF NOT EXISTS workflow_inbox_items (
  id TEXT PRIMARY KEY,
  source_node_id TEXT,
  source_event_id TEXT,
  title TEXT NOT NULL,
  pattern_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  example_json TEXT NOT NULL DEFAULT '{}',
  source_task_ids_json TEXT NOT NULL DEFAULT '[]',
  similarity_score REAL NOT NULL DEFAULT 0,
  dedupe_status TEXT NOT NULL CHECK (dedupe_status IN ('duplicate', 'needs_review', 'pending_review')),
  review_status TEXT NOT NULL CHECK (review_status IN ('pending', 'accepted', 'merged', 'ignored', 'duplicate')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_inbox_source_event
  ON workflow_inbox_items(source_event_id);
CREATE INDEX IF NOT EXISTS idx_workflow_inbox_review
  ON workflow_inbox_items(review_status, dedupe_status);

CREATE TABLE IF NOT EXISTS team_members (
  member_id TEXT PRIMARY KEY,
  team_name TEXT NOT NULL,
  node_id TEXT NOT NULL,
  real_name TEXT NOT NULL,
  endpoint TEXT,
  role TEXT NOT NULL DEFAULT 'sales',
  status TEXT NOT NULL CHECK (status IN ('online', 'offline', 'unknown')),
  version INTEGER NOT NULL DEFAULT 1,
  last_seen_at TEXT,
  updated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_team_members_team_node
  ON team_members(team_name, node_id);
CREATE INDEX IF NOT EXISTS idx_team_members_status
  ON team_members(team_name, status);

CREATE TABLE IF NOT EXISTS team_sync_events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  team_name TEXT NOT NULL,
  origin_node_id TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_team_sync_events_team_created
  ON team_sync_events(team_name, created_at);
CREATE INDEX IF NOT EXISTS idx_team_sync_events_entity
  ON team_sync_events(entity_type, entity_id);

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

CREATE TABLE IF NOT EXISTS scheduler_job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_name TEXT NOT NULL,
  handler_name TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
  duration_seconds REAL NOT NULL DEFAULT 0,
  error TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_scheduler_job_runs_job_started
  ON scheduler_job_runs(job_name, started_at DESC);

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

CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def rough_similarity(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    shorter, longer = sorted((left_norm, right_norm), key=len)
    if shorter and shorter in longer:
        return len(shorter) / len(longer)
    left_chars = set(left_norm)
    right_chars = set(right_norm)
    union = left_chars | right_chars
    if not union:
        return 0.0
    return len(left_chars & right_chars) / len(union)


SUMMARY_ID_KEYS = ("id", "projectId", "optId", "opportunityId", "custId", "customerId", "leadId", "taskId")
SUMMARY_NAME_KEYS = (
    "name",
    "projectName",
    "optName",
    "opportunityName",
    "custName",
    "customerName",
    "leadName",
    "taskName",
    "title",
)


def _pick_first(record: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _summary_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    if _pick_first(payload, SUMMARY_ID_KEYS) or _pick_first(payload, SUMMARY_NAME_KEYS):
        return payload
    data = payload.get("data")
    if isinstance(data, dict):
        if _pick_first(data, SUMMARY_ID_KEYS) or _pick_first(data, SUMMARY_NAME_KEYS):
            return data
    return payload


def extract_raw_record_summary(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    candidate = _summary_candidate(payload)
    return _pick_first(candidate, SUMMARY_ID_KEYS), _pick_first(candidate, SUMMARY_NAME_KEYS)


class SalesBrainStore:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
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

    def upsert_raw_record_by_object(
        self,
        *,
        sync_run_id: int,
        api_id: str,
        object_type: str,
        object_id: str,
        object_name: str | None,
        payload: dict[str, Any],
        fetched_at: str,
    ) -> bool:
        with self.transaction():
            row = self.conn.execute(
                """
                SELECT id
                FROM eboss_raw_records
                WHERE object_type = ? AND object_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (object_type, object_id),
            ).fetchone()
            if row is None:
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
                return True
            self.conn.execute(
                """
                UPDATE eboss_raw_records
                SET sync_run_id = ?, api_id = ?, object_name = ?, payload_json = ?, fetched_at = ?
                WHERE id = ?
                """,
                (
                    sync_run_id,
                    api_id,
                    object_name,
                    json.dumps(payload, ensure_ascii=False),
                    fetched_at,
                    row["id"],
                ),
            )
            return False

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
                object_id, object_name = extract_raw_record_summary(record)
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

    def repair_raw_record_summaries(self, *, object_type: str | None = None, limit: int | None = None) -> dict[str, Any]:
        where = "WHERE (object_id IS NULL OR object_name IS NULL)"
        params: list[Any] = []
        if object_type:
            where += " AND object_type = ?"
            params.append(object_type)
        sql = f"SELECT id, object_id, object_name, payload_json FROM eboss_raw_records {where} ORDER BY id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        scanned = 0
        repaired = 0
        skipped = 0
        with self.transaction():
            for row in rows:
                scanned += 1
                try:
                    payload = json.loads(str(row["payload_json"] or "{}"))
                except Exception:
                    skipped += 1
                    continue
                if not isinstance(payload, dict):
                    skipped += 1
                    continue
                object_id, object_name = extract_raw_record_summary(payload)
                object_id = object_id or row["object_id"]
                object_name = object_name or row["object_name"]
                if object_id == row["object_id"] and object_name == row["object_name"]:
                    skipped += 1
                    continue
                self.conn.execute(
                    """
                    UPDATE eboss_raw_records
                    SET object_id = ?, object_name = ?
                    WHERE id = ?
                    """,
                    (object_id, object_name, row["id"]),
                )
                repaired += 1
        return {"ok": True, "scanned": scanned, "repaired": repaired, "skipped": skipped, "object_type": object_type}

    def latest_raw_records(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM eboss_raw_records ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return rows_to_dicts(rows)

    def latest_raw_records_by_type(self, object_type: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM eboss_raw_records
            WHERE object_type = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (object_type, limit),
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

    def upsert_workflow_item(self, item: dict[str, Any]) -> dict[str, Any]:
        item_id = str(item["id"])
        row = {
            "id": item_id,
            "title": str(item["title"]).strip(),
            "pattern_type": str(item["pattern_type"]).strip(),
            "summary": str(item["summary"]).strip(),
            "example_json": json.dumps(item.get("example_json") or {}, ensure_ascii=False),
            "source_task_ids_json": json.dumps(item.get("source_task_ids_json") or [], ensure_ascii=False),
            "sync_status": str(item.get("sync_status", "ready")),
            "created_at": str(item.get("created_at") or item.get("now_iso")),
            "updated_at": str(item.get("updated_at") or item.get("now_iso")),
        }
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO workflow_sync_items
                  (id, title, pattern_type, summary, example_json, source_task_ids_json, sync_status, created_at, updated_at)
                VALUES
                  (:id, :title, :pattern_type, :summary, :example_json, :source_task_ids_json, :sync_status, :created_at, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                  title = excluded.title,
                  pattern_type = excluded.pattern_type,
                  summary = excluded.summary,
                  example_json = excluded.example_json,
                  source_task_ids_json = excluded.source_task_ids_json,
                  sync_status = excluded.sync_status,
                  updated_at = excluded.updated_at
                WHERE excluded.updated_at >= workflow_sync_items.updated_at
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

    def classify_workflow_candidate(self, item: dict[str, Any]) -> tuple[str, float]:
        title = str(item.get("title") or "")
        summary = str(item.get("summary") or "")
        candidates = self.conn.execute(
            """
            SELECT title, summary
            FROM workflow_sync_items
            ORDER BY updated_at DESC
            LIMIT 500
            """
        ).fetchall()
        best = 0.0
        for row in candidates:
            title_score = rough_similarity(title, str(row["title"]))
            summary_score = rough_similarity(summary, str(row["summary"]))
            score = max(title_score, summary_score)
            best = max(best, score)
            if title_score >= 1.0:
                return "duplicate", 1.0
        if best >= 0.82:
            return "needs_review", best
        return "pending_review", best

    def upsert_workflow_inbox_item(self, item: dict[str, Any]) -> dict[str, Any]:
        now_iso = str(item["now_iso"])
        dedupe_status, similarity_score = self.classify_workflow_candidate(item)
        inbox_id = str(item.get("id") or uuid.uuid4().hex)
        source_event_id = item.get("source_event_id")
        row = {
            "id": inbox_id,
            "source_node_id": item.get("source_node_id"),
            "source_event_id": source_event_id,
            "title": str(item["title"]).strip(),
            "pattern_type": str(item["pattern_type"]).strip(),
            "summary": str(item["summary"]).strip(),
            "example_json": json.dumps(item.get("example_json") or {}, ensure_ascii=False),
            "source_task_ids_json": json.dumps(item.get("source_task_ids_json") or [], ensure_ascii=False),
            "similarity_score": float(item.get("similarity_score", similarity_score)),
            "dedupe_status": str(item.get("dedupe_status") or dedupe_status),
            "review_status": str(item.get("review_status") or "pending"),
            "created_at": str(item.get("created_at") or now_iso),
            "updated_at": str(item.get("updated_at") or now_iso),
            "payload_json": json.dumps(item.get("payload_json") or {}, ensure_ascii=False),
        }
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO workflow_inbox_items
                  (id, source_node_id, source_event_id, title, pattern_type, summary,
                   example_json, source_task_ids_json, similarity_score, dedupe_status,
                   review_status, created_at, updated_at, payload_json)
                VALUES
                  (:id, :source_node_id, :source_event_id, :title, :pattern_type, :summary,
                   :example_json, :source_task_ids_json, :similarity_score, :dedupe_status,
                   :review_status, :created_at, :updated_at, :payload_json)
                ON CONFLICT(id) DO UPDATE SET
                  title = excluded.title,
                  pattern_type = excluded.pattern_type,
                  summary = excluded.summary,
                  example_json = excluded.example_json,
                  source_task_ids_json = excluded.source_task_ids_json,
                  similarity_score = excluded.similarity_score,
                  dedupe_status = excluded.dedupe_status,
                  updated_at = excluded.updated_at,
                  payload_json = excluded.payload_json
                """,
                row,
            )
        return self.get_workflow_inbox_item(inbox_id) or row

    def get_workflow_inbox_item(self, inbox_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM workflow_inbox_items WHERE id = ?", (inbox_id,)).fetchone()
        return row_to_dict(row)

    def list_workflow_inbox_items(self, *, review_status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if review_status:
            rows = self.conn.execute(
                """
                SELECT *
                FROM workflow_inbox_items
                WHERE review_status = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (review_status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM workflow_inbox_items ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return rows_to_dicts(rows)

    def update_workflow_inbox_item(self, inbox_id: str, updates: dict[str, Any], *, now_iso: str) -> dict[str, Any] | None:
        allowed = {"review_status", "dedupe_status", "similarity_score", "payload_json"}
        fields = {key: value for key, value in updates.items() if key in allowed}
        if not fields:
            return self.get_workflow_inbox_item(inbox_id)
        fields["updated_at"] = now_iso
        assignments = []
        values: list[Any] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            if key == "payload_json":
                values.append(json.dumps(value or {}, ensure_ascii=False))
            else:
                values.append(value)
        values.append(inbox_id)
        with self.transaction():
            self.conn.execute(
                f"UPDATE workflow_inbox_items SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
        return self.get_workflow_inbox_item(inbox_id)

    def upsert_team_member(self, member: dict[str, Any]) -> dict[str, Any]:
        version_raw = member.get("version", 1)
        version = 1 if version_raw in (None, "") else int(version_raw)
        row = {
            "member_id": str(member["member_id"]),
            "team_name": str(member["team_name"]),
            "node_id": str(member["node_id"]),
            "real_name": str(member.get("real_name") or ""),
            "endpoint": member.get("endpoint"),
            "role": str(member.get("role") or "sales"),
            "status": str(member.get("status") or "unknown"),
            "version": version,
            "last_seen_at": member.get("last_seen_at"),
            "updated_at": str(member["updated_at"]),
            "payload_json": json.dumps(member.get("payload_json") or {}, ensure_ascii=False),
        }
        with self.transaction():
            existing_row = self.conn.execute(
                """
                SELECT *
                FROM team_members
                WHERE member_id = ?
                   OR (team_name = ? AND node_id = ?)
                ORDER BY CASE WHEN member_id = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (row["member_id"], row["team_name"], row["node_id"], row["member_id"]),
            ).fetchone()
            existing = row_to_dict(existing_row)
            if existing:
                current_version = int(existing.get("version") or 0)
                current_updated_at = str(existing.get("updated_at") or "")
                is_stale = row["version"] < current_version and row["updated_at"] < current_updated_at
                if is_stale:
                    return existing
                self.conn.execute(
                    """
                    UPDATE team_members
                    SET member_id = :member_id,
                        team_name = :team_name,
                        node_id = :node_id,
                        real_name = :real_name,
                        endpoint = :endpoint,
                        role = :role,
                        status = :status,
                        version = :version,
                        last_seen_at = :last_seen_at,
                        updated_at = :updated_at,
                        payload_json = :payload_json
                    WHERE member_id = :existing_member_id
                    """,
                    {**row, "existing_member_id": existing["member_id"]},
                )
            else:
                self.conn.execute(
                    """
                    INSERT INTO team_members
                      (member_id, team_name, node_id, real_name, endpoint, role, status,
                       version, last_seen_at, updated_at, payload_json)
                    VALUES
                      (:member_id, :team_name, :node_id, :real_name, :endpoint, :role, :status,
                       :version, :last_seen_at, :updated_at, :payload_json)
                    """,
                    row,
                )
        return self.get_team_member(row["member_id"]) or row

    def get_team_member(self, member_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM team_members WHERE member_id = ?", (member_id,)).fetchone()
        return row_to_dict(row)

    def list_team_members(self, *, team_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if team_name:
            rows = self.conn.execute(
                """
                SELECT *
                FROM team_members
                WHERE team_name = ?
                ORDER BY status ASC, real_name ASC, updated_at DESC
                LIMIT ?
                """,
                (team_name, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM team_members ORDER BY team_name ASC, status ASC, real_name ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return rows_to_dicts(rows)

    def insert_team_sync_event(self, event: dict[str, Any]) -> bool:
        row = {
            "event_id": str(event["event_id"]),
            "event_type": str(event["event_type"]),
            "entity_type": str(event["entity_type"]),
            "entity_id": str(event["entity_id"]),
            "team_name": str(event["team_name"]),
            "origin_node_id": str(event["origin_node_id"]),
            "version": int(event.get("version") or 1),
            "created_at": str(event["created_at"]),
            "payload_json": json.dumps(event.get("payload_json") or {}, ensure_ascii=False),
        }
        with self.transaction():
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO team_sync_events
                  (event_id, event_type, entity_type, entity_id, team_name,
                   origin_node_id, version, created_at, payload_json)
                VALUES
                  (:event_id, :event_type, :entity_type, :entity_id, :team_name,
                   :origin_node_id, :version, :created_at, :payload_json)
                """,
                row,
            )
            return cur.rowcount > 0

    def list_team_sync_events(self, *, team_name: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM (
              SELECT *
              FROM team_sync_events
              WHERE team_name = ?
              ORDER BY created_at DESC
              LIMIT ?
            )
            ORDER BY created_at ASC
            """,
            (team_name, limit),
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

    def record_scheduler_job_run(
        self,
        *,
        job_name: str,
        handler_name: str,
        started_at: str,
        finished_at: str,
        status: str,
        duration_seconds: float = 0,
        error: str | None = None,
        detail_json: dict[str, Any] | None = None,
        payload_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.transaction():
            cur = self.conn.execute(
                """
                INSERT INTO scheduler_job_runs
                  (job_name, handler_name, started_at, finished_at, status, duration_seconds, error, detail_json, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_name,
                    handler_name,
                    started_at,
                    finished_at,
                    status,
                    duration_seconds,
                    error,
                    json.dumps(detail_json or {}, ensure_ascii=False),
                    json.dumps(payload_json or {}, ensure_ascii=False),
                ),
            )
        return self.get_scheduler_job_run(int(cur.lastrowid)) or {
            "id": int(cur.lastrowid),
            "job_name": job_name,
            "handler_name": handler_name,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
            "duration_seconds": duration_seconds,
            "error": error,
            "detail_json": json.dumps(detail_json or {}, ensure_ascii=False),
            "payload_json": json.dumps(payload_json or {}, ensure_ascii=False),
        }

    def get_scheduler_job_run(self, run_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM scheduler_job_runs WHERE id = ?", (run_id,)).fetchone()
        return row_to_dict(row)

    def latest_scheduler_job_run(self, job_name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM scheduler_job_runs
            WHERE job_name = ?
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            (job_name,),
        ).fetchone()
        return row_to_dict(row)

    def list_scheduler_job_runs(self, job_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if job_name:
            rows = self.conn.execute(
                """
                SELECT *
                FROM scheduler_job_runs
                WHERE job_name = ?
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                (job_name, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM scheduler_job_runs
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
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

    def set_scheduler_job_enabled(
        self,
        job_name: str,
        enabled: bool,
        *,
        payload_json: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction():
            self.conn.execute(
                """
                UPDATE scheduler_jobs
                SET enabled = ?, payload_json = ?
                WHERE job_name = ?
                """,
                (
                    1 if enabled else 0,
                    json.dumps(payload_json or {}, ensure_ascii=False),
                    job_name,
                ),
            )

    def set_state(self, key: str, value: str, *, now_iso: str) -> None:
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value = excluded.value,
                  updated_at = excluded.updated_at
                """,
                (key, value, now_iso),
            )

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        try:
            return str(row["value"])
        except (KeyError, IndexError, TypeError):
            return str(row[0])

    def delete_state(self, key: str) -> None:
        with self.transaction():
            self.conn.execute("DELETE FROM app_state WHERE key = ?", (key,))

    def count_raw_records(self, object_type: str | None = None) -> int:
        if object_type is None:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM eboss_raw_records").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS count FROM eboss_raw_records WHERE object_type = ?",
                (object_type,),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def raw_record_exists(self, *, object_type: str, object_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1
            FROM eboss_raw_records
            WHERE object_type = ? AND object_id = ?
            LIMIT 1
            """,
            (object_type, object_id),
        ).fetchone()
        return row is not None

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
