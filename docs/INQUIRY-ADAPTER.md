# Winlink-style service messages: parser contract

`federated_cms.inquiry` recognizes the service addresses Winlink clients
already use and parses their message bodies. It does no I/O and makes no
authorization decision; a mailbox adapter calls it after a B2 message is
accepted. Tests: `tests/test_inquiry.py`.

## Addresses

The domain is ignored and optional. `INQUIRY`, `INQUIRY@winlink.org` and
`INQUIRY@mainepacketradio.org` all reach the local inquiry service. A leading
`SMTP:` (used in B2 `To:` headers) is removed first. Matching is
case-insensitive on the whole local part.

| Local part | Service | Source |
| --- | --- | --- |
| `INQUIRY` | Catalog requests | Winlink FAQ Q820, Q830 (pp. 46–47) |
| `QTH` | Position reports | Winlink FAQ Q7560 (p. 96); wl2k-go `catalog/position_report.go` |

`OPTIONS` (account settings, FAQ Q11650) is not handled yet.

## Catalog request (`INQUIRY`)

- Subject: `REQUEST`, case-insensitive, surrounding whitespace ignored. A
  leading `//WL2K ` is tolerated. Anything else is an `InquiryError`.
- Body: at most 8192 UTF-8 bytes. One item per line; CRLF, LF or CR. Leading
  and trailing whitespace is ignored, blank lines are skipped, and `;` starts
  a comment to the end of the line.
- Item kinds:
  - `LIST` → kind `list`; `UPDATE_CAT` → kind `update_catalog`.
  - A line starting with `http://` or `https://` (any case) → kind `url`,
    original case kept. No whitespace; at most 2048 characters.
  - Otherwise a catalog ID `[A-Z0-9][A-Z0-9_.-]{0,63}` after upper-casing →
    kind `catalog`. Examples: `PROP_WWV`, `PUB_PACKET`, `FZAK51.PAFC`,
    `USWXRAD.GIF`.
- A line that fits none of these is reported in `rejected` with reason
  `invalid item`; parsing continues. Repeated items are kept once. After 32
  accepted items, further items are rejected with reason `too many items`.
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

## Position report (`QTH`)

Body layout written by wl2k-go (`catalog/position_report.go`, commit
`1d59b0a`), one `KEY: value` per line, keys case-insensitive, at most 8192
bytes:

| Key | Format | Required |
| --- | --- | --- |
| `DATE` | `YYYY/MM/DD HH:MM`, UTC | yes |
| `LATITUDE` | `DD-MM.mmmm` + `N`/`S` | with `LONGITUDE` |
| `LONGITUDE` | `DDD-MM.mmmm` + `E`/`W` | with `LATITUDE` |
| `SPEED` | non-negative decimal (unit unspecified) | no |
| `COURSE` | 1–3 digits, 0–359, + `T` (true) or `M` (magnetic) | no |
| `COMMENT` | up to 80 characters | no |

The hemisphere letter may be missing only when the value is zero; wl2k-go
writes a space there. Minutes must be below 60, latitude at most 90 degrees,
longitude at most 180. Unknown keys are ignored; a repeated key is an error.

## Sources

- [Winlink FAQ, 2023-03-11](https://winlink.org/sites/default/files/download/wl2k_faq_20230311.pdf)
- [wl2k-go `catalog/position_report.go`](https://github.com/la5nta/wl2k-go/blob/1d59b0a731673d1f9dbea093ff9e34a6ffdc43ea/catalog/position_report.go)
- [wl2k-go `fbb/message.go`](https://github.com/la5nta/wl2k-go/blob/1d59b0a731673d1f9dbea093ff9e34a6ffdc43ea/fbb/message.go) (`DateLayout`, message types)
