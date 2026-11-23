"""Isolated B2F handshake and proposal-deferral research endpoint.

No mailbox access or message transfer is implemented yet. Every incoming
proposal is deferred; this endpoint must not be used as a production server.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import socketserver

MAX_LINE = 512
MAX_PROPOSALS = 5


class ProtocolError(ValueError):
    pass


def proposal_checksum(lines: list[bytes]) -> int:
    return -sum(sum(line) + 13 for line in lines) & 255


class Session:
    def __init__(self, connection: socket.socket):
        self.connection = connection
        self.reader = connection.makefile("rb")

    def send(self, line: str) -> None:
        self.connection.sendall(line.encode("ascii") + b"\r")

    def read_line(self) -> bytes:
        data = bytearray()
        while len(data) <= MAX_LINE:
            byte = self.reader.read(1)
            if not byte:
                raise EOFError
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

    def run(self) -> None:
        try:
            self.send("[FCMS-0.1-B2FHM$]")
            self.send("FCMS LAB >")
            sid = False
            proposals: list[bytes] = []
            while True:
                line = self.read_line()
                if line.startswith(b"[") and line.endswith(b"]"):
                    sid = b"B2" in line.upper()
                elif line.startswith(b";"):
                    continue
                elif not sid:
                    raise ProtocolError("B2 SID required")
                elif line == b"FQ":
                    return
                elif line == b"FF":
                    self.send("FQ")
                    return
                elif line.startswith((b"FA ", b"FB ", b"FC ", b"FD ")):
                    proposals.append(line)
                    if len(proposals) > MAX_PROPOSALS:
                        raise ProtocolError("too many proposals")
                elif line.startswith(b"F> "):
                    try:
                        supplied = int(line[3:], 16)
                    except ValueError as exc:
                        raise ProtocolError("invalid checksum") from exc
                    if not proposals or supplied != proposal_checksum(proposals):
                        raise ProtocolError("proposal checksum error")
                    self.send("FS " + "=" * len(proposals))
                    proposals.clear()
                    self.send("FF")
                else:
                    raise ProtocolError("unexpected command")
        except (EOFError, ConnectionError, TimeoutError):
            pass
        except ProtocolError:
            try:
                self.send("*** Protocol error")
            except OSError:
                pass
        finally:
            self.reader.close()


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(30)
        Session(self.request).run()


class LabServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int]):
        if not ipaddress.ip_address(address[0]).is_loopback:
            raise ValueError("B2F lab endpoint requires a loopback address")
        super().__init__(address, _Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Experimental loopback-only B2F handshake endpoint")
    parser.add_argument("--port", type=int, default=63191)
    args = parser.parse_args()
    with LabServer(("127.0.0.1", args.port)) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
