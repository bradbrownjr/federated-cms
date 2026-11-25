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
XHTML = "http://www.w3.org/1999/xhtml "


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
            result = datetime.fromisoformat(value.replace("z", "Z").replace("Z", "+00:00"))
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


def _link(value: str | None, base: str, *, absolute: bool = False) -> str | None:
    """Resolve a link and accept only absolute HTTP(S) URLs."""
    value = value.strip() if value else ""
    if not value or any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in value):
        return None
    try:
        original = urlsplit(value)
        if absolute and not original.scheme:
            return None
        if original.scheme and not original.hostname:
            return None
        result = urljoin(base, value)
        parts = urlsplit(result)
        if (parts.scheme.lower() in ("http", "https") and parts.hostname
                and parts.username is None and parts.password is None):
            return result
    except (ValueError, TypeError):
        pass
    return None


class _HTMLText(HTMLParser):
    """Extract text from an Atom HTML title."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
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
    if entry[1].get("type", "text").lower() in ("xhtml", "application/xhtml+xml"):
        value = entry[1].get("_title_text", value)
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
    preferred = [attrs for attrs in links if attrs.get("type", "").strip().lower() in
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
            link = _link(identifier, base, absolute=True)
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
    title_text: list[str] = field(default_factory=list)


def parse_feed(data: bytes, *, url: str, max_bytes: int = 2_000_000,
               max_items: int = 200) -> Feed:
    """Parse a bounded RSS or Atom document without resolving entities."""
    try:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise FeedError("Feed data must be bytes-like")
        if len(data) > max_bytes:
            raise FeedError("Feed exceeds size limit")
        stack: list[_Frame] = []
        items: list[Item] = []
        feed_fields: dict = {}
        kind = None
        channel_seen = False
        truncated = False
        capture: _Frame | None = None
        hidden = 0

        rss_fields = {"title", "link", "guid", "pubDate", "description", CONTENT + "encoded", DC + "date"}
        atom_fields = {ATOM + name for name in ("title", "id", "published", "updated", "summary", "content")}

        def start(name, attrs):
            nonlocal kind, channel_seen, capture, hidden
            if len(stack) >= 100:
                raise FeedError("Feed exceeds nesting limit")
            parent = stack[-1] if stack else None
            frame = _Frame(name)
            if capture is not None and (hidden or name in (XHTML + "script", XHTML + "style")):
                hidden += 1
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
                    if attrs.get("rel", "alternate").strip().lower() in (
                            "alternate", "http://www.iana.org/assignments/relation/alternate"):
                        parent.fields.setdefault("links", []).append(attrs)
            stack.append(frame)

        def characters(text):
            if capture is not None:
                capture.text.append(text)
                if not hidden:
                    capture.title_text.append(text)

        def end(name):
            nonlocal capture, truncated, hidden
            frame = stack.pop()
            if hidden:
                hidden -= 1
            if frame.role == "field":
                if name == ATOM + "title":
                    frame.attrs["_title_text"] = "".join(frame.title_text)
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
