"""One-session TCP KISS to loopback B2F bridge for supervised lab work.

This opt-in prototype uses kissterm's AX.25 implementation. It is not a
production gateway; run only with an isolated synthetic B2F mailbox.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
from datetime import UTC, datetime
import re
import time

from kissterm.ax25.address import AX25Address
from kissterm.ax25.station import AX25Station
from kissterm.transport.base import SessionState, TransportState
from kissterm.transport.tcp_kiss import TcpKissTransport
from kissterm.tx import TransmitGate

MAX_BYTES = 65536
QUEUE_ITEMS = 256


def address(text: str) -> AX25Address:
    if not re.fullmatch(r'[A-Za-z0-9]{1,6}(?:-(?:[0-9]|1[0-5]))?', text):
        raise ValueError('invalid direct AX.25 callsign or SSID')
    return AX25Address.parse(text)


class LabGate(TransmitGate):
    """Bound frames and wall time; start closed and never reopen on loss."""

    def __init__(self, limit: int, seconds: int):
        super().__init__(enabled=False)
        self.remaining = limit
        self.deadline = time.monotonic() + seconds

    def allow(self) -> bool:
        if not self.enabled or self.remaining <= 0 or time.monotonic() >= self.deadline:
            self.set(False)
            return False
        self.remaining -= 1
        return True


class ScopedKiss(TcpKissTransport):
    """Deliver only direct frames from the configured peer on one port."""

    def __init__(self, host, tcp_port, kiss_port, station, peer):
        super().__init__(host, tcp_port, ports=kiss_port + 1)
        self.kiss_port, self.station, self.peer = kiss_port, station, peer

    async def dispatch(self, frame, port=0):
        source, dest = frame.path.source, frame.path.destination
        if (port == self.kiss_port and not frame.path.repeaters
                and (source.callsign, source.ssid) == (self.peer.callsign, self.peer.ssid)
                and (dest.callsign, dest.ssid) == (self.station.callsign, self.station.ssid)):
            await super().dispatch(frame, port)


async def bridge(link, b2f_port: int, finished: asyncio.Event):
    """Relay one AX.25 byte stream to the existing loopback B2F lab."""
    incoming: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=QUEUE_ITEMS)
    bad = asyncio.Event()
    overflow = asyncio.Event()
    disconnected = asyncio.Event()

    def on_data(_data):
        data = link.read_nowait()  # also drains kissterm's internal input buffer
        try:
            incoming.put_nowait(data)
        except asyncio.QueueFull:
            overflow.set()
            bad.set()

    def on_state(state):
        if state in (SessionState.DISCONNECTED, SessionState.FAILED):
            bad.set()
            disconnected.set()

    link.on_data.append(on_data)
    link.on_state.append(on_state)
    # A peer may send an I frame immediately after UA, before this coroutine
    # gets its first turn. Preserve bytes kissterm has already buffered.
    buffered = link.read_nowait()
    if buffered:
        incoming.put_nowait(buffered)
    writer = None
    server_complete = False
    try:
        if not link.connected:
            raise ConnectionError('radio link closed before bridge startup')
        reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', b2f_port), 5)

        async def to_cms():
            total = 0
            while True:
                data = await incoming.get()
                total += len(data)
                if total > MAX_BYTES:
                    raise ValueError('radio input limit exceeded')
                writer.write(data)
                await writer.drain()

        async def to_radio():
            nonlocal server_complete
            total = 0
            while True:
                data = await reader.read(256)
                if not data:
                    # Do not drop final bytes merely queued in the AX.25 link.
                    deadline = time.monotonic() + 120
                    while link._outbound or link.unacked_sizes:
                        if bad.is_set() or time.monotonic() >= deadline:
                            raise TimeoutError('radio output was not acknowledged')
                        await asyncio.sleep(.1)
                    server_complete = True
                    await link.disconnect()
                    await asyncio.wait_for(disconnected.wait(), 10)
                    return
                total += len(data)
                if total > MAX_BYTES:
                    raise ValueError('radio output limit exceeded')
                await link.send(data)
                # Bound kissterm's outbound buffer; it queues rather than
                # waiting for all frames to be acknowledged.
                while len(link._outbound) > 1024:
                    if bad.is_set():
                        raise ConnectionError('radio link closed')
                    await asyncio.sleep(.1)

        tasks = [asyncio.create_task(to_cms()), asyncio.create_task(to_radio()),
                 asyncio.create_task(bad.wait())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if overflow.is_set() or (bad.is_set() and not server_complete):
                raise ConnectionError('radio session ended before the B2F server completed')
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        link.on_data.remove(on_data)
        link.on_state.remove(on_state)
        finished.set()


async def run_lab(args):
    if not getattr(args, 'enable_tx', False):
        raise ValueError('explicit transmit authorization is required')
    station_addr = address(args.station)
    peer = address(args.peer)
    if (station_addr.callsign, station_addr.ssid) == (peer.callsign, peer.ssid):
        raise ValueError('station and peer must have distinct AX.25 addresses')
    transport = ScopedKiss(args.kiss_host, args.kiss_port, args.channel, station_addr, peer)
    gate = LabGate(args.max_frames, args.seconds)
    transport.gate = gate

    def log_sent(frame, port):
        # Log metadata only; do not put challenge responses or B2 content in logs.
        timestamp = datetime.now(UTC).isoformat(timespec='seconds')
        print(f'TX {timestamp} port={port} kind={frame.kind} bytes={len(frame.encode())}', flush=True)

    transport.on_sent.append(log_sent)
    finished = asyncio.Event()
    station = None
    task = None
    try:
        await transport.open()  # gate is closed before opening the modem connection
        station = AX25Station(station_addr, transport, max_links=1)

        def accepted(link):
            nonlocal task
            if task is None:
                task = asyncio.create_task(bridge(link, args.b2f_port, finished))

        station.on_incoming.append(accepted)
        gate.set(True)
        print('Lab transmit enabled; waiting for one direct session. Ctrl-C stops it.', flush=True)
        deadline = time.monotonic() + args.seconds
        while not finished.is_set():
            if transport.state is not TransportState.OPEN:
                raise ConnectionError('KISS connection lost; transmit disabled')
            if not gate.enabled or gate.remaining <= 0 or time.monotonic() >= deadline:
                raise TimeoutError('lab transmit limit reached')
            await asyncio.sleep(.1)
        if task is not None:
            await task
            print('B2F lab session finished; disabling transmit.', flush=True)
    finally:
        gate.set(False)  # shutdown never transmits a delayed frame
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if station is not None:
            station.close()
        await transport.close()


async def check_kiss_connection(host: str, port: int):
    """Check TCP reachability without opening a transmit-capable transport."""
    _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
    writer.close()
    await writer.wait_closed()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kiss-host', required=True, help='operator-provided TCP KISS host')
    parser.add_argument('--kiss-port', required=True, type=int)
    parser.add_argument('--channel', type=int, default=0)
    parser.add_argument('--station', required=True, help='local AX.25 callsign and SSID')
    parser.add_argument('--peer', required=True, help='one direct peer callsign and SSID')
    parser.add_argument('--b2f-port', type=int, default=63191)
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--max-frames', type=int, default=500)
    parser.add_argument('--check-only', action='store_true', help='connect and close without sending data')
    parser.add_argument('--enable-tx', action='store_true', help='explicitly enable lab transmission')
    args = parser.parse_args()
    if args.check_only and args.enable_tx:
        parser.error('--check-only cannot be combined with --enable-tx')
    if not args.check_only and not args.enable_tx:
        parser.error('the lab requires explicit --enable-tx; no connection was opened')
    if not (1 <= args.kiss_port <= 65535 and 1 <= args.b2f_port <= 65535
            and 0 <= args.channel <= 15 and 1 <= args.seconds <= 3600
            and 1 <= args.max_frames <= 5000):
        parser.error('port, channel or lab limits are out of range')
    try:
        local, peer = address(args.station), address(args.peer)
        if (local.callsign, local.ssid) == (peer.callsign, peer.ssid):
            parser.error('station and peer must have distinct AX.25 addresses')
    except ValueError as exc:
        parser.error(str(exc))
    if args.check_only:
        asyncio.run(check_kiss_connection(args.kiss_host, args.kiss_port))
        print('TCP KISS endpoint reachable; no data sent.', flush=True)
        return
    try:
        asyncio.run(run_lab(args))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
