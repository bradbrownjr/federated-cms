"""Gateway and B2F assertions used inside the namespace harness."""
from concurrent.futures import ThreadPoolExecutor
import re
import socket

from federated_cms.store import SQLiteStore
from tests.lab_mailbox_helpers import mailbox_call
from tests.rmsgw_full.sgl_adapter import (
    ACCOUNT, GATEWAY, GATEWAY_PASSWORD, MAILBOX_PASSWORD, REJECTION, serve_gateway,
)


def reject_gateway(cms, rf_client, root):
    db_path = root / 'mail.sqlite'
    pending = ('LABPENDING', b'Mid: LABPENDING\r\nBody: 7\r\n\r\npending')
    mailbox_call(SQLiteStore(db_path), ACCOUNT, 'store_message', *pending, 'out')
    assert not serve_gateway(cms, SQLiteStore(db_path))
    # Upstream relays the denial and then disconnects when the adapter closes.
    cms.shutdown(socket.SHUT_WR)
    seen = bytearray()
    while chunk := rf_client.recv(4096):
        seen.extend(chunk)
        if len(seen) > 4096:
            raise AssertionError('unexpected gateway output after rejection')
    assert REJECTION in seen and b'[FCMS-' not in seen and b';PQ:' not in seen
    reopened = SQLiteStore(db_path)
    assert mailbox_call(reopened, ACCOUNT, 'list_incoming') == []
    assert mailbox_call(reopened, ACCOUNT, 'pending') == [pending]
    print('PASS: gateway secret rejected before B2F; pending mailbox unchanged')


def kissterm_exchange(cms, rf_client, root, wrong_mailbox=False):
    from kissterm import _isolate
    _isolate.isolate(root / 'kissterm-profile')
    from kissterm.winlink.b2f import Client, Line, Received, Sent
    from kissterm.winlink.message import build, serialize

    upload = build(mid='LABUPLOAD', sender=ACCOUNT, to=['LAB'], subject='Gateway upload',
                   body='Synthetic upload', files=[('up.bin', bytes(range(256)) * 3)])
    download = build(mid='LABDOWNLOAD', sender='LAB', to=[ACCOUNT], subject='Gateway download',
                     body='Synthetic download', files=[('down.bin', bytes(reversed(range(256))) * 3)])
    db_path = root / 'mail.sqlite'
    store = SQLiteStore(db_path)
    mailbox_call(store, ACCOUNT, 'store_message', download.mid, serialize(download), 'out')

    def serve():
        with cms:
            return serve_gateway(cms, store)

    password = GATEWAY_PASSWORD if wrong_mailbox else MAILBOX_PASSWORD
    client = Client(ACCOUNT, GATEWAY, password=password, outbound=[upload])
    with ThreadPoolExecutor(max_workers=1) as pool:
        accepted = pool.submit(serve)
        while not client.done:
            data = rf_client.recv(4096)
            if not data:
                client.connection_lost()
                break
            client.feed(data)
            output = client.take_output()
            if output:
                rf_client.sendall(output)
        assert accepted.result(timeout=10), 'gateway failed SGL'
    events = client.take_events()
    assert any(isinstance(e, Line) and e.direction == '<' and re.fullmatch(r';PQ: [0-9]{8}', e.text)
               for e in events), 'missing B2F mailbox challenge'
    sent = [(e.mid, e.already_had) for e in events if isinstance(e, Sent)]
    received = [e for e in events if isinstance(e, Received)]
    reopened = SQLiteStore(db_path)
    incoming = mailbox_call(reopened, ACCOUNT, 'list_incoming')
    pending = mailbox_call(reopened, ACCOUNT, 'pending')
    assert mailbox_call(reopened, ACCOUNT, 'get', download.mid, 'out') == serialize(download)
    if wrong_mailbox:
        assert Line('<', '*** Protocol error') in events and client.error
        assert not sent and not received
        assert not incoming and pending == [(download.mid, serialize(download))]
        print('PASS: SGL accepted, wrong B2F mailbox secret rejected; pending mailbox unchanged')
    else:
        assert not client.error, client.error
        assert sent == [(upload.mid, False)]
        assert [e.raw for e in received] == [serialize(download)]
        assert received[0].message.files == download.files
        assert incoming == [(upload.mid, serialize(upload))] and not pending
        print('PASS: kissterm exchanged both B2 messages and binary attachments through rmsgw; '
              'reopened mailbox retained the upload and download acknowledgement')


def pat_exchange(binary, root, cms_listener, command, running_gateway):
    from tests.pat_fixture import run_pat_exchange, transport_login, verify_pat_exchange

    db_path = root / 'mail.sqlite'
    store = SQLiteStore(db_path)
    with socket.socket() as radio_listener, ThreadPoolExecutor(max_workers=1) as pool:
        radio_listener.settimeout(10)
        radio_listener.bind(('127.0.0.1', 0))
        radio_listener.listen(1)
        pat = pool.submit(run_pat_exchange, binary, root, radio_listener.getsockname()[1],
                          store, MAILBOX_PASSWORD)
        with radio_listener.accept()[0] as radio:
            if not transport_login(radio):
                raise AssertionError('Pat did not complete the synthetic Telnet prompts')
            # Hand the same connected fd to rmsgw. Only the gateway can connect
            # Pat's stream to the SGL adapter on the separate CMS listener.
            with running_gateway(command, radio, pat_disconnect=True), cms_listener.accept()[0] as cms:
                assert serve_gateway(cms, store), 'gateway failed SGL'
        uploaded = pat.result(timeout=10)
    verify_pat_exchange(root, SQLiteStore(db_path), uploaded)
    print('PASS: Pat CLI exchanged both B2 messages and attachments through rmsgw; '
          'verified client inbox, sent folder, and reopened mailbox')
