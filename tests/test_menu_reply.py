"""Contract tests for reading a client's reply to a numbered menu.

See docs/MENU-REPLY.md. The message layout is the Winlink Express reply
recorded in the interactive-services design ("Reading a reply"), addressed to
the node's service address FCMS instead of the captured SERVICE; every message
here is synthetic (callsign N0CALL). These tests define the behaviour of
federated_cms.menu_reply. Do not change them to make an implementation pass.
"""
import base64
import dataclasses
import quopri
import random
from pathlib import Path
import time
import unittest

from federated_cms.menu_reply import MenuReply, MenuReplyError, parse_menu_reply

FIXTURES = Path(__file__).parent / "fixtures" / "menu_reply"

SUBJECT = "Re: FCMS - WIKI Mercury: choose [K7QX]"
TO = "FCMS"
SEP = b"----- Message from FCMS sent 2026/11/25 00:00 -----"
ORIGINAL = (b"Message ID: 3NK9WQ2XJ7ST\nDate: 2026/11/25 00:00  (UTC)\nFrom: FCMS\nTo: N0CALL\n"
            b"Subject: FCMS - WIKI Mercury: choose [K7QX]\n\n"
            b"1. Mercury (planet)\n2. Mercury (element)\n3. 9\nEach: WIKI\xc2\xa0<its title>\n")
TEXT = 'text/plain; charset="iso-8859-1"'


def crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def qp(data: bytes) -> bytes:
    return crlf(quopri.encodestring(data.replace(b"\r\n", b"\n")))


def b64(data: bytes) -> bytes:
    return crlf(base64.encodebytes(data))


def head(subject=SUBJECT, to=TO, extra=()):
    lines = []
    if subject is not None:
        lines.append(subject if isinstance(subject, bytes) else subject.encode("ascii"))
        lines[-1] = b"Subject: " + lines[-1]
    if to is not None:
        lines.append(b"To: " + (to if isinstance(to, bytes) else to.encode("utf-8")))
    lines += [b"Message-ID: <6B3QZ8NR4TWM>", b"X-Location: 41.729167N, 72.708333W (Grid square)"]
    lines += [line if isinstance(line, bytes) else line.encode("utf-8") for line in extra]
    return b"\r\n".join(lines) + b"\r\n"


def part(body: bytes, ctype=TEXT, cte=None, extra=()):
    lines = [b"Content-Type: " + ctype.encode("ascii")] if ctype else []
    if cte:
        lines.append(b"Content-Transfer-Encoding: " + cte.encode("ascii"))
    lines += [line.encode("ascii") for line in extra]
    return b"\r\n".join(lines) + b"\r\n\r\n" + body


def multi(parts, subtype="mixed", boundary="XBOUND"):
    out = b"".join(b"--" + boundary.encode() + b"\r\n" + p + b"\r\n" for p in parts)
    return (f'multipart/{subtype}; boundary="{boundary}"', out + b"--" + boundary.encode() + b"--\r\n")


def message(body: bytes, *, ctype=None, cte=None, subject=SUBJECT, to=TO, extra=()):
    """Single-part message (top-level headers describe the body)."""
    top = list(extra)
    if ctype:
        top.append("Content-Type: " + ctype)
    if cte:
        top.append("Content-Transfer-Encoding: " + cte)
    return head(subject, to, top) + b"\r\n" + body


def wrapped(parts, subtype="mixed", **kwargs):
    ctype, body = multi(parts, subtype)
    return message(body, ctype=ctype, **kwargs)


def express(choice="2", *, original=ORIGINAL, **kwargs):
    """The observed Express shape: multipart/mixed, one quoted-printable text part."""
    text = choice.encode("utf-8") + b"\n" + SEP + b"\n\n" + original
    return wrapped([part(qp(text), TEXT, "quoted-printable")], **kwargs)


def plain(choice_text: bytes, **kwargs):
    """Single 7bit/8bit text/plain message holding raw bytes."""
    return message(choice_text, ctype=TEXT, cte="8bit", **kwargs)


def nested_part(depth: int, text: bytes = b"2\r\n") -> bytes:
    """A text part inside `depth` multipart containers, as one MIME entity."""
    inner = part(text, "text/plain", None)
    for level in reversed(range(depth)):
        inner = (f'Content-Type: multipart/mixed; boundary="b{level}"\r\n\r\n--b{level}\r\n'.encode()
                 + inner + f"\r\n--b{level}--\r\n".encode())
    return inner


def nested(depth: int) -> bytes:
    """A message whose text part is inside `depth` containers (the top level counts as one)."""
    return head() + nested_part(depth)


class Base(unittest.TestCase):
    def assertReply(self, data, code="K7QX", numbers=(2,), more=False):
        self.assertEqual(parse_menu_reply(data), MenuReply(code, tuple(numbers), more))

    def assertReason(self, data, reason):
        with self.assertRaises(MenuReplyError) as caught:
            parse_menu_reply(data)
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(str(caught.exception), reason)


class ResultTypeTests(Base):
    def test_error_class(self):
        self.assertTrue(issubclass(MenuReplyError, ValueError))
        error = MenuReplyError("no code")
        self.assertEqual(error.reason, "no code")
        self.assertEqual(str(error), "no code")

    def test_menu_reply_is_an_immutable_value(self):
        reply = MenuReply("K7QX", (2,), False)
        self.assertEqual(reply, MenuReply(code="K7QX", numbers=(2,), more=False))
        self.assertEqual([f.name for f in dataclasses.fields(MenuReply)], ["code", "numbers", "more"])
        with self.assertRaises(Exception):
            reply.code = "AAAA"
        self.assertIsInstance(parse_menu_reply(express("2 3")).numbers, tuple)

    def test_non_bytes_input_is_a_type_error(self):
        for value in ("Subject: x", None, 5):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    parse_menu_reply(value)


class FixtureTests(Base):
    def test_observed_express_reply(self):
        data = (FIXTURES / "express_reply.eml").read_bytes()
        self.assertIn(b"=C2=A0", data)  # UTF-8 in a part declared iso-8859-1
        self.assertIn(b"iso-8859-1", data)
        self.assertNotIn(b"MIME-Version", data)
        self.assertReply(data, "K7QX", (2,))

    def test_fixture_without_the_separator_would_read_the_quoted_menu(self):
        data = (FIXTURES / "express_reply.eml").read_bytes()
        broken = data.replace(b"----- Message from", b"----- Message FROM")
        self.assertNotEqual(broken, data)
        # Without a separator the quoted original is read as choices; its UTF-8
        # non-breaking space is seen before any token is judged.
        self.assertReason(broken, "non-ASCII")

    def test_base64_fixture(self):
        self.assertReply((FIXTURES / "base64_reply.eml").read_bytes(), "K7QX", (1, 3, 4))

    def test_html_only_fixture(self):
        self.assertReason((FIXTURES / "html_only.eml").read_bytes(), "no text part")

    def test_fixture_is_deterministic_and_independent_of_line_endings(self):
        data = (FIXTURES / "express_reply.eml").read_bytes()
        self.assertEqual(parse_menu_reply(data), parse_menu_reply(data.replace(b"\r\n", b"\n")))


