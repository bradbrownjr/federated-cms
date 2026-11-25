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

1. **Byte order mark.** A UTF-8 BOM means UTF-8 (`"utf-8"`); a UTF-16
   LE or BE BOM means UTF-16 (`"utf-16"`). The BOM is not part of the
   text. A BOM overrides any declared charset. Bytes after a BOM that do
   not decode become U+FFFD (`errors="replace"`, as the WHATWG decoders
   do); this step never raises.
2. **`charset` parameter of `content_type`**, case-insensitive, quotes
   removed, e.g. `text/html; charset="ISO-8859-1"`.
3. **HTML meta declaration**, only when the content is HTML (the
   *Kind* rule of `extract`, applied to the bytes read as ASCII): the first `<meta charset=...>` or
   `<meta http-equiv="Content-Type" content="...; charset=...">` within
   the first 4096 bytes. An attribute written without a value
   (`<meta http-equiv>`, `<meta charset>`) counts as an empty string. A
   meta declaration of UTF-16LE or UTF-16BE means UTF-8 (HTML Standard,
   "prescan a byte stream to determine its encoding",
   <https://html.spec.whatwg.org/multipage/parsing.html#prescan-a-byte-stream-to-determine-its-encoding>).
4. **UTF-8**, if the bytes decode strictly as UTF-8.
5. **windows-1252** otherwise.

**Labels.** A declared label is matched against the fixed table below,
after removing leading and trailing ASCII whitespace and lowercasing
ASCII letters (WHATWG Encoding Standard, "get an encoding",
<https://encoding.spec.whatwg.org/#names-and-labels>; the labels are
copied from its `encodings.json`). A label not in the table is unknown,
even when Python has a codec of that name: `punycode`, `unicode_escape`,
`cp037`, `rot13` and the like are never used. The WHATWG `replacement`
and `x-user-defined` encodings are left out, so their labels are
unknown too.

| WHATWG encoding | Python codec | Labels |
|---|---|---|
| UTF-8 | `utf-8` | `unicode-1-1-utf-8` `unicode11utf8` `unicode20utf8` `utf-8` `utf8` `x-unicode20utf8` |
| UTF-16LE | `utf-16-le` | `csunicode` `iso-10646-ucs-2` `ucs-2` `unicode` `unicodefeff` `utf-16` `utf-16le` |
| UTF-16BE | `utf-16-be` | `unicodefffe` `utf-16be` |
| IBM866 | `cp866` | `866` `cp866` `csibm866` `ibm866` |
| ISO-8859-2 | `iso8859-2` | `csisolatin2` `iso-8859-2` `iso-ir-101` `iso8859-2` `iso88592` `iso_8859-2` `iso_8859-2:1987` `l2` `latin2` |
| ISO-8859-3 | `iso8859-3` | `csisolatin3` `iso-8859-3` `iso-ir-109` `iso8859-3` `iso88593` `iso_8859-3` `iso_8859-3:1988` `l3` `latin3` |
| ISO-8859-4 | `iso8859-4` | `csisolatin4` `iso-8859-4` `iso-ir-110` `iso8859-4` `iso88594` `iso_8859-4` `iso_8859-4:1988` `l4` `latin4` |
| ISO-8859-5 | `iso8859-5` | `csisolatincyrillic` `cyrillic` `iso-8859-5` `iso-ir-144` `iso8859-5` `iso88595` `iso_8859-5` `iso_8859-5:1988` |
| ISO-8859-6 | `iso8859-6` | `arabic` `asmo-708` `csiso88596e` `csiso88596i` `csisolatinarabic` `ecma-114` `iso-8859-6` `iso-8859-6-e` `iso-8859-6-i` `iso-ir-127` `iso8859-6` `iso88596` `iso_8859-6` `iso_8859-6:1987` |
| ISO-8859-7 | `iso8859-7` | `csisolatingreek` `ecma-118` `elot_928` `greek` `greek8` `iso-8859-7` `iso-ir-126` `iso8859-7` `iso88597` `iso_8859-7` `iso_8859-7:1987` `sun_eu_greek` |
| ISO-8859-8 | `iso8859-8` | `csiso88598e` `csisolatinhebrew` `hebrew` `iso-8859-8` `iso-8859-8-e` `iso-ir-138` `iso8859-8` `iso88598` `iso_8859-8` `iso_8859-8:1988` `visual` |
| ISO-8859-8-I | `iso8859-8` | `csiso88598i` `iso-8859-8-i` `logical` |
| ISO-8859-10 | `iso8859-10` | `csisolatin6` `iso-8859-10` `iso-ir-157` `iso8859-10` `iso885910` `l6` `latin6` |
| ISO-8859-13 | `iso8859-13` | `iso-8859-13` `iso8859-13` `iso885913` |
| ISO-8859-14 | `iso8859-14` | `iso-8859-14` `iso8859-14` `iso885914` |
| ISO-8859-15 | `iso8859-15` | `csisolatin9` `iso-8859-15` `iso8859-15` `iso885915` `iso_8859-15` `l9` |
| ISO-8859-16 | `iso8859-16` | `iso-8859-16` |
| KOI8-R | `koi8-r` | `cskoi8r` `koi` `koi8` `koi8-r` `koi8_r` |
| KOI8-U | `koi8-u` | `koi8-ru` `koi8-u` |
| macintosh | `mac-roman` | `csmacintosh` `mac` `macintosh` `x-mac-roman` |
| windows-874 | `cp874` | `dos-874` `iso-8859-11` `iso8859-11` `iso885911` `tis-620` `windows-874` |
| windows-1250 | `cp1250` | `cp1250` `windows-1250` `x-cp1250` |
| windows-1251 | `cp1251` | `cp1251` `windows-1251` `x-cp1251` |
| windows-1252 | `cp1252` | `ansi_x3.4-1968` `ascii` `cp1252` `cp819` `csisolatin1` `ibm819` `iso-8859-1` `iso-ir-100` `iso8859-1` `iso88591` `iso_8859-1` `iso_8859-1:1987` `l1` `latin1` `us-ascii` `windows-1252` `x-cp1252` |
| windows-1253 | `cp1253` | `cp1253` `windows-1253` `x-cp1253` |
| windows-1254 | `cp1254` | `cp1254` `csisolatin5` `iso-8859-9` `iso-ir-148` `iso8859-9` `iso88599` `iso_8859-9` `iso_8859-9:1989` `l5` `latin5` `windows-1254` `x-cp1254` |
| windows-1255 | `cp1255` | `cp1255` `windows-1255` `x-cp1255` |
| windows-1256 | `cp1256` | `cp1256` `windows-1256` `x-cp1256` |
| windows-1257 | `cp1257` | `cp1257` `windows-1257` `x-cp1257` |
| windows-1258 | `cp1258` | `cp1258` `windows-1258` `x-cp1258` |
| x-mac-cyrillic | `mac-cyrillic` | `x-mac-cyrillic` `x-mac-ukrainian` |
| GBK | `gb18030` | `chinese` `csgb2312` `csiso58gb231280` `gb2312` `gb_2312` `gb_2312-80` `gbk` `iso-ir-58` `x-gbk` |
| gb18030 | `gb18030` | `gb18030` |
| Big5 | `big5hkscs` | `big5` `big5-hkscs` `cn-big5` `csbig5` `x-x-big5` |
| EUC-JP | `euc_jp` | `cseucpkdfmtjapanese` `euc-jp` `x-euc-jp` |
| ISO-2022-JP | `iso2022_jp` | `csiso2022jp` `iso-2022-jp` |
| Shift_JIS | `cp932` | `csshiftjis` `ms932` `ms_kanji` `shift-jis` `shift_jis` `sjis` `windows-31j` `x-sjis` |
| EUC-KR | `cp949` | `cseuckr` `csksc56011987` `euc-kr` `iso-ir-149` `korean` `ks_c_5601-1987` `ks_c_5601-1989` `ksc5601` `ksc_5601` `windows-949` |

The Python codecs are the nearest standard-library match. WHATWG decodes
GBK with the gb18030 decoder, Big5 includes HKSCS, Shift_JIS is
windows-31j and EUC-KR is windows-949, so those rows use the wider
Python codec. The returned `encoding` is `codecs.lookup()` of the codec
column, e.g. `"cp1254"` for `iso-8859-9`.

The windows-1252 decoder maps the five bytes Python's `cp1252` leaves
undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D) to U+0081, U+008D, U+008F,
U+0090 and U+009D, as the WHATWG index does. It never raises.

