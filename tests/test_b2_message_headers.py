"""Contract tests for b2_message.message_headers (docs/INQUIRY-ROBOT-SLICE1.md,
"Reading a message"), and a regression check that message_mid and
message_attachments are unchanged. Do not change them to make an
implementation pass.
"""
import unittest

from federated_cms.b2_message import B2MessageError, message_attachments, message_headers, message_mid

# Valid fixtures from tests/test_b2f_validation.py and tests/test_lab_mailbox.py,
# with the results main gave before message_headers existed.
VALID = [
    (b"Mid: ABC123\r\nBody: 5\r\nFile: 3 x.bin\r\n\r\nhello\r\n\x00\x01\x02\r\n",
     "ABC123", [(b"x.bin", b"\x00\x01\x02")]),
    (b"Mid: ABC123\r\nBody: 5\r\nFile: 3 x.bin\r\n\r\nhello\r\n\x00\x01\x02\r\n\x00",
     "ABC123", [(b"x.bin", b"\x00\x01\x02")]),
    (b"Mid: ABC123\r\nBody: 5\r\nFile: 3 x.bin\r\nFile: 2 y.bin\r\n\r\nhello\r\n\x00\x01\x02\r\nxy\r\n\x00",
     "ABC123", [(b"x.bin", b"\x00\x01\x02"), (b"y.bin", b"xy")]),
    (b"Mid: TEST123\r\nBody: 3\r\nFile: 3 demo.bin\r\n\r\nhi!\r\n\x00\xff\x01\r\n",
     "TEST123", [(b"demo.bin", b"\x00\xff\x01")]),
    (b"mid:  TEST9 \r\nX-Fold: a\r\n  b\r\n\tc\r\nbody: 0\r\n\r\n", "TEST9", []),
]
INVALID = [
    VALID[0][0] + b"\x00\x00",
    VALID[0][0] + b"other",
    b"Mid: ABC123\r\nBody: 0\r\n\r\n\r\n\x00",
    b"Mid: ABC123\r\nBody: 999\r\n\r\nshort",
    b"Mid: ABC123\r\nBody: 5\r\nFile: 4 x\r\n\r\nhello\r\nabc",
    b"Mid: ABC123\r\nBody: -1\r\n\r\n",
    b"Mid: ABC123\r\nBody: 0\r\nBody: 0\r\n\r\n",
    b"Mid: ABC123\r\nBody: 0\r\nBad header\r\n\r\n",
    b"Mid: ABC123\r\nBody: 0\r\n\r\nunexpected",
    b"Mid: \xff\r\nBody: 0\r\n\r\n",
    b"Body: 0\r\n\r\n",
    b"no blank line",
    b"Mid: A\r\nBody: 0\r\n\r\n" + b"x" * 70000,
    "Mid: A\r\nBody: 0\r\n\r\n",
    None,
    bytearray(b"Mid: A\r\nBody: 0\r\n\r\n"),
]


class MessageHeadersTests(unittest.TestCase):
    def test_order_names_values(self):
        raw = (b"Mid: ABC\r\nTo: N0CALL\r\nTO:  INQUIRY  \r\nSubject: REQUEST\r\n"
               b"X-Empty:\r\nBody: 0\r\n\r\n")
        self.assertEqual(message_headers(raw), (
            ("mid", b"ABC"), ("to", b"N0CALL"), ("to", b"INQUIRY"), ("subject", b"REQUEST"),
            ("x-empty", b""), ("body", b"0"),
        ))

    def test_folding_matches_message_sections(self):
        self.assertEqual(message_headers(VALID[4][0]), (("mid", b"TEST9"), ("x-fold", b"a b c"), ("body", b"0")))

    def test_values_are_bytes_strip_only(self):
        raw = b"Mid: A\r\nTo: \x85INQUIRY\xa0 \t\r\nCc: a\nb\rc\r\nBody: 0\r\n\r\n"
        headers = dict(message_headers(raw))
        self.assertEqual(headers["to"], b"\x85INQUIRY\xa0")   # bytes.strip removes ASCII whitespace only
        self.assertEqual(headers["cc"], b"a\nb\rc")           # lines end only at CRLF

    def test_returns_a_tuple_of_pairs(self):
        result = message_headers(VALID[0][0])
        self.assertIsInstance(result, tuple)
        for name, value in result:
            self.assertIsInstance(name, str)
            self.assertIsInstance(value, bytes)

    def test_raises_exactly_when_message_mid_does(self):
        for raw in INVALID:
            with self.subTest(raw=repr(raw)[:50]):
                with self.assertRaises(B2MessageError):
                    message_mid(raw)
                with self.assertRaises(B2MessageError):
                    message_headers(raw)
        for raw, _, _ in VALID:
            with self.subTest(raw=raw[:30]):
                message_headers(raw)

    def test_message_mid_and_attachments_unchanged(self):
        for raw, mid, attachments in VALID:
            with self.subTest(raw=raw[:30]):
                self.assertEqual(message_mid(raw), mid)
                self.assertEqual(message_attachments(raw), attachments)
        for raw in INVALID:
            with self.subTest(raw=repr(raw)[:50]):
                with self.assertRaises(B2MessageError):
                    message_attachments(raw)


if __name__ == "__main__":
    unittest.main()
