# Text extractor (`federated_cms.plaintext`)

**Status:** specification with contract tests (`tests/test_plaintext.py`).

Turns a fetched resource (an NWS text product, a web page or plain text)
into compact ASCII for a mail reply over slow radio links. It has no
network, mail or storage code: the INQUIRY robot fetches, this module
cleans, and the robot sends.

The module imports nothing from the rest of `federated_cms`, so it can
move to a shared package used by bpq-apps and rns-apps (see `STALE.md`).

## Dependencies

Python 3.11+ standard library and **anyascii** 0.3.x (ISC licence, pure
Python; <https://pypi.org/project/anyascii/>). Unidecode, html2text and
text-unidecode are GPL and must not be used.

## API

```python
from federated_cms.plaintext import (
    Document, ExtractError, decode, to_ascii, is_nws_product, clean_nws,
    html_to_text, extract, render,
)
```

`ExtractError` subclasses `ValueError`.

```python
@dataclass(frozen=True)
class Document:
    url: str
    fetched_at: datetime      # timezone-aware, UTC
    kind: str                 # "nws", "html" or "text"
    title: str | None         # ASCII, or None
    text: str                 # ASCII, "\n" line endings, no trailing newline
    truncated: bool
    original_bytes: int       # len(data) as fetched
```

### `decode(data: bytes, content_type: str | None) -> tuple[str, str]`

Returns `(text, encoding)`, where `encoding` is the Python codec name
actually used (`codecs.lookup(x).name`, e.g. `"utf-8"`, `"cp1252"`,
`"utf-16"`). Order:

1. **Byte order mark.** A UTF-8 BOM means UTF-8; a UTF-16 LE or BE BOM
   means UTF-16. The BOM is not part of the text. A BOM overrides any
   declared charset.
2. **`charset` parameter of `content_type`**, case-insensitive, quotes
   removed, e.g. `text/html; charset="ISO-8859-1"`.
3. **HTML meta declaration**, only when the content is HTML (the
   *Kind* rule of `extract`, applied to the bytes read as ASCII): the first `<meta charset=...>` or
   `<meta http-equiv="Content-Type" content="...; charset=...">` within
   the first 4096 bytes.
4. **UTF-8**, if the bytes decode strictly as UTF-8.
5. **windows-1252** otherwise.

Labels follow the WHATWG Encoding Standard
(<https://encoding.spec.whatwg.org/#names-and-labels>): `iso-8859-1`,
`latin1`, `us-ascii` and `ascii` mean windows-1252. The windows-1252
decoder maps the five bytes Python's `cp1252` leaves undefined (0x81,
0x8D, 0x8F, 0x90, 0x9D) to U+0081, U+008D, U+008F, U+0090 and U+009D, as
the WHATWG index does. It never raises.

A declared label that Python does not know, or a declared encoding that
fails to decode the bytes, falls through to steps 3 to 5. No step uses
`errors="ignore"`; a failing UTF-8 declaration is retried, not trimmed.

### `to_ascii(text: str) -> str`

1. `\r\n` and lone `\r` become `\n`.
2. Tabs expand to 8-column stops (`str.expandtabs(8)`).
3. Soft hyphen (U+00AD), zero-width space/joiners (U+200B, U+200C,
   U+200D) and U+FEFF are removed.
4. `°` directly before an ASCII capital letter is removed (`45°F` becomes
   `45F`, `43.6°N` becomes `43.6N`); any other `°` becomes ` deg`.
5. Everything else goes through `anyascii`.
6. Control characters other than `\n` (U+0000 to U+001F and U+007F) are
   removed.
7. Trailing spaces are removed from every line.

The result contains only `\n` and printable ASCII (0x20 to 0x7E).

### NWS text products

`is_nws_product(text) -> bool` is true when, after any leading blank
lines and any leading lines matching `^Expires:\d{12};;\d+$`, the first
line is a WMO abbreviated heading and the next line is an AWIPS
identifier:

- WMO heading: `^[A-Z]{4}\d{2} [A-Z]{4} \d{6}( [A-Z]{3})?$`
  (`ttaaii cccc ddhhmm [bbb]`, NWSI 10-1701 Table 4).
- AWIPS identifier: `^[A-Z0-9]{4,6} *$` (`nnnxxx`, Table 5).

`clean_nws(text) -> str` (input already decoded) removes the coding
that is for machines, keeps every line a reader needs, and does not
rewrap (products are already at most 69 characters a line, NWSI
10-1701 section 3.3):

1. Lines matching `^Expires:\d{12};;\d+$`. These are added by the NWS
   file server (tgftp.nws.noaa.gov), not by the product itself.
2. The WMO heading line, and the line directly after it when that is
   an AWIPS identifier.
3. UGC strings: a line starting `[A-Z]{2}[CZ]\d{3}` and made only of
   `[A-Z0-9>-]`, plus any following such lines, up to and including the
   line that ends in `-\d{6}-` (the product expiration time, section
   3.9.2). Example: `MEZ023-251500-`.
4. VTEC lines: lines matching `^/[^ ]+/$`.
5. Lines that are exactly `$$` or `&&` (end of product or segment, and
   content-type separator; sections 5.8 and 5.4).
6. Trailing spaces on every line.
7. Runs of blank lines become one blank line; leading and trailing
   blank lines are removed.

Zone names, "Including the cities of", issuance times, forecast text
and any forecaster identifier after `$$` stay.

### `html_to_text(markup: str, *, width: int = 69) -> tuple[str | None, str]`

Returns `(title, text)`. Not yet ASCII; `extract` does that.

**Title:** the `<title>` text with whitespace collapsed; if empty or
missing, the first `<h1>` text; otherwise `None`.

**Root:** the first `<main>` element; else the first `<article>`; else
`<body>`; else the whole document.

**Dropped with their content:** `head`, `title` (used only for the title), `script`, `style`, `noscript`,
`template`, `svg`, `iframe`, `nav`, `aside`, `form`, `button`, `select`,
`textarea`, `dialog`, any element with a `hidden` attribute or
`aria-hidden="true"`, and any element whose `role` is one of the WAI-ARIA
landmarks `navigation`, `banner`, `contentinfo`, `complementary` or
`search`. `header` and `footer` are dropped too, unless
they are inside an `article` or `main` element.

**Parsing:** a real element stack. Void elements (`area`, `base`, `br`,
`col`, `embed`, `hr`, `img`, `input`, `link`, `meta`, `source`,
`track`, `wbr`) never open a scope. An end tag closes the nearest open
element of that name and everything opened after it; an end tag with
no open element of that name is ignored. As in HTML, some start tags
close an open element first: `p` closes an open `p`; `li` closes an
open `li` of the same list; `dt` or `dd` closes an open `dt` or `dd`;
`tr` closes an open `tr`; `td` or `th` closes an open `td` or `th`.
Nesting is limited to 256 open elements: deeper start tags open no
scope and their text joins the enclosing element, except that a
dropped element still opens one (so `<script>` content stays out). A dropped element stays
dropped until its own end tag (or the end of the document), whatever
nests inside it. Character references are decoded.

**Blocks** are separated by one blank line: `p`, `div`, `section`,
`article`, `main`, `blockquote`, `pre`, `table`, `ul`, `ol`, `dl`,
headings, `hr`, `figure`. Inline whitespace collapses to single spaces.
Empty blocks produce nothing.

- **Headings:** text on its own line. `h1` is underlined with `=` and
  `h2` with `-`, each as long as the heading line. `h3` to `h6` are
  plain lines.
- **Lists:** one line per `li`, no blank line between items of the same
  list. `ul` items start `* `; `ol` items start `1. `, `2. `, ... (the
  counter restarts per list). Each nesting level indents two spaces,
  and a list nested in an item follows it with no blank line. Wrapped
  lines align under the item text.
- **`dl`:** `dt` on its own line; `dd` indented two spaces.
- **`blockquote`:** every output line, including wrapped ones, starts
  `> `.
- **`pre`:** text kept exactly, tabs expanded to 8; not wrapped or
  collapsed. Leading and trailing blank lines inside it are removed.
- **`br`:** line break within the block.
- **`hr`:** the line `---`.
- **Tables:** one line per `tr`, cells (`th`, `td`) whitespace-collapsed
  and joined with ` | `; no blank lines between rows. Long rows wrap
  with a two-space continuation indent.
- **`img`:** dropped. On real pages the alt text repeats nearby text or
  names a logo, and costs airtime.
- **Links:** the link text stays; the URL is dropped. Two links with
  nothing between them are separated by a space (pages often rely on
  CSS for that gap).
- **Blocks inside an item:** within an `li`, `dt`, `dd` or `blockquote`,
  each nested block element (any block above, or `li`, `tr`, `td`, `th`)
  starts a new line, indented like the item's wrapped lines. Within a
  table cell or heading it is separated by a space instead.

**Wrapping:** paragraphs, list items, `dd`, table rows and quotes wrap
to `width` with `textwrap`, `break_long_words=False` and
`break_on_hyphens=False`. Only `pre` lines and single words longer than
`width` may exceed it.

The text ends without trailing blank lines.

### `extract(data, *, url, fetched_at, content_type=None, width=69, max_chars=12000, max_input_bytes=2_000_000) -> Document`

1. Raise `ExtractError` when `len(data) > max_input_bytes`, `width < 20`,
   `max_chars < 200`, or `fetched_at` is naive. An aware `fetched_at` is
   converted to UTC.
2. **Kind:** `html` when the media type (before `;`, case-insensitive)
   is `text/html` or `application/xhtml+xml`, or when there is no
   content type and the text, after leading whitespace, starts with
   `<!doctype html` or `<html` (case-insensitive). Otherwise `nws` when
   `is_nws_product` is true, else `text`.
3. Decode with `decode(data, content_type)`.
4. `html`: `html_to_text(..., width=width)`. `nws`: `clean_nws`. `text`:
   line endings normalised, trailing spaces removed, blank-line runs
   collapsed to one, leading and trailing blank lines removed; lines are
   not rewrapped.
5. `to_ascii` on the text and on the title.
6. **Truncation:** when the text is longer than `max_chars`, cut at the
   last `\n` at or before `max_chars` (or at `max_chars` if there is
   none), remove trailing whitespace, and set `truncated=True`.

### `render(doc: Document) -> str`

The reply body, `\n` line endings, ending with exactly one `\n`:

```
<title>                       (only when title is not None)
Source: <url>
Fetched: YYYY-MM-DD HH:MM UTC
                              (blank line)
<text>
                              (blank line, only when truncated)
[Truncated: the full text is longer than this reply.]
```

Non-ASCII characters in the URL are percent-encoded as UTF-8
(`urllib.parse.quote` with `safe` set to the RFC 3986 reserved and
unreserved characters plus `%`), so an existing escape is not
re-encoded.

Converting `\n` to CRLF for B2F is the mail layer's job, not this
module's.

## Sources

- NWSI 10-1701, *Text Product Formats and Codes*, 29 October 2019
  (<https://www.weather.gov/media/directives/010_pdfs/pd01017001curr.pdf>):
  WMO heading (4.1.2), AWIPS identifier (4.1.3), 69-character lines
  (3.3), product expiration time (3.9.2), `&&` (5.4), `$$` (5.8), and the
  move to mixed case (2.1).
- WHATWG Encoding Standard, names and labels, and
  `index-windows-1252.txt`.
- Observed: `tgftp.nws.noaa.gov/data/forecasts/zone/me/mez023.txt`,
  2026-11-25, saved as `tests/fixtures/plaintext/mez023.txt`. NWS
  products are US Government works.
- rns-apps `lib/htmltext.py` (CC0) informed the HTML rules; this spec
  uses an element stack instead of its skip counter and adds tables,
  `pre`, headings and lists. No code is copied from it.

## Not in this version

RSS/Atom feeds, link references, readability scoring of `div` soup, and
fetching. Each needs its own spec and tests.
