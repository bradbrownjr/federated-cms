# INQUIRY robot: architecture

**Status:** design for review. Nothing here is implemented.

The robot takes mail accepted from a B2F session and addressed to
`INQUIRY`, `QTH` or `SERVICE`, works out what was asked, answers from
local data or the cache, fetches from the Internet when it has to, and
queues replies from `SERVICE` in the sender's mailbox for their next
connection. It ties together pieces specified elsewhere:

| Piece | Spec | Branch at time of writing |
| --- | --- | --- |
| Address match, `INQUIRY` and `QTH` parsing | `docs/INQUIRY-ADAPTER.md`, `src/federated_cms/inquiry.py` | `feat/inquiry-parser` (4e8c47d) |
| Services, menus, menu codes, reply rules | `docs/INTERACTIVE-SERVICES.md` | `docs/interactive-services` (3624da0) |
| Text extraction and `render` | `docs/TEXT-EXTRACTOR.md` | `feat/text-extractor` (933a74f) |
| RSS/Atom parsing | `docs/FEED-READER.md` | `feat/feed-reader` (55727ed) |
| Safe fetcher | `docs/FETCHER.md` | not pushed; being written |
| Service-command and menu-reply parsing; `WX`/`ALERTS`/`SOLAR` formatters | not written yet | see [Interfaces for parallel specs](#interfaces-for-parallel-specs) |

Line references are to `main` at 5d502c1 unless a branch is named.
FETCHER.md is cited by section because it is still being edited. Marks:
**ASSUMPTION** (chosen here, not checked), **UNVERIFIED** (a fact not yet
confirmed), **OPEN** (a decision for the project lead).

## 1. Where the robot hooks in

**Today:** accepted bytes are checked (`src/federated_cms/b2f.py:73-99`)
and stored by `LabMailboxService.store_message(..., "in")` (`b2f.py:146`)
once per `(account, mid, direction)` (`src/federated_cms/lab_mailbox.py:112-133`),
under the account bound at login (`b2f.py:177-181`), not a header.
Outbound mail is `out` rows offered five at a time
(`lab_mailbox.py:148-170`) and marked delivered after the client's next
valid turn (`b2f.py:186-192`). The lab mailbox is single-account
(`lab_mailbox.py:1-10`); the robot uses only deposit and pending, which
match the proposed `MailboxService`
(`docs/MAILBOX-AUTHORIZATION-DESIGN.md:67-84`), so it moves to the
production mailbox unchanged.

### Decision: a durable queue, processed by a worker

The B2F session thread does two cheap things:

1. **Classify** with a pure function `robot_addresses(raw) ->
   frozenset[str]`: the robot local parts found in `To:` and `Cc:`, using
   `address_local_part` (`inquiry.py:92-99` on `feat/inquiry-parser`).
   It never raises; an unreadable header gives an empty set.
2. **Enqueue** one `robot_inbox` row (`INSERT OR IGNORE`, key
   `(account, mid)`) in the same transaction that stores the inbound
   bytes, when the set is not empty.

Everything else runs in the robot worker. A session has a 30 s socket
timeout (`b2f.py:240`) and the radio link is the scarce resource, so it
never waits on DNS, a slow server or a large parse. One transaction for
the bytes and the queue row means "accepted" and "queued for the robot"
cannot disagree after a crash; this needs `store_message`
(`lab_mailbox.py:119-131`) to take both writes. The worker can be a
thread or a separate process on the same database; leases make both safe.

**Same-session replies (OPEN).** Production Winlink answered on the next
connection, about a minute later (`docs/INQUIRY-ADAPTER.md:69-71`). For
items answerable without the Internet (`HELP`, `LIST`, static items,
cache hits), the session could wait up to `same_session_wait` (proposed
2 s) between `receive_turn` and `pending` (`b2f.py:186-195`) for the
worker, then offer the replies at once, saving a radio round trip.
Nothing in the session ever waits on a fetch.

### Idempotency

| Case | Guard |
| --- | --- |
| Client re-proposes a stored MID | Server answers `-` (`b2f.py:133-134`); no bytes, no row |
| Same bytes accepted twice (race between sessions) | `store_message` returns `False` (`lab_mailbox.py:127-131`); inbox insert is `OR IGNORE` |
| Worker crashes mid-message | Lease expires and the row is claimed again; items insert `OR IGNORE` on `UNIQUE(account, mid, seq)` |
| Worker crashes after building replies | Replies, menus, usage rows and the item's `done` state commit in **one** transaction, conditional on the lease token (the pattern of `SQLiteStore.finish`, `src/federated_cms/store.py:186-200`) |

As a last guard, reply MIDs are deterministic:
`base32(sha256(mid_salt|account|inbound_mid|seq|part))[:12]`. Twelve
characters fits `MID` (`src/federated_cms/b2_message.py:7`) and wl2k-go,
whose `Message.Validate` rejects a MID over 12 characters
(`fbb/message.go` at 1d59b0a). `mid_salt` is random per node
(`robot_meta`). The outbox deposit is `OR IGNORE`, so a replayed reply
keeps the first copy.

### The original message

The raw bytes stay in the inbound store unchanged. A message addressed
only to robot addresses goes to no user mailbox; its bytes are deleted
after 7 days (time to look into a problem report). A message that also
names other recipients is delivered to them by normal routing, which is
not part of this design.

## 2. The service table

### Configuration file

One TOML file, read with `tomllib` (standard library since Python 3.11),
loaded once at start. Changes need a restart, so a half-edited file never
takes effect.

```toml
[node]
name = "N1ABC-10"                 # in Mbo: and "via <name>" footers
contact = "sysop@example.org"     # User-Agent contact: email or http(s) URL
reply_from = "SERVICE"

[robot]
menu_expiry_days = 7
max_wait_minutes = 360            # how long an item may wait for a fetch
same_session_wait_seconds = 2     # 0 disables (OPEN, section 1)
url_items = true                  # http(s) lines in INQUIRY
deny_networks = []                # passed to fetch(), e.g. the node's public IP
blocked = []                      # callsigns whose requests are ignored

[limits]                          # section 6
messages_per_day = 60

[catalog]
file = "catalog.tsv"

[[service]]
verb = "NEWS"
handler = "feeds"
description = "News headlines: NEWS [source]"
[service.options]
feeds = [{ name = "BBC", url = "https://feeds.bbci.co.uk/news/rss.xml", topics = ["WORLD"], every_minutes = 60 }]
```

The catalog is a tab-separated file, since it can hold thousands of lines:

```text
# ID          CATEGORY  TARGET                                                            DESCRIPTION
ME_ZON_YORKC  WX_US_ME  url:https://tgftp.nws.noaa.gov/data/forecasts/zone/me/mez023.txt  Coastal York County zone forecast
WX_HOME       WX_LOCAL  cmd:WX Waterboro ME                                               Forecast for Waterboro
NODE_INFO     LOCAL     text:files/node-info.txt                                          About this node
```

`url:` is fetched and extracted; `cmd:` runs a service command as if the
user typed it; `text:` is a local ASCII file read at start. The mapping
is the node's own: Winlink's catalog file holds no URLs
(`INQUIRY-ADAPTER.md:115-116`) and is not copied
(`docs/INDEPENDENT-IMPLEMENTATION.md:56-58`).

### Validation at start

The robot prints every problem found and refuses to start when:

- a table or key is unknown (catches `menu_expiry_day`);
- a `handler` is not in the handler registry, a fixed `dict` in code from
  name to class (config cannot name a module, path or function);
- a `verb` does not match `[A-Z]{2,10}`, repeats, or is reserved (`LIST`,
  `UPDATE_CAT`, `HELP`, `MORE`);
- a catalog ID fails the parser's pattern (`inquiry.py:46`), repeats, or
  equals a verb or reserved word (`docs/INTERACTIVE-SERVICES.md:25-26`
  on `docs/interactive-services`);
- a `cmd:` target does not parse or names no enabled verb; a `url:` target
  fails the fetcher's URL rules; a `text:` file is missing, outside the
  config directory, not ASCII, or over 12,000 characters;
- a fetching handler is enabled (or `url_items` is on) and the User-Agent
  `federated-cms/<version> (<node name>; <contact>)` fails the fetcher's
  `user_agent` check, which requires a contact (FETCHER.md, "Arguments").
  Wikimedia and NWS both require one (`INTERACTIVE-SERVICES.md:210-215`,
  `:360-362`);
- a handler's `validate_options` reports errors (feed URLs, languages);
- a number is out of bounds (menu expiry 1 to 90 days, parts 1 to 50).

### Dispatch only through the table

A verb is looked up by its upper-cased ASCII token in a `dict` built from
validated config; there is no other lookup. Arguments reach the handler
as a `str`, parsed by that handler's own grammar. Nothing from a message
is passed to `eval`, `exec`, `getattr`, a shell, a format string or
template, or SQL text; SQL uses parameters only.

## 3. Per-request flow

```text
B2F session                 robot dispatcher                    fetch worker
store raw + robot_inbox ──► claim inbox row (lease)
                            parse; insert robot_items
                            claim item ──► handler.step()
                              Answer ──► one txn: replies to outbox,
                                         menus, usage, item=done
                              Need ────► fetch_tasks (one per key),
                                         item=waiting ──────────────► claim task
                                                                      fetch(), no txn open
                            item=ready ◄────────────────────────────── one txn: cache upsert,
                                                                       wake waiting items
next session: pending() offers the replies
```

For every message:

1. Loop and block checks (section 9). Failing messages are `ignored`
   with a reason code and get no reply.
2. Headers `From`, `To`, `Cc`, `Subject`, `Date`, `X-Location` from a
   bounded reader. `b2_message.py:30-50` already splits headers; it needs
   a public function returning them.
3. Body decoded strictly as UTF-8, else windows-1252; the parsers reject
   non-ASCII item lines anyway (`INQUIRY-ADAPTER.md:51-55`).
4. Request limit (section 6). Then each robot address is handled once, in
   the order `QTH`, `INQUIRY`, `SERVICE`, so a position sent with a
   request is on file before `WX` reads it.

### `INQUIRY`

`parse_inquiry` (`inquiry.py:110-145`) gives items and rejected lines,
capped at 32 items with repeats removed (`INQUIRY-ADAPTER.md:56-58`). A
bad subject or empty body (`InquiryError`) gets one error reply. The
command spec extends the parser so a line starting with a verb, `HELP` or
`HELP <verb>` is kind `command`; recognition order is `LIST`/`UPDATE_CAT`,
URL, reserved word or verb, catalog ID.

| Item | Action | Reply subject |
| --- | --- | --- |
| `list`, `update_catalog` | Catalog from the loaded TSV, no network | `INQUIRY: LIST` (observed, `INQUIRY-ADAPTER.md:93`) |
| `HELP` / `HELP WX` | Verbs with descriptions / that handler's `help()` | `INQUIRY - HELP` / `INQUIRY - HELP WX` |
| `catalog` | `text:` file content; `url:` as a URL item; `cmd:` as a command; unknown ID is an error | `INQUIRY - <url>` for `url:` (observed, `INQUIRY-ADAPTER.md:75-77`), else `INQUIRY - <ID>` |
| `url` | Error if `url_items` is off; else cache, `extract`, `render` | `INQUIRY - <url>` |
| other `command` | Handler `step()` loop | `INTERACTIVE-SERVICES.md:39-43` |

Rejected lines get **one** reply for the message: line number, cleaned
text (`RejectedLine.text`) and reason for each, then the `HELP` list.
Which `LIST` format Express accepts is untested
(`INQUIRY-ADAPTER.md:120-123`); slice 1 sends a readable list and the lab
tries the `CATEGORY|ID|DESCRIPTION|SIZE|DATE` form.

### `QTH`

`parse_position_report` (`inquiry.py:165-207`). A valid report with
coordinates replaces the account's `robot_positions` row unless its
`DATE` is older than the stored one (sessions can deliver out of order);
one without coordinates is not stored. A valid report gets **no reply**
(ASSUMPTION: as Winlink; saves airtime). An invalid one gets one error
reply with the `PositionReportError` reason, never the body. The position
serves only that account's `WX` and `ALERTS` with no place given
(`INTERACTIVE-SERVICES.md:300`), is never shown to anyone else, and is
deleted after 90 days. `X-Location` (`INTERACTIVE-SERVICES.md:133-136`)
is used for that message only and not stored.

### `SERVICE` (menu replies)

1. `parse_menu_reply` (command spec) strips leading `Re:`s, finds
   `[CODE]`, and reads only lines before the quoted-original separator
   (`INTERACTIVE-SERVICES.md:120-131`).
2. No code: one error reply saying requests go to `INQUIRY` with subject
   `REQUEST`. Counts as invalid (section 6).
3. Look up `(account, code)`. Another callsign's code is simply not
   found; nothing distinguishes "someone else's" from "unknown".
4. Unknown or expired: say so, and while the row exists (until
   `purge_after`) list its options as full commands to resend to
   `INQUIRY`.
5. Unreadable choice: the menu again, same code, with one line saying
   what was not understood.
6. Valid: each number becomes an item running the option's stored
   command as if sent to `INQUIRY`, capped at 32 (`2-40` is 39 items).
7. `MORE`: the next page (section 4).

## 4. Menu-code store

```sql
CREATE TABLE robot_menus (
    account TEXT NOT NULL, code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,                        -- created_at + menu_expiry_days
    purge_after TEXT NOT NULL,                       -- expires_at + 30 days
    item_id INTEGER NOT NULL,                        -- item that made it (audit only)
    title TEXT NOT NULL,                             -- ASCII, for the expired-menu reply
    options_json TEXT NOT NULL,                      -- [{"n":1,"label":"...","command":"WIKI Mercury (planet)"}]
    first_n INTEGER NOT NULL, page_size INTEGER NOT NULL,  -- first number on this page; 20
    more_code TEXT,                                  -- next page's code, once made
    PRIMARY KEY (account, code)
);
CREATE INDEX robot_menus_purge ON robot_menus(purge_after);
```

- **Generation:** `''.join(secrets.choice(ALPHABET) for _ in range(4))`
  over `23456789ABCDEFGHJKLMNPQRSTUVWXYZ` (`INTERACTIVE-SERVICES.md:78-80`),
  1,048,576 codes.
- **Collisions:** codes are unique per account; every lookup includes the
  account. On a key conflict, draw again, up to 8 times, then fail the
  item with `internal_error`. A code stays taken until `purge_after`, so
  it is never reused while its "expired" reply can still be given. With
  per-account limits an account holds a few hundred codes at most, so a
  redraw is rare.
- **Binding:** only the account that made the menu can use it. The code
  is a handle, not a secret; the B2F login protects it (section 9).
- **Expiry:** `menu_expiry_days` (default 7); then kept to `purge_after`
  only to answer with the options' commands. The janitor deletes it.
- **Paging:** up to 200 options stored (ASSUMPTION), 20 per page,
  numbered continuously (21 to 40 on page two). `MORE` on code `K`
  creates the next page once, with its own code, saved in `K.more_code`;
  a repeated `MORE` on `K` returns that same page. A reply may pick any
  stored option number, not only those on the page it answers.

## 5. Fetching and caching

### Handler interface

Handlers do no I/O. `step` is a pure function of the command, the item
context and the cache entries it asked for; it returns either the fetches
it needs or an answer. Multi-step lookups (NWS point, then forecast URL;
Wikipedia search, then parse) are repeated calls, each seeing more of the
cache. Tests need no network.

```python
# All dataclasses frozen.
class FetchSpec:   key: str; url: str; accept: str; max_bytes: int   # accept, max_bytes go to fetch()
                   freshness: timedelta    # younger: use without fetching
                   max_stale: timedelta    # younger than this: usable when a fetch fails
class CacheEntry:  key: str; status: int   # non-2xx cached too (NWS 404 InvalidPoint)
                   content_type: str | None; content: bytes; fetched_at: datetime
                   stale: bool; error: str | None      # last refresh failure, when stale
class Need:        fetches: tuple[FetchSpec, ...]
class MenuOption:  label: str; command: str            # command e.g. "WIKI Mercury (planet)"
class Menu:        title: str; options: tuple[MenuOption, ...]
class ReplyPart:   label: str              # subject text after "INQUIRY - "
                   body: str               # ASCII, "\n" endings
                   menu: Menu | None       # coded and rendered by the robot
class Answer:      parts: tuple[ReplyPart, ...]; error: str | None = None
class ItemContext: account: str; now: datetime; node_name: str; max_chars: int
                   position: tuple[float, float] | None  # QTH, else X-Location, else None

class Service(Protocol):
    verb: str
    description: str
    @classmethod
    def validate_options(cls, options: Mapping[str, object]) -> list[str]: ...  # errors
    def help(self) -> str: ...
    def scheduled(self) -> tuple[tuple[FetchSpec, timedelta], ...]: ...         # prefetch, with interval
    def step(self, cmd: ServiceCommand, ctx: ItemContext,
             cache: Mapping[str, CacheEntry]) -> Need | Answer: ...
```

An item is stopped with `internal_error` after 8 steps. URL items and
`url:` entries use a built-in handler: one `FetchSpec`, then
`plaintext.extract` and `render` (`TEXT-EXTRACTOR.md:192-232` on
`feat/text-extractor`).

### Cache keys

`"<service>|<accept>|<url>"`, with the URL normalised as the fetcher does
(lowercase scheme and host, default port dropped, no fragment; FETCHER.md,
"URLs"); the fetcher should export that normaliser. Handlers canonicalise
inputs first: coordinates rounded to 4 decimals (ASSUMPTION; UNVERIFIED
how NWS `/points` treats finer values), zone IDs upper-cased, feed URLs
from config. One cache serves all users: a second user asking for the
same forecast costs no fetch.

### Freshness

| Data | `freshness` | `max_stale` | Basis |
| --- | --- | --- | --- |
| NWS `/points` | 24 h | 30 days | Observed `max-age=86400` (`INTERACTIVE-SERVICES.md:320-321`) |
| NWS forecast | 1 h | 24 h | Observed `max-age=3600` (`:322-323`) |
| NWS alerts | 5 min | 1 h | Observed `max-age=5` (`:363`); 5 min limits load |
| SWPC products | 1 h | 24 h | "About hourly" (`:386-387`) |
| Feeds | sysop's `every_minutes` | 7 days | Sysop schedule (`:284-286`) |
| URL items, Wikipedia | 1 h, 24 h | 24 h, 30 days | ASSUMPTION |

Config may override any value per service. Response `Cache-Control` is
not used; the table is the rule. The fetcher takes no extra request
headers (FETCHER.md, "`fetch`"), so there is no `ETag` revalidation; a
refresh is a full fetch. An `ALERTS` reply from stale data starts:
`NOT CURRENT: could not reach NWS; list from 04:32Z (47 min ago). Alerts
may have been issued or cancelled since.`

### Tables

```sql
CREATE TABLE fetch_cache (
    key TEXT PRIMARY KEY, service TEXT NOT NULL, url TEXT NOT NULL,
    status INTEGER NOT NULL, content_type TEXT,
    content BLOB NOT NULL, bytes INTEGER NOT NULL,   -- after content decoding, <= max_bytes
    fetched_at TEXT NOT NULL, fresh_until TEXT NOT NULL, stale_until TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    last_error TEXT, last_error_at TEXT              -- error class of the latest failed refresh
);
CREATE INDEX fetch_cache_lru ON fetch_cache(last_used_at);

CREATE TABLE fetch_tasks (                           -- one per key, however many items wait
    key TEXT PRIMARY KEY, service TEXT NOT NULL, url TEXT NOT NULL,
    accept TEXT NOT NULL, max_bytes INTEGER NOT NULL, host TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('demand', 'schedule')),
    attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL,
    lease_token TEXT, lease_until TEXT, created_at TEXT NOT NULL
);
CREATE INDEX fetch_tasks_ready ON fetch_tasks(next_attempt_at);

CREATE TABLE fetch_hosts (
    host TEXT PRIMARY KEY, consecutive_failures INTEGER NOT NULL DEFAULT 0,
    down_until TEXT, last_success_at TEXT, last_error TEXT
);

CREATE TABLE robot_item_waits (
    item_id INTEGER NOT NULL REFERENCES robot_items(id) ON DELETE CASCADE,
    cache_key TEXT NOT NULL,
    PRIMARY KEY (item_id, cache_key)
);
CREATE INDEX robot_item_waits_key ON robot_item_waits(cache_key);
```

### The fetch worker

- Outside every B2F session. It claims a task with a lease in a short
  `BEGIN IMMEDIATE` transaction (as `claim_next`, `store.py:166-184`),
  calls `fetch()` with no transaction open, then in one transaction
  writes the cache entry, deletes the task and sets `ready` every waiting
  item whose keys are all present.
- Calls `federated_cms.fetch.fetch` with the node User-Agent, the spec's
  `accept` and `max_bytes`, and `deny_networks`. The `fetch` callable is
  injected, so tests pass a fake; `network=` is never passed (FETCHER.md,
  "Arguments").
- One request at a time per host (Wikimedia etiquette,
  `INTERACTIVE-SERVICES.md:210-211`), at most 4 overall (ASSUMPTION).
  Demand tasks go before scheduled ones.
- A 2xx, or a status the handler treats as an answer (NWS 404), is
  cached. A 5xx, 429 or `FetchError` is retried with backoff (1, 2, 4 ...
  minutes, at most 30), honouring `Retry-After`. After 3 consecutive
  failures a host gets `down_until` with the same backoff; one success
  clears it.
- The scheduler inserts tasks from each handler's `scheduled()` (feeds at
  `every_minutes`, SWPC hourly) with `reason='schedule'`, `OR IGNORE`.