def rfc822_chain(count: int) -> bytes:
    """`count` nested message/rfc822 entities around a small text message."""
    inner = b"Subject: x\r\nContent-Type: text/plain\r\n\r\n2\r\n"
    for _ in range(count):
        inner = b"Content-Type: message/rfc822\r\n\r\n" + inner
    return inner


class RecipientTests(Base):
    """The reply must be To the node's service address, FCMS unless the caller names another."""

    def test_service_address_forms_accepted(self):
        for to in ("FCMS", "fcms", "FCMS@lab.example", "fcms@Example.ORG", "Fcms@mainepacketradio.org",
                   "FCMS@winlink.org", "SMTP:FCMS@lab.example", "smtp:fcms@lab.example",
                   '"Node Desk" <FCMS@lab.example>', "Bob <bob@example.org>, FCMS@lab.example",
                   "bob@example.org,fcms@x", "FCMS@lab.example, FCMS@other.example", "N0CALL, FCMS"):
            with self.subTest(to=to):
                self.assertReply(express(to=to))

    def test_service_is_not_accepted(self):
        # SERVICE is Winlink's own system address; the node never answers it.
        for to in ("SERVICE", "SERVICE@winlink.org", "service@lab.example", "SMTP:SERVICE@lab.example",
                   "Winlink <service@winlink.org>"):
            with self.subTest(to=to):
                self.assertReason(express(to=to), "no service address")

    def test_other_addresses_rejected(self):
        for to in ("INQUIRY@lab.example", "QTH@lab.example", "SYSTEM", "FCMSS@lab.example", "XFCMS@lab.example",
                   "FCMS.X@lab.example", "FCMS-1@lab.example", "FCMS <bob@example.org>", "F CMS",
                   "N0CALL@lab.example", "bob@fcms.example.org", "", "@lab.example"):
            with self.subTest(to=to):
                self.assertReason(express(to=to), "no service address")

    def test_non_ascii_lookalikes_rejected(self):
        # Long s upper-cases to S in Python; fullwidth, Cyrillic dze and a zero-width space are not ASCII.
        for local in ("FCM\u017f", "fcm\u017f", "\uff26\uff23\uff2d\uff33", "FCM\u0405", "FCMS\u200b"):
            with self.subTest(local=local):
                self.assertReason(express(to=(local + "@lab.example").encode("utf-8")), "no service address")

    def test_only_to_counts(self):
        self.assertReason(express(to="bob@example.org", extra=["Cc: FCMS@lab.example"]), "no service address")
        self.assertReason(express(to="bob@example.org", extra=["Bcc: FCMS@lab.example"]), "no service address")
        self.assertReason(express(to="bob@example.org", extra=["Reply-To: FCMS@lab.example"]), "no service address")
        self.assertReason(express(to="bob@example.org", extra=["From: FCMS@lab.example"]), "no service address")

    def test_to_with_cc_accepted_cc_alone_is_not(self):
        self.assertReply(express(to="FCMS", extra=["Cc: bob@example.org"]))
        self.assertReply(express(to="FCMS@lab.example", extra=["Cc: FCMS@lab.example"]))
        for cc in ("FCMS", "fcms@lab.example", "SMTP:FCMS@lab.example"):
            with self.subTest(cc=cc):
                self.assertReason(express(to="N0CALL", extra=["Cc: " + cc]), "no service address")

    def test_missing_or_garbage(self):
        self.assertReason(express(to=None), "no service address")
        self.assertReason(b"", "no service address")
        self.assertReason(b"\r\n\r\n2\r\n", "no service address")
        self.assertReason(b"this is not a mail message at all", "no service address")
        self.assertReason(bytes(range(256)) * 4, "no service address")

    def test_every_to_header_counts(self):
        self.assertReply(express(to="bob@example.org", extra=["To: FCMS@lab.example"]))
        self.assertReply(express(to="FCMS@lab.example", extra=["To: bob@example.org"]))
        self.assertReply(express(to="bob@example.org", extra=["to: x@y.example", "TO: fcms@z.example"]))
        self.assertReason(express(to="bob@example.org", extra=["To: alice@example.org"]), "no service address")

    def test_encoded_words_are_not_decoded_in_addresses(self):
        # RFC 2047 section 5: encoded words are not allowed in addr-specs. This one decodes to FCMS@x.
        self.assertReason(express(to="=?utf-8?b?RkNNU0B4?="), "no service address")
        self.assertReason(express(to="=?utf-8?q?FCMS?=@lab.example"), "no service address")
        # In a display name it is just a name; the address decides.
        self.assertReply(express(to="=?utf-8?q?Node_Desk?= <FCMS@lab.example>"))
        self.assertReason(express(to="=?utf-8?q?FCMS?= <bob@lab.example>"), "no service address")

    def test_unparseable_to_values_give_no_address_not_a_crash(self):
        for to in (b".?=\xff:\r\n ?=];;", b"\r\n ,a\r\n  >)\r\n <,\"", b"<<<>>>", b"(((", b'"', b",,,,",
                   b"a@b@c@d", b"\x00\x00", b":;", b"FCMS@x:;"):
            with self.subTest(to=to):
                try:
                    result = parse_menu_reply(express(to=to))
                except MenuReplyError as error:
                    self.assertIn(error.reason, ("no service address",))
                else:
                    self.assertIsInstance(result, MenuReply)
        self.assertReason(express(to=b".?=\xff:\r\n ?=];;"), "no service address")
        self.assertReason(express(to=b"\r\n ,a\r\n  >)\r\n <,\""), "no service address")

    def test_one_unparseable_to_header_does_not_hide_another(self):
        self.assertReply(express(to=b".?=\xff:\r\n ?=];;", extra=["To: FCMS@lab.example"]))

    def test_recipient_is_checked_before_the_subject_and_body(self):
        self.assertReason(express("junk", to="bob@example.org", subject="nothing"), "no service address")


