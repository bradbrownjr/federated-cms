"""Read already-fetched RSS 2.0 and Atom feeds safely."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.parsers import expat


ATOM = "http://www.w3.org/2005/Atom "
CONTENT = "http://purl.org/rss/1.0/modules/content/ "
DC = "http://purl.org/dc/elements/1.1/ "


class FeedError(ValueError):
    """The input cannot be read as a supported feed."""


@dataclass(frozen=True)
class Body:
    value: str
    is_html: bool


@dataclass(frozen=True)
class Item:
    title: str | None
    link: str | None
    id: str | None
    published: datetime | None
    summary: Body | None
    content: Body | None


@dataclass(frozen=True)
class Feed:
    kind: str
    title: str | None
    link: str | None
    items: tuple[Item, ...]
    truncated: bool


def parse_date(value: str) -> datetime | None:
    """Read a feed date and normalize it to UTC, or return None."""
    try:
        value = value.strip()
        if not value:
            return None
        try:
            result = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except Exception:
        return None


def _clean(value: str) -> str | None:
    """Strip surrounding whitespace and represent empty text as None."""
    return value.strip() or None


def _title(value: str | None) -> str | None:
    """Collapse whitespace in a title."""
    return " ".join(value.split()) or None if value else None


def _link(value: str | None, base: str) -> str | None:
    """Resolve a link and accept only absolute HTTP(S) URLs."""
    if not value:
        return None
    result = urljoin(base, value)
    parts = urlsplit(result)
    return result if parts.scheme.lower() in ("http", "https") and parts.netloc else None


class _HTMLText(HTMLParser):
    """Extract text from an Atom HTML title."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _construct(entry: tuple[str | None, dict] | None) -> Body | None:
    """Interpret an Atom text construct."""
    if entry is None:
        return None
    value, attrs = entry
    if not value:
        return None
    kind = attrs.get("type", "text").lower()
    if kind in ("html", "text/html"):
        return Body(value, True)
    if kind in ("xhtml", "application/xhtml+xml", "text") or kind.startswith("text/"):
        return Body(value, False)
    return None


def _atom_title(entry: tuple[str | None, dict] | None) -> str | None:
    """Interpret an Atom title, stripping markup from HTML constructs."""
    body = _construct(entry)
    if body is None:
        return None
    value = body.value
    if body.is_html:
        parser = _HTMLText()
        parser.feed(value)
        parser.close()
        value = "".join(parser.parts)
    return _title(value)


def _value(fields: dict, name: str) -> str | None:
    """Get the first value of a field."""
    entry = fields.get(name)
    return entry[0] if entry else None


def _body(fields: dict, name: str) -> Body | None:
    """Get an RSS HTML body."""
    value = _value(fields, name)
    return Body(value, True) if value else None


def _atom_link(fields: dict, base: str) -> str | None:
    """Choose an alternate Atom link in document order."""
    links = fields.get("links", [])
    preferred = [attrs for attrs in links if attrs.get("type", "").lower() in
                 ("text/html", "application/xhtml+xml")]
    chosen = preferred[0] if preferred else (links[0] if links else None)
    return _link(_clean(chosen.get("href", "")), base) if chosen is not None else None


def _item(kind: str, fields: dict, base: str) -> Item | None:
    """Convert collected direct child fields to an item."""
    if kind == "rss":
        title = _title(_value(fields, "title"))
        summary = _body(fields, "description")
        content = _body(fields, CONTENT + "encoded")
        identifier = _value(fields, "guid")
        link = _link(_value(fields, "link"), base)
        guid = fields.get("guid")
        if not _value(fields, "link") and identifier and guid[1].get("isPermaLink", "").lower() != "false":
            parts = urlsplit(identifier)
            if parts.scheme.lower() in ("http", "https") and parts.netloc:
                link = identifier
        dates = (_value(fields, "pubDate"), _value(fields, DC + "date"))
    else:
        title = _atom_title(fields.get(ATOM + "title"))
        summary = _construct(fields.get(ATOM + "summary"))
        content_entry = fields.get(ATOM + "content")
        content = None if content_entry and "src" in content_entry[1] else _construct(content_entry)
        identifier = _value(fields, ATOM + "id")
        link = _atom_link(fields, base)
        dates = (_value(fields, ATOM + "published"), _value(fields, ATOM + "updated"))
    if title is None and summary is None and content is None:
        return None
    published = next((d for d in (parse_date(v) for v in dates if v) if d), None)
    return Item(title, link, identifier, published, summary, content)


