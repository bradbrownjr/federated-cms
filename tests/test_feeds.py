"""Contract tests for federated_cms.feeds (docs/FEED-READER.md)."""

from __future__ import annotations

import dataclasses
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from federated_cms.feeds import Body, Feed, FeedError, Item, parse_date, parse_feed

FIXTURES = Path(__file__).parent / "fixtures" / "feeds"
URL = "https://example.org/feeds/news.xml"
ATOM = "http://www.w3.org/2005/Atom"
CONTENT = "http://purl.org/rss/1.0/modules/content/"
DC = "http://purl.org/dc/elements/1.1/"


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def rss(items: str, channel: str = "<title>Example</title><link>https://example.org/</link>",
        ns: str = "") -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"{ns}><channel>'
        f"{channel}{items}</channel></rss>"
    ).encode("utf-8")


def atom(entries: str, head: str = "<title>Example</title>") -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><feed xmlns="{ATOM}">'
        f"<id>urn:example:feed</id><updated>2026-11-25T00:00:00Z</updated>"
        f"{head}{entries}</feed>"
    ).encode("utf-8")


def one(data: bytes, **kwargs) -> Item:
    feed = parse_feed(data, url=URL, **kwargs)
    assert len(feed.items) == 1
    return feed.items[0]


# ---------------------------------------------------------------- real feeds


def test_nasa_rss_fixture():
    feed = parse_feed((FIXTURES / "nasa-news.rss").read_bytes(),
                      url="https://www.nasa.gov/news-release/feed/")
    assert feed.kind == "rss"
    assert feed.title == "NASA"
    assert feed.link == "https://www.nasa.gov"
    assert feed.truncated is False
    assert len(feed.items) == 2

    first, second = feed.items
    assert first.title == "NASA+ Content Coming to Big Screen at Greenbelt, Maryland Cinema"
    assert first.link == (
        "https://www.nasa.gov/nasa/nasa-content-coming-to-big-screen-at-greenbelt-maryland-cinema/"
    )
    assert first.id == "https://www.nasa.gov/?p=1067960"
    assert first.published == utc(2026, 11, 24, 21, 14, 36)
    assert first.summary.is_html is True
    assert first.summary.value.startswith("NASA announced Monday that NASA+ content")
    # CDATA: the character reference is part of the HTML, not decoded by XML.
    assert first.summary.value.endswith("the broadest possible [&#8230;]")
    assert first.content.is_html is True
    assert first.content.value.startswith('<div id="" class="hds-media')
    assert len(first.content.value) == 8515

    assert second.title == "Teams Lock in Mission Architecture for NASA’s SR-1 Freedom"
    assert second.published == utc(2026, 11, 24, 21, 4, 32)
    assert len(second.content.value) == 14396  # trailing newline removed


def test_nws_atom_fixture():
    feed = parse_feed((FIXTURES / "nws-alerts-ak.atom").read_bytes(),
                      url="https://api.weather.gov/alerts/active.atom?area=AK")
    assert feed.kind == "atom"
    assert feed.title == "Current watches, warnings, and advisories for Alaska"
    assert feed.link is None  # only rel="self"
    assert len(feed.items) == 2

    first = feed.items[0]
    assert first.title == (
        "Winter Weather Advisory issued November 24 at 2:48PM AKST until "
        "November 25 at 3:00AM AKST by NWS Juneau AK"
    )
    oid = "urn:oid:2.49.0.1.840.0.cf54e8621c00af085bd4d63d3f1746bac75ecac9.001.1"
    assert first.id == f"https://api.weather.gov/alerts/{oid}"
    assert first.link == f"https://api.weather.gov/alerts/{oid}.cap"
    assert first.published == utc(2026, 11, 24, 23, 48)
    assert first.summary.is_html is False
    assert first.summary.value.startswith("A front pushes northward across the panhandle")
    assert "\n\n* WHAT...Snow expected." in first.summary.value
    assert first.summary.value.endswith("the Haines Highway.")
    assert first.content is None
    assert feed.items[1].published == utc(2026, 11, 24, 22, 45)


def test_empty_atom_fixture():
    feed = parse_feed((FIXTURES / "nws-alerts-me-empty.atom").read_bytes(),
                      url="https://api.weather.gov/alerts/active.atom?area=ME")
    assert feed.kind == "atom"
    assert feed.title == "Current watches, warnings, and advisories for Maine"
    assert feed.items == ()
    assert feed.truncated is False


