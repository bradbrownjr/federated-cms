"""Optional offline Pat CLI integration using an explicit localhost shim.

The shim supplies Pat's Telnet login prompts but performs no authentication.
The Pat binary is never invoked with a Winlink CMS address or credentials.
"""
import os
from pathlib import Path
import socketserver
import tempfile
import threading
import unittest

from federated_cms.b2f import Session
from federated_cms.store import SQLiteStore
from tests.pat_fixture import run_pat_exchange, transport_login, verify_pat_exchange


class _PatShim(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.handle_session()
        finally:
            self.server.session_done.set()

    def handle_session(self):
        if transport_login(self.request):
            Session(self.request, self.server.store, "N0CALL", "fixture-only").run()


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        self.session_done = threading.Event()
        super().__init__(*args, **kwargs)


@unittest.skipUnless(os.environ.get("FCMS_PAT_BINARY"), "set FCMS_PAT_BINARY to run Pat integration")
class PatInteropTests(unittest.TestCase):
    def test_bidirectional_exchange(self):
        binary = str(Path(os.environ["FCMS_PAT_BINARY"]).resolve())
        with tempfile.TemporaryDirectory(prefix="fcms-pat-test-") as directory:
            root = Path(directory)
            store = SQLiteStore(root / 'lab.sqlite')
            with _Server(("127.0.0.1", 0), _PatShim) as server:
                server.store = store
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    uploaded = run_pat_exchange(binary, root, server.server_address[1], store, 'fixture-only')
                    self.assertTrue(server.session_done.wait(3), "Pat session did not finish")
                finally:
                    server.shutdown()
                    thread.join()
            verify_pat_exchange(root, SQLiteStore(root / 'lab.sqlite'), uploaded)
