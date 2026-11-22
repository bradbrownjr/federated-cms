"""Line-oriented BPQ terminal adapter (never a B2F transport)."""

from __future__ import annotations

import argparse
import os
import re
import socketserver
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .core import CMSCore
from .store import SQLiteStore, StoreUnavailable

MAX_LINE = 256
CALLSIGN = re.compile(r"^([A-Z0-9]{1,6})(?:-([0-9]{1,2}))?$", re.I)


@dataclass(frozen=True)
class SessionContext:
    scope: str
    callsign: str | None = None
    base_callsign: str | None = None
    ssid: str | None = None


def parse_callsign(line: str) -> tuple[str, str, str | None] | None:
    match = CALLSIGN.fullmatch(line.strip())
    if not match:
        return None
    call = match.group(1).upper() + (f"-{match.group(2)}" if match.group(2) else "")
    return call, match.group(1).upper(), match.group(2)


class TerminalSession:
    def __init__(self, core: CMSCore, reader: BinaryIO, writer: BinaryIO):
        self.core, self.reader, self.writer = core, reader, writer
        self.context = SessionContext(scope=uuid.uuid4().hex)

    def _write(self, line: str) -> None:
        safe = line.encode("ascii", "replace").replace(b"\x1b", b"?")[:512]
        self.writer.write(safe + b"\r\n")
        self.writer.flush()

    def _readline(self) -> str | None:
        raw = self.reader.readline(MAX_LINE + 1)
        if not raw:
            return None
        if len(raw) > MAX_LINE and not raw.endswith((b"\n", b"\r")):
            # Discard remainder; do not let it become commands.
            self.reader.readline()
            self._write("ERR line too long")
            return ""
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            self._write("ERR ASCII input only")
            return ""
        return text.strip(" \t\r\n")

    def run(self) -> None:
        self._write("FCMS research terminal. Type HELP.")
        first = self._readline()
        if first is None:
            return
        parsed = parse_callsign(first)
        if parsed:
            call, base, ssid = parsed
            self.context = SessionContext(self.context.scope, call, base, ssid)
            self._write(f"Station: {call} (claimed; not authenticated)")
        elif first:
            self._command(first)
        while True:
            self._write("FCMS>")
            line = self._readline()
            if line is None:
                return
            if not line:
                continue
            if not self._command(line):
                return

    def _command(self, line: str) -> bool:
        bits = line.upper().split()
        command = bits[0] if bits else ""
        try:
            if command == "HELP":
                self._write("HELP STATUS SERVICES WX <GRID> JOBS RESULT <ID> QUIT")
            elif command == "STATUS":
                counts = self.core.status()
                self._write("CMS available; " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())) if counts else "CMS available; queue empty")
            elif command == "SERVICES":
                self._write("weather.forecast (WX) - deterministic TEST DATA")
            elif command == "WX":
                if len(bits) != 2:
                    self._write("ERR usage: WX FN43")
                else:
                    receipt = self.core.submit_weather(bits[1], self.context.scope)
                    self.core.process_pending()
                    noun = "duplicate" if receipt.duplicate else "queued"
                    self._write(f"Request {noun}: {receipt.request_id}")
            elif command == "JOBS":
                jobs = self.core.jobs(self.context.scope)
                if not jobs:
                    self._write("No requests in this session.")
                for job in jobs[:10]:
                    self._write(f"{job.request_id} {job.status}")
            elif command == "RESULT":
                if len(bits) != 2:
                    self._write("ERR usage: RESULT <request-id>")
                else:
                    view = self.core.request(bits[1].lower(), self.context.scope)
                    if not view:
                        self._write("ERR request not found or not permitted")
                    elif view.result and view.result.get("status") == "ok":
                        self._write(view.result["content"])
                    else:
                        self._write(f"Request {view.status}")
            elif command == "QUIT":
                self._write("Bye.")
                return False
            else:
                self._write("ERR unknown command; type HELP")
        except (ValueError, StoreUnavailable) as exc:
            self._write(f"ERR {str(exc)[:120]}")
        return True


class _TCPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: "CMSListener" = self.server  # type: ignore[assignment]
        self.request.settimeout(server.idle_timeout)
        with self.request.makefile("rb") as reader, self.request.makefile("wb") as writer:
            TerminalSession(server.core, reader, writer).run()


class CMSListener(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    def __init__(self, address: tuple[str, int], core: CMSCore, idle_timeout: float = 120.0):
        self.core, self.idle_timeout = core, idle_timeout
        super().__init__(address, _TCPHandler)


def make_core(database: str) -> CMSCore:
    return CMSCore(SQLiteStore(database))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Federated CMS ASCII BPQ terminal bridge")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stdio", action="store_true", help="serve one inetd-style stdin/stdout session")
    group.add_argument("--listen", metavar="HOST:PORT", help="serve TCP sessions (default use: 127.0.0.1:63190)")
    parser.add_argument("--database", default=os.environ.get("FCMS_DATABASE", "fcms.sqlite3"))
    args = parser.parse_args(argv)
    try:
        core = make_core(args.database)
        if args.stdio:
            TerminalSession(core, sys.stdin.buffer, sys.stdout.buffer).run()
            return 0
        host, sep, port = args.listen.rpartition(":")
        if not sep or not host:
            parser.error("--listen must be HOST:PORT")
        with CMSListener((host, int(port)), core) as server:
            server.serve_forever()
    except (OSError, StoreUnavailable) as exc:
        print(f"FCMS unavailable: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