### When the Internet is down

If a fetch fails and an entry younger than `max_stale` exists, the item
is answered from it at once with its age: `NWS unavailable; forecast
below is from 2026-11-25 04:33 UTC (3 h old).`
(`INTERACTIVE-SERVICES.md:347-348`). Otherwise the item waits and is
retried until `max_wait_minutes` (6 h), then gets "could not reach NWS;
try later". Fresh data is never invented (`docs/DECISIONS.md:33`,
`docs/THREAT-MODEL-M0.md:71`).

## 6. Limits per callsign

Counted per B2F account in UTC windows from the robot's own tables, so
there is no separate counter to drift. Defaults are proposals pending lab
measurement (`INTERACTIVE-SERVICES.md:448-449`).

| Limit | Default | Counted from | Checked |
| --- | --- | --- | --- |
| Items per request | 32 | Parser (`INQUIRY-ADAPTER.md:56-58`); menu selections | Parse |
| Requests per hour | 10 | `robot_inbox`, rolling 60 min | Parse, before any item |
| Messages per day | 60 | `robot_replies` since 00:00 UTC | Before fetching, and at reply commit |
| Bytes per day | 300,000 | `robot_replies.body_bytes` since 00:00 UTC | Reply commit |
| Parts per item | 12 | Parts of one item | Reply build |
| Invalid requests per day | 5 | Error replies for bad messages | Parse |
| Undelivered replies | 100 | `delivered=0` outbox rows | Parse |

