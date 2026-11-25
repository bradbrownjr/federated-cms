"""Contract tests for Winlink-style service messages (INQUIRY catalog requests
and QTH position reports). See docs/INQUIRY-ADAPTER.md for sources.

These tests define the behaviour of federated_cms.inquiry. Do not change them
to make an implementation pass.
"""
from datetime import datetime, timezone
import math
import unittest

from federated_cms.inquiry import (
    InquiryError,
    InquiryItem,
    PositionReportError,
    address_local_part,
    parse_inquiry,
    parse_position_report,
    service_for,
)


class AddressTests(unittest.TestCase):
    def test_local_part_ignores_domain_and_case(self):
        for address in ("INQUIRY", "inquiry", "INQUIRY@winlink.org",
                        "Inquiry@mainepacketradio.org", "  inquiry@x  "):
            with self.subTest(address=address):
                self.assertEqual(address_local_part(address), "INQUIRY")

    def test_local_part_strips_smtp_prefix(self):
        self.assertEqual(address_local_part("SMTP:inquiry@winlink.org"), "INQUIRY")
        self.assertEqual(address_local_part("smtp:KC1JMH@winlink.org"), "KC1JMH")

    def test_local_part_rejects_empty(self):
        for address in ("", "   ", "@winlink.org", "SMTP:", "SMTP:@x"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    address_local_part(address)

    def test_service_for(self):
        self.assertEqual(service_for("INQUIRY@winlink.org"), "INQUIRY")
        self.assertEqual(service_for("inquiry"), "INQUIRY")
        self.assertEqual(service_for("QTH@winlink.org"), "QTH")
        self.assertEqual(service_for("qth"), "QTH")
        self.assertIsNone(service_for("KC1JMH@winlink.org"))
        self.assertIsNone(service_for("INQUIRY-1"))
        self.assertIsNone(service_for("INQUIRYX@winlink.org"))
        self.assertIsNone(service_for(""))


class InquiryTests(unittest.TestCase):
    def kinds(self, request):
        return [(item.kind, item.value) for item in request.items]

    def test_catalog_ids_one_per_line_normalized_upper(self):
        request = parse_inquiry("REQUEST", "PUB_PACKET\r\npub_vara\r\nFZAK51.PAFC\r\nUSWXRAD.GIF\r\n")
        self.assertEqual(self.kinds(request), [
            ("catalog", "PUB_PACKET"), ("catalog", "PUB_VARA"),
            ("catalog", "FZAK51.PAFC"), ("catalog", "USWXRAD.GIF"),
        ])
        self.assertEqual(request.rejected, ())

    def test_observed_express_request(self):
        # Winlink Express 1.8.6.0 to production CMS, 2026-11-24 (see doc).
        self.assertEqual(service_for("INQUIRY"), "INQUIRY")
        request = parse_inquiry("REQUEST", "ME_ZON_YORKC\r\n")
        self.assertEqual(self.kinds(request), [("catalog", "ME_ZON_YORKC")])

    def test_subject_forms(self):
        for subject in ("REQUEST", "request", "  Request  ", "//WL2K REQUEST", "//wl2k request"):
            with self.subTest(subject=subject):
                self.assertEqual(len(parse_inquiry(subject, "PROP_WWV").items), 1)
        for subject in ("", "REQUESTS", "RE: REQUEST", "Weather please"):
            with self.subTest(subject=subject):
                with self.assertRaises(InquiryError):
                    parse_inquiry(subject, "PROP_WWV")

    def test_list_and_update_cat(self):
        request = parse_inquiry("REQUEST", "list\nUPDATE_CAT\n")
        self.assertEqual(self.kinds(request), [("list", "LIST"), ("update_catalog", "UPDATE_CAT")])

    def test_url_lines_keep_original_case(self):
        request = parse_inquiry("REQUEST", "https://Example.org/Path?Q=1\nHTTP://example.com/\n")
        self.assertEqual(self.kinds(request), [
            ("url", "https://Example.org/Path?Q=1"), ("url", "HTTP://example.com/"),
        ])

    def test_url_rejections(self):
        long_url = "https://example.org/" + "a" * 2100
        request = parse_inquiry("REQUEST", "https://example.org/a b\n" + long_url + "\nftp://example.org/\n")
        self.assertEqual(request.items, ())
        self.assertEqual([r.line_number for r in request.rejected], [1, 2, 3])

    def test_line_endings_blank_lines_and_whitespace(self):
        request = parse_inquiry("REQUEST", "\r\n  PROP_WWV  \r\rWL2K_NEARBY\n\n\tAUR_TONIGHT\t")
        self.assertEqual(self.kinds(request), [
            ("catalog", "PROP_WWV"), ("catalog", "WL2K_NEARBY"), ("catalog", "AUR_TONIGHT"),
        ])

    def test_only_cr_and_lf_end_lines(self):
        # NEL and Unicode line separators are not line endings in B2 text.
        request = parse_inquiry("REQUEST", "PROP_WWV\x85INQUIRIES\u2028AUR_TONIGHT\nLIST")
        self.assertEqual(self.kinds(request), [("list", "LIST")])
        self.assertEqual([r.line_number for r in request.rejected], [1])

    def test_semicolon_starts_comment(self):
        request = parse_inquiry("REQUEST", "PROP_WWV ; solar flux\n; whole line comment\nINQUIRIES;x")
        self.assertEqual(self.kinds(request), [("catalog", "PROP_WWV"), ("catalog", "INQUIRIES")])
        self.assertEqual(request.rejected, ())

    def test_invalid_lines_are_reported_not_raised(self):
        request = parse_inquiry("REQUEST", "PROP_WWV\nsend me the weather\n_BAD\nGOOD-ID\n" + "A" * 65 + "\n")
        self.assertEqual(self.kinds(request), [("catalog", "PROP_WWV"), ("catalog", "GOOD-ID")])
        self.assertEqual([r.line_number for r in request.rejected], [2, 3, 5])
        for rejected in request.rejected:
            self.assertEqual(rejected.reason, "invalid item")
            self.assertLessEqual(len(rejected.text), 80)

    def test_rejected_line_number_counts_physical_lines(self):
        request = parse_inquiry("REQUEST", "\n\nbad item\n")
        self.assertEqual(request.rejected[0].line_number, 3)
        self.assertEqual(request.rejected[0].text, "bad item")

    def test_duplicates_kept_once(self):
        request = parse_inquiry("REQUEST", "PROP_WWV\nprop_wwv\nLIST\nlist\n")
        self.assertEqual(self.kinds(request), [("catalog", "PROP_WWV"), ("list", "LIST")])
        self.assertEqual(request.rejected, ())

    def test_item_cap(self):
        body = "\n".join(f"ITEM_{n}" for n in range(40))
        request = parse_inquiry("REQUEST", body)
        self.assertEqual(len(request.items), 32)
        self.assertEqual(request.items[-1].value, "ITEM_31")
        self.assertEqual(len(request.rejected), 8)
        self.assertTrue(all(r.reason == "too many items" for r in request.rejected))
        self.assertEqual(request.rejected[0].line_number, 33)

    def test_body_size_cap(self):
        with self.assertRaises(InquiryError):
            parse_inquiry("REQUEST", "A" * 8193)
        parse_inquiry("REQUEST", "PROP_WWV\n" + " " * (8192 - 9))

    def test_empty_request_raises(self):
        for body in ("", "\r\n\r\n", "; only a comment\n"):
            with self.subTest(body=body):
                with self.assertRaises(InquiryError):
                    parse_inquiry("REQUEST", body)

    def test_only_invalid_lines_does_not_raise(self):
        request = parse_inquiry("REQUEST", "not valid\n")
        self.assertEqual(request.items, ())
        self.assertEqual(len(request.rejected), 1)

    def test_results_are_immutable(self):
        request = parse_inquiry("REQUEST", "PROP_WWV")
        self.assertIsInstance(request.items, tuple)
        self.assertIsInstance(request.rejected, tuple)
        with self.assertRaises(Exception):
            request.items[0].value = "X"


class PositionReportTests(unittest.TestCase):
    # Body layout from wl2k-go catalog/position_report.go (commit 1d59b0a).
    PAT_BODY = ("DATE: 2026/11/24 21:05\r\n"
                "LATITUDE: 43-35.4120N\r\n"
                "LONGITUDE: 070-44.2380W\r\n"
                "SPEED: 4.500000\r\n"
                "COURSE: 045T\r\n"
                "COMMENT: Waterboro test\r\n")

    def test_pat_generated_report(self):
        report = parse_position_report(self.PAT_BODY)
        self.assertEqual(report.date, datetime(2026, 11, 24, 21, 5, tzinfo=timezone.utc))
        self.assertAlmostEqual(report.latitude, 43 + 35.4120 / 60, places=6)
        self.assertAlmostEqual(report.longitude, -(70 + 44.2380 / 60), places=6)
        self.assertAlmostEqual(report.speed, 4.5)
        self.assertEqual(report.course, (45, False))
        self.assertEqual(report.comment, "Waterboro test")

    def test_minimal_report_date_only(self):
        report = parse_position_report("DATE: 2026/01/02 03:04\n")
        self.assertIsNone(report.latitude)
        self.assertIsNone(report.longitude)
        self.assertIsNone(report.speed)
        self.assertIsNone(report.course)
        self.assertEqual(report.comment, "")

    def test_southern_eastern_and_magnetic(self):
        report = parse_position_report(
            "date: 2026/01/02 03:04\nlatitude: 33-52.0000S\nlongitude: 151-12.5000E\ncourse:  90M\n")
        self.assertAlmostEqual(report.latitude, -(33 + 52 / 60), places=6)
        self.assertAlmostEqual(report.longitude, 151 + 12.5 / 60, places=6)
        self.assertEqual(report.course, (90, True))

    def test_zero_coordinates_without_hemisphere(self):
        # wl2k-go writes a space instead of N/S/E/W for exactly zero.
        report = parse_position_report("DATE: 2026/01/02 03:04\nLATITUDE: 00-00.0000 \nLONGITUDE: 000-00.0000 \n")
        self.assertEqual(report.latitude, 0.0)
        self.assertEqual(report.longitude, 0.0)

    def test_unknown_keys_ignored(self):
        report = parse_position_report("DATE: 2026/01/02 03:04\nGRID: FN43\n")
        self.assertIsNone(report.latitude)

    def test_rejections(self):
        bad = [
            "",                                                     # no DATE
            "LATITUDE: 43-35.4120N\n",                              # no DATE
            "DATE: 2026-01-02 03:04\n",                             # wrong date layout
            "DATE: 2026/01/02 03:04\nLATITUDE: 43-35.4120N\n",      # latitude without longitude
            "DATE: 2026/01/02 03:04\nLONGITUDE: 070-44.2380W\n",    # longitude without latitude
            "DATE: 2026/01/02 03:04\nLATITUDE: 91-00.0000N\nLONGITUDE: 070-44.2380W\n",
            "DATE: 2026/01/02 03:04\nLATITUDE: 43-60.5000N\nLONGITUDE: 070-44.2380W\n",
            "DATE: 2026/01/02 03:04\nLATITUDE: 43-35.4120E\nLONGITUDE: 070-44.2380W\n",
            "DATE: 2026/01/02 03:04\nLATITUDE: 43-35.4120N\nLONGITUDE: 181-00.0000W\n",
            "DATE: 2026/01/02 03:04\nLATITUDE: 43-35.4120 \nLONGITUDE: 070-44.2380W\n",  # nonzero, no hemisphere
            "DATE: 2026/01/02 03:04\nCOURSE: 361T\n",
            "DATE: 2026/01/02 03:04\nCOURSE: 045X\n",
            "DATE: 2026/01/02 03:04\nSPEED: fast\n",
            "DATE: 2026/01/02 03:04\nSPEED: -1\n",
            "DATE: 2026/01/02 03:04\nDATE: 2026/01/02 03:05\n",     # duplicate key
            "DATE: 2026/01/02 03:04\nCOMMENT: " + "x" * 81 + "\n",
            "DATE: 2026/01/02 03:04\n" + "".join(f"K{n}: x\n" for n in range(2000)),  # over 8192 bytes, distinct unknown keys
        ]
        for body in bad:
            with self.subTest(body=body[:60]):
                with self.assertRaises(PositionReportError):
                    parse_position_report(body)

    def test_comment_of_80_characters_allowed(self):
        report = parse_position_report("DATE: 2026/01/02 03:04\nCOMMENT: " + "x" * 80 + "\n")
        self.assertEqual(len(report.comment), 80)


DATE_LINE = "DATE: 2026/01/02 03:04\n"


class AddressHardeningTests(unittest.TestCase):
    def test_non_ascii_local_part_matches_nothing(self):
        # str.upper() maps dotless i, long s and sharp s into ASCII; matching must not.
        for address in ("\u0131nquiry@x", "\u0131NQUIRY", "\u017fMTP:inquiry@x",
                        "\uff29\uff2e\uff31\uff35\uff29\uff32\uff39", "INQUIRY\u200b@x",
                        "INQUIRY\u0131@x"):
            with self.subTest(address=address):
                self.assertIsNone(service_for(address))

    def test_non_matching_forms_stay_unmatched(self):
        for address in ("INQUIRY.X@x", '"INQUIRY"@x', "Name <INQUIRY@x>",
                        "INQUIRY <bob@x>", "<INQUIRY@x>", "INQUIRY\x00@x", "SMTP:SMTP:INQUIRY"):
            with self.subTest(address=address):
                self.assertIsNone(service_for(address))

    def test_smtp_prefix_and_whitespace_still_match(self):
        self.assertEqual(service_for("SMTP:INQUIRY@winlink.org"), "INQUIRY")
        self.assertEqual(service_for(" smtp: qth "), "QTH")


class InquiryHardeningTests(unittest.TestCase):
    def kinds(self, request):
        return [(item.kind, item.value) for item in request.items]

    def test_lone_surrogate_raises_inquiry_error(self):
        for subject, body in (("REQUEST", "\ud800"), ("REQUEST", "PROP_WWV\n\udfff"),
                              ("\ud800", "PROP_WWV"), ("REQUEST", "A" * 9000 + "\ud800")):
            with self.subTest(subject=subject, body=body[-10:]):
                with self.assertRaises(InquiryError):
                    parse_inquiry(subject, body)

    def test_non_ascii_subject_raises(self):
        for subject in ("REQUE\u017fT", "\uff32\uff25\uff31\uff35\uff25\uff33\uff34", "REQUEST\u200b"):
            with self.subTest(subject=subject):
                with self.assertRaises(InquiryError):
                    parse_inquiry(subject, "PROP_WWV")

    def test_multibyte_body_size_is_counted_in_bytes(self):
        # 6 ASCII bytes + 2 bytes per character.
        parse_inquiry("REQUEST", "LIST\n;" + "\u00e9" * 4093)  # exactly 8192 bytes
        with self.assertRaises(InquiryError):
            parse_inquiry("REQUEST", "LIST\n;" + "\u00e9" * 4094)  # 8194 bytes, 4100 characters

    def test_leading_bom_removed(self):
        self.assertEqual(self.kinds(parse_inquiry("REQUEST", "\ufeffPROP_WWV\r\nLIST")),
                         [("catalog", "PROP_WWV"), ("list", "LIST")])
        with self.assertRaises(InquiryError):
            parse_inquiry("REQUEST", "\ufeff")
        # Only a leading mark is removed.
        request = parse_inquiry("REQUEST", "PROP_WWV\n\ufeffLIST")
        self.assertEqual(self.kinds(request), [("catalog", "PROP_WWV")])
        self.assertEqual([r.line_number for r in request.rejected], [2])

    def test_non_ascii_item_lines_rejected(self):
        lines = ["l\u0131st", "pub_p\u0131cket", "PUB\u00dfX", "http\u017f://x.org/y",
                 "\uff30\uff35\uff22", "PROP_WWV\u200b", "https://example.org/caf\u00e9"]
        request = parse_inquiry("REQUEST", "\n".join(lines))
        self.assertEqual(request.items, ())
        self.assertEqual([r.line_number for r in request.rejected], list(range(1, len(lines) + 1)))
        self.assertTrue(all(r.reason == "invalid item" for r in request.rejected))

    def test_url_semicolon_without_preceding_whitespace_is_part_of_url(self):
        request = parse_inquiry("REQUEST", "https://x.org/a;b=1\nhttp://x.org/p;q;r")
        self.assertEqual(self.kinds(request), [("url", "https://x.org/a;b=1"), ("url", "http://x.org/p;q;r")])

    def test_url_semicolon_after_whitespace_starts_comment(self):
        for line in ("https://x.org/a ;note", "https://x.org/a\t; note", "https://x.org/a  ;"):
            with self.subTest(line=line):
                self.assertEqual(self.kinds(parse_inquiry("REQUEST", line)), [("url", "https://x.org/a")])
        request = parse_inquiry("REQUEST", "https://x.org/a;b=1 ; note")
        self.assertEqual(self.kinds(request), [("url", "https://x.org/a;b=1")])

    def test_semicolon_outside_urls_still_comment(self):
        request = parse_inquiry("REQUEST", "PROP_WWV;x\nLIST;")
        self.assertEqual(self.kinds(request), [("catalog", "PROP_WWV"), ("list", "LIST")])
        self.assertEqual(request.rejected, ())

    def test_control_characters_reject_line_and_are_masked(self):
        cases = {
            "PROP\x00WWV": "PROP?WWV",
            "\x00PROP_WWV": "?PROP_WWV",
            "https://a/\x00b": "https://a/?b",
            "PROP_WWV\x1b[31m": "PROP_WWV?[31m",
            "PROP\x7fWWV": "PROP?WWV",
            "\x1fPROP_WWV": "?PROP_WWV",
        }
        for line, shown in cases.items():
            with self.subTest(line=line):
                request = parse_inquiry("REQUEST", line)
                self.assertEqual(request.items, ())
                self.assertEqual(len(request.rejected), 1)
                self.assertEqual(request.rejected[0].reason, "invalid item")
                self.assertEqual(request.rejected[0].text, shown)

    def test_rejected_text_masks_controls_and_stays_within_80(self):
        request = parse_inquiry("REQUEST", "\x01" * 200)
        self.assertEqual(request.rejected[0].text, "?" * 80)

    def test_tab_is_whitespace_not_a_control_error(self):
        self.assertEqual(self.kinds(parse_inquiry("REQUEST", "\tPROP_WWV\t")), [("catalog", "PROP_WWV")])

    def test_repeats_after_item_cap_are_dropped_silently(self):
        body = "\n".join([f"ITEM_{n}" for n in range(32)] + ["ITEM_0", "item_31", "ITEM_32"])
        request = parse_inquiry("REQUEST", body)
        self.assertEqual(len(request.items), 32)
        self.assertEqual([(r.line_number, r.reason) for r in request.rejected], [(35, "too many items")])


class PositionReportHardeningTests(unittest.TestCase):
    def coords(self, lat, lon):
        report = parse_position_report(f"{DATE_LINE}LATITUDE: {lat}\nLONGITUDE: {lon}\n")
        return report.latitude, report.longitude

    def assertInvalid(self, body):
        with self.assertRaises(PositionReportError):
            parse_position_report(body)

    def test_lone_surrogate_raises_position_report_error(self):
        for body in (DATE_LINE + "COMMENT: \ud800", "\udfff", DATE_LINE + "X: " + "a" * 9000 + "\ud800"):
            with self.subTest(body=body[-10:]):
                self.assertInvalid(body)

    def test_multibyte_body_size_is_counted_in_bytes(self):
        # DATE line is 23 bytes, "X: " 3, then 2 bytes per character.
        parse_position_report(DATE_LINE + "X: " + "\u00e9" * 4083)  # exactly 8192 bytes
        self.assertInvalid(DATE_LINE + "X: " + "\u00e9" * 4084)  # 8194 bytes, 4110 characters

    def test_leading_bom_removed(self):
        report = parse_position_report("\ufeff" + DATE_LINE)
        self.assertEqual(report.date, datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc))

    def test_non_ascii_letters_do_not_fold_to_ascii(self):
        # Long s must not read as S, dotless i must not read as I in keys.
        self.assertInvalid(DATE_LINE + "LATITUDE: 43-35.4120\u017f\nLONGITUDE: 070-44.2380W\n")
        self.assertInvalid(DATE_LINE + "LATIT\u0131UDE: 43-35.4120N\nLONGITUDE: 070-44.2380W\n")
        self.assertInvalid(DATE_LINE + "LATITUDE: \uff14\uff13-35.4120N\nLONGITUDE: 070-44.2380W\n")

    def test_non_ascii_comment_is_kept(self):
        self.assertEqual(parse_position_report(DATE_LINE + "COMMENT: Qu\u00e9bec\n").comment, "Qu\u00e9bec")

    def test_unknown_keys_ignored_even_when_repeated(self):
        report = parse_position_report(DATE_LINE + "Re: a\nRe: b\nGRID: x\ngrid: y\nhttp://a\nhttp://b\n")
        self.assertIsNone(report.latitude)

    def test_repeated_known_keys_rejected(self):
        for key, value in (("DATE", "2026/01/02 03:05"), ("date", "2026/01/02 03:05"),
                           ("COMMENT", "again"), ("SPEED", "1"), ("COURSE", "1T"),
                           ("LATITUDE", "43-35.4120N"), ("LONGITUDE", "070-44.2380W")):
            with self.subTest(key=key):
                body = (DATE_LINE + "LATITUDE: 43-35.4120N\nLONGITUDE: 070-44.2380W\nSPEED: 1\n"
                        "COURSE: 1T\nCOMMENT: x\n" + f"{key}: {value}\n")
                self.assertInvalid(body)

    def test_speed_must_be_finite_and_at_most_10000(self):
        for value in ("0", "4.5", "10000", "10000.0"):
            with self.subTest(value=value):
                self.assertEqual(parse_position_report(DATE_LINE + f"SPEED: {value}\n").speed, float(value))
        for value in ("10000.0001", "10001", "9" * 400, "1" + "0" * 300, "inf", "nan", "\u0661"):
            with self.subTest(value=value[:20]):
                self.assertInvalid(DATE_LINE + f"SPEED: {value}\n")

    def test_minutes_accept_one_to_four_decimals(self):
        for lat, lon, expected in (("43-35.4N", "070-44.2W", (43 + 35.4 / 60, -(70 + 44.2 / 60))),
                                   ("43-35.41N", "070-44.23W", (43 + 35.41 / 60, -(70 + 44.23 / 60))),
                                   ("43-35.412N", "070-44.238W", (43 + 35.412 / 60, -(70 + 44.238 / 60)))):
            with self.subTest(lat=lat):
                got = self.coords(lat, lon)
                self.assertAlmostEqual(got[0], expected[0], places=6)
                self.assertAlmostEqual(got[1], expected[1], places=6)
        for lat in ("43-35N", "43-35.N", "43-35.41234N"):
            with self.subTest(lat=lat):
                self.assertInvalid(f"{DATE_LINE}LATITUDE: {lat}\nLONGITUDE: 070-44.2380W\n")

    def test_range_boundaries(self):
        self.assertEqual(self.coords("90-00.0000N", "180-00.0000E"), (90.0, 180.0))
        self.assertEqual(self.coords("90-00.0000S", "180-00.0000W"), (-90.0, -180.0))
        for lat, lon in (("90-00.0001N", "000-00.0000E"), ("90-00.0001S", "000-00.0000E"),
                         ("00-00.0000N", "180-00.0001E"), ("00-00.0000N", "180-00.0001W"),
                         ("91-00.0000N", "000-00.0000E"), ("00-00.0000N", "181-00.0000E")):
            with self.subTest(lat=lat, lon=lon):
                self.assertInvalid(f"{DATE_LINE}LATITUDE: {lat}\nLONGITUDE: {lon}\n")

    def test_minutes_below_60_accepted_60_invalid_except_zero_carry(self):
        lat, lon = self.coords("43-59.9999N", "070-59.9999W")
        self.assertAlmostEqual(lat, 43 + 59.9999 / 60, places=9)
        self.assertAlmostEqual(lon, -(70 + 59.9999 / 60), places=9)
        for bad in ("43-60.0001N", "43-61.0000N", "43-60.0010N", "43-99.9999N"):
            with self.subTest(lat=bad):
                self.assertInvalid(f"{DATE_LINE}LATITUDE: {bad}\nLONGITUDE: 070-44.2380W\n")

    def test_sixty_minutes_with_zero_decimals_carries_to_next_degree(self):
        # wl2k-go formats minutes with %07.4f, so 59.99996 is written as 60.0000.
        self.assertEqual(self.coords("43-60.0000N", "070-60.0000W"), (44.0, -71.0))
        self.assertEqual(self.coords("43-60.0S", "070-60.00E"), (-44.0, 71.0))
        self.assertEqual(self.coords("89-60.0000N", "179-60.0000E"), (90.0, 180.0))
        for lat, lon in (("90-60.0000N", "000-00.0000E"), ("00-00.0000N", "180-60.0000E"),
                         ("00-60.0000 ", "000-00.0000 ")):  # carry makes it nonzero, so a hemisphere is needed
            with self.subTest(lat=lat, lon=lon):
                self.assertInvalid(f"{DATE_LINE}LATITUDE: {lat}\nLONGITUDE: {lon}\n")

    def test_zero_is_never_negative_zero(self):
        for lat, lon in (("00-00.0000S", "000-00.0000W"), ("00-00.0000N", "000-00.0000E"),
                         ("00-00.0000 ", "000-00.0000 "), ("00-00.0S", "000-00.0W")):
            with self.subTest(lat=lat):
                for value in self.coords(lat, lon):
                    self.assertEqual(value, 0.0)
                    self.assertEqual(math.copysign(1.0, value), 1.0)


