"""Full rmsgw process test. Run ONLY inside the isolated test namespaces."""
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys


def read_line(sock):
    result = bytearray()
    while len(result) < 512:
        value = sock.recv(1)
        if not value:
            raise EOFError('short line')
        if value == b'\r':
            return bytes(result)
        result.extend(value)
    raise ValueError('long line')


def read_exact(sock, length):
    result = bytearray()
    while len(result) < length:
        chunk = sock.recv(length - len(result))
        if not chunk:
            raise EOFError('short bytes')
        result.extend(chunk)
    return bytes(result)


def relay_bytes(cms, rf_client):
    cms.sendall(b'Callsign :\r')
    assert read_line(cms) == b'N0CALL N0LAB-10'
    cms.sendall(b';SQ: 23753528\r')
    response = read_line(cms)
    assert response == b';SR: 95074758 25000001 20'
    # Gateway emits its own text greeting before forwarding begins.
    # An unambiguous marker separates that from our CMS bytes.
    incoming = b'FCMSLAB-START' + bytes(range(256)) + b'ENDLAB'
    cms.sendall(incoming)
    seen = bytearray()
    while b'FCMSLAB-START' not in seen:
        chunk = rf_client.recv(4096)
        if not chunk:
            raise EOFError('gateway closed before relay marker')
        seen.extend(chunk)
    remainder = bytes(seen).split(b'FCMSLAB-START', 1)[1]
    while len(remainder) < len(incoming) - len(b'FCMSLAB-START'):
        chunk = rf_client.recv(4096)
        if not chunk:
            raise EOFError('gateway closed during payload')
        remainder += chunk
    assert remainder == incoming[len(b'FCMSLAB-START'):], ('CMS-to-client mismatch', len(remainder))
    outgoing = b'RF-LAB-START' + bytes(reversed(range(256))) + b'ENDLAB'
    rf_client.sendall(outgoing)
    assert read_exact(cms, len(outgoing)) == outgoing
    # Wait for each chunk before sending the next one, forcing
    # fragmentation at the gateway read boundary.
    for data in (b'CMS\x00\x01\x02', b'\xff\xfe\x80CMS', b'CMS\r\nX'):
        cms.sendall(data)
        assert read_exact(rf_client, len(data)) == data
    for data in (b'RF\x00\x01\x02', b'\xff\xfe\x80RF', b'RF\r\nX'):
        rf_client.sendall(data)
        assert read_exact(cms, len(data)) == data
    print('PASS: full gateway SGL login and byte-for-byte relay both directions, including fragments')


