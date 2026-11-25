# Feed reader (`federated_cms.feeds`)

**Status:** specification with contract tests (`tests/test_feeds.py`).

Parses an RSS 2.0 or Atom feed that has already been fetched. It has no
network, mail or storage code, and imports nothing from the rest of
`federated_cms`, so it can move to a shared package with the text
extractor. The `NEWS` service ([INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md))
fetches, calls this module, and passes summaries and content to the text
extractor.

Python 3.11+ standard library only.

## API

```python
from federated_cms.feeds import Body, Item, Feed, FeedError, parse_feed, parse_date
```

`FeedError` subclasses `ValueError`. All three dataclasses are frozen.

```python
@dataclass(frozen=True)
class Body:
    value: str        # as given in the feed, surrounding whitespace removed
    is_html: bool     # True: pass to the HTML extractor; False: plain text

@dataclass(frozen=True)
class Item:
    title: str | None
    link: str | None            # absolute http or https URL
    id: str | None              # RSS guid or Atom id
    published: datetime | None  # timezone-aware, UTC
    summary: Body | None
    content: Body | None        # full text, when the feed carries it

@dataclass(frozen=True)
class Feed:
    kind: str                   # "rss" or "atom"
    title: str | None
    link: str | None
    items: tuple[Item, ...]     # document order
    truncated: bool             # True when items were dropped by max_items
```

### `parse_feed(data: bytes, *, url: str, max_bytes: int = 2_000_000, max_items: int = 200) -> Feed`

`url` is where the feed was fetched from; relative links resolve against
it.

**Safety.** Every check raises `FeedError`; no other exception type
escapes for any input.

1. `len(data) > max_bytes`. The caller applies the same limit while
   decompressing, before calling.
