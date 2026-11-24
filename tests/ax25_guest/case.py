"""Run one virtual AX.25 case inside the disposable guest."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os
from pathlib import Path
import pty
import select
import selectors
import signal
import socket
import subprocess
import threading
import time

from federated_cms.store import SQLiteStore
from tests.pat_fixture import run_pat_exchange, verify_pat_exchange
from tests.rmsgw_full.sgl_adapter import GATEWAY_PASSWORD, MAILBOX_PASSWORD, serve_gateway


def config(root, address):
    (root / 'stat').mkdir(parents=True)
    (root / '.version_info').write_text('PACKAGE=RMSLAB\nPROGRAM=RMSGW\nLABEL=TEST\n')
    (root / 'gateway.conf').write_text(
        'GWCALL=N0LAB\nGRIDSQUARE=AA00aa\n'
        f'CHANNELFILE={root / "channels.xml"}\n'
        f'BANNERFILE={root / "banner"}\n'
        'LOGFACILITY=LOCAL0\nLOGMASK=EMERG\nPYTHON=/usr/bin/python3\n'
    )
    (root / 'banner').write_text('Isolated laboratory\n')
    (root / 'channels.xml').write_text(f'''<rmschannels><channel name="radio" type="ax25" active="yes">
<basecall>N0LAB</basecall><callsign>N0LAB-10</callsign><password>{GATEWAY_PASSWORD}</password>
<gridsquare>AA00AA</gridsquare><frequency>144000000</frequency><mode>0</mode>
<autoonly>0</autoonly><baud>1200</baud><power>1</power><height>1</height>
<gain>0</gain><direction>0</direction><hours>0</hours><groupreference>1</groupreference>
<servicecode>PUBLIC</servicecode><statuschecker>/bin/true</statuschecker>
</channel></rmschannels>''')
    (root / 'hosts').write_text(f'127.0.0.1:{address}:dummy-host-password\n')


def attach(name, cleanup):
    master, slave = pty.openpty()
    cleanup.callback(os.close, master)
    cleanup.callback(os.close, slave)
    pty_name = os.ttyname(slave)
    output = subprocess.check_output(['/lab/kissattach', pty_name, name], text=True, timeout=5)
    print(output.strip())
    subprocess.run(['/lab/kissparms', '-p', name, '-c', '1'], check=True, timeout=5)
    return master


def bridge(first, second, stopped, released):
    from kissterm.transport.kiss import KissDecoder, KissCommand
    from kissterm.ax25.frame import AX25Frame

    seen = set()
    disconnect_from = None
    with selectors.DefaultSelector() as selector:
        for fd, dest, source in ((first, second, 'N0LAB-10'), (second, first, 'N0CALL')):
            selector.register(fd, selectors.EVENT_READ, (dest, source, KissDecoder()))
        while not stopped.is_set():
            for event, _ in selector.select(timeout=0.1):
                dest, source, decoder = event.data
                data = os.read(event.fd, 4096)
                if not data:
                    return seen
                for port, cmd, payload in decoder.feed(data):
                    if cmd != KissCommand.DATA:
                        continue
                    frame = AX25Frame.decode(payload)
                    peer = 'N0CALL' if source == 'N0LAB-10' else 'N0LAB-10'
                    assert port == 0 and str(frame.path.source) == source
                    assert str(frame.path.destination) == peer and not frame.path.repeaters
                    kind = frame.utype.name if frame.kind == 'U' else frame.kind
                    if kind == 'DISC':
                        disconnect_from = source
                    if kind == 'UA' and disconnect_from == peer:
                        released.set()
                    if (source, kind) not in seen:
                        print(f'KISS {source} > {peer} port=0 {kind}')
                        seen.add((source, kind))
                remaining = memoryview(data)
                while remaining:
                    remaining = remaining[os.write(dest, remaining):]
    return seen


def pat_exchange(root, cms_listener):
    db_path = root / 'mail.sqlite'
    with ThreadPoolExecutor(max_workers=1) as pool:
        pat = pool.submit(run_pat_exchange, Path('/lab/pat-lab'), root, None,
                          SQLiteStore(db_path), MAILBOX_PASSWORD, virtual_ax25=True)
        try:
            with cms_listener.accept()[0] as cms:
                assert serve_gateway(cms, SQLiteStore(db_path)), 'gateway failed SGL'
        except TimeoutError:
            if pat.done():
                pat.result()
            raise
        uploaded = pat.result(timeout=30)
    verify_pat_exchange(root, SQLiteStore(db_path), uploaded)
    print('PASS: Pat AX.25 CLI exchanged both B2 messages and binary attachments')


def stop_ax25d(pidfd):
    try:
        signal.pidfd_send_signal(pidfd, signal.SIGTERM)
        if not select.select([pidfd], [], [], 5)[0]:
            raise RuntimeError('ax25d did not stop before KISS port teardown')
        print('Stopped ax25d before KISS port teardown')
    finally:
        os.close(pidfd)


def start_ax25d(cleanup):
    subprocess.run(['/lab/ax25d', '-l', '-c', '/etc/ax25/ax25d.conf'], check=True, timeout=5)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if any(line.split()[1:4] == ['ax0', 'N0LAB-10', '*']
               for line in Path('/proc/net/ax25').read_text().splitlines()):
            for exe in Path('/proc').glob('[0-9]*/exe'):
                try:
                    if exe.readlink() == Path('/lab/ax25d'):
                        pidfd = os.pidfd_open(int(exe.parent.name))
                        cleanup.callback(stop_ax25d, pidfd)
                        return
                except (FileNotFoundError, ProcessLookupError):
                    continue
        time.sleep(0.05)
    raise RuntimeError('ax25d did not bind the guest radio port')


def main():
    from kissterm import _isolate
    _isolate.isolate(Path('/tmp/kiss-profile'))
    mode = os.environ['fcms_case']
    assert mode in ('pat', 'kissterm', 'wrong-mailbox')
    assert Path('/proc/net/ax25').is_file()
    net = Path('/sys/class/net')
    assert {p.name for p in net.iterdir()} == {'lo'}
    with ExitStack() as cleanup, socket.socket() as cms_listener:
        first = attach('radio', cleanup)
        if mode == 'pat':
            second = attach('pat', cleanup)
        expected = {'lo', 'ax0', 'ax1'} if mode == 'pat' else {'lo', 'ax0'}
        assert {p.name for p in net.iterdir()} == expected
        assert len(Path('/proc/net/route').read_text().splitlines()) == 1
        assert all(line.split()[-1] == 'lo' for line in Path('/proc/net/ipv6_route').read_text().splitlines())
        print('Guest interfaces:', ' '.join(sorted(expected)))
        cms_listener.settimeout(45)
        cms_listener.bind(('127.0.0.1', 0))
        cms_listener.listen(1)
        root = Path('/lab/case')
        config(root, cms_listener.getsockname()[1])
        start_ax25d(cleanup)
        stopped = threading.Event()
        released = threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            if mode == 'pat':
                pump = pool.submit(bridge, first, second, stopped, released)
                try:
                    pat_exchange(root, cms_listener)
                    assert released.wait(8), 'missing AX.25 release handshake'
                finally:
                    stopped.set()
            else:
                from kissterm_case import exchange
                import asyncio
                with socket.socket() as kiss:
                    kiss.bind(('127.0.0.1', 0))
                    kiss.listen(1)
                    kiss.settimeout(10)

                    def relay():
                        with kiss.accept()[0] as stream:
                            return bridge(first, stream.fileno(), stopped, released)

                    pump = pool.submit(relay)
                    try:
                        asyncio.run(exchange(kiss.getsockname()[1], cms_listener, root, mode == 'wrong-mailbox'))
                    finally:
                        stopped.set()
            frames = pump.result(timeout=5)
        assert {('N0CALL', 'SABM'), ('N0LAB-10', 'UA'),
                ('N0CALL', 'I'), ('N0LAB-10', 'I')} <= frames, frames
        assert released.is_set(), 'missing AX.25 release handshake'
        print('Observed DISC and answering UA')


if __name__ == '__main__':
    main()
