"""Bounded B2 structural validation shared by the laboratory mailbox and B2F."""
from __future__ import annotations

import re

MAX_MESSAGE = 65536
MID = re.compile(r"^[A-Z0-9]{1,32}$")


class B2MessageError(ValueError):
    """A synthetic B2 message is malformed or exceeds the lab limit."""


def message_mid(raw: bytes) -> str:
    """Validate B2 section boundaries and return the single embedded MID.

    This preserves raw bytes, including attachments. It validates framing and
    sizes only; message addresses are not proof of identity or authorization.
    """
    if not isinstance(raw, bytes) or len(raw) > MAX_MESSAGE or b"\r\n\r\n" not in raw:
        raise B2MessageError("invalid B2 message")
    header_block, content = raw.split(b"\r\n\r\n", 1)
    headers: list[tuple[bytes, bytes]] = []
    for line in header_block.split(b"\r\n"):
        if line.startswith((b" ", b"\t")) and headers:
            name, value = headers[-1]
            headers[-1] = (name, value + b" " + line.strip())
            continue
        name, separator, value = line.partition(b":")
        if not separator or not re.fullmatch(rb"[A-Za-z0-9-]+", name):
            raise B2MessageError("invalid B2 header")
        headers.append((name.lower(), value.strip()))
    try:
        mids = [value.decode("ascii", "strict") for name, value in headers if name == b"mid"]
    except UnicodeDecodeError as exc:
        raise B2MessageError("invalid B2 MID") from exc
    if len(mids) != 1 or not MID.fullmatch(mids[0]):
        raise B2MessageError("invalid B2 MID")
    bodies = [value for name, value in headers if name == b"body"]
    if len(bodies) != 1 or not re.fullmatch(rb"[0-9]{1,6}", bodies[0]):
        raise B2MessageError("invalid B2 body length")
    lengths = [int(bodies[0])]
    for name, value in headers:
        if name == b"file":
            size, separator, filename = value.partition(b" ")
            if not separator or not filename.strip() or not re.fullmatch(rb"[0-9]{1,6}", size):
                raise B2MessageError("invalid B2 file header")
            lengths.append(int(size))
    pos = 0
    for index, length in enumerate(lengths):
        if length > MAX_MESSAGE or pos + length > len(content):
            raise B2MessageError("truncated B2 section")
        pos += length
        if index + 1 < len(lengths):
            if content[pos:pos + 2] != b"\r\n":
                raise B2MessageError("missing B2 section separator")
            pos += 2
    if content[pos:] not in (b"", b"\r\n"):
        raise B2MessageError("unexpected B2 trailing data")
    return mids[0]
