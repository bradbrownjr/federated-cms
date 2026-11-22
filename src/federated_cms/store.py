"""SQLite persistence boundary for local mailbox and service work.

Each operation uses a short-lived connection and an explicit transaction, so the
terminal listener can safely serve independent threads.  This is deliberately a
local storage API, not an implementation of the JSON federation transport.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .model import RequestReceipt, RequestView


VALID_STATES = {"queued", "processing", "completed", "failed", "expired"}


class StoreUnavailable(RuntimeError):
    """The local durable store could not be opened or used."""


class _ClosingConnection(sqlite3.Connection):
    """sqlite context managers commit/roll back but do not close by default."""
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        try:
            con = sqlite3.connect(self.path, timeout=5, isolation_level=None, factory=_ClosingConnection)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA synchronous = FULL")
            return con
        except sqlite3.Error as exc:
            raise StoreUnavailable("database unavailable") from exc

    def initialize(self) -> None:
        try:
            with self._connect() as con:
                con.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, mailbox TEXT NOT NULL, sender TEXT NOT NULL,
                    subject TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE,
                    owner_scope TEXT NOT NULL, service TEXT NOT NULL, payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('queued','processing','completed','failed','expired')),
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, result_json TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS requests_owner_created ON requests(owner_scope, created_at DESC);
                CREATE INDEX IF NOT EXISTS requests_status_expiry ON requests(status, expires_at);
                """)
        except sqlite3.Error as exc:
            raise StoreUnavailable("database initialization failed") from exc

    def enqueue_request(self, *, request_id: str, dedup_key: str, owner_scope: str,
                        service: str, payload: dict[str, Any], created_at: str,
                        expires_at: str) -> RequestReceipt:
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT id, status FROM requests WHERE dedup_key = ?", (dedup_key,)).fetchone()
                if row:
                    con.execute("COMMIT")
                    return RequestReceipt(row["id"], row["status"], True)
                con.execute("""INSERT INTO requests
                    (id,dedup_key,owner_scope,service,payload_json,status,created_at,expires_at,updated_at)
                    VALUES (?,?,?,?,?,'queued',?,?,?)""",
                    (request_id, dedup_key, owner_scope, service, json.dumps(payload, sort_keys=True),
                     created_at, expires_at, created_at))
                con.execute("COMMIT")
                return RequestReceipt(request_id, "queued", False)
        except sqlite3.Error as exc:
            raise StoreUnavailable("request was not durably accepted") from exc

    def store_message(self, message_id: str, mailbox: str, sender: str, subject: str,
                      body: str, created_at: str) -> bool:
        """Persist a local mailbox message; authorization remains an adapter policy."""
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                cursor = con.execute("INSERT OR IGNORE INTO messages (id,mailbox,sender,subject,body,created_at) VALUES (?,?,?,?,?,?)",
                                     (message_id, mailbox, sender, subject, body, created_at))
                con.execute("COMMIT")
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StoreUnavailable("message was not durably stored") from exc

    def mailbox_messages(self, mailbox: str, limit: int = 20) -> list[dict[str, str]]:
        try:
            with self._connect() as con:
                rows = con.execute("SELECT id,sender,subject,body,created_at FROM messages WHERE mailbox=? ORDER BY created_at DESC LIMIT ?", (mailbox, limit)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise StoreUnavailable("database unavailable") from exc

    def claim_next(self, now: str) -> sqlite3.Row | None:
        """Atomically claim one non-expired queued job, or mark stale jobs expired."""
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("UPDATE requests SET status='expired', updated_at=? WHERE status IN ('queued','processing') AND expires_at <= ?", (now, now))
                row = con.execute("SELECT * FROM requests WHERE status='queued' AND expires_at > ? ORDER BY created_at LIMIT 1", (now,)).fetchone()
                if row:
                    con.execute("UPDATE requests SET status='processing', updated_at=? WHERE id=?", (now, row["id"]))
                    row = con.execute("SELECT * FROM requests WHERE id=?", (row["id"],)).fetchone()
                con.execute("COMMIT")
                return row
        except sqlite3.Error as exc:
            raise StoreUnavailable("queue unavailable") from exc

    def finish(self, request_id: str, status: str, result: dict[str, Any], now: str) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("invalid terminal request state")
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("UPDATE requests SET status=?, result_json=?, updated_at=? WHERE id=? AND status='processing'",
                            (status, json.dumps(result, sort_keys=True), now, request_id))
                con.execute("COMMIT")
        except sqlite3.Error as exc:
            raise StoreUnavailable("could not record result") from exc

    def get_request(self, request_id: str, owner_scope: str) -> RequestView | None:
        try:
            with self._connect() as con:
                row = con.execute("SELECT * FROM requests WHERE id=? AND owner_scope=?", (request_id, owner_scope)).fetchone()
                return self._view(row) if row else None
        except sqlite3.Error as exc:
            raise StoreUnavailable("database unavailable") from exc

    def list_requests(self, owner_scope: str, limit: int = 10) -> list[RequestView]:
        try:
            with self._connect() as con:
                rows = con.execute("SELECT * FROM requests WHERE owner_scope=? ORDER BY created_at DESC LIMIT ?", (owner_scope, limit)).fetchall()
                return [self._view(row) for row in rows]
        except sqlite3.Error as exc:
            raise StoreUnavailable("database unavailable") from exc

    @staticmethod
    def _view(row: sqlite3.Row) -> RequestView:
        return RequestView(row["id"], row["status"], row["service"], row["created_at"], row["expires_at"],
                           json.loads(row["result_json"]) if row["result_json"] else None)

    def status_counts(self) -> dict[str, int]:
        try:
            with self._connect() as con:
                return {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM requests GROUP BY status")}
        except sqlite3.Error as exc:
            raise StoreUnavailable("database unavailable") from exc
