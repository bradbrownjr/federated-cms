"""Contract tests for the INQUIRY robot, slice 1.

See docs/INQUIRY-ROBOT-SLICE1.md. Expected strings, MIDs and byte counts are
written from that contract, not taken from an implementation. Do not change
these tests to make an implementation pass.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from federated_cms import CMSCore, SQLiteStore
from federated_cms.b2_message import message_mid
from federated_cms.store import StoreUnavailable
from federated_cms import robot as robot_module
from federated_cms.b2_message import B2MessageError
from federated_cms.robot import (
    ReplyError,
    Robot,
    build_reply,
    classify,
    enqueue_inbound,
    make_subject,
    reply_mid,
    robot_addresses,
)
from federated_cms.robot_config import load_config
from tests.lab_mailbox_helpers import mailbox_call
from tests.robot_helpers import (
    CRASH_POINTS, HELP_LINES, NODE, SALT, T0, Crash, FakeClock, RobotEnv,
    body_text, copy_basic, crlf, message, parse_reply, subject_of,
)

M1 = "REQ000000001"

LIST_LINES = [
    "Catalog of N1TEST-10. Send an ID to INQUIRY with subject REQUEST.",
    "",
    "LOCAL",
    "BAND_PLAN  Local band plan",
    "NODE_INFO  About this node",
    "",
    "NETS",
    "NET_TIMES  Weekly net schedule",
]
NODE_INFO_LINES = [
    "This is a test node for the INQUIRY robot.",
    "It answers HELP, LIST and the items in LIST.",
]


def expected_raw(mid: str, subject: str, body: str, *, account: str = "N0CALL",
                 date: str = "2026/11/25 04:33") -> bytes:
    data = crlf(body)
    header = (f"Mid: {mid}\r\nDate: {date}\r\nType: Private\r\nFrom: SERVICE\r\n"
              f"To: {account}\r\nSubject: {subject}\r\nMbo: {NODE}\r\nBody: {len(data)}\r\n\r\n")
    return header.encode("ascii") + data


def mid_for(seq: int, *, inbound: str = M1, account: str = "N0CALL", part: int = 1) -> str:
    return reply_mid(SALT, account, inbound, seq, part)


# ---------------------------------------------------------------------------
# Reply MIDs, subjects and the raw reply
# ---------------------------------------------------------------------------

class ReplyMidTests(unittest.TestCase):
    def test_golden_values(self):
        # base32(sha256("<salt>|<account>|<inbound mid>|<seq>|<part>"))[:12].
        # For "testsalt|N0CALL|REQ000000001|1|1" the digest starts
        # 0e b7 16 37 af 2d 29 f9; as bits 00001 11010 11011 10001 01100 01101
        # 11101 01111 ... = 1 26 27 17 12 13 29 15 ... = B 2 3 R M N 5 P ...
        self.assertEqual(reply_mid("testsalt", "N0CALL", "REQ000000001", 1, 1), "B23RMN5PFUU7")
        self.assertEqual(reply_mid("testsalt", "N0CALL", "REQ000000001", 2, 1), "MJ25S6X57TYD")
        self.assertEqual(reply_mid("testsalt", "N0CALL", "REQ000000001", 0, 1), "WLOG5X2ZANUV")
        self.assertEqual(reply_mid("othersalt", "N0CALL", "REQ000000001", 1, 1), "UFNIJPTXZAJV")
        self.assertEqual(reply_mid("testsalt", "N1TEST", "REQ000000001", 1, 1), "W3KLMF4LMSGG")

    def test_shape_and_inputs_all_matter(self):
        seen = set()
        for args in (("s", "N0CALL", "A", 1, 1), ("s", "N0CALL", "A", 1, 2), ("s", "N0CALL", "A", 2, 1),
                     ("s", "N0CALL", "B", 1, 1), ("s", "N1TEST", "A", 1, 1), ("t", "N0CALL", "A", 1, 1),
                     ("s", "N0CALL", "A", 0, 0), ("s", "N0CALL", "A", 0, 1)):
            mid = reply_mid(*args)
            self.assertRegex(mid, r"\A[A-Z2-7]{12}\Z")
            seen.add(mid)
        self.assertEqual(len(seen), 8)

    def test_deterministic(self):
        self.assertEqual(reply_mid("x", "N0CALL", "M", 3, 1), reply_mid("x", "N0CALL", "M", 3, 1))


class SubjectTests(unittest.TestCase):
    def test_plain_and_suffix(self):
        self.assertEqual(make_subject("NODE_INFO"), "INQUIRY - NODE_INFO")
        self.assertEqual(make_subject("NOPE", "not found"), "INQUIRY - NOPE: not found")
        self.assertEqual(make_subject("REQUEST", "invalid subject"), "INQUIRY - REQUEST: invalid subject")

    def test_exactly_80_is_not_cut(self):
        label = "A" * 70
        self.assertEqual(make_subject(label), "INQUIRY - " + label)
        self.assertEqual(len(make_subject(label)), 80)

    def test_long_label_cut_keeps_suffix(self):
        # 10 ("INQUIRY - ") + 64 + 11 (": not found") = 85 > 80, so the label
        # keeps 80 - 10 - 11 - 3 = 56 characters, then "...".
        subject = make_subject("B" * 64, "not found")
        self.assertEqual(subject, "INQUIRY - " + "B" * 56 + "...: not found")
        self.assertEqual(len(subject), 80)

    def test_long_label_cut_without_suffix(self):
        # 80 - 10 - 0 - 3 = 67 characters kept.
        subject = make_subject("C" * 75)
        self.assertEqual(subject, "INQUIRY - " + "C" * 67 + "...")

    def test_non_printable_becomes_question_mark(self):
        self.assertEqual(make_subject("AéB\x07C\tD"), "INQUIRY - A?B?C?D")


class BuildReplyTests(unittest.TestCase):
    def test_golden_bytes(self):
        raw = build_reply(mid="B23RMN5PFUU7", account="N0CALL", node_name=NODE,
                          subject="INQUIRY - HELP", body="one\n\ntwo\n",
                          now=datetime(2026, 11, 25, 4, 33, 59, tzinfo=timezone.utc))
        self.assertEqual(raw, b"Mid: B23RMN5PFUU7\r\nDate: 2026/11/25 04:33\r\nType: Private\r\n"
                              b"From: SERVICE\r\nTo: N0CALL\r\nSubject: INQUIRY - HELP\r\n"
                              b"Mbo: N1TEST-10\r\nBody: 12\r\n\r\none\r\n\r\ntwo\r\n")  # 3+2, 2, 3+2
        self.assertEqual(message_mid(raw), "B23RMN5PFUU7")

    def test_date_is_utc(self):
        local = timezone(timedelta(hours=-5))
        raw = build_reply(mid="ABC", account="N0CALL", node_name=NODE, subject="INQUIRY - X",
                          body="x\n", now=datetime(2026, 11, 24, 23, 59, tzinfo=local))
        self.assertIn(b"\r\nDate: 2026/11/25 04:59\r\n", raw)

    def test_refusals(self):
        good = dict(mid="ABC", account="N0CALL", node_name=NODE, subject="INQUIRY - X",
                    body="x\n", now=T0)
        bad = [
            dict(mid="abc"), dict(mid="A" * 13), dict(mid=""), dict(mid="AB-C"),
            dict(account="n0call"), dict(account="N0CALL-100"), dict(account="TOOLONG1"),
            dict(node_name="N1TEST 10"),
            dict(subject="S" * 81), dict(subject="INQUIRY - é"), dict(subject="INQUIRY - a\r\nX-Evil: 1"),
            dict(body="café\n"), dict(body="a\tb\n"), dict(body="a\r\nb\n"), dict(body="bell\x07\n"),
            dict(body="x" * 70000 + "\n"),
            dict(now=datetime(2026, 11, 25, 4, 33)),
        ]
        for change in bad:
            with self.subTest(change=list(change)):
                with self.assertRaises(ReplyError):
                    build_reply(**{**good, **change})
        self.assertTrue(issubclass(ReplyError, ValueError))


# ---------------------------------------------------------------------------
# Schema and the acceptance hook
# ---------------------------------------------------------------------------

class HookTests(unittest.TestCase):
    def setUp(self):
        self.env = RobotEnv(self)

    def test_mailbox_creates_slice1_tables_only(self):
        names = {row["name"] for row in self.env.query("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("robot_meta", "robot_inbox", "robot_items", "robot_replies", "robot_counters"):
            self.assertIn(table, names)
        for table in ("robot_menus", "robot_notices", "robot_positions", "fetch_cache", "fetch_tasks"):
            self.assertNotIn(table, names)
        columns = {row["name"] for row in self.env.query("PRAGMA table_info(robot_items)")}
        self.assertIn("attempts", columns)

    def test_initialize_schema_inserts_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            con = sqlite3.connect(Path(directory) / "x.sqlite")
            robot_module.initialize_schema(con)
            robot_module.initialize_schema(con)  # idempotent
            self.assertEqual(con.execute("SELECT COUNT(*) FROM robot_meta").fetchone()[0], 0)
            con.close()

    def test_inquiry_is_queued(self):
        self.assertTrue(self.env.deliver(message(M1, "HELP\r\n")))
        row = self.env.inbox(M1)
        self.assertEqual(row["addresses"], "INQUIRY")
        self.assertEqual(row["received_at"], "2026-11-25T04:33:00.000000Z")
        self.assertEqual(row["next_attempt_at"], "2026-11-25T04:33:00.000000Z")
        self.assertEqual(row["state"], "queued")
        self.assertEqual(row["attempts"], 0)
        self.assertIsNone(row["reason"])
        self.assertIsNone(row["lease_token"])
        self.assertIsNone(row["lease_until"])
        self.assertIsNone(row["finished_at"])

    def test_received_at_keeps_microseconds(self):
        self.env.clock.advance(1.25)
        self.env.deliver(message(M1, "HELP\r\n"))
        self.assertEqual(self.env.inbox(M1)["received_at"], "2026-11-25T04:33:01.250000Z")

    def test_addresses_from_to_and_cc_sorted(self):
        cases = [
            (dict(to=("QTH",), cc=("inquiry@example.org",)), "INQUIRY,QTH"),
            (dict(to=("SMTP:service@example.net",)), "SERVICE"),
            (dict(to=("N1TEST, INQUIRY; qth",)), "INQUIRY,QTH"),
            (dict(to=("N1TEST", "SERVICE@example.org", "INQUIRY")), "INQUIRY,SERVICE"),
        ]
        for number, (kwargs, expected) in enumerate(cases):
            with self.subTest(kwargs=kwargs):
                mid = f"ADDR{number}"
                self.env.deliver(message(mid, "LIST\r\n", **kwargs))
                self.assertEqual(self.env.inbox(mid)["addresses"], expected)

    def test_other_mail_is_not_queued(self):
        for number, to in enumerate((("N1TEST",), ("INQUIRYX",), ("Name <INQUIRY@example.org>",),
                                     ('"INQUIRY"@example.org',), ("INQUIRY.X@example.org",))):
            with self.subTest(to=to):
                mid = f"OTHER{number}"
                self.assertTrue(self.env.deliver(message(mid, "LIST\r\n", to=to)))
                self.assertIsNone(self.env.inbox(mid))

    def test_outbound_and_legacy_rows_are_not_queued(self):
        raw = message(M1, "HELP\r\n")
        mailbox_call(self.env.store, "N0CALL", "store_message", M1, raw, "out")
        self.env.store.b2f_lab_store("N0CALL", "LEGACY1", raw.replace(M1.encode(), b"LEGACY1"), "in")
        self.assertEqual(self.env.query("SELECT COUNT(*) n FROM robot_inbox")[0]["n"], 0)

    def test_duplicate_store_does_not_queue_again(self):
        raw = message(M1, "HELP\r\n")
        self.assertTrue(self.env.deliver(raw))
        self.env.run()
        self.assertEqual(self.env.inbox(M1)["state"], "done")
        self.assertFalse(self.env.deliver(raw))
        self.assertEqual(self.env.query("SELECT COUNT(*) n FROM robot_inbox")[0]["n"], 1)
        self.assertEqual(self.env.inbox(M1)["state"], "done")
        self.assertEqual(self.env.run(), 0)
        self.assertEqual(len(self.env.outbox()), 1)

    def test_hook_database_failure_stores_nothing(self):
        raw = message(M1, "HELP\r\n")
        real = robot_module.enqueue_inbound
        seen = {}

        def failing(con, *args):
            seen["in_transaction"] = con.in_transaction
            seen["queued"] = real(con, *args)  # the queue row is written, then the database fails
            raise sqlite3.OperationalError("disk I/O error")
        with patch("federated_cms.robot.enqueue_inbound", side_effect=failing):
            with self.assertRaises(StoreUnavailable):
                self.env.deliver(raw)
        self.assertEqual(seen, {"in_transaction": True, "queued": True})
        self.assertIsNone(mailbox_call(self.env.store, "N0CALL", "get", M1, "in"))
        self.assertIsNone(self.env.inbox(M1))
        self.assertTrue(self.env.deliver(raw))
        self.assertEqual(self.env.inbox(M1)["state"], "queued")

    def test_hook_called_once_per_new_inbound_row(self):
        real = robot_module.enqueue_inbound
        calls = []

        def counting(con, account, mid, raw, received_at):
            calls.append((account, mid))
            return real(con, account, mid, raw, received_at)
        raw = message(M1, "HELP\r\n")
        with patch("federated_cms.robot.enqueue_inbound", side_effect=counting):
            self.assertTrue(self.env.deliver(raw))
            self.assertFalse(self.env.deliver(raw))  # duplicate: not called again
            mailbox_call(self.env.store, "N0CALL", "store_message", "OUT1", message("OUT1", "HELP"), "out")
            self.env.deliver(message("PLAIN1", "hello", to=("N1TEST",)))
        self.assertEqual(calls, [("N0CALL", M1), ("N0CALL", "PLAIN1")])

    def test_unreadable_message_is_stored_and_recorded(self):
        raw = message(M1, "HELP\r\n")
        for error in (ValueError("bad"), B2MessageError("bad"), UnicodeDecodeError("ascii", b"x", 0, 1, "bad")):
            with self.subTest(error=type(error).__name__):
                env = RobotEnv(self)
                with self.assertLogs("federated_cms.robot", level="WARNING") as logs:
                    with patch("federated_cms.robot.classify", side_effect=error):
                        self.assertTrue(env.deliver(raw))
                self.assertEqual(mailbox_call(env.store, "N0CALL", "get", M1, "in"), raw)
                row = env.inbox(M1)
                self.assertEqual((row["addresses"], row["state"], row["reason"], row["attempts"]),
                                 ("", "ignored", "unreadable", 0))
                self.assertEqual(row["finished_at"], "2026-11-25T04:33:00.000000Z")
                self.assertEqual(row["next_attempt_at"], "2026-11-25T04:33:00.000000Z")
                record = [r for r in logs.records if r.getMessage().startswith("event=unreadable")]
                self.assertEqual(len(record), 1)
                self.assertEqual(record[0].levelname, "WARNING")
                tokens = set(record[0].getMessage().split())
                self.assertTrue({"account=N0CALL", f"mid={M1}", f"error_class={type(error).__name__}"} <= tokens)
                self.assertEqual(env.run(), 0)

    def test_enqueue_inbound_directly(self):
        con = self.env.db()
        try:
            when = datetime(2026, 11, 25, 5, 0, tzinfo=timezone.utc)
            self.assertFalse(enqueue_inbound(con, "N0CALL", "X1", message("X1", "LIST", to=("N1TEST",)), when))
            self.assertTrue(enqueue_inbound(con, "N0CALL", "X2", message("X2", "LIST"), when))
            self.assertFalse(enqueue_inbound(con, "N0CALL", "X2", message("X2", "LIST"), when))
            self.assertFalse(con.in_transaction)
        finally:
            con.close()
        self.assertEqual(self.env.inbox("X2")["received_at"], "2026-11-25T05:00:00.000000Z")


class RobotAddressesTests(unittest.TestCase):
    def test_matches(self):
        self.assertEqual(robot_addresses(message(M1, "LIST", to=("INQUIRY@example.net",))), frozenset({"INQUIRY"}))
        self.assertEqual(robot_addresses(message(M1, "LIST", to=("inquiry",), cc=("Qth@x",))),
                         frozenset({"INQUIRY", "QTH"}))
        self.assertEqual(robot_addresses(message(M1, "LIST", to=("N1TEST",))), frozenset())

    def test_folded_header(self):
        raw = (b"Mid: F1\r\nTo: N1TEST,\r\n INQUIRY\r\nSubject: REQUEST\r\nBody: 4\r\n\r\nLIST")
        self.assertEqual(robot_addresses(raw), frozenset({"INQUIRY"}))

    def test_never_raises(self):
        hostile = [
            b"", b"garbage", b"\r\n\r\n", "Mid: X\r\nBody: 0\r\n\r\n", None, 12, bytearray(b"x"),
            b"Mid: X\r\nTo: INQUIRY\r\n\r\n",                       # no Body header
            b"Mid: X\r\nTo: \xff\xfe\r\nBody: 0\r\n\r\n",
            b"Mid: X\r\nTo: @example.org\r\nBody: 0\r\n\r\n",
            b"Mid: X\r\nTo: INQUIRY\r\nBody: 0\r\n\r\n" + b"x" * 70000,
            message(M1, "LIST", to=("SMTP:",)),
        ]
        for raw in hostile:
            with self.subTest(raw=repr(raw)[:40]):
                self.assertEqual(robot_addresses(raw), frozenset())

    def test_separators_inside_a_header_value(self):
        # Header lines end only at CRLF; a lone LF, CR or U+0085 stays in the
        # value, which then holds a non-printable character and is skipped.
        for separator in (b"\n", b"\r", b"\x85"):
            with self.subTest(separator=separator):
                raw = message(M1, "LIST", to=("N0CALL",)).replace(
                    b"To: N0CALL\r\n", b"To: N0CALL" + separator + b"To: INQUIRY\r\n")
                self.assertEqual(message_mid(raw), M1)
                self.assertEqual(robot_addresses(raw), frozenset())
                self.assertEqual(classify(raw), frozenset())

    def test_space_is_not_a_separator(self):
        self.assertEqual(robot_addresses(message(M1, "LIST", to=("N0CALL INQUIRY",))), frozenset())

    def test_non_ascii_space_is_not_stripped(self):
        raw = message(M1, "LIST", to=("INQUIRY\xa0",))
        self.assertIn(b"To: INQUIRY\xa0\r\n", raw)
        self.assertEqual(robot_addresses(raw), frozenset())
        self.assertEqual(robot_addresses(message(M1, "LIST", to=(" \tINQUIRY \t, N1TEST",))),
                         frozenset({"INQUIRY"}))

    def test_classify_raises_robot_addresses_does_not(self):
        with self.assertRaises(B2MessageError):
            classify(b"garbage")
        self.assertEqual(robot_addresses(b"garbage"), frozenset())

    def test_many_recipients(self):
        to = ", ".join(f"N{n}X" for n in range(2000)) + ", INQUIRY"
        self.assertEqual(robot_addresses(message(M1, "LIST", to=(to,))), frozenset({"INQUIRY"}))


# ---------------------------------------------------------------------------
# Worker: happy paths
# ---------------------------------------------------------------------------

class HappyPathTests(unittest.TestCase):
    def setUp(self):
        self.env = RobotEnv(self)

    def one_reply(self, body, **kwargs):
        self.env.deliver(message(M1, body, **kwargs))
        self.env.run()
        out = self.env.outbox()
        self.assertEqual(len(out), 1)
        return out[0]

    def test_help(self):
        self.env.deliver(message(M1, "HELP\r\n"))
        self.assertEqual(self.env.run(), 2)  # one dispatch, one item
        mid, raw = self.env.outbox()[0]
        self.assertEqual(mid, "B23RMN5PFUU7")
        self.assertEqual(raw, expected_raw("B23RMN5PFUU7", "INQUIRY - HELP", body_text(*HELP_LINES)))
        self.assertIn(b"\r\nBody: 224\r\n", raw)  # 208 characters in 8 lines, plus 8 CRLF
        reply = self.env.replies()[0]
        self.assertEqual((reply["account"], reply["reply_mid"], reply["kind"], reply["body_bytes"],
                          reply["created_at"]),
                         ("N0CALL", "B23RMN5PFUU7", "content", 224, "2026-11-25T04:33:00.000000Z"))
        item = self.env.items(M1)[0]
        self.assertEqual(reply["item_id"], item["id"])
        self.assertEqual((item["seq"], item["kind"], item["state"], item["attempts"]), (1, "help", "done", 1))
        self.assertEqual(json.loads(item["request_json"]),
                         {"kind": "service", "value": "HELP", "arguments": "", "choice": None})
        self.assertEqual(item["finished_at"], "2026-11-25T04:33:00.000000Z")
        self.assertIsNone(item["lease_token"])
        inbox = self.env.inbox(M1)
        self.assertEqual((inbox["state"], inbox["reason"], inbox["attempts"], inbox["finished_at"]),
                         ("done", None, 1, "2026-11-25T04:33:00.000000Z"))
        self.assertIsNone(inbox["lease_token"])

    def test_reply_is_offered_by_pending(self):
        self.env.deliver(message(M1, "HELP\r\n"))
        self.env.run()
        pending = mailbox_call(self.env.store, "N0CALL", "pending")
        self.assertEqual(pending, self.env.outbox())

    def test_list(self):
        mid, raw = self.one_reply("LIST\r\n")
        self.assertEqual(raw, expected_raw(mid_for(1), "INQUIRY: LIST", body_text(*LIST_LINES)))
        self.assertEqual(self.env.items(M1)[0]["kind"], "list")

    def test_update_cat_is_list(self):
        mid, raw = self.one_reply("update_cat\r\n")
        self.assertEqual(raw, expected_raw(mid_for(1), "INQUIRY: LIST", body_text(*LIST_LINES)))

    def test_static_item(self):
        mid, raw = self.one_reply("node_info ; comment\r\n")
        self.assertEqual(raw, expected_raw(mid_for(1), "INQUIRY - NODE_INFO", body_text(*NODE_INFO_LINES)))
        self.assertEqual(self.env.items(M1)[0]["kind"], "catalog")
        self.assertEqual(self.env.replies()[0]["kind"], "content")

    def test_static_item_without_final_newline(self):
        mid, raw = self.one_reply("BAND_PLAN\r\n")
        self.assertEqual(raw, expected_raw(mid_for(1), "INQUIRY - BAND_PLAN",
                                           body_text("Synthetic band plan for tests.")))

    def test_unknown_item(self):
        mid, raw = self.one_reply("NOPE\r\n")
        self.assertEqual(raw, expected_raw(mid_for(1), "INQUIRY - NOPE: not found",
                                           body_text("No item NOPE on this node. Send LIST for the catalog.")))
        self.assertEqual(self.env.replies()[0]["kind"], "error")
        self.assertEqual(self.env.items(M1)[0]["state"], "done")

    def test_url_not_available(self):
        mid, raw = self.one_reply("https://example.org/a;b=1\r\n")
        self.assertEqual(raw, expected_raw(mid_for(1), "INQUIRY - https://example.org/a;b=1: not available",
                                           body_text("This node does not fetch web pages.")))
        self.assertEqual(self.env.items(M1)[0]["kind"], "url")
        self.assertEqual(self.env.replies()[0]["kind"], "error")

    def test_help_arguments(self):
        cases = [
            ("HELP HELP", "INQUIRY - HELP HELP",
             ["HELP lists the requests this node answers. HELP <verb> explains one."], "content"),
            ("help list", "INQUIRY - HELP list",
             ["LIST returns the catalog of items on this node. Send any ID from it to get that item."], "content"),
            ("HELP wx  fn43", "INQUIRY - HELP wx fn43: not found",
             ["No service wx fn43 on this node.", "", *HELP_LINES], "error"),
        ]
        for number, (line, subject, content, kind) in enumerate(cases):
            with self.subTest(line=line):
                inbound = f"HELPARG{number}"
                self.env.deliver(message(inbound, line + "\r\n"))
                self.env.run()
                mid = reply_mid(SALT, "N0CALL", inbound, 1, 1)
                raw = dict(self.env.outbox())[mid]
                self.assertEqual(raw, expected_raw(mid, subject, body_text(*content)))
                self.assertEqual(self.env.query("SELECT kind FROM robot_replies WHERE reply_mid=?", (mid,))[0]["kind"], kind)

    def test_help_choice_ignored(self):
        mid, raw = self.one_reply("HELP | 2\r\n")
        self.assertEqual(raw, expected_raw(mid_for(1), "INQUIRY - HELP", body_text(*HELP_LINES)))

    def test_items_in_order_with_their_own_mids(self):
        self.env.deliver(message(M1, "HELP\r\nLIST\r\nNODE_INFO\r\nHELP\r\n"))
        self.assertEqual(self.env.run(), 4)
        out = self.env.outbox()
        self.assertEqual([mid for mid, _ in out], ["B23RMN5PFUU7", "MJ25S6X57TYD", mid_for(3)])
        self.assertEqual([subject_of(raw) for _, raw in out],
                         ["INQUIRY - HELP", "INQUIRY: LIST", "INQUIRY - NODE_INFO"])
        self.assertEqual([row["seq"] for row in self.env.items(M1)], [1, 2, 3])

    def test_rejected_lines_reply(self):
        body = "LIST\r\nBAD LINE\r\nPUBßX\r\nLI\x07ST\r\nFOO\tBAR\r\n"
        self.env.deliver(message(M1, body))
        self.env.run()
        out = self.env.outbox()
        self.assertEqual([mid for mid, _ in out], ["WLOG5X2ZANUV", "B23RMN5PFUU7"])
        expected = body_text(
            "These lines were not understood:",
            "Line 2: BAD LINE (invalid item)",
            "Line 3: PUB?X (invalid item)",
            "Line 4: LI?ST (invalid item)",
            "Line 5: FOO?BAR (invalid item)",
            "", *HELP_LINES)
        self.assertEqual(out[0][1], expected_raw("WLOG5X2ZANUV", "INQUIRY - REQUEST: lines not understood", expected))
        first = self.env.replies()[0]
        self.assertEqual((first["reply_mid"], first["item_id"], first["kind"]), ("WLOG5X2ZANUV", None, "error"))
        self.assertEqual(self.env.inbox(M1)["state"], "done")

    def test_rejected_service_rules(self):
        # Reasons come from parse_inquiry with services={"HELP"}.
        self.env.deliver(message(M1, "HELP a | b | c ; x\r\nHELP |\r\n"))
        self.env.run()
        raw = dict(self.env.outbox())["WLOG5X2ZANUV"]
        self.assertEqual(parse_reply(raw)[1], crlf(body_text(
            "These lines were not understood:",
            "Line 1: HELP a | b | c (multiple choices)",
            "Line 2: HELP | (empty choice)",
            "", *HELP_LINES)).decode())

    def test_message_errors(self):
        cases = [
            (dict(body="LIST", subject="HELLO"), "invalid subject"),
            (dict(body="LIST", subject=None), "invalid subject"),
            (dict(body="LIST", subject="REQ\x07UEST"), "invalid subject"),
            (dict(body="LIST", subject="REQUÉST"), "invalid subject"),
            (dict(body=""), "empty request"),
            (dict(body="; only a comment\r\n\r\n"), "empty request"),
            (dict(body="A" * 8193), "body too large"),
            (dict(body=b"LIST\r\n\x81\r\n"), "invalid body"),
        ]
        for number, (kwargs, reason) in enumerate(cases):
            with self.subTest(reason=reason, number=number):
                self.env = RobotEnv(self)  # fresh: invalid_per_day defaults to 5
                inbound = f"BAD{number}"
                self.env.deliver(message(inbound, **kwargs))
                self.env.run()
                mid = reply_mid(SALT, "N0CALL", inbound, 0, 1)
                self.assertEqual([m for m, _ in self.env.outbox()], [mid])
                raw = dict(self.env.outbox())[mid]
                self.assertEqual(raw, expected_raw(mid, f"INQUIRY - REQUEST: {reason}", body_text(
                    f"Your request could not be read: {reason}.", "", *HELP_LINES)))
                row = self.env.inbox(inbound)
                self.assertEqual((row["state"], row["reason"]), ("done", "parse_error"))
                self.assertEqual(self.env.items(inbound), [])

    def test_cp1252_fallback(self):
        self.env.deliver(message(M1, b"LIST\r\nCAF\xe9\r\n"))
        self.env.run()
        raw = dict(self.env.outbox())["WLOG5X2ZANUV"]
        self.assertIn(b"Line 2: CAF? (invalid item)\r\n", raw)

    def test_first_from_wins(self):
        # As email.message.Message.get: a later From cannot override the first.
        self.env.deliver(message("FROM1", "HELP", sender="N1TEST", extra=("From: SERVICE",)))
        self.env.run()
        self.assertEqual(self.env.inbox("FROM1")["state"], "done")
        self.assertEqual(self.env.query("SELECT value FROM robot_counters WHERE name='from_mismatch'")[0]["value"], 1)
        self.env.deliver(message("FROM2", "HELP", sender="SERVICE", extra=("From: N0CALL",)))
        self.env.run()
        self.assertEqual((self.env.inbox("FROM2")["state"], self.env.inbox("FROM2")["reason"]),
                         ("ignored", "loop_name"))

    def test_first_subject_wins(self):
        self.env.deliver(message("SUBJ1", "HELP", subject="REQUEST", extra=("Subject: HELLO",)))
        self.env.deliver(message("SUBJ2", "HELP", subject="HELLO", extra=("Subject: REQUEST",)))
        self.env.run()
        self.assertIsNone(self.env.inbox("SUBJ1")["reason"])
        self.assertEqual(self.env.inbox("SUBJ2")["reason"], "parse_error")

    def test_attachment_ignored(self):
        body = b"HELP"
        raw = (b"Mid: ATT1\r\nFrom: N0CALL\r\nTo: INQUIRY\r\nSubject: REQUEST\r\nBody: 4\r\n"
               b"File: 5 note.txt\r\n\r\n" + body + b"\r\nLIST!\r\n")
        self.env.deliver(raw)
        self.env.run()
        out = self.env.outbox()
        self.assertEqual([subject_of(r) for _, r in out], ["INQUIRY - HELP"])

    def test_empty_catalog_list(self):
        path = Path(self.env.dir) / "empty.toml"
        path.write_text('[node]\nname = "N1TEST-10"\ncontact = "https://example.org/sysop"\n', encoding="ascii")
        robot = self.env.robot(config=load_config(path))
        self.env.deliver(message(M1, "LIST\r\nNODE_INFO\r\n"))
        robot.run_until_idle()
        out = self.env.outbox()
        self.assertEqual(out[0][1], expected_raw(mid_for(1), "INQUIRY: LIST", body_text(
            "Catalog of N1TEST-10. Send an ID to INQUIRY with subject REQUEST.", "", "No items.")))
        self.assertEqual(subject_of(out[1][1]), "INQUIRY - NODE_INFO: not found")

    def test_salt_created_once_and_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "s.sqlite")
            CMSCore(store).lab_mailbox()
            config = load_config(copy_basic(Path(directory)))
            Robot(store, config, clock=FakeClock())
            con = sqlite3.connect(Path(directory) / "s.sqlite")
            first = con.execute("SELECT value FROM robot_meta WHERE name='mid_salt'").fetchone()[0]
            self.assertRegex(first, r"\A[0-9a-f]{32}\Z")
            Robot(store, config, clock=FakeClock())
            self.assertEqual(con.execute("SELECT value FROM robot_meta WHERE name='mid_salt'").fetchone()[0], first)
            con.close()

    def test_seeded_salt_is_used(self):
        self.assertEqual(self.env.query("SELECT value FROM robot_meta WHERE name='mid_salt'")[0]["value"], SALT)
        self.env.robot()
        self.assertEqual(self.env.query("SELECT value FROM robot_meta WHERE name='mid_salt'")[0]["value"], SALT)

    def test_idle_returns_false(self):
        robot = self.env.robot()
        self.assertFalse(robot.run_once())
        self.assertEqual(robot.run_until_idle(), 0)

    def test_queued_row_waits_for_its_time(self):
        self.env.deliver(message(M1, "HELP"))
        self.env.clock.advance(-1)
        self.assertEqual(self.env.run(), 0)
        self.env.clock.advance(1)
        self.assertEqual(self.env.run(), 2)

    def test_run_until_idle_max_steps(self):
        self.env.deliver(message(M1, "HELP\r\nLIST\r\n"))
        self.assertEqual(self.env.robot().run_until_idle(max_steps=2), 2)
        self.assertEqual(len(self.env.outbox()), 1)
        self.assertEqual(self.env.run(), 1)


# ---------------------------------------------------------------------------
# Loop rules and identity
# ---------------------------------------------------------------------------

class LoopRuleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.env = RobotEnv(self, copy_basic(Path(temp.name), '[robot]\nblocked = ["N0BAD"]'))

    def assert_ignored(self, mid, reason, account="N0CALL"):
        row = self.env.inbox(mid, account)
        self.assertEqual((row["state"], row["reason"]), ("ignored", reason))
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(self.env.items(mid), [])

    def test_loop_name_accounts(self):
        for account in ("QTH", "SYSTEM", "N1TEST-10"):
            with self.subTest(account=account):
                mid = "LOOPACC" + account.replace("-", "")
                self.env.deliver(message(mid, "HELP", sender=account), account=account)
                self.env.run()
                self.assert_ignored(mid, "loop_name", account)
        self.assertEqual(self.env.all_out(), [])

    def test_loop_name_from(self):
        senders = ("SERVICE@example.net", "inquiry", "MAILER-DAEMON@example.org", "postmaster",
                   "SMTP:system@example.org", "qth@example.org", "N1TEST-10")
        for number, sender in enumerate(senders):
            with self.subTest(sender=sender):
                mid = f"LOOPFROM{number}"
                self.env.deliver(message(mid, "HELP", sender=sender))
                self.env.run()
                self.assert_ignored(mid, "loop_name")
        self.assertEqual(self.env.all_out(), [])

    def test_node_base_call_is_not_a_loop(self):
        self.env.deliver(message(M1, "HELP", sender="N1TEST"), account="N1TEST")
        self.env.run()
        self.assertEqual(len(self.env.outbox("N1TEST")), 1)

    def test_loop_mid_any_account(self):
        self.env.deliver(message(M1, "HELP"))
        self.env.run()
        reply = self.env.outbox()[0][0]
        self.assertEqual(reply, "B23RMN5PFUU7")
        self.env.deliver(message(reply, "HELP", sender="N1TEST"), account="N1TEST")
        self.env.deliver(message(reply, "HELP"), account="N0CALL")
        self.env.run()
        self.assert_ignored(reply, "loop_mid", "N1TEST")
        self.assert_ignored(reply, "loop_mid", "N0CALL")
        self.assertEqual(len(self.env.all_out()), 1)

    def test_own_reply_sent_back_is_ignored(self):
        self.env.deliver(message(M1, "HELP"))
        self.env.run()
        mid, raw = self.env.outbox()[0]
        bounced = raw.replace(b"To: N0CALL\r\n", b"To: INQUIRY\r\n")
        self.env.deliver(bounced)
        self.env.run()
        self.assert_ignored(mid, "loop_name")
        self.assertEqual(len(self.env.outbox()), 1)

    def test_auto_submitted(self):
        ignored = (["Auto-Submitted: auto-replied"], ["Auto-Submitted: auto-generated; type=x"],
                   ["Auto-Submitted: "], ["auto-submitted: AUTO-REPLIED"],
                   ["Auto-Submitted: no", "Auto-Submitted: auto-replied"])
        for number, extra in enumerate(ignored):
            with self.subTest(extra=extra):
                mid = f"AUTO{number}"
                self.env.deliver(message(mid, "HELP", extra=extra))
                self.env.run()
                self.assert_ignored(mid, "auto_submitted")
        for number, extra in enumerate((["Auto-Submitted: no"], ["Auto-Submitted:  No ; comment"])):
            with self.subTest(extra=extra):
                mid = f"HUMAN{number}"
                self.env.deliver(message(mid, "HELP", extra=extra))
                self.env.run()
                self.assertEqual(self.env.inbox(mid)["state"], "done")

    def test_blocked_account(self):
        self.env.deliver(message("BLK1", "HELP", sender="N0BAD"), account="N0BAD")
        self.env.deliver(message("BLK2", "HELP", sender="N0BAD"), account="N0CALL")
        self.env.run()
        self.assert_ignored("BLK1", "blocked", "N0BAD")
        self.assertEqual(self.env.inbox("BLK2")["state"], "done")
        self.assertEqual(self.env.outbox("N0BAD"), [])

    def test_rule_order(self):
        # Rule 1 (loop_name) before rule 3 (auto_submitted) before rule 4 (blocked).
        self.env.deliver(message("ORD1", "HELP", sender="SERVICE", extra=("Auto-Submitted: yes",)), account="N0BAD")
        self.env.deliver(message("ORD2", "HELP", sender="N0BAD", extra=("Auto-Submitted: yes",)), account="N0BAD")
        self.env.run()
        self.assert_ignored("ORD1", "loop_name", "N0BAD")
        self.assert_ignored("ORD2", "auto_submitted", "N0BAD")

    def test_unsupported_addresses(self):
        self.env.deliver(message("UNS1", "DATE: 2026/11/25 04:00", to=("QTH",)))
        self.env.deliver(message("UNS2", "1", to=("SERVICE",), subject="Re: INQUIRY - X [K7QX]"))
        self.env.deliver(message("UNS3", "HELP", to=("QTH", "INQUIRY")))
        self.env.run()
        self.assert_ignored("UNS1", "unsupported")
        self.assert_ignored("UNS2", "unsupported")
        self.assertEqual(self.env.inbox("UNS3")["state"], "done")
        self.assertEqual(len(self.env.outbox()), 1)

    def counter(self, name="from_mismatch"):
        rows = self.env.query("SELECT value FROM robot_counters WHERE day='2026-11-25' AND name=?", (name,))
        return rows[0]["value"] if rows else 0

    def test_foreign_from_replies_to_account(self):
        self.env.deliver(message(M1, "HELP", sender="N1TEST@example.net"))
        self.env.run()
        self.assertEqual(self.counter(), 1)
        self.assertEqual(self.env.outbox("N1TEST"), [])
        mid, raw = self.env.outbox("N0CALL")[0]
        self.assertIn(b"\r\nTo: N0CALL\r\n", raw)
        self.assertNotIn(b"N1TEST@", raw)

    def test_from_mismatch_counting(self):
        cases = [("N0CALL", 0), ("n0call@example.net", 0), ("SMTP:N0CALL@example.org", 0),
                 ("N0CALL-5", 1), (None, 1), ("\xe9\xe9", 1)]
        total = 0
        for number, (sender, added) in enumerate(cases):
            with self.subTest(sender=sender):
                self.env.deliver(message(f"FROM{number}", "HELP", sender=sender))
                self.env.run()
                total += added
                self.assertEqual(self.counter(), total)
                self.assertEqual(self.env.inbox(f"FROM{number}")["state"], "done")

    def test_mismatch_counted_even_when_ignored(self):
        self.env.deliver(message("MISS1", "HELP", sender="N1TEST", extra=("Auto-Submitted: auto-replied",)))
        self.env.run()
        self.assert_ignored("MISS1", "auto_submitted")
        self.assertEqual(self.counter(), 1)


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

class LimitTests(unittest.TestCase):
    def env_with(self, toml):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return RobotEnv(self, copy_basic(Path(temp.name), toml))

    def test_requests_per_hour_rolling_and_strict(self):
        env = self.env_with("[limits.accounts.N0CALL]\nrequests_per_hour = 2")
        for mid, offset in (("RPH1", 0), ("RPH2", 60), ("RPH3", 120)):
            env.clock.now = T0 + timedelta(seconds=offset)
            env.deliver(message(mid, "HELP"))
            env.run()
        self.assertEqual(env.inbox("RPH3")["reason"], "limited")
        self.assertEqual(env.inbox("RPH3")["state"], "ignored")
        # Window (T0+30, T0+3630]: RPH2, RPH3 and RPH4 = 3 > 2. Without
        # counting the limited RPH3 it would be 2, so limited rows count.
        env.clock.now = T0 + timedelta(seconds=3630)
        env.deliver(message("RPH4", "HELP"))
        env.run()
        self.assertEqual((env.inbox("RPH4")["state"], env.inbox("RPH4")["reason"]), ("ignored", "limited"))
        # Window (T0+120, T0+3720]: RPH4 and RPH5 = 2. RPH3 at exactly T0+120
        # is outside, so the boundary is strict.
        env.clock.now = T0 + timedelta(seconds=3720)
        env.deliver(message("RPH5", "HELP"))
        env.run()
        self.assertEqual(env.inbox("RPH5")["state"], "done")
        self.assertEqual(len(env.outbox()), 3)
        # Other accounts keep the default of 10.
        for number in range(3):
            env.deliver(message(f"OTH{number}", "HELP", sender="N1TEST"), account="N1TEST")
        env.run()
        self.assertEqual(len(env.outbox("N1TEST")), 3)

    def test_requests_per_hour_same_instant_uses_arrival_order(self):
        env = self.env_with("[limits]\nrequests_per_hour = 1")
        env.deliver(message("SAME1", "HELP"))
        env.deliver(message("SAME2", "HELP"))
        env.run()
        self.assertEqual(env.inbox("SAME1")["state"], "done")
        self.assertEqual((env.inbox("SAME2")["state"], env.inbox("SAME2")["reason"]), ("ignored", "limited"))

    def test_undelivered_max(self):
        env = self.env_with("[limits]\nundelivered_max = 2")
        env.deliver(message("UND1", "HELP\r\nLIST\r\nNODE_INFO"))
        env.run()
        self.assertEqual(len(env.outbox()), 3)  # checked at dispatch only
        mailbox_call(env.store, "N0CALL", "ack", env.outbox()[0][0])
        env.deliver(message("UND2", "HELP"))  # 2 undelivered: at the limit
        env.run()
        self.assertEqual((env.inbox("UND2")["state"], env.inbox("UND2")["reason"]), ("ignored", "limited"))
        mailbox_call(env.store, "N0CALL", "ack", env.outbox()[1][0])
        env.deliver(message("UND3", "HELP"))  # 1 undelivered
        env.run()
        self.assertEqual(env.inbox("UND3")["state"], "done")
        self.assertEqual(len(env.outbox()), 4)

    def test_invalid_per_day(self):
        env = self.env_with("[limits]\ninvalid_per_day = 1")
        env.deliver(message("INV1", "HELP", subject="HELLO"))
        env.deliver(message("INV2", "HELP", subject="HELLO"))
        env.deliver(message("INV3", "HELP"))
        env.run()
        self.assertEqual((env.inbox("INV1")["state"], env.inbox("INV1")["reason"]), ("done", "parse_error"))
        self.assertEqual((env.inbox("INV2")["state"], env.inbox("INV2")["reason"]), ("ignored", "invalid_limit"))
        self.assertEqual(env.inbox("INV3")["state"], "done")
        self.assertEqual(len(env.outbox()), 2)
        env.clock.advance(86400)
        env.deliver(message("INV4", "HELP", subject="HELLO"))
        env.run()
        self.assertEqual(env.inbox("INV4")["reason"], "parse_error")
        self.assertEqual(len(env.outbox()), 3)

    def test_items_per_request(self):
        env = self.env_with("[limits]\nitems_per_request = 2")
        env.deliver(message(M1, "HELP\r\nLIST\r\nNODE_INFO\r\n"))
        env.run()
        self.assertEqual([(row["seq"], row["state"]) for row in env.items(M1)],
                         [(1, "done"), (2, "done"), (3, "limited")])
        self.assertEqual(len(env.outbox()), 2)

    def test_messages_per_day(self):
        env = self.env_with("[limits.accounts.N0CALL]\nmessages_per_day = 2")
        env.deliver(message(M1, "HELP\r\nLIST\r\nNODE_INFO\r\n!!\r\n"))
        env.run()
        self.assertEqual([mid for mid, _ in env.outbox()], ["WLOG5X2ZANUV", "B23RMN5PFUU7"])
        self.assertEqual([row["state"] for row in env.items(M1)], ["done", "limited", "limited"])
        self.assertTrue(all(row["finished_at"] for row in env.items(M1)))
        # Another account is not affected by N0CALL's override.
        env.deliver(message("OTHER1", "HELP\r\nLIST\r\nNODE_INFO\r\n", sender="N1TEST"), account="N1TEST")
        env.run()
        self.assertEqual(len(env.outbox("N1TEST")), 3)
        # A new UTC day starts a new count.
        env.clock.now = datetime(2026, 11, 26, 0, 0, tzinfo=timezone.utc)
        env.deliver(message("NEXTDAY", "HELP\r\nLIST\r\nNODE_INFO\r\n"))
        env.run()
        self.assertEqual([row["state"] for row in env.items("NEXTDAY")], ["done", "done", "limited"])

    def test_message_level_reply_counts_and_can_be_dropped(self):
        env = self.env_with("[limits]\nmessages_per_day = 1")
        env.deliver(message("ML1", "HELP"))
        env.run()
        env.deliver(message("ML2", "HELP", subject="HELLO"))
        env.run()
        self.assertEqual([subject_of(raw) for _, raw in env.outbox()], ["INQUIRY - HELP"])
        self.assertEqual((env.inbox("ML2")["state"], env.inbox("ML2")["reason"]), ("done", "parse_error"))

    def test_bytes_per_day_boundary_inclusive(self):
        help_bytes = len(crlf(body_text(*HELP_LINES)))
        self.assertEqual(help_bytes, 224)
        env = self.env_with(f"[limits]\nbytes_per_day = {help_bytes}")
        env.deliver(message(M1, "HELP\r\nNOPE\r\nLIST\r\n"))
        env.run()
        self.assertEqual([row["state"] for row in env.items(M1)], ["done", "limited", "limited"])
        self.assertEqual([row["body_bytes"] for row in env.replies()], [224])

    def test_counting_rows(self):
        env = self.env_with("")
        env.deliver(message(M1, "HELP\r\nNOPE\r\n!!\r\n"))
        env.run()
        rows = env.replies()
        self.assertEqual([(r["reply_mid"], r["kind"]) for r in rows],
                         [("WLOG5X2ZANUV", "error"), ("B23RMN5PFUU7", "content"), ("MJ25S6X57TYD", "error")])
        for row, (mid, raw) in zip(rows, env.outbox()):
            self.assertEqual(row["reply_mid"], mid)
            self.assertEqual(row["body_bytes"], len(raw.split(b"\r\n\r\n", 1)[1]))
            self.assertEqual(row["created_at"], "2026-11-25T04:33:00.000000Z")


# ---------------------------------------------------------------------------
# Crash points, replay and leases
# ---------------------------------------------------------------------------

CRASH_BODY = "HELP\r\nLIST\r\nNODE_INFO\r\nNOPE\r\n!!\r\n"


def fault_once(point, exception=Crash, action=None):
    state = {"hit": 0}

    def fault(name):
        if name == point:
            state["hit"] += 1
            if state["hit"] == 1:
                if action is not None:
                    action()
                    return
                raise exception(point)
    fault.state = state
    return fault


class CrashTests(unittest.TestCase):
    def baseline(self):
        env = RobotEnv(self)
        env.deliver(message(M1, CRASH_BODY))
        env.run()
        return env.outbox()

    def test_baseline(self):
        out = self.baseline()
        self.assertEqual([mid for mid, _ in out], ["WLOG5X2ZANUV", "B23RMN5PFUU7", "MJ25S6X57TYD",
                                                   mid_for(3), mid_for(4)])

    def test_crash_at_every_point(self):
        expected = self.baseline()
        for point in CRASH_POINTS:
            with self.subTest(point=point):
                env = RobotEnv(self)
                env.deliver(message(M1, CRASH_BODY))
                fault = fault_once(point)
                with self.assertRaises(Crash):
                    env.run(fault=fault)
                self.assertEqual(fault.state["hit"], 1)
                self.check_rolled_back(env, point)
                env.clock.advance(11)  # past the 10 s lease, same minute
                env.run()
                self.assertEqual(env.outbox(), expected)
                self.assertEqual(len(env.replies()), 5)
                self.assertEqual([row["state"] for row in env.items(M1)], ["done"] * 4)
                self.assertEqual(env.inbox(M1)["state"], "done")

    def check_rolled_back(self, env, point):
        inbox = env.inbox(M1)
        if point == "claim_inbox.inside":
            self.assertEqual((inbox["state"], inbox["attempts"]), ("queued", 0))
        elif point == "dispatch.inside":
            self.assertEqual(inbox["state"], "processing")
            self.assertEqual(env.items(M1), [])
            self.assertEqual(env.outbox(), [])
            self.assertEqual(env.replies(), [])
        elif point == "claim_item.inside":
            self.assertEqual((env.items(M1)[0]["state"], env.items(M1)[0]["attempts"]), ("ready", 0))
        elif point == "item.inside":
            self.assertEqual(env.items(M1)[0]["state"], "processing")
            self.assertEqual([mid for mid, _ in env.outbox()], ["WLOG5X2ZANUV"])
            self.assertEqual(len(env.replies()), 1)

    def test_attempts_limit_inbox(self):
        env = RobotEnv(self)
        env.deliver(message(M1, CRASH_BODY))

        def always(name):
            if name == "dispatch.before":
                raise Crash(name)
        for _ in range(3):
            with self.assertRaises(Crash):
                env.run(fault=always)
            env.clock.advance(11)
        robot = env.robot(fault=always)
        self.assertTrue(robot.run_once())
        row = env.inbox(M1)
        self.assertEqual((row["state"], row["reason"], row["attempts"]), ("failed", "attempts", 3))
        self.assertEqual(robot.run_until_idle(), 0)
        self.assertEqual(env.outbox(), [])

    def test_attempts_limit_items(self):
        env = RobotEnv(self)
        env.deliver(message(M1, CRASH_BODY))

        def always(name):
            if name == "item.before":
                raise Crash(name)
        for _ in range(100):
            try:
                env.run(fault=always)
                break
            except Crash:
                env.clock.advance(11)
        else:
            self.fail("worker never went idle")
        self.assertEqual([(row["state"], row["error"], row["attempts"]) for row in env.items(M1)],
                         [("failed", "attempts", 3)] * 4)
        self.assertEqual([mid for mid, _ in env.outbox()], ["WLOG5X2ZANUV"])

    def test_replay_of_inbox_row_sends_nothing_new(self):
        env = RobotEnv(self)
        env.deliver(message(M1, CRASH_BODY))
        env.run()
        before = env.outbox()
        with env.db() as con:
            con.execute("UPDATE robot_inbox SET state='queued', reason=NULL, attempts=0, lease_token=NULL, "
                        "lease_until=NULL, finished_at=NULL")
        env.clock.advance(86400)  # a later Date would change the bytes; the first copy is kept
        env.run()
        self.assertEqual(env.outbox(), before)
        self.assertEqual(len(env.items(M1)), 4)
        self.assertEqual(len(env.replies()), 5)

    def test_replay_of_item_keeps_first_copy(self):
        env = RobotEnv(self)
        env.deliver(message(M1, CRASH_BODY))
        env.run()
        before = env.outbox()
        with env.db() as con:
            con.execute("UPDATE robot_items SET state='ready', attempts=0, finished_at=NULL WHERE seq=2")
        env.clock.advance(3600)
        self.assertEqual(env.run(), 1)
        self.assertEqual(env.outbox(), before)
        self.assertEqual(len(env.replies()), 5)


class LeaseTests(unittest.TestCase):
    def test_expired_lease_is_taken_at_lease_until(self):
        env = RobotEnv(self)
        env.deliver(message(M1, "HELP"))
        with self.assertRaises(Crash):
            env.run(fault=fault_once("claim_inbox.after"))
        row = env.inbox(M1)
        self.assertEqual(row["state"], "processing")
        self.assertEqual(row["lease_until"], "2026-11-25T04:33:10.000000Z")
        self.assertRegex(row["lease_token"], r"\A[0-9a-f]{32}\Z")
        env.clock.advance(9)
        self.assertEqual(env.run(), 0)
        env.clock.advance(1)  # now == lease_until: claimable
        self.assertEqual(env.run(), 2)
        self.assertEqual(env.inbox(M1)["attempts"], 2)
        self.assertEqual(len(env.outbox()), 1)

    def test_expired_item_lease_is_taken(self):
        env = RobotEnv(self)
        env.deliver(message(M1, "HELP"))
        with self.assertRaises(Crash):
            env.run(fault=fault_once("claim_item.after"))
        self.assertEqual(env.items(M1)[0]["state"], "processing")
        self.assertEqual(env.run(), 0)
        env.clock.advance(10)
        self.assertEqual(env.run(), 1)
        self.assertEqual((env.items(M1)[0]["state"], env.items(M1)[0]["attempts"]), ("done", 2))

    def test_stale_dispatch_cannot_commit(self):
        env = RobotEnv(self)
        env.deliver(message(M1, CRASH_BODY))
        other = {}

        def takeover():
            env.clock.advance(11)
            other["count"] = env.robot().run_until_idle()
        robot = env.robot(fault=fault_once("dispatch.before", action=takeover))
        self.assertTrue(robot.run_once())
        self.assertEqual(other["count"], 5)
        self.assertEqual(robot.run_until_idle(), 0)
        self.assertEqual(len(env.outbox()), 5)
        self.assertEqual(len(env.items(M1)), 4)
        self.assertEqual(len(env.replies()), 5)
        self.assertEqual(env.inbox(M1)["attempts"], 2)

    def test_stale_item_cannot_commit(self):
        env = RobotEnv(self)
        env.deliver(message(M1, CRASH_BODY))
        env.robot().run_once()  # dispatch only

        def takeover():
            env.clock.advance(11)
            env.robot().run_until_idle()
        robot = env.robot(fault=fault_once("item.before", action=takeover))
        self.assertTrue(robot.run_once())
        self.assertEqual(len(env.outbox()), 5)
        self.assertEqual(len(env.replies()), 5)
        self.assertEqual(env.items(M1)[0]["attempts"], 2)

    def test_expired_lease_without_takeover_cannot_commit(self):
        env = RobotEnv(self)
        env.deliver(message(M1, "HELP"))
        env.robot().run_once()
        robot = env.robot(fault=fault_once("item.before", action=lambda: env.clock.advance(10)))
        self.assertTrue(robot.run_once())
        self.assertEqual(env.outbox(), [])
        self.assertEqual(env.items(M1)[0]["state"], "processing")
        self.assertEqual(env.run(), 1)
        self.assertEqual(len(env.outbox()), 1)

    def test_live_lease_is_respected(self):
        env = RobotEnv(self)
        env.deliver(message(M1, CRASH_BODY))
        env.robot().run_once()
        seen = {}

        def other_worker():
            seen["count"] = env.robot().run_until_idle()
            seen["first"] = env.items(M1)[0]["state"]
        robot = env.robot(fault=fault_once("item.before", action=other_worker))
        self.assertTrue(robot.run_once())
        self.assertEqual(seen, {"count": 3, "first": "processing"})
        self.assertEqual([row["state"] for row in env.items(M1)], ["done"] * 4)
        self.assertEqual(len(env.outbox()), 5)


class InternalErrorTests(unittest.TestCase):
    def test_exception_in_build_gives_error_reply(self):
        env = RobotEnv(self)
        env.deliver(message(M1, "HELP\r\nLIST\r\n"))
        with self.assertLogs("federated_cms.robot", level="DEBUG") as logs:
            env.run(fault=fault_once("item.build", exception=lambda point: ValueError("secret-detail")))
        out = env.outbox()
        item = env.items(M1)[0]
        self.assertEqual(out[0][1], expected_raw(mid_for(1), "INQUIRY - HELP: error", body_text(
            f"Could not process this item (ref {item['id']}).")))
        self.assertEqual(subject_of(out[1][1]), "INQUIRY: LIST")
        self.assertEqual((item["state"], item["error"]), ("failed", "internal_error"))
        self.assertEqual(env.replies()[0]["kind"], "error")
        text = "\n".join(logs.output)
        self.assertIn("ValueError", text)
        self.assertNotIn("secret-detail", text)

    def test_base_exception_is_not_swallowed(self):
        env = RobotEnv(self)
        env.deliver(message(M1, "HELP"))
        with self.assertRaises(KeyboardInterrupt):
            env.run(fault=fault_once("item.build", exception=KeyboardInterrupt))
        self.assertEqual(env.outbox(), [])
        self.assertEqual(env.items(M1)[0]["state"], "processing")

    def test_exception_elsewhere_propagates(self):
        env = RobotEnv(self)
        env.deliver(message(M1, "HELP"))
        with self.assertRaises(ValueError):
            env.run(fault=fault_once("dispatch.before", exception=ValueError))
        self.assertEqual(env.inbox(M1)["state"], "processing")


# ---------------------------------------------------------------------------
# Hostile input
# ---------------------------------------------------------------------------

class HostileTests(unittest.TestCase):
    def setUp(self):
        self.env = RobotEnv(self)

    def error_reason(self, inbound):
        raw = dict(self.env.outbox())[reply_mid(SALT, "N0CALL", inbound, 0, 1)]
        return subject_of(raw)

    def test_huge_body(self):
        self.env.deliver(message("HUGE1", "LIST " * 12000))
        self.env.run()
        self.assertEqual(self.error_reason("HUGE1"), "INQUIRY - REQUEST: body too large")

    def test_ten_thousand_lines(self):
        self.env.deliver(message("LINES1", "LIST\n" * 10000))
        self.env.run()
        self.assertEqual(self.error_reason("LINES1"), "INQUIRY - REQUEST: body too large")
        self.assertEqual(len(self.env.outbox()), 1)

    def test_four_thousand_bad_lines_give_one_bounded_reply(self):
        self.env.deliver(message("MANY1", "!\n" * 4000))
        self.env.run()
        out = self.env.outbox()
        self.assertEqual(len(out), 1)
        lines = ["These lines were not understood:"]
        lines += [f"Line {n}: ! (invalid item)" for n in range(1, 21)]
        lines += ["...and 3980 more.", "", *HELP_LINES]
        mid = reply_mid(SALT, "N0CALL", "MANY1", 0, 1)
        self.assertEqual(out[0], (mid, expected_raw(mid, "INQUIRY - REQUEST: lines not understood",
                                                    body_text(*lines))))

    def test_exactly_twenty_rejected_lines_have_no_more_line(self):
        self.env.deliver(message("TWENTY", "!\n" * 20))
        self.env.run()
        body = parse_reply(self.env.outbox()[0][1])[1]
        self.assertIn("Line 20: ! (invalid item)\r\n\r\nSend requests", body)
        self.assertNotIn("more.", body)

    def test_too_many_items(self):
        ids = "".join(f"ITEM{n:02d}\n" for n in range(40))
        self.env.deliver(message("MAXI", ids))
        self.env.run()
        self.assertEqual(len(self.env.items("MAXI")), 32)
        out = self.env.outbox()
        self.assertEqual(len(out), 33)
        body = parse_reply(out[0][1])[1]
        self.assertIn("Line 33: ITEM32 (too many items)\r\n", body)
        self.assertIn("Line 40: ITEM39 (too many items)\r\n", body)

    def test_long_unknown_id_subject(self):
        item = "Z" * 64
        self.env.deliver(message("LONG1", item))
        self.env.run()
        raw = self.env.outbox()[0][1]
        self.assertEqual(subject_of(raw), "INQUIRY - " + "Z" * 56 + "...: not found")
        self.assertIn(f"No item {item} on this node.".encode(), raw)

    def test_long_help_argument(self):
        argument = "x" * 200
        self.env.deliver(message("LONG2", "HELP " + argument))
        self.env.run()
        raw = self.env.outbox()[0][1]
        # 10 + 11 (": not found") = 21; label "HELP " + 200 x cut to 56, then "...".
        self.assertEqual(subject_of(raw), "INQUIRY - HELP " + "x" * 51 + "...: not found")
        self.assertIn(f"No service {argument} on this node.".encode(), raw)

    def test_long_url_subject(self):
        url = "https://example.org/" + "p" * 300
        self.env.deliver(message("LONG3", url))
        self.env.run()
        subject = subject_of(self.env.outbox()[0][1])
        # 80 - 10 - len(": not available") 15 - 3 = 52 characters of the URL.
        self.assertEqual(subject, "INQUIRY - " + url[:52] + "...: not available")

    def test_replies_are_printable_ascii(self):
        body = "LIST\r\nété\r\n\x1b[31mRED\r\nA\x00B\r\n X\r\nHELP é\r\n"
        self.env.deliver(message("ASCII1", body))
        self.env.run()
        for _, raw in self.env.outbox():
            self.assertTrue(re.fullmatch(rb"[\x20-\x7e\r\n]*", raw), raw[:200])
            text = raw.split(b"\r\n\r\n", 1)[1]
            self.assertNotIn(b"\r\r", text)
            self.assertEqual(text.count(b"\r"), text.count(b"\n"))

    def test_logs_never_hold_content(self):
        with self.assertLogs("federated_cms.robot", level="DEBUG") as logs:
            self.env.deliver(message("LOG1", "HELP SECRETARG\r\nSECRETLINE!\r\nhttps://example.org/SECRETURL\r\n",
                                     extra=("X-Note: SECRETHEADER",)))
            self.env.deliver(message("LOG2", "SECRETBODY", subject="SECRETSUBJECT"))
            self.env.run()
        self.assertNotIn("SECRET", "\n".join(logs.output))

    def test_non_ascii_header_bytes(self):
        raw = message("HDR1", "HELP", sender="N0CALL").replace(b"From: N0CALL", b"From: N0\xff\xfeCALL")
        self.env.deliver(raw)
        self.env.run()
        self.assertEqual(self.env.inbox("HDR1")["state"], "done")
        self.assertEqual(len(self.env.outbox()), 1)

    def test_missing_inbound_row(self):
        # A queue row whose stored message vanished fails instead of crashing the worker.
        con = self.env.db()
        robot_module.enqueue_inbound(con, "N0CALL", "GHOST1", message("GHOST1", "HELP"), T0)
        con.close()
        self.env.deliver(message("REAL1", "HELP"))
        self.env.run()
        self.assertEqual((self.env.inbox("GHOST1")["state"], self.env.inbox("GHOST1")["reason"]),
                         ("failed", "missing"))
        self.assertEqual(self.env.inbox("REAL1")["state"], "done")
        self.assertEqual(self.env.query("SELECT * FROM robot_counters"), [])  # no From to compare


def log_events(logs) -> list[tuple[str, str, set[str]]]:
    events = []
    for record in logs.records:
        tokens = record.getMessage().split()
        if tokens and tokens[0].startswith("event="):
            events.append((tokens[0][6:], record.levelname, set(tokens[1:])))
    return events


def levels(events, name, *required) -> list[str]:
    return [level for event, level, tokens in events if event == name and set(required) <= tokens]


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.env = RobotEnv(self)

    def test_dispatch_and_item_events(self):
        self.env.deliver(message(M1, "HELP"))
        self.env.deliver(message("LOOP1", "HELP", sender="SERVICE"))
        with self.assertLogs("federated_cms.robot", level="DEBUG") as logs:
            self.env.run()
        events = log_events(logs)
        item = self.env.items(M1)[0]["id"]
        self.assertEqual(levels(events, "dispatch", "account=N0CALL", f"mid={M1}", "outcome=done"), ["INFO"])
        self.assertEqual(levels(events, "dispatch", "account=N0CALL", "mid=LOOP1", "outcome=loop_name"), ["INFO"])
        self.assertEqual(levels(events, "item", "account=N0CALL", f"mid={M1}", f"item={item}", "kind=help",
                                "outcome=done"), ["INFO"])

    def test_stale_item_error_and_attempts_events(self):
        self.env.deliver(message(M1, "HELP\r\nLIST"))
        self.env.robot().run_once()  # dispatch
        items = [row["id"] for row in self.env.items(M1)]
        with self.assertLogs("federated_cms.robot", level="DEBUG") as logs:
            self.env.robot(fault=fault_once("item.before", action=lambda: self.env.clock.advance(10))).run_once()
            self.env.robot(fault=fault_once("item.build", exception=lambda p: KeyError("x"))).run_once()
        events = log_events(logs)
        self.assertEqual(levels(events, "stale", "account=N0CALL", f"mid={M1}", f"item={items[0]}"), ["INFO"])
        self.assertEqual(levels(events, "item_error", "account=N0CALL", f"mid={M1}", f"item={items[0]}",
                                "error_class=KeyError"), ["WARNING"])

        def always(name):
            if name == "item.before":
                raise Crash(name)
        with self.assertLogs("federated_cms.robot", level="DEBUG") as logs:
            for _ in range(4):
                try:
                    self.env.run(fault=always)
                except Crash:
                    self.env.clock.advance(11)
        self.assertEqual(levels(log_events(logs), "attempts", "account=N0CALL", f"mid={M1}", f"item={items[1]}"),
                         ["WARNING"])

    def test_inbox_attempts_event(self):
        self.env.deliver(message(M1, "HELP"))

        def always(name):
            if name == "dispatch.before":
                raise Crash(name)
        for _ in range(3):
            with self.assertRaises(Crash):
                self.env.run(fault=always)
            self.env.clock.advance(11)
        with self.assertLogs("federated_cms.robot", level="DEBUG") as logs:
            self.env.robot().run_once()
        self.assertEqual(levels(log_events(logs), "attempts", "account=N0CALL", f"mid={M1}"), ["WARNING"])

    def test_stale_dispatch_event(self):
        self.env.deliver(message(M1, "HELP"))
        with self.assertLogs("federated_cms.robot", level="DEBUG") as logs:
            self.env.robot(fault=fault_once("dispatch.before", action=lambda: self.env.clock.advance(10))).run_once()
        self.assertEqual(levels(log_events(logs), "stale", "account=N0CALL", f"mid={M1}"), ["INFO"])


class ListTruncationTests(unittest.TestCase):
    HEADER = "Catalog of N1TEST-10. Send an ID to INQUIRY with subject REQUEST."

    def list_body(self, count, description):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "one.txt").write_bytes(b"One.\n")
        rows = "".join(f"ITEM{n:04d}\tBULK\ttext:one.txt\t{description}\n" for n in range(1, count + 1))
        (root / "catalog.tsv").write_bytes(rows.encode("ascii"))
        (root / "robot.toml").write_bytes(b'[node]\nname = "N1TEST-10"\ncontact = "sysop@example.org"\n'
                                          b'[catalog]\nfile = "catalog.tsv"\n')
        env = RobotEnv(self, root / "robot.toml")
        env.deliver(message(M1, "LIST"))
        env.run()
        return parse_reply(env.outbox()[0][1])[1].replace("\r\n", "\n")

    def test_list_cut_at_12000_characters(self):
        description = "D" * 70
        body = self.list_body(200, description)
        # Content characters, each line with its "\n": header 65 + 1, blank 1,
        # "BULK" 4 + 1, then 81 per entry (8 + 2 + 70 + 1). The cut note adds a
        # blank line (1) and "Items not shown: N." (20 + 1 for a two-digit N).
        # k = 146: 72 + 146 * 81 + 1 + 21 = 11920 <= 12000.
        # k = 147: 72 + 147 * 81 + 1 + 21 = 12001 > 12000. So k = 146, N = 54.
        k = 146
        expected = [self.HEADER, "", "BULK"] + [f"ITEM{n:04d}  {description}" for n in range(1, k + 1)]
        expected += ["", f"Items not shown: {200 - k}."]
        self.assertEqual(body, body_text(*expected))
        self.assertEqual(len("\n".join(expected) + "\n"), 11920)

    def test_exactly_12000_characters_is_not_cut(self):
        # 147 entries: 126 with 70-character descriptions (81 per line) and 21
        # with 71 (82 per line): 72 + 126 * 81 + 21 * 82 = 72 + 10206 + 1722 = 12000.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "one.txt").write_bytes(b"One.\n")
        descriptions = ["D" * 70] * 126 + ["E" * 71] * 21
        rows = "".join(f"ITEM{n:04d}\tBULK\ttext:one.txt\t{d}\n" for n, d in enumerate(descriptions, 1))
        (root / "catalog.tsv").write_bytes(rows.encode("ascii"))
        (root / "robot.toml").write_bytes(b'[node]\nname = "N1TEST-10"\ncontact = "sysop@example.org"\n'
                                          b'[catalog]\nfile = "catalog.tsv"\n')
        env = RobotEnv(self, root / "robot.toml")
        env.deliver(message(M1, "LIST"))
        env.run()
        body = parse_reply(env.outbox()[0][1])[1].replace("\r\n", "\n")
        expected = [self.HEADER, "", "BULK"] + [f"ITEM{n:04d}  {d}" for n, d in enumerate(descriptions, 1)]
        self.assertEqual(len("\n".join(expected) + "\n"), 12000)
        self.assertEqual(body, body_text(*expected))

    def test_one_character_over_is_cut(self):
        description = "D" * 68
        body = self.list_body(151, description)
        # All 151 entries: 72 + 151 * 79 (8 + 2 + 68 + 1) = 12001 > 12000, so
        # the list is cut. k = 150: 72 + 150 * 79 + 1 + 19 ("Items not shown: 1."
        # 19 + 1) + 1 = 11943.
        expected = [self.HEADER, "", "BULK"] + [f"ITEM{n:04d}  {description}" for n in range(1, 151)]
        expected += ["", "Items not shown: 1."]
        self.assertEqual(body, body_text(*expected))
        self.assertEqual(len("\n".join(expected) + "\n"), 11943)


if __name__ == "__main__":
    unittest.main()
