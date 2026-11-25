"""Offline attachment verification for the synthetic B2F lab mailbox."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from federated_cms import SQLiteStore
from tests.lab_mailbox_helpers import mailbox_call
from tests.verify_lab_attachments import main


class VerifyLabAttachmentsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = self.root / "seed # lab.sqlite"
        self.binary = self.root / "test bytes.bin"
        self.empty = self.root / "empty.bin"
        self.data = b"\x00\xff\r\n\r\n\x00\n"
        self.binary.write_bytes(self.data)
        self.empty.touch()
        raw = (
            b"Mid: TEST123\r\nBody: 4\r\n"
            + f"File: {len(self.data)} test bytes.bin\r\nFile: 0 empty.bin\r\n\r\n".encode()
            + b"body\r\n" + self.data + b"\r\n\r\n\x00"
        )
        store = SQLiteStore(self.database)
        mailbox_call(store, "N0CALL", "store_message", "TEST123", raw, "in")
        mailbox_call(store, "N0CALL", "store_message", "OUTONLY", raw.replace(b"TEST123", b"OUTONLY", 1), "out")

    def verify(self, *files, database=None, account="n0call", mid="TEST123"):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main([str(database or self.database), account, mid, *map(str, files)])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_verifies_binary_and_empty_attachments_with_express_terminator(self):
        before = self.database.read_bytes()
        self.assertEqual(self.verify(self.binary, self.empty), (
            0, "Verified 2 incoming lab attachment(s) byte-for-byte.\n", "",
        ))
        self.assertTrue(self.database.read_bytes() == before)

    def test_reports_equal_length_and_extra_byte_mismatches_without_contents(self):
        for changed in (b"X" + self.data[1:], self.data + b"X"):
            with self.subTest(length=len(changed)):
                self.binary.write_bytes(changed)
                self.assertEqual(self.verify(self.binary, self.empty), (
                    1, "", "An attachment differs from its fixture bytes.\n",
                ))

    def test_requires_all_attachments_in_header_order(self):
        self.assertEqual(self.verify(self.binary), (
            1, "", "Attachment count differs from the fixture count.\n",
        ))
        self.assertEqual(self.verify(self.empty, self.binary), (
            1, "", "An attachment name differs from its fixture basename.\n",
        ))

    def test_limits_the_lookup_to_the_incoming_mid_and_account(self):
        for account, mid in (("N0CALL", "OUTONLY"), ("OTHER", "TEST123")):
            with self.subTest(account=account, mid=mid):
                self.assertEqual(self.verify(self.binary, self.empty, account=account, mid=mid), (
                    1, "", "Incoming lab message was not found.\n",
                ))

    def test_missing_database_is_left_absent(self):
        missing = self.root / "missing.sqlite"
        self.assertEqual(self.verify(self.binary, self.empty, database=missing), (
            1, "", "Unable to read or validate the lab message or fixtures.\n",
        ))
        self.assertFalse(missing.exists())
