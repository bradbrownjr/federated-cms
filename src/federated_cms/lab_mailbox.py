"""Shared mailbox service for the isolated, synthetic B2F laboratory only.

``open_session`` is a trusted in-process boundary: callers must authenticate and
authorize the configured lab account before invoking it.  A Python object is
not a security boundary against other local code.  Do not use real messages,
credentials, or this service for production mailbox access.

The mailbox is separate from both legacy lab data and the plain-text mailbox.
No existing records are migrated or reclassified as authenticated mail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import secrets
import sqlite3
import time

from .store import SQLiteStore, StoreUnavailable
from .b2_message import message_mid

_ACCOUNT = re.compile(r"[A-Z0-9]{1,6}(?:-[0-9]{1,2})?\Z")
_MID = re.compile(r"[A-Z0-9]{1,32}\Z")
_LEASE_SECONDS = 120
_MAX_RAW_BYTES = 65536


class LabMailboxBusy(RuntimeError):
    """The lab mailbox already has an active session."""


class LabSessionExpired(RuntimeError):
    """The lab session was closed, replaced, or has expired."""


@dataclass(frozen=True)
class LabSession:
    """Opaque in-process lab session handle, never a network credential."""
    _token: str = field(repr=False)


class LabMailboxService:
    """SQLite-backed mailbox for explicitly scoped lab sessions.

    ``in`` means client-to-server and ``out`` means server-to-client. These
    are not assumptions about real-world sender or recipient mailboxes.
    Session leases are exclusive per mailbox across processes, expire after
    120 seconds without a service operation, and are renewed on each operation.
    Losing a lease leaves any unacknowledged messages pending for retry.
    """

    def __init__(self, store: SQLiteStore):
        self.store = store
        self.store.initialize()
        try:
            with self.store._connect() as con:
                con.executescript("""
                CREATE TABLE IF NOT EXISTS shared_lab_mailbox_sessions (
                    account TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE,
                    lease_until REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shared_lab_mailbox_messages (
                    account TEXT NOT NULL, mid TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('in', 'out')),
                    raw BLOB NOT NULL, delivered INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(account, mid, direction)
                );
                """)
        except sqlite3.Error as exc:
            raise StoreUnavailable("lab mailbox unavailable") from exc

    def open_session(self, account: str) -> LabSession:
        """Open a trusted, already-authorized lab account session.

        This method does not verify a callsign or password. Adapters must call
        it only after checking their own lab authentication and authorization.
        """
        account = account.upper()
        if not _ACCOUNT.fullmatch(account):
            raise ValueError("invalid lab mailbox account")
        token = secrets.token_urlsafe(32)
        try:
            with self.store._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                now = time.time()
                con.execute("DELETE FROM shared_lab_mailbox_sessions WHERE account=? AND lease_until<=?", (account, now))
                if con.execute("SELECT 1 FROM shared_lab_mailbox_sessions WHERE account=?", (account,)).fetchone():
                    raise LabMailboxBusy("lab mailbox already in use")
                con.execute("INSERT INTO shared_lab_mailbox_sessions(account,token,lease_until) VALUES(?,?,?)",
                            (account, token, now + _LEASE_SECONDS))
                con.execute("COMMIT")
        except sqlite3.Error as exc:
            raise StoreUnavailable("lab mailbox unavailable") from exc
        return LabSession(token)

    @staticmethod
    def _account(con: sqlite3.Connection, session: LabSession) -> str:
        if not isinstance(session, LabSession):
            raise LabSessionExpired("invalid lab session")
        now = time.time()
        cursor = con.execute("UPDATE shared_lab_mailbox_sessions SET lease_until=? WHERE token=? AND lease_until>?",
                             (now + _LEASE_SECONDS, session._token, now))
        if cursor.rowcount != 1:
            raise LabSessionExpired("lab session expired")
        return con.execute("SELECT account FROM shared_lab_mailbox_sessions WHERE token=?", (session._token,)).fetchone()[0]

    @staticmethod
    def _check(mid: str, direction: str) -> None:
        if not _MID.fullmatch(mid) or direction not in {"in", "out"}:
            raise ValueError("invalid lab message ID or direction")

    def store_message(self, session: LabSession, mid: str, raw: bytes, direction: str) -> bool:
        """Commit exact synthetic bytes once; reject a conflicting MID."""
        self._check(mid, direction)
        if not isinstance(raw, bytes) or len(raw) > _MAX_RAW_BYTES:
            raise ValueError("invalid lab message bytes")
        if message_mid(raw) != mid:
            raise ValueError("lab message MID does not match its content")
        try:
            with self.store._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                account = self._account(con, session)
                row = con.execute("SELECT raw FROM shared_lab_mailbox_messages WHERE account=? AND mid=? AND direction=?",
                                  (account, mid, direction)).fetchone()
                if row is not None and bytes(row["raw"]) != raw:
                    raise ValueError("lab message ID collision")
                if row is None:
                    con.execute("INSERT INTO shared_lab_mailbox_messages(account,mid,direction,raw) VALUES(?,?,?,?)",
                                (account, mid, direction, raw))
                con.execute("COMMIT")
                return row is None
        except sqlite3.Error as exc:
            raise StoreUnavailable("lab mailbox unavailable") from exc

    def get(self, session: LabSession, mid: str, direction: str) -> bytes | None:
        self._check(mid, direction)
        try:
            with self.store._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                account = self._account(con, session)
                row = con.execute("SELECT raw FROM shared_lab_mailbox_messages WHERE account=? AND mid=? AND direction=?",
                                  (account, mid, direction)).fetchone()
                con.execute("COMMIT")
                return bytes(row["raw"]) if row is not None else None
        except sqlite3.Error as exc:
            raise StoreUnavailable("lab mailbox unavailable") from exc

    def pending(self, session: LabSession, *, exclude: frozenset[str] | set[str] = frozenset()) -> list[tuple[str, bytes]]:
        """Offer at most five pending messages, omitting this session's deferrals.

        Walk IDs incrementally so excluded rows do not consume the offer limit
        and large message bodies are fetched only for messages actually offered.
        """
        try:
            with self.store._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                account = self._account(con, session)
                cursor = con.execute("SELECT rowid,mid FROM shared_lab_mailbox_messages WHERE account=? AND direction='out' AND delivered=0 ORDER BY rowid", (account,))
                rows = []
                for row in cursor:
                    if row["mid"] in exclude:
                        continue
                    raw = con.execute("SELECT raw FROM shared_lab_mailbox_messages WHERE rowid=?", (row["rowid"],)).fetchone()["raw"]
                    rows.append((row["mid"], bytes(raw)))
                    if len(rows) == 5:
                        break
                con.execute("COMMIT")
                return rows
        except sqlite3.Error as exc:
            raise StoreUnavailable("lab mailbox unavailable") from exc

    def list_incoming(self, session: LabSession) -> list[tuple[str, bytes]]:
        """List at most 100 messages uploaded in this lab mailbox."""
        try:
            with self.store._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                account = self._account(con, session)
                rows = con.execute("SELECT mid,raw FROM shared_lab_mailbox_messages WHERE account=? AND direction='in' ORDER BY rowid LIMIT 100", (account,)).fetchall()
                con.execute("COMMIT")
                return [(row["mid"], bytes(row["raw"])) for row in rows]
        except sqlite3.Error as exc:
            raise StoreUnavailable("lab mailbox unavailable") from exc

    def ack(self, session: LabSession, mid: str) -> bool:
        """Mark this account's pending outbound lab message delivered."""
        self._check(mid, "out")
        try:
            with self.store._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                account = self._account(con, session)
                cursor = con.execute("UPDATE shared_lab_mailbox_messages SET delivered=1 WHERE account=? AND mid=? AND direction='out' AND delivered=0", (account, mid))
                con.execute("COMMIT")
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StoreUnavailable("lab mailbox unavailable") from exc

    def close_session(self, session: LabSession) -> None:
        if not isinstance(session, LabSession):
            raise LabSessionExpired("invalid lab session")
        try:
            with self.store._connect() as con:
                con.execute("DELETE FROM shared_lab_mailbox_sessions WHERE token=?", (session._token,))
        except sqlite3.Error as exc:
            raise StoreUnavailable("lab mailbox unavailable") from exc