Every message counts: each part of a split article and each error reply.

```sql
CREATE TABLE robot_replies (
    account TEXT NOT NULL, reply_mid TEXT NOT NULL,
    item_id INTEGER,                                 -- NULL for message-level errors and notices
    kind TEXT NOT NULL CHECK (kind IN ('content', 'menu', 'error', 'notice')),
    body_bytes INTEGER NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (account, reply_mid)
);
CREATE INDEX robot_replies_account_time ON robot_replies(account, created_at);
CREATE INDEX robot_replies_mid ON robot_replies(reply_mid);

CREATE TABLE robot_notices (
    account TEXT NOT NULL, limit_name TEXT NOT NULL, window_start TEXT NOT NULL,
    PRIMARY KEY (account, limit_name, window_start)
);
```

**What the user receives:** one notice per limit per window, subject
`INQUIRY - limit reached`, not itself counted:

```text
Limit reached for N0CALL: 60 reply messages per day.
Not processed: WX FN43, WIKI Mercury (planet).
The limit resets at 00:00 UTC. Send these again after that.
```

Items cut off are listed as commands so they can be resent. Later
requests in the same window are marked `limited` and get nothing. After
`invalid_per_day` bad messages (bad subject, no menu code, empty body),
further bad messages that day get no reply.

**Abuse handling:**

