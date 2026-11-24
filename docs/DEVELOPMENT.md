# Development and BPQ bridge setup

This is an M1 research prototype. It has a local SQLite store, a deterministic
weather service, an ASCII terminal adapter, and an experimental B2F lab. It
does not implement federation.

## Linux and Windows development

Install Python 3.11 or later, then from the repository root run:

```text
python -m venv .venv
.venv/bin/python -m pip install -e ".[test]"        # Linux/macOS
.venv\Scripts\python -m pip install -e ".[test]"  # Windows PowerShell/cmd
python -m pytest
```

Start a loopback-only listener (works on either platform):

```text
python -m federated_cms.bpq --listen 127.0.0.1:63190 --database fcms.sqlite3
```

Connect with a local terminal, send `KC1JMH-8` as the first line if simulating
BPQ, then use `WX FN43`, `JOBS`, and `RESULT <id>`. Results are deliberately
synthetic and labeled `TEST DATA`. The database is retained across restarts.
Weather is public test data: its random request ID is a receipt capability, so
`RESULT <id>` works after reconnecting. Private mailbox data is never exposed
through this terminal before a separate authentication/delegation design exists.

## Experimental B2F handshake

The separate binary-clean lab listener can be started with:

```text
python -m federated_cms.b2f --port 63191 --lab-account KC1JMH --password-file lab-password.txt --database fcms-b2f-lab.sqlite3
```