class ErrorCodeTests(Base):
    """MenuReplyError.code: the subject's code once step 4 has passed, else None."""

    def code_of(self, data):
        with self.assertRaises(MenuReplyError) as caught:
            parse_menu_reply(data)
        return caught.exception.reason, caught.exception.code

    def test_constructor(self):
        self.assertIsNone(MenuReplyError("no code").code)
        error = MenuReplyError("invalid choice", code="K7QX")
        self.assertEqual((error.reason, error.code, str(error)), ("invalid choice", "K7QX", "invalid choice"))

    def test_errors_after_the_code_carry_it(self):
        cases = [
            (express("WIKI Venus"), "invalid choice"),
            (express("HELP", subject="Re: [test] x"), "invalid choice"),
            (express(""), "empty choice"),
            (express("5-4"), "invalid range"),
            (express("1-21"), "too many choices"),
            (express("caf\u00e9"), "non-ASCII"),
            ((FIXTURES / "html_only.eml").read_bytes(), "no text part"),
            (wrapped([part(b"Mg==\r\n", TEXT, "base64 (comment)")]), "unsupported transfer encoding"),
        ]
        for data, reason in cases:
            with self.subTest(reason=reason):
                expected_code = "TEST" if b"[test]" in data else "K7QX"
                self.assertEqual(self.code_of(data), (reason, expected_code))

    def test_errors_before_the_code_carry_none(self):
        cases = [
            (b"x" * 70000, "too large"),
            (express(extra=["Subject: Re: [AB2C]"]), "malformed message"),
            (express(to="N0CALL"), "no service address"),
            (express(subject="Re: x"), "no code"),
            (express(subject="Re: [K7QI]"), "invalid code"),
            (express(subject="Re: [K7QX] [AB2C]"), "multiple codes"),
        ]
        for data, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(self.code_of(data), (reason, None))


class AddressArgumentTests(Base):
    """The `address` keyword names the node's service address (docs/MENU-REPLY.md, "API")."""

    def test_default_is_fcms(self):
        self.assertEqual(parse_menu_reply(express(to="FCMS")), parse_menu_reply(express(to="FCMS"), address="FCMS"))
        self.assertReason(express(to="NODEDESK"), "no service address")

    def test_configured_address(self):
        for address, to in (("NODEDESK", "nodedesk@lab.example"), ("NODEDESK", "SMTP:NodeDesk@lab.example"),
                            ("RED-CROSS", "red-cross"), ("POLICE-99A", "Police-99A@lab.example"),
                            ("ABC", "abc"), ("ABCDEFGHIJKL", "ABCDEFGHIJKL")):
            with self.subTest(address=address, to=to):
                self.assertEqual(parse_menu_reply(express(to=to), address=address), MenuReply("K7QX", (2,), False))

    def test_configured_address_replaces_fcms(self):
        for to in ("FCMS", "FCMS@lab.example", "SERVICE", "NODEDESKS", "NODE-DESK"):
            with self.subTest(to=to):
                with self.assertRaises(MenuReplyError) as caught:
                    parse_menu_reply(express(to=to), address="NODEDESK")
                self.assertEqual(caught.exception.reason, "no service address")

    def test_address_is_keyword_only(self):
        with self.assertRaises(TypeError):
            parse_menu_reply(express(), "FCMS")

    def test_address_must_be_a_string(self):
        for address in (b"FCMS", None, 5, ["FCMS"]):
            with self.subTest(address=address):
                with self.assertRaises(TypeError):
                    parse_menu_reply(express(), address=address)

    def test_invalid_addresses_are_value_errors(self):
        for address in ("", "fcms", "Fcms", "FC", "ABCDEFGHIJKLM", "FCMS-", "-FCMS", "A-B-C", "FC MS", " FCMS",
                        "FCMS@lab.example", "SMTP:FCMS", "N0CALL", "N0CALL-10", "1FCMS", "FCM\u017f", "FCM\u0405",
                        "INQUIRY", "QTH", "SERVICE", "SYSTEM", "OPTIONS", "POSTMASTER", "MAILER-DAEMON"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError) as caught:
                    parse_menu_reply(express(to="FCMS"), address=address)
                self.assertNotIsInstance(caught.exception, MenuReplyError)

    def test_arguments_are_checked_before_the_message(self):
        with self.assertRaises(TypeError):
            parse_menu_reply("Subject: x", address="SERVICE")
        for data in (b"x" * 70000, b"", express(to="SERVICE")):
            with self.subTest(data=data[:20]):
                with self.assertRaises(ValueError) as caught:
                    parse_menu_reply(data, address="SERVICE")
                self.assertNotIsInstance(caught.exception, MenuReplyError)


