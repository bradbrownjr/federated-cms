"""Bounded HTTP fetching with destination validation at every hop."""

from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
import io
import ipaddress
import math
import re
import socket
import ssl
import threading
import time
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence
from urllib.parse import urljoin, urlsplit
import zlib


class FetchError(Exception):
    """Base error carrying the URL associated with the failure."""
    def __init__(self, message, url=None):
        super().__init__(message)
        self.url = url


class FetchConfigError(FetchError, ValueError): pass
class FetchURLError(FetchError, ValueError): pass
class FetchAddressError(FetchError): pass
class FetchConnectError(FetchError): pass
class FetchTLSError(FetchError): pass
class FetchTimeoutError(FetchError): pass
class FetchRedirectError(FetchError): pass
class FetchTooLargeError(FetchError): pass
class FetchEncodingError(FetchError): pass
class FetchProtocolError(FetchError): pass


@dataclass(frozen=True)
class Hop:
    """A followed redirect."""
    url: str
    status: int


@dataclass(frozen=True)
class FetchResult:
    """The final response and its provenance."""
    url: str
    status: int
    reason: str
    headers: Mapping[str, str]
    content_type: str | None
    media_type: str | None
    content: bytes
    encoded_bytes: int
    address: str
    redirects: tuple[Hop, ...]
    fetched_at: datetime
    elapsed: float

    @property
    def ok(self):
        """Whether the HTTP status is successful."""
        return 200 <= self.status <= 299


class Network(Protocol):
    """Resolver and numeric TCP connection seam."""
    def resolve(self, host: str, port: int, timeout: float) -> Sequence[str]: ...
    def connect(self, address: str, port: int, timeout: float) -> socket.socket: ...


_LOOKUP_SLOTS = threading.BoundedSemaphore(8)


