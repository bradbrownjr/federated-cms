"""Build pinned upstream rmsgw and test it in Linux namespaces, offline.

This script never downloads or changes an upstream checkout. It fails rather
than running the gateway if user, network, IPC, or mount isolation is missing.
"""
import argparse
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import sys
import tempfile

REVISION = '3147f017545f43c4b83ef6a037b8b0e38d0719af'
FIXTURES = Path(__file__).resolve().parent
RMSGW_SOURCES = ('rmsgw.c', 'gwinit.c', 'greet.c', 'banner.c', 'session.c',
                 'gateway.c', 'log_login.c', 'log_logout.c', 'glob.c')
LIB_SOURCES = ('fileage.c', 'syslogmap.c', 'cmsconnect.c', 'loadconfig.c',
               'getcms.c', 'getcmslist.c', 'cmsnode.c', 'statfile.c',
               'setcmsstat.c', 'cmslogin.c', 'upcase.c', 'downcase.c',
               'strcvt.c', 'fgetline.c', 'fgetlinecr.c', 'err.c', 'mapname.c',
               'sendrf.c', 'printfile.c', 'getchan.c', 'set.c', 'symbol.c',
               'var.c', 'strtrim.c', 'senddatagram.c', 'shm.c', 'statblock.c',
               'md5.c', 'readln.c', 'sglchallenge.c', 'loadversion.c',
               'loadenv.c', 'file_exists.c', 'runhook.c')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'}
NAMESPACES = ('user', 'net', 'ipc', 'mnt')


def run_checked(command, timeout):
    process = subprocess.Popen(command, env=ENV, start_new_session=True)
    try:
        status = process.wait(timeout=timeout)
        if status:
            raise subprocess.CalledProcessError(status, command)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='clean checkout of the pinned rmsgw commit')
    parser.add_argument('--cc', default='cc', help='C compiler')
    args = parser.parse_args()
    if sys.platform != 'linux':
        raise RuntimeError('Linux namespace isolation is required; no fallback is provided')
    for program in (args.cc, 'git', 'tar', 'pkg-config', 'unshare', 'ip'):
        if not shutil.which(program, path=ENV['PATH']):
            raise RuntimeError('required executable missing: ' + program)
    source = Path(args.source).resolve(strict=True)
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], env=ENV, text=True).strip()
    if revision != REVISION:
        raise RuntimeError('wrong source revision: ' + revision)
    status = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=all'], env=ENV, text=True)
    if status:
        raise RuntimeError('source checkout must be clean')
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'libxml-2.0'], env=ENV, text=True))
    host_namespaces = [os.readlink('/proc/self/ns/' + name) for name in NAMESPACES]
    with tempfile.TemporaryDirectory(prefix='fcms-rmsgw-full-', dir='/tmp') as directory:
        build = Path(directory)
        # Ignored files (including GCC precompiled headers) must not influence
        # the build. Compile an archive of the pinned commit, not the checkout.
        archive = build / 'source.tar'
        with archive.open('wb') as output:
            subprocess.run(['git', '-C', str(source), 'archive', REVISION],
                           stdout=output, check=True, env=ENV, timeout=30)
        source = build / 'source'
        source.mkdir()
        subprocess.run(['tar', '-xf', str(archive), '-C', str(source)],
                       check=True, env=ENV, timeout=30)
        config = build / 'config'
        (build / 'include').mkdir()
        # The pinned headers include ../pathnames.h. Compile with this private
        # generated include first; no file is written into upstream source.
        names = {'RMSCONFIGFILE': 'gateway.conf', 'RMSVERSIONFILE': '.version_info',
                 'RMSBTEXTFILE': 'banner', 'CMSHOSTFILE': 'hosts', 'CHANNELFILE': 'channels',
                 'XMLCHANNELFILE': 'channels.xml', 'GWHELPFILE': 'gwhelp', 'ACIHELPFILE': 'acihelp',
                 'CMSSTATDIR': 'stat', 'CHANNELUPDATER': 'no-channel-updater',
                 'VERSIONUPDATER': 'no-version-updater', 'HOOKDIR': 'no-hooks'}
        (build / 'pathnames.h').write_text(
            f'#define LAB_CONFIG_DIR "{config}"\n'
            + ''.join(f'#define {key} LAB_CONFIG_DIR "/{value}"\n' for key, value in names.items())
        )
        gateway = build / 'rmsgw-lab'
        command = [args.cc, '-std=gnu11', '-D_GNU_SOURCE', '-DGWMONITOR',
                   '-I', str(build / 'include'), '-I', str(source / 'include'),
                   '-I', str(source / 'lib'),
                   *(str(source / 'rmsgw' / item) for item in RMSGW_SOURCES),
                   *(str(source / 'lib' / item) for item in LIB_SOURCES),
                   str(FIXTURES / 'lab_shims.c'),
                   '-Wl,--wrap=ftok', '-Wl,--wrap=stat',
                   '-Wl,--wrap=openlog', '-Wl,--wrap=closelog', '-Wl,--wrap=syslog',
                   *flags, '-lm', '-o', str(gateway)]
        run_checked(command, timeout=120)
        # Set loopback up only after unshare establishes the namespaces.
        # The case driver compares against these launcher-supplied IDs.
        for case in ('valid', 'wrong'):
            run_checked(
                ['unshare', '--user', '--map-root-user', '--net', '--ipc', '--mount',
                 '--propagation', 'private', '--fork', '--kill-child',
                 '/bin/sh', '-c', 'ip link set dev lo up && exec "$@"', 'namespace-launcher',
                 sys.executable, '-I', str(FIXTURES / 'namespace_case.py'),
                 str(build), case, *host_namespaces],
                timeout=25,
            )
            shutil.rmtree(config)


if __name__ == '__main__':
    main()
