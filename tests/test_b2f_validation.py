"""Wire-level checks that a durable acknowledgement requires complete B2 data."""
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from federated_cms import lzhuf
from federated_cms.b2f import LabServer, ProtocolError, message_mid, proposal_checksum
from federated_cms.b2f_auth import login_response
from federated_cms.store import SQLiteStore
from tests.lab_mailbox_helpers import mailbox_call


def line(reader):
    result = bytearray()
    while (byte := reader.read(1)) and byte != b"\r":
        result.extend(byte)
    return bytes(result)


class B2ValidationTests(unittest.TestCase):
    def test_message_sections_must_be_complete(self):
        valid = b"Mid: ABC123\r\nBody: 5\r\nFile: 3 x.bin\r\n\r\nhello\r\n\x00\x01\x02\r\n"
        self.assertEqual(message_mid(valid), "ABC123")
        self.assertEqual(message_mid(valid + b"\x00"), "ABC123")
        two_files = (b"Mid: ABC123\r\nBody: 5\r\nFile: 3 x.bin\r\nFile: 2 y.bin\r\n\r\n"
                     b"hello\r\n\x00\x01\x02\r\nxy\r\n\x00")
        self.assertEqual(message_mid(two_files), "ABC123")
        invalid = [
            valid + b"\x00\x00",
            valid + b"other",
            b"Mid: ABC123\r\nBody: 0\r\n\r\n\r\n\x00",
            b"Mid: ABC123\r\nBody: 999\r\n\r\nshort",
            b"Mid: ABC123\r\nBody: 5\r\nFile: 4 x\r\n\r\nhello\r\nabc",
            b"Mid: ABC123\r\nBody: -1\r\n\r\n",
            b"Mid: ABC123\r\nBody: 0\r\nBody: 0\r\n\r\n",
            b"Mid: ABC123\r\nBody: 0\r\nBad header\r\n\r\n",
            b"Mid: ABC123\r\nBody: 0\r\n\r\nunexpected",
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                message_mid(raw)

    def test_invalid_body_is_not_committed_or_acknowledged(self):
        raw = b"Mid: TEST123\r\nBody: 999\r\n\r\nshort"
        compressed = lzhuf.compress(raw)
        proposal = f"FC EM TEST123 {len(raw)} {len(compressed)} 0".encode()
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            with LabServer(("127.0.0.1", 0), store, "TEST1", "fixture-only") as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    with socket.create_connection(server.server_address, timeout=3) as client:
                        client.settimeout(3)
                        with client.makefile("rb") as reader:
                            line(reader)
                            challenge = line(reader).decode().split(": ")[1]
                            line(reader)
                            reply = login_response(challenge, "fixture-only")
                            client.sendall(f";FW: TEST1\r[client-1-B2FHM$]\r;PR: {reply}\r".encode() + proposal + b"\rF> " + f"{proposal_checksum([proposal]):02X}".encode() + b"\r")
                            self.assertEqual(line(reader), b"FS +")
                            client.sendall(b"\x01\x06Lab\x000\x00" + bytes((2, len(compressed))) + compressed + bytes((4, -sum(compressed) & 255)))
                            self.assertEqual(line(reader), b"*** Protocol error")
                            self.assertEqual(reader.read(), b"")
                    self.assertIsNone(mailbox_call(store, "TEST1", "get", "TEST123", "in"))
                finally:
                    server.shutdown()
                    worker.join()

    def test_empty_proposal_block_is_an_empty_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            with LabServer(("127.0.0.1", 0), store, "TEST1", "fixture-only") as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    with socket.create_connection(server.server_address, timeout=3) as client:
                        client.settimeout(3)
                        with client.makefile("rb") as reader:
                            line(reader)
                            challenge = line(reader).decode().split(": ")[1]
                            line(reader)
                            reply = login_response(challenge, "fixture-only")
                            client.sendall(f";FW: TEST1\r[client-1-B2FHM$]\r;PR: {reply}\rF> 00\r".encode())
                            self.assertEqual(line(reader), b"FF")
                            client.sendall(b"FQ\r")
                finally:
                    server.shutdown()
                    worker.join()
