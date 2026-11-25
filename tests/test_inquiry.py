"""Contract tests for Winlink-style service messages (INQUIRY catalog requests
and QTH position reports). See docs/INQUIRY-ADAPTER.md for sources.

These tests define the behaviour of federated_cms.inquiry. Do not change them
to make an implementation pass.
"""
from datetime import datetime, timezone
import unittest

from federated_cms.inquiry import (
    InquiryError,
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
            "DATE: 2026/01/02 03:04\nLATITUDE: 43-60.0000N\nLONGITUDE: 070-44.2380W\n",
            "DATE: 2026/01/02 03:04\nLATITUDE: 43-35.4120E\nLONGITUDE: 070-44.2380W\n",
            "DATE: 2026/01/02 03:04\nLATITUDE: 43-35.4120N\nLONGITUDE: 181-00.0000W\n",
            "DATE: 2026/01/02 03:04\nLATITUDE: 43-35.4120 \nLONGITUDE: 070-44.2380W\n",  # nonzero, no hemisphere
            "DATE: 2026/01/02 03:04\nCOURSE: 361T\n",
            "DATE: 2026/01/02 03:04\nCOURSE: 045X\n",
            "DATE: 2026/01/02 03:04\nSPEED: fast\n",
            "DATE: 2026/01/02 03:04\nSPEED: -1\n",
            "DATE: 2026/01/02 03:04\nDATE: 2026/01/02 03:05\n",     # duplicate key
            "DATE: 2026/01/02 03:04\nCOMMENT: " + "x" * 81 + "\n",
            "DATE: 2026/01/02 03:04\n" + "COMMENT: x\n" * 2000,     # over 8192 bytes
        ]
        for body in bad:
            with self.subTest(body=body[:60]):
                with self.assertRaises(PositionReportError):
                    parse_position_report(body)

    def test_comment_of_80_characters_allowed(self):
        report = parse_position_report("DATE: 2026/01/02 03:04\nCOMMENT: " + "x" * 80 + "\n")
        self.assertEqual(len(report.comment), 80)


if __name__ == "__main__":
    unittest.main()
