import os
import sys
import unittest

KISSTERM = os.environ.get('FCMS_KISSTERM_PATH')
if KISSTERM:
    sys.path.insert(0, KISSTERM)
    from tests.radio_timing_model import experiment


@unittest.skipUnless(KISSTERM, 'set FCMS_KISSTERM_PATH')
class RadioTimingModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_sender_timer_shorter_than_queued_airtime_causes_retransmissions(self):
        short = await experiment(.12, .02)
        longer = await experiment(.8, .02)
        self.assertTrue(longer['complete'])
        # A 2048-byte payload requires sixteen 128-byte I frames.
        self.assertGreater(short['sender_i'], 16)
        self.assertEqual(longer['sender_i'], 16)
