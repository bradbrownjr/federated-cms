"""Scaled, lossless half-duplex AX.25 queue experiment, not an RF emulator."""
import argparse
import asyncio
import time

from kissterm.ax25.address import AX25Address, AX25Path
from kissterm.ax25.frame import AX25Frame
from kissterm.ax25.session import LinkParams
from kissterm.ax25.station import AX25Station
from kissterm.transport.base import FrameTransport, TransportInfo


async def experiment(t1, t2, payload_size=2048, airtime_scale=.00018, turnaround=.01):
    queue = asyncio.Queue()
    transmitted = []
    received = bytearray()
    completed = asyncio.Event()

    class Transport(FrameTransport):
        def __init__(self, name):
            super().__init__(TransportInfo(kind='test', name=name, detail='offline'))
            self.name = name
            self.peer = None

        async def open(self):
            pass

        async def close(self):
            pass

        async def _send_frame(self, frame, port=0):
            # KISS accepts a frame before it has actually finished on air.
            await queue.put((self, frame.encode(), frame.modulo, port))

    a, b = Transport('sender'), Transport('receiver')
    a.peer, b.peer = b, a

    async def channel():
        last = None
        while True:
            transport, raw, modulo, port = await queue.get()
            if last is not None and last != transport.name:
                await asyncio.sleep(turnaround)
            await asyncio.sleep(.005 + len(raw) * airtime_scale)
            frame = AX25Frame.decode(raw, modulo=modulo)
            transmitted.append((transport.name, frame.kind, frame.ns, frame.nr))
            await transport.peer.dispatch(frame, port)
            last = transport.name

    source, dest = AX25Address.parse('N0CALL'), AX25Address.parse('N0LAB-10')
    sender = AX25Station(source, a, params=LinkParams(paclen=128, window=4, t1=t1, t2=t2))
    receiver = AX25Station(dest, b, params=LinkParams(paclen=128, window=4, t1=t1, t2=t2))

    def on_link(link):
        def on_data(_):
            received.extend(link.read_nowait())
            if len(received) >= payload_size:
                completed.set()
        link.on_data.append(on_data)

    receiver.on_incoming.append(on_link)
    runner = asyncio.create_task(channel())
    start = time.monotonic()
    try:
        link = await sender.connect(AX25Path(dest, source), timeout=3)
        if link is None:
            return {'complete': False, 'reason': 'connect', 'frames': len(transmitted)}
        await link.send(bytes((i % 251 for i in range(payload_size))))
        try:
            await asyncio.wait_for(completed.wait(), 5)
        except TimeoutError:
            pass
        return {
            'complete': bytes(received) == bytes((i % 251 for i in range(payload_size))),
            'elapsed': round(time.monotonic() - start, 3),
            'sender_i': sum(name == 'sender' and kind == 'I' for name, kind, _, _ in transmitted),
            'receiver_s': sum(name == 'receiver' and kind == 'S' for name, kind, _, _ in transmitted),
        }
    finally:
        sender.close()
        receiver.close()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--t1', type=float, default=.2)
    parser.add_argument('--t2', type=float, default=.02)
    args = parser.parse_args()
    print(asyncio.run(experiment(args.t1, args.t2)))


if __name__ == '__main__':
    main()
