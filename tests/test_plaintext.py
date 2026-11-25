"""Contract tests for federated_cms.plaintext; see docs/TEXT-EXTRACTOR.md."""

import re
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

from federated_cms.plaintext import (
    Document, ExtractError, clean_nws, decode, extract, html_to_text,
    is_nws_product, render, to_ascii,
)

FIXTURES = Path(__file__).parent / "fixtures" / "plaintext"
WHEN = datetime(2026, 11, 25, 1, 0, tzinfo=timezone.utc)
URL = "https://tgftp.nws.noaa.gov/data/forecasts/zone/me/mez023.txt"
PRINTABLE = re.compile(r"[\n\x20-\x7e]*\Z")


def nws_raw():
    return (FIXTURES / "mez023.txt").read_bytes()


def nws_expected():
    return (FIXTURES / "mez023.expected.txt").read_text(encoding="ascii")


def body(markup, width=69):
    return html_to_text(markup, width=width)[1]


class DecodeTests(unittest.TestCase):
    def test_utf8_without_declaration(self):
        self.assertEqual(decode("café".encode(), None), ("café", "utf-8"))

    def test_declared_charset_wins(self):
        self.assertEqual(decode("café".encode("cp1252"), "text/plain; charset=windows-1252"),
                         ("café", "cp1252"))

    def test_latin1_and_ascii_labels_mean_windows_1252(self):
        self.assertEqual(decode(b"\x80", 'text/html; charset="ISO-8859-1"'), ("€", "cp1252"))
        self.assertEqual(decode(b"\x93x\x94", "text/plain; charset=us-ascii"),
                         ("“x”", "cp1252"))

    def test_bom_overrides_declaration_and_is_removed(self):
        self.assertEqual(decode(b"\xef\xbb\xbfcaf\xc3\xa9", "text/plain; charset=iso-8859-1"),
                         ("café", "utf-8"))
        self.assertEqual(decode("hi".encode("utf-16"), None), ("hi", "utf-16"))

    def test_html_meta_charset(self):
        page = b'<html><head><meta charset="windows-1252"></head><body>caf\xe9</body></html>'
        text, enc = decode(page, "text/html")
        self.assertIn("café", text)
        self.assertEqual(enc, "cp1252")

    def test_html_meta_http_equiv(self):
        page = (b'<html><head><meta http-equiv="Content-Type" '
                b'content="text/html; charset=iso-8859-1"></head><body>caf\xe9</body></html>')
        self.assertEqual(decode(page, "text/html")[1], "cp1252")

    def test_meta_ignored_when_not_html(self):
        self.assertEqual(decode(b'<meta charset="windows-1252">\xc3\xa9', "text/plain"),
                         ('<meta charset="windows-1252">é', "utf-8"))

    def test_windows_1252_fallback(self):
        self.assertEqual(decode(b"caf\xe9", None), ("café", "cp1252"))

    def test_bytes_undefined_in_python_cp1252_follow_whatwg(self):
        self.assertEqual(decode(b"a\x81\x8d\x8f\x90\x9db", None),
                         ("a\x81\x8d\x8f\x90\x9db", "cp1252"))

    def test_unknown_label_falls_through(self):
        self.assertEqual(decode("é".encode(), "text/plain; charset=x-nonsense"), ("é", "utf-8"))

    def test_failing_utf8_declaration_is_retried_not_trimmed(self):
        self.assertEqual(decode(b"caf\xe9", "text/plain; charset=utf-8"), ("café", "cp1252"))

    def test_utf8_no_break_space_is_not_mojibake(self):
        self.assertEqual(decode(b"A\xc2\xa0B", None), ("A\xa0B", "utf-8"))


