"""Read numbered-menu replies from untrusted MIME messages."""

from dataclasses import dataclass
import email
from email import policy
from email.header import decode_header, make_header
from email.utils import getaddresses
import re

from .inquiry import DEFAULT_SERVICE_ADDRESS, address_local_part, check_service_address


@dataclass(frozen=True)
class MenuReply:
    code: str
    numbers: tuple[int, ...]
    more: bool


class MenuReplyError(ValueError):
    """A menu reply failed a contract check."""

    def __init__(self, reason: str, code: str | None = None):
        self.reason = reason
        self.code = code
        super().__init__(reason)


_CODE = re.compile(r"\[([A-Za-z0-9]{4})\]")
_SEPARATOR = re.compile(rb"----- Message from .+ sent [0-9]{4}/[0-9]{2}/[0-9]{2} [0-9]{2}:[0-9]{2} -----")
_NUMBER = re.compile(rb"[1-9][0-9]{0,2}")
_RANGE = re.compile(rb"([1-9][0-9]{0,2})-([1-9][0-9]{0,2})")


def _check_tree(message):
    """Check all entities iteratively, including forwarded messages."""
    if len(message.get_all("Subject", [])) > 1:
        raise MenuReplyError("malformed message")
    stack = [(message, 0)]
    while stack:
        part, depth = stack.pop()
        container = part.get_content_maintype() in ("multipart", "message")
        if depth > 10:
            raise MenuReplyError("malformed message")
        payload = part.get_payload()
        if isinstance(payload, list):
            stack.extend((child, depth + int(container)) for child in payload)


def _has_recipient(message, service_address):
    """Check each raw To field without decoding encoded words."""
    for value in message.get_all("To", []):
        try:
            addresses = getaddresses([str(value)])
        except Exception:
            continue
        for _, address in addresses:
            try:
                if address_local_part(address) == service_address:
                    return True
            except ValueError:
                continue
    return False


def _subject_code(message):
    """Decode only the subject and locate exactly one code-shaped token."""
    try:
        value = str(message.get("Subject", "")).replace("\r", "").replace("\n", "")
        value = str(make_header(decode_header(value)))
    except Exception:
        value = ""
    codes = _CODE.findall(value)
    if not codes:
        raise MenuReplyError("no code")
    if len(codes) > 1:
        raise MenuReplyError("multiple codes")
    code = codes[0].upper()
    if any(char not in "23456789ABCDEFGHJKLMNPQRSTUVWXYZ" for char in code):
        raise MenuReplyError("invalid code")
    return code


def _text_part(message):
    """Find the first eligible part, descending only into multiparts."""
    stack = [message]
    while stack:
        part = stack.pop()
        content_type = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            payload = part.get_payload()
            if isinstance(payload, list):
                stack.extend(reversed(payload))
        elif content_type == "text/plain" and part.get_content_disposition() != "attachment":
            return part
    raise MenuReplyError("no text part")


def _choices(data):
    """Validate all tokens in order, then calculate their bounded union."""
    lines = []
    for line in re.split(rb"\r\n|\r|\n", data):
        if _SEPARATOR.fullmatch(line):
            break
        lines.append(line)
    if any(not line.isascii() for line in lines):
        raise MenuReplyError("non-ASCII")
    tokens = [token for line in lines for token in re.split(rb"[ \t,]+", line.strip(b" \t")) if token]
    if not tokens:
        raise MenuReplyError("empty choice")
    intervals = []
    more = False
    for token in tokens:
        if token.upper() == b"MORE":
            more = True
        elif _NUMBER.fullmatch(token):
            number = int(token)
            intervals.append((number, number))
        else:
            match = _RANGE.fullmatch(token)
            if not match:
                raise MenuReplyError("invalid choice")
            start, end = map(int, match.groups())
            if start > end:
                raise MenuReplyError("invalid range")
            intervals.append((start, end))
    if more:
        if len(tokens) != 1:
            raise MenuReplyError("invalid choice")
        return (), True
    # Merge intervals before expanding them, so even a large range stays cheap.
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    if sum(end - start + 1 for start, end in merged) > 20:
        raise MenuReplyError("too many choices")
    return tuple(n for start, end in merged for n in range(start, end + 1)), False


def parse_menu_reply(message: bytes, *, address: str = DEFAULT_SERVICE_ADDRESS) -> MenuReply:
    """Parse a message, mapping unexpected mail parser failures to our error."""
    if not isinstance(message, bytes):
        raise TypeError("message must be bytes")
    check_service_address(address)
    if len(message) > 65536:
        raise MenuReplyError("too large")
    code = None
    try:
        parsed = email.message_from_bytes(message, policy=policy.compat32)
        _check_tree(parsed)
        if not _has_recipient(parsed, address):
            raise MenuReplyError("no service address")
        code = _subject_code(parsed)
        part = _text_part(parsed)
        encoding = str(part.get("Content-Transfer-Encoding", "")).lower() or "7bit"
        if encoding not in {"7bit", "8bit", "binary", "quoted-printable", "base64"}:
            raise MenuReplyError("unsupported transfer encoding")
        payload = part.get_payload(decode=True)
        numbers, more = _choices(payload if payload is not None else b"")
        return MenuReply(code, numbers, more)
    except MenuReplyError as exc:
        if exc.reason != "malformed message":
            exc.code = code
        raise
    except Exception as exc:
        raise MenuReplyError("malformed message") from exc