class SubjectTests(Base):
    def test_re_prefixes_and_case(self):
        for subject in (SUBJECT, "INQUIRY - WIKI Mercury: choose [K7QX]", "RE: INQUIRY - X [K7QX]",
                        "re:Re:RE: re: INQUIRY - X [K7QX]", "Re : Re  :  INQUIRY - X [K7QX]",
                        "  Re: [K7QX]", "Re:[K7QX]", "[K7QX]", "Re: " * 200 + "[K7QX]",
                        "Re: INQUIRY - WIKI Mercury: choose [k7qx]", "Re: x [K7qX] y"):
            with self.subTest(subject=subject):
                self.assertReply(express(subject=subject))

    def test_bracketed_words_are_codes(self):
        # Whether the account holds such a menu is the robot's check (docs/INQUIRY-ROBOT.md, section 3).
        self.assertReply(express(subject="Re: [TEST] WX"), "TEST")
        self.assertReply(express(subject="[news] today"), "NEWS")

    def test_code_is_returned_upper_case(self):
        self.assertEqual(parse_menu_reply(express(subject="Re: [ab2c]")).code, "AB2C")

    def test_whole_alphabet(self):
        for code in ("2345", "6789", "ABCD", "EFGH", "JKLM", "NPQR", "STUV", "WXYZ", "ZZZZ", "2222", "9999"):
            with self.subTest(code=code):
                self.assertReply(express(subject=f"Re: x [{code}]"), code)

    def test_encoded_word_and_folded_subjects(self):
        self.assertReply(express(subject="=?utf-8?q?Re=3A_INQUIRY_-_WIKI_M=C3=A9rcury_=5BK7QX=5D?="))
        self.assertReply(express(subject="Re: INQUIRY - WIKI\r\n Mercury: choose\r\n [K7QX]"))
        self.assertReply(express(subject="Re: INQUIRY - WIKI M\u00e9rcury [K7QX]".encode("utf-8")))

    def test_missing_code(self):
        for subject in (None, "", "Re: INQUIRY - WIKI Mercury", "Re: K7QX", "Re: (K7QX)", "Re: {K7QX}", "Re: <K7QX>",
                        "Re: [K7Q]", "Re: [K7QXY]", "Re: [ K7QX ]", "Re: [K7Q X]", "Re: []", "Re: [", "Re: K7QX]",
                        "Re: [K7QX", "Re: [[K7QX", "Re: [K7-X]", "Re: [K7_X]", "Re: [K7.X]",
                        "Re: [planet]", "Re: [ABCDE]",
                        "Re: [\uff2b\uff17\uff31\uff38]".encode("utf-8"),   # fullwidth letters and digits
                        "Re: [\u017f234]".encode("utf-8"),                 # long s
                        "Re: [K\u0663QX]".encode("utf-8"),                 # Arabic-Indic digit
                        "Re: [K7\u212aX]".encode("utf-8")):                # Kelvin sign
            with self.subTest(subject=subject):
                self.assertReason(express(subject=subject), "no code")

    def test_codes_outside_the_alphabet(self):
        for code in ("K7QI", "K7QO", "K70X", "K71X", "0000", "1111", "IIII", "OOOO", "I234", "k7qi", "K7Qo", "ABCI",
                     "2O34", "2345".replace("2", "0")):
            with self.subTest(code=code):
                self.assertReason(express(subject=f"Re: INQUIRY - WIKI x [{code}]"), "invalid code")

    def test_multiple_codes(self):
        for subject in ("Re: [K7QX] [K7QX]", "Re: [K7QX] [AB2C]", "Re: [K7QX][AB2C]", "Re: [K7QX] [K7QI]",
                        "Re: [K7QI] [K7QO]", "Re: [Mars] choose [K7QX]", "Re: [k7qx] [K7QX]", "Re: [K7QX] x [1234]"):
            with self.subTest(subject=subject):
                self.assertReason(express(subject=subject), "multiple codes")

    def test_other_bracketed_text_is_ignored(self):
        for subject in ("Re: INQUIRY - WIKI [planet] Mercury: choose [K7QX]", "Re: [ABCDE] [K7QX]", "Re: [K7QX] [AB]",
                        "Re: [ K7QX ] [K7QX]", "Re: [] [K7QX] []"):
            with self.subTest(subject=subject):
                self.assertReply(express(subject=subject))

    def test_the_code_is_not_taken_from_the_body(self):
        # The quoted original contains "[K7QX]" as well.
        self.assertIn(b"[K7QX]", ORIGINAL)
        self.assertReason(express(subject="Re: INQUIRY - WIKI Mercury"), "no code")
        self.assertReply(express(subject="Re: INQUIRY - [AB2C]"), "AB2C")

    def test_duplicate_subject_headers_are_malformed(self):
        data = express(extra=["Subject: Re: [AB2C]"])
        self.assertReason(data, "malformed message")
        self.assertReason(express(extra=["Subject: Re: [K7QX]"]), "malformed message")
        self.assertReason(express(subject="Re: [K7QX]", extra=["subject: x"]), "malformed message")

    def test_subject_checked_before_body(self):
        self.assertReason(express("not a choice", subject="Re: x"), "no code")
        self.assertReason(express("not a choice", subject="Re: [K7QI]"), "invalid code")


class TextPartTests(Base):
    def test_plain_single_part_with_and_without_headers(self):
        self.assertReply(message(b"3\r\n"), numbers=(3,))
        self.assertReply(message(b"3\r\n", ctype="text/plain"), numbers=(3,))
        self.assertReply(message(b"3\r\n", ctype="TEXT/Plain; charset=us-ascii"), numbers=(3,))
        self.assertReply(message(b"3", ctype=None, cte="7bit"), numbers=(3,))
        self.assertReply(head() + b"\r\n3\r\n", numbers=(3,))

    def test_no_mime_version_needed(self):
        self.assertNotIn(b"MIME-Version", express())
        self.assertReply(express())
        self.assertReply(express(extra=["MIME-Version: 1.0"]))

    def test_headers_only_is_an_empty_choice(self):
        self.assertReason(head(), "empty choice")
        self.assertReason(head() + b"\r\n", "empty choice")

    def test_multipart_forms(self):
        text = part(b"4\r\n", "text/plain; charset=us-ascii", "7bit")
        html = part(b"<p>9</p>", "text/html; charset=us-ascii", "7bit")
        blob = part(b"\x00\x01\x02", "application/octet-stream", "binary", ["Content-Disposition: attachment; filename=a.bin"])
        for parts in ([text], [text, html], [html, text], [blob, text], [blob, html, text, blob]):
            for subtype in ("mixed", "alternative", "related", "x-unknown"):
                with self.subTest(parts=len(parts), subtype=subtype, first=parts[0][:20]):
                    self.assertReply(wrapped(parts, subtype), numbers=(4,))

    def test_nested_multipart_is_searched_in_order(self):
        inner_type, inner = multi([part(b"6\r\n", "text/plain", None)], "alternative", "INNER")
        nested_part = part(inner, inner_type)
        later = part(b"9\r\n", "text/plain", None)
        self.assertReply(wrapped([nested_part, later]), numbers=(6,))
        self.assertReply(wrapped([part(b"<b>1</b>", "text/html", None), nested_part]), numbers=(6,))

    def test_only_the_first_text_plain_part_is_used(self):
        empty = part(b"\r\n", "text/plain", None)
        later = part(b"9\r\n", "text/plain", None)
        self.assertReason(wrapped([empty, later]), "empty choice")
        self.assertReply(wrapped([part(b"2\r\n", "text/plain", None), later]), numbers=(2,))
        junk = part(b"junk words\r\n", "text/plain", None)
        self.assertReason(wrapped([junk, later]), "invalid choice")

    def test_attachments_are_skipped(self):
        attached = part(b"5\r\n", "text/plain", None, ["Content-Disposition: attachment; filename=notes.txt"])
        inline = part(b"2\r\n", "text/plain", None, ["Content-Disposition: inline"])
        self.assertReply(wrapped([attached, inline]), numbers=(2,))
        self.assertReason(wrapped([attached]), "no text part")
        self.assertReason(wrapped([attached, attached]), "no text part")
        upper = part(b"5\r\n", "text/plain", None, ["Content-Disposition: ATTACHMENT"])
        self.assertReason(wrapped([upper]), "no text part")

    def test_html_only_messages(self):
        html = part(b"<html><body>2</body></html>", "text/html; charset=utf-8", "7bit")
        self.assertReason(wrapped([html]), "no text part")
        self.assertReason(wrapped([html], "alternative"), "no text part")
        self.assertReason(message(b"<p>2</p>", ctype="text/html"), "no text part")

    def test_no_text_part(self):
        blob = part(b"\x00\x01", "application/octet-stream", "binary")
        image = part(b64(b"GIF89a"), "image/gif", "base64")
        for parts in ([blob], [image, blob], []):
            with self.subTest(parts=len(parts)):
                self.assertReason(wrapped(parts), "no text part")

    def test_embedded_messages_are_not_entered(self):
        inner = b"Subject: x\r\nContent-Type: text/plain\r\n\r\n2\r\n"
        self.assertReason(wrapped([part(inner, "message/rfc822", None)]), "no text part")
        self.assertReason(message(inner, ctype="message/rfc822"), "no text part")
        self.assertReason(wrapped([part(b"2\r\n", "message/partial", None)]), "no text part")

    def test_multipart_without_boundary(self):
        self.assertReason(message(b"2\r\n", ctype="multipart/mixed"), "no text part")

    def test_charset_is_ignored(self):
        for ctype in ('text/plain; charset="utf-16"', "text/plain; charset=utf-7", "text/plain; charset=x-nonsense",
                      'text/plain; charset="iso-8859-1"', "text/plain; charset=", "text/plain; charset"):
            with self.subTest(ctype=ctype):
                self.assertReply(wrapped([part(b"2\r\n" + SEP + b"\r\n\xc3\xa9\r\n", ctype, "8bit")]))

    def test_transfer_encodings(self):
        body = b"2, 4-5\r\n" + SEP + b"\r\n\r\n" + ORIGINAL.replace(b"\n", b"\r\n")
        expected = (2, 4, 5)
        for cte in ("quoted-printable", "Quoted-Printable", "QUOTED-PRINTABLE"):
            with self.subTest(cte=cte):
                self.assertReply(wrapped([part(qp(body), TEXT, cte)]), numbers=expected)
        for cte in ("base64", "BASE64", "Base64"):
            with self.subTest(cte=cte):
                self.assertReply(wrapped([part(b64(body), TEXT, cte)]), numbers=expected)
        for cte in ("7bit", "8bit", "binary", "7BIT", None):
            with self.subTest(cte=cte):
                self.assertReply(wrapped([part(body, TEXT, cte)]), numbers=expected)
        self.assertReply(message(b64(body), ctype=TEXT, cte="base64"), numbers=expected)

    def test_unsupported_transfer_encoding(self):
        for cte in ("x-uuencode", "uuencode", "gzip", "x-foo", "quoted-printable, base64", "8 bit", "base64 (comment)",
                    "quoted-printable (c)", "x-uue", "uue"):
            with self.subTest(cte=cte):
                self.assertReason(wrapped([part(b"2\r\n", TEXT, cte)]), "unsupported transfer encoding")

    def test_quoted_printable_details(self):
        # Soft line breaks and escaped digits are decoded before the lines are read.
        self.assertReply(wrapped([part(b"1-=\r\n3\r\n", TEXT, "quoted-printable")]), numbers=(1, 2, 3))
        self.assertReply(wrapped([part(b"=32 =33\r\n", TEXT, "quoted-printable")]), numbers=(2, 3))
        self.assertReply(wrapped([part(b"2=2C3\r\n", TEXT, "quoted-printable")]), numbers=(2, 3))
        # =0A is a line break after decoding.
        self.assertReply(wrapped([part(b"2=0A3\r\n", TEXT, "quoted-printable")]), numbers=(2, 3))

    def test_base64_details(self):
        self.assertReply(wrapped([part(b64(b"MORE"), TEXT, "base64")]), numbers=(), more=True)
        self.assertReply(wrapped([part(b"Mg==", TEXT, "base64")]), numbers=(2,))
        # get_payload(decode=True) is lenient: padding is repaired, and a body
        # that is not valid base64 at all may come back unchanged.
        self.assertReply(wrapped([part(b"Mg", TEXT, "base64")]), numbers=(2,))
        self.assertReply(wrapped([part(b"2\r\n", TEXT, "base64")]), numbers=(2,))
        self.assertReply(message(b"2\r\n", ctype=TEXT, cte="base64"), numbers=(2,))

    def test_base64_garbage_is_an_error_not_a_crash(self):
        for body in (b"!!!!", b"@@@@\r\n####", b"====", b"M", b"\xff\xfe\xfd\xfc"):
            with self.subTest(body=body):
                with self.assertRaises(MenuReplyError):
                    parse_menu_reply(wrapped([part(body, TEXT, "base64")]))

    def test_base64_body_with_non_ascii_before_the_separator(self):
        self.assertReason(wrapped([part(b64(b"\xc2\xa02\r\n" + SEP), TEXT, "base64")]), "non-ASCII")
        self.assertReason(wrapped([part(b64(b"2\xa0\r\n"), TEXT, "base64")]), "non-ASCII")