def main():
    if len(sys.argv) != 7 or sys.argv[2] not in ('valid', 'wrong', 'kissterm', 'wrong-mailbox'):
        raise RuntimeError('invalid internal namespace arguments')
    for index, name in enumerate(('user', 'net', 'ipc', 'mnt'), 3):
        if os.readlink('/proc/self/ns/' + name) == sys.argv[index]:
            raise RuntimeError('namespace isolation missing: ' + name)

    build = Path(sys.argv[1]).resolve(strict=True)
    build_stat = build.stat()
    if (
        build.parent != Path('/tmp').resolve()
        or not build.name.startswith('fcms-rmsgw-full-')
        or not stat.S_ISDIR(build_stat.st_mode)
        or stat.S_IMODE(build_stat.st_mode) != 0o700
        or build_stat.st_uid != os.getuid()
    ):
        raise RuntimeError('expected a private temporary build directory')
    gateway = build / 'rmsgw-lab'
    if gateway.is_symlink() or not gateway.is_file():
        raise RuntimeError('expected a gateway binary in the build directory')

    links = subprocess.check_output(['ip', '-o', 'link', 'show', 'up'], text=True)
    names = [line.split(':', 2)[1].strip().split('@')[0] for line in links.splitlines()]
    if names != ['lo']:
        raise RuntimeError(f'non-loopback UP network devices: {names!r}')
    if subprocess.check_output(['ip', 'route', 'show'], text=True).strip():
        raise RuntimeError('unexpected IPv4 route')
    if subprocess.check_output(['ip', '-6', 'route', 'show'], text=True).strip():
        raise RuntimeError('unexpected IPv6 route')
    addresses = subprocess.check_output(['ip', '-6', '-o', 'addr', 'show'], text=True)
    if any(' inet6 ::1/128 ' not in item for item in addresses.splitlines()):
        raise RuntimeError('unexpected IPv6 address')
    # -I excludes environment and script paths. Only import the lab source
    # and the launcher's private, pinned kissterm export after checking isolation.
    repo = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(repo / 'src'), str(repo)]
    if sys.argv[2] in ('kissterm', 'wrong-mailbox'):
        sys.path.insert(0, str(build / 'reference-kissterm'))
    from tests.rmsgw_full.chain_case import kissterm_exchange, reject_gateway
    from tests.rmsgw_full.sgl_adapter import GATEWAY_PASSWORD

    root = build / 'config'
    root.mkdir(mode=0o700)
    (root / 'stat').mkdir()
    (root / '.version_info').write_text('PACKAGE=RMSLAB\nPROGRAM=RMSGW\nLABEL=TEST\n')
    (root / 'gateway.conf').write_text(
        'GWCALL=N0LAB\nGRIDSQUARE=AA00aa\n'
        f'CHANNELFILE={root / "channels.xml"}\n'
        f'BANNERFILE={root / "banner"}\n'
        'LOGFACILITY=LOCAL0\nLOGMASK=EMERG\nPYTHON=/usr/bin/python3\n'
    )
    (root / 'banner').write_text('Isolated laboratory\n')
    wrong_password = sys.argv[2] == 'wrong'
    lab_password = 'wrong-fixture' if wrong_password else GATEWAY_PASSWORD
    (root / 'channels.xml').write_text('''<rmschannels><channel name="radio" type="ax25" active="yes">
<basecall>N0LAB</basecall><callsign>N0LAB-10</callsign><password>''' + lab_password + '''</password>
<gridsquare>AA00AA</gridsquare><frequency>144000000</frequency><mode>0</mode>
<autoonly>0</autoonly><baud>1200</baud><power>1</power><height>1</height>
<gain>0</gain><direction>0</direction><hours>0</hours><groupreference>1</groupreference>
<servicecode>PUBLIC</servicecode><statuschecker>/bin/true</statuschecker>
</channel></rmschannels>''')
    with socket.socket() as listener:
        listener.settimeout(8)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        host_entry = f'127.0.0.1:{listener.getsockname()[1]}:dummy-host-password\n'
        (root / 'hosts').write_text(host_entry)
        if (root / 'hosts').read_text() != host_entry:
            raise RuntimeError('unexpected CMS hosts')
        rf_client, rf_gateway = socket.socketpair()
        rf_client.settimeout(8)
        process = subprocess.Popen([str(gateway), '-c', str(root / 'gateway.conf'), '-P', 'radio', 'N0CALL'],
                                   stdin=rf_gateway, stdout=rf_gateway, stderr=subprocess.PIPE)
        rf_gateway.close()
        try:
            with listener.accept()[0] as cms:
                cms.settimeout(8)
                if wrong_password:
                    reject_gateway(cms, rf_client, root)
                elif sys.argv[2] in ('kissterm', 'wrong-mailbox'):
                    kissterm_exchange(cms, rf_client, root, wrong_mailbox=sys.argv[2] == 'wrong-mailbox')
                else:
                    relay_bytes(cms, rf_client)
            return_code = process.wait(timeout=8)
            if return_code != 0:
                raise RuntimeError(f'gateway exited with status {return_code}')
            print('PASS: gateway exited cleanly after simulated CMS disconnect')
        finally:
            rf_client.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            stderr = process.stderr.read()
            process.stderr.close()
            if stderr:
                print('Gateway stderr:', stderr.decode('ascii', 'replace'))


if __name__ == '__main__':
    main()
