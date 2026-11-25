# INQUIRY robot, slice 1: contract

**Status:** contract for implementation. Nothing here is implemented yet.

This is the exact behaviour of slice 1 of the robot designed in
`docs/INQUIRY-ROBOT.md` (PR #24): the queue hook, the worker with leases,
the reply builder, `HELP`, `LIST`, static catalog items, the
rejected-lines reply, loop rules, per-account limits without notices, and
the configuration with refusal to start. Where this file and
INQUIRY-ROBOT.md differ, this file wins for slice 1; each difference is
listed under [Decisions](#decisions).

The tests are the contract in executable form. Do not change them to make
an implementation pass.

| Test file | Covers |
| --- | --- |
| `tests/test_robot_config.py` | `load_config`, catalog TSV, every refusal reason, the `check` and `run` commands |
| `tests/test_robot.py` | hook, worker, leases, crash points, replies, MIDs, loop rules, limits, hostile input |
| `tests/test_robot_b2f.py` | a request and its reply over the loopback B2F lab server; an unreadable message does not break the session |
| `tests/test_b2_message_headers.py` | `b2_message.message_headers`, and `message_mid`/`message_attachments` unchanged |
| `tests/robot_helpers.py` | fake clock, message builder, fixture paths (test code, not contract) |
| `tests/fixtures/robot/` | configuration fixtures |

Run: `PYTHONPATH=src python -m pytest tests/test_robot_config.py tests/test_robot.py tests/test_robot_b2f.py tests/test_b2_message_headers.py`
or `python -m unittest discover -v`.

## Files

The implementation creates or edits these four source files and no
others:

| File | Change |
| --- | --- |
| `src/federated_cms/b2_message.py` | edit: add the public `message_headers` |
| `src/federated_cms/robot.py` | new: schema, hook functions, worker, reply builder, `HELP`, `LIST`, static items, `main` |
| `src/federated_cms/robot_config.py` | new: `load_config`, catalog TSV loading, validation |
| `src/federated_cms/lab_mailbox.py` | edit: create the robot schema, call the hook in `store_message` |

`lab_mailbox.py` imports the module (`from . import robot as _robot`) and
calls `_robot.enqueue_inbound(...)` and `_robot.initialize_schema(...)`
through it, so tests can patch `federated_cms.robot.enqueue_inbound`.
`robot.py` must not import `lab_mailbox` at module level.

Standard library only (`tomllib`, `sqlite3`, `hashlib`, `base64`,
`secrets`, `ipaddress`, `logging`, `argparse`). Python 3.11 or later.

## Time and text formats

- The worker's clock is the `clock` callable: it returns a timezone-aware
  UTC `datetime`. The hook's clock is `time.time()` inside `lab_mailbox`
  (the same call that renews the mailbox session), converted with
  `datetime.fromtimestamp(t, timezone.utc)`.
- Every timestamp stored in a robot table is text in the fixed-width form
  `%Y-%m-%dT%H:%M:%S.%fZ` (`2026-11-25T04:33:00.000000Z`), so text order is
  time order.
- "Today" is the UTC calendar day of the worker clock at the moment of the
  check; its start is `YYYY-MM-DDT00:00:00.000000Z`.
- Printable ASCII means U+0020 to U+007E.

## Schema

`initialize_schema(con)` runs this script (all `IF NOT EXISTS`) and inserts
no rows. `LabMailboxService.__init__` calls it with the connection it uses
for its own tables.

```sql
CREATE TABLE IF NOT EXISTS robot_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS robot_inbox (
    account TEXT NOT NULL, mid TEXT NOT NULL,
    addresses TEXT NOT NULL,
    received_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued','processing','done','failed','ignored')),
    reason TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL,
    lease_token TEXT, lease_until TEXT, finished_at TEXT,
    PRIMARY KEY (account, mid)
);
CREATE INDEX IF NOT EXISTS robot_inbox_ready ON robot_inbox(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS robot_inbox_account_time ON robot_inbox(account, received_at);
CREATE TABLE IF NOT EXISTS robot_items (
    id INTEGER PRIMARY KEY,
    account TEXT NOT NULL, mid TEXT NOT NULL, seq INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('list','help','catalog','url','command','choice','more')),
    request_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('ready','processing','waiting','done','failed','limited')),
    steps INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    deadline TEXT NOT NULL,
    lease_token TEXT, lease_until TEXT,
    error TEXT, created_at TEXT NOT NULL, finished_at TEXT,
    UNIQUE (account, mid, seq),
    FOREIGN KEY (account, mid) REFERENCES robot_inbox(account, mid)
);
CREATE INDEX IF NOT EXISTS robot_items_state ON robot_items(state, deadline);
CREATE TABLE IF NOT EXISTS robot_replies (
    account TEXT NOT NULL, reply_mid TEXT NOT NULL,
    item_id INTEGER,
    kind TEXT NOT NULL CHECK (kind IN ('content','menu','error','notice')),
    body_bytes INTEGER NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (account, reply_mid)
);
CREATE INDEX IF NOT EXISTS robot_replies_account_time ON robot_replies(account, created_at);
CREATE INDEX IF NOT EXISTS robot_replies_mid ON robot_replies(reply_mid);
CREATE TABLE IF NOT EXISTS robot_counters (
    day TEXT NOT NULL, name TEXT NOT NULL, value INTEGER NOT NULL,
    PRIMARY KEY (day, name)
);
```

`robot_meta` rows: `mid_salt` (created by `Robot.__init__`, see
[Reply MIDs](#reply-mids)). Tables for menus, positions, notices and the
fetch cache are not created in slice 1.

## Hook: queueing accepted mail

```python
ROBOT_ADDRESSES = frozenset({"INQUIRY", "QTH", "SERVICE"})
def classify(raw: bytes) -> frozenset[str]: ...
def robot_addresses(raw: bytes) -> frozenset[str]: ...
def initialize_schema(con: sqlite3.Connection) -> None: ...
def enqueue_inbound(con: sqlite3.Connection, account: str, mid: str, raw: bytes,
                    received_at: datetime) -> bool: ...
```

**`classify(raw)`** reads the message headers (see
[Reading a message](#reading-a-message)) and returns the members of
`ROBOT_ADDRESSES` that are the local part of any `To:` or `Cc:` address.
A header value may hold several addresses separated by `,` or `;`. Each
part has spaces and tabs removed from both ends; a part that is then empty
or holds any character outside printable ASCII is skipped (so
`To: INQUIRY\xa0` and `To: N0CALL\rTo: INQUIRY` name no robot address).
Other parts go to `inquiry.address_local_part`; a part it rejects is
skipped. `To: N0CALL INQUIRY` is one address with local part
`N0CALL INQUIRY`. `classify` raises `B2MessageError` when `message_headers`
does.

**`robot_addresses(raw)`** is `classify(raw)`, except that it never raises:
any exception, including for a `raw` that is not `bytes`, gives
`frozenset()`.

**`enqueue_inbound(con, account, mid, raw, received_at)`** calls
`classify(raw)`, looked up as a module global at call time so tests can
patch `federated_cms.robot.classify`.

- `classify` raised any `Exception`: the message content cannot block its
  own storage. Insert, with `INSERT OR IGNORE`, a row with
  `addresses=''`, `state='ignored'`, `reason='unreadable'`, `attempts=0`,
  `next_attempt_at = finished_at = received_at`; log `event=unreadable`;
  return `False`.
- The set is empty: write nothing, return `False`.
- Otherwise run

```sql
INSERT OR IGNORE INTO robot_inbox (account, mid, addresses, received_at, state, attempts, next_attempt_at)
VALUES (?, ?, ?, ?, 'queued', 0, ?)
```

  with `addresses` the sorted names joined by `,` (`INQUIRY,QTH`) and
  `next_attempt_at = received_at`, and return whether a row was inserted.

It does not open, commit or roll back a transaction. Only exceptions from
its SQL statements leave it, the same as any storage failure.

**In `LabMailboxService.store_message`**, inside the existing
`BEGIN IMMEDIATE` transaction, after the new row is inserted and before
`COMMIT`, when `direction == "in"`: call
`_robot.enqueue_inbound(con, account, mid, raw, received_at)`. Nothing is
called for `out`, or when the row already existed (duplicate). A
database error in the hook leaves the whole transaction uncommitted, so
neither the message nor the queue row is stored, and comes out as
`StoreUnavailable`, as today. Nothing about a message's content makes the
hook raise, so a bad message cannot fail the B2F session or hold up the
client's outbox. Only `store_message`, which needs a
session opened after B2F login, queues anything (loop rule 1).

## Reading a message

```python
# federated_cms.b2_message
def message_headers(raw: bytes) -> tuple[tuple[str, bytes], ...]
```

`message_headers` returns the headers in order as `(name, value)`, built
by the same loop `_message_sections` uses: the name lower-cased and
decoded as ASCII, the value `bytes.strip()`-ed, a line starting with space
or tab appended to the previous value after one space (`value + b" " +
line.strip()`). It raises `B2MessageError` exactly when `message_mid`
would, for the same input. `message_mid` and `message_attachments` keep
their behaviour.

The robot reads headers only through `message_headers` and decodes each
value as ISO-8859-1 (never fails), with no further stripping. Lines are
separated by `\r\n` only: a lone `\r`, `\n` or `\x85` is part of the
value. When a header appears more than once, `Subject` and `From` use the
first, as `email.message.Message.get` does; a later copy cannot override
what the client's header block put first. `To`, `Cc` and `Auto-Submitted`
use all copies. A missing `Subject` is `""`. A `From` value holding a
character outside printable ASCII counts as unparsable.

`federated_cms.menu_reply` reads headers with the `email` package, which
parses display names (`To: Bob <SERVICE@x>` is `SERVICE` there, and no
robot address here). Slice 4 must reconcile the two readers before
`SERVICE` mail is processed.

The body is the first `Body:` bytes after the blank line. It is decoded as
strict UTF-8; if that fails, as strict `cp1252`; if that fails, the
message is a message-level error with reason `invalid body`. Attachments
are ignored.

## Worker

```python
class Robot:
    def __init__(self, store: SQLiteStore, config: RobotConfig, *,
                 clock: Callable[[], datetime], lease_seconds: int = 60,
                 fault: Callable[[str], None] | None = None) -> None: ...
    def run_once(self) -> bool: ...
    def run_until_idle(self, max_steps: int = 100_000) -> int: ...
```

`__init__` creates the schema (through `initialize_schema`), then
`INSERT OR IGNORE INTO robot_meta VALUES ('mid_salt', secrets.token_hex(16))`,
then reads `mid_salt` back and keeps it for the life of the object. A salt
already present is never replaced. Database errors raise `StoreUnavailable`.

`run_once` does at most one unit of work and returns `True` when it
claimed a row (whether or not its later commit succeeded), else `False`:

1. Claim an inbox row. If one was claimed, dispatch it and return `True`.
2. Otherwise claim an item. If one was claimed, process it and return
   `True`.
3. Otherwise return `False`.

`run_until_idle` calls `run_once` until it returns `False` or `max_steps`
calls have been made, and returns the number of calls that returned
`True`.

Each claim and each commit is one short `BEGIN IMMEDIATE` transaction on a
new connection from `store._connect()`. `sqlite3.Error` is raised as
`StoreUnavailable`. No transaction is open while replies are built.

### Fault points

`fault`, when given, is called with a point name at each point below. It
exists so tests can simulate a crash or pause a worker. The robot never
catches an exception that is not a subclass of `Exception`; tests raise a
`BaseException` subclass to simulate the process dying, and it must
propagate out of `run_once` unchanged with any open transaction rolled
back. An `Exception` raised from `fault` is handled only at `item.build`
(see below); at every other point it also propagates.

| Point | When |
| --- | --- |
| `claim_inbox.before` | every `run_once`, before the inbox claim transaction opens |
| `claim_inbox.inside` | a row was claimed: after its `UPDATE`, before `COMMIT` |
| `claim_inbox.after` | a row was claimed: after `COMMIT` |
| `dispatch.before` | after parsing and building the message-level reply, before the dispatch transaction opens |
| `dispatch.inside` | the lease check passed: after all dispatch writes, before `COMMIT` |
| `dispatch.after` | the lease check passed: after the dispatch `COMMIT` |
| `claim_item.before` | no inbox row was claimed: before the item claim transaction opens |
| `claim_item.inside` | an item was claimed: after its `UPDATE`, before `COMMIT` |
| `claim_item.after` | an item was claimed: after `COMMIT` |
| `item.build` | before the item's reply is built (no transaction open) |
| `item.before` | after the reply is built, before the item transaction opens |
| `item.inside` | the lease check passed: after all item writes, before `COMMIT` |
| `item.after` | the lease check passed: after the item `COMMIT` |

`.before` points have no transaction open, so a test may run a second
`Robot` on the same database from inside the callback.

### Leases

- **Claim inbox.** A row is claimable when `state='queued' AND
  next_attempt_at <= now`, or `state='processing' AND lease_until <= now`.
  The oldest by `received_at`, then `rowid`, is taken. If its `attempts`
  is already 3, it is set to `state='failed'`, `reason='attempts'`,
  `finished_at=now`, lease cleared, and `run_once` returns `True` with no
  dispatch and no fault points after `claim_inbox.before`. Otherwise it is
  set to `state='processing'`, `attempts=attempts+1`, a new
  `lease_token = secrets.token_hex(16)`, `lease_until = now +
  lease_seconds`.
- **Claim item.** An item is claimable when `state='ready'`, or
  `state='processing' AND lease_until <= now`; lowest `id` first. The same
  `attempts` rule applies: at 3 the item becomes `state='failed'`,
  `error='attempts'`, `finished_at=now`, with no reply and no fault points
  after `claim_item.before`.
- **Commit.** The dispatch and item transactions first check that the row
  still has `state='processing'`, the worker's `lease_token`, and
  `lease_until > now` (clock read when the transaction opens). If not,
  they write nothing, roll back, and `run_once` returns `True`. A worker
  whose lease expired therefore cannot write replies, items or states.

### Dispatch

Before the transaction: read the message from the account's `in` row in
`shared_lab_mailbox_messages`, parse it, and build the message-level reply
if there is one. Inside the transaction, after the lease check, the first
matching rule decides:

| Order | Condition | Result (`robot_inbox.state`, `reason`) |
| --- | --- | --- |
| 0 | no `in` row with this account and MID in `shared_lab_mailbox_messages` | `failed`, `missing` |
| 1 | account or `From:` local part is a loop name | `ignored`, `loop_name` |
| 2 | inbound MID is a `reply_mid` in `robot_replies` (any account) | `ignored`, `loop_mid` |
| 3 | any `Auto-Submitted:` value other than `no` | `ignored`, `auto_submitted` |
| 4 | account is in `robot.blocked` | `ignored`, `blocked` |
| 5 | `INQUIRY` not in `addresses` | `ignored`, `unsupported` |
| 6 | more than `requests_per_hour` requests | `ignored`, `limited` |
| 7 | at least `undelivered_max` undelivered `out` rows | `ignored`, `limited` |
| 8 | message-level error, invalid limit reached | `ignored`, `invalid_limit` |
| 9 | message-level error | `done`, `parse_error` (reply if it fits) |
| 10 | otherwise | `done`, `NULL` (items, rejected-lines reply if it fits) |

Every result sets `finished_at=now` and clears the lease. `ignored`
messages get no reply and create no items.

- **Loop names:** `SERVICE`, `INQUIRY`, `QTH`, `SYSTEM`, `MAILER-DAEMON`,
  `POSTMASTER` and `node.name`, compared exactly with the account and with
  `address_local_part(From)` (so `N1TEST-10` matches the node
  `N1TEST-10`; `N1TEST` does not).
- **Auto-Submitted:** the value up to the first `;`, with spaces and tabs
  removed from both ends, compared
  case-insensitively with `no`. An empty value is not `no`.
- **From mismatch:** when the first `From:` is missing, unparsable, or its
  local part differs from the account, add 1 to `robot_counters` row
  `(today, 'from_mismatch')` in the dispatch transaction, for any result
  except `missing`.
  Nothing else changes: replies always go to the account.
- **Requests per hour:** count `robot_inbox` rows of the account, in any
  state, with `received_at` greater than this row's `received_at` minus
  3600 s and either earlier than it, or equal to it with `rowid` not above
  this row's. More than the account's `requests_per_hour` is rule 6.
- **Undelivered:** count `shared_lab_mailbox_messages` rows with the
  account, `direction='out'`, `delivered=0`.
- **Message-level error:** `parse_inquiry` raised `InquiryError`, or the
  body could not be decoded (reason `invalid body`). The reason is the
  exception's message: `invalid subject`, `empty request`,
  `body too large`, `invalid body`.
- **Invalid limit:** count `robot_inbox` rows of the account with
  `reason='parse_error'` and `finished_at` today. At least
  `invalid_per_day` is rule 8.
- **Items** (rule 10): `parse_inquiry(subject, body, services=frozenset(configured verbs) | {"HELP"})`.
  Each item gets a row with `seq` 1, 2, ... in parser order, inserted with
  `INSERT OR IGNORE`, `request_json = json.dumps(dataclasses.asdict(item), sort_keys=True)`,
  `deadline = created_at = now`, `state='ready'`; items with `seq` above
  the account's `items_per_request` are inserted with `state='limited'`.
  `kind`: parser kinds `list` and `update_catalog` are `list`; service
  `HELP` is `help`; `catalog` and `url` keep their names; any other
  service verb is `command`.
- **Message-level replies** (rules 9 and 10): the message error reply or,
  when `rejected` is not empty, the rejected-lines reply. `seq` 0, part 1,
  `item_id` NULL, kind `error`. It is deposited only if it fits (see
  [Limits](#limits)); otherwise it is dropped and the row's result is
  unchanged.

### Processing an item

Call `fault("item.build")`, then build the reply from the table in
[Replies](#replies). If `fault` or the builder raises an `Exception`, the
item instead gets the internal error reply and ends `failed` with
`error='internal_error'`; the exception's class name, not its message, is
logged.

Inside the transaction, after the lease check: if the reply fits, deposit
it, insert its `robot_replies` row (`item_id` = item id), and set the item
`done` (or `failed` for the internal error reply). If it does not fit, set
the item `state='limited'` and deposit nothing. Set `finished_at=now`,
clear the lease.

### Depositing a reply

In the same transaction as the state change:

```sql
INSERT OR IGNORE INTO shared_lab_mailbox_messages (account, mid, direction, raw) VALUES (?, ?, 'out', ?);
INSERT OR IGNORE INTO robot_replies (account, reply_mid, item_id, kind, body_bytes, created_at) VALUES (?, ?, ?, ?, ?, ?);
```

`body_bytes` is the `Body:` value. A replayed reply with the same MID keeps
the first copy. Replies go only to the B2F account's mailbox on this node,
never to the `From:` address and never by any other route.

## Limits

Per account: `config.limits_for(account)`. Slice 1 enforces these and
sends no notices:

| Key | Where | Rule |
| --- | --- | --- |
| `items_per_request` | dispatch | items past this `seq` are `limited` |
| `requests_per_hour` | dispatch | rule 6 |
| `undelivered_max` | dispatch | rule 7 |
| `invalid_per_day` | dispatch | rule 8 |
| `messages_per_day` | every deposit | fits only if `n + 1 <= messages_per_day` |
| `bytes_per_day` | every deposit | fits only if `b + body_bytes <= bytes_per_day` |

`n` and `b` are the count and `SUM(body_bytes)` of the account's
`robot_replies` rows with `kind != 'notice'` and `created_at` today. A
reply fits when both rules hold. `parts_per_item`, `node_fetches_per_hour`
and `node_bytes_per_day` are validated and stored but not used.

## Replies

### Reply MIDs

```python
def reply_mid(salt: str, account: str, inbound_mid: str, seq: int, part: int) -> str
```

`base64.b32encode(hashlib.sha256(f"{salt}|{account}|{inbound_mid}|{seq}|{part}".encode("ascii")).digest()).decode("ascii")[:12]`.
Twelve characters from `A-Z2-7`. Item replies use the item's `seq` and
part 1. The message-level reply uses `seq` 0, part 1. (`seq` 0, part 0 is
kept for the limit notice of a later slice.)

### Raw message

```python
class ReplyError(ValueError): ...
def build_reply(*, mid: str, account: str, node_name: str, subject: str,
                body: str, now: datetime) -> bytes
```

Returns, with every `\n` in `body` turned into `\r\n`:

```text
Mid: <mid>\r\n
Date: <now as %Y/%m/%d %H:%M, UTC>\r\n
Type: Private\r\n
From: SERVICE\r\n
To: <account>\r\n
Subject: <subject>\r\n
Mbo: <node_name>\r\n
Body: <byte count of the converted body>\r\n
\r\n
<converted body>
```

Nothing follows the body. Raises `ReplyError` when: `mid` does not match
`[A-Z0-9]{1,12}`; `account` or `node_name` does not match
`[A-Z0-9]{1,6}(-[0-9]{1,2})?`; `subject` is longer than 80 characters or
has a character that is not printable ASCII; `body` has a character other
than printable ASCII and `\n`, or contains `\r`; `now` is naive; or
`b2_message.message_mid` rejects the result or returns another MID.

### Subjects

```python
def make_subject(label: str, suffix: str = "") -> str
```

Each character of `label` that is not printable ASCII becomes `?`. The
subject is `INQUIRY - <label>`, plus `: <suffix>` when `suffix` is not
empty. When that is over 80 characters, `label` is cut to
`80 - 10 - len(": " + suffix if suffix else "") - 3` characters and `...`
is added, so the subject is exactly 80 characters; the suffix is never
cut. The `LIST` reply subject is the fixed `INQUIRY: LIST`.

An item's label: `LIST` for `list`; the verb, plus a space and the
arguments when there are any, for a service item (the choice is not
shown); the value for `catalog` and `url`.

### Bodies

Every body is `content` lines, then an empty line, then `via <node.name>`,
each line ending in `\n`:

```python
body = "\n".join(content_lines + ["", f"via {node_name}"]) + "\n"
```

`HELP_LINES` (with configured services, one line `f"{verb:<13}{description}"`
each, sorted by verb, after the `LIST` line; none exist in slice 1):

```text
Send requests to INQUIRY with subject REQUEST, one per line.

HELP         this list
HELP <verb>  about one service
LIST         the catalog of items on this node
<ID>         an item from the catalog
```

| Reply | Subject | `content_lines` | `robot_replies.kind` |
| --- | --- | --- | --- |
| `HELP` (no arguments; any choice ignored) | `INQUIRY - HELP` | `HELP_LINES` | `content` |
| `HELP <a>`, `a` upper-cased is `HELP` | `make_subject("HELP " + a)` | `HELP lists the requests this node answers. HELP <verb> explains one.` | `content` |
| `HELP <a>`, `a` upper-cased is `LIST` | `make_subject("HELP " + a)` | `LIST returns the catalog of items on this node. Send any ID from it to get that item.` | `content` |
| `HELP <a>`, anything else | `make_subject("HELP " + a, "not found")` | `No service <a> on this node.`, `""`, `HELP_LINES` | `error` |
| `LIST` or `UPDATE_CAT` | `INQUIRY: LIST` | see below | `content` |
| catalog ID found | `make_subject(ID)` | the item's text, split into lines | `content` |
| catalog ID not found | `make_subject(ID, "not found")` | `No item <ID> on this node. Send LIST for the catalog.` | `error` |
| URL | `make_subject(url, "not available")` | `This node does not fetch web pages.` | `error` |
| internal error | `make_subject(label, "error")` | `Could not process this item (ref <item id>).` | `error` |
| message error | `make_subject("REQUEST", reason)` | `Your request could not be read: <reason>.`, `""`, `HELP_LINES` | `error` |
| rejected lines | `make_subject("REQUEST", "lines not understood")` | see below | `error` |

`<a>` is the item's `arguments` as parsed (case kept).

**`LIST` content:**

```text
Catalog of <node.name>. Send an ID to INQUIRY with subject REQUEST.
```

then, with no entries, `""` and `No items.`; otherwise entries sorted by
`(category, id)`, and for each category `""`, the category name, then one
line `f"{id}  {description}"` (two spaces) per entry. The content text
(`"\n".join(content_lines) + "\n"`) may be at most 12,000 characters. When
all entries do not fit, the largest number `k` of leading entries is shown
for which the content, with the lines `""` and
`Items not shown: <n - k>.` appended, fits; a category
heading appears only with at least one of its entries.

**Rejected-lines content:** `These lines were not understood:`, then for
each of the first 20 rejected lines `Line <line_number>: <text> (<reason>)`
where every character of `text` that is not printable ASCII (tab included)
becomes `?`; then, when there were more than 20,
`...and <count - 20> more.`; then `""` and `HELP_LINES`.

**Static item text:** the file's text as loaded (see
[Catalog](#catalog)), already ending in `\n`; its lines are the content.

## Configuration

```python
class ConfigError(ValueError):
    problems: tuple[str, ...]          # str(error) is "\n".join(problems)

def load_config(path: str | Path) -> RobotConfig

@dataclass(frozen=True)
class Limits:
    items_per_request: int = 32
    requests_per_hour: int = 10
    messages_per_day: int = 100
    bytes_per_day: int = 1_000_000
    parts_per_item: int = 12
    invalid_per_day: int = 5
    undelivered_max: int = 100

@dataclass(frozen=True)
class CatalogEntry:
    id: str; category: str; target: str; description: str
    text: str                          # loaded file text, "\n" line ends, ends with "\n"

@dataclass(frozen=True)
class ServiceConfig:
    verb: str; handler: str; description: str; options: Mapping[str, object]

@dataclass(frozen=True)
class RobotConfig:
    config_dir: Path                   # the config file's directory, resolved
    node_name: str
    contact: str | None
    reply_from: str                    # "SERVICE"
    menu_expiry_days: int              # default 7
    max_wait_minutes: int              # default 360
    same_session_wait_seconds: int     # default 2
    url_items: bool                    # default True
    deny_networks: tuple[str, ...]     # default ()
    blocked: frozenset[str]            # default frozenset()
    limits: Limits                     # [limits] over the defaults
    account_limits: Mapping[str, Limits]   # [limits.accounts.X] over `limits`
    node_fetches_per_hour: int         # default 600
    node_bytes_per_day: int            # default 5_000_000
    catalog: tuple[CatalogEntry, ...]  # file order
    services: tuple[ServiceConfig, ...]
    def limits_for(self, account: str) -> Limits: ...   # account_limits.get(account, limits)
```

All in `federated_cms.robot_config`; `robot.py` may re-export them.
`load_config` reads the file with `tomllib` and collects every problem
before raising one `ConfigError`. Problems are strings
`<path>: <reason>`. Tests compare the set of problems, not their order. A
key or table name shown in a problem has each non-printable-ASCII
character replaced with `?`.

### Schema

| Key | Type | Default | Rule |
| --- | --- | --- | --- |
| `node.name` | string | required | `[A-Z0-9]{1,6}(-[0-9]{1,2})?` |
| `node.contact` | string | none; required when `url_items` is true | at most 128 characters, fullmatch `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}` or `https?://[!-~]+` |
| `node.reply_from` | string | `SERVICE` | must be `SERVICE` |
| `robot.menu_expiry_days` | integer | 7 | 1 to 90 |
| `robot.max_wait_minutes` | integer | 360 | 1 to 10080 |
| `robot.same_session_wait_seconds` | integer | 2 | 0 to 10 |
| `robot.url_items` | boolean | true | |
| `robot.deny_networks` | list of strings | `[]` | each `ipaddress.ip_network(value, strict=True)` |
| `robot.blocked` | list of strings | `[]` | each `[A-Z0-9]{1,6}(-[0-9]{1,2})?` |
| `limits.items_per_request` | integer | 32 | 1 to 32 |
| `limits.parts_per_item` | integer | 12 | 1 to 50 |
| `limits.requests_per_hour`, `messages_per_day`, `bytes_per_day`, `invalid_per_day`, `undelivered_max` | integer | table above | 1 to 1,000,000,000 |
| `limits.node_fetches_per_hour`, `limits.node_bytes_per_day` | integer | 600, 5,000,000 | 1 to 1,000,000,000 |
| `limits.accounts.<CALL>` | table | | `<CALL>` matches the account pattern; keys are the seven per-account keys only, same rules |
| `catalog.file` | string | no catalog | path relative to the config file's directory |
| `service` | array of tables | `[]` | keys `verb`, `handler`, `description` (required), `options` (table, default `{}`) |
| `service[i].verb` | string | | `[A-Z]{2,10}`, not `LIST`, `HELP`, `MORE`, `UPDATE_CAT`, unique |
| `service[i].handler` | string | | a key of `HANDLERS`, which is `{}` in slice 1 |
| `service[i].description` | string | | 1 to 60 printable ASCII characters |

An "integer" is an `int` that is not a `bool`; any size is accepted from
TOML and then range-checked. Every other top-level key, and every other key
in these tables, is unknown. A table of the wrong type gives only its
`must be a table` problem; the keys it should hold are not reported as
`required`. A table of the wrong type
gives only its `must be a table` problem; the keys it should hold are not
reported as `required`.

### Refusal reasons

| Problem | When |
| --- | --- |
| `config: cannot read` | the file is missing or unreadable |
| `config: too large` | the file is over 1,000,000 bytes (exactly 1,000,000 is read) |
| `config: invalid TOML` | `tomllib` rejects it (no other problems are reported) |
| `<key>: unknown key` | an unknown key or table, at any level, for example `robot.menu_expiry_day: unknown key`, `nodes: unknown key`, `limits.accounts.N0EOC.node_bytes_per_day: unknown key` |
| `<path>: must be a table` | `node`, `robot`, `limits`, `catalog`, `limits.accounts`, `limits.accounts.<CALL>`, `service[i].options` not a table |
| `service: must be a list of tables` | `service` is not an array of tables |
| `<path>: required` | `node.name`; `node.contact` when `url_items` is true; `catalog.file` when `[catalog]` exists; `service[i].verb`, `.handler`, `.description` |
| `<path>: must be a string` | a string key with another type |
| `<path>: must be an integer` | an integer key with another type, `bool` included |
| `<path>: must be a boolean` | `robot.url_items` not a `bool` |
| `<path>: must be a list of strings` | `deny_networks` or `blocked` not a list, or with a non-string element |
| `<path>: out of range` | an integer outside its bounds |
| `<path>: invalid value` | `node.name`, `node.contact`, `node.reply_from`, `service[i].verb`, `service[i].description` failing their rule; `robot.deny_networks[i]` or `robot.blocked[i]` failing (index in the path); `limits.accounts.<CALL>` with a bad callsign |
| `service[i].verb: reserved` | `LIST`, `HELP`, `MORE` (`UPDATE_CAT` already fails the pattern) |
| `service[i].verb: duplicate` | the same verb as an earlier service |
| `service[i].handler: unknown handler` | not in `HANDLERS` |
| `catalog.file: outside config directory` | the resolved path is not inside the resolved config directory |
| `catalog.file: not found` | not a readable regular file |
| `catalog.file: too large` | over 2,000,000 bytes (rows are then not read) |
| `catalog: too many entries` | more than 10,000 entry rows (lines that, after removing one trailing `\r`, are not empty and do not start with `#`); rows are then not checked |
| `catalog:<n>: <reason>` | a bad row; `n` is the 1-based line number; see below |

### Catalog

A text file of lines separated by `\n`; one trailing `\r` per line is
removed. A byte order mark is three bytes above 0x7F, so it gives
`catalog:1: not ASCII`. `text:` paths resolve against the config file's
directory, not the catalog file's. Lines that are empty or start with `#` are skipped. Every other
line is an entry with exactly four fields separated by tabs:
`ID`, `CATEGORY`, `TARGET`, `DESCRIPTION`. Fields are not stripped. Each
row gets at most one problem, the first that applies:

| Reason | When |
| --- | --- |
| `not ASCII` | the line has a byte above 0x7F |
| `invalid characters` | the line has a control character other than tab |
| `wrong number of fields` | not exactly four tab-separated fields |
| `invalid ID` | ID does not fullmatch `[A-Z0-9][A-Z0-9_.-]{0,63}` |
| `reserved ID` | ID is `LIST`, `UPDATE_CAT`, `HELP`, `MORE`, or any `service[i].verb` that is a string matching `[A-Z]{2,10}` |
| `duplicate ID` | ID already used by an earlier row |
| `invalid category` | not `[A-Z0-9_]{1,32}` |
| `invalid description` | empty, or over 80 characters |
| `unsupported target` | starts with `url:` or `cmd:` |
| `invalid target` | does not start with `text:`, or the path after it is empty |
| `text file outside config directory` | the resolved path is not inside the resolved config directory (`..`, absolute paths, symbolic links) |
| `text file missing` | not a regular file |
| `text file too large` | over 12,000 bytes |
| `text file not ASCII` | a byte above 0x7F |
| `text file has control characters` | after turning `\r\n` and then `\r` into `\n`, a character other than printable ASCII and `\n` (tab included) |
| `text file empty` | no characters after that conversion |

Loaded text gets a final `\n` when it lacks one.

## Command line

```python
def main(argv: list[str] | None = None) -> int
```

`python -m federated_cms.robot` calls `sys.exit(main())`.

- `check --config PATH`: load the config. Valid: print `ok` to stdout,
  return 0. Invalid: print each problem on its own line to stderr, return 2.
- `run --config PATH --database PATH [--once]`: load the config first;
  when invalid, print as `check` does and return 2 without opening or
  creating the database. Otherwise open `SQLiteStore(PATH)`, build a
  `Robot` with the real UTC clock, and call `run_until_idle`. With
  `--once`, return 0 after that. Without it, repeat after `time.sleep(1)`
  (called through the `time` module) until `KeyboardInterrupt`, then
  return 0.
- An `Exception` from a pass (a poison message, `StoreUnavailable`) is
  caught, logged as `event=run_error` with its class name, and the loop
  goes on to the next pass, after the same sleep. With `--once`, `run`
  then returns 1.

## Logging

Logger `federated_cms.robot`. Each record's message is space-separated
`key=value` tokens, `event=<name>` first, other keys in any order. These
events are required, with at least these keys; more keys and more events
are allowed:

| Event | Level | When | Keys |
| --- | --- | --- | --- |
| `dispatch` | INFO | dispatch committed | `account`, `mid`, `outcome` (the reason, or the state when the reason is NULL) |
| `item` | INFO | item committed | `account`, `mid`, `item` (id), `kind`, `outcome` (final state) |
| `stale` | INFO | a commit's lease check failed | `account`, `mid`, plus `item` for an item |
| `attempts` | WARNING | a row or item set `failed`/`attempts` | `account`, `mid`, plus `item` for an item |
| `item_error` | WARNING | an item got the internal error reply | `account`, `mid`, `item`, `error_class` |
| `unreadable` | WARNING | `enqueue_inbound` recorded an unreadable message | `account`, `mid`, `error_class` |
| `run_error` | ERROR | `main run` caught an exception | `error_class` |

Subjects, bodies, arguments, URLs, catalog text and exception messages are
never logged; for an exception, its class name is.

## Replies and Auto-Submitted

Replies carry no `Auto-Submitted` header. They go only to the account's
mailbox on this node; revisit when replies can leave the node.

## Left for later slices

Same-session wait; `QTH` (slice 2); cache, fetch worker, URL items and
`url:` targets (3); menus, `SERVICE` replies and `MORE` (4); `WX`,
`ALERTS`, `SOLAR` (5); `NEWS`, `WIKI` and splitting (6); limit notices,
node-wide caps, `parts_per_item`, `status`, the janitor and retention (7);
`cmd:` targets and the handler registry's first entries; the "could not
process your message" reply after 3 failed attempts; reconciling the
`menu_reply` header reader with `message_headers` (4); an `Auto-Submitted`
header on replies once they can leave the node; production
`MailboxService` deposit.

## Decisions

Made for this contract on 2026-11-25, against INQUIRY-ROBOT.md:

1. **The hook is in `lab_mailbox.py`, not `store.py`.** The acceptance
   transaction is `LabMailboxService.store_message`; `SQLiteStore` has no
   transaction for B2F mail.
2. **All three robot addresses are queued; only `INQUIRY` is processed.**
   A message without `INQUIRY` is `ignored` with reason `unsupported`, so
   `QTH` reports and menu replies received before slices 2 and 4 are not
   answered later.
3. **Headers come from a public `b2_message.message_headers`**, the same
   loop that validates framing, so the robot and the mailbox cannot
   disagree about where a header ends. This makes `b2_message.py` a fourth
   file.
4. **Inbox `done` means dispatched.** Items carry their own state; the
   inbox row is finished when its items exist.
5. **`robot_items` has an `attempts` column**, and both inbox rows and
   items stop at 3 claims with `failed`/`attempts`, so a message that
   crashes the worker every time cannot loop. No reply is sent for it in
   slice 1.
6. **`url:` and `cmd:` catalog targets refuse to start** (`unsupported
   target`) instead of loading and failing later. The handler registry is
   empty, so any `[[service]]` table refuses with `unknown handler`.
7. **URL items get a `not available` reply** whatever `url_items` says;
   `url_items` still decides whether `node.contact` is required.
8. **Limits are enforced without notices.** `robot_notices` is not
   created. Limited items and messages get nothing. Invalid requests are
   counted from `robot_inbox.reason = 'parse_error'`, not from replies.
9. **`LIST` is cut at 12,000 characters** with a count of what was left
   out, instead of being split into parts.
10. **The rejected-lines reply lists at most 20 lines**; a 4,000-line body
    of bad lines gives one reply of bounded size.
11. **Static text files** are limited to 12,000 bytes and to printable
    ASCII and line ends; tabs refuse to start. Line ends are normalised.
12. **`node.reply_from` must be `SERVICE`.** The key stays for later use;
    another value would need its own loop rule.
13. **Footer:** every reply ends with an empty line and `via <node.name>`.
14. **Header values are read as ISO-8859-1**, the first `Subject` and
    `From` win, and an undecodable body is the message error
    `invalid body`.
15. **Message-level replies use `seq` 0**, part 1; part 0 is kept for the
    notice.
16. **`HELP` ignores a choice**, and `HELP <a>` keeps the user's case in
    the subject.
17. **Account override tables must use upper-case callsigns**, so two
    spellings cannot override the same account.
18. **Crash tests use named fault points** passed to `Robot`, because the
    design names the commit boundaries but no way to stop between them.
19. **A queue row whose stored message is missing fails** with reason
    `missing` instead of stopping the worker.
20. **An unreadable message is stored and recorded**, never refused by the
    hook, so it cannot block the client's outbox.
21. **`main run` survives a failing pass**; `--once` reports it with exit
    status 1.

## Open questions

1. Is a silent limit acceptable in the lab until slice 7 adds notices, or
   should slice 1 send the notice for `messages_per_day` at least?
2. Should `QTH` and `SERVICE` mail arriving before their slices stay
   `queued` instead of `ignored`, so they are answered once supported?
   Leaving them queued means the worker must skip them on every pass.