class HeaderSemanticsTests(Base):
    """Content type, disposition and transfer encoding are what the standard
    library's get_content_type(), get_content_disposition() and
    get_payload(decode=True) make of the headers, with no extra trimming."""

    def test_content_type_that_is_not_type_slash_subtype_is_text_plain(self):
        self.assertReply(wrapped([part(b"2\r\n", "text/html/extra", None)]))
        self.assertReply(wrapped([part(b"2\r\n", "html", None)]))
        self.assertReply(wrapped([part(b"2\r\n", "text//", None)]))

    def test_comment_after_content_type_is_not_ignored(self):
        self.assertReason(wrapped([part(b"2\r\n", "text/plain (c)", None)]), "no text part")
        self.assertReason(wrapped([part(b"2\r\n", "text/plain (c); charset=us-ascii", None)]), "no text part")

    def test_comment_after_disposition_is_not_ignored(self):
        data = wrapped([part(b"2\r\n", "text/plain", None, ["Content-Disposition: attachment (c)"])])
        self.assertReply(data)
        data = wrapped([part(b"2\r\n", "text/plain", None, ["Content-Disposition: attachment; filename=a.txt"])])
        self.assertReason(data, "no text part")

    def test_comment_after_transfer_encoding_is_not_understood(self):
        data = wrapped([part(b"Mg==\r\n", TEXT, "base64 (comment)")])
        self.assertReason(data, "unsupported transfer encoding")

    def test_empty_transfer_encoding_is_7bit(self):
        self.assertReply(wrapped([part(b"2\r\n", TEXT, "")]))
        self.assertReply(message(b"2\r\n", ctype=TEXT, cte=""))

    def test_digest_children_default_to_message_rfc822(self):
        ctype, body = multi([part(b"2\r\n", None, None)], "digest")
        self.assertReason(message(body, ctype=ctype), "no text part")
        # Naming the type makes it a text part again; so does another subtype.
        ctype, body = multi([part(b"2\r\n", "text/plain", None)], "digest")
        self.assertReply(message(body, ctype=ctype))
        ctype, body = multi([part(b"2\r\n", None, None)], "mixed")
        self.assertReply(message(body, ctype=ctype))


