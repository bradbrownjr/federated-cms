"""Scaled timing model; no physical modem, RF, or Express implementation."""
import asyncio
import os
import sys
import unittest

KISSTERM = os.environ.get('FCMS_KISSTERM_PATH')
if KISSTERM:
    sys.path.insert(0, KISSTERM)
    from kissterm.ax25.address import AX25Address, AX25Path
    from kissterm.ax25.frame import AX25Frame, SType, UType
    from kissterm.ax25.session import LinkParams
    from kissterm.ax25.station import AX25Station
    from kissterm.transport.base import FrameTransport, TransportInfo


@unittest.skipUnless(KISSTERM, 'set FCMS_KISSTERM_PATH')
class DelayedAckModel(unittest.IsolatedAsyncioTestCase):
    async def burst(self, t2, repeated_polls=0):
        class QueuedModem(FrameTransport):
            def __init__(self):
                super().__init__(TransportInfo(kind='test', name='queue', detail='offline'))
                self.queued = []

            async def open(self):
                pass

            async def close(self):
                pass

            async def _send_frame(self, frame, port=0):
                # Model a busy channel: retain frames until the burst ends.
                self.queued.append(frame)

        modem = QueuedModem()
        local, peer = AX25Address.parse('N0LAB-10'), AX25Address.parse('N0CALL')
        path = AX25Path(local, peer)
        station = AX25Station(local, modem, params=LinkParams(t2=t2), max_links=1)
        try:
            await modem.dispatch(AX25Frame.u_frame(path, UType.SABM, pf=True))
            modem.queued.clear()
            for ns in range(4):
                await modem.dispatch(AX25Frame.i_frame(path, ns=ns, nr=0, info=b'x' * 128,
                                                      pf=(ns == 3)))
                if ns < 3:
                    await asyncio.sleep(.04)
            for _ in range(repeated_polls):
                await modem.dispatch(AX25Frame.s_frame(path, SType.RR, nr=0, pf=True, command=True))
            await asyncio.sleep(.02)
            return [(f.stype, f.nr, f.pf) for f in modem.queued if f.kind == 'S']
        finally:
            station.close()

    async def test_delayed_ack_can_queue_stale_acknowledgments_during_burst(self):
        short = await self.burst(.01)
        longer = await self.burst(.3)
        self.assertEqual([nr for kind, nr, _ in short if kind == SType.RR], [1, 2, 3, 4])
        self.assertEqual(longer, [(SType.RR, 4, True)])

    async def test_repeated_polls_queue_repeated_final_responses(self):
        queued = await self.burst(.3, repeated_polls=4)
        self.assertEqual(queued, [(SType.RR, 4, True)] * 5)
