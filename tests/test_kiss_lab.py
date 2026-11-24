"""Offline TCP KISS, real AX.25 and B2F laboratory integration."""
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from federated_cms.b2f import LabServer
from federated_cms.store import SQLiteStore
from tests.lab_mailbox_helpers import mailbox_call

KISSTERM = os.environ.get('FCMS_KISSTERM_PATH')
if KISSTERM:
    sys.path.insert(0, KISSTERM)
    from federated_cms.kiss_lab import (MAX_BYTES, LabGate, ScopedKiss, bridge,
                                       check_kiss_connection, main, run_lab)
    from kissterm.ax25.address import AX25Address, AX25Path
    from kissterm.ax25.station import AX25Station
    from kissterm.ax25.frame import AX25Frame, UType
    from kissterm.transport.base import SessionState
    from kissterm.transport.tcp_kiss import TcpKissTransport
    from kissterm.winlink.b2f import Client, Received, Sent
    from kissterm.winlink.message import build, serialize


@unittest.skipUnless(KISSTERM, 'set FCMS_KISSTERM_PATH for the optional KISS test')
class KissLabTests(unittest.IsolatedAsyncioTestCase):
    def test_gate_starts_closed_and_exhausts(self):
        gate = LabGate(2, 30)
        self.assertFalse(gate.allow())
        gate.set(True)
        self.assertTrue(gate.allow())
        self.assertTrue(gate.allow())
        self.assertFalse(gate.allow())
        self.assertFalse(gate.enabled)

    def test_no_enable_flag_opens_no_connections(self):
        argv = ['kiss_lab', '--kiss-host', '127.0.0.1', '--kiss-port', '1234',
                '--station', 'N0LAB-10', '--peer', 'N0CALL']
        with patch.object(sys, 'argv', argv), patch('federated_cms.kiss_lab.run_lab') as run:
            with self.assertRaises(SystemExit):
                main()
            run.assert_not_called()

    def test_invalid_or_duplicate_addresses_open_no_connections(self):
        for station, peer in (('N0LAB-16', 'N0CALL'), ('N0LAB*', 'N0CALL'),
                              ('N0CALL-0', 'N0CALL')):
            argv = ['kiss_lab', '--kiss-host', '127.0.0.1', '--kiss-port', '1234',
                    '--station', station, '--peer', peer, '--enable-tx']
            with self.subTest(station=station, peer=peer), patch.object(sys, 'argv', argv), \
                    patch('federated_cms.kiss_lab.run_lab') as run:
                with self.assertRaises(SystemExit):
                    main()
                run.assert_not_called()

    def test_check_only_rejects_transmit_flag(self):
        argv = ['kiss_lab', '--kiss-host', '127.0.0.1', '--kiss-port', '1234',
                '--station', 'N0LAB-10', '--peer', 'N0CALL', '--check-only', '--enable-tx']
        with patch.object(sys, 'argv', argv), patch('federated_cms.kiss_lab.run_lab') as run:
            with self.assertRaises(SystemExit):
                main()
            run.assert_not_called()

    def test_timeout_is_reported_without_a_traceback(self):
        argv = ['kiss_lab', '--kiss-host', '127.0.0.1', '--kiss-port', '1234',
                '--station', 'N0LAB-10', '--peer', 'N0CALL', '--enable-tx']
        output = io.StringIO()
        with (patch.object(sys, 'argv', argv), redirect_stderr(output),
              patch('federated_cms.kiss_lab.run_lab', new=Mock(return_value=object())),
              patch('federated_cms.kiss_lab.asyncio.run', side_effect=TimeoutError('limit reached'))):
            self.assertEqual(main(), 1)
        self.assertEqual(output.getvalue().strip(), 'Lab stopped: limit reached')

    async def test_filters_other_sources_channels_and_digipeaters(self):
        station, peer = AX25Address.parse('N0LAB-10'), AX25Address.parse('N0CALL')
        transport = ScopedKiss('127.0.0.1', 1, 0, station, peer)
        received = []
        transport.subscribe(lambda frame, port: received.append(frame))
        frame = lambda path: AX25Frame.u_frame(path, UType.SABM, pf=True)
        valid = frame(AX25Path(station, peer))
        output = io.StringIO()
        with redirect_stdout(output):
            await transport.dispatch(valid, 0)
            await transport.dispatch(valid, 1)
            await transport.dispatch(frame(AX25Path(station, AX25Address.parse('K1BAD'))), 0)
            await transport.dispatch(frame(AX25Path(station, peer, (AX25Address.parse('DIGI'),))), 0)
        self.assertEqual(received, [valid])
        self.assertIn('source=K1BAD kind=SABM direct=True accepted=False', output.getvalue())

    async def test_programmatic_start_requires_transmit_authorization(self):
        with patch('federated_cms.kiss_lab.ScopedKiss') as transport:
            with self.assertRaisesRegex(ValueError, 'transmit authorization'):
                await run_lab(SimpleNamespace(enable_tx=False))
            transport.assert_not_called()

    async def test_check_only_sends_no_bytes(self):
        received = asyncio.get_running_loop().create_future()

        async def accept(reader, writer):
            try:
                received.set_result(await reader.read())
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(accept, '127.0.0.1', 0)
        try:
            await check_kiss_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            self.assertEqual(await asyncio.wait_for(received, 3), b'')
        finally:
            server.close()
            await server.wait_closed()

    async def test_modem_disconnect_stops_instead_of_rearming(self):
        accepted = 0
        received = bytearray()

        async def drop(reader, writer):
            nonlocal accepted
            accepted += 1
            try:
                try:
                    received.extend(await asyncio.wait_for(reader.read(4096), .3))
                except TimeoutError:
                    pass
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(drop, '127.0.0.1', 0)
        args = SimpleNamespace(kiss_host='127.0.0.1', kiss_port=server.sockets[0].getsockname()[1],
                               channel=0, station='N0LAB-10', peer='N0CALL', b2f_port=1,
                               max_frames=10, seconds=4, enable_tx=True)
        try:
            with self.assertRaises(ConnectionError):
                await asyncio.wait_for(run_lab(args), 3)
            await asyncio.sleep(.7)
            self.assertEqual(accepted, 1)
            self.assertEqual(received, b'')
        finally:
            server.close()
            await server.wait_closed()

    async def test_unavailable_b2f_endpoint_ends_session(self):
        server = await asyncio.start_server(lambda reader, writer: None, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        link = Mock(on_data=[], on_state=[])
        link.read_nowait.return_value = b''
        finished = asyncio.Event()
        with self.assertRaises(OSError):
            await asyncio.wait_for(bridge(link, port, finished), 5)
        self.assertTrue(finished.is_set())
        self.assertEqual(link.on_data, [])
        self.assertEqual(link.on_state, [])

    async def test_disconnected_before_bridge_does_not_open_b2f(self):
        link = Mock(on_data=[], on_state=[], connected=False)
        link.read_nowait.return_value = b''
        finished = asyncio.Event()
        with patch('federated_cms.kiss_lab.asyncio.open_connection') as connect:
            with self.assertRaisesRegex(ConnectionError, 'before bridge startup'):
                await bridge(link, 63191, finished)
            connect.assert_not_called()
        self.assertTrue(finished.is_set())
        self.assertEqual(link.on_data, [])
        self.assertEqual(link.on_state, [])

    async def test_b2f_output_limit_stops_forwarding(self):
        async def send_excess(reader, writer):
            try:
                writer.write(b'X' * (MAX_BYTES + 1))
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(send_excess, '127.0.0.1', 0)
        link = SimpleNamespace(on_data=[], on_state=[], _outbound=bytearray(), unacked_sizes=[], connected=True,
                               read_nowait=lambda: b'')
        sent = bytearray()

        async def capture(data):
            sent.extend(data)

        link.send = capture
        finished = asyncio.Event()
        try:
            with self.assertRaisesRegex(ValueError, 'radio output limit'):
                await asyncio.wait_for(bridge(link, server.sockets[0].getsockname()[1], finished), 5)
            self.assertTrue(finished.is_set())
            self.assertLessEqual(len(sent), MAX_BYTES)
        finally:
            server.close()
            await server.wait_closed()

    async def test_bytes_received_before_bridge_start_are_forwarded(self):
        got = asyncio.get_running_loop().create_future()

        async def receive(reader, writer):
            try:
                got.set_result(await reader.readexactly(3))
            finally:
                writer.close()
                await writer.wait_closed()

        class BufferedLink:
            def __init__(self):
                self.on_data, self.on_state = [], []
                self._outbound, self.unacked_sizes = bytearray(), []
                self.buffered = b'abc'
                self.connected = True

            def read_nowait(self):
                data, self.buffered = self.buffered, b''
                return data

            async def disconnect(self):
                for callback in self.on_state:
                    callback(SessionState.DISCONNECTED)

        server = await asyncio.start_server(receive, '127.0.0.1', 0)
        try:
            await asyncio.wait_for(bridge(BufferedLink(), server.sockets[0].getsockname()[1],
                                          asyncio.Event()), 5)
            self.assertEqual(await asyncio.wait_for(got, 3), b'abc')
        finally:
            server.close()
            await server.wait_closed()

    async def test_local_kiss_b2f_exchange_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / 'lab.sqlite')
            store.initialize()
            upload = build(mid='KISSUPLOAD', sender='N0CALL', to=['LAB'], subject='Up',
                           body='Synthetic upload', files=[('up.bin', bytes(range(64)))])
            download = build(mid='KISSDOWN', sender='LAB', to=['N0CALL'], subject='Down',
                             body='Synthetic download', files=[('down.bin', bytes(range(64, 128)))])
            mailbox_call(store, 'N0CALL', 'store_message', download.mid, serialize(download), 'out')
            b2f = LabServer(('127.0.0.1', 0), store, 'N0CALL', 'fixture-only')
            thread = threading.Thread(target=b2f.serve_forever, daemon=True)
            thread.start()
            writers = []
            ready = asyncio.Event()

            async def accept(reader, writer):
                writers.append(writer)
                if len(writers) == 2:
                    ready.set()
                try:
                    await asyncio.wait_for(ready.wait(), 5)
                    while data := await reader.read(4096):
                        for other in writers:
                            if other is not writer:
                                other.write(data)
                                await other.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()

            hub = await asyncio.start_server(accept, '127.0.0.1', 0)
            port = hub.sockets[0].getsockname()[1]
            args = SimpleNamespace(kiss_host='127.0.0.1', kiss_port=port, channel=0,
                                   station='N0LAB-10', peer='N0CALL', b2f_port=b2f.server_address[1],
                                   max_frames=500, seconds=45, enable_tx=True)
            lab = asyncio.create_task(run_lab(args))
            transport = TcpKissTransport('127.0.0.1', port)
            client_station = None
            try:
                await transport.open()
                await asyncio.wait_for(ready.wait(), 5)
                source = AX25Address.parse('N0CALL')
                client_station = AX25Station(source, transport)
                incoming = asyncio.Queue()

                def capture(link):
                    link.on_data.append(lambda _: incoming.put_nowait(link.read_nowait()))

                client_station.on_link_created.append(capture)
                link = await client_station.connect(AX25Path(AX25Address.parse('N0LAB-10'), source), timeout=10)
                self.assertIsNotNone(link)
                client = Client('N0CALL', 'N0LAB-10', password='fixture-only', outbound=[upload])
                while not client.done:
                    chunk = await asyncio.wait_for(incoming.get(), 15)
                    client.feed(chunk)
                    if output := client.take_output():
                        await link.send(output)
                await asyncio.wait_for(lab, 15)
                events = client.take_events()
                self.assertFalse(client.error)
                self.assertEqual([(e.mid, e.already_had) for e in events if isinstance(e, Sent)],
                                 [(upload.mid, False)])
                self.assertEqual([e.raw for e in events if isinstance(e, Received)], [serialize(download)])
                reopened = SQLiteStore(Path(directory) / 'lab.sqlite')
                self.assertEqual(mailbox_call(reopened, 'N0CALL', 'list_incoming'), [(upload.mid, serialize(upload))])
                self.assertEqual(mailbox_call(reopened, 'N0CALL', 'pending'), [])
            finally:
                lab.cancel()
                await asyncio.gather(lab, return_exceptions=True)
                if client_station:
                    client_station.close()
                await transport.close()
                hub.close()
                await hub.wait_closed()
                for writer in writers:
                    writer.close()
                b2f.shutdown()
                thread.join(timeout=5)
                b2f.server_close()
