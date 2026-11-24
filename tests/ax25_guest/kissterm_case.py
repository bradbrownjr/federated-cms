import asyncio
from concurrent.futures import TimeoutError as FutureTimeout

from tests.rmsgw_full.chain_case import kissterm_exchange
from kissterm.ax25.address import AX25Address, AX25Path
from kissterm.ax25.station import AX25Station
from kissterm.transport.base import SessionState
from kissterm.transport.tcp_kiss import TcpKissTransport


class StationStream:
    def __init__(self, loop, link, events):
        self.loop = loop
        self.link = link
        self.events = events
        self.pending = bytearray()

    def recv(self, count):
        if not self.pending:
            future = asyncio.run_coroutine_threadsafe(self.events.get(), self.loop)
            try:
                data = future.result(timeout=20)
            except FutureTimeout:
                future.cancel()
                raise
            self.pending.extend(data)
        data = bytes(self.pending[:count])
        del self.pending[:count]
        return data

    def sendall(self, data):
        future = asyncio.run_coroutine_threadsafe(self.link.send(data), self.loop)
        future.result(timeout=10)


async def exchange(port, cms_listener, root, wrong_mailbox):
    source = AX25Address.parse('N0CALL')
    destination = AX25Address.parse('N0LAB-10')
    transport = TcpKissTransport('127.0.0.1', port)
    station = AX25Station(source, transport)
    events = asyncio.Queue()
    disconnected = asyncio.Event()

    def capture(link):
        link.on_data.append(events.put_nowait)

        def on_state(state):
            if state in (SessionState.DISCONNECTED, SessionState.FAILED):
                events.put_nowait(b'')
                disconnected.set()

        link.on_state.append(on_state)

    station.on_link_created.append(capture)
    await transport.open()
    try:
        link = await station.connect(AX25Path(destination, source), timeout=8)
        assert link is not None, 'kissterm could not connect over AX.25'
        loop = asyncio.get_running_loop()
        cms, _ = await loop.run_in_executor(None, cms_listener.accept)
        try:
            radio = StationStream(loop, link, events)
            await asyncio.to_thread(kissterm_exchange, cms, radio, root, wrong_mailbox)
            await link.disconnect()
            await asyncio.wait_for(disconnected.wait(), timeout=8)
            assert link.state is SessionState.DISCONNECTED, link.state
        finally:
            cms.close()
    finally:
        await transport.close()