An unknown label, or a declared encoding that fails to decode the bytes
strictly, falls through to steps 3 to 5. No step uses
`errors="ignore"`; a failing UTF-8 declaration is retried, not trimmed.
Every table codec decodes in time linear in the input.

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

Both functions first normalise line endings: each run of `\r`
characters followed by `\n` (`\r\n`, and the `\r\r\n` of products as
sent over NOAAPort and EMWIN) becomes one `\n`, and any other `\r`
becomes `\n`. Only `\n` then separates lines.

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

### `html_to_text(markup: str, *, width: int = 69, ascii: bool = False) -> tuple[str | None, str]`

Returns `(title, text)`. With `ascii=False` the text is not converted
to ASCII. With `ascii=True` every piece of text (text nodes and `pre`
content) goes through `to_ascii` before layout, so wrapping, underline
lengths, list indents and the width rule all hold on the ASCII text, and
the title is converted too. `extract` uses `ascii=True`.

NUL characters (U+0000) are removed from `markup` before parsing; they
never produce a line break or any other output. The parser is closed at
the end of the input, so text after the last tag is kept, including an
incomplete character reference (`<p>AT&T` gives `AT&T`, `hi &copy`
gives `hi ©`).

**Title:** the `<title>` text with whitespace collapsed; if empty or
missing, the first `<h1>` text outside dropped elements; otherwise
`None`.

