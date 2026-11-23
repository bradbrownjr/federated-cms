import socket
import threading
import unittest
import tempfile
from pathlib import Path
import os
import sys
from unittest.mock import patch

from federated_cms.b2f import LabServer, Session, ProtocolError, proposal_checksum
from federated_cms.b2f_auth import login_response
from federated_cms import lzhuf
from federated_cms.store import SQLiteStore


class B2FLabTests(unittest.TestCase):
    PASSWORD = "fixture-only"

    @staticmethod
    def _line(reader):
        data = bytearray()
        while (byte := reader.read(1)) and byte != b"\r":
            data.extend(byte)
        return bytes(data)

    def _handshake(self, client, reader, account="TEST1", password=PASSWORD):
        self.assertIn(b"B2", self._line(reader))
        challenge = self._line(reader).decode().split(": ", 1)[1]
        self.assertIn(b">", self._line(reader))
        response = login_response(challenge, password)
        client.sendall(f";FW: {account}\r[client-1-B2FHM$]\r;PR: {response}\r".encode())

    def test_secure_login_vectors(self):
        self.assertEqual(login_response("23753528", "FOOBAR"), "72768415")
        self.assertEqual(login_response("23753528", "FooBar"), "95074758")

    def test_invalid_identity_or_response_cannot_access_mailbox(self):
        for account, password in (("TEST1", "wrong"), ("OTHER", self.PASSWORD)):
            with self.subTest(account=account, password=password), tempfile.TemporaryDirectory() as directory:
                store = SQLiteStore(Path(directory) / "lab.sqlite")
                store.initialize()
                store.b2f_lab_store("TEST1", "TEST123", b"private fixture", "out")
                with LabServer(("127.0.0.1", 0), store, "TEST1", self.PASSWORD) as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        with socket.create_connection(server.server_address, timeout=3) as client:
                            client.settimeout(3)
                            with client.makefile("rb") as reader:
                                self._handshake(client, reader, account, password)
                                client.sendall(b"FF\r")
                                self.assertEqual(self._line(reader), b"*** Protocol error")
                                self.assertEqual(reader.read(), b"")
                        self.assertEqual(len(store.b2f_lab_outbox("TEST1")), 1)
                    finally:
                        server.shutdown()
                        thread.join()

    def test_missing_response_and_old_challenge_response_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            store.b2f_lab_store("TEST1", "TEST123", b"private fixture", "out")
            with patch("federated_cms.b2f.secrets.randbelow", return_value=87654321):
                with LabServer(("127.0.0.1", 0), store, "TEST1", self.PASSWORD) as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        old_response = login_response("12345678", self.PASSWORD)
                        for response in (b"", f";PR: {old_response}\r".encode()):
                            with self.subTest(response=bool(response)):
                                with socket.create_connection(server.server_address, timeout=3) as client:
                                    client.settimeout(3)
                                    with client.makefile("rb") as reader:
                                        self._line(reader)
                                        self.assertEqual(self._line(reader), b";PQ: 87654321")
                                        self._line(reader)
                                        client.sendall(b";FW: TEST1\r[client-1-B2FHM$]\r" + response + b"FF\r")
                                        self.assertEqual(self._line(reader), b"*** Protocol error")
                        self.assertEqual(len(store.b2f_lab_outbox("TEST1")), 1)
                    finally:
                        server.shutdown()
                        thread.join()

    def test_outbound_delivery_requires_valid_next_turn(self):
        for response in (b"BAD\r", b"FC broken\r", b"", b"FF\r"):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                store = SQLiteStore(Path(directory) / "lab.sqlite")
                store.initialize()
                raw = b"Mid: TEST123\r\nBody: 5\r\n\r\nhello"
                store.b2f_lab_store("TEST1", "TEST123", raw, "out")
                with LabServer(("127.0.0.1", 0), store, "TEST1", self.PASSWORD) as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        with socket.create_connection(server.server_address, timeout=3) as client:
                            client.settimeout(3)
                            with client.makefile("rb") as reader:
                                self._handshake(client, reader)
                                client.sendall(b"FF\r")
                                self.assertTrue(self._line(reader).startswith(b"FC EM TEST123 "))
                                self.assertTrue(self._line(reader).startswith(b"F> "))
                                client.sendall(b"FS +\r")
                                self.assertEqual(reader.read(1), b"\x01")
                                header_len = reader.read(1)[0]
                                reader.read(header_len)
                                while True:
                                    kind = reader.read(1)
                                    if kind == b"\x04":
                                        reader.read(1)
                                        break
                                    self.assertEqual(kind, b"\x02")
                                    size = reader.read(1)[0] or 256
                                    self.assertEqual(len(reader.read(size)), size)
                                if response:
                                    client.sendall(response)
                                    self._line(reader)
                                else:
                                    client.shutdown(socket.SHUT_WR)
                                    reader.read()
                        pending = store.b2f_lab_outbox("TEST1")
                        self.assertEqual(bool(pending), response != b"FF\r")
                    finally:
                        server.shutdown()
                        thread.join()

    def test_wire_duplicate_is_rejected_without_receiving_body(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            store.b2f_lab_store("TEST1", "TEST123", b"original", "in")
            with LabServer(("127.0.0.1", 0), store, "TEST1", self.PASSWORD) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with socket.create_connection(server.server_address, timeout=3) as client:
                        client.settimeout(3)
                        with client.makefile("rb") as reader:
                            self._handshake(client, reader)
                            proposal = b"FC EM TEST123 100 50 0"
                            client.sendall(proposal + b"\rF> " + f"{proposal_checksum([proposal]):02X}".encode() + b"\r")
                            self.assertEqual(self._line(reader), b"FS -")
                            self.assertEqual(self._line(reader), b"FF")
                            client.sendall(b"FQ\r")
                    self.assertEqual(store.b2f_lab_get("TEST1", "TEST123", "in"), b"original")
                finally:
                    server.shutdown()
                    thread.join()

    def test_empty_exchange_and_proposal_deferral(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            self._deferral(store)

    def _deferral(self, store):
        with LabServer(("127.0.0.1", 0), store, "TEST1", self.PASSWORD) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.create_connection(server.server_address, timeout=2) as client:
                    client.settimeout(2)
                    proposal = b"FD EM TEST12345678 10 20 0"
                    with client.makefile("rb") as reader:
                        self._handshake(client, reader)
                        client.sendall(proposal + b"\rF> " + f"{proposal_checksum([proposal]):02X}".encode() + b"\rFQ\r")
                        self.assertEqual(self._line(reader), b"FS =")
                        self.assertEqual(self._line(reader), b"FF")
            finally:
                server.shutdown()
                thread.join()

    def test_non_loopback_rejected(self):
        with self.assertRaises(ValueError):
            LabServer(("0.0.0.0", 0), None, "TEST1", self.PASSWORD)

    def test_corrupted_compressed_message_is_rejected(self):
        raw = b"Mid: TEST123\r\nBody: 5\r\n\r\nhello"
        data = lzhuf.compress(raw)
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            left, right = socket.socketpair()
            try:
                session = Session(left, store, "TEST1", self.PASSWORD)
                right.sendall(b"\x01\x06Lab\x000\x00" + bytes((2, len(data))) + data + bytes((4, (-sum(data) + 1) & 255)))
                with self.assertRaises(ProtocolError):
                    session.read_message("TEST123", len(raw), len(data))
                self.assertIsNone(store.b2f_lab_get("TEST1", "TEST123", "in"))
                session.reader.close()
            finally:
                left.close()
                right.close()

    def test_lab_message_duplicate_and_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            self.assertTrue(store.b2f_lab_store("TEST1", "ABC", b"first", "in"))
            self.assertFalse(store.b2f_lab_store("TEST1", "ABC", b"first", "in"))
            with self.assertRaises(ValueError):
                store.b2f_lab_store("TEST1", "ABC", b"different", "in")
            self.assertEqual(store.b2f_lab_get("TEST1", "ABC", "in"), b"first")

    @unittest.skipUnless(os.environ.get("FCMS_KISSTERM_PATH"), "set FCMS_KISSTERM_PATH to run kissterm interoperability")
    def test_kissterm_bidirectional_roundtrip(self):
        sys.path.insert(0, os.environ["FCMS_KISSTERM_PATH"])
        try:
            from kissterm.winlink.b2f import Client, Received, Sent
            from kissterm.winlink.message import build, serialize
            with tempfile.TemporaryDirectory() as directory:
                store = SQLiteStore(Path(directory) / "lab.sqlite")
                store.initialize()
                incoming = build(sender="KC1JMH", to=["LAB"], subject="Upload", body="Test upload")
                outgoing = build(sender="LAB", to=["KC1JMH"], subject="Download", body="Test download")
                store.b2f_lab_store("KC1JMH", outgoing.mid, serialize(outgoing), "out")
                with LabServer(("127.0.0.1", 0), store, "KC1JMH", self.PASSWORD) as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        with socket.create_connection(server.server_address, timeout=3) as conn:
                            conn.settimeout(3)
                            client = Client("KC1JMH", "FCMS", version="1.0", password=self.PASSWORD, outbound=[incoming])
                            while not client.done:
                                data = conn.recv(4096)
                                if not data:
                                    client.connection_lost()
                                    break
                                client.feed(data)
                                output = client.take_output()
                                if output:
                                    conn.sendall(output)
                            events = client.take_events()
                            self.assertEqual(client.error, "")
                            self.assertEqual([e.mid for e in events if isinstance(e, Sent)], [incoming.mid])
                            self.assertEqual([e.message.mid for e in events if isinstance(e, Received)], [outgoing.mid])
                            self.assertEqual(store.b2f_lab_get("KC1JMH", incoming.mid, "in"), serialize(incoming))
                    finally:
                        server.shutdown()
                        thread.join()
                self.assertEqual(SQLiteStore(Path(directory) / "lab.sqlite").b2f_lab_get("KC1JMH", incoming.mid, "in"), serialize(incoming))
        finally:
            sys.path.pop(0)
