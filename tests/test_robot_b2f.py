"""End-to-end contract test: an INQUIRY request and its reply over the
loopback B2F lab server (docs/INQUIRY-ROBOT-SLICE1.md). Offline; no network
beyond 127.0.0.1. Do not change it to make an implementation pass.
"""
from __future__ import annotations

from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from federated_cms import SQLiteStore, lzhuf
from federated_cms.b2f import LabServer, proposal_checksum
from federated_cms.b2f_auth import login_response
from federated_cms.robot import Robot
from federated_cms.robot_config import load_config
from tests.lab_mailbox_helpers import mailbox_call
from tests.robot_helpers import BASIC, HELP_LINES, SALT, FakeClock, body_text, crlf, message

PASSWORD = "fixture-only"


def line(reader) -> bytes:
    data = bytearray()
    while (byte := reader.read(1)) and byte != b"\r":
        data.extend(byte)
    return bytes(data)


def handshake(client, reader, account="N0CALL"):
    assert b"B2" in line(reader)
    challenge = line(reader).decode().split(": ", 1)[1]
    assert b">" in line(reader)
    client.sendall(f";FW: {account}\r[client-1-B2FHM$]\r;PR: {login_response(challenge, PASSWORD)}\r".encode())


def send_block(client, data: bytes) -> None:
    title = b"Request\x000\x00"
    blocks = b"".join(bytes((2, len(data[pos:pos + 250]))) + data[pos:pos + 250]
                      for pos in range(0, len(data), 250))
    client.sendall(b"\x01" + bytes((len(title),)) + title + blocks + bytes((4, -sum(data) & 255)))


def read_block(reader) -> bytes:
    assert reader.read(1) == b"\x01"
    reader.read(reader.read(1)[0])
    data = bytearray()
    while True:
        kind = reader.read(1)
        if kind == b"\x04":
            assert (sum(data) + reader.read(1)[0]) & 255 == 0
            return bytes(data)
        assert kind == b"\x02"
        size = reader.read(1)[0] or 256
        data.extend(reader.read(size))


class RobotOverB2FTests(unittest.TestCase):
    def test_help_request_reply_and_duplicate_proposal(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory, \
                patch("federated_cms.lab_mailbox.time.time", side_effect=lambda: clock.timestamp()):
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            mailbox_call(store, "N0CALL", "get", "X", "in")  # creates the mailbox and robot tables
            con = store._connect()
            with con:
                con.execute("INSERT INTO robot_meta (name, value) VALUES ('mid_salt', ?)", (SALT,))
            raw = message("REQ000000001", "HELP\r\n")
            data = lzhuf.compress(raw)
            proposal = f"FC EM REQ000000001 {len(raw)} {len(data)} 0".encode()
            checksum = f"F> {proposal_checksum([proposal]):02X}\r".encode()
            with LabServer(("127.0.0.1", 0), store, "N0CALL", PASSWORD) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with socket.create_connection(server.server_address, timeout=3) as client:
                        client.settimeout(3)
                        with client.makefile("rb") as reader:
                            handshake(client, reader)
                            client.sendall(proposal + b"\r" + checksum)
                            self.assertEqual(line(reader), b"FS +")
                            send_block(client, data)
                            self.assertEqual(line(reader), b"FF")
                            client.sendall(b"FQ\r")
                            self.assertEqual(reader.read(), b"")

                    robot = Robot(store, load_config(BASIC), clock=clock)
                    self.assertEqual(robot.run_until_idle(), 2)

                    with socket.create_connection(server.server_address, timeout=3) as client:
                        client.settimeout(3)
                        with client.makefile("rb") as reader:
                            handshake(client, reader)
                            client.sendall(proposal + b"\r" + checksum)  # the client offers it again
                            self.assertEqual(line(reader), b"FS -")
                            offer = line(reader)
                            self.assertTrue(offer.startswith(b"FC EM B23RMN5PFUU7 "), offer)
                            self.assertTrue(line(reader).startswith(b"F> "))
                            client.sendall(b"FS +\r")
                            reply = lzhuf.decompress(read_block(reader))
                            client.sendall(b"FF\r")
                            self.assertEqual(line(reader), b"FF")
                            client.sendall(b"FQ\r")
                            self.assertEqual(reader.read(), b"")
                finally:
                    server.shutdown()
                    thread.join()

            expected_body = crlf(body_text(*HELP_LINES))
            self.assertEqual(reply, (
                b"Mid: B23RMN5PFUU7\r\nDate: 2026/11/25 04:33\r\nType: Private\r\nFrom: SERVICE\r\n"
                b"To: N0CALL\r\nSubject: INQUIRY - HELP\r\nMbo: N1TEST-10\r\n"
                + f"Body: {len(expected_body)}\r\n\r\n".encode() + expected_body))
            check = store._connect()
            with check:
                self.assertEqual(check.execute("SELECT COUNT(*) FROM robot_inbox").fetchone()[0], 1)
                self.assertEqual(check.execute(
                    "SELECT delivered FROM shared_lab_mailbox_messages WHERE mid='B23RMN5PFUU7'").fetchone()[0], 1)
            self.assertEqual(Robot(store, load_config(BASIC), clock=clock).run_until_idle(), 0)


if __name__ == "__main__":
    unittest.main()
