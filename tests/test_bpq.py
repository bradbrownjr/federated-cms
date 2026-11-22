import io
import socket
import threading
import time
import tempfile
from pathlib import Path
import unittest

from federated_cms.bpq import CMSListener, TerminalSession, make_core, parse_callsign


def transcript(core, text):
    out = io.BytesIO()
    TerminalSession(core, io.BytesIO(text.encode("ascii")), out).run()
    return out.getvalue().decode("ascii")


def _socket_exchange(port, lines):
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        sock.settimeout(2)
        sock.sendall(lines.encode("ascii"))
        data = b""
        while True:
            try:
                part = sock.recv(4096)
            except socket.timeout:
                break
            if not part:
                break
            data += part
    return data.decode("ascii")


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_stdio_commands_and_callsign(self):
        result = transcript(make_core(self.path / "a.sqlite"), "KC1JMH-8\r\nHELP\nWX FN43\nJOBS\nQUIT\n")
        self.assertIn("Station: KC1JMH-8", result)
        self.assertIn("HELP STATUS", result)
        self.assertIn("Request queued:", result)
        self.assertIn("completed", result)
        self.assertIn("Bye.", result)
        self.assertEqual(parse_callsign("KC1JMH-8"), ("KC1JMH-8", "KC1JMH", "8"))

    def test_invalid_and_oversized_input(self):
        result = transcript(make_core(self.path / "a.sqlite"), "HELP\n" + "X" * 300 + "\nNOPE\nQUIT\n")
        self.assertIn("ERR line too long", result)
        self.assertIn("ERR unknown command", result)

    def test_socket_entrypoint_concurrent_and_equivalent(self):
        core = make_core(self.path / "socket.sqlite")
        with CMSListener(("127.0.0.1", 0), core, idle_timeout=2) as server:
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            outputs = []
            workers = [threading.Thread(target=lambda: outputs.append(_socket_exchange(port, "KC1JMH\nHELP\nWX FN43\nQUIT\n"))) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers: worker.join()
            server.shutdown()
        self.assertEqual(len(outputs), 2)
        self.assertTrue(all("HELP STATUS" in out and "Request queued:" in out and "Bye." in out for out in outputs))
        self.assertEqual(core.status()["completed"], 2)
