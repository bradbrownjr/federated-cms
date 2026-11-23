"""Isolated authorization experiment for synthetic fixtures only.

This module does not authenticate credentials and is not connected to any
mailbox or network adapter. Provisioning and opening sessions are trusted
in-process test operations; neither Python objects nor this database are a
security boundary against other code in the process or a local database writer.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass, field
import hashlib
import math
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from pathlib import Path

_ACTIONS = frozenset({"read", "deposit"})
_MAX_SESSION_SECONDS = 3600
_MAX_FIXTURE_BYTES = 65536


class PolicyDenied(RuntimeError):
    """The session or grant is absent, expired, revoked or out of scope."""


class PolicyUnavailable(RuntimeError):
    """Policy storage or its time source cannot safely authorize work."""


@dataclass(frozen=True)
class PolicySession:
    """Opaque synthetic session handle, not an authenticated identity proof."""
    _token: str = field(repr=False)


class PolicyExperiment:
    """SQLite policy and synthetic fixture operations with transactional checks.

    Use a dedicated test database. The optional clock is injected only for
    deterministic offline tests; timestamps are UTC epoch seconds. A detected
    backward clock step fails closed until time catches up to the last committed
    timestamp. This is a lab safeguard, not a complete distributed-clock design.
    The protected operation is authorized at the final clock check immediately
    before COMMIT; that check and physical commit are not simultaneous. This is
    check-time linearization, not a guarantee about the wall time of disk commit.
    A write already holding the SQLite transaction linearizes before a later
    revocation transaction; revocation does not cancel a write in progress.
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.path = str(path)
        self._clock = clock
        try:
            with closing(self._connect()) as con:
                con.executescript("""
                CREATE TABLE IF NOT EXISTS policy_lab_clock (
                    id INTEGER PRIMARY KEY CHECK(id=1), last_seen REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS policy_lab_principals (
                    id TEXT PRIMARY KEY, active INTEGER NOT NULL CHECK(active IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS policy_lab_mailboxes (
                    id TEXT PRIMARY KEY, active INTEGER NOT NULL CHECK(active IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS policy_lab_grants (
                    principal TEXT NOT NULL REFERENCES policy_lab_principals(id),
                    mailbox TEXT NOT NULL REFERENCES policy_lab_mailboxes(id),
                    action TEXT NOT NULL CHECK(action IN ('read','deposit')),
                    expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(principal, mailbox, action)
                );
                CREATE TABLE IF NOT EXISTS policy_lab_sessions (
                    token_hash BLOB PRIMARY KEY, principal TEXT NOT NULL REFERENCES policy_lab_principals(id),
                    expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS policy_lab_fixtures (
                    mailbox TEXT NOT NULL REFERENCES policy_lab_mailboxes(id),
                    fixture_id TEXT NOT NULL, data BLOB NOT NULL,
                    PRIMARY KEY(mailbox, fixture_id)
                );
                """)
        except sqlite3.Error as exc:
            raise PolicyUnavailable("policy database unavailable") from exc

    def _connect(self) -> sqlite3.Connection:
        con = None
        try:
            con = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            con.execute("PRAGMA foreign_keys=ON")
            return con
        except sqlite3.Error as exc:
            if con is not None:
                con.close()
            raise PolicyUnavailable("policy database unavailable") from exc

    def _read_clock(self) -> float:
        try:
            now = float(self._clock())
        except Exception as exc:
            raise PolicyUnavailable("policy clock unavailable") from exc
        if not math.isfinite(now) or now < 0:
            raise PolicyUnavailable("policy clock unavailable")
        return now

    @contextmanager
    def _transaction(self) -> Iterator[tuple[sqlite3.Connection, float]]:
        try:
            with closing(self._connect()) as con:
                con.execute("BEGIN IMMEDIATE")
                try:
                    now = self._read_clock()
                    row = con.execute("SELECT last_seen FROM policy_lab_clock WHERE id=1").fetchone()
                    if row is not None and now < row[0]:
                        raise PolicyUnavailable("policy clock moved backwards")
                    con.execute("INSERT INTO policy_lab_clock VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen", (now,))
                    con.execute("SAVEPOINT policy_operation")
                    try:
                        yield con, now
                    except (PolicyDenied, PolicyUnavailable):
                        # Keep the last valid time even when authorization or
                        # the subsequent clock check fails, while rolling back
                        # every protected operation change.
                        observed = con.execute("SELECT last_seen FROM policy_lab_clock WHERE id=1").fetchone()[0]
                        con.execute("ROLLBACK TO policy_operation")
                        con.execute("UPDATE policy_lab_clock SET last_seen=MAX(last_seen, ?) WHERE id=1", (observed,))
                        con.execute("RELEASE policy_operation")
                        con.execute("COMMIT")
                        raise
                    con.execute("RELEASE policy_operation")
                    con.execute("COMMIT")
                except BaseException:
                    if con.in_transaction:
                        con.execute("ROLLBACK")
                    raise
        except sqlite3.Error as exc:
            raise PolicyUnavailable("policy database unavailable") from exc

    @staticmethod
    def _id(value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 128 or not value.isascii() or not all(c.isalnum() or c in "-_." for c in value):
            raise ValueError("invalid synthetic identifier")
        return value

    @staticmethod
    def _expiry(value: float) -> float:
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("invalid expiry")
        return value

    def add_principal(self, principal: str) -> None:
        """Trusted test fixture provisioning; never an externally callable API."""
        with self._transaction() as (con, _):
            con.execute("INSERT INTO policy_lab_principals VALUES (?, 1)", (self._id(principal),))

    def add_mailbox(self, mailbox: str) -> None:
        with self._transaction() as (con, _):
            con.execute("INSERT INTO policy_lab_mailboxes VALUES (?, 1)", (self._id(mailbox),))

    def set_grant(self, principal: str, mailbox: str, action: str, *, expires: float) -> None:
        if action not in _ACTIONS:
            raise ValueError("unknown action")
        with self._transaction() as (con, now):
            expires = self._expiry(expires)
            if expires <= now:
                raise ValueError("grant must expire in the future")
            con.execute("""INSERT INTO policy_lab_grants(principal,mailbox,action,expires,revoked)
                        VALUES(?,?,?,?,0) ON CONFLICT(principal,mailbox,action)
                        DO UPDATE SET expires=excluded.expires, revoked=0""",
                        (self._id(principal), self._id(mailbox), action, expires))

    def revoke_grant(self, principal: str, mailbox: str, action: str) -> None:
        with self._transaction() as (con, _):
            con.execute("UPDATE policy_lab_grants SET revoked=1 WHERE principal=? AND mailbox=? AND action=?",
                        (self._id(principal), self._id(mailbox), action))

    def disable_principal(self, principal: str) -> None:
        with self._transaction() as (con, _):
            con.execute("UPDATE policy_lab_principals SET active=0 WHERE id=?", (self._id(principal),))

    def disable_mailbox(self, mailbox: str) -> None:
        with self._transaction() as (con, _):
            con.execute("UPDATE policy_lab_mailboxes SET active=0 WHERE id=?", (self._id(mailbox),))

    def open_session(self, principal: str, *, ttl: float = 300) -> PolicySession:
        """Trusted fixture method; caller has NOT been authenticated here."""
        principal = self._id(principal)
        ttl = float(ttl)
        if not math.isfinite(ttl) or not 0 < ttl <= _MAX_SESSION_SECONDS:
            raise ValueError("invalid session duration")
        token = secrets.token_urlsafe(32)
        with self._transaction() as (con, now):
            if not con.execute("SELECT 1 FROM policy_lab_principals WHERE id=? AND active=1", (principal,)).fetchone():
                raise PolicyDenied("not authorized")
            con.execute("INSERT INTO policy_lab_sessions(token_hash,principal,expires) VALUES(?,?,?)",
                        (hashlib.sha256(token.encode("ascii")).digest(), principal, now + ttl))
        return PolicySession(token)

    def revoke_session(self, session: PolicySession) -> None:
        digest = self._digest(session)
        with self._transaction() as (con, _):
            con.execute("UPDATE policy_lab_sessions SET revoked=1 WHERE token_hash=?", (digest,))

    @staticmethod
    def _digest(session: PolicySession) -> bytes:
        if not isinstance(session, PolicySession) or not isinstance(session._token, str):
            raise PolicyDenied("not authorized")
        try:
            return hashlib.sha256(session._token.encode("ascii")).digest()
        except UnicodeEncodeError as exc:
            raise PolicyDenied("not authorized") from exc

    def _authorize(self, con: sqlite3.Connection, now: float, session: PolicySession, mailbox: str, action: str) -> None:
        digest = self._digest(session)
        row = con.execute("""SELECT 1 FROM policy_lab_sessions s
            JOIN policy_lab_principals p ON p.id=s.principal AND p.active=1
            JOIN policy_lab_grants g ON g.principal=p.id AND g.revoked=0 AND g.expires>?
            JOIN policy_lab_mailboxes m ON m.id=g.mailbox AND m.active=1
            WHERE s.token_hash=? AND s.revoked=0 AND s.expires>?
              AND m.id=? AND g.action=?""", (now, digest, now, self._id(mailbox), action)).fetchone()
        if row is None:
            raise PolicyDenied("not authorized")

    def _recheck(self, con: sqlite3.Connection, started: float, session: PolicySession,
                 mailbox: str, action: str) -> None:
        """Recheck deadlines immediately before commit (not a commit-time clock)."""
        now = self._read_clock()
        if now < started:
            raise PolicyUnavailable("policy clock unavailable or moved backwards")
        con.execute("UPDATE policy_lab_clock SET last_seen=? WHERE id=1", (now,))
        self._authorize(con, now, session, mailbox, action)

    def deposit_fixture(self, session: PolicySession, mailbox: str, fixture_id: str, data: bytes) -> None:
        """Authorize and commit one bounded synthetic fixture in one transaction."""
        fixture_id = self._id(fixture_id)
        if not isinstance(data, bytes) or len(data) > _MAX_FIXTURE_BYTES:
            raise ValueError("invalid fixture")
        with self._transaction() as (con, now):
            self._authorize(con, now, session, mailbox, "deposit")
            con.execute("INSERT INTO policy_lab_fixtures(mailbox,fixture_id,data) VALUES(?,?,?)", (mailbox, fixture_id, data))
            self._recheck(con, now, session, mailbox, "deposit")

    def read_fixture(self, session: PolicySession, mailbox: str, fixture_id: str) -> bytes | None:
        """Authorize and read one synthetic fixture in the same transaction."""
        fixture_id = self._id(fixture_id)
        with self._transaction() as (con, now):
            self._authorize(con, now, session, mailbox, "read")
            row = con.execute("SELECT data FROM policy_lab_fixtures WHERE mailbox=? AND fixture_id=?", (mailbox, fixture_id)).fetchone()
            self._recheck(con, now, session, mailbox, "read")
            return bytes(row[0]) if row else None
