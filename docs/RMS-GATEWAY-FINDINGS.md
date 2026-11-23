# Linux RMS Gateway: source-level CMS interface findings

The pinned Linux RMS Gateway relayed bidirectional B2F exchanges from kissterm and Pat to a
Federated CMS mailbox through a synthetic Secure Gateway Login (SGL) adapter.
The experiment ran upstream gateway session and relay code with lab build paths
and shims in Linux namespaces. The simulated radio-facing streams used a socketpair or a loopback socket.
The gateway declares version 2.5.1.
[Inspected rmsgw revision](https://github.com/nwdigitalradio/rmsgw/tree/3147f017545f43c4b83ef6a037b8b0e38d0719af).

## Finding

The operator-editable host table selected the independent loopback endpoint.
The endpoint had to complete the gateway login first. Only then could the B2F
server send its SID and `;PQ:` mailbox challenge through the gateway to the
client. The direct B2F lab port starts with the SID, so this test used a
separate gateway adapter in front of the existing B2F session.

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

## Isolated full-process relay experiment

The optional Linux harness in `tests/rmsgw_full/` builds the pinned gateway
from a Git archive and starts its actual main, session and relay code inside
private user, network, IPC and mount namespaces. A synthetic local CMS checks
the gateway login; an AF_UNIX socketpair simulates the client stream. The
experiment verifies a valid login, rejection of an incorrect fixture secret,
all 256 byte values and fragments in both directions, and clean exit after
the mock CMS disconnects. See the harness README for prerequisites, isolation
details and the guarded entrypoint.

## Isolated client-to-mailbox experiment

The full-process harness now also runs the B2F client from kissterm commit
`f3310c71efa8e3e23263ed25745a9c365a9efc74` over the simulated client stream.
The pinned gateway connects to a synthetic SGL adapter on loopback. It answers
a fresh gateway challenge before the adapter hands the socket to Federated CMS.
The client then answers the B2F server's separate mailbox challenge.

The client sent and received synthetic B2 messages carrying binary attachments.
After the session completed, a reopened SQLite mailbox contained the exact
upload and retained download, with the delivery acknowledged. With a wrong
gateway secret the adapter rejected the connection before the B2F greeting.
With a valid gateway secret and a wrong mailbox password, the B2F client
received a protocol rejection. Neither rejection changed the seeded mailbox.

A second run used the Pat CLI built from commit
`2ad31e8fc1f1748ac1b00cac9d88da54e3e0efe2`. A loopback listener supplied
Pat's Telnet prompts and handed the connected socket to the gateway. Pat
composed and sent the upload, filed the received download in its inbox,
and exited successfully. Reopening the database confirmed the exact upload
and the acknowledged download. Pat's inbox preserved the complete downloaded
body and binary attachment.

In an ordinary five-case run, the gateway exited zero in the four other cases
and received SIGPIPE in the Pat case. A second Pat run traced the gateway
process with Linux strace 6.13 inside fresh isolated namespaces. The gateway
forwarded Pat's final B2F `FQ` to the CMS socket, read EOF on the Pat socket
and closed the CMS descriptor. Its session-statistics write to Pat returned
the full byte count. Its next write, the 21-byte station sign-off
`; N0CALL de N0LAB SK\n`, returned `EPIPE`, followed by SIGPIPE and process
exit. The successful statistics write establishes local kernel acceptance only.
Pat exited zero, and the client and reopened mailbox checks passed in both runs.

The pinned source writes the sign-off before recording logout and updating
per-user statistics. The signal interrupted those later steps in the traced
session. A clean gateway exit on the Pat path remains unverified. The trace
covers a single loopback session and does not establish close ordering on
other transports. [Pinned gateway session code](https://github.com/nwdigitalradio/rmsgw/blob/3147f017545f43c4b83ef6a037b8b0e38d0719af/rmsgw/session.c#L169-L201).

This validates the pinned client protocol engine and CLI, upstream gateway session
and relay code, test-only SGL adapter, and shared lab mailbox in the isolated
Linux build. The gateway uses the lab build paths and shims described above.
RF and AX.25, stock installed gateway binaries, other gateway revisions, and
production authentication remain unverified by this experiment.
[Reproduce the five namespace cases](../tests/rmsgw_full/README.md).