class ServiceCommandTests(unittest.TestCase):
    """Service commands in INQUIRY bodies; see docs/INQUIRY-ADAPTER.md."""

    SERVICES = frozenset({"WIKI", "NEWS", "WX", "SOLAR"})

    def parse(self, body, services=None):
        return parse_inquiry("REQUEST", body, services=self.SERVICES if services is None else services)

    def one(self, line):
        request = self.parse(line)
        self.assertEqual(request.rejected, (), line)
        self.assertEqual(len(request.items), 1, line)
        return request.items[0]

    def reasons(self, body):
        return [(r.line_number, r.reason) for r in self.parse(body).rejected]

    def test_default_services_empty_changes_nothing(self):
        for kwargs in ({}, {"services": frozenset()}):
            with self.subTest(kwargs=kwargs):
                request = parse_inquiry("REQUEST", "WIKI Mercury\nWIKI\n", **kwargs)
                self.assertEqual(request.items, (InquiryItem("catalog", "WIKI"),))
                self.assertEqual([r.line_number for r in request.rejected], [1])
                self.assertEqual(request.rejected[0].reason, "invalid item")

    def test_services_is_keyword_only(self):
        with self.assertRaises(TypeError):
            parse_inquiry("REQUEST", "WIKI", frozenset({"WIKI"}))

    def test_forms(self):
        cases = [
            ("WIKI", "", None),
            ("wiki mercury", "mercury", None),
            ("WiKi Mercury (planet)", "Mercury (planet)", None),
            ("WIKI Mercury | 3", "Mercury", "3"),
            ("NEWS BBC|3", "BBC", "3"),
            ("NEWS BBC |  3   FULL ", "BBC", "3 FULL"),
            ("WIKI  Mercury \t (planet)", "Mercury (planet)", None),
            ("  WX\tWaterboro   ME  ", "Waterboro ME", None),
            ("WIKI | 2", "", "2"),
            ("WIKI|2", "", "2"),
            ("WIKI Mercury ; a comment", "Mercury", None),
            ("WIKI Mercury | 3 ; a comment", "Mercury", "3"),
            ("WIKI ; only a comment", "", None),
            ("WIKI a;b", "a", None),
            ("WIKI Mercury\t|\tFULL", "Mercury", "FULL"),
            ("WIKI " + "".join(chr(c) for c in range(0x21, 0x7F) if chr(c) not in ";|"),
             "".join(chr(c) for c in range(0x21, 0x7F) if chr(c) not in ";|"), None),
        ]
        for line, arguments, choice in cases:
            with self.subTest(line=line):
                item = self.one(line)
                self.assertEqual(item.kind, "service")
                self.assertEqual(item.value, line.strip().replace("|", " ").split()[0].upper())
                self.assertEqual(item.arguments, arguments)
                self.assertEqual(item.choice, choice)

    def test_item_shape_and_equality(self):
        item = self.one("wiki Mercury | 3")
        self.assertEqual(item, InquiryItem("service", "WIKI", "Mercury", "3"))
        self.assertEqual(item, InquiryItem(kind="service", value="WIKI", arguments="Mercury", choice="3"))
        self.assertEqual(hash(item), hash(InquiryItem("service", "WIKI", "Mercury", "3")))
        with self.assertRaises(Exception):
            item.arguments = "x"

    def test_existing_kinds_keep_default_new_fields(self):
        request = self.parse("PROP_WWV\nLIST\nUPDATE_CAT\nhttps://example.org/a|b\n")
        self.assertEqual(request.items, (
            InquiryItem("catalog", "PROP_WWV"), InquiryItem("list", "LIST"),
            InquiryItem("update_catalog", "UPDATE_CAT"), InquiryItem("url", "https://example.org/a|b"),
        ))
        for item in request.items:
            self.assertEqual(item.arguments, "")
            self.assertIsNone(item.choice)

    def test_order_is_preserved_across_kinds(self):
        request = self.parse("PROP_WWV\nWIKI a\nLIST\nNEWS BBC | 2\n")
        self.assertEqual([i.kind for i in request.items], ["catalog", "service", "list", "service"])

    def test_service_verb_wins_over_catalog_id(self):
        self.assertEqual(self.one("SOLAR").kind, "service")
        self.assertEqual(self.one("solar").value, "SOLAR")

    def test_verb_is_the_whole_first_word(self):
        # Not service verbs: still judged as before.
        request = self.parse("WIKIS\nWIKI.X\nWIKI-X\nWIKI_X\nWIKIPEDIA Mercury\n")
        self.assertEqual([(i.kind, i.value) for i in request.items], [
            ("catalog", "WIKIS"), ("catalog", "WIKI.X"), ("catalog", "WIKI-X"), ("catalog", "WIKI_X")])
        self.assertEqual([(r.line_number, r.reason) for r in request.rejected], [(5, "invalid item")])

    def test_non_ascii_lookalike_verbs_are_not_services(self):
        for line in ("w\u0131ki Mercury", "\uff37\uff29\uff2b\uff29 x", "WIKI\u200b x", "\u017folar",
                     "WIKI\u00a0Mercury", "SOLAR\u2003x", "wiki\x0bx"):
            with self.subTest(line=line):
                request = self.parse(line)
                self.assertEqual(request.items, ())
                self.assertEqual([r.reason for r in request.rejected], ["invalid item"])

    def test_non_service_lines_with_pipe_keep_old_behaviour(self):
        request = self.parse("LIST WIKI\nPROP_WWV | 2\nUNKNOWN a\n")
        self.assertEqual(request.items, ())
        self.assertEqual([r.reason for r in request.rejected], ["invalid item"] * 3)

    def test_argument_length_limit(self):
        self.assertEqual(len(self.one("WIKI " + "a" * 200).arguments), 200)
        self.assertEqual(self.reasons("WIKI " + "a" * 201), [(1, "arguments too long")])
        # Length is measured after collapsing.
        spaced = ("a" + " " * 5) * 50
        self.assertGreater(len(spaced), 200)
        self.assertEqual(self.one("WIKI " + spaced).arguments, " ".join("a" * 50))
        self.assertEqual(self.reasons("WIKI " + "a" * 8000), [(1, "arguments too long")])

    def test_choice_length_limit(self):
        self.assertEqual(len(self.one("WIKI x | " + "c" * 80).choice), 80)
        self.assertEqual(self.reasons("WIKI x | " + "c" * 81), [(1, "choice too long")])
        spaced = ("c" + " " * 4) * 30
        self.assertGreater(len(spaced), 80)
        self.assertEqual(self.one("WIKI x | " + spaced).choice, " ".join("c" * 30))

    def test_choice_rules(self):
        self.assertEqual(self.reasons("WIKI Mercury |"), [(1, "empty choice")])
        self.assertEqual(self.reasons("WIKI Mercury |   \t"), [(1, "empty choice")])
        self.assertEqual(self.reasons("WIKI Mercury | ; comment"), [(1, "empty choice")])
        self.assertEqual(self.reasons("WIKI |"), [(1, "empty choice")])
        for line in ("WIKI a | b | c", "WIKI a ||", "WIKI a | | b", "WIKI a | b |", "NEWS | | "):
            with self.subTest(line=line):
                self.assertEqual(self.reasons(line), [(1, "multiple choices")])
        for rejected_item in self.parse("WIKI a | b | c").rejected:
            self.assertEqual(rejected_item.text, "WIKI a | b | c")

    def test_invalid_characters(self):
        for line in ("WIKI caf\u00e9", "WIKI a | caf\u00e9", "WIKI a\x00b", "WIKI a\x7fb", "WIKI a\x1bb",
                     "WIKI a\x0bb", "WIKI \u00a0x", "WIKI a\u2003b", "WIKI a | \u200b1", "WIKI \U0001f600",
                     "WIKI a\x0cb"):
            with self.subTest(line=line):
                self.assertEqual(self.reasons(line), [(1, "invalid characters")])

    def test_non_ascii_in_comment_is_ignored(self):
        self.assertEqual(self.one("WIKI Mercury ; caf\u00e9 \x00").arguments, "Mercury")

    def test_reason_precedence(self):
        self.assertEqual(self.reasons("WIKI " + "a" * 201 + " | b | c"), [(1, "arguments too long")])
        self.assertEqual(self.reasons("WIKI a\u00e9" + "a" * 300), [(1, "invalid characters")])
        self.assertEqual(self.reasons("WIKI a | b | " + "c" * 100), [(1, "multiple choices")])
        self.assertEqual(self.reasons("WIKI " + "a" * 201 + " |"), [(1, "arguments too long")])

    def test_rejected_text_is_cut_and_has_no_control_characters(self):
        request = self.parse("\n\nWIKI a\x00b\x1bc " + "x" * 300 + "\nWIKI \u00e9 " + "y" * 300)
        self.assertEqual([r.line_number for r in request.rejected], [3, 4])
        for rejected_line in request.rejected:
            self.assertLessEqual(len(rejected_line.text), 80)
            self.assertFalse(any(ord(c) < 32 or ord(c) == 127 for c in rejected_line.text))
        self.assertTrue(request.rejected[0].text.startswith("WIKI a?b?c "))

    def test_rejected_service_line_does_not_stop_parsing_or_count_toward_limit(self):
        request = self.parse("WIKI ok\nWIKI bad \u00e9\nWIKI ok2 | 1\n" + "\n".join(f"C{n}" for n in range(30)))
        self.assertEqual(len(request.items), 32)
        self.assertEqual([r.reason for r in request.rejected], ["invalid characters"])

    def test_duplicates_kept_once(self):
        request = self.parse("WIKI Mercury\nwiki  Mercury ; again\nWIKI Mercury | 3\nWIKI Mercury |3\nWIKI mercury\nWIKI\nwiki \n")
        self.assertEqual([(i.arguments, i.choice) for i in request.items],
                         [("Mercury", None), ("Mercury", "3"), ("mercury", None), ("", None)])
        self.assertEqual(request.rejected, ())

    def test_service_commands_count_toward_the_item_limit(self):
        body = "\n".join(f"C{n}" for n in range(31)) + "\nWIKI a\nWIKI a\nWIKI b\nC0\nSOLAR\n"
        request = self.parse(body)
        self.assertEqual(len(request.items), 32)
        self.assertEqual(request.items[-1], InquiryItem("service", "WIKI", "a"))
        self.assertEqual([(r.line_number, r.reason, r.text) for r in request.rejected],
                         [(34, "too many items", "WIKI b"), (36, "too many items", "SOLAR")])

    def test_only_service_commands_up_to_the_limit(self):
        body = "\n".join(f"WIKI topic {n}" for n in range(40))
        request = self.parse(body)
        self.assertEqual(len(request.items), 32)
        self.assertEqual(request.items[-1].arguments, "topic 31")
        self.assertEqual([r.line_number for r in request.rejected], list(range(33, 41)))
        self.assertTrue(all(r.reason == "too many items" for r in request.rejected))

    def test_request_errors_still_apply(self):
        with self.assertRaises(InquiryError):
            parse_inquiry("Weather", "WIKI a", services=self.SERVICES)
        with self.assertRaises(InquiryError):
            parse_inquiry("REQUEST", "; WIKI a\n", services=self.SERVICES)
        with self.assertRaises(InquiryError):
            parse_inquiry("REQUEST", "WIKI " + "a" * 8200, services=self.SERVICES)
        with self.assertRaises(InquiryError):
            parse_inquiry("REQUEST", "WIKI a\ud800", services=self.SERVICES)
        self.assertEqual(len(parse_inquiry("//WL2K REQUEST", "WIKI a", services=self.SERVICES).items), 1)

    def test_bom_and_line_endings(self):
        request = self.parse("\ufeffWIKI a\r\nNEWS b | 2\rWX c\n")
        self.assertEqual([i.value for i in request.items], ["WIKI", "NEWS", "WX"])

    def test_invalid_services_raise_value_error_not_inquiry_error(self):
        bad = [{"LIST"}, {"UPDATE_CAT"}, {"WIKI", "LIST"}, {"wiki"}, {"Wiki"}, {""}, {"A B"}, {"WIKI|X"},
               {"WIKI\t"}, {"1ABC"}, {"-AB"}, {"A" * 33}, {"\u0131NQ"}, {"WIKI;"}, {"W.K"}, {"WIKI\n"}]
        for services in bad:
            for subject, body in (("REQUEST", "PROP_WWV"), ("not a request", ""), ("REQUEST", "")):
                with self.subTest(services=services, subject=subject):
                    with self.assertRaises(ValueError) as caught:
                        parse_inquiry(subject, body, services=frozenset(services))
                    self.assertNotIsInstance(caught.exception, InquiryError)

    def test_valid_service_verbs(self):
        services = frozenset({"A", "A" * 32, "X1", "SOLAR_X", "MY-SVC", "NEWS"})
        request = self.parse("A\nx1 y\nmy-svc | 1\n" + "a" * 32 + "\n", services=services)
        self.assertEqual([i.value for i in request.items], ["A", "X1", "MY-SVC", "A" * 32])
        self.assertEqual(self.parse("WIKI", services={"WIKI"}).items[0].kind, "service")  # a plain set works too

    def test_services_of_wrong_type_raise_type_error(self):
        for services in ("WIKI", ["WIKI"], ("WIKI",), None, {"WIKI": 1}.keys()):
            with self.subTest(services=services):
                with self.assertRaises(TypeError):
                    parse_inquiry("REQUEST", "WIKI", services=services)


if __name__ == "__main__":
    unittest.main()
