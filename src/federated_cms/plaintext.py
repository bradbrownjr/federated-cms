"""Plain-text extraction; see docs/TEXT-EXTRACTOR.md."""

import codecs
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
import textwrap
from urllib.parse import quote

from anyascii import anyascii


@dataclass(frozen=True)
class Document:
    url: str
    fetched_at: datetime
    kind: str
    title: str | None
    text: str
    truncated: bool
    original_bytes: int


class ExtractError(ValueError):
    pass


_CHARSET = re.compile(r'charset\s*=\s*["\']?\s*([^\s;"\'/>]+)', re.I)
_HTML_START = re.compile(r'^\s*(?:<!doctype\s+html\b|<html\b)', re.I)
_WMO = re.compile(r'^[A-Z]{4}\d{2} [A-Z]{4} \d{6}(?: [A-Z]{3})?$')
_AWIPS = re.compile(r'^[A-Z0-9]{4,6} *$')
_EXPIRES = re.compile(r'^Expires:\d{12};;\d+$')
_UGC = re.compile(r'^[A-Z]{2}[CZ]\d{3}[A-Z0-9>\-]*$')
_UGC_CONT = re.compile(r'^[A-Z0-9>\-]+$')
_UGC_END = re.compile(r'-\d{6}-$')
_VTEC = re.compile(r'^/[^ ]+/$')
_SPACE = re.compile(r'\s+')
_BLANKS = re.compile(r'\n(?:[ \t]*\n)+')
_CP1252_UNDEFINED = {0xDC00 + b: b for b in (0x81, 0x8D, 0x8F, 0x90, 0x9D)}
_MAX_DEPTH = 256
_DEGREE = re.compile('°(?=[A-Z])')
_LANDMARKS = frozenset(('navigation', 'banner', 'contentinfo', 'complementary', 'search'))


def _is_html(start, content_type):
    if content_type:
        return content_type.split(';', 1)[0].strip().lower() in ('text/html', 'application/xhtml+xml')
    return bool(_HTML_START.match(start))


def _codec(label):
    if label.lower().replace('_', '-') in ('iso-8859-1', 'latin1', 'latin-1', 'us-ascii', 'ascii'):
        return 'cp1252'
    try:
        return codecs.lookup(label).name
    except LookupError:
        return None


def _decode_with(data, encoding):
    if encoding == 'cp1252':
        return data.decode('cp1252', 'surrogateescape').translate(_CP1252_UNDEFINED)
    return data.decode(encoding)


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.labels = []

    def handle_starttag(self, tag, attrs):
        if tag != 'meta':
            return
        attrs = dict(attrs)
        if attrs.get('charset'):
            self.labels.append(attrs['charset'])
        elif attrs.get('http-equiv', '').lower() == 'content-type':
            match = _CHARSET.search(attrs.get('content', ''))
            if match:
                self.labels.append(match.group(1))


def decode(data: bytes, content_type: str | None) -> tuple[str, str]:
    if data.startswith(codecs.BOM_UTF8):
        return data.decode('utf-8-sig'), 'utf-8'
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode('utf-16'), 'utf-16'
    labels = []
    match = _CHARSET.search(content_type or '')
    if match:
        labels.append(match.group(1))
    if _is_html(data[:1024].decode('ascii', 'replace'), content_type):
        parser = _MetaParser()
        parser.feed(data[:4096].decode('ascii', 'replace'))
        labels.extend(parser.labels[:1])
    labels.extend(('utf-8', 'cp1252'))
    for label in labels:
        encoding = _codec(label)
        if encoding:
            try:
                return _decode_with(data, encoding), encoding
            except (UnicodeError, LookupError):
                pass
    raise ExtractError('Unable to decode input')


def to_ascii(text: str) -> str:
    text = text.replace('\r\n', '\n').replace('\r', '\n').expandtabs(8)
    text = text.translate(dict.fromkeys(map(ord, '\u00ad\u200b\u200c\u200d\ufeff')))
    text = _DEGREE.sub('', text).replace('°', ' deg')
    text = anyascii(text)
    text = ''.join(c for c in text if c == '\n' or ' ' <= c <= '~')
    return '\n'.join(line.rstrip(' ') for line in text.split('\n'))


