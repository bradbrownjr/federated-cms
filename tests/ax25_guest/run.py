"""Build and boot the offline Debian amd64 AX.25 guest."""
import argparse
import gzip
import hashlib
import lzma
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

FIXTURE = Path(__file__).resolve().parent
REPO = FIXTURE.parents[1]
sys.path.insert(0, str(REPO))
from tests.rmsgw_full import run_experiment as gateway

ENV = gateway.ENV
KERNEL = '6.12.118+deb13-amd64'
TRIPLET = 'x86_64-linux-gnu'
CASES = {
    'pat': 'PASS: Pat AX.25 CLI exchanged both B2 messages and binary attachments',
    'kissterm': 'PASS: kissterm exchanged both B2 messages and binary attachments',
    'wrong-mailbox': 'PASS: SGL accepted, wrong B2F mailbox secret rejected',
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pins(name):
    result = {}
    for line in (FIXTURE / name).read_text().splitlines():
        checksum, item = line.split(maxsplit=1)
        result[item] = checksum
    return result


def unpack_packages(inputs, build):
    packages = build / 'packages'
    packages.mkdir()
    private = build / 'input.deb'
    for name, checksum in pins('packages.sha256').items():
        path = inputs / name
        if not path.is_file():
            raise ValueError(f'missing pinned Debian archive: {path}')
        shutil.copyfile(path, private)
        if digest(private) != checksum:
            raise ValueError(f'archive checksum mismatch: {name}')
        subprocess.run(['dpkg-deb', '-x', private, packages], check=True, env=ENV, timeout=120)
    private.unlink()
    return packages


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)


def stage_runtime(guest, packages, programs):
    # Copy only the package-listed sources. Ignore local additions, startup
    # hooks and bytecode in the host's Python installation.
    stdlib = Path('/usr/lib/python3.13')
    site = Path('/usr/lib/python3/dist-packages')
    libraries = ('platformdirs', 'rich', 'pygments', 'markdown_it', 'mdurl')
    listing = subprocess.check_output(
        ['dpkg-query', '-L', 'libpython3.13-minimal', 'libpython3.13-stdlib',
         *('python3-' + name.replace('_', '-') for name in libraries)],
        text=True, env=ENV)
    checksums = {}
    elf_inputs = list(programs) + [Path('/usr/bin/python3.13')]
    trees = [(stdlib, guest / 'usr/lib/python3.13')]
    trees.extend((site / name, guest / 'lab' / name) for name in libraries)
    paths = sorted(set(map(Path, listing.splitlines())))
    for directory, target in trees:
        group = hashlib.sha256()
        for path in paths:
            if not path.is_relative_to(directory) or path.suffix not in ('.py', '.so') or path.name == 'sitecustomize.py':
                continue
            relative = path.relative_to(directory)
            dest = target / relative
            copy(path, dest)
            group.update(f'{digest(dest)}  {relative}\n'.encode())
            if path.suffix == '.so':
                elf_inputs.append(path)
        checksums[directory.name] = group.hexdigest()

    # Resolve DT_NEEDED with readelf. A supplied Pat executable runs only
    # inside the guest. libgcc_s is also loaded dynamically by glibc.
    libdir = Path('/usr/lib') / TRIPLET
    loader = libdir / 'ld-linux-x86-64.so.2'
    copy(loader, guest / 'lib64' / loader.name)
    copy(Path('/usr/bin/python3.13'), guest / 'usr/bin/python3')
    checksums['python3.13-bin'] = digest(guest / 'usr/bin/python3')
    checksums[loader.name] = digest(guest / 'lib64' / loader.name)
    found = set()
    needed = {'libgcc_s.so.1'}
    while elf_inputs or needed:
        if elf_inputs:
            path = elf_inputs.pop()
            output = subprocess.check_output(['readelf', '-dW', path], env=ENV, text=True, timeout=10)
            for name in re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', output):
                if not re.fullmatch(r'[a-zA-Z0-9_+.\-]+', name):
                    raise ValueError(f'unexpected library name: {name!r}')
                if name not in found:
                    needed.add(name)
            continue
        name = needed.pop()
        found.add(name)
        source = packages / 'usr/lib' / name if name == 'libax25.so.0' else libdir / name
        dest = guest / 'lib' / TRIPLET / name
        copy(source, dest)
        if name != 'libax25.so.0':
            checksums[name] = digest(dest)
        elf_inputs.append(source)
    expected = pins('runtime.sha256')
    for name in sorted(checksums.keys() | expected.keys()):
        if checksums.get(name) != expected.get(name):
            raise ValueError(f'guest runtime checksum mismatch: {name} (got {checksums.get(name)})')


