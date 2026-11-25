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


_LABEL_GROUPS = {
    'utf-8': 'unicode-1-1-utf-8 unicode11utf8 unicode20utf8 utf-8 utf8 x-unicode20utf8',
    'utf-16-le': 'csunicode iso-10646-ucs-2 ucs-2 unicode unicodefeff utf-16 utf-16le',
    'utf-16-be': 'unicodefffe utf-16be',
    'cp866': '866 cp866 csibm866 ibm866',
    'iso8859-2': 'csisolatin2 iso-8859-2 iso-ir-101 iso8859-2 iso88592 iso_8859-2 iso_8859-2:1987 l2 latin2',
    'iso8859-3': 'csisolatin3 iso-8859-3 iso-ir-109 iso8859-3 iso88593 iso_8859-3 iso_8859-3:1988 l3 latin3',
    'iso8859-4': 'csisolatin4 iso-8859-4 iso-ir-110 iso8859-4 iso88594 iso_8859-4 iso_8859-4:1988 l4 latin4',
    'iso8859-5': 'csisolatincyrillic cyrillic iso-8859-5 iso-ir-144 iso8859-5 iso88595 iso_8859-5 iso_8859-5:1988',
    'iso8859-6': 'arabic asmo-708 csiso88596e csiso88596i csisolatinarabic ecma-114 iso-8859-6 iso-8859-6-e iso-8859-6-i iso-ir-127 iso8859-6 iso88596 iso_8859-6 iso_8859-6:1987',
    'iso8859-7': 'csisolatingreek ecma-118 elot_928 greek greek8 iso-8859-7 iso-ir-126 iso8859-7 iso88597 iso_8859-7 iso_8859-7:1987 sun_eu_greek',
    'iso8859-8': 'csiso88598e csisolatinhebrew hebrew iso-8859-8 iso-8859-8-e iso-ir-138 iso8859-8 iso88598 iso_8859-8 iso_8859-8:1988 visual csiso88598i iso-8859-8-i logical',
    'iso8859-10': 'csisolatin6 iso-8859-10 iso-ir-157 iso8859-10 iso885910 l6 latin6',
    'iso8859-13': 'iso-8859-13 iso8859-13 iso885913',
    'iso8859-14': 'iso-8859-14 iso8859-14 iso885914',
    'iso8859-15': 'csisolatin9 iso-8859-15 iso8859-15 iso885915 iso_8859-15 l9',
    'iso8859-16': 'iso-8859-16',
    'koi8-r': 'cskoi8r koi koi8 koi8-r koi8_r',
    'koi8-u': 'koi8-ru koi8-u',
    'mac-roman': 'csmacintosh mac macintosh x-mac-roman',
    'cp874': 'dos-874 iso-8859-11 iso8859-11 iso885911 tis-620 windows-874',
    'cp1250': 'cp1250 windows-1250 x-cp1250',
    'cp1251': 'cp1251 windows-1251 x-cp1251',
    'cp1252': 'ansi_x3.4-1968 ascii cp1252 cp819 csisolatin1 ibm819 iso-8859-1 iso-ir-100 iso8859-1 iso88591 iso_8859-1 iso_8859-1:1987 l1 latin1 us-ascii windows-1252 x-cp1252',
    'cp1253': 'cp1253 windows-1253 x-cp1253',
    'cp1254': 'cp1254 csisolatin5 iso-8859-9 iso-ir-148 iso8859-9 iso88599 iso_8859-9 iso_8859-9:1989 l5 latin5 windows-1254 x-cp1254',
    'cp1255': 'cp1255 windows-1255 x-cp1255',
    'cp1256': 'cp1256 windows-1256 x-cp1256',
    'cp1257': 'cp1257 windows-1257 x-cp1257',
    'cp1258': 'cp1258 windows-1258 x-cp1258',
    'mac-cyrillic': 'x-mac-cyrillic x-mac-ukrainian',
    'gb18030': 'chinese csgb2312 csiso58gb231280 gb2312 gb_2312 gb_2312-80 gbk iso-ir-58 x-gbk gb18030',
    'big5hkscs': 'big5 big5-hkscs cn-big5 csbig5 x-x-big5',
    'euc_jp': 'cseucpkdfmtjapanese euc-jp x-euc-jp',
    'iso2022_jp': 'csiso2022jp iso-2022-jp',
    'cp932': 'csshiftjis ms932 ms_kanji shift-jis shift_jis sjis windows-31j x-sjis',
    'cp949': 'cseuckr csksc56011987 euc-kr iso-ir-149 korean ks_c_5601-1987 ks_c_5601-1989 ksc5601 ksc_5601 windows-949',
}
_LABELS = {label: codecs.lookup(codec).name for codec, labels in _LABEL_GROUPS.items()
           for label in labels.split()}


def _codec(label):
    """Look up only the encoding labels specified by WHATWG."""
    return _LABELS.get(label.strip('\t\n\f\r ').lower())


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
        if 'charset' in attrs:
            self.labels.append(attrs['charset'] or '')
        elif (attrs.get('http-equiv') or '').lower() == 'content-type':
            match = _CHARSET.search(attrs.get('content') or '')
            if match:
                self.labels.append(match.group(1))


