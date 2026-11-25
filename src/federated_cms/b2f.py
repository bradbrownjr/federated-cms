"""Loopback-only B2F interoperability laboratory with legacy lab login.

This is an isolated test mailbox, not an authenticated mail server. Never use
real mail or credentials. B2F and the ordinary BPQ terminal remain separate.
"""
from __future__ import annotations

import argparse
import ipaddress
import hmac
import re
import secrets
import socket
import socketserver
import struct

from . import lzhuf
from .b2_message import B2MessageError, MAX_MESSAGE, MID, message_mid as validate_message
from .b2f_auth import login_response
from .lab_mailbox import LabMailboxService, LabMailboxBusy, LabSessionExpired
from .store import SQLiteStore, StoreUnavailable

MAX_LINE = 512
MAX_COMPRESSED = 131072


class ProtocolError(ValueError):
    pass


def proposal_checksum(lines: list[bytes]) -> int:
    return -sum(sum(line) + 13 for line in lines) & 255


def message_mid(raw: bytes) -> str:
    try:
        return validate_message(raw)
    except B2MessageError as exc:
        raise ProtocolError("invalid B2 message") from exc


class Session:
    def __init__(self, connection: socket.socket, store: SQLiteStore, account: str, password: str):
        self.connection, self.store, self.account, self.password = connection, store, account, password
        self.mailbox = LabMailboxService(store)
        self.mailbox_session = None
        self.reader = connection.makefile("rb")

    def send(self, line: str) -> None:
        self.connection.sendall(line.encode("ascii") + b"\r")

    def read_exact(self, count: int) -> bytes:
        data = self.reader.read(count)
        if len(data) != count:
            raise EOFError
        return data

    def read_line(self) -> bytes:
        data = bytearray()
        while len(data) <= MAX_LINE:
            byte = self.read_exact(1)
            if byte == b"\r":
                if data:
                    return bytes(data)
                continue
            if byte == b"\n" and not data:
                continue
            if not 32 <= byte[0] <= 126:
                raise ProtocolError("invalid line")
            data.extend(byte)
        raise ProtocolError("line too long")

    def read_message(self, mid: str, size: int, compressed_size: int) -> bytes:
        if self.read_exact(1) != b"\x01":
            raise ProtocolError("missing message header")
        length = self.read_exact(1)[0]
        header = self.read_exact(length)
        parts = header.split(b"\0")
        if len(parts) != 3 or parts[1] != b"0" or parts[2]:
            raise ProtocolError("unsupported message offset")
        data = bytearray()
        while True:
            kind = self.read_exact(1)[0]
            if kind == 4:
                if (sum(data) + self.read_exact(1)[0]) & 255:
                    raise ProtocolError("message checksum error")
                break
            if kind != 2:
                raise ProtocolError("invalid message block")
            length = self.read_exact(1)[0] or 256
            if len(data) + length > compressed_size:
                raise ProtocolError("compressed message too large")
            data.extend(self.read_exact(length))
        if len(data) != compressed_size or len(data) < 6 or struct.unpack_from('<I', data, 2)[0] != size:
            raise ProtocolError("message length mismatch")
        raw = lzhuf.decompress(bytes(data))
        if len(raw) != size or message_mid(raw) != mid:
            raise ProtocolError("message MID mismatch")
        return raw

    def write_message(self, compressed: bytes) -> None:
        self.connection.sendall(b"\x01\x06Lab\x000\x00")
        for pos in range(0, len(compressed), 125):
            chunk = compressed[pos:pos + 125]
            self.connection.sendall(bytes((2, len(chunk))) + chunk)
        self.connection.sendall(bytes((4, -sum(compressed) & 255)))

    def receive_turn(self, first: bytes) -> bool:
        if first == b"FQ":
            return False
        if first == b"FF":
            return True
        proposals = []
        lines = []
        seen = set()
        line = first
        while line.startswith((b"FA ", b"FB ", b"FC ", b"FD ")):
            lines.append(line)
            if len(lines) > 5:
                raise ProtocolError("too many proposals")
            parts = line.decode("ascii").split()
            if len(parts) != 6:
                raise ProtocolError("bad proposal")
            try:
                mid, size, compressed = parts[2], int(parts[3]), int(parts[4])
            except ValueError as exc:
                raise ProtocolError("bad proposal") from exc
            if not MID.fullmatch(mid) or not 0 <= size <= MAX_MESSAGE or not 6 <= compressed <= MAX_COMPRESSED:
                raise ProtocolError("proposal bounds")
            answer = "=" if parts[0] != "FC" or parts[1] not in {"EM", "CM"} else "+"
            if mid in seen:
                answer = "="
            elif self.mailbox.get(self.mailbox_session, mid, "in") is not None:
                answer = "-"
            seen.add(mid)
            proposals.append((mid, size, compressed, answer))
            line = self.read_line()
        if not line.startswith(b"F> ") or int(line[3:], 16) != proposal_checksum(lines):
            raise ProtocolError("proposal checksum")
        if not proposals:
            return True
        self.send("FS " + "".join(item[3] for item in proposals))
        for mid, size, compressed, answer in proposals:
            if answer == "+":
                raw = self.read_message(mid, size, compressed)
                self.mailbox.store_message(self.mailbox_session, mid, raw, "in")
        return True

    def run(self) -> None:
        try:
            self.send("[FCMS-0.1-B2FWHM$]")
            challenge = f"{secrets.randbelow(100_000_000):08d}"
            self.send(f";PQ: {challenge}")
            self.send("FCMS LAB >")
            sid = False
            caller = None
            response = None
            handshake_lines = 0
            while True:
                line = self.read_line()
                handshake_lines += 1
                if handshake_lines > 16:
                    raise ProtocolError("handshake too long")
                if line.startswith(b";FW:"):
                    if caller is not None:
                        raise ProtocolError("duplicate caller")
                    caller = line[4:].strip().decode("ascii").upper()
                elif line.startswith(b";PR:"):
                    if response is not None:
                        raise ProtocolError("duplicate response")
                    response = line[4:].strip().decode("ascii")
                elif line.startswith(b"[") and line.endswith(b"]"):
                    sid = b"B2" in line.upper()
                elif line.startswith(b";"):
                    continue
                else:
                    expected = login_response(challenge, self.password)
                    if (not sid or caller != self.account or response is None
                            or not hmac.compare_digest(response, expected)):
                        raise ProtocolError("lab account mismatch")
                    self.mailbox_session = self.mailbox.open_session(self.account)
                    break
            pending_delivery: list[str] = []
            deferred: set[str] = set()
            while True:
                continuing = self.receive_turn(line)
                # Only a complete, valid next turn acknowledges a transmitted
                # message. EOF, malformed lines and invalid proposal blocks
                # leave it pending so a later session can retry it.
                for mid in pending_delivery:
                    self.mailbox.ack(self.mailbox_session, mid)
                pending_delivery = []
                if not continuing:
                    return
                messages = self.mailbox.pending(self.mailbox_session, exclude=deferred)
                if messages:
                    encoded = [(mid, raw, lzhuf.compress(raw)) for mid, raw in messages]
                    lines = [f"FC EM {mid} {len(raw)} {len(data)} 0".encode() for mid, raw, data in encoded]
                    for proposal in lines:
                        self.send(proposal.decode())
                    self.send(f"F> {proposal_checksum(lines):02X}")
                    answer = self.read_line()
                    if not answer.startswith(b"FS ") or len(answer[3:]) != len(encoded):
                        raise ProtocolError("bad response")
                    if any(choice not in b"+Yy-NnRr=LlHh" for choice in answer[3:]):
                        raise ProtocolError("unsupported response")
                    accepted = []
                    for (mid, _, data), choice in zip(encoded, answer[3:]):
                        if choice in b"+Yy":
                            self.write_message(data)
                            accepted.append(mid)
                        elif choice in b"-NnRr":
                            self.mailbox.ack(self.mailbox_session, mid)
                        else:
                            deferred.add(mid)
                            if len(deferred) > 1000:
                                raise ProtocolError("too many deferred messages")
                    line = self.read_line()
                    pending_delivery = accepted
                else:
                    self.send("FF")
                    line = self.read_line()
        except (EOFError, ConnectionError, TimeoutError):
            pass
        except (ProtocolError, lzhuf.LzhufError, StoreUnavailable, LabMailboxBusy, LabSessionExpired, ValueError):
            try:
                self.send("*** Protocol error")
            except OSError:
                pass
        finally:
            try:
                if self.mailbox_session is not None:
                    self.mailbox.close_session(self.mailbox_session)
            finally:
                self.reader.close()


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(30)
        Session(self.request, self.server.store, self.server.account, self.server.password).run()


class LabServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: SQLiteStore, account: str, password: str):
        if not ipaddress.ip_address(address[0]).is_loopback:
            raise ValueError("B2F lab endpoint requires a loopback address")
        if not re.fullmatch(r"[A-Za-z0-9]{1,6}(?:-[0-9]{1,2})?", account) or not password or len(password.encode("utf-8")) > 128 or any(ord(c) < 32 for c in password):
            raise ValueError("invalid lab account or password")
        self.store, self.account, self.password = store, account.upper(), password
        super().__init__(address, _Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Loopback-only B2F authentication laboratory")
    parser.add_argument("--port", type=int, default=63191)
    parser.add_argument("--database", default="fcms-b2f-lab.sqlite3")
    parser.add_argument("--lab-account", required=True, help="test callsign for the lab mailbox")
    parser.add_argument("--password-file", required=True, help="UTF-8 file holding a disposable lab password")
    args = parser.parse_args()
    with open(args.password_file, encoding="utf-8") as secret_file:
        password = secret_file.read(130).rstrip("\r\n")
    store = SQLiteStore(args.database)
    store.initialize()
    with LabServer(("127.0.0.1", args.port), store, args.lab_account, password) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
