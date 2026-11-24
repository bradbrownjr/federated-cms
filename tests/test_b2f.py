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
from tests.lab_mailbox_helpers import mailbox_call


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
                mailbox_call(store, "TEST1", "store_message", "TEST123", b"Mid: TEST123\r\nBody: 0\r\n\r\n", "out")
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
                        self.assertEqual(len(mailbox_call(store, "TEST1", "pending")), 1)
                    finally:
                        server.shutdown()
                        thread.join()

    def test_client_refusal_without_handshake_keeps_mail_pending(self):
        # A Packet Winlink client may reject an unfamiliar server before
        # sending its SID or challenge response. This is not authentication.
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / 'lab.sqlite')
            store.initialize()
            raw = b'Mid: TEST123\r\nBody: 0\r\n\r\n'
            mailbox_call(store, 'TEST1', 'store_message', 'TEST123', raw, 'out')
            with LabServer(('127.0.0.1', 0), store, 'TEST1', self.PASSWORD) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with socket.create_connection(server.server_address, timeout=3) as client:
                        client.settimeout(3)
                        with client.makefile('rb') as reader:
                            self._line(reader)
                            self._line(reader)
                            self._line(reader)
                            client.sendall(b'Winlink session connected to Peer-to-Peer station\r')
                            self.assertEqual(self._line(reader), b'*** Protocol error')
                    self.assertEqual(mailbox_call(store, 'TEST1', 'pending'), [('TEST123', raw)])
                    self.assertEqual(mailbox_call(store, 'TEST1', 'list_incoming'), [])
                finally:
                    server.shutdown()
                    thread.join()

    def test_missing_response_and_old_challenge_response_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            mailbox_call(store, "TEST1", "store_message", "TEST123", b"Mid: TEST123\r\nBody: 0\r\n\r\n", "out")
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
                        self.assertEqual(len(mailbox_call(store, "TEST1", "pending")), 1)
                    finally:
                        server.shutdown()
                        thread.join()

    def test_outbound_delivery_requires_valid_next_turn(self):
        for response in (b"BAD\r", b"FC broken\r", b"", b"FF\r"):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                store = SQLiteStore(Path(directory) / "lab.sqlite")
                store.initialize()
                raw = b"Mid: TEST123\r\nBody: 5\r\n\r\nhello"
                mailbox_call(store, "TEST1", "store_message", "TEST123", raw, "out")
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
                                    if response == b"FF\r":
                                        client.sendall(b"FQ\r")
                                    self.assertEqual(reader.read(), b"")
                                else:
                                    client.shutdown(socket.SHUT_WR)
                                    reader.read()
                        pending = mailbox_call(store, "TEST1", "pending")
                        self.assertEqual(bool(pending), response != b"FF\r")
                    finally:
                        server.shutdown()
                        thread.join()

    def test_wire_duplicate_is_rejected_without_receiving_body(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            original = b"Mid: TEST123\r\nBody: 8\r\n\r\noriginal"
            mailbox_call(store, "TEST1", "store_message", "TEST123", original, "in")
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
                            self.assertEqual(reader.read(), b"")
                    self.assertEqual(mailbox_call(store, "TEST1", "get", "TEST123", "in"), original)
                finally:
                    server.shutdown()
                    thread.join()

    def test_invalid_multi_message_response_keeps_entire_outbox_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            for mid in ("TEST123", "TEST456"):
                mailbox_call(store, "TEST1", "store_message", mid, f"Mid: {mid}\r\nBody: 0\r\n\r\n".encode(), "out")
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
                            self.assertTrue(self._line(reader).startswith(b"FC EM TEST456 "))
                            self.assertTrue(self._line(reader).startswith(b"F> "))
                            client.sendall(b"FS -?\r")
                            self.assertEqual(self._line(reader), b"*** Protocol error")
                            self.assertEqual(reader.read(), b"")
                    self.assertEqual([mid for mid, _ in mailbox_call(store, "TEST1", "pending")], ["TEST123", "TEST456"])
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
                self.assertIsNone(mailbox_call(store, "TEST1", "get", "TEST123", "in"))
                session.reader.close()
            finally:
                left.close()
                right.close()

    def test_lab_message_duplicate_and_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            first = b"Mid: ABC\r\nBody: 5\r\n\r\nfirst"
            different = b"Mid: ABC\r\nBody: 9\r\n\r\ndifferent"
            self.assertTrue(mailbox_call(store, "TEST1", "store_message", "ABC", first, "in"))
            self.assertFalse(mailbox_call(store, "TEST1", "store_message", "ABC", first, "in"))
            with self.assertRaises(ValueError):
                mailbox_call(store, "TEST1", "store_message", "ABC", different, "in")
            self.assertEqual(mailbox_call(store, "TEST1", "get", "ABC", "in"), first)

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
                mailbox_call(store, "KC1JMH", "store_message", outgoing.mid, serialize(outgoing), "out")
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
                            while conn.recv(4096):
                                pass  # Wait for server-side session release and EOF.
                            events = client.take_events()
                            self.assertEqual(client.error, "")
                            self.assertEqual([e.mid for e in events if isinstance(e, Sent)], [incoming.mid])
                            self.assertEqual([e.message.mid for e in events if isinstance(e, Received)], [outgoing.mid])
                    finally:
                        server.shutdown()
                        thread.join()
                self.assertEqual(mailbox_call(SQLiteStore(Path(directory) / "lab.sqlite"), "KC1JMH", "get", incoming.mid, "in"), serialize(incoming))
        finally:
            sys.path.pop(0)
