"""Offline tests for the explicitly synthetic shared lab mailbox service."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from federated_cms import CMSCore, SQLiteStore
from federated_cms.lab_mailbox import LabMailboxBusy, LabSessionExpired
from federated_cms.b2_message import B2MessageError
from federated_cms.store import StoreUnavailable


def b2(mid: str, body: bytes) -> bytes:
    return f"Mid: {mid}\r\nBody: {len(body)}\r\n\r\n".encode("ascii") + body


class LabMailboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "mail.sqlite"
        self.store = SQLiteStore(self.path)
        self.core = CMSCore(self.store)
        self.mailbox = self.core.lab_mailbox()

    def test_reopen_retains_exact_bytes_and_ack(self):
        raw = b"Mid: TEST123\r\nBody: 3\r\nFile: 3 demo.bin\r\n\r\nhi!\r\n\x00\xff\x01\r\n"
        out_raw = raw.replace(b"TEST123", b"TEST456", 1)
        session = self.mailbox.open_session("test1")
        self.assertTrue(self.mailbox.store_message(session, "TEST123", raw, "in"))
        self.assertTrue(self.mailbox.store_message(session, "TEST456", out_raw, "out"))
        self.mailbox.close_session(session)
        reopened = CMSCore(SQLiteStore(self.path)).lab_mailbox()
        session = reopened.open_session("TEST1")
        self.assertEqual(reopened.get(session, "TEST123", "in"), raw)
        self.assertEqual(reopened.list_incoming(session), [("TEST123", raw)])
        self.assertEqual(reopened.pending(session), [("TEST456", out_raw)])
        self.assertTrue(reopened.ack(session, "TEST456"))
        self.assertFalse(reopened.ack(session, "TEST456"))
        reopened.close_session(session)
        again = CMSCore(SQLiteStore(self.path)).lab_mailbox()
        session = again.open_session("TEST1")
        self.assertEqual(again.pending(session), [])
        again.close_session(session)

    def test_account_isolation_and_exclusive_session_across_instances(self):
        first = self.mailbox.open_session("TEST1")
        other_instance = CMSCore(SQLiteStore(self.path)).lab_mailbox()
        with self.assertRaises(LabMailboxBusy):
            other_instance.open_session("test1")
        second = other_instance.open_session("TEST2")
        raw = b2("TEST123", b"account one")
        self.mailbox.store_message(first, "TEST123", raw, "out")
        self.assertIsNone(other_instance.get(second, "TEST123", "out"))
        self.assertEqual(other_instance.pending(second), [])
        self.assertFalse(other_instance.ack(second, "TEST123"))
        self.assertEqual(self.mailbox.pending(first), [("TEST123", raw)])
        self.mailbox.close_session(first)
        other_instance.close_session(second)

    def test_expired_session_cannot_ack_after_takeover(self):
        with patch("federated_cms.lab_mailbox.time.time", return_value=1000):
            old = self.mailbox.open_session("TEST1")
            self.mailbox.store_message(old, "TEST123", b2("TEST123", b"pending"), "out")
        with patch("federated_cms.lab_mailbox.time.time", return_value=1120):
            replacement = self.mailbox.open_session("TEST1")
            with self.assertRaises(LabSessionExpired):
                self.mailbox.ack(old, "TEST123")
            self.mailbox.close_session(old)  # must not close the replacement
            self.assertEqual(self.mailbox.pending(replacement), [("TEST123", b2("TEST123", b"pending"))])
            self.assertTrue(self.mailbox.ack(replacement, "TEST123"))
            self.mailbox.close_session(replacement)

    def test_duplicate_collision_and_legacy_data_isolation(self):
        self.store.b2f_lab_store("TEST1", "OLDMID", b"legacy bytes", "in")
        session = self.mailbox.open_session("TEST1")
        self.assertIsNone(self.mailbox.get(session, "OLDMID", "in"))
        self.assertTrue(self.mailbox.store_message(session, "NEWID", b2("NEWID", b"first"), "in"))
        self.assertFalse(self.mailbox.store_message(session, "NEWID", b2("NEWID", b"first"), "in"))
        with self.assertRaises(ValueError):
            self.mailbox.store_message(session, "NEWID", b2("NEWID", b"different"), "in")
        self.assertEqual(self.mailbox.get(session, "NEWID", "in"), b2("NEWID", b"first"))
        self.assertEqual(self.store.b2f_lab_get("TEST1", "OLDMID", "in"), b"legacy bytes")
        self.mailbox.close_session(session)

    def test_closed_session_has_no_access(self):
        session = self.mailbox.open_session("TEST1")
        self.mailbox.close_session(session)
        with self.assertRaises(LabSessionExpired):
            self.mailbox.list_incoming(session)

    def test_pending_excludes_deferred_ids_before_batch_limit(self):
        session = self.mailbox.open_session("TEST1")
        for index in range(10):
            self.mailbox.store_message(session, f"MID{index}", b2(f"MID{index}", f"body{index}".encode()), "out")
        self.assertEqual([mid for mid, _ in self.mailbox.pending(session)], [f"MID{i}" for i in range(5)])
        excluded = {f"MID{i}" for i in range(7)}
        self.assertEqual(self.mailbox.pending(session, exclude=excluded), [(f"MID{i}", b2(f"MID{i}", f"body{i}".encode())) for i in range(7, 10)])
        self.assertEqual(len(self.mailbox.pending(session)), 5)  # exclusions never mark delivery
        self.mailbox.close_session(session)

    def test_unavailable_store_fails_closed_without_acknowledging(self):
        session = self.mailbox.open_session("TEST1")
        self.mailbox.store_message(session, "TEST123", b2("TEST123", b"pending"), "out")
        self.store.path = str(self.path.parent)  # a directory cannot be opened as a database
        try:
            with self.assertRaises(StoreUnavailable):
                self.mailbox.ack(session, "TEST123")
        finally:
            self.store.path = str(self.path)
        self.assertEqual(self.mailbox.pending(session), [("TEST123", b2("TEST123", b"pending"))])
        self.mailbox.close_session(session)

    def test_insert_rejects_malformed_and_mismatched_b2(self):
        session = self.mailbox.open_session("TEST1")
        with self.assertRaises(B2MessageError):
            self.mailbox.store_message(session, "TEST123", b"not a B2 message", "out")
        with self.assertRaises(ValueError):
            self.mailbox.store_message(session, "TEST123", b2("OTHER", b"body"), "out")
        self.assertEqual(self.mailbox.pending(session), [])
        self.mailbox.close_session(session)
