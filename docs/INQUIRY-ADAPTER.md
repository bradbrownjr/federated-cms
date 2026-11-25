# Service messages: parser contract

`federated_cms.inquiry` recognizes the addresses Winlink clients already
use for inquiries and position reports (the compatibility layer), checks
the node's own service address (the native layer), and parses the message
bodies of both. The two layers are described in
[INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md#two-layers). It does no I/O and makes no
authorization decision; a mailbox adapter calls it after a B2 message is
accepted. Tests: `tests/test_inquiry.py`. Menu replies from clients are read by
`federated_cms.menu_reply`, see [MENU-REPLY.md](MENU-REPLY.md).

## Addresses

The domain is ignored and optional. `INQUIRY`, `INQUIRY@winlink.org` and
`INQUIRY@mainepacketradio.org` all reach the local inquiry service. A leading
`SMTP:` (used in B2 `To:` headers) is removed first. Matching is
case-insensitive on the whole local part.

- Matching is ASCII only. Upper-casing applies to ASCII letters alone, and a
  local part or `SMTP:` prefix containing any non-ASCII character (dotless
  `ı`, long `ſ`, fullwidth letters) matches nothing: `service_for` returns
  `None`.
- `service_for` and `address_local_part` take one address. The caller splits
  recipient lists and strips display names; text such as `Name <INQUIRY@x>`,
  `"INQUIRY"@x` or `INQUIRY.X@x` is not the local part `INQUIRY`.

| Local part | Service | Source |
| --- | --- | --- |
| `INQUIRY` | Catalog requests | Winlink FAQ Q820, Q830 (pp. 46–47) |
| `QTH` | Position reports | Winlink FAQ Q7560 (p. 96); wl2k-go `catalog/position_report.go` |

`OPTIONS` (account settings, FAQ Q11650) is not handled. `service_for`
does not return the node's service address; the robot matches that with
`address_local_part` against its configuration (see [Service
address](#service-address)).

## Catalog request (`INQUIRY`)

- Subject: `REQUEST`, case-insensitive (ASCII only), surrounding whitespace
  ignored. A leading `//WL2K ` is tolerated. Anything else, including any
  non-ASCII subject or one containing a lone surrogate, is an `InquiryError`.
- Body: at most 8192 UTF-8 bytes. The check compares the character count
  first and never raises anything but `InquiryError`; a body containing a lone
  surrogate is an `InquiryError`. A leading U+FEFF (byte order mark) is
  removed before parsing. One item per line; CRLF, LF or CR. Leading and
  trailing whitespace is ignored and blank lines are skipped.
- Comments: `;` starts a comment to the end of the line. On a line that
  starts with `http://` or `https://`, `;` starts a comment only when
  preceded by whitespace, so `https://x.org/a;b=1` is one URL.
- Item kinds:
  - `LIST` → kind `list`; `UPDATE_CAT` → kind `update_catalog`.
  - A line starting with `http://` or `https://` (any case, ASCII only, so
    `httpſ://` is not a URL) → kind `url`, original case kept. No whitespace;
    at most 2048 characters.
  - Otherwise a catalog ID `[A-Z0-9][A-Z0-9_.-]{0,63}` after ASCII
    upper-casing → kind `catalog`. Examples: `PROP_WWV`, `PUB_PACKET`,
    `FZAK51.PAFC`, `USWXRAD.GIF`.
- A line that fits none of these is reported in `rejected` with reason
  `invalid item`; parsing continues. This includes any line containing a
  non-ASCII character (`lıst`, `PUBßX`) and any line containing a control
  character (C0 other than tab, or DEL). In `RejectedLine.text`, control
  characters are replaced with `?` so replies never echo them.
- Repeated items are kept once. After 32 accepted items, further items are
  rejected with reason `too many items`; a repeat of an accepted item is
  still dropped silently.
- A body with no items and no rejected lines is an `InquiryError`.

`;` handling and case-insensitive IDs come from user reports, not Winlink
documentation.

### Observed exchange (production CMS, 2026-11-24)

Winlink Express 1.8.6.0 over Telnet to `cms.winlink.org`, operator's own
account:

- **Request:** `To: INQUIRY` (no domain), `Subject: REQUEST`, body
  `ME_ZON_YORKC`, 142 bytes uncompressed.
- **Reply:** arrived on the next connection about a minute later. One message
  for one item.
  - `From: SERVICE`. The B2F proposal names the sender as `SERVICE@winlink.org`,
    and Express shows `Source: SYSTEM`.
  - `Subject: INQUIRY - https://tgftp.nws.noaa.gov/data/forecasts/zone/me/mez023.txt`.
    The catalog ID resolves to a source URL, and the subject names the URL,
    not the ID.
  - Body: the fetched NWS text unchanged, followed by a Winlink footer. The
    unchanged text includes product lines such as `Expires:202611251200;;874602`
    and the `$$` terminator. 1,962 bytes uncompressed, 935 compressed; no
    attachment.

A second request with body `UPDATE_CAT` and `INQUIRIES` returned two
messages:

- `Subject: INQUIRY - https://catalog.winlink.org/inquiries.txt`: Winlink's
  own description of inquiries. It says inquiries are accepted only from
  Winlink radio accounts, `LIST` returns the catalog, any Internet URL may be
  requested, and replies must stay under about 120 KB compressed. The public
  file is 1,886 bytes, the same as the `SIZE` field for `INQUIRIES` in the
  third-party copy of `Winlink Queries.txt`, so `SIZE` appears to be the
  source's byte count.
- `Subject: INQUIRY: LIST`: the full catalog, 180,875 bytes uncompressed and
  42,678 compressed. `UPDATE_CAT` returned the whole list, not a delta, and
  the subject format differs from URL replies.

Express stores received messages as MIME files
(`C:\RMS Express\<CALL>\Messages\<MID>.mime`). The stored reply has
`From: SERVICE@winlink.org`, `To: KC1JMH@winlink.org`, `X-Source: SYSTEM`, and
a `text/plain; charset="iso-8859-1"` quoted-printable body. Those are Express's
MIME conversion; the B2 headers on the wire (for example `Mbo:` and `Type:`)
are not in that file. The `INQUIRIES` reply also shows a stray `Â` where a
UTF-8 non-breaking space was decoded as ISO-8859-1.

### Express catalog file

After that `LIST` reply, Express rewrote
`C:\RMS Express\<CALL>\Data\Winlink Queries.txt` (121,538 bytes). Each line is
now `CATEGORY|ID|DESCRIPTION|SIZE|DATE`, for example
`WX_US_ME|ME_ZON_YORKC|Coastal York County Maine Zone Forecast|1344|2025/10/02`.
A 2017 copy of the same file has four fields with no date, and is UTF-8 with a
BOM and CRLF line endings. Descriptions include non-ASCII text such as
`für` and `Québec`.

- The file holds no URLs. The ID-to-source mapping exists only on the server,
  so ours needs its own table.
- `SIZE` appears to be the last measured reply size. Some entries look broken
  at the source: many are `0`, and several single-county zone forecasts list
  42,537 bytes, against roughly 1,500 for their neighbours.
- The `LIST` reply (180,875 bytes) is larger than the file Express wrote
  (121,538 bytes). Whether the reply carries extra fields Express drops is
  unknown. The test is to send our own `LIST` reply from the lab and check
  whether Express updates its catalog from it.
- `UPDATE_CAT` is also a catalog ID (`WL2K_HELP|UPDATE_CAT|How to update the
  catalog list`), but sending it returned the catalog itself, matching the
  parser's special handling.

So a catalog entry is an ID mapped to a source. The node answers from its
own service address, one reply per item, with the extracted text as the
body. Replies
use plain ASCII where possible so the charset mismatch cannot occur.

## Service commands

A body line may be a service command such as `WIKI Mercury | 2`. Commands
belong to the native layer: they run when sent to the service address
(`parse_commands`, [below](#service-address)). In `INQUIRY` mail,
`parse_inquiry` still recognises them, so the robot can list them in one
reply pointing to the service address instead of reporting them as bad
catalog IDs; they are not run (design:
[INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md#compatibility-requests)).
This section is the grammar and the `parse_inquiry` contract; the parser
does not know what any verb does. Tests: `tests/test_inquiry.py`, class
`ServiceCommandTests`.

### Signature

```python
def parse_inquiry(subject: str, body: str, *, services: frozenset[str] = frozenset()) -> InquiryRequest
```

`services` is keyword-only. The default (empty) behaves exactly as before:
with no services configured, no line is a service command.

- `services` must be a `frozenset` or `set`; any other type (including a
  bare `str`, which would iterate into single letters) raises `TypeError`.
- Each verb must be a `str` (anything else raises `TypeError`) and match `[A-Z][A-Z0-9_-]{0,31}` (ASCII upper case only), and
  `LIST` and `UPDATE_CAT` are not allowed. A bad verb raises `ValueError`
  that is not an `InquiryError`. `services` is checked first, before the
  subject and body, so a bad configuration never looks like a bad request.

### Result

`InquiryItem` gains two fields after the existing two, both with defaults, so
`InquiryItem("catalog", "X")` still constructs and compares as before:

```python
@dataclass(frozen=True)
class InquiryItem:
    kind: str
    value: str
    arguments: str = ""
    choice: str | None = None
```

For a service command: `kind` is `"service"`, `value` is the verb upper-cased
(ASCII), `arguments` is the collapsed argument text (`""` when none) with its
case kept, and `choice` is the collapsed choice (case kept) or `None` when the
line has no `|`. Every other kind keeps `arguments == ""` and `choice is None`.

### Line form

`VERB [arguments] [| choice]`, read after comments are removed.

- **Comments:** unchanged. `;` starts a comment on a line that does not start
  with `http://` or `https://`, so a service line cannot carry a `;` in its
  arguments or choice. That costs titles containing `;`, which are rare and
  can be requested through a menu choice. The rule is kept so every line
  type has one comment rule; a node needing `;` in arguments would need a
  separate decision.
- **Verb:** the text from the start of the stripped line up to the first
  space, tab or `|`, upper-cased with ASCII rules, must equal an entry of
  `services` exactly. `WIKI|2`, `wiki`, `WIKI\tMercury` are `WIKI`; `WIKIS`
  and `WIKI.X` are not (they stay catalog IDs as before) and neither is
  `w\u0131ki` (an `invalid item`). A service verb wins over a catalog ID of the same
  name, so `WIKI` alone is a service command, not catalog ID `WIKI`.
- **Arguments:** the text between the verb and the first `|` (or the end of
  the line). Spaces and tabs are collapsed to one space and the ends trimmed.
  It may be empty. After collapsing it holds ASCII printable characters only
  (`0x20`-`0x7E`) and at most 200 of them.
- **Choice:** the text after the first `|`, collapsed the same way. It must
  not be empty, must not contain another `|`, holds ASCII printable
  characters only and is at most 80 characters.
- The whole line may be 8192 bytes at most like any body; the limits above
  are checked after collapsing, so spaces do not count twice.

### Rejected lines

A service line that breaks a rule goes to `rejected` like any bad line,
parsing continues, `RejectedLine.text` is the line after comment removal and
trimming (not collapsed, so `WIKI   a | b | c ; x` shows `WIKI   a | b | c`),
cut to 80 characters with control characters replaced by `?`. Reasons, checked in this order, first
match wins:

| Reason | When |
| --- | --- |
| `invalid characters` | any non-ASCII character (including U+0085 and U+2028) or control character (C0 other than tab, such as `\x1c`, and DEL) in the line after comment removal |
| `arguments too long` | more than 200 characters after collapsing |
| `multiple choices` | a second `|` after the first |
| `empty choice` | `|` present, choice empty after collapsing |
| `choice too long` | more than 80 characters after collapsing |

Lines that are not service commands keep reason `invalid item`, including a
line whose first word only looks like a verb (`WIKIS x`, `w\u0131ki x`).

### Duplicates and the item limit

- Two commands are duplicates when verb, arguments and choice are all equal
  after collapsing. Case matters in arguments and choice (`WIKI Mercury` and
  `WIKI mercury` are two commands; the service decides how to match). A
  duplicate is dropped silently, even beyond the limit.
- Service commands count toward the 32-item limit with every other kind. The
  33rd distinct accepted item is rejected with `too many items`.
- A body of only service lines is not empty. An empty request, a bad subject
  and an oversized body raise `InquiryError` as before.

## Service address

The native layer's address: `FCMS` unless the sysop sets another. Tests:
`tests/test_inquiry.py`, classes `ServiceAddressTests` and
`NativeCommandTests`.

The implementation changes `src/federated_cms/inquiry.py` (the names
below) and `src/federated_cms/menu_reply.py` (its `address` argument,
[MENU-REPLY.md](MENU-REPLY.md#api)) and no other file.

```python
DEFAULT_SERVICE_ADDRESS = "FCMS"
RESERVED_ADDRESSES = frozenset({"INQUIRY", "QTH", "SERVICE", "SYSTEM", "OPTIONS", "POSTMASTER", "MAILER-DAEMON"})

def check_service_address(address: str) -> str
def parse_commands(body: str, *, services: frozenset[str]) -> InquiryRequest
```

### `check_service_address`

Returns `address` unchanged when it is valid. A value that is not a `str`
raises `TypeError`; an invalid one raises `ValueError`. Valid means all of:

- 3 to 12 characters;
- ASCII upper-case letters only, or letters, one `-`, then letters and
  digits: the full match of `[A-Z]+(-[A-Z0-9]+)?`;
- not in `RESERVED_ADDRESSES`.

The form is Winlink's rule for tactical addresses: "alpha characters only,
or alpha characters, followed by a dash, followed by alphanumeric
characters", at most 12 characters including the dash (Winlink FAQ,
2023-03-11, Q7030, p. 94; also Q360, p. 20). Express users already type
such names, so the address is one a Winlink client can be expected to
address. Because the part before any `-` is letters only, the address can
never equal a callsign, which always contains a digit, so it cannot
collide with a user's account. The minimum of 3 is this project's choice.
Upper case only, so a configuration has one spelling; mail matches it
case-insensitively through `address_local_part`.

UNVERIFIED, and tested in the lab ([ON-AIR-TESTS.md](ON-AIR-TESTS.md)):
whether Express accepts an address that has no Winlink account, written
bare (`FCMS`) or with another domain (`FCMS@lab.example`), and what it puts
in `To:`. wl2k-go, Pat's library, does no check: `AddressFromString` keeps
a bare name, upper-cased, and turns a name with a domain other than
`winlink.org` into `SMTP:<name>@<domain>` (`fbb/message.go` at 1d59b0a).
Either form reduces to the local part through `address_local_part`.

`RESERVED_ADDRESSES` holds the compatibility addresses, Winlink's own
system and account-settings addresses, and the usual mail-system names.
The robot's loop rules use the same set ([INQUIRY-ROBOT.md](INQUIRY-ROBOT.md#mail-loops)).

### `parse_commands`

Reads the body of a message to the service address. The subject is not an
argument: it is not read for commands. The robot first calls
`menu_reply.parse_menu_reply`; only a `no code` result makes the message a
command message ([INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md#native-requests)).

**Arguments**, checked in this order before the body is read:

- `services` is required and keyword-only, with the same rules as in
  `parse_inquiry`: a `set` or `frozenset` of `str` (else `TypeError`),
  each matching `[A-Z][A-Z0-9_-]{0,31}` and not `LIST` or `UPDATE_CAT`
  (else `ValueError` that is not an `InquiryError`). The robot passes the
  configured verbs plus `HELP`.
- `body` must be a `str`, else `TypeError`.

**Body**, each failure an `InquiryError`:

1. More than 65,536 characters, or more than 65,536 bytes as UTF-8:
   `body too large`. The character count is compared first.
2. A lone surrogate anywhere: `invalid body`.
3. A leading U+FEFF is removed.
4. Lines are split at CRLF, LF or CR. Reading stops before the first line
   that, as split and not stripped, matches in full (`re.fullmatch`)
   `----- Message from .+ sent [0-9]{4}/[0-9]{2}/[0-9]{2} [0-9]{2}:[0-9]{2} -----`.
   This is the separator Express writes above a quoted original
   (observed, [INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md#reading-a-reply));
   `menu_reply` uses the same pattern. Any sender name matches. A trailing
   space, a leading space, text before it or non-ASCII digits make it an
   ordinary line.
5. The text before that line (the whole body when there is none) may be at
   most 8,192 bytes as UTF-8: `body too large`. So a Reply that quotes a
   12,000-character article still fits, while the part the user wrote has
   `parse_inquiry`'s limit.

**Lines** before the separator, numbered from 1 (blank and comment lines
count):

- Comments are removed and the line trimmed as in `parse_inquiry`; a line
  that is then empty is skipped.
- A line whose first word is in `services` is a service command, parsed by
  the grammar in [Service commands](#service-commands) with the same
  results and rejection reasons (`invalid characters`, `arguments too
  long`, `multiple choices`, `empty choice`, `choice too long`). For a
  body of command lines, `parse_commands(body, services=s)` equals
  `parse_inquiry("REQUEST", body, services=s)`.
- Any other line is rejected, the first matching reason:

  | Reason | When |
  | --- | --- |
  | `invalid characters` | a non-ASCII character, or a control character (C0 other than tab, and DEL) |
  | `inquiry item` | the line, upper-cased (ASCII), is `LIST` or `UPDATE_CAT`, or starts with `HTTP://` or `HTTPS://` |
  | `unknown command` | anything else, including catalog IDs, bare numbers and `MORE` |

- `RejectedLine.text` is formed as in `parse_inquiry`: after comment
  removal and trimming, cut to 80 characters, control characters replaced
  with `?`.
- Duplicates are dropped and the 33rd distinct command is rejected with
  `too many items`, as in `parse_inquiry`. Rejected lines do not count
  toward the limit.

**Result:** an `InquiryRequest` whose items all have kind `service`. A
body with nothing to read (empty, blank lines, comments, or a separator
first) returns `InquiryRequest((), ())` and raises nothing; the robot
answers it with the `HELP` reply.

## Position report (`QTH`)

Body layout written by wl2k-go (`catalog/position_report.go`, commit
`1d59b0a`), one `KEY: value` per line, keys case-insensitive (ASCII only),
at most 8192 bytes. The size check compares the character count first and
never raises anything but `PositionReportError`; a body containing a lone
surrogate is a `PositionReportError`. A leading U+FEFF is removed.

| Key | Format | Required |
| --- | --- | --- |
| `DATE` | `YYYY/MM/DD HH:MM`, UTC | yes |
| `LATITUDE` | `DD-MM.m` to `DD-MM.mmmm` + `N`/`S` | with `LONGITUDE` |
| `LONGITUDE` | `DDD-MM.m` to `DDD-MM.mmmm` + `E`/`W` | with `LATITUDE` |
| `SPEED` | non-negative decimal, at most 10000 (unit unspecified) | no |
| `COURSE` | 1–3 digits, 0–359, + `T` (true) or `M` (magnetic) | no |
| `COMMENT` | up to 80 characters | no |

The hemisphere letter may be missing only when the value is zero; wl2k-go
writes a space there. Coordinates, hemisphere letters and numbers must be
ASCII. Minutes carry 1 to 4 decimal places and must be below 60, with one
exception: exactly 60 with all-zero decimals (`43-60.0000N`) carries to the
next degree, because wl2k-go formats minutes with `%07.4f` and can round
59.99996 up to `60.0000`. The given or carried value must stay within 90
degrees of latitude and 180 of longitude (`90-00.0000` and `180-00.0000` are
valid; `89-60.0000` carries to exactly 90 and is valid; `90-00.0001` and
`90-60.0000` are not). Zero is returned as `0.0`, never `-0.0`.

`SPEED` must be finite and at most 10000; anything else makes the report
invalid.

A repeated known key (`DATE`, `LATITUDE`, `LONGITUDE`, `SPEED`, `COURSE`,
`COMMENT`) is an error. Unknown keys, repeated or not, are ignored.

## Sources

- [Winlink FAQ, 2023-03-11](https://winlink.org/sites/default/files/download/wl2k_faq_20230311.pdf)
- [wl2k-go `catalog/position_report.go`](https://github.com/la5nta/wl2k-go/blob/1d59b0a731673d1f9dbea093ff9e34a6ffdc43ea/catalog/position_report.go)
- [wl2k-go `fbb/message.go`](https://github.com/la5nta/wl2k-go/blob/1d59b0a731673d1f9dbea093ff9e34a6ffdc43ea/fbb/message.go) (`DateLayout`, message types)