def initramfs(root, output):
    def entry(stream, inode, name, mode, data=b'', major=0, minor=0):
        name = os.fsencode(name) + b'\0'
        fields = (inode, mode, 0, 0, 1, 0, len(data), 0, 0, major, minor, len(name), 0)
        header = b'070701' + ''.join(f'{v:08x}' for v in fields).encode() + name
        stream.write(header + bytes(-len(header) % 4) + data + bytes(-len(data) % 4))

    with gzip.GzipFile(filename=output, mode='wb', compresslevel=3, mtime=0) as archive:
        entry(archive, 1, 'dev', stat.S_IFDIR | 0o755)
        entry(archive, 2, 'dev/console', stat.S_IFCHR | 0o600, major=5, minor=1)
        for inode, path in enumerate(sorted(root.rglob('*')), 3):
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                mode = stat.S_IFLNK | 0o777
                data = os.fsencode(os.readlink(path))
            elif stat.S_ISREG(mode):
                mode = stat.S_IFREG | (0o755 if mode & 0o111 else 0o644)
                data = path.read_bytes()
            elif stat.S_ISDIR(mode):
                mode = stat.S_IFDIR | (0o1777 if path == root / 'tmp' else 0o755)
                data = b''
            else:
                raise ValueError(f'unexpected object in guest: {path}')
            entry(archive, inode, path.relative_to(root), mode, data)
        entry(archive, inode + 1, 'TRAILER!!!', 0)


def stage(args, build):
    pat = gateway.stage_pat(args.pat_binary, build, required_settings=(
        '-tags=libax25', 'CGO_ENABLED=1', 'GOOS=linux', 'GOARCH=amd64'))
    source = build / 'rmsgw'
    gateway.export_revision(args.source.resolve(strict=True), gateway.REVISION, source)
    gateway.export_revision(args.kissterm_source.resolve(strict=True), gateway.KISSTERM_REVISION,
                            build / 'reference', ('kissterm',))
    # Upstream greet.c embeds __DATE__. Use its pinned Git commit timestamp.
    binary = gateway.build_gateway(source, build, Path('/lab/case'), 'cc',
                                   env=dict(ENV, SOURCE_DATE_EPOCH='1784557462'))
    packages = unpack_packages(args.packages, build)
    root = build / 'guest'
    for directory in ('bin', 'proc', 'sys', 'tmp', 'etc/ax25', 'lab/modules'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / 'tmp').chmod(0o1777)
    modules = packages / 'usr/lib/modules' / KERNEL / 'kernel'
    for name in ('lib/crc16', 'net/ax25/ax25', 'drivers/net/hamradio/mkiss'):
        module = modules / (name + '.ko.xz')
        (root / 'lab/modules' / module.stem).write_bytes(lzma.decompress(module.read_bytes()))
    copy(packages / 'usr/bin/busybox', root / 'bin/busybox')
    for applet in ('sh', 'true'):
        (root / 'bin' / applet).symlink_to('busybox')
    tools = [packages / 'usr/sbin' / name for name in ('ax25d', 'kissattach', 'kissparms')]
    for program in (binary, pat, *tools):
        copy(program, root / 'lab' / program.name)
    stage_runtime(root, packages, [binary, pat, *tools])
    shutil.copytree(build / 'reference/kissterm', root / 'lab/kissterm')
    # The CMS source under test comes from this checkout.
    for path in (REPO / 'src/federated_cms').glob('*.py'):
        copy(path, root / 'lab/federated_cms' / path.name)
    for path in ('__init__.py', 'pat_fixture.py', 'lab_mailbox_helpers.py',
                 'rmsgw_full/sgl_adapter.py', 'rmsgw_full/chain_case.py'):
        copy(REPO / 'tests' / path, root / 'lab/tests' / path)
    for name in ('case.py', 'kissterm_case.py', 'enter.py'):
        copy(FIXTURE / name, root / 'lab' / name)
    copy(FIXTURE / 'init', root / 'init')
    (root / 'init').chmod(0o755)
    (root / 'lab/enter.py').chmod(0o755)
    (root / 'etc/passwd').write_text('root:x:0:0:root:/:/bin/sh\n')
    (root / 'etc/group').write_text('root:x:0:\n')
    (root / 'etc/ax25/axports').write_text(
        'radio N0LAB-10 9600 256 4 Virtual gateway\npat N0CALL 9600 256 4 Virtual client\n')
    (root / 'etc/ax25/ax25d.conf').write_text(
        '[N0LAB-10 VIA radio]\nN0CALL * * * * * * - root /lab/enter.py enter -P %d %U\n'
        'default * * * * * * L\n')
    archive = build / 'guest.cpio.gz'
    initramfs(root, archive)
    print(f'Guest initramfs SHA-256 {digest(archive)}', flush=True)
    return packages