class TopLevelHeaderTests(Base):
    """Subject and To come only from the top-level header block."""

    def test_part_headers_are_not_duplicates_or_sources(self):
        text = part(b"2\r\n", TEXT, None, ["Subject: Re: [AB2C]", "To: bob@example.org", "Subject: Re: [WXYZ]"])
        self.assertReply(wrapped([text]))

    def test_embedded_message_headers_are_not_read(self):
        inner = b"Subject: Re: [AB2C]\r\nSubject: Re: [WXYZ]\r\nTo: FCMS@lab.example\r\n\r\n9\r\n"
        attachment = part(inner, "message/rfc822", None, ["Content-Disposition: attachment"])
        self.assertReply(wrapped([part(b"2\r\n", TEXT, None), attachment]))
        self.assertReply(wrapped([attachment, part(b"2\r\n", TEXT, None)]))

    def test_code_and_address_in_parts_do_not_help(self):
        inner = b"Subject: Re: [AB2C]\r\nTo: FCMS@lab.example\r\n\r\n9\r\n"
        attachment = part(inner, "message/rfc822", None)
        text = part(b"2\r\n", TEXT, None, ["Subject: Re: [AB2C]", "To: FCMS@lab.example"])
        self.assertReason(wrapped([text, attachment], subject="Re: nothing"), "no code")
        self.assertReason(wrapped([text, attachment], to="bob@example.org"), "no service address")


class SeparatorTests(Base):
    def reply_with(self, text: bytes):
        return plain(text)

    def test_only_lines_before_the_separator_are_read(self):
        self.assertReply(self.reply_with(b"2\r\n" + SEP + b"\r\n3\r\nnot a choice\r\n"), numbers=(2,))
        self.assertReply(self.reply_with(b"2\r\n" + SEP + b"\r\n" + SEP + b"\r\n"), numbers=(2,))
        self.assertReply(self.reply_with(b"2\r\n\r\n" + SEP + b"\r\n"), numbers=(2,))
        self.assertReply(self.reply_with(b"2\r\n3\r\n" + SEP), numbers=(2, 3))

    def test_no_separator_uses_the_whole_part(self):
        self.assertReply(self.reply_with(b"2\r\n4\r\n"), numbers=(2, 4))
        self.assertReply(self.reply_with(b"2"), numbers=(2,))

    def test_separator_first_leaves_nothing_to_read(self):
        self.assertReason(self.reply_with(SEP + b"\r\n2\r\n"), "empty choice")
        self.assertReason(self.reply_with(b"\r\n \r\n" + SEP + b"\r\n2\r\n"), "empty choice")

    def test_separator_inside_a_line_is_not_a_separator(self):
        self.assertReason(self.reply_with(b"2 " + SEP + b"\r\n"), "invalid choice")
        self.assertReason(self.reply_with(b"2" + SEP + b"\r\n"), "invalid choice")
        self.assertReason(self.reply_with(SEP + b" 2\r\n"), "invalid choice")

    def test_near_misses_are_ordinary_lines(self):
        for line in (SEP + b" ", b" " + SEP, SEP + b"\t", SEP[:-1], SEP.replace(b"Message", b"message"),
                     SEP.replace(b"-----", b"----", 1), SEP.replace(b"sent", b"Sent"), SEP.replace(b"/11/", b"/1/"),
                     SEP.replace(b"00:00", b"0:00"), SEP.replace(b"2026", b"26"), SEP.replace(b"from", b"form"),
                     SEP.replace(b"Message from FCMS", b"Message from"), SEP + b"-"):
            with self.subTest(line=line):
                self.assertReason(self.reply_with(b"2\r\n" + line + b"\r\n3\r\n"), "invalid choice")

    def test_separator_name_and_date_rules(self):
        for line in (b"----- Message from N0CALL sent 2026/11/25 23:59 -----",
                     b"----- Message from a b c sent 2026/11/25 00:00 -----",
                     b"----- Message from J\xc3\xb6rg sent 2026/11/25 00:00 -----",
                     b"----- Message from \xff\xfe sent 2026/11/25 00:00 -----",
                     b"----- Message from sent sent 2026/11/25 00:00 -----"):
            with self.subTest(line=line):
                self.assertReply(self.reply_with(b"2\r\n" + line + b"\r\n\xc3\xa9\r\n"))

    def test_non_ascii_digits_in_the_date_do_not_match(self):
        line = "----- Message from FCMS sent \u0662\u0660\u0662\u0666/11/25 00:00 -----".encode("utf-8")
        self.assertReason(self.reply_with(b"2\r\n" + line + b"\r\n"), "non-ASCII")
        line = "----- Message from FCMS sent 2026/11/25 \uff10\uff10:00 -----".encode("utf-8")
        self.assertReason(self.reply_with(b"2\r\n" + line + b"\r\n"), "non-ASCII")

    def test_line_endings(self):
        for newline in (b"\r\n", b"\n", b"\r"):
            with self.subTest(newline=newline):
                self.assertReply(self.reply_with(b"2" + newline + b"3" + newline + SEP + newline + b"9" + newline),
                                 numbers=(2, 3))

    def test_other_control_characters_are_not_line_endings(self):
        for char in (b"\x0b", b"\x0c", b"\x1c", b"\x1d", b"\x1e"):
            with self.subTest(char=char):
                self.assertReason(self.reply_with(b"2" + char + b"3\r\n"), "invalid choice")
        # A vertical tab before the separator hides it, so the quoted text is read.
        self.assertReason(self.reply_with(b"2\r\n\x0b" + SEP + b"\r\n"), "invalid choice")
        self.assertReason(self.reply_with(b"2\x85" + b"3\r\n"), "non-ASCII")

    def test_quoted_original_is_never_decoded(self):
        data = b"2\r\n" + SEP + b"\r\n" + b"".join(bytes([b]) + b"\r\n" for b in range(128, 256))
        self.assertReply(self.reply_with(data))
        data = b"2\r\n" + SEP + b"\r\n\xc3\x28\xa0\xa1\xe2\x28\xa1\r\n"
        self.assertReply(self.reply_with(data))


