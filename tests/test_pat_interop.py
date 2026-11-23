"""Optional offline Pat CLI integration using an explicit localhost shim.

The shim supplies Pat's Telnet login prompts but performs no authentication.
The Pat binary is never invoked with a Winlink CMS address or credentials.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socketserver
import subprocess
import tempfile
import threading
import unittest

from federated_cms.b2f import Session
from federated_cms.store import SQLiteStore


class _PatShim(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        # One-byte reads avoid consuming any subsequent B2F bytes.
        def read_line():
            result = bytearray()
            while len(result) < 128:
                byte = self.request.recv(1)
                if not byte:
                    return b""
                if byte == b"\r":
                    return bytes(result)
                result.extend(byte)
            return b""

        self.request.sendall(b"Callsign:\r")
        caller = read_line()
        self.request.sendall(b"Password:\r")
        password = read_line()
        if caller == b"N0CALL" and password == b"lab-only":
            Session(self.request, self.server.store, "N0CALL", "fixture-only").run()


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


@unittest.skipUnless(os.environ.get("FCMS_PAT_BINARY"), "set FCMS_PAT_BINARY to run Pat integration")
class PatInteropTests(unittest.TestCase):
    def test_bidirectional_exchange(self):
        binary = str(Path(os.environ["FCMS_PAT_BINARY"]).resolve())
        with tempfile.TemporaryDirectory(prefix="fcms-pat-test-") as directory:
            root = Path(directory)
            env = os.environ.copy()
            for kind in ("CONFIG", "DATA", "STATE"):
                path = root / kind.lower()
                (path / "pat").mkdir(parents=True)
                env[f"XDG_{kind}_HOME"] = str(path)
            # Block accidental HTTP service access as well as disabling Pat's
            # version reporter. Cache the account-confirmation check so Pat's
            # connect command does not query the production account API.
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                env[key] = "http://127.0.0.1:9"
            env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
            env["GZIP_EXPERIMENT"] = "0"
            cache = root / "state" / "pat" / ".account-confirmed_N0CALL.json"
            cache.write_text(json.dumps(datetime.now(timezone.utc).isoformat()), encoding="utf-8")
            config = root / "config.json"
            config.write_text(json.dumps({"mycall": "N0CALL", "version_reporting_disabled": True,
                                          "secure_login_password": "fixture-only", "locator": "FN43"}), encoding="utf-8")
            options = [binary, "--config", str(config), "--mbox", str(root / "mail"),
                       "--log", str(root / "pat.log"), "--event-log", str(root / "events.json")]
            composed = subprocess.run(options + ["compose", "--subject", "Lab upload", "LAB"],
                                      input="Synthetic upload\n", text=True, capture_output=True, env=env, timeout=15)
            self.assertEqual(composed.returncode, 0, composed.stdout + composed.stderr)
            store = SQLiteStore(root / "lab.sqlite")
            store.initialize()
            raw = (b"Mid: LABTEST12345\r\nBody: 18\r\nDate: 2026/01/01 00:00\r\n"
                   b"From: LAB\r\nSubject: Lab download\r\nTo: N0CALL\r\nType: Private\r\n\r\nSynthetic download")
            store.b2f_lab_store("N0CALL", "LABTEST12345", raw, "out")
            with _Server(("127.0.0.1", 0), _PatShim) as server:
                server.store = store
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f"telnet://N0CALL:lab-only@127.0.0.1:{server.server_address[1]}/WL2K"
                    result = subprocess.run(options + ["connect", url], text=True, capture_output=True, env=env, timeout=30)
                finally:
                    server.shutdown()
                    thread.join()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(store.b2f_lab_outbox("N0CALL"), [])
            with store._connect() as con:
                rows = con.execute("SELECT raw FROM b2f_lab_messages WHERE account='N0CALL' AND direction='in'").fetchall()
            self.assertEqual(len(rows), 1, result.stdout + result.stderr)
            self.assertIn(b"Synthetic upload", rows[0]["raw"])
            files = list((root / "mail" / "N0CALL").rglob("*"))
            self.assertTrue(any(p.is_file() and b"Synthetic download" in p.read_bytes() for p in files))