def _normal_lines(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = '\n'.join(line.rstrip() for line in text.split('\n'))
    return _BLANKS.sub('\n\n', text).strip('\n')


def is_nws_product(text: str) -> bool:
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or _EXPIRES.fullmatch(lines[i])):
        i += 1
    return i + 1 < len(lines) and bool(_WMO.fullmatch(lines[i]) and _AWIPS.fullmatch(lines[i + 1]))


def clean_nws(text: str) -> str:
    lines = text.splitlines()
    output = []
    ugc = False
    heading = False
    for line in lines:
        line = line.rstrip()
        if _EXPIRES.fullmatch(line):
            continue
        after_heading, heading = heading, False
        if _WMO.fullmatch(line):
            heading = True
            continue
        if after_heading and _AWIPS.fullmatch(line):
            continue
        if ugc:
            if _UGC_CONT.fullmatch(line):
                ugc = not bool(_UGC_END.search(line))
                continue
            ugc = False
        if _UGC.fullmatch(line):
            ugc = not bool(_UGC_END.search(line))
            continue
        if _VTEC.fullmatch(line) or line in ('$$', '&&'):
            continue
        output.append(line)
    return _normal_lines('\n'.join(output))


_VOID = frozenset('area base br col embed hr img input link meta source track wbr'.split())
_DROP = frozenset('head title script style noscript template svg iframe nav aside form button select textarea dialog'.split())
_BLOCK = frozenset('p div section article main blockquote pre table ul ol dl h1 h2 h3 h4 h5 h6 hr figure header footer'.split())
_INNER_BLOCK = _BLOCK | {'li', 'tr', 'td', 'th'}


@dataclass
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    dropped: bool = False


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node('document')
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        groups = {'p': {'p'}, 'li': {'li'}, 'dt': {'dt', 'dd'}, 'dd': {'dt', 'dd'},
                  'tr': {'tr'}, 'td': {'td', 'th'}, 'th': {'td', 'th'}}
        boundaries = {'li': {'ul', 'ol'}, 'dt': {'dl'}, 'dd': {'dl'},
                      'tr': {'table'}, 'td': {'tr', 'table'}, 'th': {'tr', 'table'}}
        if tag in groups:
            for i in range(len(self.stack) - 1, 0, -1):
                node = self.stack[i]
                if node.dropped:
                    break
                if node.tag in groups[tag]:
                    del self.stack[i:]
                    break
                if node.tag in boundaries.get(tag, set()):
                    break
        attrs = dict(attrs)
        hidden = ('hidden' in attrs or (attrs.get('aria-hidden') or '').lower() == 'true'
                  or (attrs.get('role') or '').strip().lower() in _LANDMARKS)
        inside = any(n.tag in ('article', 'main') for n in self.stack)
        dropped = hidden or tag in _DROP or (tag in ('header', 'footer') and not inside)
        if len(self.stack) > _MAX_DEPTH and tag not in _VOID and not (dropped and not self.stack[-1].dropped):
            return
        node = _Node(tag, attrs, dropped=dropped)
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
            if self.stack[i].dropped:
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _nodes(node):
    yield node
    for child in node.children:
        if isinstance(child, _Node):
            yield from _nodes(child)


def _raw(node, include_dropped=False):
    if node.dropped and not include_dropped:
        return ''
    return ''.join(child if isinstance(child, str) else _raw(child, include_dropped) for child in node.children)


def _collapse(text):
    return _SPACE.sub(' ', text).strip()


def _inline_children(children, sep=' '):
    parts = []
    after_link = False
    for child in children:
        if isinstance(child, str):
            parts.append(child)
            after_link = False
            continue
        text = _inline(child, sep)
        if child.tag in _INNER_BLOCK:
            text = sep + text + sep
        elif child.tag == 'a' and after_link:
            text = ' ' + text
        after_link = child.tag == 'a' and not child.dropped
        parts.append(text)
    return ''.join(parts)


def _inline(node, sep=' '):
    if node.dropped or node.tag == 'img':
        return ''
    if node.tag == 'br':
        return '\x00'
    return _inline_children(node.children, sep)


def _wrap(text, width, initial='', subsequent=''):
    lines = []
    for part in text.split('\x00'):
        part = _collapse(part)
        if part:
            lines.extend(textwrap.wrap(part, width=width, initial_indent=subsequent if lines else initial,
                                       subsequent_indent=subsequent, break_long_words=False, break_on_hyphens=False))
    return '\n'.join(lines)