class ChoiceTests(Base):
    def choice(self, text):
        return parse_menu_reply(express(text))

    def test_valid_choices(self):
        cases = {
            "2": (2,), "1": (1,), "999": (999,), "1 3": (1, 3), "3 1": (1, 3), "2,1 3": (1, 2, 3),
            "2, 1,3": (1, 2, 3), "2,3": (2, 3), "2-4": (2, 3, 4), "1-3 2-5": (1, 2, 3, 4, 5), "  4  ": (4,),
            "5 5 5": (5,), "1-1": (1,), "999-999": (999,), "2,,3": (2, 3), ", 2 ,": (2,), "2\t3": (2, 3),
            "2 3,4-6, 9": (2, 3, 4, 5, 6, 9), "10 9 8 7": (7, 8, 9, 10), "1-20": tuple(range(1, 21)),
            "980-999": tuple(range(980, 1000)), "1-10 5-15": tuple(range(1, 16)), "1-20 1-20": tuple(range(1, 21)),
            "1-10 11-20": tuple(range(1, 21)), "20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1": tuple(range(1, 21)),
            "2\n3": (2, 3), "\n\n2\n\n": (2,), "2\r\n\r\n3": (2, 3), "   \n\t\n 7": (7,), "2 \n 4-5 ,\n6,": (2, 4, 5, 6),
        }
        for text, numbers in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.choice(text), MenuReply("K7QX", numbers, False))

    def test_more(self):
        for text in ("MORE", "more", "More", "mOrE", " MORE ", "MORE,", ",MORE", "MORE\n", "\nMORE\n\n", "\tMORE\t"):
            with self.subTest(text=text):
                self.assertEqual(self.choice(text), MenuReply("K7QX", (), True))

    def test_more_must_be_alone(self):
        for text in ("MORE 2", "2 MORE", "MORE MORE", "MORE,MORE", "MORE\nMORE", "2\nMORE", "MORE\n2", "MORE 1-3",
                     "MOREMORE", "MORE2", "MORE-2", "MOR", "MORES", "M0RE", "MORE.", "MORE!", "NEXT", "MORE?"):
            with self.subTest(text=text):
                self.assertReason(express(text), "invalid choice")

    def test_empty_choice(self):
        for text in ("", " ", "\n", "\r\n\r\n", "  \t ", ",", " , ,, ", ",\n,\n"):
            with self.subTest(text=text):
                self.assertReason(express(text), "empty choice")
        self.assertReason(plain(b""), "empty choice")
        self.assertReason(plain(SEP + b"\r\n"), "empty choice")

    def test_invalid_tokens(self):
        for text in ("two", "2a", "a2", "0", "00", "01", "02", "007", "1000", "99999", "-1", "+2", "2.5", "2.", ".5",
                     "2-", "-3", "2--4", "2 - 4", "2 -4", "2- 4", "1-2-3", "1e1", "0x2", "2;3", "2/3", "2:3", "2|3",
                     "#2", "(2)", "2)", "'2'", '"2"', "<2>", "[2]", "2 3 x", "1,2,3,x", "1-999999", "1-1000",
                     "0-5", "1-0", "01-5", "1-05", "1-2x", "x1-2", "2\\3", "2=3", "2*3", "2 and 3", "all", "ALL",
                     "FULL", "1 2 3 four", "2\x003", "2\x01", "2\x7f", "\x002", "2\x1b[0m", "2\x0b3", "MORE\x00",
                     "1 000", "1,000", "2 .", "1 - 999", "0 1", "1_0", "1_000", "1-1_0", "1_0-3", "1__0",
                     "MORE 1-999", "1-999 MORE", "MORE_1", "\u0000"):
            with self.subTest(text=text):
                self.assertReason(express(text), "invalid choice")

    def test_invalid_range(self):
        for text in ("3-2", "999-1", "20-19", "10-1", "5-4 1", "1 5-4", "2-1,3", "MORE 5-4", "5-4 MORE", "5-4 junk"):
            with self.subTest(text=text):
                self.assertReason(express(text), "invalid range")

    def test_too_many_choices(self):
        for text in ("1-21", "1-999", "1-10 12-22", "1-20 21", "1-1 2-22", "980-999 5",
                     " ".join(str(n) for n in range(1, 22)), "1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21",
                     "\n".join(str(n) for n in range(1, 22)), "1-999 1-999", "500-520", "1-5 100-115 900"):
            with self.subTest(text=text[:40]):
                self.assertReason(express(text), "too many choices")

    def test_non_ascii_digits_and_text(self):
        for text in ("\u0662", "\uff12", "\u00b2", "\u2462", "2\u00a03", "2\u2003 3", "2\u20134", "2\u22124",
                     "2 \u0663", "\uff2d\uff2f\uff32\uff25", "MORE\u200b", "\u0131", "caf\u00e9"):
            with self.subTest(text=text):
                self.assertReason(express(text), "non-ASCII")

    def test_non_ascii_after_a_valid_line_still_fails(self):
        self.assertReason(plain(b"2\r\n3\xc2\xa0\r\n" + SEP), "non-ASCII")
        self.assertReason(plain(b"2\r\n\xc3\xa9\r\n" + SEP), "non-ASCII")
        self.assertReason(plain(b"\xc3\xa9\r\n" + SEP), "non-ASCII")
        self.assertReason(plain(b"\xff"), "non-ASCII")

    def test_non_ascii_beats_other_choice_errors(self):
        self.assertReason(plain(b"1-999 \xc3\xa9"), "non-ASCII")
        self.assertReason(plain(b"MORE 2 \xc3\xa9"), "non-ASCII")

    def test_error_precedence_among_choice_errors(self):
        self.assertReason(express("1-999 junk"), "invalid choice")
        self.assertReason(express("junk 5-4"), "invalid choice")
        self.assertReason(express("5-4 junk"), "invalid range")
        self.assertReason(express("5-4 1-999"), "invalid range")
        # After every token is valid: MORE with others first, then the count.
        self.assertReason(express("MORE 1-999"), "invalid choice")
        self.assertReason(express("MORE 2"), "invalid choice")

    def test_large_inputs_are_fast_and_bounded(self):
        started = time.monotonic()
        self.assertReply(plain(b"1 " * 20000), numbers=(1,))
        self.assertReason(plain(b"1-999 " * 8000), "too many choices")
        self.assertReason(plain(b"1-999999 " * 4000), "invalid choice")
        self.assertReason(plain(b"9" * 30000), "invalid choice")
        self.assertReason(plain(b"1-" + b"9" * 30000), "invalid choice")
        self.assertReason(plain(b"," * 30000 + b"\r\n" * 10000), "empty choice")
        self.assertLess(time.monotonic() - started, 5.0)


class HostileHeaderTimingTests(Base):
    """Quoted and parenthesised header values must not cost more than a moment.

    email.policy.default needs tens of seconds on these inputs and raises
    RecursionError on the last one; the parser must use email.policy.compat32.
    """

    LIMIT = 2.0

    def timed(self, data):
        self.assertLessEqual(len(data), 65536)
        started = time.monotonic()
        try:
            result = parse_menu_reply(data)
        except MenuReplyError as error:
            result = error
        self.assertLess(time.monotonic() - started, self.LIMIT)
        return result

    def test_content_type_of_quotes(self):
        data = message(b"2\r\n", ctype='"' * 20000)
        self.assertEqual(self.timed(data), MenuReply("K7QX", (2,), False))

    def test_to_of_quotes(self):
        result = self.timed(express(to='"' * 30000))
        self.assertIsInstance(result, MenuReplyError)
        self.assertEqual(result.reason, "no service address")

    def test_sixty_parts_with_long_quoted_content_types(self):
        parts = [part(b"2\r\n", 'text/plain; name="' + "a" * 970 + '"', None) for _ in range(60)]
        data = wrapped(parts)
        self.assertGreater(len(data), 60000)
        self.assertEqual(self.timed(data), MenuReply("K7QX", (2,), False))

    def test_content_type_of_open_parentheses(self):
        data = message(b"2\r\n", ctype="(" * 30000)
        self.assertEqual(self.timed(data), MenuReply("K7QX", (2,), False))

    def test_other_headers_of_quotes_and_parentheses(self):
        for name in ("Content-Disposition", "Content-Transfer-Encoding", "Subject", "Message-ID", "Cc", "From"):
            for char in ('"', "(", "<", "\\"):
                with self.subTest(header=name, char=char):
                    base = express()
                    data = base.replace(b"\r\n\r\n", f"\r\n{name}: {char * 20000}\r\n\r\n".encode(), 1)
                    result = self.timed(data)
                    self.assertTrue(isinstance(result, (MenuReply, MenuReplyError)))


