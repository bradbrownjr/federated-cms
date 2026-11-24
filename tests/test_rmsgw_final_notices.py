"""Test the experimental upstream patch against disposable local streams."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.rmsgw_full import run_experiment as gateway

ROOT = Path(__file__).resolve().parent
SOURCE = os.environ.get('FCMS_RMSGW_SOURCE')


@unittest.skipUnless(SOURCE, 'set FCMS_RMSGW_SOURCE for the optional upstream patch test')
class FinalNoticeTests(unittest.TestCase):
    def test_socket_pipe_and_existing_pending_signal(self):
        if sys.platform != 'linux' or not shutil.which('cc'):
            self.fail('the upstream patch test requires Linux and a C compiler')
        with tempfile.TemporaryDirectory(prefix='fcms-notice-test-') as directory:
            build = Path(directory)
            source = build / 'source'
            gateway.export_revision(Path(SOURCE).resolve(strict=True), gateway.REVISION, source)
            subprocess.run(['git', 'apply', '--unidiff-zero', str(ROOT / 'fixtures/rmsgw-final-notices.patch')],
                           cwd=source, check=True, env=gateway.ENV, timeout=10)
            (build / 'include').mkdir()
            (build / 'pathnames.h').write_text('/* isolated test */\n')
            executable = build / 'notice-test'
            subprocess.run(['cc', '-std=gnu11', '-ffunction-sections', '-fdata-sections',
                            '-I', str(build / 'include'), '-I', str(source / 'include'),
                            '-I', str(source), str(ROOT / 'fixtures/rmsgw-final-notices-test.c'),
                            str(source / 'lib/sendrf.c'), '-Wl,--gc-sections', '-o', str(executable)],
                           check=True, env=gateway.ENV, timeout=60)
            subprocess.run([str(executable)], check=True, env=gateway.ENV, timeout=5)