class SystemNetwork:
    """System resolver with bounded daemon workers and numeric socket dialing."""
    def resolve(self, host, port, timeout):
        """Resolve once, preserving the order of distinct addresses."""
        if not _LOOKUP_SLOTS.acquire(blocking=False):
            raise OSError('resolver capacity exhausted')
        done = threading.Event()
        result = []
        def lookup():
            try:
                result.append((True, socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
            except Exception as exc:
                result.append((False, exc))
            finally:
                _LOOKUP_SLOTS.release()
                done.set()
        try:
            threading.Thread(target=lookup, daemon=True).start()
        except BaseException:
            _LOOKUP_SLOTS.release()
            raise
        if not done.wait(timeout):
            raise TimeoutError('DNS timeout')
        if not result[0][0]:
            raise result[0][1]
        return list(dict.fromkeys(item[4][0] for item in result[0][1]))

    def connect(self, address, port, timeout):
        """Connect directly to a numeric address without a lookup."""
        ip = ipaddress.ip_address(address)
        sock = socket.socket(socket.AF_INET if ip.version == 4 else socket.AF_INET6, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect((address, port))
            return sock
        except BaseException:
            sock.close()
            raise


_IPV4_DENY = tuple(map(ipaddress.ip_network, ('0.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '192.0.0.0/24', '192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '192.88.99.0/24', '198.18.0.0/15', '224.0.0.0/4', '240.0.0.0/4')))
_IPV6_DENY = tuple(map(ipaddress.ip_network, ('2001::/23', '2001:db8::/32', '3fff::/20', '2002::/16')))
_IPV6_GLOBAL = ipaddress.ip_network('2000::/3')
_NAT64 = ipaddress.ip_network('64:ff9b::/96')


def _embedded(ip):
    """Return the IPv4 destination represented by a mapped or NAT64 address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xffffffff)
    return None


def is_public_address(address):
    """Apply the fixed public-address policy, including translated IPv4."""
    if not isinstance(address, (str, ipaddress.IPv4Address, ipaddress.IPv6Address)) or '%' in str(address):
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    ip = _embedded(ip) or ip
    if ip.version == 4:
        return ip.is_global and not any(ip in net for net in _IPV4_DENY)
    return ip.is_global and ip in _IPV6_GLOBAL and not any(ip in net for net in _IPV6_DENY)


def _validate(user_agent, accept, max_bytes, max_encoded_bytes, max_redirects,
              connect_timeout, read_timeout, total_timeout, ports, deny_networks, context):
    """Validate configuration and materialize one-shot iterables."""
    def printable(value, maximum):
        return isinstance(value, str) and 1 <= len(value) <= maximum and all(' ' <= c <= '~' for c in value)
    try:
        if not printable(user_agent, 256) or user_agent.strip() != user_agent or not re.search(r'https?://|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', user_agent):
            raise ValueError('invalid user agent')
        if not printable(accept, 512):
            raise ValueError('invalid accept')
        for value, lo, hi in ((max_bytes, 1, 100_000_000), (max_encoded_bytes, 1, 100_000_000), (max_redirects, 0, 10)):
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError('invalid limit')
        for value in (connect_timeout, read_timeout, total_timeout):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid timeout')
        if isinstance(ports, (str, bytes)) or isinstance(deny_networks, (str, bytes)):
            raise ValueError('invalid iterable')
        ports = tuple(ports)
        networks = tuple(ipaddress.ip_network(x, strict=False) for x in tuple(deny_networks))
        if not ports or any(type(p) is not int or not 1 <= p <= 65535 for p in ports):
            raise ValueError('invalid ports')
        if context is not None and (not isinstance(context, ssl.SSLContext) or not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED or context.minimum_version not in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3) or context.keylog_filename is not None):
            raise ValueError('invalid TLS context')
        return ports, networks
    except (ValueError, TypeError, OverflowError) as exc:
        raise FetchConfigError(str(exc)) from exc


def _url(value, ports, error_url=None):
    """Validate and normalize an absolute URL."""
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(not '!' <= c <= '~' for c in value):
            raise ValueError('invalid URL characters or length')
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        if scheme not in ('http', 'https') or not parts.netloc or '@' in parts.netloc:
            raise ValueError('invalid authority or scheme')
        host = parts.hostname
        if not host:
            raise ValueError('missing host')
        host = host.lower()
        port = parts.port or (443 if scheme == 'https' else 80)
        # Explicit zero is not an absent port.
        if parts.port == 0 or port not in ports:
            raise ValueError('port refused')
        literal = None
        if parts.netloc.startswith('['):
            if '%' in host or not re.fullmatch(r'\[[^\]]+\](?::[0-9]*)?', parts.netloc):
                raise ValueError('invalid IPv6 host')
            literal = ipaddress.IPv6Address(host)
            authority = '[' + host + ']'
        else:
            if ':' in host or not re.fullmatch(r'[^:]+(?::[0-9]*)?', parts.netloc):
                raise ValueError('invalid host')
            if re.fullmatch(r'(?:[0-9]+|0[xX][0-9a-fA-F]*)', host.split('.')[-1]):
                literal = ipaddress.IPv4Address(host)
            else:
                labels = host.split('.')
                if len(host) > 253 or len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?', label) for label in labels) or host.endswith(('.localhost', '.local', '.home.arpa')):
                    raise ValueError('invalid name')
            authority = host
        default = 443 if scheme == 'https' else 80
        if port != default:
            authority += ':' + str(port)
        path = parts.path or '/'
        target = path + ('?' + parts.query if parts.query else '')
        return scheme + '://' + authority + target, scheme, host, port, authority, target, literal
    except (ValueError, UnicodeError) as exc:
        raise FetchURLError('invalid URL', error_url) from exc


def _remaining(deadline, url):
    """Return positive remaining time or signal the total deadline."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FetchTimeoutError('total timeout', url)
    return remaining


class _Reader(io.RawIOBase):
    """Count socket bytes and enforce the deadline before every receive."""
    def __init__(self, sock, deadline, timeout, limit, url):
        self.sock, self.deadline, self.timeout, self.limit, self.url = sock, deadline, timeout, limit, url
        self.count = 0
    def readable(self):
        return True
    def readinto(self, buffer):
        self.sock.settimeout(min(self.timeout, _remaining(self.deadline, self.url)))
        count = self.sock.recv_into(buffer, min(len(buffer), self.limit - self.count + 1))
        self.count += count
        if self.count > self.limit:
            raise FetchTooLargeError('wire limit exceeded', self.url)
        return count


class _SocketAdapter:
    """Provide HTTPResponse a buffered reader over the checked socket."""
    def __init__(self, sock, deadline, timeout, limit, url):
        self.sock, self.reader = sock, _Reader(sock, deadline, timeout, limit, url)
    def makefile(self, mode):
        return io.BufferedReader(self.reader)


def _framing(response, url):
    """Validate all framing fields and return the declared length."""
    lengths = response.headers.get_all('Content-Length', [])
    transfer = response.headers.get_all('Transfer-Encoding', [])
    if transfer and (lengths or ', '.join(transfer) != 'chunked'):
        raise FetchProtocolError('invalid transfer encoding', url)
    if lengths:
        items = [x.strip(' \t') for line in lengths for x in line.split(',')]
        if any(not re.fullmatch(r'[0-9]+', x) for x in items) or any(int(x) != int(items[0]) for x in items):
            raise FetchProtocolError('invalid content length', url)
        length = int(items[0])
        # http.client does not understand comma-separated valid lengths.
        if response.status not in (204, 304) and not 100 <= response.status < 200:
            response.length = length
        return length
    return None


def _coding(response, url):
    """Select a supported content coding."""
    values = response.headers.get_all('Content-Encoding', [])
    codings = [x.strip().lower() for line in values for x in line.split(',') if x.strip() and x.strip().lower() != 'identity']
    if len(codings) > 1 or codings and codings[0] not in ('gzip', 'x-gzip', 'deflate'):
        raise FetchEncodingError('unsupported content encoding', url)
    return codings[0] if codings else 'identity'


def _body(response, coding, max_bytes, max_encoded_bytes, url):
    """Read and incrementally decode a bounded response body."""
    result = bytearray()
    encoded = 0
    decoder = None
    pending = b''
    completed = False
    try:
        while True:
            piece = response.read1(min(65536, max_encoded_bytes - encoded + 1))
            if not piece:
                break
            encoded += len(piece)
            if encoded > max_encoded_bytes:
                raise FetchTooLargeError('encoded body too large', url)
            if coding == 'identity':
                result.extend(piece)
                if len(result) > max_bytes:
                    raise FetchTooLargeError('body too large', url)
                continue
            pending += piece
            if coding == 'deflate' and decoder is None and len(pending) < 2:
                continue
            while pending:
                if completed:
                    if coding == 'deflate':
                        raise FetchEncodingError('trailing deflate data', url)
                    pending = pending.lstrip(b'\0')
                    if not pending:
                        break
                    decoder, completed = None, False
                if decoder is None:
                    if coding in ('gzip', 'x-gzip'):
                        decoder = zlib.decompressobj(31)
                    else:
                        b0, b1 = pending[:2]
                        decoder = zlib.decompressobj(15 if b0 & 15 == 8 and (b0 * 256 + b1) % 31 == 0 else -15)
                output = decoder.decompress(pending, max_bytes - len(result) + 1)
                result.extend(output)
                if len(result) > max_bytes:
                    raise FetchTooLargeError('decoded body too large', url)
                if decoder.eof:
                    pending = decoder.unused_data
                    completed = True
                else:
                    pending = decoder.unconsumed_tail
        if response.length is not None and response.length != 0:
            raise FetchProtocolError('incomplete body', url)
        if coding != 'identity' and encoded and not completed:
            raise FetchEncodingError('incomplete compressed stream', url)
        return bytes(result), encoded
    except zlib.error as exc:
        raise FetchEncodingError('invalid compressed stream', url) from exc


def _clean(value):
    """Replace non-printable and non-ASCII characters."""
    return ''.join(c if ' ' <= c <= '~' else '?' for c in value)


def fetch(url, *, user_agent, accept='*/*', max_bytes=2_000_000,
          max_encoded_bytes=2_000_000, max_redirects=5, connect_timeout=10.0,
          read_timeout=15.0, total_timeout=30.0, ports=(80, 443),
          deny_networks=(), ssl_context=None, network=None):
    """Fetch one URL while checking each destination and bounding resources."""
    start = time.monotonic()
    ports, networks = _validate(user_agent, accept, max_bytes, max_encoded_bytes, max_redirects, connect_timeout, read_timeout, total_timeout, ports, deny_networks, ssl_context)
    current = _url(url, ports)
    deadline = start + total_timeout
    network = network if network is not None else SystemNetwork()
    redirects, seen = [], set()
    while True:
        normalized, scheme, host, port, authority, target, literal = current
        seen.add(normalized)
        try:
            if literal is not None:
                addresses = [str(literal)]
            else:
                addresses = list(network.resolve(host, port, min(connect_timeout, _remaining(deadline, normalized))))
            _remaining(deadline, normalized)
        except TimeoutError as exc:
            raise FetchTimeoutError('DNS timeout', normalized) from exc
        except OSError as exc:
            raise FetchConnectError('DNS failure', normalized) from exc
        if not addresses:
            raise FetchConnectError('no addresses', normalized)
        for address in addresses:
            if not is_public_address(address):
                raise FetchAddressError('address refused', normalized)
            ip = ipaddress.ip_address(address)
            embedded = _embedded(ip)
            if any((ip.version == net.version and ip in net) or (embedded is not None and embedded.version == net.version and embedded in net) for net in networks):
                raise FetchAddressError('address denied', normalized)
        sock = None
        last_error = None
        for address in addresses:
            try:
                sock = network.connect(address, port, min(connect_timeout, _remaining(deadline, normalized)))
                break
            except OSError as exc:
                last_error = exc
        if sock is None:
            raise FetchConnectError('connection failed', normalized) from last_error
        response = None
        try:
            _remaining(deadline, normalized)
            local = ipaddress.ip_address(sock.getsockname()[0])
            remote = ipaddress.ip_address(address)
            local_mapped = local.ipv4_mapped if local.version == 6 else None
            remote_mapped = remote.ipv4_mapped if remote.version == 6 else None
            if local_mapped is not None or remote_mapped is not None:
                local, remote = local_mapped or local, remote_mapped or remote
                same = local == remote
            else:
                same = local == remote or (local.version == remote.version == 6 and int(local) >> 64 == int(remote) >> 64)
            if same:
                raise FetchAddressError('self connection refused', normalized)
            if scheme == 'https':
                if ssl_context is None:
                    context = ssl.create_default_context()
                    context.minimum_version = ssl.TLSVersion.TLSv1_2
                    context.keylog_filename = None
                else:
                    context = ssl_context
                try:
                    sock.settimeout(min(connect_timeout, _remaining(deadline, normalized)))
                    sock = context.wrap_socket(sock, server_hostname=host, do_handshake_on_connect=False)
                    sock.settimeout(min(connect_timeout, _remaining(deadline, normalized)))
                    sock.do_handshake()
                    _remaining(deadline, normalized)
                except TimeoutError as exc:
                    raise FetchTimeoutError('TLS timeout', normalized) from exc
                except (ssl.SSLError, OSError) as exc:
                    raise FetchTLSError('TLS failure', normalized) from exc
            sock.settimeout(min(read_timeout, _remaining(deadline, normalized)))
            connection = http.client.HTTPConnection(host, port)
            connection.sock = sock
            connection.putrequest('GET', target, skip_host=True, skip_accept_encoding=True)
            for name, value in (('Host', authority), ('User-Agent', user_agent), ('Accept', accept), ('Accept-Encoding', 'gzip, deflate'), ('Connection', 'close')):
                connection.putheader(name, value)
            connection.endheaders()
            adapter = _SocketAdapter(sock, deadline, read_timeout, max_encoded_bytes + 262144, normalized)
            response = http.client.HTTPResponse(adapter, method='GET')
            response.begin()
            fetched_at = datetime.now(timezone.utc)
            if not 200 <= response.status <= 599:
                raise FetchProtocolError('invalid final status', normalized)
            length = _framing(response, normalized)
            locations = response.headers.get_all('Location', [])
            if response.status in (301, 302, 303, 307, 308) and locations:
                if len(locations) > 1:
                    raise FetchProtocolError('multiple locations', normalized)
                location = locations[0]
                if any(not '!' <= c <= '~' for c in location):
                    raise FetchURLError('invalid location', normalized)
                try:
                    joined = urljoin(normalized, location)
                except (ValueError, UnicodeError) as exc:
                    raise FetchURLError('invalid location', normalized) from exc
                next_url = _url(joined, ports, normalized)
                if len(redirects) >= max_redirects or next_url[0] in seen or (scheme == 'https' and next_url[1] == 'http'):
                    raise FetchRedirectError('redirect refused', normalized)
                redirects.append(Hop(normalized, response.status))
                current = next_url
                continue
            coding = _coding(response, normalized)
            if length is not None and length > (min(max_bytes, max_encoded_bytes) if coding == 'identity' else max_encoded_bytes):
                raise FetchTooLargeError('declared body too large', normalized)
            content, encoded = _body(response, coding, max_bytes, max_encoded_bytes, normalized)
            allowed = ('cache-control', 'content-encoding', 'content-language', 'content-length', 'content-type', 'date', 'etag', 'expires', 'last-modified', 'location', 'retry-after')
            headers = MappingProxyType({key: _clean(', '.join(response.headers.get_all(key))) for key in allowed if response.headers.get_all(key) is not None})
            content_type = headers.get('content-type')
            media = content_type.split(';', 1)[0].strip().lower() if content_type is not None else None
            if media is not None and media.count('/') != 1:
                media = None
            return FetchResult(normalized, response.status, _clean(response.reason or ''), headers, content_type, media, content, encoded, address, tuple(redirects), fetched_at, time.monotonic() - start)
        except FetchError:
            raise
        except TimeoutError as exc:
            raise FetchTimeoutError('read timeout', normalized) from exc
        except (OSError, http.client.HTTPException, UnicodeError, ValueError) as exc:
            raise FetchProtocolError('HTTP failure', normalized) from exc
        finally:
            if response is not None:
                response.close()
            sock.close()