class ToAsciiTests(unittest.TestCase):
    def test_anyascii_example(self):
        self.assertEqual(to_ascii("άνθρωποι"), "anthropoi")
        self.assertEqual(to_ascii("北京"), "BeiJing")

    def test_degrees(self):
        self.assertEqual(to_ascii("45°F and 7°C at 30° bearing"), "45F and 7C at 30 deg bearing")
        self.assertEqual(to_ascii("43.64°N 70.3°W"), "43.64N 70.3W")

    def test_typographic_punctuation(self):
        self.assertEqual(to_ascii("“Hi” — it’s…"), '"Hi" - it\'s...')

    def test_no_break_space(self):
        self.assertEqual(to_ascii("A\xa0B"), "A B")

    def test_line_endings(self):
        self.assertEqual(to_ascii("a\r\nb\rc"), "a\nb\nc")

    def test_tabs_expand(self):
        self.assertEqual(to_ascii("a\tb"), "a       b")

    def test_invisible_characters_removed(self):
        self.assertEqual(to_ascii("soft­hy​p‌‍hen﻿"), "softhyphen")

    def test_control_characters_removed(self):
        self.assertEqual(to_ascii("a\x00b\x07c\x7fd\x81e"), "abcde")

    def test_trailing_spaces_removed(self):
        self.assertEqual(to_ascii("a  \nb "), "a\nb")

    def test_output_is_printable_ascii(self):
        self.assertRegex(to_ascii("Zoë 😀 ½ €   \x1b[31m\tend"), PRINTABLE)


class NwsTests(unittest.TestCase):
    def test_fixture_detected_and_cleaned(self):
        text = nws_raw().decode("ascii")
        self.assertTrue(is_nws_product(text))
        self.assertEqual(clean_nws(text), nws_expected())

    def test_detection(self):
        self.assertTrue(is_nws_product("FPUS51 KGYX 250049\nZFPGYX\n\nText\n"))
        self.assertTrue(is_nws_product("\n\nFPUS51 KGYX 250049 CCA\nZFPGYX\nText\n"))
        self.assertFalse(is_nws_product("Hello\nWorld\n"))
        self.assertFalse(is_nws_product("FPUS51 KGYX 250049\nnot an id line\n"))

    def test_awips_line_only_directly_after_heading(self):
        product = "FPUS51 KGYX 250049\nZFPGYX\nText.\n\n$$\n\nSMITH\n"
        self.assertEqual(clean_nws(product), "Text.\n\nSMITH")
        self.assertEqual(clean_nws("FPUS51 KGYX 250049\nnot an id\nSMITH\n"), "not an id\nSMITH")

    def test_segmented_product(self):
        product = (
            "WWUS41 KGYX 250100\nWSWGYX\n\n"
            "URGENT - WINTER WEATHER MESSAGE\nNational Weather Service Gray ME\n\n"
            "MEZ007>009-012-013-\n018>028-251500-\n"
            "/O.NEW.KGYX.WS.W.0001.261126T0000Z-261127T0000Z/\n"
            "Northern Oxford-Southern Oxford-\n...WINTER STORM WARNING IN EFFECT...\n\n"
            "* WHAT...Heavy snow.   \n\n&&\n\nPrecautionary text.\n\n$$\n\nABC\n\n"
        )
        self.assertEqual(clean_nws(product), (
            "URGENT - WINTER WEATHER MESSAGE\nNational Weather Service Gray ME\n\n"
            "Northern Oxford-Southern Oxford-\n...WINTER STORM WARNING IN EFFECT...\n\n"
            "* WHAT...Heavy snow.\n\nPrecautionary text.\n\nABC"
        ))

    def test_not_rewrapped(self):
        lines = nws_expected().splitlines()
        self.assertIn("and Kittery", lines)
        self.assertTrue(all(len(line) <= 69 for line in lines))


