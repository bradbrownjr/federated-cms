"""Synthetic single-channel Secure Gateway Login adapter for the offline harness."""
import hmac
import secrets

from federated_cms.b2f import Session
from federated_cms.b2f_auth import login_response

ACCOUNT = 'N0CALL'
GATEWAY = 'N0LAB-10'
GATEWAY_PASSWORD = 'FooBar'
MAILBOX_PASSWORD = 'fixture-only'
REJECTION = b'*** Gateway login rejected\r'


def _read_line(connection):
    # Leave any bytes following CR in the socket for the B2F session.
    data = bytearray()
    while len(data) < 128:
        byte = connection.recv(1)
        if not byte:
            raise EOFError
        if byte == b'\r':
            return bytes(data)
        if not 32 <= byte[0] <= 126:
            raise ValueError('invalid SGL line')
        data.extend(byte)
    raise ValueError('long SGL line')


def serve_gateway(connection, store):
    """Handle one lab connection. Return whether the gateway login succeeded.

    The caller owns the socket and closes it when this function returns.
    B2F independently authenticates the client before opening a mailbox lease.
    """
    connection.settimeout(8)
    try:
        connection.sendall(b'Callsign :\r')
        if _read_line(connection) != f'{ACCOUNT} {GATEWAY}'.encode('ascii'):
            raise ValueError('unknown lab identity')
        challenge = f'{secrets.randbelow(100_000_000):08d}'
        connection.sendall(f';SQ: {challenge}\r'.encode('ascii'))
        expected = login_response(challenge, GATEWAY_PASSWORD)
        # These two trailing fields are literals in the pinned rmsgw revision.
        if not hmac.compare_digest(_read_line(connection), f';SR: {expected} 25000001 20'.encode('ascii')):
            raise ValueError('invalid gateway response')
    except (ValueError, EOFError, TimeoutError, ConnectionError):
        try:
            connection.sendall(REJECTION)
        except OSError:
            pass
        return False
    Session(connection, store, ACCOUNT, MAILBOX_PASSWORD).run()
    return True
