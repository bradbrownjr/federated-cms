# Development and BPQ bridge setup

This is an M1 research prototype. It has a local SQLite store, a deterministic
weather service, and an ASCII terminal adapter. It does not implement federation
or B2F.

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

An apparent BPQ callsign is recorded only as session display context. A random
session scope limits `JOBS` to work created within that session; a new
connection cannot enumerate another session's work by claiming its callsign.
Only explicitly public weather receipts are retrievable by request ID.