class HtmlTitleAndRootTests(unittest.TestCase):
    def test_title(self):
        self.assertEqual(html_to_text("<title>  A \n page </title><p>x</p>")[0], "A page")
        self.assertEqual(html_to_text("<title> </title><h1>Head</h1><p>x</p>")[0], "Head")
        self.assertIsNone(html_to_text("<p>x</p>")[0])

    def test_main_preferred(self):
        page = ("<body><nav>Menu</nav><p>Outside</p><main><p>Inside</p></main>"
                "<article><p>Later</p></article></body>")
        self.assertEqual(body(page), "Inside")

    def test_article_then_body(self):
        self.assertEqual(body("<body><p>Outside</p><article><p>Story</p></article></body>"), "Story")
        self.assertEqual(body("<body><p>Only</p></body>"), "Only")

    def test_dropped_elements(self):
        page = ("<head><style>p{}</style></head><body><script>var x=1;</script>"
                "<nav><p>menu</p></nav><aside>ad</aside><form><p>search</p></form>"
                "<noscript>enable js</noscript><template><p>t</p></template>"
                "<svg><text>s</text></svg><button>Go</button><dialog>d</dialog>"
                "<p>kept</p></body>")
        self.assertEqual(body(page), "kept")

    def test_landmark_roles_dropped(self):
        page = ('<div role="navigation">n</div><div role="search">s</div><div role="banner">b</div>'
                '<div role="contentinfo">c</div><div role="complementary">x</div><p role="note">kept</p>')
        self.assertEqual(body(page), "kept")

    def test_hidden_elements(self):
        page = '<div hidden><p>x</p></div><p aria-hidden="true">y</p><p>z</p>'
        self.assertEqual(body(page), "z")

    def test_header_footer_outside_article_dropped(self):
        page = "<body><header>Site</header><p>Text</p><footer>Copyright</footer></body>"
        self.assertEqual(body(page), "Text")

    def test_header_inside_article_kept(self):
        page = "<article><header><h1>T</h1></header><p>x</p><footer>By me</footer></article>"
        self.assertEqual(body(page), "T\n=\n\nx\n\nBy me")


class HtmlStructureTests(unittest.TestCase):
    def test_paragraphs_and_whitespace(self):
        self.assertEqual(body("<p>  one \n two </p><div>three</div>"), "one two\n\nthree")

    def test_empty_blocks_produce_nothing(self):
        self.assertEqual(body("<p> </p><div></div><p>x</p><p></p>"), "x")

    def test_headings(self):
        self.assertEqual(body("<h1>Big</h1><h2>Sub</h2><h3>Small</h3><p>x</p>"),
                         "Big\n===\n\nSub\n---\n\nSmall\n\nx")

    def test_lists(self):
        page = "<ul><li>One</li><li>Two<ul><li>Inner</li></ul></li></ul><ol><li>A</li><li>B</li></ol>"
        self.assertEqual(body(page), "* One\n* Two\n  * Inner\n\n1. A\n2. B")

    def test_list_counter_restarts(self):
        self.assertEqual(body("<ol><li>a</li></ol><ol><li>b</li></ol>"), "1. a\n\n1. b")

    def test_list_wrapping_aligns(self):
        self.assertEqual(body("<ul><li>alpha beta gamma delta epsilon</li></ul>", width=20),
                         "* alpha beta gamma\n  delta epsilon")
        self.assertEqual(body("<ol><li>alpha beta gamma delta epsilon</li></ol>", width=20),
                         "1. alpha beta gamma\n   delta epsilon")

    def test_blocks_inside_list_items_break_lines(self):
        page = ('<ul><li><div><p>Tonight</p><p><img alt="Clear, low 21."></p>'
                '<p>Low: 21 &deg;F</p><p>Clear</p></div></li><li>a<br>b</li></ul>')
        self.assertEqual(body(page), "* Tonight\n  Low: 21 \u00b0F\n  Clear\n* a\n  b")

    def test_blocks_inside_quotes_and_cells(self):
        self.assertEqual(body("<blockquote><p>one</p><p>two</p></blockquote>"), "> one\n> two")
        self.assertEqual(body("<table><tr><td><p>a</p><p>b</p></td><td>c</td></tr></table>"), "a b | c")
        self.assertEqual(body("<h2><span>A</span><div>B</div></h2>"), "A B\n---")

    def test_adjacent_links_are_separated(self):
        page = '<p><a href="/1">One</a><a href="/2">Two</a> <a href="/3">Three</a>.</p>'
        self.assertEqual(body(page), "One Two Three.")

    def test_definition_list(self):
        self.assertEqual(body("<dl><dt>Term</dt><dd>Meaning</dd></dl>"), "Term\n  Meaning")

    def test_blockquote(self):
        self.assertEqual(body("<blockquote>one two three four five six</blockquote>", width=20),
                         "> one two three four\n> five six")

    def test_pre_kept_exactly(self):
        self.assertEqual(body("<p>a</p><pre>\n  x  y\n\tz\n\n</pre><p>b</p>"),
                         "a\n\n  x  y\n        z\n\nb")

    def test_br_and_hr(self):
        self.assertEqual(body("<p>line one<br>line two</p><hr><p>b</p>"),
                         "line one\nline two\n\n---\n\nb")

    def test_table(self):
        page = ("<table><tr><th>Day</th><th>High</th></tr>"
                "<tr><td>Wed</td><td> 45 </td></tr></table><p>after</p>")
        self.assertEqual(body(page), "Day | High\nWed | 45\n\nafter")

    def test_images_and_links(self):
        self.assertEqual(body('<p>See <img src=x alt="map"> here <img src=y></p>'), "See here")
        self.assertEqual(body('<p>Read <a href="https://x.example/">the docs</a>.</p>'), "Read the docs.")

    def test_character_references(self):
        self.assertEqual(body("<p>Fish &amp; chips &lt;3 &#8212; ok</p>"), "Fish & chips <3 — ok")

    def test_wrapping(self):
        text = body("<p>" + "word " * 30 + "</p>", width=30)
        self.assertTrue(all(len(line) <= 30 for line in text.splitlines()))
        self.assertEqual(text.split(), ["word"] * 30)

    def test_long_word_not_broken(self):
        url = "https://example.org/" + "a" * 80
        self.assertEqual(body(f"<p>go {url} now</p>", width=30), f"go\n{url}\nnow")


