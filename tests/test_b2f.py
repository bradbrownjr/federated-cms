import socket
import threading
import unittest
import tempfile
from pathlib import Path
import os
import sys

from federated_cms.b2f import LabServer, Session, ProtocolError, proposal_checksum
from federated_cms import lzhuf
from federated_cms.store import SQLiteStore


class B2FLabTests(unittest.TestCase):
    def test_empty_exchange_and_proposal_deferral(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            self._deferral(store)

    def _deferral(self, store):
        with LabServer(("127.0.0.1", 0), store, "TEST1") as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.create_connection(server.server_address, timeout=2) as client:
                    client.settimeout(2)
                    proposal = b"FD EM TEST12345678 10 20 0"
                    client.sendall(b";FW: TEST1\r[client-1-B2FHM$]\r" + proposal + b"\rF> " + f"{proposal_checksum([proposal]):02X}".encode() + b"\rFQ\r")
                    data = bytearray()
                    while part := client.recv(1024):
                        data.extend(part)
                    self.assertIn(b"[FCMS-0.1-B2FHM$]\r", data)
                    self.assertIn(b"FS =\rFF\r", data)
            finally:
                server.shutdown()
                thread.join()

    def test_non_loopback_rejected(self):
        with self.assertRaises(ValueError):
            LabServer(("0.0.0.0", 0), None, "TEST1")

    def test_corrupted_compressed_message_is_rejected(self):
        raw = b"Mid: TEST123\r\nBody: 5\r\n\r\nhello"
        data = lzhuf.compress(raw)
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "lab.sqlite")
            store.initialize()
            left, right = socket.socketpair()
            try:
                session = Session(left, store, "TEST1")
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
                with LabServer(("127.0.0.1", 0), store, "KC1JMH") as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        with socket.create_connection(server.server_address, timeout=3) as conn:
                            conn.settimeout(3)
                            client = Client("KC1JMH", "FCMS", version="1.0", outbound=[incoming])
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
