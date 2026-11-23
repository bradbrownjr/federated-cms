"""Offline tests of the synthetic gateway-to-B2F handoff."""
from concurrent.futures import ThreadPoolExecutor
import socket
import unittest
from unittest.mock import patch

from federated_cms.b2f_auth import login_response
from tests.rmsgw_full import sgl_adapter as adapter


class GatewayAdapterTests(unittest.TestCase):
    def _start(self):
        client, server = socket.socketpair()
        client.settimeout(3)
        store = object()

        def serve():
            with server:
                return adapter.serve_gateway(server, store)

        pool = self.enterContext(ThreadPoolExecutor(max_workers=1))
        future = pool.submit(serve)
        self.addCleanup(client.close)
        reader = self.enterContext(client.makefile('rb'))
        self.assertEqual(reader.read(len(b'Callsign :\r')), b'Callsign :\r')
        return client, reader, future, store

    @patch.object(adapter, 'Session')
    def test_wrong_identity_rejection(self, session):
        for identity in (b'N0CALL OTHER\r', b'OTHER N0LAB-10\r', b'x' * 128, b'\xff'):
            with self.subTest(identity=identity):
                client, reader, future, _ = self._start()
                client.sendall(identity)
                self.assertEqual(reader.read(), adapter.REJECTION)
                self.assertFalse(future.result())
        session.assert_not_called()

    @patch.object(adapter, 'Session')
    @patch.object(adapter.secrets, 'randbelow', return_value=23753528)
    def test_stale_response_wrong_secret_and_metadata_rejected(self, _random, session):
        for response in (login_response('12345678', adapter.GATEWAY_PASSWORD) + ' 25000001 20',
                         login_response('23753528', adapter.MAILBOX_PASSWORD) + ' 25000001 20',
                         '95074758 25000002 20', '95074758 25000001 20 extra'):
            with self.subTest(response=response):
                client, reader, future, _ = self._start()
                client.sendall(b'N0CALL N0LAB-10\r')
                self.assertEqual(reader.read(len(b';SQ: 23753528\r')), b';SQ: 23753528\r')
                client.sendall(f';SR: {response}\r'.encode('ascii'))
                self.assertEqual(reader.read(), adapter.REJECTION)
                self.assertFalse(future.result())
        session.assert_not_called()

    @patch.object(adapter, 'Session')
    @patch.object(adapter.secrets, 'randbelow', return_value=23753528)
    def test_success_hands_pending_bytes_to_separate_b2f_login(self, _random, session):
        client, reader, future, store = self._start()
        client.sendall(b'N0CALL N0LAB-10\r')
        self.assertEqual(reader.read(len(b';SQ: 23753528\r')), b';SQ: 23753528\r')
        # This also catches a buffered SGL parser discarding pipelined bytes.
        def start_session(connection, *_args):
            self.assertEqual(connection.recv(2), b'B2')
            return session.return_value

        session.side_effect = start_session
        client.sendall(b';SR: 95074758 25000001 20\rB2')
        self.assertTrue(future.result(timeout=3))
        _, got_store, account, password = session.call_args.args
        self.assertIs(got_store, store)
        self.assertEqual((account, password), (adapter.ACCOUNT, adapter.MAILBOX_PASSWORD))
        session.return_value.run.assert_called_once_with()
