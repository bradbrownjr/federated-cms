"""Offline B2F interoperability: binary attachments, retries and duplicates."""

import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from federated_cms.b2f import LabServer, _Handler
from federated_cms.store import SQLiteStore
from tests.lab_mailbox_helpers import mailbox_call


@unittest.skipUnless(os.environ.get("FCMS_KISSTERM_PATH"), "set FCMS_KISSTERM_PATH for kissterm tests")
class KisstermRecoveryTests(unittest.TestCase):
    ACCOUNT = "N0CALL"
    PASSWORD = "fixture-only"

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, os.environ["FCMS_KISSTERM_PATH"])
        try:
            from kissterm.winlink import b2f, message
            cls.b2f, cls.message = b2f, message
        finally:
            sys.path.pop(0)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fcms-b2f-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "lab.sqlite"
        self.store = SQLiteStore(self.db_path)
        self.store.initialize()

    def start_server(self):
        server = LabServer(("127.0.0.1", 0), self.store, self.ACCOUNT, self.PASSWORD)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return server

    def exchange(self, server, outbound=(), have=None):
        client = self.b2f.Client(self.ACCOUNT, "FCMS", version="1.0", password=self.PASSWORD,
                                 outbound=list(outbound), have=have)
        with socket.create_connection(server.server_address, timeout=3) as conn:
            conn.settimeout(3)
            while not client.done:
                data = conn.recv(4096)
                if not data:
                    client.connection_lost()
                    break
                client.feed(data)
                output = client.take_output()
                if output:
                    conn.sendall(output)
        self.assertEqual(client.error, "")
        return client.take_events()

    def test_bidirectional_binary_attachments_and_wire_duplicate(self):
        incoming_file = bytes(range(256)) * 4 + b"\x00\xff\r\n"
        outgoing_file = bytes(reversed(range(256))) * 3 + b"\r\n\x00"
        incoming = self.message.build(sender=self.ACCOUNT, to=["LAB"], subject="Attachment up",
                                      body="Upload", files=[("upload.bin", incoming_file)])
        outgoing = self.message.build(sender="LAB", to=[self.ACCOUNT], subject="Attachment down",
                                      body="Download", files=[("download.bin", outgoing_file)])
        mailbox_call(self.store, self.ACCOUNT, "store_message", outgoing.mid, self.message.serialize(outgoing), "out")
        server = self.start_server()

        events = self.exchange(server, outbound=[incoming])
        received = [event for event in events if isinstance(event, self.b2f.Received)]
        sent = [event for event in events if isinstance(event, self.b2f.Sent)]
        self.assertEqual([(event.mid, event.already_had) for event in sent], [(incoming.mid, False)])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].message.files, [("download.bin", outgoing_file)])
        persisted = mailbox_call(SQLiteStore(self.db_path), self.ACCOUNT, "get", incoming.mid, "in")
        self.assertEqual(persisted, self.message.serialize(incoming))
        self.assertEqual(self.message.parse(persisted).files, [("upload.bin", incoming_file)])
        self.assertEqual(mailbox_call(self.store, self.ACCOUNT, "pending"), [])

        events = self.exchange(server, outbound=[incoming])
        sent = [event for event in events if isinstance(event, self.b2f.Sent)]
        self.assertEqual([(event.mid, event.already_had) for event in sent], [(incoming.mid, True)])
        self.assertEqual(mailbox_call(self.store, self.ACCOUNT, "get", incoming.mid, "in"), persisted)

    def test_interrupted_upload_is_retried_after_restart(self):
        payload = bytes(range(256)) * 8
        message = self.message.build(sender=self.ACCOUNT, to=["LAB"], subject="Interrupted",
                                     body="Retry this", files=[("retry.bin", payload)])
        server = self.start_server()
        worker_done = threading.Event()

        class ObservedHandler(_Handler):
            def handle(self):
                try:
                    super().handle()
                finally:
                    worker_done.set()

        server.RequestHandlerClass = ObservedHandler
        client = self.b2f.Client(self.ACCOUNT, "FCMS", version="1.0", password=self.PASSWORD,
                                 outbound=[message])
        with socket.create_connection(server.server_address, timeout=3) as conn:
            conn.settimeout(3)
            while True:
                data = conn.recv(4096)
                self.assertTrue(data)
                client.feed(data)
                output = client.take_output()
                if output.startswith(b"\x01"):
                    # Disconnect in the middle of the actual client's message.
                    conn.sendall(output[:len(output) // 2])
                    break
                if output:
                    conn.sendall(output)

        # Wait until the server observes EOF before checking persistent state.
        self.assertTrue(worker_done.wait(3), "server did not finish the interrupted session")
        server.shutdown()
        server.server_close()
        self.assertIsNone(mailbox_call(SQLiteStore(self.db_path), self.ACCOUNT, "get", message.mid, "in"))

        restarted = self.start_server()
        events = self.exchange(restarted, outbound=[message])
        sent = [event for event in events if isinstance(event, self.b2f.Sent)]
        self.assertEqual([(event.mid, event.already_had) for event in sent], [(message.mid, False)])
        recovered = mailbox_call(SQLiteStore(self.db_path), self.ACCOUNT, "get", message.mid, "in")
        self.assertEqual(recovered, self.message.serialize(message))
        self.assertEqual(self.message.parse(recovered).files, [("retry.bin", payload)])
