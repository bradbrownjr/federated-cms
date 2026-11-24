"""Disposable offline Pat CLI profile and synthetic two-way exchange fixture."""
from datetime import datetime, timezone
import json
import os
import subprocess

from tests.lab_mailbox_helpers import mailbox_call

ACCOUNT = 'N0CALL'
DOWNLOAD_MID = 'LABTEST12345'
UPLOAD_ATTACHMENT = bytes(range(256)) * 3 + b'\x00\xff\r\n'
DOWNLOAD_ATTACHMENT = bytes(reversed(range(256))) * 3 + b'\r\n\x00\xff'
DOWNLOAD = (b'Mid: LABTEST12345\r\nBody: 18\r\nDate: 2026/01/01 00:00\r\n'
            + f'File: {len(DOWNLOAD_ATTACHMENT)} download.bin\r\n'.encode()
            + b'From: LAB\r\nSubject: Lab download\r\nTo: N0CALL\r\nType: Private\r\n\r\n'
            + b'Synthetic download\r\n' + DOWNLOAD_ATTACHMENT + b'\r\n')


def transport_login(request):
    request.settimeout(10)
    for prompt, expected in ((b'Callsign:\r', b'N0CALL\r'), (b'Password:\r', b'lab-only\r')):
        request.sendall(prompt)
        # Stop at CR and leave subsequent B2F bytes for the gateway.
        for byte in expected:
            if request.recv(1) != bytes([byte]):
                return False
    return True


def run_pat_exchange(binary, root, port, store, password, *, virtual_ax25=False):
    """Compose and connect to the selected local lab transport. Return original upload."""
    env = os.environ.copy()
    for kind in ('CONFIG', 'DATA', 'STATE', 'CACHE'):
        path = root / kind.lower()
        (path / 'pat').mkdir(parents=True)
        env[f'XDG_{kind}_HOME'] = str(path)
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        env[key] = 'http://127.0.0.1:9'
    env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
    env['GZIP_EXPERIMENT'] = '0'
    # Pat checks this local account-confirmation cache before connecting.
    cache = root / 'state' / 'pat' / '.account-confirmed_N0CALL.json'
    cache.write_text(json.dumps(datetime.now(timezone.utc).isoformat()), encoding='utf-8')
    config = root / 'config.json'
    config.write_text(json.dumps({'mycall': ACCOUNT, 'version_reporting_disabled': True,
                                  'secure_login_password': password, 'locator': 'FN43'}), encoding='utf-8')
    options = [str(binary), '--config', str(config), '--mbox', str(root / 'mail'),
               '--log', str(root / 'pat.log'), '--event-log', str(root / 'events.json')]
    upload_path = root / 'upload.bin'
    upload_path.write_bytes(UPLOAD_ATTACHMENT)
    composed = subprocess.run(options + ['compose', '--subject', 'Lab upload', '--attachment', str(upload_path), 'LAB'],
                              input='Synthetic upload\n', text=True, capture_output=True, env=env, timeout=15)
    if composed.returncode:
        raise AssertionError('Pat compose failed: ' + composed.stderr)
    (outgoing,) = (root / 'mail' / ACCOUNT / 'out').glob('*.b2f')
    upload = outgoing.read_bytes()
    assert b'Synthetic upload' in upload and UPLOAD_ATTACHMENT in upload
    mailbox_call(store, ACCOUNT, 'store_message', DOWNLOAD_MID, DOWNLOAD, 'out')
    url = f'telnet://N0CALL:lab-only@127.0.0.1:{port}/WL2K'
    if virtual_ax25:
        url = 'ax25+linux://pat/N0LAB-10'
    result = subprocess.run(options + ['connect', url], stdin=subprocess.DEVNULL, text=True,
                            capture_output=True, env=env, timeout=30)
    if result.returncode:
        raise AssertionError('Pat connect failed: ' + result.stderr)
    return outgoing.stem, upload


def verify_pat_exchange(root, store, uploaded):
    mid, upload = uploaded
    assert mailbox_call(store, ACCOUNT, 'list_incoming') == [(mid, upload)]
    assert mailbox_call(store, ACCOUNT, 'pending') == []
    assert mailbox_call(store, ACCOUNT, 'get', DOWNLOAD_MID, 'out') == DOWNLOAD
    account_dir = root / 'mail' / ACCOUNT
    assert not list((account_dir / 'out').glob('*.b2f'))
    assert (account_dir / 'sent' / f'{mid}.b2f').read_bytes() == upload
    inbox = (account_dir / 'in' / f'{DOWNLOAD_MID}.b2f').read_bytes()
    # Pat adds its X-Unread header. Check the exact body and attachment section.
    assert inbox.split(b'\r\n\r\n', 1)[1] == DOWNLOAD.split(b'\r\n\r\n', 1)[1]