class HtmlParsingTests(unittest.TestCase):
    def test_implied_end_tags(self):
        self.assertEqual(body("<p>one<p>two"), "one\n\ntwo")
        self.assertEqual(body("<ul><li>a<li>b</ul>"), "* a\n* b")
        self.assertEqual(body("<dl><dt>t<dd>d</dl>"), "t\n  d")
        self.assertEqual(body("<table><tr><td>a<td>b<tr><td>c</table>"), "a | b\nc")

    def test_stray_end_tag_ignored(self):
        self.assertEqual(body("<p>a</span>b</p>"), "ab")

    def test_dropped_element_contains_unclosed_tags(self):
        self.assertEqual(body("<nav><div><p>menu</nav><p>body text</p>"), "body text")

    def test_unclosed_dropped_element_drops_rest(self):
        self.assertEqual(body("<p>kept</p><script>var a = '<p>x</p>';"), "kept")

    def test_void_elements_do_not_open_scopes(self):
        self.assertEqual(body("<p>a<br>b<img alt=c>d<wbr>e</p><p>f</p>"), "a\nbde\n\nf")

    def test_deep_nesting_is_flattened(self):
        self.assertEqual(body("<div>" * 5000 + "deep" + "</div>" * 5000 + "<p>after</p>"), "deep\n\nafter")
        deep = "<span>" * 300
        self.assertEqual(body(f"<p>{deep}a<script>x()</script>b</p>"), "ab")


