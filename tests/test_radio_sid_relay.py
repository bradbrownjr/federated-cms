import asyncio
import unittest

from tests.radio_sid_relay import handle, ORIGINAL, SID


class RadioRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_sid_changes_and_binary_data_passes(self):
        payload = bytes(range(256))
        received = asyncio.get_running_loop().create_future()

        async def source(reader, writer):
            writer.write(ORIGINAL + payload)
            await writer.drain()
            received.set_result(await reader.readexactly(len(payload)))
            writer.close()

        upstream = await asyncio.start_server(source, '127.0.0.1', 0)
        target = upstream.sockets[0].getsockname()[1]
        relay = await asyncio.start_server(lambda r, w: handle(r, w, target), '127.0.0.1', 0)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', relay.sockets[0].getsockname()[1])
            self.assertEqual(await reader.readuntil(b'\r'), SID)
            self.assertEqual(await reader.readexactly(len(payload)), payload)
            writer.write(payload)
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(received, 3), payload)
            writer.close()
            await writer.wait_closed()
        finally:
            relay.close()
            upstream.close()
            await relay.wait_closed()
            await upstream.wait_closed()