def decode(data: bytes, content_type: str | None) -> tuple[str, str]:
    if data.startswith(codecs.BOM_UTF8):
        return data.decode('utf-8-sig', 'replace'), 'utf-8'
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode('utf-16', 'replace'), 'utf-16'
    labels = []
    match = _CHARSET.search(content_type or '')
    if match:
        labels.append(match.group(1))
    if _is_html(data[:1024].decode('ascii', 'replace'), content_type):
        parser = _MetaParser()
        parser.feed(data[:4096].decode('ascii', 'replace'))
        labels.extend('utf-8' if _codec(label) in ('utf-16-le', 'utf-16-be') else label
                      for label in parser.labels[:1])
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
    lines = re.sub(r'\r+\n|\r', '\n', text).split('\n')
    i = 0
    while i < len(lines) and (not lines[i].strip() or _EXPIRES.fullmatch(lines[i])):
        i += 1
    return i + 1 < len(lines) and bool(_WMO.fullmatch(lines[i]) and _AWIPS.fullmatch(lines[i + 1]))


def clean_nws(text: str) -> str:
    lines = re.sub(r'\r+\n|\r', '\n', text).split('\n')
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
_BLOCK = frozenset('p div section article main blockquote pre table ul ol dl h1 h2 h3 h4 h5 h6 hr figure header footer html body'.split())
_INNER_BLOCK = _BLOCK | {'li', 'tr', 'td', 'th'}


@dataclass
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    dropped: bool = False


_IMPLIED = {'p': ('p',), 'li': ('li', 'ul', 'ol'),
            'dt': ('dt', 'dd', 'dl'), 'dd': ('dt', 'dd', 'dl'),
            'tr': ('tr', 'table'), 'td': ('td', 'th', 'tr', 'table'),
            'th': ('td', 'th', 'tr', 'table')}