def _format(node, width, depth=0):
    if node.dropped:
        return ''
    tag = node.tag
    if tag == 'hr':
        return '---'
    if tag == 'pre':
        return _raw(node).expandtabs(8).strip('\n')
    if tag in ('ul', 'ol'):
        items = []
        count = 0
        for child in node.children:
            if not isinstance(child, _Node) or child.tag != 'li' or child.dropped:
                continue
            count += 1
            prefix = '  ' * depth + (f'{count}. ' if tag == 'ol' else '* ')
            inline = _inline_children([c for c in child.children
                                       if not isinstance(c, _Node) or c.tag not in ('ul', 'ol')], '\x00')
            line = _wrap(inline, width, prefix, ' ' * len(prefix))
            if line:
                items.append(line)
            for nested in child.children:
                if isinstance(nested, _Node) and nested.tag in ('ul', 'ol'):
                    result = _format(nested, width, depth + 1)
                    if result:
                        items.append(result)
        return '\n'.join(items)
    if tag == 'dl':
        parts = []
        for child in node.children:
            if isinstance(child, _Node) and child.tag in ('dt', 'dd') and not child.dropped:
                indent = '  ' if child.tag == 'dd' else ''
                result = _wrap(_inline(child, '\x00'), width, indent, indent)
                if result:
                    parts.append(result)
        return '\n'.join(parts)
    if tag == 'table':
        rows = []
        for row in _nodes(node):
            if row.tag != 'tr' or row.dropped:
                continue
            cells = [_collapse(_inline(c)) for c in row.children if isinstance(c, _Node) and c.tag in ('td', 'th') and not c.dropped]
            if cells:
                rows.append(_wrap(' | '.join(cells), width, subsequent='  '))
        return '\n'.join(rows)
    if tag == 'blockquote':
        return _wrap(_inline(node, '\x00'), width, '> ', '> ')
    if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
        result = _wrap(_inline(node), width)
        if result and tag in ('h1', 'h2'):
            result += '\n' + ('=' if tag == 'h1' else '-') * len(result.split('\n')[-1])
        return result
    parts = []
    pending = []

    def flush():
        result = _wrap(_inline_children(pending), width)
        if result:
            parts.append(result)
        pending.clear()

    for child in node.children:
        if isinstance(child, _Node) and child.tag in _BLOCK:
            flush()
            result = _format(child, width, depth)
            if result:
                parts.append(result)
        else:
            pending.append(child)
    flush()
    return '\n\n'.join(parts)


def html_to_text(markup: str, *, width: int = 69) -> tuple[str | None, str]:
    parser = _Tree()
    parser.feed(markup)
    nodes = list(_nodes(parser.root))
    title = next((_collapse(_raw(n, True)) for n in nodes if n.tag == 'title' and _collapse(_raw(n, True))), None)
    if not title:
        title = next((_collapse(_raw(n)) for n in nodes if n.tag == 'h1' and _collapse(_raw(n))), None)
    root = next((n for tag in ('main', 'article', 'body') for n in nodes if n.tag == tag and not n.dropped), parser.root)
    return title, _format(root, width)


def extract(data, *, url, fetched_at, content_type=None, width=69, max_chars=12000, max_input_bytes=2_000_000):
    if len(data) > max_input_bytes or width < 20 or max_chars < 200 or fetched_at.utcoffset() is None:
        raise ExtractError('Invalid extraction parameters')
    text, _ = decode(data, content_type)
    title = None
    if _is_html(text[:1024], content_type):
        kind = 'html'
        title, text = html_to_text(text, width=width)
    elif is_nws_product(text):
        kind = 'nws'
        text = clean_nws(text)
    else:
        kind = 'text'
        text = _normal_lines(text)
    text = to_ascii(text)
    if title is not None:
        title = to_ascii(title)
    truncated = len(text) > max_chars
    if truncated:
        end = text.rfind('\n', 0, max_chars + 1)
        text = text[:end if end >= 0 else max_chars].rstrip()
    return Document(url, fetched_at.astimezone(timezone.utc), kind, title, text, truncated, len(data))


def render(doc: Document) -> str:
    lines = []
    if doc.title is not None:
        lines.append(doc.title)
    lines.append('Source: ' + quote(doc.url, safe=":/?#[]@!$&'()*+,;=-._~%"))
    lines.append('Fetched: ' + doc.fetched_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    result = '\n'.join(lines) + '\n\n' + doc.text
    if doc.truncated:
        result += '\n\n[Truncated: the full text is longer than this reply.]'
    return result.rstrip('\n') + '\n'