def verify_log(case, output):
    lines = output.splitlines()
    markers = ['Stopped ax25d before KISS port teardown', 'Observed DISC and answering UA',
               f'FCMS AX25 PASS {case}', 'DISPATCH connected AF_AX25/SOCK_SEQPACKET radio N0CALL',
               'KISS N0CALL > N0LAB-10 port=0 SABM', 'KISS N0LAB-10 > N0CALL port=0 UA',
               'KISS N0CALL > N0LAB-10 port=0 I', 'KISS N0LAB-10 > N0CALL port=0 I']
    if (not all(lines.count(marker) == 1 for marker in markers)
            or not any(line.startswith(CASES[case]) for line in lines)
            or any(error in output for error in ('FCMS AX25 FAIL', 'Traceback (most recent call last)',
                                                 'Exception in thread', 'accept error', 'Kernel panic'))):
        raise RuntimeError(f'{case} did not complete the guest assertions')


def boot(case, packages, build, log):
    command = [
        'unshare', '--user', '--map-root-user', '--net', '--ipc', '--fork', '--kill-child',
        'env', f'LD_LIBRARY_PATH={packages}/usr/lib/{TRIPLET}',
        packages / 'usr/bin/qemu-system-x86_64', '-no-user-config', '-nodefaults', '-nic', 'none',
        '-accel', 'tcg', '-m', '384', '-smp', '1', '-display', 'none', '-monitor', 'none',
        '-serial', 'stdio', '-no-reboot', '-L', packages / 'usr/share/qemu',
        '-bios', packages / 'usr/share/seabios/bios-256k.bin', '-kernel', packages / 'boot' / f'vmlinuz-{KERNEL}',
        '-initrd', build / 'guest.cpio.gz', '-append', f'console=ttyS0 rdinit=/init panic=-1 loglevel=3 fcms_case={case}',
    ]
    with log.open('xb') as stream:
        child = subprocess.Popen(command, cwd=build, env=ENV, start_new_session=True,
                                 stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
        try:
            status = child.wait(timeout=180)
            if status:
                raise subprocess.CalledProcessError(status, command)
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            raise
    verify_log(case, log.read_text(errors='replace'))


def terminate(signum, _frame):
    # Unwind the active QEMU process group and the private temporary directory.
    raise SystemExit(128 + signum)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('source', 'kissterm-source', 'pat-binary', 'packages'):
        parser.add_argument('--' + flag, required=True, type=Path)
    parser.add_argument('--case', choices=CASES, help='run one case (default: all three)')
    parser.add_argument('--log-dir', type=Path, help='new directory to retain guest console logs')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() == 0:
        parser.error('run as an unprivileged Linux user')
    if subprocess.check_output(['dpkg', '--print-architecture'], env=ENV, text=True).strip() != 'amd64':
        parser.error('the pinned Debian amd64 runtime is required')
    signal.signal(signal.SIGTERM, terminate)
    with tempfile.TemporaryDirectory(prefix='fcms-ax25-') as scratch:
        build = Path(scratch)
        packages = stage(args, build)
        logs = args.log_dir or build / 'logs'
        logs.mkdir(mode=0o700)
        for case in ([args.case] if args.case else CASES):
            print(f'Booting {case}', flush=True)
            log = logs / (case + '.log')
            try:
                boot(case, packages, build, log)
            except Exception:
                if log.is_file():
                    print(log.read_text(errors='replace')[-8000:], file=sys.stderr)
                raise
            print(f'PASS {case}', flush=True)


if __name__ == '__main__':
    main()