def test_types_are_frozen():
    item = one(rss("<item><title>A</title></item>"))
    assert isinstance(item, Item)
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.title = "B"
    feed = parse_feed(rss(""), url=URL)
    assert isinstance(feed, Feed) and isinstance(feed.items, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        Body("x", False).value = "y"


# ---------------------------------------------------------------- RSS


def test_rss_fields():
    item = one(rss(
        "<item><title>  Storm \n  warning </title>"
        "<link>https://example.org/a</link>"
        "<description>&lt;p&gt;Wind &amp;amp; rain&lt;/p&gt;</description>"
        "<guid>abc-1</guid><pubDate>Sun, 19 May 2002 15:21:36 GMT</pubDate></item>"
    ))
    assert item.title == "Storm warning"
    assert item.link == "https://example.org/a"
    assert item.id == "abc-1"
    assert item.published == utc(2002, 5, 19, 15, 21, 36)
    assert item.summary == Body("<p>Wind &amp; rain</p>", True)
    assert item.content is None


def test_rss_content_encoded_by_namespace_not_prefix():
    ns = f' xmlns:x="{CONTENT}" xmlns:content="http://example.com/other/"'
    item = one(rss(
        "<item><title>A</title><x:encoded><![CDATA[<p>Full</p>]]></x:encoded>"
        "<content:encoded>wrong</content:encoded></item>", ns=ns))
    assert item.content == Body("<p>Full</p>", True)


def test_rss_link_falls_back_to_permalink_guid():
    item = one(rss("<item><title>A</title><guid>https://example.org/p/1</guid></item>"))
    assert item.link == "https://example.org/p/1"
    assert item.id == "https://example.org/p/1"


def test_rss_guid_not_permalink_is_not_a_link():
    item = one(rss(
        '<item><title>A</title><guid isPermaLink="false">https://example.org/?p=1</guid></item>'))
    assert item.link is None
    assert item.id == "https://example.org/?p=1"


def test_rss_relative_and_unsafe_links():
    feed = parse_feed(rss(
        "<item><title>A</title><link>/story/1</link></item>"
        "<item><title>B</title><link>javascript:alert(1)</link></item>"
        "<item><title>C</title><link>data:text/html,x</link></item>"
        "<item><title>D</title><link>ftp://example.org/x</link></item>"
    ), url=URL)
    assert [i.link for i in feed.items] == ["https://example.org/story/1", None, None, None]


def test_rss_dc_date_fallback():
    ns = f' xmlns:dc="{DC}"'
    item = one(rss("<item><title>A</title><dc:date>2026-11-24T21:14:36-05:00</dc:date></item>",
                   ns=ns))
    assert item.published == utc(2026, 11, 25, 2, 14, 36)


def test_unreadable_date_falls_back():
    ns = f' xmlns:dc="{DC}"'
    item = one(rss("<item><title>A</title><pubDate>soon</pubDate>"
                   "<dc:date>2026-11-24T21:14:36Z</dc:date></item>", ns=ns))
    assert item.published == utc(2026, 11, 24, 21, 14, 36)
    item = one(atom("<entry><title>A</title><published>n/a</published>"
                    "<updated>2026-11-25T10:00:00Z</updated></entry>"))
    assert item.published == utc(2026, 11, 25, 10)


def test_duplicate_fields_use_the_first():
    item = one(rss("<item><title>First</title><title>Second</title>"
                   "<link>https://example.org/1</link><link>https://example.org/2</link></item>"))
    assert (item.title, item.link) == ("First", "https://example.org/1")


def test_rss_unreadable_date_is_none():
    item = one(rss("<item><title>A</title><pubDate>yesterday</pubDate></item>"))
    assert item.published is None


def test_rss_ignores_atom_link_in_channel_and_nested_elements():
    ns = f' xmlns:atom="{ATOM}"'
    channel = (
        '<atom:link href="https://example.org/self" rel="self"/>'
        "<title>Chan</title><image><title>Logo</title><link>https://img/</link></image>"
    )
    feed = parse_feed(rss("<item><title>A</title><source><title>X</title></source></item>",
                          channel=channel, ns=ns), url=URL)
    assert feed.title == "Chan"
    assert feed.link is None
    assert feed.items[0].title == "A"


def test_rss_old_version_accepted():
    data = b'<rss version="0.91"><channel><title>Old</title><item><title>A</title></item></channel></rss>'
    feed = parse_feed(data, url=URL)
    assert feed.kind == "rss" and feed.items[0].title == "A"


def test_rss_without_channel_is_error():
    with pytest.raises(FeedError):
        parse_feed(b'<rss version="2.0"></rss>', url=URL)


def test_empty_values_are_none_and_empty_items_skipped():
    feed = parse_feed(rss(
        "<item><title>   </title><description></description><link>https://e.org/1</link></item>"
        "<item><description>Only a summary</description></item>"
    ), url=URL)
    assert len(feed.items) == 1
    assert feed.items[0].title is None
    assert feed.items[0].summary == Body("Only a summary", True)


def test_summary_keeps_inner_whitespace():
    item = one(rss("<item><description>\n  line one\n\n  line two  \n</description></item>"))
    assert item.summary.value == "line one\n\n  line two"


def test_max_items_truncates():
    items = "".join(f"<item><title>T{n}</title></item>" for n in range(5))
    feed = parse_feed(rss(items), url=URL, max_items=3)
    assert [i.title for i in feed.items] == ["T0", "T1", "T2"]
    assert feed.truncated is True
    assert parse_feed(rss(items), url=URL, max_items=5).truncated is False


def test_skipped_items_do_not_count_toward_max_items():
    items = "<item></item>" * 3 + "<item><title>A</title></item><item><title>B</title></item>"
    feed = parse_feed(rss(items), url=URL, max_items=2)
    assert [i.title for i in feed.items] == ["A", "B"]
    assert feed.truncated is False


# ---------------------------------------------------------------- Atom


def test_atom_fields_and_feed_link():
    feed = parse_feed(atom(
        "<entry><id> urn:x:1 </id><title>Plain &amp; simple</title>"
        '<link rel="alternate" type="text/html" href="https://example.org/1"/>'
        "<updated>2003-12-13T18:30:02Z</updated>"
        "<summary>Short</summary><content type=\"html\">&lt;p&gt;Long&lt;/p&gt;</content></entry>",
        head='<title>Example</title><link href="https://example.org/"/>'
             '<link rel="self" href="https://example.org/feed"/>',
    ), url=URL)
    assert feed.link == "https://example.org/"
    item = feed.items[0]
    assert item.id == "urn:x:1"
    assert item.title == "Plain & simple"
    assert item.link == "https://example.org/1"
    assert item.published == utc(2003, 12, 13, 18, 30, 2)
    assert item.summary == Body("Short", False)
    assert item.content == Body("<p>Long</p>", True)


def test_atom_published_preferred_over_updated():
    item = one(atom("<entry><title>A</title><updated>2026-11-25T10:00:00Z</updated>"
                    "<published>2026-11-24T10:00:00+02:00</published></entry>"))
    assert item.published == utc(2026, 11, 24, 8)


def test_atom_link_choice():
    item = one(atom(
        "<entry><title>A</title>"
        '<link rel="self" href="https://example.org/self"/>'
        '<link rel="alternate" type="application/json" href="https://example.org/json"/>'
        '<link type="text/html" href="page/1"/>'
        "</entry>"))
    assert item.link == "https://example.org/feeds/page/1"


def test_atom_link_falls_back_to_first_alternate():
    item = one(atom(
        "<entry><title>A</title>"
        '<link rel="enclosure" href="https://example.org/a.mp3"/>'
        '<link rel="alternate" type="application/json" href="https://example.org/json"/>'
        '<link href="https://example.org/second"/>'
        "</entry>"))
    assert item.link == "https://example.org/json"


def test_atom_html_title_tags_removed():
    item = one(atom(
        '<entry><title type="html">Snow &lt;b&gt;heavy&lt;/b&gt; &amp;amp; ice</title></entry>'))
    assert item.title == "Snow heavy & ice"


def test_atom_xhtml_constructs():
    xhtml = 'xmlns:h="http://www.w3.org/1999/xhtml"'
    item = one(atom(
        f'<entry><title type="xhtml"><h:div {xhtml}>Big <h:em>news</h:em></h:div></title>'
        f'<content type="xhtml"><h:div {xhtml}><h:p>One</h:p><h:p>Two</h:p></h:div></content>'
        "</entry>"))
    assert item.title == "Big news"
    assert item.content == Body("OneTwo", False)


def test_atom_content_types():
    def content(attrs: str, body: str = "x") -> Body | None:
        return one(atom(f"<entry><title>A</title><content {attrs}>{body}</content></entry>")).content

    assert content('type="TEXT"') == Body("x", False)
    assert content('type="text/plain"') == Body("x", False)
    assert content('type="text/html"', "&lt;b&gt;x&lt;/b&gt;") == Body("<b>x</b>", True)
    assert content('type="image/png"', "iVBORw0K") is None
    assert content('src="https://example.org/full"') is None
    assert content('type="html" src="https://example.org/full"') is None


def test_atom_unknown_title_type_is_none():
    feed = parse_feed(atom('<entry><title type="image/png">x</title><summary>S</summary></entry>'),
                      url=URL)
    assert feed.items[0].title is None


def test_atom_ignores_other_namespaces():
    item = one(atom('<entry><title>A</title><x:title xmlns:x="urn:other">B</x:title>'
                    '<summary xmlns="urn:other">not atom</summary></entry>'))
    assert item.title == "A"
    assert item.summary is None


# ---------------------------------------------------------------- encodings


def test_iso_8859_1_declared():
    data = ('<?xml version="1.0" encoding="ISO-8859-1"?><rss><channel>'
            "<item><title>Café</title></item></channel></rss>").encode("latin-1")
    assert one(data).title == "Café"


def test_windows_1252_declared():
    data = b'<?xml version="1.0" encoding="windows-1252"?><rss><channel><item><title>NASA\x92s</title></item></channel></rss>'
    assert one(data).title == "NASA’s"


def test_utf16_with_bom():
    text = '<?xml version="1.0" encoding="UTF-16"?><rss><channel><item><title>°F</title></item></channel></rss>'
    assert one(text.encode("utf-16")).title == "°F"


# ---------------------------------------------------------------- refusals


@pytest.mark.parametrize("data", [
    b"",
    b"not xml at all",
    b"<rss><channel><item><title>A</title></channel></rss>",
    b"<rss><channel><item><title>A&nbsp;B</title></item></channel></rss>",
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><item/></rdf:RDF>',
    b"<html><body>Not a feed</body></html>",
    b'<feed xmlns="urn:not-atom"><entry><title>A</title></entry></feed>',
    b'<rss:rss xmlns:rss="urn:x"><channel/></rss:rss>',
])
def test_not_a_feed(data):
    with pytest.raises(FeedError):
        parse_feed(data, url=URL)


def test_feed_error_is_value_error():
    assert issubclass(FeedError, ValueError)


def test_doctype_refused_even_without_entities():
    data = b'<?xml version="1.0"?><!DOCTYPE rss><rss><channel><title>A</title></channel></rss>'
    with pytest.raises(FeedError):
        parse_feed(data, url=URL)


def test_doctype_refused_in_utf16():
    text = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE rss><rss><channel/></rss>'
    with pytest.raises(FeedError):
        parse_feed(text.encode("utf-16"), url=URL)


def test_billion_laughs_refused_quickly():
    entities = '<!ENTITY a0 "lol">' + "".join(
        f'<!ENTITY a{n} "{("&a" + str(n - 1) + ";") * 10}">' for n in range(1, 10))
    data = (f'<?xml version="1.0"?><!DOCTYPE rss [{entities}]>'
            "<rss><channel><item><title>&a9;</title></item></channel></rss>").encode()
    start = time.perf_counter()
    with pytest.raises(FeedError):
        parse_feed(data, url=URL)
    assert time.perf_counter() - start < 1


def test_external_entity_refused(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    data = (f'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x SYSTEM "{secret.as_uri()}">]>'
            "<rss><channel><item><title>&x;</title></item></channel></rss>").encode()
    with pytest.raises(FeedError):
        parse_feed(data, url=URL)


def test_size_limit():
    data = rss("<item><title>A</title></item>")
    assert parse_feed(data, url=URL, max_bytes=len(data)).items[0].title == "A"
    with pytest.raises(FeedError):
        parse_feed(data, url=URL, max_bytes=len(data) - 1)


def test_default_size_limit():
    data = rss("<item><title>A</title><description>" + "x" * 2_000_000 + "</description></item>")
    with pytest.raises(FeedError):
        parse_feed(data, url=URL)


def test_nesting_limit():
    ok = "<x>" * 90 + "</x>" * 90
    assert one(rss(f"<item><title>A</title>{ok}</item>")).title == "A"
    deep = "<x>" * 101 + "</x>" * 101
    with pytest.raises(FeedError):
        parse_feed(rss(f"<item><title>A</title>{deep}</item>"), url=URL)


def test_very_deep_nesting_is_feed_error_not_recursion():
    deep = "<x>" * 200_000 + "</x>" * 200_000
    with pytest.raises(FeedError):
        parse_feed(rss(deep), url=URL)


def test_large_feed_is_fast():
    items = "".join(
        f"<item><title>Headline {n}</title><link>https://example.org/{n}</link>"
        f"<description>&lt;p&gt;Summary {n} {'word ' * 40}&lt;/p&gt;</description>"
        f"<pubDate>Tue, 24 Nov 2026 21:14:36 +0000</pubDate><guid>g{n}</guid></item>"
        for n in range(5000))
    data = rss(items)
    assert len(data) > 1_000_000
    start = time.perf_counter()
    feed = parse_feed(data, url=URL, max_bytes=3_000_000, max_items=10_000)
    assert time.perf_counter() - start < 3
    assert len(feed.items) == 5000
    assert feed.items[-1].link == "https://example.org/4999"


# ---------------------------------------------------------------- parse_date


@pytest.mark.parametrize("value, expected", [
    ("Sun, 19 May 2002 15:21:36 GMT", utc(2002, 5, 19, 15, 21, 36)),
    ("Tue, 24 Nov 2026 21:14:36 +0000", utc(2026, 11, 24, 21, 14, 36)),
    ("Tue, 24 Nov 2026 16:14:36 -0500", utc(2026, 11, 24, 21, 14, 36)),
    ("19 May 02 15:21 EST", utc(2002, 5, 19, 20, 21)),
    ("Sun, 19 May 2002 15:21:36 -0000", utc(2002, 5, 19, 15, 21, 36)),
    ("  2003-12-13T18:30:02Z  ", utc(2003, 12, 13, 18, 30, 2)),
    ("2026-11-24T14:48:00-09:00", utc(2026, 11, 24, 23, 48)),
    ("2003-12-13T18:30:02.25Z", datetime(2003, 12, 13, 18, 30, 2, 250000, tzinfo=timezone.utc)),
    ("2003-12-13T18:30:02", utc(2003, 12, 13, 18, 30, 2)),
    ("2003-12-13", utc(2003, 12, 13)),
])
def test_parse_date(value, expected):
    result = parse_date(value)
    assert result == expected
    assert result.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("value", ["", "   ", "yesterday", "Tue, 99 Foo 2026", "2026-13-45T99:00:00Z"])
def test_parse_date_unreadable(value):
    assert parse_date(value) is None


# ------------------------------------------------- link, date and title hardening

BAD_LINKS = [
    "http://[::1",                    # urlsplit raises ValueError
    "http://example.org/x&#x09;y",    # tab
    "http://example.org/&#x0a;z",     # newline
    "http://example.org/a&#x0d;b",    # carriage return
    "http://example.org/a&#x7f;b",    # DEL
    "http://example.org/a b",         # space
    "http://example.org/a&#x85;b",    # NEL, str.isspace
    "http://example.org/a&#xa0;b",    # no-break space, str.isspace
    "http://nasa.gov@evil.example/",  # user name
    "http://user:pw@evil.example/",   # user name and password
    "http://:pw@evil.example/",       # password only
    "http://@/",                      # empty host
    "http:///path",                   # empty host
]


@pytest.mark.parametrize("bad", BAD_LINKS)
def test_rss_bad_link_is_none(bad):
    item = one(rss(f"<item><title>A</title><link>{bad}</link></item>"))
    assert item.link is None
    assert item.title == "A"


@pytest.mark.parametrize("bad", BAD_LINKS)
def test_rss_bad_guid_is_not_a_link(bad):
    item = one(rss(f"<item><title>A</title><guid>{bad}</guid></item>"))
    assert item.link is None


@pytest.mark.parametrize("bad", BAD_LINKS)
def test_atom_bad_href_is_none(bad):
    item = one(atom(f'<entry><title>A</title><link href="{bad}"/></entry>'))
    assert item.link is None


def test_bad_item_link_does_not_fail_feed():
    feed = parse_feed(rss(
        "<item><title>A</title><link>https://example.org/a</link></item>"
        "<item><title>B</title><link>http://[::1</link></item>"
        "<item><title>C</title><guid>http://[::1</guid></item>"
        "<item><title>D</title><link>https://example.org/d</link></item>"
    ), url=URL)
    assert [(i.title, i.link) for i in feed.items] == [
        ("A", "https://example.org/a"), ("B", None), ("C", None),
        ("D", "https://example.org/d")]


def test_bad_feed_link_does_not_fail_feed():
    feed = parse_feed(rss("", channel="<title>T</title><link>http://[::1</link>"), url=URL)
    assert feed.title == "T" and feed.link is None
    feed = parse_feed(atom("", head='<title>T</title><link href="http://[::1"/>'), url=URL)
    assert feed.title == "T" and feed.link is None


def test_good_links_still_accepted():
    feed = parse_feed(rss(
        "<item><title>A</title><link>  https://example.org/a  </link></item>"
        "<item><title>B</title><link>//cdn.example.net/b</link></item>"
        "<item><title>C</title><guid>  https://example.org/c  </guid></item>"
    ), url=URL)
    assert [i.link for i in feed.items] == [
        "https://example.org/a", "https://cdn.example.net/b", "https://example.org/c"]


@pytest.mark.parametrize("value, expected", [
    ("2003-12-13t18:30:02z", utc(2003, 12, 13, 18, 30, 2)),
    ("2003-12-13T18:30:02z", utc(2003, 12, 13, 18, 30, 2)),
    ("2003-12-13t18:30:02Z", utc(2003, 12, 13, 18, 30, 2)),
    ("2026-11-24t14:48:00-09:00", utc(2026, 11, 24, 23, 48)),
])
def test_parse_date_lowercase_t_and_z(value, expected):
    assert parse_date(value) == expected


def test_atom_html_title_drops_script_and_style():
    item = one(atom(
        '<entry><title type="html">&lt;script&gt;evil()&lt;/script&gt;Snow'
        ' &lt;style&gt;b{color:red}&lt;/style&gt;today</title></entry>'))
    assert item.title == "Snow today"


def test_atom_xhtml_title_drops_script_and_style():
    xhtml = 'xmlns:h="http://www.w3.org/1999/xhtml"'
    item = one(atom(
        f'<entry><title type="xhtml"><h:div {xhtml}><h:script>evil()</h:script>Snow '
        "<h:style>b{color:red}</h:style>today</h:div></title></entry>"))
    assert item.title == "Snow today"


def test_atom_link_rel_iri_and_attribute_whitespace():
    iri = "http://www.iana.org/assignments/relation/alternate"
    assert one(atom(
        f'<entry><title>A</title><link rel="{iri}" href="https://example.org/a"/></entry>'
    )).link == "https://example.org/a"
    assert one(atom(
        '<entry><title>A</title><link rel=" Alternate " href="https://example.org/a"/></entry>'
    )).link == "https://example.org/a"
    assert one(atom(
        "<entry><title>A</title>"
        '<link rel="alternate" type="application/json" href="https://example.org/json"/>'
        '<link rel="alternate" type=" TEXT/HTML " href="https://example.org/page"/>'
        "</entry>")).link == "https://example.org/page"


@pytest.mark.parametrize("data", [
    rss("<item><title>A</title></item>").decode(),
    None,
    123,
    [60, 114],
])
def test_non_bytes_data_refused(data):
    with pytest.raises(FeedError):
        parse_feed(data, url=URL)


@pytest.mark.parametrize("wrap", [bytes, bytearray, memoryview])
def test_bytes_like_data_accepted(wrap):
    data = wrap(rss("<item><title>A</title></item>"))
    assert parse_feed(data, url=URL).items[0].title == "A"


def test_nesting_limit_is_exactly_100():
    def doc(total: int) -> bytes:
        inner = total - 2  # rss and channel
        return ("<rss><channel>" + "<x>" * inner + "</x>" * inner
                + "</channel></rss>").encode()
    assert parse_feed(doc(100), url=URL).kind == "rss"
    with pytest.raises(FeedError):
        parse_feed(doc(101), url=URL)
