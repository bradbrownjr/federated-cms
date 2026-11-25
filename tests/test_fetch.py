"""Contract tests for federated_cms.fetch (docs/FETCHER.md).

Offline: every connection goes to a threaded server on 127.0.0.1 through
the explicit ``network=`` seam. Public-looking addresses returned by the
fake resolver are mapped to the local server by the fake dialer, so the
real address policy is exercised unchanged.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import math
import random
import socket
import socketserver
import ssl
import threading
import time
import tracemalloc
import zlib
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from federated_cms.fetch import (
    FetchAddressError,
    FetchConfigError,
    FetchConnectError,
    FetchEncodingError,
    FetchError,
    FetchProtocolError,
    FetchRedirectError,
    FetchResult,
    FetchTLSError,
    FetchTimeoutError,
    FetchTooLargeError,
    FetchURLError,
    Hop,
    SystemNetwork,
    fetch,
    is_public_address,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fetch"
UA = "federated-cms-test/0.1 (https://example.org/contact; ops@example.org)"

PUBLIC_V4 = "93.184.215.14"      # public.example
PUBLIC_V4_B = "93.184.215.15"    # other.example
PUBLIC_V4_C = "93.184.215.16"    # secure.example, wrong.example (TLS server)
PUBLIC_V6 = "2606:4700:4700::1111"

# Server-side handlers stop on their own after this long, so a broken
# implementation cannot hang the suite.
HANDLER_CAP = 3.0


# --------------------------------------------------------------- local server


@dataclasses.dataclass
class Seen:
    request_line: str
    headers: list[tuple[str, str]]

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        srv = self.server
        srv.log.append(Seen(self.requestline, list(self.headers.items())))
        route = srv.routes.get(self.path)
        self.close_connection = True
        if route is None:
            send(self, 404, b"not found")
        else:
            route(self)

    def log_message(self, *args) -> None:
        pass


class _TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def get_request(self):
        sock, addr = super().get_request()
        self.connections += 1
        return sock, addr

    def handle_error(self, request, client_address) -> None:
        pass


class LocalServer:
    def __init__(self, tls: ssl.SSLContext | None = None):
        self.httpd = _TCPServer(("127.0.0.1", 0), _Handler)
        self.httpd.routes = {}
        self.httpd.log = []
        self.httpd.connections = 0
        self.sni: list[str | None] = []
        self.stop = threading.Event()
        self.httpd.stop = self.stop
        if tls is not None:
            tls.sni_callback = lambda sslsock, name, ctx: self.sni.append(name)
            self.httpd.socket = tls.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    @property
    def routes(self) -> dict:
        return self.httpd.routes

    @property
    def log(self) -> list[Seen]:
        return self.httpd.log

    @property
    def connections(self) -> int:
        return self.httpd.connections

    def reset(self) -> None:
        """Release handlers still running from the last test and clear state."""
        self.stop.set()
        self.stop = threading.Event()
        self.httpd.stop = self.stop
        self.httpd.routes = {}
        self.httpd.log = []
        self.httpd.connections = 0
        self.sni.clear()

    def close(self) -> None:
        self.stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def send(h, status: int, body: bytes = b"", headers=(), *, length: bool = True,
         reason: str | None = None) -> None:
    if reason is None:
        try:
            reason = HTTPStatus(status).phrase
        except ValueError:
            reason = "Whatever"
    lines = [f"HTTP/1.1 {status} {reason}"]
    lines += [f"{k}: {v}" for k, v in headers]
    if length:
        lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: close")
    h.wfile.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)


def ok(body: bytes = b"ok", headers=(("Content-Type", "text/plain"),), **kw):
    return lambda h: send(h, 200, body, headers, **kw)


def redirect(location: str, status: int = 302, headers=()):
    return lambda h: send(h, status, b"", (("Location", location), *headers))


def raw(data: bytes):
    return lambda h: h.wfile.write(data)


def stall_after(data: bytes):
    """Send ``data`` and then nothing, until the client goes away."""
    def handler(h):
        h.wfile.write(data)
        h.server.stop.wait(HANDLER_CAP)
    return handler


def drip(head: bytes, unit: bytes, interval: float = 0.05):
    """Send ``head``, then ``unit`` every ``interval`` seconds."""
    def handler(h):
        h.wfile.write(head)
        end = time.monotonic() + HANDLER_CAP
        while time.monotonic() < end and not h.server.stop.is_set():
            time.sleep(interval)
            try:
                h.wfile.write(unit)
            except OSError:
                return
    return handler


def stream_forever(head: bytes, unit: bytes = b"x" * 65536):
    def handler(h):
        h.wfile.write(head)
        end = time.monotonic() + HANDLER_CAP
        while time.monotonic() < end and not h.server.stop.is_set():
            try:
                h.wfile.write(unit)
            except OSError:
                return
    return handler


# ---------------------------------------------------------------- fake network


class FakeNetwork:
    """The test seam: a scripted resolver and a dialer to 127.0.0.1."""

    def __init__(self, answers=None, routes=None):
        self.answers = dict(answers or {})
        self.routes = dict(routes or {})
        self.resolved: list[tuple[str, int, float]] = []
        self.connected: list[tuple[str, int, float]] = []

    def resolve(self, host, port, timeout):
        self.resolved.append((host, port, timeout))
        answer = self.answers.get(host)
        if answer is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        if callable(answer):
            answer = answer(sum(1 for r in self.resolved if r[0] == host))
        if isinstance(answer, BaseException):
            raise answer
        return list(answer)

    def connect(self, address, port, timeout):
        self.connected.append((address, port, timeout))
        target = self.routes.get((address, port))
        if target is None:
            raise ConnectionRefusedError(111, "Connection refused")
        if isinstance(target, BaseException):
            raise target
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", target))
        return sock

    @property
    def hosts(self) -> list[str]:
        return [r[0] for r in self.resolved]

    @property
    def addresses(self) -> list[str]:
        return [c[0] for c in self.connected]


class AnythingResolves(FakeNetwork):
    """Resolves every name to a public address: refusals must come from URL rules."""

    def resolve(self, host, port, timeout):
        self.resolved.append((host, port, timeout))
        return [PUBLIC_V4]


@pytest.fixture(autouse=True)
def system_dns_guard(monkeypatch):
    """fetch must never resolve or dial by itself when given a network."""
    calls: list[tuple] = []

    def refuse(name):
        def fn(*args, **kwargs):
            calls.append((name, args))
            raise OSError(f"test: {name} must not be called")
        return fn

    for name in ("getaddrinfo", "create_connection", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, refuse(name))
    yield calls
    assert calls == [], f"fetch used system name resolution or dialing: {calls}"


@pytest.fixture(scope="module")
def _plain_server():
    s = LocalServer()
    yield s
    s.close()


@pytest.fixture
def server(_plain_server):
    _plain_server.reset()
    yield _plain_server
    _plain_server.reset()


def server_tls_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(FIXTURES / "server.pem", FIXTURES / "server.key")
    return ctx


def client_tls_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(FIXTURES / "ca.pem"))


@pytest.fixture(scope="module")
def _tls_server():
    s = LocalServer(tls=server_tls_context())
    yield s
    s.close()


@pytest.fixture
def tls_server(_tls_server):
    _tls_server.reset()
    yield _tls_server
    _tls_server.reset()


def make_net(server, tls_server=None) -> FakeNetwork:
    answers = {
        "public.example": [PUBLIC_V4],
        "other.example": [PUBLIC_V4_B],
        "secure.example": [PUBLIC_V4_C],
        "wrong.example": [PUBLIC_V4_C],
    }
    routes = {
        (PUBLIC_V4, 80): server.port,
        (PUBLIC_V4_B, 80): server.port,
        (PUBLIC_V4, 8080): server.port,
        (PUBLIC_V6, 80): server.port,
    }
    if tls_server is not None:
        routes[(PUBLIC_V4_C, 443)] = tls_server.port
    return FakeNetwork(answers, routes)


@pytest.fixture
def net(server):
    return make_net(server)


def get(url, net, **kw):
    kw.setdefault("user_agent", UA)
    return fetch(url, network=net, **kw)


def gzip_bytes(data: bytes) -> bytes:
    c = zlib.compressobj(6, zlib.DEFLATED, 31)
    return c.compress(data) + c.flush()


def deflate_bytes(data: bytes, raw_stream: bool = False) -> bytes:
    c = zlib.compressobj(6, zlib.DEFLATED, -15 if raw_stream else 15)
    return c.compress(data) + c.flush()


def zeros_compressed(total: int, wbits: int) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, wbits)
    block = bytes(1 << 20)
    out = [c.compress(block) for _ in range(total >> 20)]
    out.append(c.flush())
    return b"".join(out)


# ------------------------------------------------------------- address policy


ALLOWED = [
    "93.184.215.14",
    "8.8.8.8",
    "1.1.1.1",
    "44.0.0.1",                     # AMPRNet: public; deny_networks narrows it
    "2606:4700:4700::1111",
    "2001:4860:4860::8888",
    "::ffff:8.8.8.8",               # IPv4-mapped public address
    "64:ff9b::808:808",             # NAT64 well-known prefix, embeds 8.8.8.8
    ipaddress.ip_address("8.8.8.8"),
    ipaddress.ip_address("2606:4700:4700::1111"),
]

REFUSED = [
    # IPv4 special-purpose blocks
    "0.0.0.0", "0.1.2.3", "10.0.0.1", "192.0.2.1", "172.16.0.1", "172.31.255.255",
    "192.168.1.1", "100.64.0.1", "100.127.255.254", "127.0.0.1", "127.255.255.254",
    "169.254.169.254", "169.254.0.1", "192.0.0.1", "192.0.0.9", "192.0.0.170",
    "192.0.2.1", "198.51.100.7", "203.0.113.9", "192.88.99.1", "198.18.0.1",
    "198.19.255.255", "224.0.0.1", "239.255.255.250", "240.0.0.1", "255.255.255.255",
    # IPv6 outside 2000::/3
    "::", "::1", "::7f00:1", "::a00:1", "::8.8.8.8", "100::1", "4000::1", "5f00::1",
    "fc00::1", "fd12:3456:789a::1", "fe80::1", "fec0::1", "ff02::1", "ff05::1:3",
    "64:ff9b:1::808:808",
    # IPv4-mapped forms of refused IPv4
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254", "::ffff:0.0.0.0",
    "::ffff:a9fe:a9fe",
    # NAT64 well-known prefix embedding refused IPv4
    "64:ff9b::7f00:1", "64:ff9b::a00:1", "64:ff9b::a9fe:a9fe", "64:ff9b::c0a8:101",
    # refused blocks inside 2000::/3
    "2001::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2", "2001:1::1", "2001:db8::1",
    "2002:c0a8:101::1", "2002:808:808::1", "3fff::1",
    # zone IDs, never valid here
    "fe80::1%eth0", "2606:4700:4700::1111%1",
    # not strict IP addresses
    "", "localhost", "example.org", "127.1", "0x7f.0.0.1", "2130706433", "0177.0.0.1",
    "010.0.0.1", " 8.8.8.8", "8.8.8.8 ", "[::1]", "1.2.3.4.5", "8.8.8.8/32",
    # objects
    ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1"),
    ipaddress.ip_address("::ffff:169.254.169.254"),
    # other types
    2130706433, None, b"8.8.8.8", 8.8,
]


@pytest.mark.parametrize("address", ALLOWED, ids=repr)
def test_policy_allows_public_addresses(address):
    assert is_public_address(address) is True


@pytest.mark.parametrize("address", REFUSED, ids=repr)
def test_policy_refuses(address):
    assert is_public_address(address) is False


# ----------------------------------------------------------------- happy path


def test_plain_fetch(server, net):
    server.routes["/hello"] = lambda h: send(h, 200, b"hello", (
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Cache-Control", "max-age=60"),
        ("Cache-Control", "public"),
        ("Set-Cookie", "session=1"),
        ("X-Other", "1"),
        ("ETag", '"abc"'),
    ))
    before = datetime.now(timezone.utc)
    result = get("http://public.example/hello", net)
    after = datetime.now(timezone.utc)

    assert isinstance(result, FetchResult)
    assert result.url == "http://public.example/hello"
    assert result.status == 200
    assert result.reason == "OK"
    assert result.ok is True
    assert result.content == b"hello"
    assert result.encoded_bytes == 5
    assert result.content_type == "text/plain; charset=utf-8"
    assert result.media_type == "text/plain"
    assert result.address == PUBLIC_V4
    assert result.redirects == ()
    assert dict(result.headers) == {
        "content-type": "text/plain; charset=utf-8",
        "cache-control": "max-age=60, public",
        "etag": '"abc"',
        "content-length": "5",
    }
    with pytest.raises(TypeError):
        result.headers["x"] = "y"  # read-only mapping
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = 500
    assert result.fetched_at.tzinfo is not None
    assert result.fetched_at.utcoffset().total_seconds() == 0
    assert before <= result.fetched_at <= after
    assert isinstance(result.elapsed, float) and 0 < result.elapsed < 5

    assert net.resolved[0][:2] == ("public.example", 80)
    assert net.addresses == [PUBLIC_V4]
    assert server.log[0].request_line == "GET /hello HTTP/1.1"


def test_request_headers_exact(server, net):
    server.routes["/h"] = ok()
    get("http://public.example/h", net, accept="application/geo+json")
    seen = server.log[0]
    assert seen.headers == [
        ("Host", "public.example"),
        ("User-Agent", UA),
        ("Accept", "application/geo+json"),
        ("Accept-Encoding", "gzip, deflate"),
        ("Connection", "close"),
    ]


def test_default_accept(server, net):
    server.routes["/h"] = ok()
    get("http://public.example/h", net)
    assert server.log[0].header("Accept") == "*/*"


def test_path_and_query_sent_verbatim(server, net):
    server.routes["/a%20b/c%2Fd?x=%41&y"] = ok()
    result = get("http://public.example/a%20b/c%2Fd?x=%41&y", net)
    assert result.status == 200
    assert server.log[0].request_line == "GET /a%20b/c%2Fd?x=%41&y HTTP/1.1"


def test_url_normalization(server, net):
    server.routes["/a?b=1"] = ok()
    result = get("HTTP://Public.Example:80/a?b=1#frag", net)
    assert result.url == "http://public.example/a?b=1"
    assert server.log[0].request_line == "GET /a?b=1 HTTP/1.1"
    assert server.log[0].header("Host") == "public.example"


def test_empty_path_becomes_slash(server, net):
    server.routes["/"] = ok()
    result = get("http://public.example", net)
    assert result.url == "http://public.example/"
    assert server.log[0].request_line == "GET / HTTP/1.1"


def test_non_default_port_in_host_and_url(server, net):
    server.routes["/x"] = ok()
    result = get("http://public.example:8080/x", net, ports=(80, 8080))
    assert result.url == "http://public.example:8080/x"
    assert server.log[0].header("Host") == "public.example:8080"
    assert net.connected[0][:2] == (PUBLIC_V4, 8080)


def test_ipv4_literal_is_not_resolved(server, net):
    server.routes["/"] = ok()
    result = get(f"http://{PUBLIC_V4}/", net)
    assert result.status == 200
    assert net.resolved == []
    assert net.addresses == [PUBLIC_V4]
    assert server.log[0].header("Host") == PUBLIC_V4


def test_ipv6_literal_is_not_resolved(server, net):
    server.routes["/"] = ok()
    result = get("http://[2606:4700:4700:0::1111]/", net)
    assert result.url == "http://[2606:4700:4700:0::1111]/"   # kept as written
    assert net.resolved == []
    assert net.addresses == [PUBLIC_V6]                       # canonical for dialing
    assert result.address == PUBLIC_V6
    assert server.log[0].header("Host") == "[2606:4700:4700:0::1111]"


@pytest.mark.parametrize("status", [404, 500, 503])
def test_non_2xx_is_a_result(server, net, status):
    server.routes["/e"] = lambda h: send(h, status, b'{"title":"Not Found"}', (
        ("Content-Type", "application/problem+json"), ("Retry-After", "120")))
    result = get("http://public.example/e", net)
    assert result.status == status
    assert result.ok is False
    assert result.content == b'{"title":"Not Found"}'
    assert result.media_type == "application/problem+json"
    assert result.headers["retry-after"] == "120"


def test_204_empty(server, net):
    server.routes["/n"] = lambda h: send(h, 204, b"", length=False)
    result = get("http://public.example/n", net)
    assert result.status == 204 and result.ok and result.content == b""
    assert result.content_type is None and result.media_type is None


@pytest.mark.parametrize("value,media", [
    ("TEXT/HTML; charset=ISO-8859-1", "text/html"),
    ("application/rss+xml", "application/rss+xml"),
    ("  application/json ;x=1", "application/json"),
    ("text", None),
    ("a/b/c", None),
    ("", None),
])
def test_media_type(server, net, value, media):
    server.routes["/m"] = lambda h: send(h, 200, b"x", (("Content-Type", value),))
    result = get("http://public.example/m", net)
    assert result.media_type == media


@pytest.mark.parametrize("ua", [
    "bot/1 (https://example.org/bot)",
    "bot/1 (ops@example.org)",
    "CoolBot/0.0 (https://example.org/coolbot/; coolbot@example.org) generic-library/0.0",
    "(myweatherapp.com, contact@myweatherapp.com)",
    "KC1JMH-node/0.1 (http://example.net/)",
])
def test_user_agent_with_contact_is_sent(server, net, ua):
    server.routes["/u"] = ok()
    get("http://public.example/u", net, user_agent=ua)
    assert server.log[0].header("User-Agent") == ua


# ------------------------------------------------------------ DNS rebinding


def test_connects_to_the_checked_address_with_one_lookup(server):
    """A second lookup would answer 127.0.0.1; only the first may be used."""
    def rebinding(n):
        return [PUBLIC_V4] if n == 1 else ["127.0.0.1"]

    net = FakeNetwork({"rebind.example": rebinding}, {(PUBLIC_V4, 80): server.port})
    server.routes["/"] = ok(b"public")
    result = get("http://rebind.example/", net)

    assert result.content == b"public"
    assert result.address == PUBLIC_V4
    assert net.hosts == ["rebind.example"]                # exactly one lookup
    assert [c[:2] for c in net.connected] == [(PUBLIC_V4, 80)]
    assert server.log[0].header("Host") == "rebind.example"


def test_rebinding_on_second_lookup_cannot_reach_loopback(server):
    net = FakeNetwork({"rebind.example": lambda n: ["127.0.0.1"]},
                      {("127.0.0.1", 80): server.port})
    server.routes["/"] = ok()
    with pytest.raises(FetchAddressError) as err:
        get("http://rebind.example/", net)
    assert err.value.url == "http://rebind.example/"
    assert net.connected == []
    assert server.connections == 0


@pytest.mark.parametrize("answer", [
    [PUBLIC_V4, "10.0.0.1"],
    ["10.0.0.1", PUBLIC_V4],
    [PUBLIC_V4, "::1"],
    [PUBLIC_V4, "169.254.169.254"],
    [PUBLIC_V4, "not-an-address"],
    [PUBLIC_V4, "fe80::1%eth0"],
])
def test_any_refused_answer_refuses_the_host(server, answer):
    net = FakeNetwork({"mixed.example": answer}, {(PUBLIC_V4, 80): server.port})
    with pytest.raises(FetchAddressError):
        get("http://mixed.example/", net)
    assert net.connected == []


def test_fallback_to_next_checked_address(server):
    net = FakeNetwork({"two.example": [PUBLIC_V4_B, PUBLIC_V4]},
                      {(PUBLIC_V4_B, 80): ConnectionRefusedError(111, "refused"),
                       (PUBLIC_V4, 80): server.port})
    server.routes["/"] = ok()
    result = get("http://two.example/", net)
    assert result.address == PUBLIC_V4
    assert net.addresses == [PUBLIC_V4_B, PUBLIC_V4]
    assert net.hosts == ["two.example"]


@pytest.mark.parametrize("failure", [
    ConnectionRefusedError(111, "refused"),
    TimeoutError("timed out"),
    OSError(113, "No route to host"),
])
def test_all_connections_fail(failure):
    net = FakeNetwork({"down.example": [PUBLIC_V4, PUBLIC_V4_B]},
                      {(PUBLIC_V4, 80): failure, (PUBLIC_V4_B, 80): failure})
    with pytest.raises(FetchConnectError) as err:
        get("http://down.example/", net)
    assert err.value.url == "http://down.example/"
    assert net.addresses == [PUBLIC_V4, PUBLIC_V4_B]


@pytest.mark.parametrize("answer", [[], socket.gaierror(-2, "Name or service not known"),
                                    OSError("resolver down")])
def test_resolution_failure(answer):
    net = FakeNetwork({"gone.example": answer})
    with pytest.raises(FetchConnectError):
        get("http://gone.example/", net)
    assert net.connected == []


def test_resolution_timeout():
    net = FakeNetwork({"slow.example": TimeoutError("resolver timed out")})
    with pytest.raises(FetchTimeoutError):
        get("http://slow.example/", net)


def test_timeouts_passed_to_network_are_bounded(server, net):
    server.routes["/"] = ok()
    get("http://public.example/", net, connect_timeout=2.0, total_timeout=1.5)
    (_, _, resolve_timeout), = net.resolved
    (_, _, connect_timeout), = net.connected
    assert 0 < resolve_timeout <= 1.5
    assert 0 < connect_timeout <= 1.5
    get("http://public.example/", net, connect_timeout=0.75, total_timeout=10)
    assert 0 < net.resolved[1][2] <= 0.75
    assert 0 < net.connected[1][2] <= 0.75


def test_deny_networks_narrows_policy(server, net):
    server.routes["/"] = ok()
    with pytest.raises(FetchAddressError):
        get("http://public.example/", net, deny_networks=["93.184.215.0/24"])
    assert net.connected == []


def test_deny_networks_checks_embedded_ipv4(server):
    net = FakeNetwork({"mapped.example": ["::ffff:93.184.215.14"]},
                      {("::ffff:93.184.215.14", 80): server.port})
    with pytest.raises(FetchAddressError):
        get("http://mapped.example/", net, deny_networks=["93.184.215.14/32"])
    assert net.connected == []


# ------------------------------------------------------------------- URLs


BAD_URLS = [
    # schemes
    "ftp://public.example/", "file:///etc/passwd", "gopher://public.example/",
    "javascript:alert(1)", "data:text/plain,hi", "mailto:ops@public.example",
    "ws://public.example/", "public.example/path", "//public.example/",
    # authority
    "http://", "http:///path", "http://user:pw@public.example/", "http://user@public.example/",
    "http://public.example@other.example/", "http://evil.example\\@127.0.0.1/",
    # ports
    "http://public.example:8080/", "http://public.example:22/", "http://public.example:0/",
    "http://public.example:65536/", "http://public.example:abc/", "https://public.example:8443/",
    # numeric hosts that getaddrinfo would read as 127.0.0.1, and friends
    "http://2130706433/", "http://0x7f.1/", "http://127.1/", "http://0177.0.0.1/",
    "http://0x7f000001/", "http://017700000001/", "http://1.2.3.4.5/",
    "http://public.example.123/", "http://public.0x10/", "http://127.0.0.1./",
    # names
    "http://localhost/", "http://LOCALHOST/", "http://api.localhost/", "http://printer.local/",
    "http://nas.home.arpa/", "http://nas/", "http://public.example./", "http://-bad.example/",
    "http://bad-.example/", "http://%31%32%37.0.0.1/", "http://ex!ample.org/",
    "http://" + "a" * 64 + ".example/", "http://" + ".".join(["abcdefghi"] * 26) + ".example/",
    "http://public..example/",
    # IPv6 literal syntax
    "http://[::1/", "http://[fe80::1%25eth0]/", "http://[not-v6]/", "http://[127.0.0.1]/",
    # characters
    "http://public.example/\r\nX-Injected: 1", "http://public.example/a b",
    "http://public.example/\ta", "http://public.example/\x00", "http://públic.example/",
    "http://public.example/é", " http://public.example/", "",
    "http://public.example/" + "a" * 2048,
]


@pytest.mark.parametrize("url", BAD_URLS, ids=repr)
def test_url_refused_before_network(url):
    net = AnythingResolves()
    with pytest.raises(FetchURLError) as err:
        get(url, net)
    assert isinstance(err.value, ValueError)
    assert err.value.url is None
    assert net.resolved == [] and net.connected == []


@pytest.mark.parametrize("url", [None, b"http://public.example/", 42])
def test_url_must_be_str(url):
    net = AnythingResolves()
    with pytest.raises(FetchURLError):
        get(url, net)
    assert net.resolved == []


def test_url_length_limit_is_2048(server, net):
    path = "/" + "a" * (2048 - len("http://public.example/"))
    server.routes[path] = ok()
    assert get("http://public.example" + path, net).status == 200
    with pytest.raises(FetchURLError):
        get("http://public.example" + path + "a", net)


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1/", "http://127.0.0.1/"),
    ("http://192.0.2.1/admin", "http://192.0.2.1/admin"),
    ("http://169.254.169.254/latest/meta-data/", "http://169.254.169.254/latest/meta-data/"),
    ("http://0.0.0.0/", "http://0.0.0.0/"),
    ("http://100.100.100.100/", "http://100.100.100.100/"),
    ("http://[::1]/", "http://[::1]/"),
    ("http://[::ffff:127.0.0.1]/", "http://[::ffff:127.0.0.1]/"),
    ("http://[::FFFF:A9FE:A9FE]/", "http://[::ffff:a9fe:a9fe]/"),
    ("http://[fd00::1]/", "http://[fd00::1]/"),
    ("http://[64:ff9b::a9fe:a9fe]/", "http://[64:ff9b::a9fe:a9fe]/"),
    ("https://[fe80::1]/", "https://[fe80::1]/"),
])
def test_ip_literal_refused_before_network(url, expected):
    net = AnythingResolves()
    with pytest.raises(FetchAddressError) as err:
        get(url, net)
    assert err.value.url == expected
    assert net.resolved == [] and net.connected == []


def test_default_network_refuses_loopback_without_connecting(server, monkeypatch):
    server.routes["/"] = ok()
    with pytest.raises(FetchAddressError):
        fetch(f"http://127.0.0.1:{server.port}/", user_agent=UA, ports=(server.port,))
    assert server.connections == 0


# ------------------------------------------------------------- arguments


BAD_ARGS = [
    {"user_agent": ""},
    {"user_agent": "Python-urllib/3.13"},
    {"user_agent": "federated-cms/0.1"},
    {"user_agent": "federated-cms/0.1 (KC1JMH)"},
    {"user_agent": "bot/1 (example.org)"},
    {"user_agent": "bot/1 (ops@localhost)"},
    {"user_agent": " bot/1 (ops@example.org)"},
    {"user_agent": "bot/1 (ops@example.org) "},
    {"user_agent": "bot/1 (ops@example.org)\r\nX-Evil: 1"},
    {"user_agent": "bot/1 (ops@example.org)\n"},
    {"user_agent": "bot/1 (ops@example.org)\t"},
    {"user_agent": "bót/1 (ops@example.org)"},
    {"user_agent": "a" * 240 + " (ops@example.org)"},
    {"user_agent": None},
    {"user_agent": 123},
    {"user_agent": b"bot/1 (ops@example.org)"},
    {"accept": ""},
    {"accept": "text/html\r\nX-Evil: 1"},
    {"accept": "téxt/html"},
    {"accept": "a" * 513},
    {"accept": None},
    {"max_bytes": 0}, {"max_bytes": -1}, {"max_bytes": True}, {"max_bytes": 1.5},
    {"max_bytes": 100_000_001}, {"max_encoded_bytes": 0}, {"max_encoded_bytes": "1"},
    {"max_redirects": -1}, {"max_redirects": 11}, {"max_redirects": True},
    {"connect_timeout": 0}, {"connect_timeout": -1}, {"connect_timeout": math.nan},
    {"connect_timeout": math.inf}, {"connect_timeout": True}, {"connect_timeout": "1"},
    {"read_timeout": 0}, {"total_timeout": 0}, {"total_timeout": math.inf},
    {"ports": ()}, {"ports": (0,)}, {"ports": (65536,)}, {"ports": (True,)},
    {"ports": ("80",)},
    {"deny_networks": ("not-a-network",)},
    {"ssl_context": "yes"},
]


@pytest.mark.parametrize("kwargs", BAD_ARGS, ids=repr)
def test_bad_arguments(kwargs):
    net = AnythingResolves()
    kwargs = {"user_agent": UA, **kwargs}
    with pytest.raises(FetchConfigError) as err:
        fetch("http://public.example/", network=net, **kwargs)
    assert isinstance(err.value, ValueError)
    assert err.value.url is None
    assert net.resolved == [] and net.connected == []


def test_user_agent_is_required():
    with pytest.raises(TypeError):
        fetch("http://public.example/", network=AnythingResolves())


def test_arguments_checked_before_url():
    net = AnythingResolves()
    with pytest.raises(FetchConfigError):
        fetch("file:///etc/passwd", user_agent="curl/8", network=net)


def _weak_contexts():
    no_hostname = ssl.create_default_context()
    no_hostname.check_hostname = False
    no_verify = ssl.create_default_context()
    no_verify.check_hostname = False
    no_verify.verify_mode = ssl.CERT_NONE
    optional = ssl.create_default_context()
    optional.verify_mode = ssl.CERT_OPTIONAL
    return [no_hostname, no_verify, optional]


@pytest.mark.parametrize("context", _weak_contexts(), ids=["no-hostname", "cert-none",
                                                          "cert-optional"])
def test_weakened_tls_context_refused_even_for_http(context):
    net = AnythingResolves()
    with pytest.raises(FetchConfigError):
        get("http://public.example/", net, ssl_context=context)
    assert net.resolved == []


# -------------------------------------------------------------- proxies


def test_proxy_environment_is_ignored(server, net, monkeypatch, system_dns_guard):
    proxy = socket.socket()
    proxy.bind(("127.0.0.1", 0))
    proxy.listen(8)
    proxy.settimeout(0.2)
    hits = []

    def accept_loop():
        end = time.monotonic() + HANDLER_CAP
        while time.monotonic() < end:
            try:
                conn, _ = proxy.accept()
            except OSError:
                continue
            hits.append(conn)
            conn.close()

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{proxy.getsockname()[1]}"
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY",
                 "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, url)
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    server.routes["/p"] = ok(b"direct")
    try:
        result = get("http://public.example/p", net)
    finally:
        proxy.close()
    assert result.content == b"direct"
    assert hits == []
    assert server.log[0].request_line == "GET /p HTTP/1.1"  # origin form, not absolute
    assert net.addresses == [PUBLIC_V4]


# ------------------------------------------------------------------ TLS


@pytest.fixture
def tls_net(server, tls_server):
    return make_net(server, tls_server)


def test_https_uses_hostname_for_sni_and_verification(tls_server, tls_net):
    tls_server.routes["/ok"] = ok(b"secret")
    result = get("https://secure.example/ok", tls_net, ssl_context=client_tls_context())
    assert result.content == b"secret"
    assert result.url == "https://secure.example/ok"
    assert result.address == PUBLIC_V4_C
    assert tls_net.connected[0][:2] == (PUBLIC_V4_C, 443)
    assert tls_server.sni == ["secure.example"]
    assert tls_server.log[0].header("Host") == "secure.example"


def test_https_certificate_name_mismatch(tls_server, tls_net):
    tls_server.routes["/ok"] = ok()
    with pytest.raises(FetchTLSError) as err:
        get("https://wrong.example/ok", tls_net, ssl_context=client_tls_context())
    assert err.value.url == "https://wrong.example/ok"
    assert tls_server.log == []


def test_https_verifies_by_default(tls_server, tls_net):
    tls_server.routes["/ok"] = ok()
    with pytest.raises(FetchTLSError):
        get("https://secure.example/ok", tls_net)  # test CA is not trusted by default
    assert tls_server.log == []


def test_https_to_plain_server_is_tls_error(server):
    net = FakeNetwork({"secure.example": [PUBLIC_V4_C]}, {(PUBLIC_V4_C, 443): server.port})
    with pytest.raises(FetchTLSError):
        get("https://secure.example/", net, ssl_context=client_tls_context(),
            total_timeout=2)


def test_redirect_http_to_https_allowed(server, tls_server, tls_net):
    server.routes["/up"] = redirect("https://secure.example/ok", 301)
    tls_server.routes["/ok"] = ok(b"upgraded")
    result = get("http://public.example/up", tls_net, ssl_context=client_tls_context())
    assert result.content == b"upgraded"
    assert result.redirects == (Hop("http://public.example/up", 301),)
    assert result.url == "https://secure.example/ok"


def test_redirect_https_to_http_refused(server, tls_server, tls_net):
    tls_server.routes["/down"] = redirect("http://public.example/plain")
    server.routes["/plain"] = ok()
    with pytest.raises(FetchRedirectError) as err:
        get("https://secure.example/down", tls_net, ssl_context=client_tls_context())
    assert err.value.url == "https://secure.example/down"
    assert server.log == []
    assert [c[0] for c in tls_net.connected] == [PUBLIC_V4_C]


# -------------------------------------------------------------- redirects


def test_redirect_chain(server, net):
    server.routes["/r1"] = redirect("/r2", 301)
    server.routes["/r2"] = redirect("http://other.example/r3", 302)
    server.routes["/r3"] = redirect("final?x=1#frag", 303)
    server.routes["/final?x=1"] = ok(b"done")
    result = get("http://public.example/r1", net)
    assert result.content == b"done"
    assert result.url == "http://other.example/final?x=1"
    assert result.redirects == (
        Hop("http://public.example/r1", 301),
        Hop("http://public.example/r2", 302),
        Hop("http://other.example/r3", 303),
    )
    assert net.hosts == ["public.example", "public.example", "other.example",
                         "other.example"]                   # one lookup per hop
    assert net.addresses == [PUBLIC_V4, PUBLIC_V4, PUBLIC_V4_B, PUBLIC_V4_B]
    assert [s.header("Host") for s in server.log] == [
        "public.example", "public.example", "other.example", "other.example"]
    assert [s.request_line for s in server.log] == [
        "GET /r1 HTTP/1.1", "GET /r2 HTTP/1.1", "GET /r3 HTTP/1.1",
        "GET /final?x=1 HTTP/1.1"]
    for seen in server.log:
        assert seen.header("User-Agent") == UA


@pytest.mark.parametrize("status", [307, 308])
def test_method_preserving_redirects_followed(server, net, status):
    server.routes["/a"] = redirect("/b", status)
    server.routes["/b"] = ok()
    result = get("http://public.example/a", net)
    assert result.status == 200
    assert result.redirects == (Hop("http://public.example/a", status),)


@pytest.mark.parametrize("target", [
    "http://127.0.0.1/",
    "http://192.0.2.1/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[::ffff:7f00:1]/",
    "http://0.0.0.0/",
    "http://[64:ff9b::a00:1]/",
    "http://[fe80::1]/",
])
def test_redirect_to_private_literal_refused(server, net, target):
    server.routes["/r"] = redirect(target)
    with pytest.raises(FetchAddressError) as err:
        get("http://public.example/r", net)
    assert err.value.url == target
    assert net.addresses == [PUBLIC_V4]
    assert len(server.log) == 1


@pytest.mark.parametrize("answer", [["192.168.1.10"], ["127.0.0.1"], ["169.254.169.254"],
                                    ["::ffff:10.0.0.1"], ["fd00::5"]])
def test_redirect_to_name_resolving_private_refused(server, answer):
    net = make_net(server)
    net.answers["internal.example"] = answer
    net.routes[(answer[0], 80)] = server.port
    server.routes["/r"] = redirect("http://internal.example/admin")
    with pytest.raises(FetchAddressError) as err:
        get("http://public.example/r", net)
    assert err.value.url == "http://internal.example/admin"
    assert net.hosts == ["public.example", "internal.example"]
    assert net.addresses == [PUBLIC_V4]


@pytest.mark.parametrize("location", [
    "file:///etc/passwd", "ftp://public.example/", "gopher://127.0.0.1:25/_HELO",
    "http://public.example:25/", "http://user:pw@public.example/", "http://2130706433/",
    "http://localhost/", "/cafÃ©",
])
def test_redirect_location_must_pass_url_rules(server, net, location):
    server.routes["/r"] = redirect(location)
    with pytest.raises(FetchURLError) as err:
        get("http://public.example/r", net)
    assert err.value.url == "http://public.example/r"
    assert net.addresses == [PUBLIC_V4]


def test_redirect_loop_detected(server, net):
    server.routes["/a"] = redirect("/b")
    server.routes["/b"] = redirect("http://PUBLIC.example:80/a#x")
    with pytest.raises(FetchRedirectError) as err:
        get("http://public.example/a", net)
    assert [s.request_line for s in server.log] == ["GET /a HTTP/1.1", "GET /b HTTP/1.1"]
    assert err.value.url == "http://public.example/b"


def test_self_redirect_detected(server, net):
    server.routes["/self"] = redirect("/self")
    with pytest.raises(FetchRedirectError):
        get("http://public.example/self", net)
    assert len(server.log) == 1


def _chain(server, hops: int) -> None:
    for i in range(hops):
        server.routes[f"/c{i}"] = redirect(f"/c{i + 1}")
    server.routes[f"/c{hops}"] = ok(b"end")


def test_five_redirects_allowed_by_default(server, net):
    _chain(server, 5)
    result = get("http://public.example/c0", net)
    assert result.content == b"end"
    assert len(result.redirects) == 5


def test_sixth_redirect_refused_by_default(server, net):
    _chain(server, 6)
    with pytest.raises(FetchRedirectError) as err:
        get("http://public.example/c0", net)
    assert err.value.url == "http://public.example/c5"
    assert [s.request_line for s in server.log] == [
        f"GET /c{i} HTTP/1.1" for i in range(6)]          # /c6 never requested


def test_max_redirects_zero(server, net):
    _chain(server, 1)
    with pytest.raises(FetchRedirectError):
        get("http://public.example/c0", net, max_redirects=0)
    assert len(server.log) == 1


def test_redirect_without_location_is_a_result(server, net):
    server.routes["/r"] = lambda h: send(h, 302, b"moved?")
    result = get("http://public.example/r", net)
    assert result.status == 302
    assert result.redirects == ()
    assert result.content == b"moved?"


@pytest.mark.parametrize("status", [300, 304, 305])
def test_other_3xx_not_followed(server, net, status):
    server.routes["/r"] = lambda h: send(h, status, b"", (("Location", "/elsewhere"),))
    result = get("http://public.example/r", net)
    assert result.status == status
    assert result.headers["location"] == "/elsewhere"
    assert len(server.log) == 1


def test_redirect_body_not_read(server, net):
    server.routes["/r"] = stall_after(
        b"HTTP/1.1 302 Found\r\nLocation: /ok\r\nContent-Length: 10000000\r\n"
        b"Content-Encoding: br\r\n\r\npartial")
    server.routes["/ok"] = ok()
    start = time.monotonic()
    result = get("http://public.example/r", net, read_timeout=2, max_encoded_bytes=1000)
    assert result.status == 200
    assert time.monotonic() - start < 1.0


# ------------------------------------------------------- content encodings


BODY = b"The quick brown fox jumps over the lazy dog.\n" * 50


@pytest.mark.parametrize("coding,encoded", [
    ("gzip", gzip_bytes(BODY)),
    ("x-gzip", gzip_bytes(BODY)),
    ("GZIP", gzip_bytes(BODY)),
    ("identity, gzip", gzip_bytes(BODY)),
    ("gzip, identity", gzip_bytes(BODY)),
    (" gzip ,", gzip_bytes(BODY)),
    ("deflate", deflate_bytes(BODY)),
    ("deflate", deflate_bytes(BODY, raw_stream=True)),
    ("identity", BODY),
    ("", BODY),
    ("gzip", gzip_bytes(BODY[:100]) + gzip_bytes(BODY[100:])),  # two members
    ("gzip", gzip_bytes(BODY) + b"\0" * 7),                     # NUL padding
])
def test_content_codings_decoded(server, net, coding, encoded):
    server.routes["/c"] = lambda h: send(h, 200, encoded, (("Content-Encoding", coding),))
    result = get("http://public.example/c", net)
    assert result.content == BODY
    assert result.encoded_bytes == len(encoded)


def test_chunked_gzip(server, net):
    encoded = gzip_bytes(BODY)
    chunks = [encoded[i:i + 100] for i in range(0, len(encoded), 100)]
    payload = b"".join(b"%x\r\n%s\r\n" % (len(c), c) for c in chunks) + b"0\r\n\r\n"
    server.routes["/c"] = raw(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Encoding: gzip\r\n"
        b"Connection: close\r\n\r\n" + payload)
    result = get("http://public.example/c", net)
    assert result.content == BODY
    assert result.encoded_bytes == len(encoded)


@pytest.mark.parametrize("coding", ["br", "zstd", "compress", "x-compress", "bogus",
                                    "gzip, gzip", "deflate, gzip", "gzip, br"])
def test_unsupported_content_coding(server, net, coding):
    server.routes["/c"] = lambda h: send(h, 200, gzip_bytes(BODY),
                                         (("Content-Encoding", coding),))
    with pytest.raises(FetchEncodingError) as err:
        get("http://public.example/c", net)
    assert err.value.url == "http://public.example/c"


@pytest.mark.parametrize("coding,encoded", [
    ("gzip", b"\x1f\x8b" + bytes(random.Random(1).randrange(256) for _ in range(200))),
    ("gzip", b"definitely not gzip"),
    ("gzip", gzip_bytes(BODY)[:-30]),                   # truncated
    ("gzip", gzip_bytes(BODY) + b"trailing junk"),
    ("deflate", deflate_bytes(BODY)[:-10]),             # truncated
    ("deflate", deflate_bytes(BODY) + b"trailing"),
    ("deflate", b"\xff\xff\xff\xff"),
])
def test_corrupt_content_coding(server, net, coding, encoded):
    server.routes["/c"] = lambda h: send(h, 200, encoded, (("Content-Encoding", coding),))
    with pytest.raises(FetchEncodingError):
        get("http://public.example/c", net)


@pytest.mark.parametrize("coding", ["gzip", "deflate"])
def test_empty_body_with_coding(server, net, coding):
    server.routes["/c"] = lambda h: send(h, 200, b"", (("Content-Encoding", coding),))
    result = get("http://public.example/c", net)
    assert result.content == b""
    assert result.encoded_bytes == 0


# --------------------------------------------------------------- body limits


@pytest.mark.parametrize("coding,wbits", [("gzip", 31), ("deflate", 15)])
def test_decompression_bomb_stopped_at_limit(server, net, coding, wbits):
    bomb = zeros_compressed(32 << 20, wbits)   # 32 MiB of zeros, about 32 KiB compressed
    assert len(bomb) < 200_000
    server.routes["/bomb"] = lambda h: send(h, 200, bomb, (("Content-Encoding", coding),))
    tracemalloc.start()
    try:
        start = time.monotonic()
        with pytest.raises(FetchTooLargeError) as err:
            get("http://public.example/bomb", net, max_bytes=1_000_000)
        elapsed = time.monotonic() - start
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert err.value.url == "http://public.example/bomb"
    assert elapsed < 2.0
    assert peak < 16_000_000, f"peak {peak} bytes: the bomb was expanded"


def test_decoded_limit_boundary(server, net):
    exact = b"a" * 10_000
    server.routes["/exact"] = lambda h: send(h, 200, gzip_bytes(exact),
                                             (("Content-Encoding", "gzip"),))
    server.routes["/over"] = lambda h: send(h, 200, gzip_bytes(exact + b"a"),
                                            (("Content-Encoding", "gzip"),))
    assert get("http://public.example/exact", net, max_bytes=10_000).content == exact
    with pytest.raises(FetchTooLargeError):
        get("http://public.example/over", net, max_bytes=10_000)


def test_identity_limit_boundary(server, net):
    server.routes["/exact"] = ok(b"a" * 10_000)
    server.routes["/over"] = ok(b"a" * 10_001)
    assert len(get("http://public.example/exact", net, max_bytes=10_000).content) == 10_000
    with pytest.raises(FetchTooLargeError):
        get("http://public.example/over", net, max_bytes=10_000)


def test_declared_length_over_encoded_limit_fails_before_reading(server, net):
    server.routes["/big"] = stall_after(
        b"HTTP/1.1 200 OK\r\nContent-Length: 10000000\r\nContent-Encoding: gzip\r\n\r\n")
    start = time.monotonic()
    with pytest.raises(FetchTooLargeError):
        get("http://public.example/big", net, max_encoded_bytes=1_000_000, read_timeout=2)
    assert time.monotonic() - start < 1.0


def test_declared_identity_length_over_decoded_limit_fails_before_reading(server, net):
    server.routes["/big"] = stall_after(b"HTTP/1.1 200 OK\r\nContent-Length: 150000\r\n\r\n")
    start = time.monotonic()
    with pytest.raises(FetchTooLargeError):
        get("http://public.example/big", net, max_bytes=100_000, read_timeout=2)
    assert time.monotonic() - start < 1.0


def test_endless_body_without_length(server, net):
    server.routes["/endless"] = stream_forever(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n")
    start = time.monotonic()
    with pytest.raises(FetchTooLargeError):
        get("http://public.example/endless", net, max_bytes=200_000)
    assert time.monotonic() - start < 2.0


def test_encoded_limit_applies_to_compressed_bytes(server, net):
    noise = random.Random(7).randbytes(300_000)        # does not compress
    encoded = gzip_bytes(noise)
    server.routes["/noise"] = raw(b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n"
                                  b"Connection: close\r\n\r\n" + encoded)
    with pytest.raises(FetchTooLargeError):
        get("http://public.example/noise", net, max_encoded_bytes=100_000,
            max_bytes=10_000_000)


def test_chunked_body_over_limit(server, net):
    chunk = b"x" * 50_000
    payload = b"".join(b"%x\r\n%s\r\n" % (len(chunk), chunk) for _ in range(10)) + b"0\r\n\r\n"
    server.routes["/c"] = raw(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
                              b"Connection: close\r\n\r\n" + payload)
    with pytest.raises(FetchTooLargeError):
        get("http://public.example/c", net, max_bytes=200_000)


# ------------------------------------------------------------------- time


def test_slow_drip_headers_hit_total_limit(server, net):
    server.routes["/drip"] = drip(b"HTTP/1.1 200 OK\r\n", b"X")
    start = time.monotonic()
    with pytest.raises(FetchTimeoutError) as err:
        get("http://public.example/drip", net, read_timeout=2, total_timeout=0.4)
    elapsed = time.monotonic() - start
    assert 0.35 <= elapsed < 1.2
    assert err.value.url == "http://public.example/drip"


def test_slow_drip_body_hits_total_limit(server, net):
    server.routes["/drip"] = drip(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n", b"x")
    start = time.monotonic()
    with pytest.raises(FetchTimeoutError):
        get("http://public.example/drip", net, read_timeout=2, total_timeout=0.4)
    assert 0.35 <= time.monotonic() - start < 1.2


def test_total_limit_covers_redirect_hops(server, net):
    server.routes["/r"] = redirect("/drip")
    server.routes["/drip"] = drip(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n", b"x")
    start = time.monotonic()
    with pytest.raises(FetchTimeoutError) as err:
        get("http://public.example/r", net, read_timeout=2, total_timeout=0.4)
    assert time.monotonic() - start < 1.2
    assert err.value.url == "http://public.example/drip"


def test_read_timeout(server, net):
    server.routes["/stall"] = stall_after(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nab")
    start = time.monotonic()
    with pytest.raises(FetchTimeoutError):
        get("http://public.example/stall", net, read_timeout=0.3, total_timeout=5)
    assert 0.25 <= time.monotonic() - start < 1.2


def test_no_response_at_all_times_out(server, net):
    server.routes["/silent"] = stall_after(b"")
    with pytest.raises(FetchTimeoutError):
        get("http://public.example/silent", net, read_timeout=0.3, total_timeout=5)


# --------------------------------------------------------------- protocol


@pytest.mark.parametrize("response", [
    b"garbage\r\n\r\n",
    b"",                                                         # closed at once
    b"HTTP/1.1 200 OK\r\n" + b"".join(b"X-H%d: v\r\n" % i for i in range(150)) + b"\r\n",
    b"HTTP/1.1 200 OK\r\nX-Long: " + b"a" * 70_000 + b"\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\nonly ten b",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip, chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\nhello\r\n0\r\n\r\n",
    b"HTTP/1.1 999 Nope\r\nContent-Length: 0\r\n\r\n",
    b"HTTP/1.1 abc Nope\r\nContent-Length: 0\r\n\r\n",
], ids=["garbage", "empty", "too-many-headers", "long-header", "short-body",
        "transfer-gzip", "bad-chunk", "status-999", "status-text"])
def test_malformed_responses(server, net, response):
    server.routes["/bad"] = raw(response)
    with pytest.raises(FetchProtocolError):
        get("http://public.example/bad", net, read_timeout=1, total_timeout=3)


# ------------------------------------------------------------ errors, seam


def test_error_hierarchy():
    for cls in (FetchConfigError, FetchURLError, FetchAddressError, FetchConnectError,
                FetchTLSError, FetchTimeoutError, FetchRedirectError, FetchTooLargeError,
                FetchEncodingError, FetchProtocolError):
        assert issubclass(cls, FetchError)
    assert issubclass(FetchConfigError, ValueError)
    assert issubclass(FetchURLError, ValueError)
    for cls in (FetchAddressError, FetchConnectError, FetchTLSError, FetchTimeoutError,
                FetchRedirectError, FetchTooLargeError, FetchEncodingError,
                FetchProtocolError):
        assert not issubclass(cls, ValueError)
    assert not issubclass(FetchError, OSError)


def test_hop_is_frozen():
    hop = Hop("http://public.example/", 301)
    assert hop.url == "http://public.example/" and hop.status == 301
    with pytest.raises(dataclasses.FrozenInstanceError):
        hop.status = 302


def test_system_network_numeric_resolve_and_connect(server, monkeypatch):
    monkeypatch.undo()   # this test alone uses the real socket functions
    net = SystemNetwork()
    assert list(net.resolve("127.0.0.1", 80, 1.0)) == ["127.0.0.1"]
    sock = net.connect("127.0.0.1", server.port, 1.0)
    try:
        assert sock.getpeername() == ("127.0.0.1", server.port)
        assert sock.gettimeout() == 1.0
    finally:
        sock.close()


def test_no_environment_switch_widens_policy(server, monkeypatch):
    for name in ("FEDERATED_CMS_FETCH_ALLOW_PRIVATE", "FETCH_ALLOW_LOOPBACK", "TESTING"):
        monkeypatch.setenv(name, "1")
    net = FakeNetwork({"loop.example": ["127.0.0.1"]}, {("127.0.0.1", 80): server.port})
    with pytest.raises(FetchAddressError):
        get("http://loop.example/", net)
    assert is_public_address("127.0.0.1") is False
