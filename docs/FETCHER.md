# Safe fetcher (`federated_cms.fetch`)

**Status:** specification with contract tests (`tests/test_fetch.py`).

Fetches one http or https URL for a request that came from an untrusted
radio-mail user, and returns the decoded bytes. Every URL, redirect
target and DNS answer is hostile until checked. The fetcher must not
reach the node's own LAN, loopback, link-local or cloud metadata
addresses (`169.254.169.254`), and must stop on large bodies,
decompression bombs and slow servers ([THREAT-MODEL-M0.md](THREAT-MODEL-M0.md),
rows "URL-based SSRF and rebinding" and "Oversized or hostile input").

It has no mail, cache or storage code and imports nothing from the rest
of `federated_cms`, so it can move to a shared package with the text
extractor and feed reader. Callers pass `content` and `content_type` to
`federated_cms.plaintext.extract` or `federated_cms.feeds.parse_feed`.

## Dependencies

Python 3.11+ standard library only (`http.client`, `ssl`, `socket`,
`ipaddress`, `zlib`, `urllib.parse`). No dependency is needed: the one
piece the standard library lacks, connecting to an address chosen in
advance while keeping the hostname for `Host` and TLS, is a small
override of `http.client.HTTPConnection.connect` (see "Connecting").

`urllib.request` must not be used. It reads proxies from the
environment ("if proxy settings are detected (for example, when a
`*_proxy` environment variable like `http_proxy` is set), ProxyHandler
is default installed",
<https://docs.python.org/3/library/urllib.request.html#urllib.request.urlopen>),
follows redirects without a per-hop address check, and resolves the
host again inside `http.client`.

## API

```python
from federated_cms.fetch import (
    fetch, is_public_address, FetchResult, Hop, Network, SystemNetwork,
    FetchError, FetchConfigError, FetchURLError, FetchAddressError,
    FetchConnectError, FetchTLSError, FetchTimeoutError, FetchRedirectError,
    FetchTooLargeError, FetchEncodingError, FetchProtocolError,
)
```

### Errors

```
FetchError(Exception)                  .url: str | None
├── FetchConfigError(FetchError, ValueError)   bad arguments from the caller
├── FetchURLError(FetchError, ValueError)      URL or Location refused
├── FetchAddressError(FetchError)              address refused by policy
├── FetchConnectError(FetchError)              no address, or every connection failed
├── FetchTLSError(FetchError)                  handshake, certificate or hostname failure
├── FetchTimeoutError(FetchError)              read timeout or total time limit
├── FetchRedirectError(FetchError)             too many, loop, or https -> http
├── FetchTooLargeError(FetchError)             a byte limit was exceeded
├── FetchEncodingError(FetchError)             unsupported or corrupt content coding
└── FetchProtocolError(FetchError)             malformed or incomplete HTTP response
```

`url` is a normalized URL (see "URLs"):

- `FetchConfigError`, and `FetchURLError` for the start URL: `None`.
- `FetchURLError` for a `Location`, and `FetchRedirectError`: the URL
  whose response carried the redirect.
- Every other error: the URL being resolved, connected to or read. For
  a redirect target that passed the URL rules, that is the target.

Every exception from `fetch` is a `FetchError`; `OSError`,
`ssl.SSLError`, `http.client.HTTPException`, `zlib.error` and
`UnicodeError` never escape. The original exception is the
`__cause__`.

### Result

```python
@dataclass(frozen=True)
class Hop:
    url: str          # normalized URL that answered with a redirect
    status: int       # 301, 302, 303, 307 or 308

@dataclass(frozen=True)
class FetchResult:
    url: str                     # normalized final URL
    status: int                  # final status, 100-599 (never a followed redirect)
    reason: str                  # reason phrase, cleaned (see below), may be ""
    headers: Mapping[str, str]   # read-only, values cleaned; see "Response headers"
    content_type: str | None     # cleaned Content-Type value, or None
    media_type: str | None       # "type/subtype" lowercased, or None
    content: bytes               # body after content decoding
    encoded_bytes: int           # body bytes received before content decoding
    address: str                 # IP address connected to for the final hop
    redirects: tuple[Hop, ...]   # in order; empty when none were followed
    fetched_at: datetime         # UTC, timezone-aware, when the final headers arrived
    elapsed: float               # seconds (time.monotonic) for the whole call

    @property
    def ok(self) -> bool: ...    # 200 <= status <= 299
```

