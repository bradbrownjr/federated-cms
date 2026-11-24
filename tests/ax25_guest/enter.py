#!/usr/bin/python3
"""Check ax25d's connected stdin/stdout before executing the real gateway."""
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
os.execv('/lab/rmsgw-lab', ['rmsgw-lab', *sys.argv[1:]])
