"""Shared CMS lab mailbox behavior exercised through its public service API."""

from pathlib import Path
import socket
import tempfile
import threading
import unittest

from federated_cms import CMSCore, SQLiteStore
from federated_cms.b2f import LabServer
from federated_cms.b2f_auth import login_response
from tests.lab_mailbox_helpers import mailbox_call


class SharedLabInteropTests(unittest.TestCase):
    @staticmethod
    def _line(reader):
        result = bytearray()
        while (byte := reader.read(1)) and byte != b"\r":
            result.extend(byte)
        return bytes(result)

    def _start_client_turn(self, client, reader):
        self.assertIn(b"B2", self._line(reader))
        challenge = self._line(reader).decode().split(": ", 1)[1]
        self.assertIn(b">", self._line(reader))
        response = login_response(challenge, "fixture-only")
        client.sendall(f";FW: N0CALL\r[client-1-B2FHM$]\r;PR: {response}\rFF\r".encode())

    def test_deferred_message_waits_until_next_session(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "mail.sqlite")
            CMSCore(store)
            raw = b"Mid: TEST123\r\nBody: 5\r\n\r\nhello"
            mailbox_call(store, "N0CALL", "store_message", "TEST123", raw, "out")
            with LabServer(("127.0.0.1", 0), store, "N0CALL", "fixture-only") as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with socket.create_connection(server.server_address, timeout=3) as client:
                        client.settimeout(3)
                        with client.makefile("rb") as reader:
                            self._start_client_turn(client, reader)
                            self.assertTrue(self._line(reader).startswith(b"FC EM TEST123 "))
                            self.assertTrue(self._line(reader).startswith(b"F> "))
                            client.sendall(b"FS =\rFF\r")
                            # Deferral means later; repeated FF must not reoffer
                            # the same item until the client reconnects.
                            self.assertEqual(self._line(reader), b"FF")
                            client.sendall(b"FQ\r")
                            self.assertEqual(reader.read(), b"")
                    self.assertEqual(mailbox_call(store, "N0CALL", "pending"), [("TEST123", raw)])
                    with socket.create_connection(server.server_address, timeout=3) as client:
                        client.settimeout(3)
                        with client.makefile("rb") as reader:
                            self._start_client_turn(client, reader)
                            self.assertTrue(self._line(reader).startswith(b"FC EM TEST123 "))
                            self.assertTrue(self._line(reader).startswith(b"F> "))
                            client.sendall(b"FS =\rFQ\r")
                            self.assertEqual(reader.read(), b"")
                    self.assertEqual(mailbox_call(store, "N0CALL", "pending"), [("TEST123", raw)])
                finally:
                    server.shutdown()
                    thread.join()

    def test_second_socket_cannot_enter_same_mailbox_until_first_disconnects(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "mail.sqlite")
            CMSCore(store)
            raw = b"Mid: TEST123\r\nBody: 5\r\n\r\nhello"
            mailbox_call(store, "N0CALL", "store_message", "TEST123", raw, "out")
            with LabServer(("127.0.0.1", 0), store, "N0CALL", "fixture-only") as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with socket.create_connection(server.server_address, timeout=3) as first:
                        first.settimeout(3)
                        with first.makefile("rb") as first_reader:
                            self._start_client_turn(first, first_reader)
                            self.assertTrue(self._line(first_reader).startswith(b"FC EM TEST123 "))
                            self.assertTrue(self._line(first_reader).startswith(b"F> "))
                            with socket.create_connection(server.server_address, timeout=3) as second:
                                second.settimeout(3)
                                with second.makefile("rb") as second_reader:
                                    self._start_client_turn(second, second_reader)
                                    self.assertEqual(self._line(second_reader), b"*** Protocol error")
                                    self.assertEqual(second_reader.read(), b"")
                            first.sendall(b"FS =\rFQ\r")
                            self.assertEqual(first_reader.read(), b"")
                    self.assertEqual(mailbox_call(store, "N0CALL", "pending"), [("TEST123", raw)])
                    with socket.create_connection(server.server_address, timeout=3) as third:
                        third.settimeout(3)
                        with third.makefile("rb") as third_reader:
                            self._start_client_turn(third, third_reader)
                            self.assertTrue(self._line(third_reader).startswith(b"FC EM TEST123 "))
                            self.assertTrue(self._line(third_reader).startswith(b"F> "))
                            third.sendall(b"FS =\rFQ\r")
                            self.assertEqual(third_reader.read(), b"")
                finally:
                    server.shutdown()
                    thread.join()

    def test_scoped_mailboxes_do_not_expose_or_ack_other_account_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "mail.sqlite")
            service = CMSCore(store).lab_mailbox()
            alice = service.open_session("N0CALL")
            bob = service.open_session("K1ABC")
            try:
                raw = b"Mid: SAME123\r\nBody: 5\r\n\r\nhello"
                self.assertTrue(service.store_message(alice, "SAME123", raw, "out"))
                self.assertIsNone(service.get(bob, "SAME123", "out"))
                self.assertEqual(service.pending(bob), [])
                self.assertFalse(service.ack(bob, "SAME123"))
                self.assertEqual(service.pending(alice), [("SAME123", raw)])
                other = b"Mid: SAME123\r\nBody: 5\r\n\r\nother"
                self.assertTrue(service.store_message(bob, "SAME123", other, "out"))
                self.assertEqual(service.pending(bob), [("SAME123", other)])
                self.assertTrue(service.ack(bob, "SAME123"))
                self.assertEqual(service.pending(alice), [("SAME123", raw)])
            finally:
                service.close_session(alice)
                service.close_session(bob)
