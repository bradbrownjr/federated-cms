"""Test helpers for the INQUIRY robot contract (docs/INQUIRY-ROBOT-SLICE1.md).

Fake clock, B2 message builder, fixture paths and a database environment.
This file is test code, not part of the contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sqlite3
import tempfile
from unittest.mock import patch

from federated_cms import CMSCore, SQLiteStore
from federated_cms.b2_message import message_mid
from federated_cms.robot import Robot
from federated_cms.robot_config import load_config
from tests.lab_mailbox_helpers import mailbox_call

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "robot"
BASIC_DIR = FIXTURES / "basic"
BASIC = BASIC_DIR / "robot.toml"
NODE = "N1TEST-10"
SALT = "testsalt"
T0 = datetime(2026, 11, 25, 4, 33, 0, tzinfo=timezone.utc)

HELP_LINES = [
    "Send requests to INQUIRY with subject REQUEST, one per line.",
    "",
    "HELP         this list",
    "HELP <verb>  about one service",
    "LIST         the catalog of items on this node",
    "<ID>         an item from the catalog",
]

CRASH_POINTS = (
    "claim_inbox.before", "claim_inbox.inside", "claim_inbox.after",
    "dispatch.before", "dispatch.inside", "dispatch.after",
    "claim_item.before", "claim_item.inside", "claim_item.after",
    "item.before", "item.inside", "item.after",
)


class Crash(BaseException):
    """Simulated process death; the robot must never catch it."""


class FakeClock:
    def __init__(self, now: datetime = T0):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def timestamp(self) -> float:
        return self.now.timestamp()


def body_text(*content_lines: str, node: str = NODE) -> str:
    """Reply body from the contract: content, empty line, 'via <node>'."""
    return "\n".join(list(content_lines) + ["", f"via {node}"]) + "\n"


def crlf(text: str) -> bytes:
    return text.replace("\n", "\r\n").encode("ascii")


def message(mid: str, body: str | bytes, *, subject: str | None = "REQUEST",
            sender: str | None = "N0CALL", to=("INQUIRY",), cc=(), extra=()) -> bytes:
    """A synthetic inbound B2 message as a Winlink client would send it."""
    lines = [f"Mid: {mid}", "Date: 2026/11/25 04:30", "Type: Private"]
    if sender is not None:
        lines.append(f"From: {sender}")
    lines += [f"To: {address}" for address in to]
    lines += [f"Cc: {address}" for address in cc]
    if subject is not None:
        lines.append(f"Subject: {subject}")
    lines += list(extra)
    lines.append("Mbo: N0CALL")
    data = body if isinstance(body, bytes) else body.encode("utf-8")
    header = "\r\n".join(lines + [f"Body: {len(data)}"]) + "\r\n\r\n"
    return header.encode("latin-1") + data


def parse_reply(raw: bytes) -> tuple[list[tuple[str, str]], str]:
    header, body = raw.split(b"\r\n\r\n", 1)
    fields = [tuple(line.decode("ascii").split(": ", 1)) for line in header.split(b"\r\n")]
    return fields, body.decode("ascii")


def subject_of(raw: bytes) -> str:
    return dict(parse_reply(raw)[0])["Subject"]


def copy_basic(directory: Path, extra_toml: str = "") -> Path:
    """Copy the basic fixture into `directory` and append TOML text."""
    target = Path(directory) / "config"
    shutil.copytree(BASIC_DIR, target)
    path = target / "robot.toml"
    if extra_toml:
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n" + extra_toml + "\n")
    return path


class RobotEnv:
    """A temporary database with the lab mailbox, the robot schema and a known salt."""

    def __init__(self, testcase, config_path: Path | str = BASIC, *, lease_seconds: int = 10):
        self.temp = tempfile.TemporaryDirectory()
        testcase.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.path = self.dir / "lab.sqlite"
        self.store = SQLiteStore(self.path)
        self.clock = FakeClock()
        self.lease_seconds = lease_seconds
        CMSCore(self.store).lab_mailbox()  # creates the mailbox and robot tables
        with self.db() as con:
            con.execute("INSERT INTO robot_meta (name, value) VALUES ('mid_salt', ?)", (SALT,))
        self.config = load_config(config_path)
        patcher = patch("federated_cms.lab_mailbox.time.time", side_effect=lambda: self.clock.timestamp())
        patcher.start()
        testcase.addCleanup(patcher.stop)

    def db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    def robot(self, fault=None, config=None) -> Robot:
        return Robot(self.store, config or self.config, clock=self.clock,
                     lease_seconds=self.lease_seconds, fault=fault)

    def deliver(self, raw: bytes, account: str = "N0CALL") -> bool:
        return mailbox_call(self.store, account, "store_message", message_mid(raw), raw, "in")

    def run(self, **kwargs) -> int:
        return self.robot(**kwargs).run_until_idle()

    def query(self, sql: str, params=()) -> list[sqlite3.Row]:
        con = self.db()
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def outbox(self, account: str = "N0CALL") -> list[tuple[str, bytes]]:
        rows = self.query("SELECT mid, raw FROM shared_lab_mailbox_messages "
                          "WHERE account=? AND direction='out' ORDER BY rowid", (account,))
        return [(row["mid"], bytes(row["raw"])) for row in rows]

    def all_out(self) -> list[tuple[str, str, bytes]]:
        rows = self.query("SELECT account, mid, raw FROM shared_lab_mailbox_messages "
                          "WHERE direction='out' ORDER BY rowid")
        return [(row["account"], row["mid"], bytes(row["raw"])) for row in rows]

    def inbox(self, mid: str, account: str = "N0CALL") -> sqlite3.Row:
        rows = self.query("SELECT * FROM robot_inbox WHERE account=? AND mid=?", (account, mid))
        return rows[0] if rows else None

    def items(self, mid: str | None = None) -> list[sqlite3.Row]:
        if mid is None:
            return self.query("SELECT * FROM robot_items ORDER BY id")
        return self.query("SELECT * FROM robot_items WHERE mid=? ORDER BY seq", (mid,))

    def replies(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM robot_replies ORDER BY rowid")
