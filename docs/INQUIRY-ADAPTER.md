# Winlink-style service messages: parser contract

`federated_cms.inquiry` recognizes the service addresses Winlink clients
already use and parses their message bodies. It does no I/O and makes no
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

`OPTIONS` (account settings, FAQ Q11650) is not handled yet.

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

So a catalog entry is an ID mapped to a source. Our server answers from
`SERVICE`, one reply per item, with the extracted text as the body. Replies
use plain ASCII where possible so the charset mismatch cannot occur.

## Service commands (`INQUIRY`)

A body line may be a service command such as `WIKI Mercury | 2`, handled by
a node service instead of the catalog. Design: `INTERACTIVE-SERVICES.md`
(branch `docs/interactive-services`, "Requests"). This section is the parser
contract only; the parser does not know what any verb does. Tests:
`tests/test_inquiry.py`, class `ServiceCommandTests`.

### Signature

```python
def parse_inquiry(subject: str, body: str, *, services: frozenset[str] = frozenset()) -> InquiryRequest
```

`services` is keyword-only. The default (empty) behaves exactly as before:
with no services configured, no line is a service command.

- `services` must be a `frozenset` or `set`; any other type (including a
  bare `str`, which would iterate into single letters) raises `TypeError`.
- Each verb must match `[A-Z][A-Z0-9_-]{0,31}` (ASCII upper case only), and
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
parsing continues, `RejectedLine.text` is the line cut to 80 characters with
control characters replaced by `?`. Reasons, checked in this order, first
match wins:

| Reason | When |
| --- | --- |
| `invalid characters` | any non-ASCII character or control character (C0 other than tab, DEL) in the line after comment removal |
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
