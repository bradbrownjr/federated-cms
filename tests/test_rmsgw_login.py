"""Optional offline test of the pinned upstream rmsgw gateway login routine.

Set FCMS_RMSGW_SOURCE to an unmodified checkout and provide a C compiler.
Build products stay in a temporary directory. Only AF_UNIX socketpairs are
used: this test does not load gateway configuration, open an IP socket, run
reporting hooks, contact a CMS, or involve RF.
"""
from pathlib import Path
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest


RMSGW_REVISION = "3147f017545f43c4b83ef6a037b8b0e38d0719af"
HARNESS_SOURCE = Path(__file__).parent / "fixtures" / "rmsgw_login_harness.c"
SOURCE = os.environ.get("FCMS_RMSGW_SOURCE")
COMPILER = shutil.which(os.environ.get("CC", "cc"))
GIT = shutil.which("git")


def _read_cr(sock):
    result = bytearray()
    while len(result) < 256:
        byte = sock.recv(1)
        if not byte:
            raise EOFError("socket closed before line terminator")
        if byte == b"\r":
            return bytes(result)
        result.extend(byte)
    raise ValueError("gateway response exceeded limit")


@unittest.skipUnless(SOURCE, "set FCMS_RMSGW_SOURCE for the optional rmsgw test")
class RmsgwLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not sys.platform.startswith("linux"):
            raise RuntimeError("rmsgw login test requires Linux")
        if not COMPILER or not GIT:
            raise RuntimeError("rmsgw login test requires a C compiler and git")
        cls.source = Path(SOURCE).resolve()
        if not cls.source.is_dir():
            raise RuntimeError(f"rmsgw checkout does not exist: {cls.source}")
        revision = subprocess.run(["git", "-C", str(cls.source), "rev-parse", "HEAD"],
                                  check=True, capture_output=True, text=True, timeout=10).stdout.strip()
        if revision != RMSGW_REVISION:
            raise RuntimeError(f"rmsgw checkout must be pinned to {RMSGW_REVISION}; got {revision}")
        changed = subprocess.run(["git", "-C", str(cls.source), "status", "--porcelain", "--untracked-files=all"],
                                 check=True, capture_output=True, text=True, timeout=10).stdout
        if changed:
            raise RuntimeError("rmsgw checkout must not contain modifications or untracked files")
        cls.temp = tempfile.TemporaryDirectory(prefix="fcms-rmsgw-login-")
        cls.addClassCleanup(cls.temp.cleanup)
        build = Path(cls.temp.name)
        (build / "include").mkdir()
        (build / "pathnames.h").write_text("/* login-only build; no configured paths */\n", encoding="ascii")
        cls.harness = build / "gateway-login"
        result = subprocess.run([COMPILER, "-std=gnu11", "-I", str(build / "include"),
                                 "-I", str(cls.source / "include"),
                                 *(str(cls.source / "lib" / name) for name in
                                   ("cmslogin.c", "sglchallenge.c", "readln.c", "md5.c")),
                                 str(HARNESS_SOURCE), "-o", str(cls.harness)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(f"rmsgw login harness compile failed:\n{result.stderr}")

    def _start(self, password="FooBar"):
        local, gateway = socket.socketpair()
        local.settimeout(3)
        process = subprocess.Popen([str(self.harness), str(gateway.fileno()), password],
                                   pass_fds=(gateway.fileno(),), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        gateway.close()
        self.addCleanup(local.close)
        self.addCleanup(self._cleanup, process)
        return local, process

    @staticmethod
    def _cleanup(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)

    def test_fragmented_prompts_and_known_response(self):
        sock, process = self._start()
        for fragment in (b"Call", b"sign", b" :\r"):
            sock.sendall(fragment)
        self.assertEqual(_read_cr(sock), b"N0CALL N0LAB-10")
        for fragment in (b";S", b"Q: 2375", b"3528\r"):
            sock.sendall(fragment)
        self.assertEqual(_read_cr(sock), b";SR: 95074758 25000001 20")
        self.assertEqual(process.wait(timeout=3), 0)

    def test_incorrect_gateway_secret_fails_local_comparison(self):
        sock, process = self._start("wrong-fixture")
        sock.sendall(b"Callsign :\r")
        self.assertEqual(_read_cr(sock), b"N0CALL N0LAB-10")
        sock.sendall(b";SQ: 23753528\r")
        self.assertNotEqual(_read_cr(sock), b";SR: 95074758 25000001 20")
        # The upstream routine returns success after sending; a server must
        # check the response and close the connection on a mismatch.
        self.assertEqual(process.wait(timeout=3), 0)

    def test_unexpected_challenge_is_rejected(self):
        sock, process = self._start()
        sock.sendall(b"Callsign :\r")
        self.assertEqual(_read_cr(sock), b"N0CALL N0LAB-10")
        sock.sendall(b";NO: 23753528\r")
        self.assertEqual(process.wait(timeout=3), 1)

    def test_upstream_does_not_reject_nonnumeric_challenge(self):
        sock, process = self._start()
        sock.sendall(b"Callsign :\r")
        self.assertEqual(_read_cr(sock), b"N0CALL N0LAB-10")
        sock.sendall(b";SQ: ABCDEFGH\r")
        self.assertRegex(_read_cr(sock), rb"^;SR: [0-9]{8} 25000001 20$")
        self.assertEqual(process.wait(timeout=3), 0)

    def test_disconnect_before_challenge_fails(self):
        sock, process = self._start()
        sock.sendall(b"Callsign :\r")
        self.assertEqual(_read_cr(sock), b"N0CALL N0LAB-10")
        sock.shutdown(socket.SHUT_WR)
        self.assertEqual(process.wait(timeout=3), 1)