class LimitAndHardeningTests(Base):
    def padded(self, size):
        base = plain(b"2\r\n" + SEP + b"\r\n")
        return base + b"x" * (size - len(base))

    def test_size_limit(self):
        self.assertEqual(len(self.padded(65536)), 65536)
        self.assertReply(self.padded(65536))
        self.assertReason(self.padded(65537), "too large")
        self.assertReason(self.padded(70000), "too large")

    def test_size_is_checked_before_anything_else(self):
        self.assertReason(b"x" * 10_000_000, "too large")
        self.assertReason(b"\x00" * 65537, "too large")
        self.assertReason(express(to="bob@example.org") + b"x" * 70000, "too large")

    def test_nesting_limit(self):
        self.assertReply(nested(1), numbers=(2,))
        self.assertReply(nested(10), numbers=(2,))
        for depth in (11, 12, 50, 400):
            with self.subTest(depth=depth):
                self.assertReason(nested(depth), "malformed message")

    def test_message_entities_count_as_containers(self):
        for chain, expected in ((1, "no text part"), (9, "no text part"), (10, "malformed message"),
                                (11, "malformed message"), (12, "malformed message"), (1000, "malformed message")):
            with self.subTest(chain=chain):
                ctype, body = multi([rfc822_chain(chain)])
                data = message(body, ctype=ctype)
                self.assertLess(len(data), 65536)
                self.assertReason(data, expected)

    def test_message_chain_at_top_level(self):
        # The top-level message is the first container of the chain.
        self.assertReason(head() + rfc822_chain(10), "no text part")
        self.assertReason(head() + rfc822_chain(11), "malformed message")
        self.assertReason(head() + rfc822_chain(1500), "malformed message")

    def test_mixed_multipart_and_message_nesting(self):
        # multipart > message > multipart > message ... alternating, 12 containers.
        inner = part(b"2\r\n", "text/plain", None)
        for level in range(6):
            ctype, body = multi([inner], "mixed", f"M{level}")
            inner = (f"Content-Type: {ctype}\r\n\r\n").encode() + body
            inner = b"Content-Type: message/rfc822\r\n\r\n" + inner
        self.assertReason(head() + inner, "malformed message")

    def test_deep_nesting_beside_a_found_text_part_is_still_malformed(self):
        first = part(b"2\r\n", "text/plain", None)
        ctype, body = multi([first, nested_part(10)])      # the outer container makes 11
        self.assertReason(message(body, ctype=ctype), "malformed message")
        ctype, body = multi([first, nested_part(9)])       # 10 in all
        self.assertReply(message(body, ctype=ctype), numbers=(2,))

    def test_many_headers_and_parts(self):
        extra = [f"X-Pad-{n}: {'y' * 40}" for n in range(500)]
        self.assertReply(express(extra=extra))
        parts = [part(b"<p>x</p>", "text/html", None)] * 300 + [part(b"2\r\n", "text/plain", None)]
        self.assertReply(wrapped(parts), numbers=(2,))

    def test_long_lines_and_whitespace(self):
        self.assertReply(plain(b" " * 30000 + b"2" + b" " * 30000), numbers=(2,))
        self.assertReply(express(subject="Re: " + "x " * 5000 + "[K7QX]"))

    def test_bytes_that_are_not_mail_never_raise_anything_else(self):
        rng = random.Random(20261125)
        good = (FIXTURES / "express_reply.eml").read_bytes()
        samples = [bytes(rng.randrange(256) for _ in range(rng.randrange(0, 300))) for _ in range(300)]
        for _ in range(300):
            data = bytearray(good)
            for _ in range(rng.randrange(1, 6)):
                data[rng.randrange(len(data))] = rng.randrange(256)
            samples.append(bytes(data[: rng.randrange(1, len(data) + 1)]))
        for _ in range(100):
            line = rng.choice([b"Subject", b"To", b"Content-Type", b"Content-Transfer-Encoding", b"X-Location"])
            samples.append(good.replace(line + b":", line + b"\x00:").replace(b"boundary=", b"boundary\xff="))
        for data in samples:
            try:
                result = parse_menu_reply(data)
            except MenuReplyError:
                continue
            self.assertIsInstance(result, MenuReply)
            self.assertRegex(result.code, r"\A[2-9A-HJ-NP-Z]{4}\Z")

    def test_invalid_boundaries_and_headers_do_not_crash(self):
        for data in (
            head() + b"Content-Type: multipart/mixed; boundary=\r\n\r\n--\r\n2\r\n",
            head() + b'Content-Type: multipart/mixed; boundary=""\r\n\r\n----\r\n2\r\n',
            head() + b"Content-Type: \r\n\r\n2\r\n",
            head() + b"Content-Type: text/plain/extra\r\n\r\n2\r\n",
            head() + b"Content-Type: ;;;\r\n\r\n2\r\n",
            head() + b"Content-Transfer-Encoding: \r\n\r\n2\r\n",
            head() + b"Content-Type: text/plain; charset=\xff\xfe\r\n\r\n2\r\n",
            b"Subject: Re: [K7QX]\r\nTo: FCMS@x\r\n Subject: folded\r\n\r\n2\r\n",
            b"To: " + b"FCMS@x, " * 5000 + b"\r\nSubject: [K7QX]\r\n\r\n2\r\n",
            b"Subject: " + b"=?utf-8?q?" * 2000 + b"[K7QX]\r\nTo: FCMS@x\r\n\r\n2\r\n",
            b"Subject: =?bogus?x?zzz?= [K7QX]\r\nTo: FCMS@x\r\n\r\n2\r\n",
            b"From nobody Tue Nov 25 00:00:00 2026\r\nSubject: [K7QX]\r\nTo: FCMS@x\r\n\r\n2\r\n",
        ):
            with self.subTest(data=data[:60]):
                try:
                    result = parse_menu_reply(data)
                except MenuReplyError:
                    continue
                self.assertIsInstance(result, MenuReply)


if __name__ == "__main__":
    unittest.main()
