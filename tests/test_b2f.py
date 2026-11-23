import socket
import threading
import unittest

from federated_cms.b2f import LabServer, proposal_checksum


class B2FLabTests(unittest.TestCase):
    def test_empty_exchange_and_proposal_deferral(self):
        with LabServer(("127.0.0.1", 0)) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.create_connection(server.server_address, timeout=2) as client:
                    client.settimeout(2)
                    proposal = b"FC EM TEST12345678 10 20 0"
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
            LabServer(("0.0.0.0", 0))