class _Tree(HTMLParser):
    """Build a bounded tree, keeping open-tag lookups constant time."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node('document')
        self.stack = [self.root]
        self.positions = {}
        self.dropped = None
        self.landmark_depth = 0
        self.head_title = None

    def _pop_to(self, index):
        while len(self.stack) > index:
            node = self.stack.pop()
            self.positions[node.tag].pop()
            if node.tag in ('article', 'main') and not node.dropped:
                self.landmark_depth -= 1
            if node is self.dropped:
                self.dropped = None

    def handle_starttag(self, tag, attrs):
        if self.dropped is not None:
            if self.dropped.tag == 'head' and tag == 'title' and self.head_title is None:
                self.head_title = _Node('title', dropped=True)
                self.dropped.children.append(self.head_title)
            return
        if len(self.stack) > _MAX_DEPTH and tag not in _DROP and tag not in ('header', 'footer'):
            if not any(k in ('hidden', 'aria-hidden', 'role') for k, v in attrs):
                return
        group = _IMPLIED.get(tag)
        if group:
            positions = [(self.positions[t][-1], t) for t in group if self.positions.get(t)]
            if positions:
                index, found = max(positions)
                targets = group[:2] if tag in ('dt', 'dd', 'td', 'th') else group[:1]
                if found in targets:
                    self._pop_to(index)
        attrs = dict(attrs)
        hidden = ('hidden' in attrs or (attrs.get('aria-hidden') or '').lower() == 'true'
                  or (attrs.get('role') or '').strip().lower() in _LANDMARKS)
        dropped = hidden or tag in _DROP or (tag in ('header', 'footer') and not self.landmark_depth)
        if len(self.stack) > _MAX_DEPTH and not dropped:
            return
        node = _Node(tag, attrs, dropped=dropped)
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)
            self.positions.setdefault(tag, []).append(len(self.stack) - 1)
            if dropped:
                self.dropped = node
            elif tag in ('article', 'main'):
                self.landmark_depth += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.dropped is not None:
            if tag == 'title' and self.head_title is not None:
                self.head_title = None
                return
            if self.dropped.tag == tag:
                self._pop_to(len(self.stack) - 1)
            return
        positions = self.positions.get(tag)
        if positions:
            self._pop_to(positions[-1])

    def handle_data(self, data):
        if self.head_title is not None:
            self.head_title.children.append(data)
        elif self.dropped is None or self.dropped.tag == 'title':
            self.stack[-1].children.append(data)


def _nodes(node):
    """Walk nodes in document order without recursion."""
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        if not current.dropped or current.tag == 'head':
            pending.extend(child for child in reversed(current.children) if isinstance(child, _Node))


def _raw(node, include_dropped=False):
    parts = []
    pending = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            parts.append(current)
        elif include_dropped or not current.dropped:
            pending.extend(reversed(current.children))
    return ''.join(parts)


def _collapse(text):
    return _SPACE.sub(' ', text).strip()


def _inline_children(children, sep=' '):
    """Flatten inline content, preserving block and adjacent-link boundaries."""
    parts = []
    stack = [(iter(children), False, None)]
    while stack:
        iterator, after_link, closing = stack[-1]
        try:
            child = next(iterator)
        except StopIteration:
            stack.pop()
            if closing:
                parts.append(closing)
            continue
        if isinstance(child, str):
            parts.append(child)
            stack[-1] = (iterator, False, closing)
        elif not child.dropped and child.tag != 'img':
            if child.tag == 'br':
                parts.append('\x00')
            else:
                block = child.tag in _INNER_BLOCK
                if block:
                    parts.append(sep)
                elif child.tag == 'a' and after_link:
                    parts.append(' ')
                stack.append((iter(child.children), False, sep if block else None))
            stack[-2 if child.tag != 'br' else -1] = (iterator, child.tag == 'a', closing)
    return ''.join(parts)


def _inline(node, sep=' '):
    if node.dropped or node.tag == 'img':
        return ''
    if node.tag == 'br':
        return '\x00'
    return _inline_children(node.children, sep)


def _list_children(node, tags):
    """Yield list items through div wrappers without entering other scopes."""
    pending = list(reversed(node.children))
    while pending:
        child = pending.pop()
        if isinstance(child, _Node) and not child.dropped:
            if child.tag in tags:
                yield child
            elif child.tag == 'div':
                pending.extend(reversed(child.children))


def _table_rows(node):
    pending = list(reversed(node.children))
    while pending:
        child = pending.pop()
        if isinstance(child, _Node) and not child.dropped and child.tag != 'table':
            if child.tag == 'tr':
                yield child
            else:
                pending.extend(reversed(child.children))


def _wrap(text, width, initial='', subsequent=''):
    lines = []
    for part in text.split('\x00'):
        part = _collapse(part)
        if part:
            indent = subsequent if lines else initial
            if len(indent) + len(part) <= width:
                lines.append(indent + part)
            else:
                lines.extend(textwrap.wrap(part, width=width, initial_indent=indent,
                                           subsequent_indent=subsequent, break_long_words=False, break_on_hyphens=False))
    return '\n'.join(lines)


def _format_node(node, width, depth, formatted):
    if node.dropped:
        return ''
    tag = node.tag
    if tag == 'hr':
        return '---'
    if tag == 'pre':
        return _inline(node).replace('\x00', '\n').expandtabs(8).strip('\n')
    if tag in ('ul', 'ol'):
        items = []
        count = 0
        for child in _list_children(node, ('li',)):
            count += 1
            prefix = ' ' * min(2 * depth, width // 2) + (f'{count}. ' if tag == 'ol' else '* ')
            inline = _inline_children([c for c in child.children
                                       if not isinstance(c, _Node) or c.tag not in ('ul', 'ol')], '\x00')
            line = _wrap(inline, width, prefix, ' ' * len(prefix))
            if line:
                items.append(line)
            for nested in child.children:
                if isinstance(nested, _Node) and nested.tag in ('ul', 'ol'):
                    result = formatted.get(id(nested), '')
                    if result:
                        items.append(result)
        return '\n'.join(items)
    if tag == 'dl':
        parts = []
        for child in _list_children(node, ('dt', 'dd')):
            indent = '  ' if child.tag == 'dd' else ''
            result = _wrap(_inline(child, '\x00'), width, indent, indent)
            if result:
                parts.append(result)
        return '\n'.join(parts)
    if tag == 'table':
        rows = []
        for row in _table_rows(node):
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
            result = formatted.get(id(child), '')
            if result:
                parts.append(result)
        else:
            pending.append(child)
    flush()
    return '\n\n'.join(parts)



def _format(node, width):
    """Format the bounded tree bottom-up without recursion."""
    pending = [(node, 0, False)]
    formatted = {}
    while pending:
        current, depth, visited = pending.pop()
        if visited:
            formatted[id(current)] = _format_node(current, width, depth, formatted)
        elif not current.dropped:
            pending.append((current, depth, True))
            next_depth = depth + (current.tag in ('ul', 'ol'))
            pending.extend((child, next_depth, False) for child in reversed(current.children)
                           if isinstance(child, _Node) and not child.dropped)
    return formatted.get(id(node), '')

def html_to_text(markup: str, *, width: int = 69, ascii: bool = False) -> tuple[str | None, str]:
    parser = _Tree()
    parser.feed(markup.replace('\x00', ''))
    parser.close()
    if ascii:
        for node in _nodes(parser.root):
            node.children = [(c if c.isascii() and c.isprintable() else to_ascii(c + 'x')[:-1])
                             if isinstance(c, str) else c
                             for c in node.children]
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
        title, text = html_to_text(text, width=width, ascii=True)
    elif is_nws_product(text):
        kind = 'nws'
        text = clean_nws(text)
    else:
        kind = 'text'
        text = _normal_lines(text)
    text = _normal_lines(to_ascii(text))
    if title is not None:
        title = to_ascii(title) or None
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
