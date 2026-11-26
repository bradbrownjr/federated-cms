# INQUIRY robot: architecture

**Status:** design for review. Nothing here is implemented.

The robot takes mail accepted from a B2F session and addressed to the
node's service address (`FCMS` unless the sysop sets another), `INQUIRY`
or `QTH`, works out what was asked, answers from local data or the cache,
fetches from the Internet when it has to, and queues replies from the
service address in the sender's mailbox for their next connection. The
service address carries the node's own commands and menus; `INQUIRY` and
`QTH` behave as Winlink's do (the two layers,
[INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md#two-layers)). It ties together pieces specified elsewhere:

| Piece | Spec and code |
| --- | --- |
| Address match, service address check, `INQUIRY`, `QTH` and native command parsing | `docs/INQUIRY-ADAPTER.md`, `src/federated_cms/inquiry.py` |
| Menu-reply parsing | `docs/MENU-REPLY.md`, `src/federated_cms/menu_reply.py` |
| Services, menus, menu codes, reply rules | `docs/INTERACTIVE-SERVICES.md` |
| Text extraction and `render` | `docs/TEXT-EXTRACTOR.md`, `src/federated_cms/plaintext.py` |
| RSS/Atom parsing | `docs/FEED-READER.md`, `src/federated_cms/feeds.py` |
| Safe fetcher | `docs/FETCHER.md`, `src/federated_cms/fetch.py` |
| `WX`/`ALERTS`/`SOLAR` formatters | `docs/WX-FORMATTERS.md`, `src/federated_cms/wx_format.py` |

The robot's own parts are listed under [Interfaces](#interfaces).

Line references are to `main` at 7f6d3b4. Marks:
**ASSUMPTION** (chosen here, not checked), **UNVERIFIED** (a fact not yet
confirmed), **OPEN** (a decision for the project lead).

## 1. Where the robot hooks in

**Today:** accepted bytes are checked (`src/federated_cms/b2f.py:73-99`)
and stored by `LabMailboxService.store_message(..., "in")` (`src/federated_cms/b2f.py:146`)
once per `(account, mid, direction)` (`src/federated_cms/lab_mailbox.py:112-133`),
under the account bound at login (`src/federated_cms/b2f.py:177-181`), not a header.
Outbound mail is `out` rows offered five at a time
(`src/federated_cms/lab_mailbox.py:148-170`) and marked delivered after the client's next
valid turn (`src/federated_cms/b2f.py:186-192`). The lab mailbox is single-account
(`src/federated_cms/lab_mailbox.py:1-10`); the robot uses only deposit and pending, which
match the proposed `MailboxService`
(`docs/MAILBOX-AUTHORIZATION-DESIGN.md:67-84`), so it moves to the
production mailbox unchanged.

### Decision: a durable queue, processed by a worker

The B2F session thread does two cheap things:

1. **Classify** with a pure function `robot_addresses(raw, *,
   service_address) -> frozenset[str]`: which of the service address,
   `INQUIRY` and `QTH` are local parts of addresses in `To:` (every `To:`
   header; `Cc:` does not count), using
   `address_local_part` (`src/federated_cms/inquiry.py:94-101`).
   It never raises; an unreadable header gives an empty set. `SERVICE` is
   not a robot address, so mail to it is not queued and gets no reply.
2. **Enqueue** one `robot_inbox` row (`INSERT OR IGNORE`, key
   `(account, mid)`) in the same transaction that stores the inbound
   bytes, when the set is not empty.

Everything else runs in the robot worker. A session has a 30 s socket
timeout (`src/federated_cms/b2f.py:240`) and the radio link is the scarce resource, so it
never waits on DNS, a slow server or a large parse. One transaction for
the bytes and the queue row means "accepted" and "queued for the robot"
cannot disagree after a crash; this needs `store_message`
(`src/federated_cms/lab_mailbox.py:119-131`) to take both writes. The worker can be a
thread or a separate process on the same database; leases make both safe.

**Same-session replies (on by default).** Production Winlink answered on the next
connection, about a minute later (`docs/INQUIRY-ADAPTER.md:72-73`). For
items answerable without the Internet (`HELP`, `LIST`, static items,
cache hits), the session could wait up to `same_session_wait` (proposed
2 s) between `receive_turn` and `pending` (`src/federated_cms/b2f.py:186-195`) for the
worker, then offer the replies at once, saving a radio round trip.
Nothing in the session ever waits on a fetch.

### Idempotency

| Case | Guard |
| --- | --- |
| Client re-proposes a stored MID | Server answers `-` (`src/federated_cms/b2f.py:133-134`); no bytes, no row |
| Same bytes accepted twice (race between sessions) | `store_message` returns `False` (`src/federated_cms/lab_mailbox.py:127-131`); inbox insert is `OR IGNORE` |
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
service_address = "FCMS"         # native address; every reply comes from it

[robot]
menu_expiry_days = 7
max_wait_minutes = 360            # how long an item may wait for a fetch
same_session_wait_seconds = 2     # 0 disables (section 1)
url_items = true                  # http(s) lines in INQUIRY
deny_networks = []                # passed to fetch(), e.g. the node's public IP
blocked = []                      # callsigns whose requests are ignored

[limits]                          # section 6; every key optional
items_per_request = 32
requests_per_hour = 10
messages_per_day = 100
bytes_per_day = 1_000_000
parts_per_item = 12
invalid_per_day = 5
undelivered_max = 100
node_fetches_per_hour = 600       # all accounts together
node_bytes_per_day = 5_000_000

[limits.accounts.N0EOC]           # per-callsign override, e.g. a served agency
messages_per_day = 300
bytes_per_day = 3_000_000

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
(`docs/INQUIRY-ADAPTER.md:116-117`) and is not copied
(`docs/INDEPENDENT-IMPLEMENTATION.md:56-58`).

### Validation at start

The robot prints every problem found and refuses to start when:

- `node.service_address` fails `inquiry.check_service_address`: 3 to 12
  upper-case letters, or letters, one `-`, then letters and digits; not
  `INQUIRY`, `QTH`, `SERVICE`, `SYSTEM`, `OPTIONS`, `POSTMASTER` or
  `MAILER-DAEMON` ([INQUIRY-ADAPTER.md](INQUIRY-ADAPTER.md#service-address),
  which records the source and what is UNVERIFIED);
- a table or key is unknown (catches `menu_expiry_day`);
- a `handler` is not in the handler registry, a fixed `dict` in code from
  name to class (config cannot name a module, path or function);
- a `verb` does not match `[A-Z]{2,10}`, repeats, or is reserved (`LIST`,
  `UPDATE_CAT`, `HELP`, `MORE`);
- a catalog ID fails the parser's pattern (`src/federated_cms/inquiry.py:48`), repeats, or
  equals a verb or reserved word (`docs/INTERACTIVE-SERVICES.md:25-26`
  on `docs/interactive-services`);
- a `cmd:` target does not parse or names no enabled verb; a `url:` target
  fails the fetcher's URL rules; a `text:` file is missing, outside the
  config directory, not ASCII, or over 12,000 characters;
- a fetching handler is enabled (or `url_items` is on) and the User-Agent
  `federated-cms/<version> (<node name>; <contact>)` fails the fetcher's
  `user_agent` check, which requires a contact (`docs/FETCHER.md`, "Arguments").
  Wikimedia and NWS both require one (`docs/INTERACTIVE-SERVICES.md:210-215`,
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
2. Headers `From`, `To`, `Subject`, `Date`, `X-Location` from a
   bounded reader. `src/federated_cms/b2_message.py:30-50` already splits headers; it needs
   a public function returning them.
3. Body decoded strictly as UTF-8, else windows-1252; the parsers reject
   non-ASCII item lines anyway (`docs/INQUIRY-ADAPTER.md:52-56`).
4. Request limit (section 6). Then each robot address is handled once, in
   the order `QTH`, `INQUIRY`, service address, so a position sent with a
   request is on file before `WX` reads it.

### `INQUIRY`

`parse_inquiry` (`src/federated_cms/inquiry.py:141-182`) gives items and rejected lines,
capped at 32 items with repeats removed (`docs/INQUIRY-ADAPTER.md:57-59`). A
bad subject or empty body (`InquiryError`) gets one error reply. The
robot calls `parse_inquiry(subject, body, services=...)` with the
configured verbs plus `HELP`, only so that command lines are recognised
and pointed out instead of being judged as catalog IDs. Recognition order
is a service verb, `LIST`/`UPDATE_CAT`, URL, catalog ID.

| Item | Action | Reply subject |
| --- | --- | --- |
| `list`, `update_catalog` | Catalog from the loaded TSV, no network | `INQUIRY: LIST` (observed, `docs/INQUIRY-ADAPTER.md:94-96`) |
| `catalog` | `text:` file content; `url:` as a URL item; `cmd:` as a native command; unknown ID is an error | `INQUIRY - <url>` for `url:` (observed, `docs/INQUIRY-ADAPTER.md:76-78`), else `INQUIRY - <ID>` |
| `url` | Error if `url_items` is off; else cache, `extract`, `render` | `INQUIRY - <url>` |
| `service`, `HELP` included | Not run. All of the message's command lines go into one pointer reply ([INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md#compatibility-requests)) | `INQUIRY - REQUEST: send commands to <service address>` |

Rejected lines get **one** reply for the message: line number, cleaned
text (`RejectedLine.text`) and reason (`RejectedLine.reason`) for each of
the first 20, then `...and <count - 20> more.` when there are more (the
cap of the slice 1 contract), then one line saying what `INQUIRY` takes
and that commands go to the service address.
Which `LIST` format Express accepts is untested
(`docs/INQUIRY-ADAPTER.md:121-124`); slice 1 sends a readable list and the lab
tries the `CATEGORY|ID|DESCRIPTION|SIZE|DATE` form.

### `QTH`

`parse_position_report` (`src/federated_cms/inquiry.py:202-244`). A valid report with
coordinates replaces the account's `robot_positions` row unless its
`DATE` is older than the stored one (sessions can deliver out of order);
one without coordinates is not stored. A valid report gets **no reply**
(ASSUMPTION: as Winlink; saves airtime). An invalid one gets one error
reply, subject `QTH - position report: <reason>`, with the
`PositionReportError` reason and never the body. Whether Winlink answers
an invalid report is UNVERIFIED. The position
serves only that account's `WX` and `ALERTS` with no place given
(`docs/INTERACTIVE-SERVICES.md:300`), is never shown to anyone else, and is
deleted after 90 days. `X-Location` (`docs/INTERACTIVE-SERVICES.md:133-136`)
is used for that message only and not stored.

### Service address (native requests and menu replies)

The robot picks this mail by `address_local_part` of each `To:` address
against `node.service_address` (`service_for` returns only `INQUIRY` and
`QTH`).

1. **Read the menu fields.** `parse_menu_reply(raw_message,
   address=service_address)` (`src/federated_cms/menu_reply.py`;
   `docs/MENU-REPLY.md`) takes the whole stored MIME message as bytes,
   not a subject and body. It checks the `To` address, finds the one
   `[CODE]` anywhere in the subject, reads the first `text/plain` part
   and only the lines before the quoted-original separator. It returns
   `MenuReply(code, numbers, more)` or raises `MenuReplyError` with a
   `reason` and, when the subject held one valid code, that `code`.
   Messages over 65,536 bytes are refused (`too large`), the same size as
   `MAX_MESSAGE`; the robot passes the stored message unchanged.
2. **Decide menu reply or commands**, looking up `(account, code)` first
   whenever there is a code. Another callsign's code is simply not found;
   nothing distinguishes "someone else's" from "unknown".

   | Parser result | `(account, code)` stored | Then |
   | --- | --- | --- |
   | `MenuReply` | yes | Steps 4 to 7 |
   | `MenuReply` | no | One reply: no menu `<code>` for this callsign; to send commands, write a new message to the service address |
   | `MenuReplyError` with `code` (`no text part`, `unsupported transfer encoding`, `non-ASCII`, `empty choice`, `invalid choice`, `invalid range`, `too many choices`) | yes | Step 3 |
   | `MenuReplyError` with `code` | no | Commands (step 5) |
   | `no code`, `invalid code`, `multiple codes` | | Commands (step 5) |
   | `too large`, `malformed message` | | One error reply, subject `FCMS - message: <reason>` |
   | `no service address` | | Cannot happen for mail routed here; logged as `internal_error` |

   A subject word in brackets, such as `[TEST]`, is code-shaped and
   becomes a code (`MENU-REPLY.md`, step 4), so this lookup is what keeps
   it from blocking a request. A row kept for an expired menu (until
   `purge_after`) counts as stored.
3. **Menu reply error:** one reply naming the reason and saying to answer
   with option numbers, such as `2` or `1-3`, or `MORE`, and to send
   commands in a new message. Subject `FCMS - menu reply: <reason>`. It
   counts as invalid (section 6).
4. **Expired:** say so and list the options as full commands to send to
   the service address. **A number with no stored option:** one error
   reply naming the numbers and the menu's valid range. **Valid:** each
   number (sorted, no repeats, at most 20 per reply) becomes an item
   running the option's stored command as if sent to the service address
   (`2-21` is 20 items). **`MORE`:** the next page (section 4).
5. **Commands.** `parse_commands(body, services=<configured verbs plus
   HELP>)` ([INQUIRY-ADAPTER.md](INQUIRY-ADAPTER.md#parse_commands)), on
   the B2 body as for `INQUIRY`. Each item runs as below; rejected lines
   get one reply for the message listing at most 20 (line, text and
   reason), then `...and <count - 20> more.`, then the `HELP` list; an
   empty request gets the `HELP` reply; `InquiryError` (`body too large`,
   `invalid body`) gets one error reply, subject `FCMS - message:
   <reason>`.

   | Item | Action | Reply subject |
   | --- | --- | --- |
   | `HELP` with no / one argument (`HELP WX`) | Verbs with descriptions / that handler's `help()` | `FCMS - HELP` / `FCMS - HELP WX` |
   | other `service` | Handler `step()` loop | [INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md#replies) |

`FCMS` in these subjects is the configured service address.

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
  over `23456789ABCDEFGHJKLMNPQRSTUVWXYZ` (`docs/INTERACTIVE-SERVICES.md:78-80`),
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
                   listed_in_body: bool = False   # True: the body already prints the options
class ReplyPart:   label: str              # subject text after "<prefix> - "
                   body: str               # ASCII, "\n" endings
                   menu: Menu | None       # stored and coded by the robot; rendered by it unless listed_in_body
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
    def step(self, item: InquiryItem, ctx: ItemContext,
             cache: Mapping[str, CacheEntry]) -> Need | Answer: ...
```

An item is stopped with `internal_error` after 8 steps. URL items and
`url:` entries use a built-in handler: one `FetchSpec`, then
`plaintext.extract` and `render` (`docs/TEXT-EXTRACTOR.md:316-361`).

### Cache keys

`"<service>|<accept>|<url>"`, with the URL normalised as the fetcher does
(lowercase scheme and host, default port dropped, no fragment; `docs/FETCHER.md`,
"URLs"); the normaliser is private in `src/federated_cms/fetch.py` (`_url`), so the robot needs it exported. Handlers canonicalise
inputs first: coordinates rounded to 4 decimals (ASSUMPTION; UNVERIFIED
how NWS `/points` treats finer values), zone IDs upper-cased, feed URLs
from config. One cache serves all users: a second user asking for the
same forecast costs no fetch.

### Freshness

| Data | `freshness` | `max_stale` | Basis |
| --- | --- | --- | --- |
| NWS `/points` | 24 h | 30 days | Observed `max-age=86400` (`docs/INTERACTIVE-SERVICES.md:320-321`) |
| NWS forecast | 1 h | 24 h | Observed `max-age=3600` (`:322-323`) |
| NWS alerts | 5 min | 1 h | Observed `max-age=5` (`:363`); 5 min limits load |
| SWPC products | 1 h | 24 h | "About hourly" (`:386-387`) |
| Feeds | sysop's `every_minutes` | 7 days | Sysop schedule (`:284-286`) |
| URL items, Wikipedia | 1 h, 24 h | 24 h, 30 days | ASSUMPTION |

Config may override any value per service. Response `Cache-Control` is
not used; the table is the rule. The fetcher takes no extra request
headers (`docs/FETCHER.md`, "`fetch`"), so there is no `ETag` revalidation; a
refresh is a full fetch. A `WX` or `ALERTS` reply from stale data is
built with `format_nws_error` (see "When the Internet is down").

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
  `BEGIN IMMEDIATE` transaction (as `claim_next`, `src/federated_cms/store.py:166-184`),
  calls `fetch()` with no transaction open, then in one transaction
  writes the cache entry, deletes the task and sets `ready` every waiting
  item whose keys are all present.
- Calls `federated_cms.fetch.fetch` with the node User-Agent, the spec's
  `accept` and `max_bytes`, and `deny_networks`. The `fetch` callable is
  injected, so tests pass a fake; `network=` is never passed (`docs/FETCHER.md`,
  "Arguments").
- One request at a time per host (Wikimedia etiquette,
  `docs/INTERACTIVE-SERVICES.md:210-211`), at most 4 overall (ASSUMPTION).
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
is answered from it at once with its age. For `WX` and `ALERTS` the
handler formats the stale entry as usual, then calls
`format_nws_error(service, None, ..., cached=<that reply>, cached_at=<entry
fetched_at>, now=now)`, which gives `NWS unavailable, try later.` and
`Older reply follows, age N min.` before the cached reply
(`docs/WX-FORMATTERS.md`, "Errors"). Other services state the age in their own
wording. Otherwise the item waits and is
retried until `max_wait_minutes` (6 h), then gets "could not reach NWS;
try later". Fresh data is never invented (`docs/DECISIONS.md:33`,
`docs/THREAT-MODEL-M0.md:71`).

## 6. Limits per callsign

Counted per B2F account in UTC windows from the robot's own tables, so
there is no separate counter to drift. Defaults were chosen on 2026-11-25
and are to be revisited after lab measurement.

| Limit (`[limits]` key) | Default | Counted from | Checked |
| --- | --- | --- | --- |
| Items per request (`items_per_request`) | 32 | Parser (`docs/INQUIRY-ADAPTER.md:57-59`); a menu reply selects at most 20 numbers (`docs/MENU-REPLY.md`) | Parse |
| Requests per hour (`requests_per_hour`) | 10 | `robot_inbox`, rolling 60 min | Parse, before any item |
| Messages per day (`messages_per_day`) | 100 | `robot_replies` since 00:00 UTC | Before fetching, and at reply commit |
| Bytes per day (`bytes_per_day`) | 1,000,000 | `robot_replies.body_bytes` since 00:00 UTC | Reply commit |
| Parts per item (`parts_per_item`) | 12 | Parts of one item | Reply build |
| Invalid requests per day (`invalid_per_day`) | 5 | Error replies for bad messages | Parse |
| Undelivered replies (`undelivered_max`) | 100 | `delivered=0` outbox rows | Parse |

Every value is set by the sysop in `[limits]`; a key left out keeps its
default. `[limits.accounts.<CALL>]` overrides any of the per-callsign keys
for one account (for example a served agency's tactical account). Values
must be positive integers; anything else stops the robot at start, like
any config error. `items_per_request` cannot exceed the parser's 32.

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
`FCMS - limit reached` (the service address), not itself counted:

```text
Limit reached for N0CALL: 100 reply messages per day.
Not processed: WX FN43, WIKI Mercury (planet).
The limit resets at 00:00 UTC. Send these again after that.
```

Items cut off are listed as commands so they can be resent. Later
requests in the same window are marked `limited` and get nothing. After
`invalid_per_day` bad messages (an `INQUIRY` request with a bad subject or
no items, an unreadable native message, a bad menu reply),
further bad messages that day get no reply.

**Abuse handling:**

- `blocked` accounts: requests `ignored`, no reply.
- Node-wide caps across all accounts, fetches per hour and reply bytes
  per day (`node_fetches_per_hour`, `node_bytes_per_day`; defaults 600 and 5 MB), so many accounts together cannot make
  the node a relay. When reached, demand fetches wait and stale data is
  served where allowed.
- `url_items = false` limits the node to the catalog and services the
  sysop chose.
- Limit hits, blocks and invalid streaks are logged with the account.

## 7. Reply construction

**One message per item** (`docs/INTERACTIVE-SERVICES.md:36-37`), one for all
rejected lines, at most one notice. Split articles give one message per
part.

**Headers.** Field names from wl2k-go `fbb/header.go` (1d59b0a), `Mid`
first ("this is defined to be the first value"), then a fixed order:

```text
Mid: 4K7QZ2XWPA5M
Date: 2026/11/25 04:33
Type: Private
From: FCMS
To: N0CALL
Subject: FCMS - WX FN43
Mbo: N1ABC-10
Body: 1187
```

- `Date` uses wl2k-go's `DateLayout`, `2006/01/02 15:04`, in UTC.
- `From:` is the service address without a domain, for every reply,
  `INQUIRY` and `QTH` replies included, so a user's Reply goes back to
  it. The node never sends from `SERVICE`, Winlink's own system address,
  and never uses a `winlink.org` address as its own
  ([INDEPENDENT-IMPLEMENTATION.md](INDEPENDENT-IMPLEMENTATION.md#what-we-do-not-do)).
- `To:` is the B2F account, never the inbound `From:` (section 9).
- `Type: Private` is an ASSUMPTION. wl2k-go also defines `Service`,
  `Inquiry`, `Position Report`, `Option` and `System` (`fbb/message.go`);
  how Express treats each is UNVERIFIED and is a lab test.
- No attachments.

**Subject:** `<prefix> - <label>[: <suffix>]`. The prefix is `INQUIRY`
or `QTH` for compatibility replies and the service address for native
ones; suffixes are such as `choose [CODE]`, `not found`, `(part 2 of 6)`;
`INQUIRY: LIST` is fixed
([INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md#replies)). At most 80
characters: the label is cut with `...`, the prefix, suffix and code
never. wl2k-go rejects subjects over
128 (`Message.Validate`), so 80 leaves margin. Printable ASCII only; any
other character in a label becomes `?`.

**Body:**

1. Handler text or `render` output (`docs/TEXT-EXTRACTOR.md:340-361`).
2. For menus, the block from the robot's menu renderer
   (`docs/INTERACTIVE-SERVICES.md:55-74`): numbered options with their
   commands, then the code and reply instructions. `WX` and `ALERTS`
   text from `wx_format` already holds the numbered options and their
   commands, so for those the renderer adds only the code and
   instructions (`Menu.listed_in_body`).
3. The source footer each service requires (NWS, Wikipedia, feeds).
4. Checked to be printable ASCII plus `\n`; anything else is a bug that
   fails the item with `internal_error` instead of sending it.
5. `\n` to CRLF (the mail layer's job, `docs/TEXT-EXTRACTOR.md:359-360`);
   `Body:` is the byte count after that.

**Size and splitting.** A part's text is at most `max_chars` (12,000),
far under `MAX_MESSAGE` (65,536 bytes, `src/federated_cms/b2_message.py:6`). `WIKI | FULL`
and `NEWS | n FULL` split with the extractor's planned `split`
(`docs/INTERACTIVE-SERVICES.md:191-196`); other services truncate with the
extractor's note. Past `parts_per_item`, the last part sent says how many
there were and how to ask for one section. Menus offering a full article
show its size in messages first (`docs/INTERACTIVE-SERVICES.md:169-171`).

**Queueing.** Each message passes `b2_message.message_mid`
(`src/federated_cms/b2_message.py:14-21`), then is deposited as an `out` row for the
account in the transaction that marks the item done. The next session
offers it through `pending()` like any mail. The robot needs a trusted
`deposit(account, mid, raw)` with no user session: the proposed
`deposit_message` (`docs/MAILBOX-AUTHORIZATION-DESIGN.md:69-74`) under a
service grant.

## 8. Failure handling and observability

| Failure | Scope | User sees |
| --- | --- | --- |
| Unknown ID or verb, bad arguments, not found, outside NWS coverage | Item | Error reply for that item; other items continue |
| Fetch failed, stale data usable | Item | Content with age note |
| Fetch failed, no data, `max_wait` passed | Item | "could not reach <source>; try later" |
| Handler raised, or 8 steps without an answer | Item | "could not process this item (ref 1234)" |
| `INQUIRY` request with a bad subject or no items, unreadable native message, bad menu reply | Message | One error reply |
| Mail to `SERVICE` only | Message | Nothing; not queued |
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
  checked addresses, and bounds size and time (`docs/FETCHER.md`, "Addresses",
  "Connecting"; `docs/THREAT-MODEL-M0.md:26-27`).
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
2. Ignored when the account or the `From:` local part is the service
   address, one of `inquiry.RESERVED_ADDRESSES` (`INQUIRY`, `QTH`,
   `SERVICE`, `SYSTEM`, `OPTIONS`, `POSTMASTER`, `MAILER-DAEMON`) or the
   node's name.
3. Ignored when the inbound MID is in `robot_replies` (one of ours).
4. Ignored when `Auto-Submitted` is present with a value other than `no`
   (RFC 3834, section 5). Whether Winlink clients send it is UNVERIFIED;
   the check is free.
5. Replies go only to the account's mailbox on this node, never by SMTP,
   never to `From:`. No reply is sent about a reply.
6. Mail to `SERVICE` is never queued or answered, and the node never
   sends from `SERVICE`.
7. Limits (section 6) bound any loop not caught above.

### Spoofed callsigns

Identity is the account bound to the B2F session (`src/federated_cms/b2f.py:177-181`).
Headers are not identity; addresses "are not proof of identity or
authorization" (`src/federated_cms/b2_message.py:17-18`).

The login proves that the client knew the account's shared secret when
answering one `;PQ:` challenge. It does not prove that the person holds
the licence or that the transmitting station is that callsign, and the
legacy response is a short MD5-based value open to offline guessing
(`docs/MAILBOX-AUTHORIZATION-DESIGN.md:49-58`). Through an RMS gateway, the
identity is whatever the gateway relays; gateway-to-CMS behaviour is not
established (`docs/ARCHITECTURE-RFC-0001.md:10`). A BPQ terminal
callsign is unverified (`docs/DECISIONS.md:12`) and is not an account for
the robot.

So a stolen secret exposes that account's menus, limits and last
position along with its mail; positions are never shown to anyone else.
A `From:` that differs from the account (tactical
address, other SSID) is counted (`from_mismatch`) and changes nothing.

### Unsafe content and storage growth

Replies are printable ASCII (`docs/TEXT-EXTRACTOR.md:145`, rechecked in
section 7); feeds with `<!DOCTYPE` are refused (`docs/FEED-READER.md:57-62`).
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
   duplicate proposals, replay, bad config. Lab: Express sends `HELP` to
   the service address, `LIST` and a static ID to `INQUIRY`, and gets the
   replies; record how it shows each
   `Type:` and our `LIST`.
2. **`QTH`.** Positions, invalid-report reply, retention.
3. **Cache, fetch worker, URL items and `url:` entries.** One task per
   key, per-host serialisation, backoff, stale answers with age,
   `max_wait`, scheduled fetches, janitor; time driven by the fake clock.
4. **Menus and menu replies.** Codes, expiry, `MORE`, numbers run as
   commands, expired-menu replies; tested with a test-only handler that
   returns a fixed menu. The table in section 3 ("Decide menu reply or
   commands") is tested row by row, including: `Re: [TEST] x` with body
   `HELP` from an account holding no menu `TEST` gets the `HELP` reply;
   the same message from an account holding menu `TEST` gets the menu
   reply error; `Re: [TEST]` with body `2` and no menu `TEST` gets the
   "no menu" reply; another account's code with body `HELP` runs `HELP`.
5. **`WX`, `ALERTS`, `SOLAR`.** Handlers that call the `wx_format`
   functions; NWS and SWPC fixtures are in `tests/fixtures/`; place from
   QTH or `X-Location`.
6. **`NEWS`, then `WIKI`.** Feed schedules, digest and item menus,
   splitting.
7. **Limits and metrics.** Defaults from lab measurements, node-wide
   caps, `status`. Per-account counting exists from slice 1.

## Interfaces

Written and tested; the robot calls these and does not reimplement them.
The specs win over this document.

**Service commands** (`src/federated_cms/inquiry.py`, `docs/INQUIRY-ADAPTER.md`):

```python
parse_inquiry(subject: str, body: str, *, services: frozenset[str] = frozenset()) -> InquiryRequest
InquiryRequest(items: tuple[InquiryItem, ...], rejected: tuple[RejectedLine, ...])
InquiryItem(kind: str, value: str, arguments: str = "", choice: str | None = None)
RejectedLine(line_number: int, text: str, reason: str)
```

`services` is a `frozenset` or `set` of verbs matching
`[A-Z][A-Z0-9_-]{0,31}`, never `LIST` or `UPDATE_CAT`; a wrong type
raises `TypeError` and a bad verb `ValueError`, both before the request
is read. A service line has `kind="service"`, `value` the verb,
`arguments` (at most 200 characters), `choice` (at most 80, or `None`).
`InquiryError` is raised for a bad subject, empty body or empty request.
`service_for` returns only `INQUIRY` and `QTH`.

**Service address and native commands** (`src/federated_cms/inquiry.py`,
`docs/INQUIRY-ADAPTER.md`, "Service address"):

```python
DEFAULT_SERVICE_ADDRESS = "FCMS"
RESERVED_ADDRESSES: frozenset[str]   # INQUIRY QTH SERVICE SYSTEM OPTIONS POSTMASTER MAILER-DAEMON
check_service_address(address: str) -> str
parse_commands(body: str, *, services: frozenset[str]) -> InquiryRequest
```

`parse_commands` reads only the lines above Express's quoted-original
separator; every item is kind `service`; other lines are rejected as
`unknown command`, `inquiry item` or `invalid characters`; an empty body
gives an empty request, not an error.

**Menu replies** (`src/federated_cms/menu_reply.py`, `docs/MENU-REPLY.md`):

```python
parse_menu_reply(message: bytes, *, address: str = "FCMS") -> MenuReply
MenuReply(code: str, numbers: tuple[int, ...], more: bool)
MenuReplyError(ValueError)   # .reason, one of the listed reasons; .code, the subject's code or None
```

`message` is the whole MIME message; anything else raises `TypeError`.
`address` is the service address; it is checked with
`check_service_address`, and `SERVICE` is refused.
Only `MenuReplyError` escapes for bytes. `numbers` are sorted, unique,
1 to 999, at most 20; empty when `more`.

**Formatters** (`src/federated_cms/wx_format.py`, `docs/WX-FORMATTERS.md`). They take
parsed JSON (`json.loads` results) and timezone-aware times, and return
`str`: ASCII, lines of at most `LINE_MAX` (69), attribution included.
They raise only `WxFormatError(ValueError)`.

```python
format_wx(points: dict, forecast: dict, *, place: str, node: str, now: datetime, full: bool = False) -> str
format_alerts(alerts: dict, *, place: str, node: str, checked_at: datetime, limit: int = 5) -> str
format_alert_full(alerts: dict, number: int, *, place: str, node: str, checked_at: datetime) -> str
format_solar(*, fetched_at: datetime, now: datetime, flux=None, kp_3h=None, kp_1m=None,
             scales=None, xray=None, forecast_3day=None, daily_indices=None) -> str
format_nws_error(service: str, problem: dict | None, *, place: str, node: str,
                 cached: str | None = None, cached_at: datetime | None = None,
                 now: datetime | None = None) -> str
xray_class(flux: int | float) -> str
```

**What the robot owns:**

- The verb table and the `services` set passed to `parse_commands` and
  `parse_inquiry` (configured verbs plus `HELP`), `HELP` output, and the
  pointer reply for commands sent to `INQUIRY`.
- Routing by recipient (service address, `INQUIRY`, `QTH`) and the stored raw
  message handed to `parse_menu_reply`.
- Menu codes, the `robot_menus` rows, number-to-command lookup, expiry,
  paging and `MORE`, and rendering the code and reply instructions. A
  `Menu` is built by the handler, not by the formatters, which return
  text only: `format_wx` prints options `WX <place> | FULL` and
  `WX <place> | ALERTS`; `format_alerts` prints `ALERTS <place> | k` for
  each block shown (at most `limit`). The handler builds the matching
  `Menu` with `listed_in_body=True` from the same commands.
- Turning a formatter's `str` into a `ReplyPart`: the subject label (for
  example `WX 43.6,-70.7`, cut to 80 characters), splitting, and the
  place label passed as `place`, which is also a valid command argument.
- Fetching, caching, the stale-data choice and passing `cached` and
  `cached_at` to `format_nws_error`, and fetching the seventh SOLAR
  product `daily_indices` (`text/daily-solar-indices.txt`) with the other
  six.
- Item-level errors for non-NWS failures, and everything else in this
  document.

## Decisions

Made on 2026-11-25:

1. Same-session replies for local answers: on, waiting up to 2 s.
2. Reply `Type:`: `Private` until the lab shows what Express does with
   `Service` or `System`.
3. `QTH`: no reply to a valid report.
4. Default limits: those in section 6 (32 items per request, 10 requests
   per hour, 100 messages and 1 MB per day per callsign).
5. No "queued" reply for a request waiting on the Internet.
6. URL items: on by default; the sysop can turn them off.
7. `WIKI` and `NEWS` split long content; other services truncate with
   the extractor's note.
8. Two layers. The node's own syntax (`HELP`, services, menus and menu
   replies) goes to its service address, `node.service_address`, default
   `FCMS`. `INQUIRY` and `QTH` take what Winlink's services take and
   nothing more; a command sent to `INQUIRY` gets a pointer reply. Every
   reply comes from the service address. Mail to `SERVICE` is not
   answered.

## Open questions

1. With federation, an item a node cannot fetch could become a request
   envelope to a peer (`docs/REQUEST-ENVELOPE-v0.1.md`) filling the same
   cache. Not designed here.