- `blocked` accounts: requests `ignored`, no reply.
- Node-wide caps across all accounts, fetches per hour and reply bytes
  per day (proposed 600 and 5 MB), so many accounts together cannot make
  the node a relay. When reached, demand fetches wait and stale data is
  served where allowed.
- `url_items = false` limits the node to the catalog and services the
  sysop chose.
- Limit hits, blocks and invalid streaks are logged with the account.

## 7. Reply construction

**One message per item** (`INTERACTIVE-SERVICES.md:36-37`), one for all
rejected lines, at most one notice. Split articles give one message per
part.

**Headers.** Field names from wl2k-go `fbb/header.go` (1d59b0a), `Mid`
first ("this is defined to be the first value"), then a fixed order:

```text
Mid: 4K7QZ2XWPA5M
Date: 2026/11/25 04:33
Type: Private
From: SERVICE
To: N0CALL
Subject: INQUIRY - WX FN43
Mbo: N1ABC-10
Body: 1187
```

- `Date` uses wl2k-go's `DateLayout`, `2006/01/02 15:04`, in UTC.
- `From: SERVICE` without a domain; the node never uses a `winlink.org`
  address as its own (`INDEPENDENT-IMPLEMENTATION.md:61-62`).
- `To:` is the B2F account, never the inbound `From:` (section 9).
- `Type: Private` is an ASSUMPTION. wl2k-go also defines `Service`,
  `Inquiry`, `Position Report`, `Option` and `System` (`fbb/message.go`);
  how Express treats each is UNVERIFIED and is a lab test.