**Root:** the first `<main>` element; else the first `<article>`; else
`<body>`; else the whole document. A `main`, `article` or `body` inside
a dropped element is never the root.

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
nests inside it, and no text inside a dropped element appears anywhere
in the output: not in tables, lists, headings or the title fallback.
Character references are decoded.

**Blocks** are separated by one blank line: `p`, `div`, `section`,
`article`, `main`, `blockquote`, `pre`, `table`, `ul`, `ol`, `dl`,
headings, `hr`, `figure`, and also `html` and `body`, so a document
without a `<body>` tag keeps its block structure. Inline whitespace
collapses to single spaces. Empty blocks produce nothing.

- **Headings:** text on its own line. `h1` is underlined with `=` and
  `h2` with `-`, each as long as the heading line. `h3` to `h6` are
  plain lines.
- **Lists:** one line per `li`, no blank line between items of the same
  list. `ul` items start `* `; `ol` items start `1. `, `2. `, ... (the
  counter restarts per list). Each nesting level indents two spaces,
  up to at most `width // 2` spaces, and a list nested in an item
  follows it with no blank line. Wrapped lines align under the item
  text. `li` elements wrapped in a `div` inside the `ul` or `ol` are
  items of that list.
- **`dl`:** `dt` on its own line; `dd` indented two spaces. `dt` and
  `dd` wrapped in a `div` inside the `dl` (allowed by HTML) are kept the
  same way.
- **`blockquote`:** every output line, including wrapped ones, starts
  `> `.
- **`pre`:** text kept exactly, tabs expanded to 8; not wrapped or
  collapsed. A `br` inside it is a line break. Leading and trailing
  blank lines inside it are removed.
- **`br`:** line break within the block.
- **`hr`:** the line `---`.
- **Tables:** one line per `tr`, cells (`th`, `td`) whitespace-collapsed
  and joined with ` | `; no blank lines between rows. Long rows wrap
  with a two-space continuation indent. A table nested in a cell is
  part of that cell's text and is not printed again as rows of its own.
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

**Cost:** time is linear in the input, with a small per-tag cost that
does not grow with the number of open elements. The limit is relative
to the standard library parser, so it holds on a slow or busy machine:
on each input below, `extract` takes at most 4 times as long as one
`html.parser.HTMLParser(convert_charrefs=True)` `feed` and `close` of the
same input decoded as UTF-8, plus 2 seconds, both timed in the same
process. The inputs are all within the default `max_input_bytes`: 256
nested `<div>` then `<p>` repeated to 2,000,000 bytes; 660,000 `<b>`
tags; 255 nested `<div>` then `<p>a</p>` repeated to 2,000,000 bytes.
The same limit applies to `decode` of 2,000,000 bytes declared as
`charset=punycode` (an unknown label), against a UTF-8 decode plus one
parser pass of the same bytes.

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
4. `html`: `html_to_text(..., width=width, ascii=True)`. `nws`: `clean_nws`. `text`:
   line endings normalised, trailing spaces removed, blank-line runs
   collapsed to one, leading and trailing blank lines removed; lines are
   not rewrapped.
5. `to_ascii` on the text and on the title. A title that is empty
   after `to_ascii` becomes `None`. Then, because `to_ascii` can empty
   a line (one holding only invisible or combining characters), runs of
   blank lines are collapsed to one again and leading and trailing
   blank lines are removed.
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
