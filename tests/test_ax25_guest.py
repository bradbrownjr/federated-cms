"""Fail closed if a pinned input or the required AX.25 evidence is missing."""
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.ax25_guest import run
from tests.ax25_guest.run import CASES, pins, unpack_packages, verify_log


class AX25GuestTests(unittest.TestCase):
    def test_corrupt_deb_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = next(iter(pins('packages.sha256')))
            (root / name).write_bytes(b'truncated')
            with patch('tests.ax25_guest.run.subprocess.run') as extract:
                with self.assertRaisesRegex(ValueError, 'archive checksum mismatch'):
                    unpack_packages(root, root)
                extract.assert_not_called()

    @unittest.skipUnless(sys.platform == 'linux', 'Linux launcher signal handling')
    def test_termination_kills_guest_group_and_removes_private_build(self):
        self.addCleanup(signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM))
        argv = ['run.py', '--source', '.', '--kissterm-source', '.', '--pat-binary', '.', '--packages', '.']
        with (patch.object(sys, 'argv', argv), patch.object(run.os, 'geteuid', return_value=1000),
              patch.object(run.subprocess, 'check_output', return_value='amd64'),
              patch.object(run, 'stage', side_effect=lambda args, build: build / 'packages') as stage,
              patch.object(run.subprocess, 'Popen') as spawn, patch.object(run.os, 'killpg') as killpg):
            child = spawn.return_value
            child.pid = 12345

            def wait(timeout=None):
                if timeout is not None:
                    self.assertIs(signal.getsignal(signal.SIGTERM), run.terminate)
                    signal.raise_signal(signal.SIGTERM)
                return 0

            child.wait.side_effect = wait
            with self.assertRaises(SystemExit) as result:
                run.main()
            self.assertEqual(result.exception.code, 128 + signal.SIGTERM)
            killpg.assert_called_once_with(child.pid, signal.SIGKILL)
            self.assertEqual(child.wait.call_count, 2)
            build = stage.call_args.args[1]
            self.assertFalse(build.exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux launcher signal handling')
    def test_termination_during_spawn_reaps_started_guest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(run.subprocess, 'Popen') as spawn,
                  patch.object(run.os, 'killpg') as killpg):
                child = spawn.return_value
                child.pid = 12345
                def start(*args, **kwargs):
                    signal.raise_signal(signal.SIGTERM)
                    return child
                spawn.side_effect = start
                with self.assertRaises(SystemExit) as result:
                    run.boot('pat', root, root, root / 'guest.log')
                self.assertEqual(result.exception.code, 128 + signal.SIGTERM)
                killpg.assert_called_once_with(child.pid, signal.SIGKILL)
                child.wait.assert_called_once_with()

    def test_require_one_dispatch_frames_case_result_and_clean_teardown(self):
        for case, outcome in CASES.items():
            lines = [
                'DISPATCH connected AF_AX25/SOCK_SEQPACKET radio N0CALL',
                'KISS N0CALL > N0LAB-10 port=0 SABM', 'KISS N0LAB-10 > N0CALL port=0 UA',
                'KISS N0CALL > N0LAB-10 port=0 I', 'KISS N0LAB-10 > N0CALL port=0 I',
                outcome, 'Observed DISC and answering UA', 'Stopped ax25d before KISS port teardown',
                'Observed gateway exit status 0',
                f'FCMS AX25 PASS {case}',
            ]
            verify_log(case, '\n'.join(lines))
            verify_log(case, '\n'.join(
                'Observed gateway exit status -13' if line == 'Observed gateway exit status 0' else line
                for line in lines))
            for index, marker in enumerate(lines):
                with self.subTest(case=case, missing=marker), self.assertRaises(RuntimeError):
                    verify_log(case, '\n'.join(lines[:index] + lines[index + 1:]))
            for error in (lines[0], 'Observed gateway exit status -13',
                          'Exception in thread relay', 'accept error Invalid argument'):
                with self.subTest(case=case, extra=error), self.assertRaises(RuntimeError):
                    verify_log(case, '\n'.join([*lines, error]))