- No attachments.

**Subject:** `INQUIRY - <label>[: <suffix>]`, suffixes such as
`choose [CODE]`, `not found`, `(part 2 of 6)`
(`INTERACTIVE-SERVICES.md:39-47`). At most 80 characters: the label is
cut with `...`, the suffix and code never. wl2k-go rejects subjects over
128 (`Message.Validate`), so 80 leaves margin. Printable ASCII only; any
other character in a label becomes `?`.

**Body:**

1. Handler text or `render` output (`TEXT-EXTRACTOR.md:212-232`).
2. For menus, the block from the menu spec's `render_menu`
   (`INTERACTIVE-SERVICES.md:55-74`).
3. The source footer each service requires (NWS, Wikipedia, feeds).
4. Checked to be printable ASCII plus `\n`; anything else is a bug that
   fails the item with `internal_error` instead of sending it.
5. `\n` to CRLF (the mail layer's job, `TEXT-EXTRACTOR.md:231-232`);
   `Body:` is the byte count after that.

**Size and splitting.** A part's text is at most `max_chars` (12,000),
far under `MAX_MESSAGE` (65,536 bytes, `b2_message.py:6`). `WIKI | FULL`
and `NEWS | n FULL` split with the extractor's planned `split`
(`INTERACTIVE-SERVICES.md:191-196`); other services truncate with the
extractor's note. Past `parts_per_item`, the last part sent says how many
there were and how to ask for one section. Menus offering a full article
show its size in messages first (`INTERACTIVE-SERVICES.md:169-171`).

**Queueing.** Each message passes `b2_message.message_mid`
(`b2_message.py:14-21`), then is deposited as an `out` row for the
account in the transaction that marks the item done. The next session
offers it through `pending()` like any mail. The robot needs a trusted
`deposit(account, mid, raw)` with no user session: the proposed
`deposit_message` (`MAILBOX-AUTHORIZATION-DESIGN.md:69-74`) under a
service grant.

## 8. Failure handling and observability

| Failure | Scope | User sees |
| --- | --- | --- |
| Unknown ID or verb, bad arguments, not found, outside NWS coverage | Item | Error reply for that item; other items continue |
| Fetch failed, stale data usable | Item | Content with age note |
| Fetch failed, no data, `max_wait` passed | Item | "could not reach <source>; try later" |
| Handler raised, or 8 steps without an answer | Item | "could not process this item (ref 1234)" |
| Bad subject, empty body, no menu code | Message | One error reply |
| Robot crash, store unavailable | Message | Nothing; retried. After 3 attempts, "could not process your message (ref ...)" |
| Loop, block, limit after notice | Message | Nothing |

Handler exceptions are caught per item, logged by class and ref, and
never stop the worker. The ref (`robot_items.id`) lets a sysop find the
item without the user quoting content.

```sql
CREATE TABLE robot_inbox (
    account TEXT NOT NULL, mid TEXT NOT NULL,
    addresses TEXT NOT NULL,                         -- e.g. "INQUIRY,QTH"
    received_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued', 'processing', 'done', 'failed', 'ignored')),
    reason TEXT,                                     -- short code: loop, blocked, limited, parse_error
    attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL,
    lease_token TEXT, lease_until TEXT, finished_at TEXT,
    PRIMARY KEY (account, mid)
);
CREATE INDEX robot_inbox_ready ON robot_inbox(state, next_attempt_at);

CREATE TABLE robot_items (
    id INTEGER PRIMARY KEY,
    account TEXT NOT NULL, mid TEXT NOT NULL, seq INTEGER NOT NULL,  -- seq: order in the message
    kind TEXT NOT NULL CHECK (kind IN ('list', 'help', 'catalog', 'url', 'command', 'choice', 'more')),
    request_json TEXT NOT NULL,                      -- parsed item: verb, args, choice, url, code
    state TEXT NOT NULL CHECK (state IN ('ready', 'processing', 'waiting', 'done', 'failed', 'limited')),
    steps INTEGER NOT NULL DEFAULT 0,
    deadline TEXT NOT NULL,                          -- created_at + max_wait
    lease_token TEXT, lease_until TEXT,
    error TEXT, created_at TEXT NOT NULL, finished_at TEXT,
    UNIQUE (account, mid, seq),
    FOREIGN KEY (account, mid) REFERENCES robot_inbox(account, mid)
);
CREATE INDEX robot_items_state ON robot_items(state, deadline);

CREATE TABLE robot_positions (
    account TEXT PRIMARY KEY, reported_at TEXT NOT NULL,  -- reported_at: DATE from the report
    received_at TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL
);
CREATE TABLE robot_counters (                        -- day: YYYY-MM-DD UTC
    day TEXT NOT NULL, name TEXT NOT NULL, value INTEGER NOT NULL, PRIMARY KEY (day, name)
);
CREATE TABLE robot_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);  -- schema_version, mid_salt
```

**Logging.** One `key=value` line per event through `logging`: event,
account, inbound MID, item ref, kind, verb, outcome code, reply count and
bytes, elapsed; for fetches, host, status, bytes, elapsed, error class.
**Never logged:** subjects, bodies, arguments, URL paths or queries,
positions, menu options, or exception messages that could quote input
(the class name is logged instead).

**Metrics.** `python -m federated_cms.robot status` reads the database
and prints: inbox and items by state with the oldest `queued`, `ready`
and `waiting` age; fetch tasks waiting, hosts down with `down_until` and
last error class, fetches and failures per host in 24 h; cache entries,
bytes, hits, misses and stale answers today; replies undelivered, sent
today and their bytes; notices, blocks and ignored messages by reason;
live menus; table sizes. Daily figures come from `robot_counters`,
incremented in the transaction that does the counted thing.

**Janitor.** Hourly, in short transactions of at most 1,000 rows:

| Data | Removed after |
| --- | --- |
| Inbound bytes of robot-only messages | 7 days |
| Outbox replies: delivered / never delivered | 7 days / 14 days |
| `robot_inbox`, `robot_items`, `robot_item_waits`, `robot_replies` | 30 days |
| `robot_notices` | 2 days |
| `robot_menus` | `purge_after` |
| `robot_positions` | 90 days after `received_at` |
| `fetch_cache` | `stale_until`; least recently used first over `cache_max_bytes` (proposed 200 MB) |
| `robot_counters` | 90 days |

`request_json` holds what the user asked (a title, a URL) and goes with
its item after 30 days.

## 9. Security

### SSRF

- The robot has no network code. All Internet access is the fetch worker
  calling `federated_cms.fetch.fetch`, which checks every URL, redirect
  and DNS answer against a fixed public-address policy, connects only to
  checked addresses, and bounds size and time (FETCHER.md, "Addresses",
  "Connecting"; `THREAT-MODEL-M0.md:26-27`).
- Service handlers build URLs from fixed bases (`https://api.weather.gov/`,
  `https://services.swpc.noaa.gov/`, the configured Wikipedia host), with
  user text only in validated, percent-encoded parameters. The robot
  checks each task's host against the handler's declared hosts, so a
  handler bug cannot fetch elsewhere.
- URL items are the only user-chosen destinations: they can be turned
  off, get `deny_networks` on top of the fetcher's policy (the node's
  public address; AMPRNet if the sysop wants), and accept only text media
  (`text/*`, `application/xhtml+xml`). Images such as Winlink's `.GIF`
  catalog items are out of scope.