class ExtractTests(unittest.TestCase):
    def test_nws(self):
        doc = extract(nws_raw(), url=URL, fetched_at=WHEN, content_type="text/plain")
        self.assertEqual(doc, Document(URL, WHEN, "nws", None, nws_expected(), False, len(nws_raw())))

    def test_document_is_frozen(self):
        doc = extract(b"x", url=URL, fetched_at=WHEN)
        with self.assertRaises(FrozenInstanceError):
            doc.text = "y"

    def test_html_by_type_and_by_sniffing(self):
        page = "<!DOCTYPE html><title>Café</title><p>45°F — ok</p>".encode()
        for ctype in ("text/html; charset=utf-8", "application/xhtml+xml", None):
            doc = extract(page, url=URL, fetched_at=WHEN, content_type=ctype)
            self.assertEqual((doc.kind, doc.title, doc.text), ("html", "Cafe", "45F - ok"))

    def test_html_not_sniffed_when_type_says_text(self):
        doc = extract(b"<html><p>x</p></html>", url=URL, fetched_at=WHEN, content_type="text/plain")
        self.assertEqual((doc.kind, doc.text), ("text", "<html><p>x</p></html>"))

    def test_html_with_bom_is_sniffed(self):
        doc = extract(b"\xef\xbb\xbf<!doctype html><p>x</p>", url=URL, fetched_at=WHEN)
        self.assertEqual((doc.kind, doc.text), ("html", "x"))

    def test_large_windows_1252_input(self):
        doc = extract(b"caf\xe9 " * 300_000, url=URL, fetched_at=WHEN)
        self.assertTrue(doc.text.startswith("cafe cafe"))

    def test_plain_text(self):
        doc = extract(b"\n\nline1  \r\n\r\n\r\n  indented\n\n", url=URL, fetched_at=WHEN)
        self.assertEqual((doc.kind, doc.title, doc.text), ("text", None, "line1\n\n  indented"))

    def test_width_reaches_html(self):
        doc = extract(b"<p>alpha beta gamma delta</p>", url=URL, fetched_at=WHEN,
                      content_type="text/html", width=20)
        self.assertEqual(doc.text, "alpha beta gamma\ndelta")

    def test_truncation_at_line(self):
        lines = [f"line{i:03d}" for i in range(300)]
        doc = extract("\n".join(lines).encode(), url=URL, fetched_at=WHEN, max_chars=200)
        self.assertTrue(doc.truncated)
        self.assertEqual(doc.text, "\n".join(lines[:25]))

    def test_truncation_without_newline(self):
        doc = extract(b"x" * 500, url=URL, fetched_at=WHEN, max_chars=200)
        self.assertEqual((doc.text, doc.truncated), ("x" * 200, True))

    def test_fetched_at_converted_to_utc(self):
        eastern = datetime(2026, 11, 24, 20, 0, tzinfo=timezone(timedelta(hours=-5)))
        doc = extract(b"x", url=URL, fetched_at=eastern)
        self.assertEqual(doc.fetched_at, WHEN)
        self.assertEqual(doc.fetched_at.utcoffset(), timedelta(0))

    def test_errors(self):
        self.assertTrue(issubclass(ExtractError, ValueError))
        cases = [
            dict(fetched_at=datetime(2026, 11, 25, 1, 0)),
            dict(width=19),
            dict(max_chars=199),
            dict(max_input_bytes=10),
        ]
        for extra in cases:
            kwargs = dict(url=URL, fetched_at=WHEN) | extra
            with self.subTest(extra=extra), self.assertRaises(ExtractError):
                extract(b"12345678901", **kwargs)


class RenderTests(unittest.TestCase):
    def test_with_title(self):
        doc = Document("https://example.org/a", WHEN, "html", "Title", "Body", False, 10)
        self.assertEqual(render(doc), "Title\nSource: https://example.org/a\n"
                                      "Fetched: 2026-11-25 01:00 UTC\n\nBody\n")

    def test_without_title(self):
        doc = Document(URL, WHEN, "nws", None, "Body", False, 10)
        self.assertEqual(render(doc), f"Source: {URL}\nFetched: 2026-11-25 01:00 UTC\n\nBody\n")

    def test_truncated(self):
        doc = Document(URL, WHEN, "text", None, "Body", True, 10)
        self.assertTrue(render(doc).endswith(
            "\n\nBody\n\n[Truncated: the full text is longer than this reply.]\n"))

    def test_url_percent_encoded(self):
        doc = Document("https://example.org/café?q=a b%20&x=[1]", WHEN, "text", None, "B", False, 1)
        self.assertIn("Source: https://example.org/caf%C3%A9?q=a%20b%20&x=[1]\n", render(doc))

    def test_end_to_end_is_ascii(self):
        out = render(extract(nws_raw(), url=URL, fetched_at=WHEN))
        self.assertTrue(out.startswith(f"Source: {URL}\nFetched: 2026-11-25 01:00 UTC\n\n"
                                       "Zone Forecasts for New Hampshire and western Maine\n"))
        self.assertTrue(out.endswith(".TUESDAY...Partly sunny. Highs in the upper 40s.\n"))
        self.assertRegex(out, PRINTABLE)
        self.assertNotIn("$$", out)
        self.assertNotIn("Expires:", out)
        self.assertNotIn("MEZ023", out)


if __name__ == "__main__":
    unittest.main()
