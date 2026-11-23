# Linux RMS Gateway: source-level CMS interface findings

**Status:** source inspection and isolated login-routine test; full gateway interoperability remains untested. Inspected
[`nwdigitalradio/rmsgw` commit `3147f017545f43c4b83ef6a037b8b0e38d0719af`](https://github.com/nwdigitalradio/rmsgw/tree/3147f017545f43c4b83ef6a037b8b0e38d0719af)
(declared version 2.5.1). No full gateway was run, no production Winlink endpoint
was contacted, and no real credentials or RF equipment were used. These findings
apply to that source revision, not every Linux RMS, BPQ, or RMS gateway.

## Finding

The inspected gateway has an operator-editable CMS host table, so selecting an
independent TCP endpoint appears possible in principle. **This is not proof of
working redirection or compatibility.** Before forwarding a connected RF
client's traffic, this revision performs a separate gateway-to-CMS login. The
current Federated CMS B2F laboratory starts directly with a client B2F SID and
`;PQ:` challenge; it does not speak this gateway login or accept an AX.25
connection. Configuring the lab's current B2F port as the CMS host therefore
cannot by itself establish the inspected gateway session.

## Source-backed path

1. `rmsgw` receives the calling station's callsign as a command-line argument,
   normally supplied by `ax25d`; it takes a port/channel argument and looks up
   channel configuration. See [`man/rmsgw.1`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/man/rmsgw.1#L75-L102),
   [`rmsgw.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/rmsgw/rmsgw.c#L146-L199), and
   [`session.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/rmsgw/session.c#L88-L140).
2. It reads and cycles through the CMS host table; its man page describes the
   usual `/etc/rmsgw/hosts` and status directory. Each table row has hostname,
   TCP port and host-password fields. The source rejects ports below 1024 and
   empty host-password fields. See [`rmsgw.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/rmsgw/rmsgw.c#L324-L367),
   [`man/rmsgw.1`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/man/rmsgw.1#L100-L123),
   [`rmslib.h`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/include/rmslib.h#L112-L123), and
   [`getcmslist.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/lib/getcmslist.c#L60-L95).
   The host-table path is a build-time pathname; the gateway's `-c` option
   overrides its general configuration, not this host-table path. See
   [`pathnames.h.in`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/pathnames.h.in#L3-L11) and
   [`rmsgw.c` options](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/rmsgw/rmsgw.c#L146-L177).
3. The CMS connection is an IPv4 TCP socket resolved with `gethostbyname`,
   using the configured host and port. See
   [`cmsconnect.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/lib/cmsconnect.c#L58-L104).
4. Before relaying traffic, `cmslogin()` calls `sglProcess()`. It waits for a
   `Callsign :` prompt, sends the connected user callsign and the gateway
   channel callsign, receives a `;SQ:` challenge, and sends a `;SR:` response
   with additional fixed fields. The challenge response is computed from the
   configured channel password, not the client user's password. See
   [`cmslogin.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/lib/cmslogin.c#L207-L325) and
   [`cmslogin()`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/lib/cmslogin.c#L385-L401).
   This gateway login is distinct from the user's B2F `;PQ:` / `;PR:` login.
   The revision's [`sglchallenge.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/lib/sglchallenge.c#L73-L80)
   describes an MD5-derived response; do not infer that the protocols have the
   same authorization semantics merely because they use related primitives.
5. The code then relays data between the TCP socket and the RF-facing stream,
   with completion detection and a keepalive filter. See
   [`gateway.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/rmsgw/gateway.c#L101-L112),
   [CMS-to-AX.25 path](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/rmsgw/gateway.c#L173-L235), and
   [AX.25-to-CMS path](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/rmsgw/gateway.c#L292-L342).
   This source path suggests binary message data can be relayed; it is not a
   measured guarantee of binary transparency under fragmentation or load.

## Inconsistencies and operational constraints

- The [`gateway.conf` manual](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/man/rms-config.5#L64-L74)
  describes an `AUTHMODE` choice. The inspected [`loadconfig.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/lib/loadconfig.c#L44-L57)
  does not list that setting, and `cmslogin()` unconditionally uses SGL.
  `telnetProcess()` remains in the source but is not selected by `cmslogin()`.
- The host table still requires a nonempty host-password field, but this
  revision's active SGL path uses the channel's `ch_password`; the channel
  must have a password before the gateway tries connecting. See
  [`getcmslist.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/lib/getcmslist.c#L80-L95),
  [`session.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/rmsgw/session.c#L126-L140), and
  [`cmslogin.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/lib/cmslogin.c#L385-L401).
- `sglProcess()` considers the login flow complete after sending the response;
  comments say rejection is left to the later session. That is not evidence
  that the server accepted the gateway credentials. See
  [`cmslogin.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/lib/cmslogin.c#L318-L346).
- The inspected source logs gateway challenge/password material at debug level;
  use only disposable synthetic credentials in a laboratory and avoid retaining
  sensitive logs. See [`sglchallenge.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/lib/sglchallenge.c#L145-L155).
- Automatic channel/version reporting is a separate subsystem invoking update
  scripts. A safe offline test must disable it and all hooks rather than simply
  replacing the CMS host list. See [`sendchannel.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/rmsgw_aci/sendchannel.c#L45-L58),
  [`sendversion.c`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/rmsgw_aci/sendversion.c#L45-L57), and
  [`pathnames.h.in`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/pathnames.h.in#L11-L14).
- The upstream [README](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/README.md#L20-L32)
  lists AX.25 support and libraries, XML, ncurses, Python and requests; the
  checked [`configure.ac`](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/configure.ac#L10-L27)
  checks compiler, libxml2 and ncurses. Treat historical version requirements
  as unverified until an isolated build is attempted.

## Next isolated experiment

Create a separate test-only gateway login adapter and an isolated gateway
configuration whose only host is loopback. Use synthetic callsigns, passwords,
and B2 messages. Disable reporting, hooks and RF paths; enforce isolation from
production destinations. Test both valid and invalid `;SQ:` / `;SR:` gateway
logins before allowing a distinct client B2F session, and verify byte-for-byte
binary forwarding, fragmentation, disconnect and failure handling. Reproduce
the actual gateway process and its launch context before making a compatibility
claim. The existing client-only B2F and Pat-shim tests do not satisfy this gate.

## Isolated upstream login-routine experiment

`tests/test_rmsgw_login.py` compiles the pinned upstream `cmslogin.c`,
`sglchallenge.c`, `readln.c`, and `md5.c` into a small test harness. Set
`FCMS_RMSGW_SOURCE` to a clean checkout of the pinned revision and run
`PYTHONPATH=src python -m unittest tests.test_rmsgw_login -v`. The test requires
a Linux C compiler and uses a temporary build directory. It does not write to
the checkout. All interactions use an AF_UNIX socketpair, synthetic callsigns
and disposable fixture passwords, with debug syslog disabled. No gateway
configuration, IP sockets, hooks, production services or RF are involved.

Tests verify fragmented callsign and challenge prompts, a known response
vector, an unexpected challenge prefix, and disconnect before the challenge.
The inspected upstream routine accepts an eight-character nonnumeric challenge
after `;SQ:` and produces a response; a lab server must validate its own
challenge format and not infer gateway-side validation from the prefix check.
The wrong-password test also demonstrates that upstream `cmslogin()` returns
success after sending its response without waiting for server acceptance. The
test fixture—not the upstream routine—recognizes the response as incorrect.
This validates the isolated upstream function only, not the full gateway
process, a Federated CMS gateway adapter, binary relay, or interoperability.
