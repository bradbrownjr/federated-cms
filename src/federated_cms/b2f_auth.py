"""Legacy B2F secure-login response for isolated interoperability tests.

Algorithm and test vectors: kissterm winlink/secure.py, ported from wl2k-go
`fbb/secure.go` (MIT, LA5NTA), itself a port of paclink-unix's. The
protocol uses MD5 and a short response; it is not a modern password-authentication scheme and provides no transport encryption.
"""
import hashlib

_SALT = bytes((
    77, 197, 101, 206, 190, 249, 93, 200, 51, 243, 93, 237, 71, 94, 239, 138,
    68, 108, 70, 185, 225, 137, 217, 16, 51, 122, 193, 48, 194, 195, 198, 175,
    172, 169, 70, 84, 61, 62, 104, 186, 114, 52, 61, 168, 66, 129, 192, 208,
    187, 249, 232, 193, 41, 113, 41, 45, 240, 16, 29, 228, 208, 228, 61, 20,
))


def login_response(challenge: str, password: str) -> str:
    digest = hashlib.md5(challenge.encode("ascii") + password.encode("utf-8") + _SALT,
                         usedforsecurity=False).digest()
    value = ((digest[3] & 0x3f) << 24) | (digest[2] << 16) | (digest[1] << 8) | digest[0]
    return f"{value:08d}"[-8:]