It binds only to localhost and exchanges synthetic B2 messages through the
same SQLite lab mailbox service exposed by `CMSCore.lab_mailbox()`. This
service is separate from the ordinary text mailbox and production accounts.
Create `lab-password.txt` manually with a disposable test
password, restrict file permissions to the local test account, and configure the
same test password in the client. The server issues a B2F `;PQ:` challenge and
checks the client's `;PR:` response against that single configured lab account.
Do not enter a lab password into a production-linked Winlink Express setup:
Winlink Express can update the real account password. See
[Winlink's password guidance](https://winlink.org/content/password_problems).
The file contains a recoverable plaintext password, which this legacy algorithm
requires; do not use a real Winlink password or reuse any production secret.
The challenge uses MD5 and an eight-digit response, is vulnerable to offline
password guessing and challenge collisions, has no rate limiting, and does not
encrypt traffic or authenticate the server. It is for interoperability research,
not production authentication. Do not use actual private mail or shared-host access.
There is no SMTP, forwarding, resume, or production mailbox integration.
The service allows one active lab session per account; its 120-second lease
expires after inactivity or an interrupted process. A stale session cannot
acknowledge messages after another session takes over.

To test with a local kissterm checkout (no network required):

```text
FCMS_KISSTERM_PATH=../kissterm PYTHONPATH=src python -m unittest tests.test_b2f -v
```

For controlled tests, `CMSCore(store).lab_mailbox()` returns a service whose
trusted in-process `open_session(account)` context supports
`store_message(session, mid, raw, 'out')` for seeding synthetic outbound B2
bytes and `get(session, mid, 'in')` for received bytes; always call
`close_session(session)` afterward. Close a seeding session before connecting
the B2F client. `open_session` itself does not authenticate the caller: the
adapter checks its configured test account and challenge before invoking it.
It is not an authorization boundary against other local code or disk access.
The lease is renewed only during mailbox operations, not while bytes are
transferring. A transfer lasting more than 120 seconds can lose its lease and
remain pending for retry. This mechanism is not ready for general RF service.

`--database` accepts an operator-selected SQLite path; the separate lab tables
do not enforce physical database or filesystem isolation. Use a dedicated
test database and protect it from other local users. The lab service's `ack`
checks the session and account, but does not issue a per-message claim token;
the B2F adapter constrains acknowledgements to its own protocol sequence.

Older `b2f_lab_messages` rows remain in their original table but are not
automatically imported into the new shared lab mailbox. The old lab database
is retained unchanged; use a fresh database for this experiment.
An offline synthetic bidirectional exchange also passes with Pat commit
`2ad31e8fc1f1748ac1b00cac9d88da54e3e0efe2`, using a test-only localhost shim
for Pat's Telnet callsign/password prompts. The B2F lab endpoint itself does
not implement those Telnet prompts. Secure mailbox authorization remains open.
On the wire, an already stored MID is rejected before its content is sent;
the server therefore cannot detect a different body offered under that MID.
The byte-for-byte collision check applies only when storing a body locally.
CI exercises the roundtrip against kissterm commit
`f3310c71efa8e3e23263ed25745a9c365a9efc74`.

To run the Pat test with a separately built binary, set `FCMS_PAT_BINARY` to
its absolute path and run `python -m unittest tests.test_pat_interop -v`. It
creates a temporary configuration and mailbox, disables version reporting,
pre-populates Pat's account-confirmation cache, blocks external HTTP via a
local closed proxy, and uses only a loopback test connection. Use only the
pinned Pat version: other versions may have different startup behavior.

Winlink Express and compatible clients can answer a B2F secure-login challenge
using their configured password; see Winlink's
[data-flow document](https://winlink.org/sites/default/files/downloads/winlink_data_flow_and_data_packaging.pdf).
The separate `lab-only` Telnet prompt password in the Pat test is a fixture and
provides no mailbox authorization; B2F challenge verification is separate.

The LZHUF codec is copied from kissterm commit `f3310c71efa8e3e23263ed25745a9c365a9efc74`
(Copyright 2026 Brad Brown Jr, MIT license; see the repository LICENSE), with
its own source and algorithm attribution in `lzhuf.py`.

For Linux inetd mode, use the same database path visible to the inetd account:

```text
python -m federated_cms.bpq --stdio --database /var/lib/fcms/fcms.sqlite3
```

Illustrative `/etc/services` and `/etc/inetd.conf` entries:

```text
fcms  63190/tcp
fcms stream tcp nowait bpq /path/to/.venv/bin/python -m federated_cms.bpq --stdio --database /var/lib/fcms/fcms.sqlite3
```

Use the exact inetd syntax for the installed implementation and ensure its user
can create the database directory. No listener is opened by `--stdio`.

For an illustrative BPQ configuration, add an unused local port and calculate
the correct `HOST` index from the full existing `CMDPORT` list:

```text
CMDPORT ... 63190
APPLICATION 24,FCMS,C 9 HOST 19 K S
```

`S` returns the user to the node; it does not request a callsign. Do not use
`NOCALL` if a BPQ callsign first line is wanted. On Windows start the standalone
listener before BPQ32; do not configure inetd. A dedicated application SSID is
optional and must be an unused local SSID in the BPQ configuration.

## Internal application API

`CMSCore` is the shared boundary for terminal and future B2F adapters:

- `submit_weather(grid, owner_scope, idempotency_key=...)` durably accepts work.
- `process_pending()` dispatches local service work.
- `request()` and `jobs()` enforce the stored ownership scope.
- `store_local_message()` and `local_mailbox()` are the internal mailbox seam;
  terminal mailbox reading is intentionally not exposed before authentication.

The SQLite boundary makes its own atomic transactions. The internal records are
not all request-envelope JSON; a future B2F adapter maps protocol operations to
this API, while its binary-clean listener remains separate from BPQ terminal I/O.

For an explicitly operator-controlled experiment with TCP KISS and synthetic
lab mail, see [TCP-KISS-B2F-LAB.md](TCP-KISS-B2F-LAB.md). This does not make
the B2F laboratory or its legacy authentication production-ready.

An apparent BPQ callsign is recorded only as session display context. A random
session scope limits `JOBS` to work created within that session; a new
connection cannot enumerate another session's work by claiming its callsign.
Only explicitly public weather receipts are retrievable by request ID.