**Non-2xx statuses are results, not errors.** NWS returns problem
details with 404 and 500, and Wikipedia's API returns error bodies; the
caller decides what to tell the user. The body of a non-2xx response is
read, limited and decoded like any other.

**Cleaned text.** `http.client` decodes the status line and header
fields as ISO-8859-1, so a server can put escape sequences or other
control characters into the reason phrase and header values. `fetch`
replaces every character outside U+0020 to U+007E in `reason` and in
each value of `headers` (and so in `content_type` and `media_type`) with
`?`. Callers can put them in mail without further cleaning. Redirects
are followed using the raw `Location` value, which the URL rules check.

### `fetch(url, *, user_agent, ...) -> FetchResult`

```python
def fetch(
    url: str,
    *,
    user_agent: str,
    accept: str = "*/*",
    max_bytes: int = 2_000_000,
    max_encoded_bytes: int = 2_000_000,
    max_redirects: int = 5,
    connect_timeout: float = 10.0,
    read_timeout: float = 15.0,
    total_timeout: float = 30.0,
    ports: Iterable[int] = (80, 443),
    deny_networks: Iterable[str] = (),
    ssl_context: ssl.SSLContext | None = None,
    network: Network | None = None,
) -> FetchResult
```

Only `GET` is sent. There is no parameter for extra request headers,
cookies, credentials or a request body.

## Checks, in order

1. **Arguments** (`FetchConfigError`, before any URL parsing or
   network call). See "Arguments".
2. **URL** (`FetchURLError`). See "URLs".
3. **Address.** An IP-literal host is checked without calling
   `network.resolve`. A name is resolved once, and every address
   returned is checked (`FetchAddressError`). See "Addresses".
4. **Connect** to a checked address (`FetchConnectError`), refuse a
   connection to the node itself (`FetchAddressError`), then TLS for
   https (`FetchTLSError`, or `FetchTimeoutError` for a slow handshake).
5. **Request and response** (`FetchProtocolError`, `FetchTimeoutError`).
6. **Redirect** handling, which repeats steps 2 to 5 for the new URL
   (`FetchRedirectError`).
7. **Body** limits and decoding (`FetchTooLargeError`,
   `FetchEncodingError`).

## Arguments

`FetchConfigError` when any of these fails:

- `user_agent`: a `str` of 1 to 256 characters, each U+0020 to U+007E,
  with no leading or trailing space, that contains a contact: an
  `http://` or `https://` URL, or an email address
  (`[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`), found with
  `re.search`. Wikimedia: "Scripts should use an informative User-Agent
  string with contact information, or they may be blocked without
  notice" and "The contact information should be given as an email
  address, a website, or a wiki user"
  (<https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy>).
  NWS: "A User Agent is required to identify your application"
  (<https://www.weather.gov/documentation/services-web-api>). A wiki user
  name alone is not accepted here.
- `accept`: a `str` of 1 to 512 characters, each U+0020 to U+007E. NWS
  selects formats with it, e.g. `application/geo+json` or
  `application/cap+xml`.
- `max_bytes`, `max_encoded_bytes`: `int` (not `bool`), 1 to
  100_000_000.
- `max_redirects`: `int` (not `bool`), 0 to 10.
- `connect_timeout`, `read_timeout`, `total_timeout`: `int` or `float`
  (not `bool`), finite and greater than 0.
- `ports`: an iterable, not a `str` or `bytes`, with at least one item,
  each an `int` (not `bool`) from 1 to 65535.
- `deny_networks`: an iterable, not a `str` or `bytes`, each item
  accepted by `ipaddress.ip_network(x, strict=False)`.
- `ssl_context`: `None`, or an `ssl.SSLContext` with
  `check_hostname is True`, `verify_mode == ssl.CERT_REQUIRED`,
  `minimum_version` equal to `ssl.TLSVersion.TLSv1_2` or
  `ssl.TLSVersion.TLSv1_3`, and `keylog_filename is None`. A context
  from `ssl.create_default_context()` has `minimum_version ==
  TLSVersion.MINIMUM_SUPPORTED` and is refused until the caller sets it.
  A weakened context is refused even for http URLs.

`ports` and `deny_networks` are each converted to a tuple once, at the
start of the call, so a one-shot iterator applies to every hop. A bare
`str` or `bytes` is refused instead of being read one character at a
time.

`deny_networks` and `ports` can only narrow what is reachable: no
argument allows an address that `is_public_address` refuses.

No module in `src/` other than `federated_cms/fetch.py` passes
`network=`; a test checks this. Services always use the system
network.

## URLs

The same rules apply to the start URL and to every resolved `Location`.
Failures raise `FetchURLError`.

1. `str`, 1 to 2048 characters, each U+0021 to U+007E. No spaces,
   control characters or non-ASCII; the caller percent-encodes and
   IDNA-encodes first. This runs before `urllib.parse.urlsplit`, which
   does not validate ("`urlsplit()` does not perform validation") and
   changes its input ("`\n`, `\r` and tab `\t` characters are removed
   from the URL at any position",
   <https://docs.python.org/3/library/urllib.parse.html#urllib.parse.urlsplit>).
2. Scheme `http` or `https`, case-insensitive.
3. A non-empty host. No userinfo (`@` anywhere in the authority).
4. Port: `urlsplit(...).port` must not raise; an absent or empty port
   means 80 for http and 443 for https. The effective port must be in
   `ports`.
5. Host, lowercased:
   - **IPv6 literal** in brackets: parsed with `ipaddress.IPv6Address`.
     A zone ID (`%`) is refused.
   - **Numeric host.** If the host "ends in a number" as defined by the
     WHATWG URL Standard (<https://url.spec.whatwg.org/#ends-in-a-number-checker>;
     here: the last dot-separated label matches `[0-9]+` or
     `0[xX][0-9a-fA-F]*`), it must be
     a dotted-decimal IPv4 address that `ipaddress.IPv4Address` accepts.
     That constructor refuses leading zeros ("Leading zeroes are not
     tolerated to prevent confusion with octal notation",
     <https://docs.python.org/3/library/ipaddress.html#ipaddress.IPv4Address>).
     So `2130706433`, `0x7f.1`, `127.1`, `0177.0.0.1` and `1.2.3.4.5`
     are refused. glibc's `getaddrinfo` reads each of the first four as
     `127.0.0.1`, which is why they never reach the resolver.
   - **Name:** at least two labels; labels of 1 to 63 characters from
     `a-z 0-9 - _`, not starting or ending with `-`; at most 253
     characters in total; no trailing dot. `localhost` and names under
     `.localhost` (RFC 6761 section 6.3), `.local` (RFC 6762) and
     `.home.arpa` (RFC 8375) are refused. These are cheap early
     refusals; the address check is what protects the LAN.
6. The fragment is removed and never sent.

**Normalized URL** (used in `FetchResult.url`, `Hop.url`, error `url`):
lowercase scheme; lowercase host (an IPv6 literal lowercased and in
brackets, otherwise as written, because `str(IPv6Address)` output for
IPv4-mapped addresses changed in Python 3.13); `:port` only when it differs from
the scheme default; the path as given, or `/` when empty; `?query` when
the query is non-empty; no fragment.

## Addresses

`is_public_address(address) -> bool` is the only address policy. It
has no switch, and no argument of `fetch` can widen it.

`address` is a `str` or an `ipaddress.IPv4Address`/`IPv6Address`. Any
other type, a string that `ipaddress.ip_address` does not accept, and
any address with a zone ID return `False`.

1. An IPv4-mapped IPv6 address (`::ffff:0:0/96`) is judged by its
   embedded IPv4 address. OWASP: "An IPv4-mapped address
   (`::ffff:169.254.169.254`) reaches the same host as its IPv4 form"
   (<https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html>).
2. **NAT64.** An address in the well-known prefix `64:ff9b::/96`
   (RFC 6052) is judged by the IPv4 address in its last 32 bits. A
   NAT64 gateway on the node's network would translate
   `64:ff9b::a00:1` to `10.0.0.1`. RFC 6052 section 3.1 forbids that
   use ("The Well-Known Prefix MUST NOT be used to represent non-global
   IPv4 addresses"), but DNS answers are attacker-controlled. The
   local-use prefix `64:ff9b:1::/48` (RFC 8215) has no fixed format and
   is refused by rule 4.
3. **IPv4** is refused when inside any of:

   | Block | Use (IANA IPv4 Special-Purpose Address Registry) |
   | --- | --- |
   | `0.0.0.0/8` | "This network", includes `0.0.0.0` |
   | `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` | Private-Use |
   | `100.64.0.0/10` | Shared Address Space (carrier NAT, Tailscale) |
   | `127.0.0.0/8` | Loopback |
   | `169.254.0.0/16` | Link Local, includes cloud metadata |
   | `192.0.0.0/24` | IETF Protocol Assignments |
   | `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24` | Documentation |
   | `192.88.99.0/24` | Deprecated 6to4 relay anycast |
   | `198.18.0.0/15` | Benchmarking |
   | `224.0.0.0/4` | Multicast |
   | `240.0.0.0/4` | Reserved, includes `255.255.255.255` |

4. **IPv6** is refused unless inside `2000::/3`, the only block IANA
   assigns for global unicast ("IANA unicast address assignments are
   currently limited to the IPv6 unicast address range of 2000::/3",
   IANA IPv6 Address Space registry). That one rule refuses `::`, `::1`,
   the deprecated IPv4-compatible `::/96`, `64:ff9b:1::/48`, `100::/64`,
   `5f00::/16`, `fc00::/7`, `fe80::/10`, the deprecated site-local
   `fec0::/10` and multicast `ff00::/8`. Inside `2000::/3` these are
   also refused:

   | Block | Use (IANA IPv6 Special-Purpose Address Registry) |
   | --- | --- |
   | `2001::/23` | IETF Protocol Assignments, includes Teredo `2001::/32` |
   | `2001:db8::/32`, `3fff::/20` | Documentation |
   | `2002::/16` | 6to4, embeds an IPv4 address |

5. Finally, refused when `ipaddress`'s `is_global` is `False`.

For rules 1 and 2, rules 3 and 5 are applied to the embedded IPv4
address.

`ipaddress.is_global` alone is not enough, and its answers changed in
Python 3.13 ("Fixed some false positives and false negatives"). On
Python 3.13.5 it returns `True` for `::7f00:1` (IPv4-compatible
loopback), `64:ff9b::a00:1` (NAT64 to `10.0.0.1`), `fec0::1`, `5f00::1`,
`4000::1`, `192.0.0.9` and `192.88.99.1`. The tables above make the
policy the same on every supported Python; `is_global` only adds
refusals.

Within `fetch`, an address is also refused when it falls inside any
`deny_networks` entry, checked against both the address and, for rules
1 and 2, the embedded IPv4 address. Operator uses:

- The node's own public IPv4 address, when the router forwards it back
  to the LAN (hairpin NAT).
- A network-specific NAT64 prefix, when the site runs DNS64 with one.
  Rule 2 only knows the well-known prefix; addresses under any other
  prefix are judged as ordinary IPv6 and would reach LAN hosts through
  the translator.
- AMPRNet, if the operator does not want requests reaching it. ARDC
  holds "more than 12 million IP addresses in the 44.0.0.0/9 and
  44.128.0.0/10 address spaces" (<https://www.ardc.net/about/>). They
  are globally routed and allowed by default; a node with a 44Connect
  tunnel reaches them over that tunnel.

**Self-connection.** After each TCP connect, `fetch` compares the
address it passed to `network.connect` with the local address of the
returned socket (`sock.getsockname()[0]`). It raises
`FetchAddressError`, without sending a request or trying another
address, when they are equal, or when both are IPv6 and share the same
/64. This stops a request to the node's own public address, or to a
neighbour on its IPv6 subnet, without a configured list.

**All answers are checked.** If any address returned for a name is
refused, the whole hop fails with `FetchAddressError` and nothing is
connected. A name that resolves to both public and private addresses is
a rebinding signal. OWASP: "Retrieve all the IP addresses behind the
domain name (records A + AAAA for IPv4 + IPv6) and apply the same
public-address verification".

## Connecting

DNS rebinding works because a check and a connection can use two
different lookups. OWASP: "A second, unchecked DNS lookup between
validation and connection can bypass these checks" and "Use an HTTP
client mechanism that connects to the validated IP while preserving the
original hostname".

`http.client` does that second lookup: `HTTPConnection.connect()` calls
`self._create_connection((self.host, self.port), ...)`, which is
`socket.create_connection`, and that "will try to resolve it for both
AF_INET and AF_INET6"
(<https://docs.python.org/3/library/socket.html#socket.create_connection>).
So:

- An IP-literal host is passed to `network.connect` as
  `str(ipaddress.ip_address(host))`; resolver answers are passed
  unchanged. `FetchResult.address` is the string passed to the
  `network.connect` call that succeeded.
- Each hop calls `network.resolve(host, port, timeout)` **exactly once**
  (not at all for an IP literal) and connects only to addresses from
  that answer, in the order given. If a connection attempt fails, the
  next address is tried; every address was checked, as OWASP asks
  ("Apply the destination policy to retries and fallback connections as
  well"). Only a failed `network.connect` moves on to the next address.
  When all fail, `FetchConnectError`, including when an attempt timed
  out. A TLS failure or a self-connection refusal ends the hop at once. An empty answer, or `OSError` from `resolve`, is
  `FetchConnectError`; a `TimeoutError` from `resolve` is
  `FetchTimeoutError`.
- The implementation overrides `connect()` of an
  `http.client.HTTPConnection` subclass (or does the equivalent) so that
  it installs the socket returned by `network.connect` instead of
  opening its own. `fetch` never calls `socket.getaddrinfo`,
  `socket.create_connection` or `socket.gethostbyname` itself.
- For https, `fetch` wraps that socket with
  `ssl_context.wrap_socket(sock, server_hostname=host)`, where `host` is
  the URL host, not the IP. That sends SNI and checks the certificate
  against the name ("server_hostname specifies the hostname of the
  service which we are connecting to",
  <https://docs.python.org/3/library/ssl.html#ssl.SSLContext.wrap_socket>).
  For an IP-literal host, `server_hostname` is the IP string, checked
  against the certificate's IP address entries. The handshake runs with
  `do_handshake_on_connect=False` and an explicit `do_handshake()` under
  a timeout of `min(connect_timeout, remaining)`.
- The default context is `ssl.create_default_context()`, which sets
  `CERT_REQUIRED` and, through `PROTOCOL_TLS_CLIENT`, hostname checking.
  `fetch` then sets `minimum_version = ssl.TLSVersion.TLSv1_2` and
  `keylog_filename = None`: `create_default_context` copies the
  `SSLKEYLOGFILE` environment variable into `keylog_filename`
  (CPython `Lib/ssl.py`), which would write session keys to disk. The
  default trust store is OpenSSL's, which reads the `SSL_CERT_FILE` and
  `SSL_CERT_DIR` environment variables (`ssl.get_default_verify_paths()`
  reports them as `openssl_cafile_env` and `openssl_capath_env`). Those
  are the operator's environment, not something a request can change.
- A handshake that times out raises `FetchTimeoutError`. Any other
  handshake or certificate failure (`ssl.SSLError`,
  `ssl.CertificateError`, or `OSError` during the handshake) raises
  `FetchTLSError`. Neither tries the next address.
- One connection per hop, closed after the hop. Redirects never reuse a
  connection.

### `Network` (the test seam)

```python
class Network(Protocol):
    def resolve(self, host: str, port: int, timeout: float) -> Sequence[str]: ...
    def connect(self, address: str, port: int, timeout: float) -> socket.socket: ...
```

`fetch(network=None)` uses `SystemNetwork()`:

- `resolve` calls `socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)`
  and returns the `sockaddr[0]` strings, duplicates removed, first
  occurrence kept. `getaddrinfo` has no timeout, so it runs in a daemon
  thread; if it has not returned within `timeout`, `resolve` raises
  `TimeoutError` and abandons the thread.
- At most 8 lookup threads may be running at once, across all
  `SystemNetwork` instances in the process (abandoned ones count until
  `getaddrinfo` returns). A ninth call raises `OSError` without starting
  a thread, which `fetch` reports as `FetchConnectError`. This bounds
  the threads a stuck resolver can pile up.
- `connect` creates a `socket.socket` of the address's family, sets
  `timeout`, connects to `(address, port)` and returns the socket. It
  does no name resolution.

The seam replaces only the resolver and the TCP dial. URL rules, the
address policy, TLS, HTTP parsing, limits and redirects all run
unchanged. A test makes `public.example` resolve to `93.184.215.14` and
maps that address to a server on `127.0.0.1`; if a fake resolver
answered `127.0.0.1`, `fetch` would refuse it as it would in
production. There is no environment variable, global or module flag
that changes behaviour.

`network.resolve` receives `timeout = min(connect_timeout, remaining)`
and `network.connect` receives `timeout = min(connect_timeout,
remaining)`, where `remaining` is what is left of `total_timeout`. The
TLS handshake uses the same bound.

## Proxies and ambient state

`fetch` never uses a proxy and never reads `http_proxy`, `https_proxy`,
`all_proxy`, `no_proxy` or their uppercase forms, macOS or Windows proxy
settings, `.netrc`, or a cookie jar. The request line is always in
origin form (`GET /path?query HTTP/1.1`).

## Request

Exactly these header fields, once each, in this order:

```
Host: <host>[:<port>]
User-Agent: <user_agent>
Accept: <accept>
Accept-Encoding: gzip, deflate
Connection: close
```

`Host` is the lowercase URL host (IPv6 in brackets), with `:port` only
when the port differs from the scheme default. Use
`putrequest(..., skip_host=True, skip_accept_encoding=True)` so
`http.client` adds neither
(<https://docs.python.org/3/library/http.client.html#http.client.HTTPConnection.putrequest>).
The path and query are sent exactly as they appear in the URL,
percent-encoding kept.

## Time limits

- `connect_timeout` bounds resolution, each TCP connect and the TLS
  handshake.
- `read_timeout` bounds each wait for data from the server.
- `total_timeout` bounds the whole call, all hops included, measured
  with `time.monotonic()`. Every wait uses at most the time remaining.
  `http.client` sets one socket timeout per call and reads many times
  inside `HTTPResponse.begin()` and `read()`, so a server that sends one
  byte just inside each read timeout would never time out. Enforce the
  deadline per receive (for example a wrapper that sets
  `min(read_timeout, remaining)` before each `recv`) or close the socket
  at the deadline.
- For https the wrapper or timer must act on the `ssl.SSLSocket`.
  `SSLContext.wrap_socket` detaches the plain socket (`sock.detach()`
  in `SSLSocket._create`), so anything wrapped around the plain socket
  no longer sees the reads.
- When the time remaining is zero or less, `fetch` raises
  `FetchTimeoutError` before starting any wait. It never calls
  `settimeout` with 0 (non-blocking mode) or a negative value (which
  raises `ValueError`). This applies after a slow `network.resolve` or
  `network.connect` returns, too.

A read timeout or the total limit raises `FetchTimeoutError`. A
`FetchTimeoutError` from the total limit is raised no later than about
0.2 s after the deadline.

## Response

`http.client` parses the status line and headers. Its limits (lines of
65536 bytes, 100 header fields) and any malformed status line, early
close, bad chunk framing or incomplete body raise `FetchProtocolError`.
So do:

- A status outside 100 to 599; `http.client` itself accepts up to 999.
- A final status from 101 to 199. `http.client` skips `100 Continue`
  responses itself; any other 1xx it would return as the final
  response.
- `Transfer-Encoding`, judged on all its field lines joined with `", "`,
  that is anything other than `chunked` (case-insensitive, surrounding
  spaces ignored). `http.client` only decodes `chunked`, and would
  otherwise return chunk framing as content. So `gzip, chunked`, and
  `chunked` sent twice, are refused.
- `Transfer-Encoding` and `Content-Length` both present. RFC 9112
  section 6.3: "Such a message might indicate an attempt to perform
  request smuggling ... and ought to be handled as an error."
- An invalid `Content-Length`. All its field lines are split on commas
  and each element has surrounding spaces and tabs removed; every
  element must match `[0-9]+` and all must be equal. RFC 9112 section
  6.3 allows only that case ("unless the field value can be
  successfully parsed as a comma-separated list ..., all values in the
  list are valid, and all values in the list are the same"). `5`,
  `5, 5` and two lines of `5` are accepted; `+5`, `0x5`, `5;x`, `5, 6`
  and an empty value are not.

**Bytes on the wire.** Every byte read from the socket for a hop counts:
status line, headers, `1xx` responses that `http.client` skips, chunk
size lines and extensions, trailers and body. More than
`max_encoded_bytes + 262144` (256 KiB for framing) raises
`FetchTooLargeError`. Without this, `http.client` would read up to 100
header lines of 64 KiB, any number of `100 Continue` responses, and any
number of trailer lines, none of which count as body.

### Redirects

- Followed: 301, 302, 303, 307 and 308 with a `Location`. Every followed
  redirect is a `GET` with the same request headers. Other 3xx
  statuses, and a redirect status without `Location`, are returned as
  the result.
- `Location` is resolved with `urllib.parse.urljoin` against the
  current normalized URL (RFC 9110 section 10.2.2: "When it has the form
  of a relative reference ..., the final value is computed by resolving
  it against the target URI"), then passes every URL rule and address
  check again (`FetchURLError`, `FetchAddressError`). The fragment is
  dropped; the fetcher never uses fragments, so RFC 9110's fragment
  inheritance does not apply.
- The redirect response body is not read.
- A redirect from https to http raises `FetchRedirectError`. Content
  fetched over TLS must not be replaced by content anyone on the path
  can change. http to https is allowed.
- A `Location` equal (after normalization) to a URL already requested
  in this call raises `FetchRedirectError`. RFC 9110 section 15.4: "A
  client SHOULD detect and intervene in cyclical redirections".
- Following more than `max_redirects` redirects raises
  `FetchRedirectError`; with `max_redirects=0` the first followable
  redirect raises. The default 5 matches RFC 9110's note: "An earlier
  version of this specification recommended a maximum of five
  redirections".

### Response headers

`FetchResult.headers` is a read-only mapping
(`types.MappingProxyType`) with lowercase names, holding only these
fields when present: `cache-control`, `content-encoding`,
`content-language`, `content-length`, `content-type`, `date`, `etag`,
`expires`, `last-modified`, `location`, `retry-after`. Repeated fields
are joined with `", "` as `HTTPResponse.getheader` does. Others, such as
`set-cookie`, are left out.

`media_type` is the part of `Content-Type` before any `;`, stripped and
lowercased, when it contains exactly one `/`; otherwise `None`.

### Body limits

- If `Content-Length` is present and greater than `max_encoded_bytes`
  (for identity, greater than the smaller of the two limits),
  `FetchTooLargeError` is raised before the body is read.
- More than `max_encoded_bytes` body bytes received (after chunked
  decoding, before content decoding) is `FetchTooLargeError`.
- More than `max_bytes` of decoded content is `FetchTooLargeError`. For
  identity, both limits apply to the same bytes.
- The body is read in pieces of at most 65536 bytes and decoded as it
  arrives. The fetcher never holds more than `max_bytes + 65536` decoded
  bytes plus one piece of encoded input. Partial content is never
  returned.

### Content codings

`Accept-Encoding: gzip, deflate` is sent; identity is acceptable by
default (RFC 9110 section 12.5.3, rule 2). The response's
`Content-Encoding` is split on commas; entries are stripped and
lowercased; empty entries and `identity` are ignored.

- No entries left: identity.
- One entry, `gzip` or `x-gzip` (RFC 9110 section 8.4.1.3: "A recipient
  SHOULD consider "x-gzip" to be equivalent to "gzip""): decoded with
  `zlib.decompressobj(wbits=31)`. After the end of a member, NUL bytes
  are skipped, as Python's `gzip` module does, and any further bytes
  start a new member.
- One entry, `deflate`: RFC 9110 section 8.4.1.2 says it is "a "zlib"
  data format [RFC1950] containing a "deflate" compressed data stream",
  and notes "Some non-conformant implementations send the "deflate"
  compressed data without the zlib wrapper". The first body bytes are
  buffered until there are two (a read may return one). If those two
  bytes are a valid zlib header (`b0 & 0x0F == 8` and
  `(b0 * 256 + b1) % 31 == 0`), `wbits=15`; otherwise raw deflate,
  `wbits=-15`. Bytes after the end of the stream are
  `FetchEncodingError`.
- Anything else (`br`, `zstd`, `compress`, unknown names) or more than
  one coding: `FetchEncodingError`, without reading the body.
- A stream that ends before `eof` (truncated) or that zlib rejects:
  `FetchEncodingError`.
- An empty body is `b""` whatever the coding.

Codings are checked only on the response that becomes the result; a
followed redirect's body is never read or decoded.

Each `decompress` call passes `max_length = max_bytes - produced + 1`
and feeds `unconsumed_tail` back until it is empty ("If the optional
parameter max_length is non-zero then the return value will be no
longer than max_length ... unconsumed data will be stored in the
attribute unconsumed_tail",
<https://docs.python.org/3/library/zlib.html#zlib.Decompress.decompress>).
A 64 MB run of zeros compresses to about 64 KB; this is how it is
stopped at `max_bytes` without being expanded.

## Not in scope

Caching, retries, rate limits per user or per host, `robots.txt`,
`HEAD`, request bodies, HTTP/2, brotli, and choosing which hosts a
service may use. Those belong to the services that call `fetch`.

## Tests

`tests/test_fetch.py` runs offline in a few seconds. It starts a plain
and a TLS server on `127.0.0.1` (the TLS server does each handshake in
its handler thread with a timeout, so a stalled client cannot block
later tests) and passes a fake `Network` whose
resolver answers public-looking addresses (`93.184.215.14`, ...) and
whose dialer maps them to those servers. An autouse fixture replaces
`socket.getaddrinfo`, `socket.create_connection` and
`socket.gethostbyname` with functions that fail the test, so any lookup
or dial outside the seam is caught.

The TLS fixtures in `tests/fixtures/fetch/` (a test CA, and a
certificate for `secure.example` and IP address `93.184.215.16`, valid
until 2126) are made by
`tests/fixtures/fetch/make-certs.sh`. The private keys are test
material only.