2. Any document type declaration (`<!DOCTYPE`), wherever it is and in
   any encoding. Feeds have no need for one, and refusing it blocks
   entity expansion and external entities whatever the installed Expat
   version. Python's XML documentation says Expat below 2.7.2 may be
   vulnerable to entity expansion
   (<https://docs.python.org/3/library/xml.html#xml-vulnerabilities>).
3. Malformed XML, including an undefined entity such as `&nbsp;`.
4. More than 100 nested elements.
5. A root element that is not `rss` (no namespace) or Atom `feed`.
   RSS 1.0 (`rdf:RDF`) is refused in this version.

Implement with `xml.parsers.expat` and namespace processing, failing in
the document type and entity declaration handlers and never loading
external entities. ElementTree's C parser does not expose those
handlers.

**Encoding** comes from the byte order mark or the XML declaration, as
Expat reads them (UTF-8 by default). Expat handles UTF-8, UTF-16,
ISO-8859-1 and US-ASCII itself; Python supplies other single-byte
encodings such as windows-1252. An HTTP `charset` is not used.

**Names** are matched by namespace URI and local name, never by prefix:

| Name | Namespace |
| --- | --- |
| RSS elements | none |
| Atom | `http://www.w3.org/2005/Atom` |
| `content:encoded` | `http://purl.org/rss/1.0/modules/content/` |
| `dc:date` | `http://purl.org/dc/elements/1.1/` |

When a field occurs more than once, the first is used. Only direct
children are read: a channel's or feed's own fields, and
each item's or entry's own fields. Other elements, extensions such as
`cap:` and `media:`, and Atom elements inside an RSS channel are
ignored.

**Text values.** An element's character data, CDATA included, with
character references decoded once by the XML parser. Titles have runs
of whitespace (as `str.split` sees it, so no-break spaces too) collapsed
to one space. Every value has surrounding
whitespace removed, and an empty value means the field is `None`.

**Links** resolve against `url` (`urllib.parse.urljoin`). A result whose
scheme is not `http` or `https` is `None`. `xml:base` is not used.

#### RSS (`<rss>`, any `version`)

The `channel` child gives `title` and `link`. Each `item` child of the
channel:

- `title`: `title`.
- `link`: `link`; when there is none, `guid` when it is an absolute
  http or https URL and its `isPermaLink` attribute is not `false`.
- `id`: `guid`.
- `published`: `pubDate` through `parse_date`; when that is missing or
  unreadable, `dc:date`.
- `summary`: `description`, `is_html=True`. (RSS 2.0 allows
  entity-encoded HTML there, and feeds use it.)
- `content`: `content:encoded`, `is_html=True`.

A `channel` missing is a `FeedError`.

#### Atom (`<feed>`, RFC 4287)

The feed's `title` and `link`, and for each `entry`:

- `title`: a text construct (below), then collapsed.
- `link`: from the `link` children whose `rel` is `alternate` or
  absent: the first with `type` `text/html` or `application/xhtml+xml`,
  else the first of them. Its `href`, resolved.
- `id`: `id`.
- `published`: `published` through `parse_date`; when that is missing
  or unreadable, `updated`.
- `summary` and `content`: text constructs. `content` with a `src`
  attribute (content held elsewhere) is `None`.

**Text constructs** by `type` (case-insensitive):

| `type` | Body | As a title |
| --- | --- | --- |
| absent, `text`, or `text/*` | character data, `is_html=False` | as is |
| `html` or `text/html` | character data (escaped HTML, decoded once), `is_html=True` | tags removed, references decoded |
| `xhtml` or `application/xhtml+xml` | character data of all descendants, `is_html=False` | the same |
| anything else | `None` | `None` |

#### Both formats

- An item whose `title`, `summary` and `content` are all `None` is
  skipped.
- After `max_items` items are kept, the rest are dropped and
  `truncated` is `True`.

### `parse_date(value: str) -> datetime | None`

Returns an aware UTC datetime, or `None` when the value cannot be read.
Never raises.

1. Surrounding whitespace removed; empty gives `None`.
2. RFC 822 / RFC 2822 (`email.utils.parsedate_to_datetime`): `Sun, 19
   May 2002 15:21:36 GMT`, two-digit years, US zone names such as
   `EST`, numeric offsets. RSS 2.0 requires this format and allows a
   two-digit year.
3. Otherwise ISO 8601 / RFC 3339 (`datetime.fromisoformat`): `Z` or an
   offset, or a date alone. Atom requires RFC 3339.
4. A result without a zone (or with RFC 2822's `-0000`) is taken as UTC.
   A result with one is converted to UTC.

## Sources

- RSS 2.0 specification, <https://www.rssboard.org/rss-specification>:
  channel `title`, `link`, `description`; item `title`, `link`,
  `description`, `pubDate`, `guid`; "at least one of title or
  description must be present"; RFC 822 dates with two- or four-digit
  years.
- RSS content module, <https://web.resource.org/rss/1.0/modules/content/>:
  `content:encoded` and its namespace.
- RFC 4287 (Atom): namespace (1.2), dates (3.3), text constructs (3.1),
  `atom:entry` (4.1.2), `atom:content` and `src` (4.1.3),
  `atom:link` (4.2.7).
- Observed feeds, 2026-11-25, saved under `tests/fixtures/feeds/` (both
  US Government works), cut to two items each:
  - `nasa-news.rss`: <https://www.nasa.gov/news-release/feed/>
  - `nws-alerts-ak.atom`: <https://api.weather.gov/alerts/active.atom?area=AK>
  - `nws-alerts-me-empty.atom`: the same for `area=ME`, with no alerts.

## Notes from real feeds

Seventeen live feeds were parsed on 2026-11-25 (BBC, NPR, NASA, ARRL,
Hacker News, Le Monde, WordPress, GitHub, Python Insider, NWS, USGS,
YouTube, xkcd, Reddit, Mastodon and others). All parsed except one HTML
page, refused at its `<!DOCTYPE html>`. Behaviour the service must
allow for:

- Some feeds carry their text only in Atom `content` (GitHub releases),
  or only in Media RSS `media:description` (YouTube), which this version
  ignores.
- A missing or blocked feed can arrive as a valid, empty feed (Reddit
  returned Atom titled "Not Found"). Fetch errors come from the HTTP
  status, which is the caller's job.
- `updated` is a revision time, not an event time (USGS earthquakes
  have no `published`).

## Not in this version

RSS 1.0, Media RSS, `xml:base`, enclosures, categories, authors, feed
discovery from HTML pages, and fetching or caching (the `NEWS`
service's job).
