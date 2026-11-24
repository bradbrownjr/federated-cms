"""Fail closed if a pinned input or the required AX.25 evidence is missing."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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

    def test_require_one_dispatch_frames_case_result_and_clean_teardown(self):
        for case, outcome in CASES.items():
            lines = [
                'DISPATCH connected AF_AX25/SOCK_SEQPACKET radio N0CALL',
                'KISS N0CALL > N0LAB-10 port=0 SABM', 'KISS N0LAB-10 > N0CALL port=0 UA',
                'KISS N0CALL > N0LAB-10 port=0 I', 'KISS N0LAB-10 > N0CALL port=0 I',
                outcome, 'Observed DISC and answering UA', 'Stopped ax25d before KISS port teardown',
                f'FCMS AX25 PASS {case}',
            ]
            verify_log(case, '\n'.join(lines))
            for index, marker in enumerate(lines):
                with self.subTest(case=case, missing=marker), self.assertRaises(RuntimeError):
                    verify_log(case, '\n'.join(lines[:index] + lines[index + 1:]))
            for error in (lines[0], 'Exception in thread relay', 'accept error Invalid argument'):
                with self.subTest(case=case, extra=error), self.assertRaises(RuntimeError):
                    verify_log(case, '\n'.join([*lines, error]))