### Mail loops

1. Only messages from an authenticated B2F session are queued. A future
   route (SMTP, federation) reaches the robot only with its own design.
2. Ignored when the account or the `From:` local part is `SERVICE`,
   `INQUIRY`, `QTH`, `SYSTEM`, `MAILER-DAEMON`, `POSTMASTER` or the node's
   name.
3. Ignored when the inbound MID is in `robot_replies` (one of ours).
4. Ignored when `Auto-Submitted` is present with a value other than `no`
   (RFC 3834, section 5). Whether Winlink clients send it is UNVERIFIED;
   the check is free.
5. Replies go only to the account's mailbox on this node, never by SMTP,
   never to `From:`. No reply is sent about a reply.
6. Limits (section 6) bound any loop not caught above.

### Spoofed callsigns

Identity is the account bound to the B2F session (`b2f.py:177-181`).
Headers are not identity; addresses "are not proof of identity or
authorization" (`b2_message.py:17-18`).

The login proves that the client knew the account's shared secret when
answering one `;PQ:` challenge. It does not prove that the person holds
the licence or that the transmitting station is that callsign, and the
legacy response is a short MD5-based value open to offline guessing
(`MAILBOX-AUTHORIZATION-DESIGN.md:49-58`). Through an RMS gateway, the
identity is whatever the gateway relays; gateway-to-CMS behaviour is not
established (`docs/ARCHITECTURE-RFC-0001.md:10`). A BPQ terminal
callsign is unverified (`docs/DECISIONS.md:12`) and is not an account for
the robot.