@dataclass
class _Frame:
    """One open element; only recognized containers and fields hold data."""
    name: str
    role: str = "ignored"
    attrs: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)
    text: list[str] = field(default_factory=list)


def parse_feed(data: bytes, *, url: str, max_bytes: int = 2_000_000,
               max_items: int = 200) -> Feed:
    """Parse a bounded RSS or Atom document without resolving entities."""
    try:
        if len(data) > max_bytes:
            raise FeedError("Feed exceeds size limit")
        stack: list[_Frame] = []
        items: list[Item] = []
        feed_fields: dict = {}
        kind = None
        channel_seen = False
        truncated = False
        capture: _Frame | None = None

        rss_fields = {"title", "link", "guid", "pubDate", "description", CONTENT + "encoded", DC + "date"}
        atom_fields = {ATOM + name for name in ("title", "id", "published", "updated", "summary", "content")}

        def start(name, attrs):
            nonlocal kind, channel_seen, capture
            if len(stack) >= 100:
                raise FeedError("Feed exceeds nesting limit")
            parent = stack[-1] if stack else None
            frame = _Frame(name)
            if parent is None:
                if name == "rss":
                    kind = "rss"
                    frame.role = "root"
                elif name == ATOM + "feed":
                    kind = "atom"
                    frame.role = "feed"
                    frame.fields = feed_fields
                else:
                    raise FeedError("Unsupported feed root")
            elif kind == "rss" and parent.role == "root" and name == "channel" and not channel_seen:
                frame.role = "feed"
                frame.fields = feed_fields
                channel_seen = True
            elif parent.role == "feed" and name == ("item" if kind == "rss" else ATOM + "entry"):
                frame.role = "item"
            elif parent.role in ("feed", "item"):
                allowed = rss_fields if kind == "rss" else atom_fields
                if name in allowed:
                    frame.role = "field"
                    frame.attrs = attrs
                    capture = frame
                elif kind == "atom" and name == ATOM + "link":
                    if attrs.get("rel", "alternate") == "alternate":
                        parent.fields.setdefault("links", []).append(attrs)
            stack.append(frame)

        def characters(text):
            if capture is not None:
                capture.text.append(text)

        def end(name):
            nonlocal capture, truncated
            frame = stack.pop()
            if frame.role == "field":
                stack[-1].fields.setdefault(name, (_clean("".join(frame.text)), frame.attrs))
                capture = None
            elif frame.role == "item":
                item = _item(kind, frame.fields, url)
                if item is not None:
                    if len(items) < max_items:
                        items.append(item)
                    else:
                        truncated = True

        def refuse(*args):
            raise FeedError("Document type declaration refused (an HTML page, or a feed with a DTD)")

        parser = expat.ParserCreate(namespace_separator=" ")
        parser.buffer_text = True
        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CharacterDataHandler = characters
        parser.StartDoctypeDeclHandler = refuse
        parser.EntityDeclHandler = refuse
        parser.ExternalEntityRefHandler = refuse
        parser.Parse(data, True)
        if kind == "rss" and not channel_seen:
            raise FeedError("RSS channel is missing")
        if kind == "rss":
            title = _title(_value(feed_fields, "title"))
            link = _link(_value(feed_fields, "link"), url)
        else:
            title = _atom_title(feed_fields.get(ATOM + "title"))
            link = _atom_link(feed_fields, url)
        return Feed(kind, title, link, tuple(items), truncated)
    except FeedError:
        raise
    except Exception as exc:
        raise FeedError("Could not parse feed") from exc
