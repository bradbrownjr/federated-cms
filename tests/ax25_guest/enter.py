#!/usr/bin/python3
"""Check the AX.25 socket and record the gateway child's exit status."""
import ctypes
import os
import signal
import socket
import sys

with open('/dev/console', 'w') as console:
    os.dup2(console.fileno(), 2)

radio = socket.socket(socket.AF_AX25, socket.SOCK_SEQPACKET, fileno=0)
assert radio.getsockopt(socket.SOL_SOCKET, socket.SO_DOMAIN) == socket.AF_AX25
assert radio.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_SEQPACKET
assert os.path.samestat(os.fstat(0), os.fstat(1))

# CPython cannot size a sockaddr_ax25. Let Linux report the peer and check
# the family plus the seven-byte AX.25 source address from its sockaddr.
address = ctypes.create_string_buffer(128)
length = ctypes.c_uint(len(address))
libc = ctypes.CDLL(None, use_errno=True)
if libc.getpeername(0, address, ctypes.byref(length)) != 0:
    raise OSError(ctypes.get_errno(), 'getpeername')
assert 9 <= length.value <= len(address)
assert int.from_bytes(address.raw[:2], sys.byteorder) == socket.AF_AX25
assert bytes(c >> 1 for c in address.raw[2:8]) == b'N0CALL'
assert address.raw[8] & 0x1e == 0
assert sys.argv[1:] == ['-P', 'radio', 'N0CALL'], sys.argv
print('DISPATCH connected AF_AX25/SOCK_SEQPACKET radio N0CALL', file=sys.stderr, flush=True)
radio.detach()
# CPython ignores SIGPIPE on startup. ax25d launches native services with
# the default disposition, which this descriptor-check wrapper must preserve.
signal.signal(signal.SIGPIPE, signal.SIG_DFL)
# ax25d ignores SIGCHLD; restore it for the monitor, but preserve the
# inherited disposition in the gateway itself.
inherited_sigchld = signal.getsignal(signal.SIGCHLD)
signal.signal(signal.SIGCHLD, signal.SIG_DFL)
pid = os.fork()
if pid == 0:
    signal.signal(signal.SIGCHLD, inherited_sigchld)
    os.execv('/lab/rmsgw-lab', ['rmsgw-lab', *sys.argv[1:]])

# The monitor must not keep the AX.25 stream open while the gateway exits.
os.close(0)
os.close(1)
_, status = os.waitpid(pid, 0)
code = os.waitstatus_to_exitcode(status)
with open('/lab/case/gateway-exit.tmp', 'x', encoding='ascii') as result:
    result.write(f'{code}\n')
os.replace('/lab/case/gateway-exit.tmp', '/lab/case/gateway-exit')
print(f'GATEWAY EXIT {code}', file=sys.stderr, flush=True)
