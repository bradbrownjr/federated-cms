import asyncio
import unittest

from tests.radio_sid_relay import handle, run, ORIGINAL, SID


class RadioRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_rejects_overlong_sid(self):
        async def source(reader, writer):
            writer.write(b'x' * 600 + b'\r')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream = await asyncio.start_server(source, '127.0.0.1', 0)
        try:
            with self.assertRaises(asyncio.LimitOverrunError):
                await run(0, upstream.sockets[0].getsockname()[1], 1)
        finally:
            upstream.close()
            await upstream.wait_closed()

    async def test_client_half_close_still_receives_final_reply(self):
        payload = bytes(range(256))

        async def source(reader, writer):
            writer.write(ORIGINAL)
            await writer.drain()
            await reader.read()
            await asyncio.sleep(.05)
            writer.write(payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream = await asyncio.start_server(source, '127.0.0.1', 0)
        target = upstream.sockets[0].getsockname()[1]
        relay = await asyncio.start_server(lambda r, w: handle(r, w, target, 1), '127.0.0.1', 0)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', relay.sockets[0].getsockname()[1])
            self.assertEqual(await reader.readuntil(b'\r'), SID)
            writer.write_eof()
            self.assertEqual(await asyncio.wait_for(reader.read(), 2), payload)
            writer.close()
            await writer.wait_closed()
        finally:
            relay.close()
            upstream.close()
            await relay.wait_closed()
            await upstream.wait_closed()

    async def test_unavailable_mailbox_fails_before_relay_is_armed(self):
        import socket
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            missing_port = reservation.getsockname()[1]
        with self.assertRaises(OSError):
            await run(0, missing_port, 1)

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