So a stolen secret exposes that account's menus, limits and last
position along with its mail; positions are never shown to anyone else.
A `From:` that differs from the account (tactical
address, other SSID) is counted (`from_mismatch`) and changes nothing.

### Unsafe content and storage growth

Replies are printable ASCII (`TEXT-EXTRACTOR.md:71-84`, rechecked in
section 7); feeds with `<!DOCTYPE` are refused (`FEED-READER.md:57-62`).
Every robot table has a retention rule (section 8),
and the inputs that create rows are capped: 32 items per message, 10
requests per hour per account, 200 options per menu, 2 MB per cache entry
and `cache_max_bytes` in total.

## 10. Build order

Every slice runs offline under `unittest` with the existing B2 fixtures
and `tests/lab_mailbox_helpers.py`, a fake clock and a fake `fetch`
returning saved responses, and ends with a lab check from Express or Pat
against the loopback B2F server.

1. **Plumbing, `HELP`, `LIST`, a static item.** Inbox hook in the
   acceptance transaction, dispatcher with leases, reply builder (MIDs,
   headers, subjects, CRLF, `message_mid` check), outbox deposit,
   rejected-lines reply, loop rules, config with refusal on errors,
   catalog `text:` targets. Tests: crash at each commit boundary,
   duplicate proposals, replay, bad config. Lab: Express sends `HELP`,
   `LIST` and a static ID and gets the replies; record how it shows each
   `Type:` and our `LIST`.
