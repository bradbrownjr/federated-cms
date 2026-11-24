import socket
import threading
import unittest

from tests.sid_probe import probe


class SIDProbeTests(unittest.TestCase):
    def test_one_connection_sends_sid_without_accessing_credentials(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            results = []
            thread = threading.Thread(target=lambda: results.append(probe(listener, "[FCMS-0.1-B2FHM$]", 2)))
            thread.start()
            with socket.create_connection(listener.getsockname(), timeout=2) as peer:
                with peer.makefile("rb") as reader:
                    self.assertEqual(reader.read(len(b"[FCMS-0.1-B2FHM$]\rFCMS SID PROBE >\r")),
                                     b"[FCMS-0.1-B2FHM$]\rFCMS SID PROBE >\r")
                peer.sendall(b"private input must never be printed")
            thread.join(3)
            self.assertEqual(results, ["client sent data"])

    def test_rejects_injected_line(self):
        with socket.socket() as listener:
            with self.assertRaises(ValueError):
                probe(listener, "[FCMS]\r;PQ: 12345678")