2. **`QTH`.** Positions, invalid-report reply, retention.
3. **Cache, fetch worker, URL items and `url:` entries.** One task per
   key, per-host serialisation, backoff, stale answers with age,
   `max_wait`, scheduled fetches, janitor; time driven by the fake clock.
4. **Menus and `SERVICE` replies.** Codes, expiry, `MORE`, numbers run as
   commands, expired-menu replies; tested with a test-only handler that
   returns a fixed menu.
5. **`WX`, `ALERTS`, `SOLAR`.** Formatters behind handlers; NWS and SWPC
   fixtures saved from real responses; place from QTH or `X-Location`.
6. **`NEWS`, then `WIKI`.** Feed schedules, digest and item menus,
   splitting.
7. **Limits and metrics.** Defaults from lab measurements, node-wide
   caps, `status`. Per-account counting exists from slice 1.

## Interfaces for parallel specs

Proposals for the two specs being written to accept or change.

**Service commands and menu replies** (module `federated_cms.commands`):

```python
class ServiceCommand:  verb: str        # upper-case, a configured verb or HELP
                       args: str        # whitespace collapsed, <= 200 chars
                       choice: str | None  # text after "|": "3", "FULL", "3 FULL"
def parse_command(line: str, verbs: frozenset[str]) -> ServiceCommand | None
    # None: not a command; the caller tries a catalog ID next
class MenuReply:       code: str; more: bool
                       numbers: tuple[int, ...]  # ranges expanded, order kept, repeats removed
class MenuReplyError(ValueError): ...   # no code, or text not numbers/ranges/MORE
def parse_menu_reply(subject: str, text: str) -> MenuReply
def render_menu(menu: Menu, *, code: str, account: str,
                expires_at: datetime, first_n: int, has_more: bool) -> str
```

`parse_inquiry` gains a `verbs` argument so command lines become kind
`command`, and `service_for` (`inquiry.py:102-107`) also returns
`SERVICE`.

**`WX`, `ALERTS`, `SOLAR` formatters** (module `federated_cms.wxformat`):
pure functions from fetched bytes to text, called from a handler's `step`
once its keys are cached.

```python
class Formatted:  label: str        # subject label, e.g. "WX 43.6,-70.7"
                  body: str         # ASCII, "\n" endings, footer included
                  options: tuple[MenuOption, ...]
def format_wx(place: str, points: bytes, forecast: bytes, *,
              forecast_fetched_at: datetime, full: bool, now: datetime) -> Formatted
def format_alerts(place: str, alerts: bytes, *, fetched_at: datetime,
                  stale: bool, index: int | None, now: datetime) -> Formatted
def format_solar(products: Mapping[str, tuple[bytes, datetime] | None], *,
                 now: datetime) -> Formatted  # product path -> (content, fetched_at), None if never fetched
```

Bad data (NWS problem details, missing fields) raises
`FormatError(ValueError)` with a short code the robot maps to an error
reply.

## Open questions for the project lead

1. Same-session replies for local answers (section 1): on by default?
2. Reply `Type:`: `Private` until the lab shows what Express does with
   `Service` or `System`.
3. `QTH`: no reply to a valid report, assumed to match Winlink.
4. Default limits (section 6) before lab measurements exist.
5. Should a request waiting on the Internet get an immediate "queued"
   reply? This design sends none, to save airtime.
6. URL items on by default, or off until the sysop enables them?
7. Split or truncate long content for services other than `WIKI` and
   `NEWS` (`INTERACTIVE-SERVICES.md:466-467`).
8. With federation, an item a node cannot fetch could become a request
   envelope to a peer (`docs/REQUEST-ENVELOPE-v0.1.md`) filling the same
   cache. Not designed here.
