"""Contract tests for federated_cms.wx_format; see docs/WX-FORMATTERS.md.

Expected strings for the fixtures are written out by hand from the fixture
values and the rules in the spec, not produced by running an implementation.
"""

import copy
import json
import math
import re
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from federated_cms.plaintext import to_ascii
from federated_cms.wx_format import (
    LINE_MAX, WxFormatError, format_alert_full, format_alerts, format_nws_error,
    format_solar, format_wx, xray_class,
)

FIXTURES = Path(__file__).parent / "fixtures"
UTC = timezone.utc
NODE = "fcms.example.net"
PLACE = "Portland Head Light"
NOW = datetime(2026, 11, 25, 13, 45, tzinfo=UTC)
CHECKED = datetime(2026, 11, 25, 4, 33, tzinfo=UTC)
FETCHED = datetime(2026, 11, 25, 4, 21, tzinfo=UTC)
SOLAR_NOW = datetime(2026, 11, 25, 4, 33, tzinfo=UTC)
SOURCE = ("Source: NWS via fcms.example.net\n"
          "Text is shortened or reformatted. Not official NWS text.\n")
ALLOWED = re.compile(r"[\n\x20-\x7e]*\Z")

DEFAULT = object()

HOSTILE = [
    "x" * 5000,
    "café ☃ \U0001F600 ‮ RLO ​ zero ﻿ bom ­ soft",
    "\x00\x01\x02 NUL \x1b[31mred\x1b[0m \x7f del",
    "a\tb\r\nc\rd\ne    f g h",
    "45°F and 43.6°N and 10° alone",
    "<script>alert(1)</script> & \"quotes\" 'single' %s %(x)s {0} {x}",
    "-" * 200 + " " + "a-b-" * 100,
    "\n\n\n   \n\t\n",
    "lone \ud800 surrogate \udfff pair \ud83d\ude00 \U0010ffff \u0000",
    "e\u0301" * 3000 + " \u4e2d\u6587 \u0645\u0631\u062d\u0628\u0627 \u05e9\u05dc\u05d5\u05dd",
    "",
]


def load(*parts):
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="ascii"))


def points():
    return load("nws", "points_portland_head_light.json")


def forecast():
    return load("nws", "forecast_portland_head_light.json")


def alerts():
    return load("nws", "alerts_ca.json")


def solar_args():
    return dict(
        flux=load("swpc", "10cm-flux.json"),
        kp_3h=load("swpc", "noaa-planetary-k-index.json"),
        kp_1m=load("swpc", "planetary_k_index_1m.json"),
        scales=load("swpc", "noaa-scales.json"),
        xray=load("swpc", "xrays-6-hour.json"),
        forecast_3day=(FIXTURES / "swpc" / "3-day-forecast.txt").read_text(encoding="ascii"),
    )


def clean(text):
    return " ".join(to_ascii(text).split())


def mini_points(cwa="GYX", zone="https://api.weather.gov/zones/forecast/MEZ024",
                city="Portland", state="ME"):
    props = {}
    if cwa is not None:
        props["cwa"] = cwa
    if zone is not None:
        props["forecastZone"] = zone
    if city is not None or state is not None:
        rp = {}
        if city is not None:
            rp["city"] = city
        if state is not None:
            rp["state"] = state
        props["relativeLocation"] = {"properties": rp}
    return {"properties": props}


def period(name="Today", text="Sunny.", end="2026-11-25T18:00:00-05:00", **extra):
    p = {"name": name, "detailedForecast": text}
    if end is not None:
        p["endTime"] = end
    p.update(extra)
    return p


def mini_forecast(*periods, update="2026-11-25T06:53:28+00:00"):
    props = {"periods": list(periods)}
    if update is not None:
        props["updateTime"] = update
    return {"properties": props}


def feature(event="Test Warning", **props):
    p = {"event": event, "ends": "2026-11-25T09:00:00-08:00"}
    p.update(props)
    return {"properties": p}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def sprinkle(obj):
    """Add an unknown key to every object, to test that extras are ignored."""
    if isinstance(obj, dict):
        out = {k: sprinkle(v) for k, v in obj.items()}
        out["x-unknown"] = {"a": [1, 2, {"b": None}]}
        return out
    if isinstance(obj, list):
        return [sprinkle(v) for v in obj]
    return obj


def check_reply(tc, text):
    tc.assertIsInstance(text, str)
    tc.assertTrue(text.endswith("\n"), "must end with a newline")
    tc.assertFalse(text.endswith("\n\n"), "must end with one newline only")
    tc.assertFalse(text.startswith("\n"), "no leading blank line")
    tc.assertRegex(text, ALLOWED, "ASCII and newline only")
    for line in text.split("\n")[:-1]:
        tc.assertLessEqual(len(line), LINE_MAX, repr(line))
        tc.assertEqual(line, line.rstrip(" "), "trailing space: %r" % line)
    return text


class ReplyCase(unittest.TestCase):
    def assertReply(self, text):
        return check_reply(self, text)


# --------------------------------------------------------------------- API


class ApiTests(unittest.TestCase):
    def test_constants_and_error_class(self):
        self.assertEqual(LINE_MAX, 69)
        self.assertTrue(issubclass(WxFormatError, ValueError))


# ---------------------------------------------------------------------- WX

WX_HEAD = ("WX Portland Head Light NWS GYX  upd 2026-11-25 0653Z\n"
           "Zone MEZ024 near Portland ME\n")

WX_SHORT = WX_HEAD + (
    "TODAY: Mostly sunny, with a high near 43. East wind 0 to 5 mph.\n"
    "TONIGHT: Mostly cloudy, with a low around 26. North wind 0 to 5 mph.\n"
    "THANKSGIVING DAY: Mostly cloudy, with a high near 46. Northeast wind\n"
    "  5 to 15 mph, with gusts as high as 30 mph.\n"
    "1. Full forecast (14 periods)\n"
    "2. Alerts for this place\n") + SOURCE

WX_FULL = WX_HEAD + (
    "TODAY: Mostly sunny, with a high near 43. East wind 0 to 5 mph.\n"
    "TONIGHT: Mostly cloudy, with a low around 26. North wind 0 to 5 mph.\n"
    "THANKSGIVING DAY: Mostly cloudy, with a high near 46. Northeast wind\n"
    "  5 to 15 mph, with gusts as high as 30 mph.\n"
    "THURSDAY NIGHT: Rain. Cloudy, with a low around 42. Northeast wind 15\n"
    "  to 20 mph, with gusts as high as 35 mph. Chance of precipitation is\n"
    "  100%.\n"
    "FRIDAY: Rain. Mostly cloudy, with a high near 49. Northwest wind 5 to\n"
    "  15 mph, with gusts as high as 30 mph. Chance of precipitation is\n"
    "  100%.\n"
    "FRIDAY NIGHT: Rain likely. Mostly cloudy, with a low around 38.\n"
    "  Chance of precipitation is 60%.\n"
    "SATURDAY: Mostly sunny, with a high near 53.\n"
    "SATURDAY NIGHT: A slight chance of rain between 10pm and 11pm. Mostly\n"
    "  cloudy, with a low around 38.\n"
    "SUNDAY: Mostly sunny, with a high near 53.\n"
    "SUNDAY NIGHT: A chance of rain after 2am. Partly cloudy, with a low\n"
    "  around 40. Chance of precipitation is 30%.\n"
    "MONDAY: Rain likely. Mostly cloudy, with a high near 55. Chance of\n"
    "  precipitation is 70%.\n"
    "MONDAY NIGHT: A chance of rain before 7pm. Partly cloudy, with a low\n"
    "  around 35. Chance of precipitation is 40%.\n"
    "TUESDAY: Sunny, with a high near 48.\n"
    "TUESDAY NIGHT: Partly cloudy, with a low around 31.\n") + SOURCE


def wx(**kw):
    args = dict(place=PLACE, node=NODE, now=NOW)
    args.update(kw)
    pts = args.pop("points", None) or points()
    fc = args.pop("forecast", None) or forecast()
    return format_wx(pts, fc, **args)


class WxExactTests(ReplyCase):
    def test_short_reply(self):
        self.assertEqual(wx(), WX_SHORT)

    def test_full_reply(self):
        self.assertEqual(wx(full=True), WX_FULL)

    def test_replies_satisfy_the_properties(self):
        self.assertReply(wx())
        self.assertReply(wx(full=True))

    def test_expired_periods_are_dropped(self):
        # "Today" ends 2026-11-25 18:00 -05:00, which is 23:00Z.
        now = datetime(2026, 11, 25, 23, 30, tzinfo=UTC)
        expected = WX_HEAD + (
            "TONIGHT: Mostly cloudy, with a low around 26. North wind 0 to 5 mph.\n"
            "THANKSGIVING DAY: Mostly cloudy, with a high near 46. Northeast wind\n"
            "  5 to 15 mph, with gusts as high as 30 mph.\n"
            "THURSDAY NIGHT: Rain. Cloudy, with a low around 42. Northeast wind 15\n"
            "  to 20 mph, with gusts as high as 35 mph. Chance of precipitation is\n"
            "  100%.\n"
            "1. Full forecast (13 periods)\n"
            "2. Alerts for this place\n") + SOURCE
        self.assertEqual(wx(now=now), expected)

    def test_period_ending_exactly_now_is_dropped(self):
        end = datetime(2026, 11, 25, 23, 0, tzinfo=UTC)
        self.assertIn("(13 periods)", wx(now=end))
        self.assertNotIn("TODAY:", wx(now=end))
        self.assertIn("TODAY:", wx(now=end - timedelta(seconds=1)))
        self.assertIn("(14 periods)", wx(now=end - timedelta(seconds=1)))

    def test_now_in_another_zone_is_the_same_instant(self):
        local = NOW.astimezone(timezone(timedelta(hours=-5)))
        self.assertEqual(wx(now=local), WX_SHORT)

    def test_full_variant_also_drops_expired_periods(self):
        out = wx(full=True, now=datetime(2026, 11, 25, 23, 30, tzinfo=UTC))
        self.assertNotIn("TODAY:", out)
        self.assertIn("TUESDAY NIGHT:", out)

    def test_every_period_expired(self):
        out = wx(now=datetime(2027, 1, 1, tzinfo=UTC))
        self.assertEqual(out, WX_HEAD
                         + "No current forecast periods. NWS data may be out of date.\n"
                         + SOURCE)
        self.assertEqual(wx(now=datetime(2027, 1, 1, tzinfo=UTC), full=True), out)

    def test_empty_period_list_is_the_same_as_all_expired(self):
        out = format_wx(mini_points(), mini_forecast(), place="Here", node=NODE, now=NOW)
        self.assertEqual(out, "WX Here NWS GYX  upd 2026-11-25 0653Z\n"
                              "Zone MEZ024 near Portland ME\n"
                              "No current forecast periods. NWS data may be out of date.\n" + SOURCE)

    def test_fewer_than_three_periods_and_one_period_wording(self):
        out = format_wx(mini_points(), mini_forecast(period("Today", "Sunny.")),
                        place="Here", node=NODE, now=NOW)
        self.assertEqual(out, "WX Here NWS GYX  upd 2026-11-25 0653Z\n"
                              "Zone MEZ024 near Portland ME\n"
                              "TODAY: Sunny.\n"
                              "1. Full forecast (1 period)\n"
                              "2. Alerts for this place\n" + SOURCE)

    def test_period_order_is_kept_not_sorted(self):
        fc = mini_forecast(period("Tonight", "B.", number=2), period("Today", "A.", number=1))
        out = format_wx(mini_points(), fc, place="Here", node=NODE, now=NOW)
        self.assertLess(out.index("TONIGHT:"), out.index("TODAY:"))

    def test_period_without_end_time_is_kept(self):
        fc = mini_forecast(period("Today", "A.", end=None))
        out = format_wx(mini_points(), fc, place="Here", node=NODE, now=datetime(2100, 1, 1, tzinfo=UTC))
        self.assertIn("TODAY: A.\n", out)

    def test_unknown_fields_are_ignored(self):
        out = format_wx(sprinkle(points()), sprinkle(forecast()), place=PLACE, node=NODE, now=NOW)
        self.assertEqual(out, WX_SHORT)

    def test_inputs_are_not_changed(self):
        pts, fc = points(), forecast()
        before = copy.deepcopy((pts, fc))
        format_wx(pts, fc, place=PLACE, node=NODE, now=NOW, full=True)
        self.assertEqual((pts, fc), before)

    def test_same_inputs_same_output(self):
        self.assertEqual(wx(), wx())


class WxHeaderTests(ReplyCase):
    def run_wx(self, **kw):
        base = dict(place="Here", node=NODE, now=NOW)
        base.update(kw)
        pts = base.pop("points", mini_points())
        fc = base.pop("forecast", mini_forecast(period()))
        return format_wx(pts, fc, **base)

    def test_place_is_cleaned(self):
        out = self.run_wx(place="  Cape\n\tElizabeth   ME ")
        self.assertTrue(out.startswith("WX Cape Elizabeth ME NWS GYX  upd "))

    def test_non_ascii_place_is_transliterated(self):
        out = self.run_wx(place="Pörtland Héad")
        self.assertTrue(out.startswith("WX Portland Head NWS GYX"))
        self.assertReply(out)

    def test_place_that_just_fits_is_unchanged(self):
        place = "A" * 36
        out = self.run_wx(place=place)
        first = out.split("\n")[0]
        self.assertEqual(first, "WX " + place + " NWS GYX  upd 2026-11-25 0653Z")
        self.assertEqual(len(first), 69)

    def test_long_place_is_cut_with_dots(self):
        out = self.run_wx(place="A" * 40)
        first = out.split("\n")[0]
        self.assertEqual(first, "WX " + "A" * 33 + "... NWS GYX  upd 2026-11-25 0653Z")
        self.assertEqual(len(first), 69)

    def test_cut_place_loses_trailing_space(self):
        out = self.run_wx(place="A" * 32 + " " + "B" * 7)
        first = out.split("\n")[0]
        self.assertEqual(first, "WX " + "A" * 32 + "... NWS GYX  upd 2026-11-25 0653Z")
        self.assertEqual(len(first), 68)

    def test_enormous_place(self):
        out = self.run_wx(place="Q" * 1_000_000)
        self.assertEqual(len(out.split("\n")[0]), 69)
        self.assertReply(out)

    def test_office_falls_back_to_grid_id(self):
        pts = {"properties": {"gridId": "CAR"}}
        out = self.run_wx(points=pts)
        self.assertTrue(out.startswith("WX Here NWS CAR  upd "))
        self.assertNotIn("Zone", out)

    def test_null_cwa_falls_back_to_grid_id(self):
        pts = {"properties": {"cwa": None, "gridId": "CAR"}}
        self.assertIn("NWS CAR", self.run_wx(points=pts))

    def test_cwa_wins_over_grid_id(self):
        pts = {"properties": {"cwa": "GYX", "gridId": "CAR"}}
        self.assertIn("NWS GYX", self.run_wx(points=pts))

    def test_update_time_is_converted_to_utc(self):
        fc = mini_forecast(period(), update="2026-11-25T01:53:28-05:00")
        self.assertIn("upd 2026-11-25 0653Z\n", self.run_wx(forecast=fc))

    def test_generated_at_is_the_fallback(self):
        fc = {"properties": {"generatedAt": "2026-11-25T13:29:33+00:00", "periods": [period()]}}
        self.assertIn("upd 2026-11-25 1329Z\n", self.run_wx(forecast=fc))

    def test_update_time_wins_over_generated_at(self):
        fc = {"properties": {"updateTime": "2026-11-25T06:53:28+00:00",
                             "generatedAt": "2026-11-25T13:29:33+00:00", "periods": [period()]}}
        self.assertIn("upd 2026-11-25 0653Z\n", self.run_wx(forecast=fc))

    def test_no_update_time(self):
        out = self.run_wx(forecast=mini_forecast(period(), update=None))
        self.assertTrue(out.startswith("WX Here NWS GYX  upd n/a\n"))

    def test_null_update_time_counts_as_absent(self):
        fc = mini_forecast(period(), update=None)
        fc["properties"]["updateTime"] = None
        self.assertIn("upd n/a\n", self.run_wx(forecast=fc))

    def test_zone_line_variants(self):
        self.assertIn("Zone MEZ024 near Portland ME\n", self.run_wx())
        self.assertIn("Zone MEZ024 near Portland\n", self.run_wx(points=mini_points(state=None)))
        self.assertIn("Zone MEZ024\n", self.run_wx(points=mini_points(city=None)))
        self.assertIn("Zone MEZ024\n", self.run_wx(points=mini_points(city=None, state=None)))
        out = self.run_wx(points=mini_points(zone=None))
        self.assertNotIn("Zone", out)

    def test_invalid_zone_id_gives_no_zone_line(self):
        for zone in ("https://api.weather.gov/zones/forecast/mez024",
                     "https://api.weather.gov/zones/forecast/MEZ24",
                     "https://api.weather.gov/zones/forecast/MEC005",
                     "https://api.weather.gov/zones/forecast/", "", "MEZ024-evil", 5):
            with self.subTest(zone=zone):
                self.assertNotIn("Zone", self.run_wx(points=mini_points(zone=zone)))

    def test_bare_zone_id_is_accepted(self):
        self.assertIn("Zone MEZ024 near", self.run_wx(points=mini_points(zone="MEZ024")))

    def test_city_text_is_cleaned_and_zone_line_wraps(self):
        out = self.run_wx(points=mini_points(city="Saint\nÉtienne " + "x" * 100, state="ME"))
        self.assertReply(out)
        lines = out.split("\n")
        self.assertTrue(lines[1].startswith("Zone MEZ024 near Saint Etienne xxx"))
        self.assertTrue(lines[2].startswith("  x"))

    def test_city_wrong_type_is_an_error(self):
        with self.assertRaises(WxFormatError):
            self.run_wx(points=mini_points(city=5))


class WxPeriodTests(ReplyCase):
    def one(self, p):
        out = format_wx(mini_points(zone=None, city=None, state=None), mini_forecast(p),
                        place="Here", node=NODE, now=NOW)
        # without the header, the two menu lines, the two source lines and the empty tail
        return self.assertReply(out).split("\n")[1:-5]

    def test_name_is_upper_cased_and_cleaned(self):
        self.assertEqual(self.one(period("this\n afternoon", "Hot."))[0], "THIS AFTERNOON: Hot.")

    def test_wrap_is_greedy_at_69_with_two_space_indent(self):
        text = " ".join(["word"] * 40)
        lines = self.one(period("Today", text))
        self.assertEqual(lines[0], "TODAY: " + " ".join(["word"] * 12))  # 7 + 12*5-1 = 66, a 13th would be 71
        self.assertEqual(lines[1], "  " + " ".join(["word"] * 13))      # 2 + 13*5-1 = 66, a 14th would be 71
        self.assertTrue(all(len(l) <= 69 for l in lines))
        self.assertEqual(" ".join(" ".join(lines).split()).replace("TODAY: ", ""), text)

    def test_line_of_exactly_69_characters_is_not_wrapped(self):
        name = "THURSDAY"
        text = "x" * (69 - len(name) - 2)
        self.assertEqual(self.one(period("Thursday", text)), [name + ": " + text])
        self.assertEqual(len(self.one(period("Thursday", text + "y"))), 2)

    def test_breaks_only_at_spaces_not_at_hyphens(self):
        text = "a" * 50 + " mid-afternoon-thunderstorm-possible-with-hail"
        lines = self.one(period("Today", text))
        self.assertEqual(lines[1], "  mid-afternoon-thunderstorm-possible-with-hail")

    def test_word_longer_than_a_line_is_broken_and_nothing_is_lost(self):
        lines = self.one(period("Today", "x" * 500))
        self.assertTrue(all(len(l) <= 69 for l in lines))
        self.assertEqual("".join(lines).count("x"), 500)

    def test_detailed_forecast_falls_back_to_short_forecast_then_na(self):
        self.assertEqual(self.one({"name": "Today", "detailedForecast": None, "shortForecast": "Rain"}),
                         ["TODAY: Rain"])
        self.assertEqual(self.one({"name": "Today", "detailedForecast": "  ", "shortForecast": "Rain"}),
                         ["TODAY: Rain"])
        self.assertEqual(self.one({"name": "Today"}), ["TODAY: n/a"])
        self.assertEqual(self.one({"name": "Today", "detailedForecast": "", "shortForecast": ""}),
                         ["TODAY: n/a"])

    def test_non_ascii_text_becomes_ascii(self):
        lines = self.one(period("Today", "Highs near 45°F, café – “quoted” \x1b[31mred\x00"))
        text = "\n".join(lines)
        self.assertIn("Highs near 45F, cafe", text)
        self.assertNotIn("\x1b", text)

    def test_tabs_newlines_and_runs_of_spaces_collapse(self):
        self.assertEqual(self.one(period("Today", "A\tB\r\n\r\nC     D"))[0], "TODAY: A B C D")

    def test_hostile_period_texts(self):
        for h in HOSTILE:
            with self.subTest(h=h[:20]):
                out = format_wx(mini_points(), mini_forecast(period("Today", h)),
                                place="Here", node=NODE, now=NOW)
                self.assertReply(out)

    def test_hostile_period_names(self):
        for h in HOSTILE:
            with self.subTest(h=h[:20]):
                args = (mini_points(), mini_forecast(period(h, "Text.")))
                if not clean(h):
                    with self.assertRaises(WxFormatError):
                        format_wx(*args, place="Here", node=NODE, now=NOW)
                else:
                    self.assertReply(format_wx(*args, place="Here", node=NODE, now=NOW))

    def test_megabyte_text_is_quick(self):
        text = ("storm " * 200_000)
        t0 = time.perf_counter()
        out = format_wx(mini_points(), mini_forecast(period("Today", text)), place="Here",
                        node=NODE, now=NOW, full=True)
        self.assertLess(time.perf_counter() - t0, 5)
        self.assertReply(out)
        self.assertEqual(out.count("storm"), 200_000)

    def test_many_periods_are_quick(self):
        periods = [period("Day %d" % i, "Sunny.") for i in range(20_000)]
        t0 = time.perf_counter()
        short = format_wx(mini_points(), mini_forecast(*periods), place="Here", node=NODE, now=NOW)
        self.assertIn("1. Full forecast (20000 periods)\n", short)
        full = format_wx(mini_points(), mini_forecast(*periods), place="Here", node=NODE, now=NOW, full=True)
        self.assertEqual(sum(1 for l in full.split("\n") if l.startswith("DAY ")), 20_000)
        self.assertLess(time.perf_counter() - t0, 10)


class WxMalformedTests(unittest.TestCase):
    def call(self, pts=DEFAULT, fc=DEFAULT, **kw):
        args = dict(place="Here", node=NODE, now=NOW)
        args.update(kw)
        return format_wx(mini_points() if pts is DEFAULT else pts,
                         mini_forecast(period()) if fc is DEFAULT else fc, **args)

    def test_baseline_works(self):
        self.assertTrue(self.call().startswith("WX Here"))

    def test_bad_points(self):
        for bad in (None, [], "text", 5, {}, {"properties": None}, {"properties": []},
                    {"properties": {}}, {"properties": {"cwa": "gyx"}}, {"properties": {"cwa": "GY"}},
                    {"properties": {"cwa": "GYXXX"}}, {"properties": {"cwa": 5}},
                    {"properties": {"cwa": "GYX\n"}}, {"properties": {"cwa": "G Y"}},
                    {"properties": {"cwa": None, "gridId": None}}, {"properties": {"cwa": "", "gridId": "GYX"}},
                    {"title": "Data Unavailable For Requested Point", "status": 404}):
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    self.call(pts=bad)

    def test_bad_forecast(self):
        for bad in (None, [], "text", 5, {}, {"properties": None}, {"properties": {}},
                    {"properties": {"periods": None}}, {"properties": {"periods": {}}},
                    {"properties": {"periods": "x"}}, {"properties": {"periods": [None]}},
                    {"properties": {"periods": [5]}}, {"properties": {"periods": ["x"]}},
                    {"properties": {"periods": [[]]}}):
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    self.call(fc=bad)

    def test_bad_period_fields(self):
        bad_periods = [
            {}, {"detailedForecast": "x"}, {"name": None}, {"name": ""}, {"name": "  \n"},
            {"name": 5}, {"name": ["Today"]}, {"name": "\x00\x01"},
            {"name": "Today", "detailedForecast": 5},
            {"name": "Today", "detailedForecast": ["a"]},
            {"name": "Today", "shortForecast": 7},
            {"name": "Today", "endTime": "soon"},
            {"name": "Today", "endTime": 12345},
            {"name": "Today", "endTime": "2026-11-25T18:00:00"},  # no offset
            {"name": "Today", "endTime": ""},
        ]
        for bad in bad_periods:
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    self.call(fc=mini_forecast(bad))

    def test_every_period_is_validated_shown_or_not(self):
        with self.assertRaises(WxFormatError):
            self.call(fc=mini_forecast(period(), period(), period(), period(), {"name": 5}))
        with self.assertRaises(WxFormatError):  # expired, and not shown
            self.call(fc=mini_forecast(period(end="2020-01-01T00:00:00Z", text=5), period()))

    def test_bad_update_time(self):
        for bad in ("yesterday", "", "2026-11-25T06:53:28", 5, ["2026-11-25T06:53:28+00:00"]):
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    self.call(fc=mini_forecast(period(), update=bad))

    def test_bad_place_and_node(self):
        for bad in (None, 5, b"x", ["x"], "", "   ", "\n\t", "\x00\x7f", "​﻿"):
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    self.call(place=bad)
                with self.assertRaises(WxFormatError):
                    self.call(node=bad)

    def test_naive_or_wrong_now(self):
        for bad in (datetime(2026, 11, 25, 13, 45), None, "2026-11-25T13:45:00Z", 1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    self.call(now=bad)

    def test_node_in_source_line_is_cleaned_and_cut(self):
        out = self.call(node="  my\nnodeé  ")
        self.assertIn("\nSource: NWS via my nodee\n", out)
        out = self.call(node="n" * 100)
        src = [l for l in out.split("\n") if l.startswith("Source:")][0]
        self.assertEqual(src, "Source: NWS via " + "n" * 50 + "...")
        self.assertEqual(len(src), 69)
        out = self.call(node="n" * 53)
        self.assertIn("\nSource: NWS via " + "n" * 53 + "\n", out)

    def test_hostile_node(self):
        for h in HOSTILE:
            with self.subTest(h=h[:20]):
                if not clean(h):
                    with self.assertRaises(WxFormatError):
                        self.call(node=h)
                else:
                    check_reply(self, self.call(node=h))

    def test_hostile_place(self):
        for h in HOSTILE:
            with self.subTest(h=h[:20]):
                if not clean(h):
                    with self.assertRaises(WxFormatError):
                        self.call(place=h)
                else:
                    check_reply(self, self.call(place=h))


# ------------------------------------------------------------------ ALERTS

ALERTS_HEAD = "ALERTS CA: 3 active (checked 04:33Z)\n"

ALERT1 = (
    "1. HIGH WIND WARNING\n"
    "   Severe, NWS Medford OR\n"
    "   Until 2026-11-24 22:00 -08:00 (2026-11-25 0600Z)\n"
    "   WHAT...South winds 30 to 40 mph with gusts up to 60 mph.\n"
    "   WHERE...Most of the Shasta Valley in central Siskiyou County. This\n"
    "   includes portions of Interstate 5 as well as the communities of\n"
    "   Weed, Gazelle, Grenada and Montague. WHEN...Until 10 PM PST this\n"
    "   evening. IMPACTS...Damaging winds could blow down trees, power\n"
    "   lines, and unsecured objects. Power outages are possible. Travel\n"
    "   could be...\n"
    "   ACTION: Winds this strong can make driving difficult, especially\n"
    "   for high profile vehicles. Use extra caution. People are urged to\n"
    "   secure loose objects,...\n")

ALERT2 = (
    "2. WIND ADVISORY\n"
    "   Moderate, NWS Eureka CA\n"
    "   Until 2026-11-25 09:00 -08:00 (2026-11-25 1700Z)\n"
    "   WHAT...South winds 20 to 30 mph with gusts up to 55 mph.\n"
    "   WHERE...Southwestern Humboldt. WHEN...Until 9 AM PST Wednesday.\n"
    "   IMPACTS...Wind gusts will be particularly strong along windward\n"
    "   ridges and exposed coastal headlands.\n"
    "   ACTION: Winds this strong can make driving difficult, especially\n"
    "   for high profile vehicles. Use extra caution.\n")

ALERT3 = (
    "3. COASTAL FLOOD WATCH\n"
    "   Moderate, NWS Eureka CA\n"
    "   Until 2026-11-26 15:00 -08:00 (2026-11-26 2300Z)\n"
    "   WHAT...For the Coastal Flood Warning, High astronomical tides will\n"
    "   cause major flooding in low lying areas. Up to 1 foot of saltwater\n"
    "   inundation above ground level is possible in low lying areas near\n"
    "   shorelines and tidal waterways. For the Coastal Flood Watch, High\n"
    "   astronomical tides and large surf may cause major flooding in low\n"
    "   lying areas along the Redwood Coast. Up to 1.0 foot of\n"
    "   saltwater...\n"
    "   ACTION: A Coastal Flood Warning means that flooding is occurring\n"
    "   or imminent. Coastal residents in the warned area should be alert\n"
    "   for rising water and...\n")

ALERTS_SHORT = ALERTS_HEAD + ALERT1 + ALERT2 + ALERT3 + (
    "Read one in full:\n"
    "1. ALERTS CA | 1\n"
    "2. ALERTS CA | 2\n"
    "3. ALERTS CA | 3\n") + SOURCE

ALERT_FULL_1 = (
    "ALERTS CA: alert 1 of 3 (checked 04:33Z)\n"
    "HIGH WIND WARNING\n"
    "Severe, NWS Medford OR\n"
    "Until 2026-11-24 22:00 -08:00 (2026-11-25 0600Z)\n"
    "\n"
    "* WHAT...South winds 30 to 40 mph with gusts up to 60 mph.\n"
    "\n"
    "* WHERE...Most of the Shasta Valley in central Siskiyou County. This\n"
    "includes portions of Interstate 5 as well as the communities of Weed,\n"
    "Gazelle, Grenada and Montague.\n"
    "\n"
    "* WHEN...Until 10 PM PST this evening.\n"
    "\n"
    "* IMPACTS...Damaging winds could blow down trees, power lines, and\n"
    "unsecured objects. Power outages are possible. Travel could be\n"
    "difficult, especially for high profile vehicles.\n"
    "\n"
    "* ADDITIONAL DETAILS...Highways and roads running west to east,\n"
    "including highway 97, will be exposed to tough driving conditions\n"
    "given the strong southerly wind speeds.\n"
    "\n"
    "ACTION: Winds this strong can make driving difficult, especially for\n"
    "high profile vehicles. Use extra caution.\n"
    "\n"
    "People are urged to secure loose objects, including large or\n"
    "inflatable holiday decorations that could be blown around or damaged\n"
    "by the wind.\n") + SOURCE

ALERT_FULL_2 = (
    "ALERTS CA: alert 2 of 3 (checked 04:33Z)\n"
    "WIND ADVISORY\n"
    "Moderate, NWS Eureka CA\n"
    "Until 2026-11-25 09:00 -08:00 (2026-11-25 1700Z)\n"
    "\n"
    "* WHAT...South winds 20 to 30 mph with gusts up to 55 mph.\n"
    "\n"
    "* WHERE...Southwestern Humboldt.\n"
    "\n"
    "* WHEN...Until 9 AM PST Wednesday.\n"
    "\n"
    "* IMPACTS...Wind gusts will be particularly strong along windward\n"
    "ridges and exposed coastal headlands.\n"
    "\n"
    "ACTION: Winds this strong can make driving difficult, especially for\n"
    "high profile vehicles. Use extra caution.\n") + SOURCE


def al(coll=DEFAULT, **kw):
    args = dict(place="CA", node=NODE, checked_at=CHECKED)
    args.update(kw)
    return format_alerts(alerts() if coll is DEFAULT else coll, **args)


def block_text(out):
    """The description lines of the first block, indents removed and joined."""
    lines = out.split("\n")
    i = next(n for n, l in enumerate(lines) if l.startswith("   Until"))
    body = []
    for l in lines[i + 1:]:
        if not l.startswith("   "):
            break
        body.append(l[3:])
    return body


class AlertsExactTests(ReplyCase):
    def test_short_reply(self):
        self.assertEqual(al(), ALERTS_SHORT)

    def test_property_checks(self):
        self.assertReply(al())

    def test_limit_one(self):
        expected = (ALERTS_HEAD + "Showing the first 1 of 3.\n" + ALERT1
                    + "Read one in full:\n1. ALERTS CA | 1\n" + SOURCE)
        self.assertEqual(al(limit=1), expected)

    def test_limit_equal_to_count_has_no_showing_line(self):
        self.assertEqual(al(limit=3), ALERTS_SHORT)
        self.assertEqual(al(limit=20), ALERTS_SHORT)

    def test_bad_limit(self):
        for bad in (0, -1, 21, 100, True, False, "5", 5.0, None, [5]):
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    al(limit=bad)

    def test_full_alert_one(self):
        out = format_alert_full(alerts(), 1, place="CA", node=NODE, checked_at=CHECKED)
        self.assertEqual(out, ALERT_FULL_1)

    def test_full_alert_two(self):
        out = format_alert_full(alerts(), 2, place="CA", node=NODE, checked_at=CHECKED)
        self.assertEqual(out, ALERT_FULL_2)

    def test_full_alert_three_properties(self):
        out = self.assertReply(format_alert_full(alerts(), 3, place="CA", node=NODE, checked_at=CHECKED))
        self.assertIn("\nACTION: A Coastal Flood Warning means that flooding is occurring or\n", out)
        self.assertIn("discouraged!.\n", out)  # the end of the IMPACTS paragraph, not cut
        squashed = "".join(out.split())
        self.assertIn("significantcoastalimpacts.", squashed)           # the end of the description
        self.assertIn("harborandbarclosures.", squashed)                # the end of the instruction

    def test_full_alert_bad_numbers(self):
        for bad in (0, 4, -1, 100, True, False, "1", 1.0, None, [1]):
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    format_alert_full(alerts(), bad, place="CA", node=NODE, checked_at=CHECKED)

    def test_unknown_fields_are_ignored(self):
        self.assertEqual(al(sprinkle(alerts())), ALERTS_SHORT)
        out = format_alert_full(sprinkle(alerts()), 1, place="CA", node=NODE, checked_at=CHECKED)
        self.assertEqual(out, ALERT_FULL_1)

    def test_inputs_are_not_changed(self):
        a = alerts()
        before = copy.deepcopy(a)
        al(a)
        format_alert_full(a, 3, place="CA", node=NODE, checked_at=CHECKED)
        self.assertEqual(a, before)

    def test_checked_at_in_another_zone_is_the_same_instant(self):
        local = datetime(2026, 11, 24, 20, 33, tzinfo=timezone(timedelta(hours=-8)))
        self.assertEqual(al(checked_at=local), ALERTS_SHORT)

    def test_checked_at_is_not_used_to_drop_alerts(self):
        # Every alert in the fixture has already ended by this time; the
        # response is what NWS called active when it was fetched.
        late = datetime(2027, 1, 1, tzinfo=UTC)
        out = al(checked_at=late)
        self.assertTrue(out.startswith("ALERTS CA: 3 active (checked 00:00Z)\n"))


class AlertsNoneTests(ReplyCase):
    def test_none_active_fixture(self):
        out = format_alerts(load("nws", "alerts_none.json"), place="ME", node=NODE, checked_at=CHECKED)
        self.assertEqual(out, "ALERTS ME: no active alerts (checked 04:33Z).\n")

    def test_none_active_with_minutes_in_utc(self):
        local = datetime(2026, 11, 25, 6, 5, tzinfo=timezone(timedelta(hours=2)))
        out = al(collection(), checked_at=local)
        self.assertEqual(out, "ALERTS CA: no active alerts (checked 04:05Z).\n")

    def test_skipped_statuses_leave_none_active(self):
        out = al(collection(feature(status="Test"), feature(status="Exercise")))
        self.assertEqual(out, "ALERTS CA: no active alerts (checked 04:33Z).\n")

    def test_long_place_in_none_line(self):
        out = self.assertReply(al(collection(), place="A" * 100))
        self.assertEqual(out, "ALERTS " + "A" * 23 + "...: no active alerts (checked 04:33Z).\n")
        self.assertEqual(len(out) - 1, 69)

    def test_none_line_has_no_attribution(self):
        self.assertNotIn("Source", al(collection()))

    def test_unknown_fields_in_empty_response(self):
        self.assertEqual(al(sprinkle(collection())), "ALERTS CA: no active alerts (checked 04:33Z).\n")


class AlertsFieldTests(ReplyCase):
    def one(self, event="Test Warning", **props):
        return self.assertReply(al(collection(feature(event, **props))))

    def test_status_actual_and_absent_are_kept(self):
        out = al(collection(feature("A", status="Actual"), feature("B"), feature("C", status="Test")))
        self.assertIn("2 active", out)
        self.assertIn("\n1. A\n", out)
        self.assertIn("\n2. B\n", out)
        self.assertNotIn("\n3. ", out)

    def test_skipped_feature_is_not_validated_further(self):
        out = al(collection(feature(event=None, status="Test"), {"properties": {"status": "System"}}, feature("B")))
        self.assertIn("1 active", out)

    def test_status_wrong_type(self):
        with self.assertRaises(WxFormatError):
            al(collection(feature(status=5)))

    def test_full_number_counts_only_kept_alerts(self):
        coll = collection(feature("Skipped One", status="Test"), feature("Kept One"), feature("Kept Two"))
        out = format_alert_full(coll, 2, place="CA", node=NODE, checked_at=CHECKED)
        self.assertIn("alert 2 of 2", out)
        self.assertIn("KEPT TWO\n", out)
        with self.assertRaises(WxFormatError):
            format_alert_full(coll, 3, place="CA", node=NODE, checked_at=CHECKED)

    def test_event_is_upper_cased_and_cleaned(self):
        out = self.one(event="  special\nweather étude statement ")
        self.assertIn("\n1. SPECIAL WEATHER ETUDE STATEMENT\n", out)

    def test_duplicates_are_kept(self):
        f = feature("Same")
        self.assertIn("2 active", al(collection(f, copy.deepcopy(f))))

    def test_api_order_is_kept(self):
        out = al(collection(feature("Zulu"), feature("Alpha")))
        self.assertLess(out.index("1. ZULU"), out.index("2. ALPHA"))

    def test_severity_and_sender_line(self):
        self.assertIn("\n   Severe, NWS Medford OR\n", self.one(severity="Severe", senderName="NWS Medford OR"))
        self.assertIn("\n   Severe\n", self.one(severity="Severe"))
        self.assertIn("\n   NWS Medford OR\n", self.one(senderName="NWS Medford OR"))
        out = self.one()
        self.assertEqual(out.split("\n")[2], "   Until 2026-11-25 09:00 -08:00 (2026-11-25 1700Z)")
        out = self.one(severity=None, senderName="  ")
        self.assertEqual(out.split("\n")[2][:8], "   Until")

    def test_until_prefers_ends_over_expires(self):
        out = self.one(ends="2026-11-26T15:00:00-08:00", expires="2026-11-25T14:00:00-08:00")
        self.assertIn("   Until 2026-11-26 15:00 -08:00 (2026-11-26 2300Z)\n", out)

    def test_until_falls_back_to_expires(self):
        out = self.one(ends=None, expires="2026-11-25T14:00:00-08:00")
        self.assertIn("   Until 2026-11-25 14:00 -08:00 (2026-11-25 2200Z)\n", out)

    def test_until_absent_or_null(self):
        out = self.one(ends=None)
        self.assertIn("\n   Until n/a\n", out)
        f = feature("X")
        del f["properties"]["ends"]
        self.assertIn("\n   Until n/a\n", al(collection(f)))

    def test_until_offsets(self):
        cases = {
            "2026-11-25T09:30:00+05:30": "2026-11-25 09:30 +05:30 (2026-11-25 0400Z)",
            "2026-11-25T09:30:00Z": "2026-11-25 09:30 +00:00 (2026-11-25 0930Z)",
            "2026-11-25T23:59:59-00:30": "2026-11-25 23:59 -00:30 (2026-11-26 0029Z)",
            "2026-12-31T23:00:00-05:00": "2026-12-31 23:00 -05:00 (2027-01-01 0400Z)",
        }
        for given, shown in cases.items():
            with self.subTest(given=given):
                self.assertIn("   Until %s\n" % shown, self.one(ends=given))

    def test_bad_until_values(self):
        for bad in ("soon", "", "2026-11-25T09:00:00", "2026-11-25", 5, ["2026-11-25T09:00:00Z"], True):
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    self.one(ends=bad)
        with self.assertRaises(WxFormatError):
            self.one(ends=None, expires="tomorrow")

    def test_bad_text_types(self):
        for key in ("description", "instruction", "severity", "senderName"):
            for bad in (5, ["x"], {"a": 1}, True):
                with self.subTest(key=key, bad=bad):
                    with self.assertRaises(WxFormatError):
                        self.one(**{key: bad})

    def test_no_description_and_no_instruction(self):
        for v in (None, "", "  \n\t\n "):
            with self.subTest(v=v):
                out = self.one(description=v, instruction=v)
                self.assertEqual(out.split("\n")[:4], ["ALERTS CA: 1 active (checked 04:33Z)",
                                                       "1. TEST WARNING",
                                                       "   Until 2026-11-25 09:00 -08:00 (2026-11-25 1700Z)",
                                                       "Read one in full:"])

    def test_instruction_only_and_description_only(self):
        out = self.one(description="Heavy rain.")
        self.assertIn("   Heavy rain.\n", out)
        self.assertNotIn("ACTION", out)
        out = self.one(instruction="Stay home.")
        self.assertIn("   ACTION: Stay home.\n", out)

    def test_paragraphs_join_and_bullets_are_removed(self):
        out = self.one(description="* WHAT...Wind.\n\n* WHERE...Here.\n  \n\n\n*not a bullet\n\n** bold **")
        self.assertEqual(block_text(out)[0], "WHAT...Wind. WHERE...Here. *not a bullet ** bold **")

    def test_single_newlines_are_just_spaces(self):
        out = self.one(description="a\nb\r\nc\n* d")
        self.assertEqual(block_text(out)[0], "a b c * d")

    def test_event_wrong_or_missing(self):
        for bad in (None, "", "  ", "\x00", 5, ["x"]):
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    al(collection(feature(event=bad)))
        with self.assertRaises(WxFormatError):
            al(collection({"properties": {"ends": "2026-11-25T09:00:00-08:00"}}))

    def test_malformed_collections(self):
        for bad in (None, [], "x", 5, {}, {"features": None}, {"features": {}}, {"features": "x"},
                    {"features": [None]}, {"features": [5]}, {"features": [[]]}, {"features": [{}]},
                    {"features": [{"properties": None}]}, {"features": [{"properties": []}]},
                    {"title": "Not Found", "status": 404}):
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    al(bad)
                with self.assertRaises(WxFormatError):
                    format_alert_full(bad, 1, place="CA", node=NODE, checked_at=CHECKED)

    def test_bad_arguments(self):
        for kw in ({"place": ""}, {"place": None}, {"node": ""}, {"node": 5},
                   {"checked_at": datetime(2026, 11, 25, 4, 33)}, {"checked_at": None},
                   {"checked_at": "2026-11-25T04:33:00Z"}):
            with self.subTest(kw=kw):
                with self.assertRaises(WxFormatError):
                    al(**kw)
                with self.assertRaises(WxFormatError):
                    format_alert_full(alerts(), 1, **dict(dict(place="CA", node=NODE, checked_at=CHECKED), **kw))


class AlertsCutTests(ReplyCase):
    def body(self, **props):
        out = self.assertReply(al(collection(feature(**props))))
        return block_text(out)

    def desc(self, text, flat=False):
        lines = self.body(description=text)
        return "".join(lines) if flat else " ".join(lines)

    def instr(self, text, flat=False):
        lines = self.body(instruction=text)
        self.assertTrue(lines[0].startswith("ACTION:"))
        if flat:
            return "".join(lines).replace(" ", "")[len("ACTION:"):]
        return " ".join(lines)[len("ACTION: "):]

    def test_400_is_kept_and_401_is_cut(self):
        s = "ab " * 133 + "a"
        self.assertEqual(len(s), 400)
        self.assertEqual(self.desc(s), s)
        # 401 characters: window = text[:398], whose last space is at index 395
        self.assertEqual(self.desc(s + "b"), "ab " * 131 + "ab" + "...")

    def test_cut_at_last_space_in_window(self):
        # 100 words of 4 letters: spaces at 4, 9, ..., window = text[:398],
        # last space at index 394, so 79 words remain.
        text = " ".join(["word"] * 100)
        self.assertEqual(self.desc(text), " ".join(["word"] * 79) + "...")
        self.assertEqual(len(" ".join(["word"] * 79) + "..."), 397)

    def test_space_exactly_at_the_window_end(self):
        text = "a" * 397 + " " + "b" * 10
        self.assertEqual(self.desc(text, flat=True), "a" * 397 + "...")

    def test_no_space_in_window_cuts_hard(self):
        text = "a" * 398 + " bbb"
        self.assertEqual(self.desc(text, flat=True), "a" * 397 + "...")
        self.assertEqual(self.desc("z" * 100000, flat=True), "z" * 397 + "...")

    def test_result_is_at_most_400(self):
        for n in range(395, 410):
            with self.subTest(n=n):
                text = " ".join(["abcde"] * 200)[:n].rstrip()
                got = self.desc(text)
                self.assertLessEqual(len(got), 400)
                if len(text) <= 400:
                    self.assertEqual(got, text)
                else:
                    self.assertTrue(got.endswith("..."))

    def test_instruction_limit_is_150_without_the_prefix(self):
        self.assertEqual(self.instr("a" * 150, flat=True), "a" * 150)
        text = " ".join(["word"] * 100)
        # window = text[:148]; spaces at 4, 9, ..., last at 144: 29 words
        self.assertEqual(self.instr(text), " ".join(["word"] * 29) + "...")
        self.assertEqual(self.instr("a" * 151, flat=True), "a" * 147 + "...")
        self.assertEqual(self.instr("a" * 147 + " " + "b" * 9, flat=True), "a" * 147 + "...")

    def test_bullets_are_removed_before_the_limit_is_counted(self):
        text = "* " + "a" * 400  # 402 characters, 400 after the bullet
        self.assertEqual(self.desc(text, flat=True), "a" * 400)

    def test_megabyte_description_is_quick(self):
        t0 = time.perf_counter()
        out = al(collection(feature(description="storm " * 200_000, instruction="act " * 200_000)))
        self.assertLess(time.perf_counter() - t0, 5)
        self.assertLess(len(out), 3000)
        self.assertReply(out)

    def test_full_variant_does_not_cut(self):
        text = " ".join(["word"] * 1000)
        out = self.assertReply(format_alert_full(collection(feature(description=text)), 1, place="CA",
                                                 node=NODE, checked_at=CHECKED))
        self.assertEqual(out.count("word"), 1000)
        self.assertNotIn("...", out)

    def test_full_variant_paragraphs(self):
        coll = collection(feature(description="One.\n\n\n  \nTwo\nlines.\n \n\nThree.",
                                  instruction="Do this.\n\nThen that."))
        out = format_alert_full(coll, 1, place="CA", node=NODE, checked_at=CHECKED)
        self.assertEqual(out, "ALERTS CA: alert 1 of 1 (checked 04:33Z)\n"
                              "TEST WARNING\n"
                              "Until 2026-11-25 09:00 -08:00 (2026-11-25 1700Z)\n"
                              "\n"
                              "One.\n"
                              "\n"
                              "Two lines.\n"
                              "\n"
                              "Three.\n"
                              "\n"
                              "ACTION: Do this.\n"
                              "\n"
                              "Then that.\n" + SOURCE)

    def test_full_variant_without_text(self):
        out = format_alert_full(collection(feature()), 1, place="CA", node=NODE, checked_at=CHECKED)
        self.assertEqual(out, "ALERTS CA: alert 1 of 1 (checked 04:33Z)\n"
                              "TEST WARNING\n"
                              "Until 2026-11-25 09:00 -08:00 (2026-11-25 1700Z)\n" + SOURCE)

    def test_full_variant_long_word_is_split(self):
        out = format_alert_full(collection(feature(description="y" * 1000)), 1, place="CA",
                                node=NODE, checked_at=CHECKED)
        self.assertReply(out)
        self.assertEqual(out.count("y"), 1000)


class AlertsPlaceAndLimitTests(ReplyCase):
    def test_long_place_header_and_command_lines(self):
        place = "A" * 129
        out = self.assertReply(al(place=place))
        lines = out.split("\n")
        self.assertEqual(lines[0], "ALERTS " + "A" * 32 + "...: 3 active (checked 04:33Z)")
        self.assertEqual(len(lines[0]), 69)
        i = lines.index("Read one in full:")
        command = [lines[i + 1]]
        j = i + 2
        while lines[j].startswith("   "):
            command.append(lines[j])
            j += 1
        self.assertTrue(command[0].startswith("1. ALERTS AAAA"))
        self.assertEqual("".join("".join(x.split()) for x in command), "1.ALERTS" + place + "|1")

    def test_place_with_spaces_wraps_the_command_line(self):
        place = " ".join(["Plantation"] * 9)
        out = self.assertReply(al(place=place, limit=1))
        lines = out.split("\n")
        i = lines.index("Read one in full:")
        self.assertEqual(lines[i + 1], "1. ALERTS Plantation Plantation Plantation Plantation Plantation")
        self.assertEqual(lines[i + 2], "   Plantation Plantation Plantation Plantation | 1")

    def test_many_alerts(self):
        coll = collection(*[feature("Event %d" % i, description="d %d" % i) for i in range(200)])
        out = self.assertReply(al(coll))
        self.assertTrue(out.startswith("ALERTS CA: 200 active (checked 04:33Z)\nShowing the first 5 of 200.\n"))
        self.assertEqual(out.count("ALERTS CA | "), 5)
        self.assertIn("5. EVENT 4\n", out)
        self.assertNotIn("EVENT 5\n", out)
        out = self.assertReply(al(coll, limit=20))
        self.assertEqual(out.count("ALERTS CA | "), 20)
        full = format_alert_full(coll, 200, place="CA", node=NODE, checked_at=CHECKED)
        self.assertIn("alert 200 of 200", full)

    def test_hundred_thousand_alerts_are_quick(self):
        coll = collection(*[feature("E") for _ in range(100_000)])
        t0 = time.perf_counter()
        out = al(coll)
        format_alert_full(coll, 100_000, place="CA", node=NODE, checked_at=CHECKED)
        self.assertLess(time.perf_counter() - t0, 10)
        self.assertIn("100000 active", out)

    def test_hostile_values_everywhere(self):
        for h in HOSTILE:
            with self.subTest(h=h[:20]):
                f = feature("Warning " + h, severity=h, senderName=h, description=h, instruction=h)
                self.assertReply(al(collection(f)))
                self.assertReply(format_alert_full(collection(f), 1, place="CA", node=NODE, checked_at=CHECKED))
                if clean(h):
                    self.assertReply(al(collection(feature("W")), place=h))
                    self.assertReply(al(collection(feature("W")), node=h))
                    self.assertReply(al(collection(), place=h))

    def test_hostile_event_alone(self):
        for h in HOSTILE:
            with self.subTest(h=h[:20]):
                coll = collection(feature(h))
                if not clean(h):
                    with self.assertRaises(WxFormatError):
                        al(coll)
                else:
                    self.assertReply(al(coll))


# ------------------------------------------------------------------- SOLAR

SOLAR_EXPECTED = (
    "SOLAR 2026-11-25 0433Z (data age 12 min)\n"
    "SFI 88  A 15  Kp 0 (3h max 3)\n"
    "X-ray B2.1  Storms now: R0 S0 G0\n"
    "Forecast: Kp up to 5 (G1) on Nov 27\n"
    "Source: NOAA SWPC\n")


def solar(**kw):
    args = dict(fetched_at=FETCHED, now=SOLAR_NOW)
    args.update(solar_args())
    args.update(kw)
    return format_solar(**args)


def kp3(tag, kp, a=4):
    return {"time_tag": tag, "Kp": kp, "a_running": a, "station_count": 8}


def kp1(tag, kp):
    return {"time_tag": tag, "kp_index": 0, "estimated_kp": kp, "kp": "0Z"}


def xr(tag, flux, energy="0.1-0.8nm"):
    return {"time_tag": tag, "satellite": 18, "flux": flux, "observed_flux": flux, "energy": energy}


def lines_of(text):
    return text.split("\n")[:-1]


class SolarExactTests(ReplyCase):
    def test_fixture_reply(self):
        self.assertEqual(solar(), SOLAR_EXPECTED)

    def test_properties(self):
        self.assertReply(solar())

    def test_unknown_fields_are_ignored(self):
        args = {k: sprinkle(v) for k, v in solar_args().items() if not isinstance(v, str)}
        args["forecast_3day"] = solar_args()["forecast_3day"]
        self.assertEqual(format_solar(fetched_at=FETCHED, now=SOLAR_NOW, **args), SOLAR_EXPECTED)

    def test_inputs_are_not_changed(self):
        args = {k: v for k, v in solar_args().items()}
        before = copy.deepcopy(args)
        format_solar(fetched_at=FETCHED, now=SOLAR_NOW, **args)
        self.assertEqual(args, before)

    def test_now_in_another_zone_is_the_same_instant(self):
        zone = timezone(timedelta(hours=-5))
        out = solar(now=SOLAR_NOW.astimezone(zone), fetched_at=FETCHED.astimezone(zone))
        self.assertEqual(out, SOLAR_EXPECTED)

    def test_shuffled_lists_give_the_same_reply(self):
        a = solar_args()
        for key in ("flux", "kp_3h", "kp_1m", "xray"):
            a[key] = list(reversed(a[key]))
        a["xray"] = a["xray"][1::2] + a["xray"][0::2]
        self.assertEqual(format_solar(fetched_at=FETCHED, now=SOLAR_NOW, **a), SOLAR_EXPECTED)

    def test_crlf_forecast_text(self):
        text = solar_args()["forecast_3day"].replace("\n", "\r\n")
        self.assertEqual(solar(forecast_3day=text), SOLAR_EXPECTED)


class SolarAgeTests(ReplyCase):
    def header(self, **delta):
        out = solar(fetched_at=SOLAR_NOW - timedelta(**delta))
        return out.split("\n")[0]

    def test_age_steps(self):
        cases = [
            (dict(seconds=0), "0 min"), (dict(seconds=59), "0 min"), (dict(seconds=60), "1 min"),
            (dict(minutes=11, seconds=59), "11 min"), (dict(minutes=12), "12 min"),
            (dict(minutes=119, seconds=59), "119 min"), (dict(minutes=120), "2 h"),
            (dict(minutes=179), "2 h"), (dict(minutes=180), "3 h"),
            (dict(minutes=2879), "47 h"), (dict(minutes=2880), "2 d"),
            (dict(days=3, hours=23), "3 d"), (dict(days=400), "400 d"),
        ]
        for delta, text in cases:
            with self.subTest(delta=delta):
                self.assertEqual(self.header(**delta), "SOLAR 2026-11-25 0433Z (data age %s)" % text)

    def test_fetched_in_the_future_counts_as_zero(self):
        self.assertEqual(self.header(minutes=-30), "SOLAR 2026-11-25 0433Z (data age 0 min)")

    def test_header_uses_utc_with_zero_padding(self):
        out = solar(now=datetime(2026, 1, 2, 3, 4, 59, tzinfo=UTC), fetched_at=datetime(2026, 1, 2, 3, 0, tzinfo=UTC))
        self.assertEqual(out.split("\n")[0], "SOLAR 2026-01-02 0304Z (data age 4 min)")


class SolarPartialTests(ReplyCase):
    def test_each_missing_product(self):
        cases = {
            "flux": {1: "SFI n/a  A 15  Kp 0 (3h max 3)"},
            "kp_3h": {1: "SFI 88  A n/a  Kp 0"},
            "kp_1m": {1: "SFI 88  A 15  Kp n/a (3h max 3)"},
            "scales": {2: "X-ray B2.1  Storms now: n/a"},
            "xray": {2: "X-ray n/a  Storms now: R0 S0 G0"},
            "forecast_3day": {3: "Forecast: n/a"},
        }
        for key, changes in cases.items():
            with self.subTest(key=key):
                got = lines_of(self.assertReply(solar(**{key: None})))
                want = lines_of(SOLAR_EXPECTED)
                for i, text in changes.items():
                    want[i] = text
                self.assertEqual(got, want)

    def test_only_one_product(self):
        out = solar(flux=load("swpc", "10cm-flux.json"), kp_3h=None, kp_1m=None, scales=None,
                    xray=None, forecast_3day=None)
        self.assertEqual(out, "SOLAR 2026-11-25 0433Z (data age 12 min)\n"
                              "SFI 88  A n/a  Kp n/a\n"
                              "X-ray n/a  Storms now: n/a\n"
                              "Forecast: n/a\n"
                              "Source: NOAA SWPC\n")

    def test_all_missing_is_an_error(self):
        with self.assertRaises(WxFormatError):
            format_solar(fetched_at=FETCHED, now=SOLAR_NOW)

    def test_empty_products_are_n_a_not_errors(self):
        out = format_solar(fetched_at=FETCHED, now=SOLAR_NOW, flux=[], kp_3h=[], kp_1m=[], scales={},
                           xray=[], forecast_3day="")
        self.assertEqual(out, "SOLAR 2026-11-25 0433Z (data age 12 min)\n"
                              "SFI n/a  A n/a  Kp n/a\n"
                              "X-ray n/a  Storms now: n/a\n"
                              "Forecast: n/a\n"
                              "Source: NOAA SWPC\n")

    def test_shape_errors(self):
        for key in ("flux", "kp_3h", "kp_1m", "xray"):
            for bad in ({}, "text", 5, {"flux": 88}, True):
                with self.subTest(key=key, bad=bad):
                    with self.assertRaises(WxFormatError):
                        solar(**{key: bad})
        for bad in ([], "text", 5, [{"0": {}}], True):
            with self.subTest(scales=bad):
                with self.assertRaises(WxFormatError):
                    solar(scales=bad)
        for bad in (5, [], {}, b"text", True):
            with self.subTest(forecast=bad):
                with self.assertRaises(WxFormatError):
                    solar(forecast_3day=bad)

    def test_bad_times(self):
        for kw in ({"fetched_at": datetime(2026, 11, 25, 4, 21)}, {"now": datetime(2026, 11, 25, 4, 33)},
                   {"fetched_at": None}, {"now": None}, {"now": "2026-11-25T04:33:00Z"}):
            with self.subTest(kw=kw):
                with self.assertRaises(WxFormatError):
                    solar(**kw)

    def test_garbage_entries_are_skipped_silently(self):
        junk = [None, 5, "x", [], {}, {"flux": 90}, {"time_tag": "2026-11-25T00:00:00"},
                {"time_tag": "never", "flux": 99}, {"time_tag": 5, "flux": 99}]
        a = solar_args()
        for key in ("flux", "kp_3h", "kp_1m", "xray"):
            a[key] = junk + a[key]
        self.assertEqual(format_solar(fetched_at=FETCHED, now=SOLAR_NOW, **a), SOLAR_EXPECTED)

    def test_all_garbage_entries_give_n_a(self):
        junk = [None, 5, "x", [], {}]
        out = solar(flux=junk, kp_3h=junk, kp_1m=junk, xray=junk, scales={"0": 5}, forecast_3day="junk")
        self.assertEqual(lines_of(out)[1:4], ["SFI n/a  A n/a  Kp n/a", "X-ray n/a  Storms now: n/a",
                                              "Forecast: n/a"])


class SolarValueTests(ReplyCase):
    def sfi(self, flux, tag="2026-11-24T20:00:00"):
        return lines_of(solar(flux=[{"flux": flux, "time_tag": tag}]))[1].split("  A ")[0]

    def test_sfi_formats(self):
        for value, text in ((88, "SFI 88"), (88.0, "SFI 88"), (88.46, "SFI 88.5"), (88.44, "SFI 88.4"),
                            (150.25, "SFI 150.3"), (0.5, "SFI 0.5"), (9999, "SFI 9999"),
                            (9999.94, "SFI 9999.9")):
            with self.subTest(value=value):
                self.assertEqual(self.sfi(value), text)

    def test_sfi_unusable_values(self):
        for bad in (0, 0.0, -1, 10000, 1e30, 1e308, float("nan"), float("inf"), float("-inf"),
                    "88", None, True, False, [88], {"v": 88}):
            with self.subTest(bad=bad):
                self.assertEqual(self.sfi(bad), "SFI n/a")

    def test_sfi_latest_usable_entry_wins(self):
        flux = [{"flux": 70, "time_tag": "2026-11-20T20:00:00"}, {"flux": 99, "time_tag": "2026-11-24T20:00:00"},
                {"flux": "bad", "time_tag": "2026-11-25T20:00:00"}, {"flux": 80, "time_tag": "2026-11-22T20:00:00"}]
        self.assertTrue(lines_of(solar(flux=flux))[1].startswith("SFI 99  "))

    def test_equal_time_tags_later_entry_wins(self):
        flux = [{"flux": 70, "time_tag": "2026-11-24T20:00:00"}, {"flux": 71, "time_tag": "2026-11-24T20:00:00"}]
        self.assertTrue(lines_of(solar(flux=flux))[1].startswith("SFI 71  "))

    def test_time_tag_forms(self):
        tags = ["2026-11-24T20:00:00", "2026-11-24T20:00:00Z", "2026-11-24T20:00:00+00:00"]
        for tag in tags:
            with self.subTest(tag=tag):
                self.assertEqual(self.sfi(88, tag), "SFI 88")
        # 21:00+02:00 is 19:00Z, earlier than 20:00 (naive means UTC)
        flux = [{"flux": 1, "time_tag": "2026-11-24T21:00:00+02:00"}, {"flux": 2, "time_tag": "2026-11-24T20:00:00"}]
        self.assertTrue(lines_of(solar(flux=flux))[1].startswith("SFI 2  "))

    def test_a_index(self):
        base = [kp3("2026-11-24T21:00:00", 1.33, 5)]
        for value, text in ((15, "A 15"), (0, "A 0"), (999, "A 999")):
            with self.subTest(value=value):
                out = solar(kp_3h=base + [kp3("2026-11-25T00:00:00", 3.0, value)])
                self.assertIn("  %s  Kp " % text, lines_of(out)[1])
        for bad in (1000, -1, "15", 15.0, None, True, float("nan")):
            with self.subTest(bad=bad):
                out = solar(kp_3h=base + [kp3("2026-11-25T00:00:00", 3.0, bad)])
                self.assertIn("  A 5  Kp ", lines_of(out)[1], "falls back to the latest usable entry")

    def test_kp_text_rounding(self):
        cases = ((1.33, "1.3"), (2.67, "2.7"), (2.33, "2.3"), (0.0, "0"), (0, "0"), (3.0, "3"), (9, "9"),
                 (9.0, "9"), (1.25, "1.3"), (0.05, "0.1"), (0.04, "0"), (4.95, "5"), (8.94, "8.9"),
                 (0.67, "0.7"), (5, "5"))
        for value, text in cases:
            with self.subTest(value=value):
                out = solar(kp_1m=[kp1("2026-11-25T04:28:00", value)])
                self.assertIn("  Kp %s (3h max" % text, lines_of(out)[1])

    def test_kp_unusable_values(self):
        for bad in (-0.1, 9.01, 10, "3", None, True, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                out = solar(kp_1m=[kp1("2026-11-25T04:28:00", bad)])
                self.assertIn("  Kp n/a (3h max 3)", lines_of(out)[1])
        out = solar(kp_1m=[kp1("2026-11-25T04:20:00", 2.0), kp1("2026-11-25T04:28:00", "bad")])
        self.assertIn("  Kp 2 (3h max", lines_of(out)[1])

    def test_three_hour_max_window_boundaries(self):
        entries = [
            kp3("2026-11-24T04:32:59", 8.0),   # 24 h and 1 s before now: outside
            kp3("2026-11-24T04:33:00", 6.0),   # exactly 24 h before: inside
            kp3("2026-11-25T04:33:00", 5.0),   # exactly now: inside
            kp3("2026-11-25T04:33:01", 7.0),   # in the future: outside
        ]
        out = solar(kp_3h=entries)
        self.assertIn("(3h max 6)", lines_of(out)[1])
        out = solar(kp_3h=[entries[0], entries[3]])
        self.assertNotIn("3h max", out)
        self.assertEqual(lines_of(out)[1], "SFI 88  A 4  Kp 0")

    def test_three_hour_max_shows_a_decimal_when_needed(self):
        out = solar(kp_3h=[kp3("2026-11-25T00:00:00", 4.33)])
        self.assertEqual(lines_of(out)[1], "SFI 88  A 4  Kp 0 (3h max 4.3)")

    def test_a_and_max_use_different_entries_when_needed(self):
        entries = [kp3("2026-11-25T00:00:00", "bad", 20), kp3("2026-11-24T21:00:00", 2.0, "bad")]
        self.assertEqual(lines_of(solar(kp_3h=entries))[1], "SFI 88  A 20  Kp 0 (3h max 2)")

    def test_xray_uses_latest_in_the_long_band_only(self):
        entries = [xr("2026-11-25T04:10:00Z", 1.25e-7), xr("2026-11-25T04:20:00Z", 5.75e-5),
                   xr("2026-11-25T04:30:00Z", 9e-4, energy="0.05-0.4nm"),
                   xr("2026-11-25T04:31:00Z", 2.85e-3, energy="other"),
                   {"time_tag": "2026-11-25T04:32:00Z", "flux": 1e-3}]
        self.assertTrue(lines_of(solar(xray=entries))[2].startswith("X-ray M5.7  "))
        self.assertTrue(lines_of(solar(xray=entries[:1]))[2].startswith("X-ray B1.2  "))
        self.assertTrue(lines_of(solar(xray=entries[2:]))[2].startswith("X-ray n/a  "))

    def test_xray_skips_unusable_flux(self):
        good = xr("2026-11-25T04:10:00Z", 3e-6)
        for bad in (0, 0.0, -1e-7, 1, 2.5, 1e300, float("nan"), float("inf"), None, "1e-6", True):
            with self.subTest(bad=bad):
                entries = [good, xr("2026-11-25T04:20:00Z", bad)]
                self.assertTrue(lines_of(solar(xray=entries))[2].startswith("X-ray C3.0  "))

    def test_xray_uses_flux_not_observed_flux(self):
        e = xr("2026-11-25T04:20:00Z", 2.5e-7)
        e["observed_flux"] = 2.0e-6
        self.assertTrue(lines_of(solar(xray=[e]))[2].startswith("X-ray B2.5  "))

    def test_xray_big_list_is_quick(self):
        entries = [xr("2026-11-%02dT%02d:%02d:00Z" % (1 + i // 1440 % 28, i // 60 % 24, i % 60), 1e-7 + i * 1e-12)
                   for i in range(100_000)]
        t0 = time.perf_counter()
        out = solar(xray=entries)
        self.assertLess(time.perf_counter() - t0, 5)
        self.assertReply(out)


class SolarScalesTests(ReplyCase):
    def storms(self, now_obj):
        out = self.assertReply(solar(scales={"0": now_obj}))
        return lines_of(out)[2].split("Storms now: ")[1]

    def test_levels(self):
        def sc(r, s, g):
            return {"R": {"Scale": r}, "S": {"Scale": s}, "G": {"Scale": g}}
        self.assertEqual(self.storms(sc("0", "0", "0")), "R0 S0 G0")
        self.assertEqual(self.storms(sc("3", "5", "1")), "R3 S5 G1")
        self.assertEqual(self.storms(sc("5", "4", "2")), "R5 S4 G2")

    def test_unusable_scale_values_show_a_question_mark(self):
        for bad in (None, "", "6", "10", "-1", "x", " 1", "1 ", 3, 0, True, ["3"], {"a": 1}, "٣"):
            with self.subTest(bad=bad):
                self.assertEqual(self.storms({"R": {"Scale": bad}, "S": {"Scale": "1"}, "G": {"Scale": "2"}}),
                                 "R? S1 G2")

    def test_missing_letters(self):
        self.assertEqual(self.storms({"G": {"Scale": "1"}}), "R? S? G1")
        self.assertEqual(self.storms({}), "R? S? G?")
        self.assertEqual(self.storms({"R": None, "S": 5, "G": []}), "R? S? G?")
        self.assertEqual(self.storms({"R": {"Text": "none"}, "S": {"Scale": "0"}, "G": {"Scale": "0"}}), "R? S0 G0")

    def test_without_the_current_observation_it_is_n_a(self):
        fixture = load("swpc", "noaa-scales.json")
        for scales in ({"1": fixture["1"], "2": fixture["2"]}, {"0": None}, {"0": []}, {"0": "R0 S0 G0"},
                       {"-1": fixture["-1"]}):
            with self.subTest(scales=scales):
                self.assertEqual(lines_of(solar(scales=scales))[2], "X-ray B2.1  Storms now: n/a")

    def test_forecast_days_are_not_used(self):
        s = load("swpc", "noaa-scales.json")
        self.assertEqual(s["3"]["G"]["Scale"], "1")
        self.assertIn("Storms now: R0 S0 G0", solar(scales=s))


FORECAST_HEAD = ("The greatest expected 3 hr Kp for Nov 25-Nov 27 2026 is 5.00 (NOAA Scale\nG1).\n\n"
                 "NOAA Kp index breakdown Nov 25-Nov 27 2026\n\n"
                 "             Nov 25       Nov 26       Nov 27\n")


def rows(*triples):
    out = []
    for i, cells in enumerate(triples):
        out.append("%02d-%02dUT       %s\n" % (i * 3, (i * 3 + 3) % 24, "         ".join(cells)))
    return "".join(out)


class SolarForecastTests(ReplyCase):
    def fc(self, text):
        return lines_of(self.assertReply(solar(forecast_3day=text)))[3]

    def test_quiet_forecast_has_no_g_level(self):
        text = FORECAST_HEAD + rows(("1.67", "2.33", "4.33"), ("1.67", "2.00", "3.00"))
        self.assertEqual(self.fc(text), "Forecast: Kp up to 4.3 on Nov 27")

    def test_other_g_levels_and_whole_numbers(self):
        text = FORECAST_HEAD + rows(("1.67", "6.33 (G2)", "4.33"), ("1.67", "2.00", "3.00"))
        self.assertEqual(self.fc(text), "Forecast: Kp up to 6.3 (G2) on Nov 26")
        text = FORECAST_HEAD + rows(("7.00 (G3)", "2.33", "4.33"))
        self.assertEqual(self.fc(text), "Forecast: Kp up to 7 (G3) on Nov 25")

    def test_ties_go_to_the_earliest_column_then_row(self):
        text = FORECAST_HEAD + rows(("1.00", "4.00", "2.00"), ("1.00", "1.00", "4.00"), ("4.00", "1.00", "1.00"))
        self.assertEqual(self.fc(text), "Forecast: Kp up to 4 on Nov 25")
        text = FORECAST_HEAD + rows(("1.00", "1.00", "1.00"), ("1.00", "4.00", "2.00"), ("1.00", "4.00 (G1)", "1.00"))
        self.assertEqual(self.fc(text), "Forecast: Kp up to 4 on Nov 26")

    def test_trailing_spaces_and_indent_on_marker_line(self):
        text = "  NOAA Kp index breakdown Nov 25-Nov 27 2026   \n\n  Nov 25  Nov 26  Nov 27  \n" \
               + rows(("1.00", "2.00", "3.00 "))
        self.assertEqual(self.fc(text), "Forecast: Kp up to 3 on Nov 27")

    def test_date_header_forms(self):
        text = "NOAA Kp index breakdown\n\n  Dec 1   Dec 2   Dec 10\n" + rows(("1.00", "2.00", "3.00"))
        self.assertEqual(self.fc(text), "Forecast: Kp up to 3 on Dec 10")

    def test_rows_after_a_blank_line_are_not_read(self):
        text = FORECAST_HEAD + rows(("1.00", "2.00", "3.00")) + "\n" + "00-03UT       9.00 (G5)   9.00   9.00\n"
        self.assertEqual(self.fc(text), "Forecast: Kp up to 3 on Nov 27")

    def test_rows_that_do_not_fit_are_ignored(self):
        text = (FORECAST_HEAD + "00-03UT       1.00         2.00\n"            # two cells
                + "03-06UT       1.00         2.00         3.00         4.00\n"  # four cells
                + "06-09UT       1.00         2.00         9.50\n"               # Kp over 9
                + "09-12UT       1.00         2.00         x.xx\n"               # not a number
                + "12-15UT       1.00         2.50         1.00\n")
        self.assertEqual(self.fc(text), "Forecast: Kp up to 2.5 on Nov 26")

    def test_no_usable_table(self):
        for text in ("", "nothing here", "NOAA Kp index breakdown Nov 25-Nov 27 2026\n",
                     FORECAST_HEAD,  # header but no rows
                     "NOAA Kp index breakdown\n\n  Nov 25  Nov 26\n" + rows(("1.00", "2.00", "3.00")),  # two dates
                     "NOAA Kp index breakdown\n\n  Nov 25  Nov 26  Nov 27  Nov 28\n" + rows(("1.00", "2.00", "3.00")),
                     "NOAA Kp index breakdown\n\n  Nov 25  Nov 26  extra  Nov 27\n" + rows(("1.00", "2.00", "3.00")),
                     "NOAA Kp index breakdown\n\n" + rows(("1.00", "2.00", "3.00")),  # no date header
                     rows(("1.00", "2.00", "3.00"))):
            with self.subTest(text=text[:40]):
                self.assertEqual(self.fc(text), "Forecast: n/a")

    def test_first_marker_line_wins(self):
        text = (FORECAST_HEAD + rows(("1.00", "2.00", "3.00"))
                + "\nNOAA Kp index breakdown again\n\n  Nov 28  Nov 29  Nov 30\n" + rows(("9.00", "9.00", "9.00")))
        self.assertEqual(self.fc(text), "Forecast: Kp up to 3 on Nov 27")

    def test_hostile_text(self):
        for h in HOSTILE:
            with self.subTest(h=h[:20]):
                self.assertEqual(self.fc(h + "\n" + h), "Forecast: n/a")
        self.assertEqual(self.fc("NOAA Kp index breakdown " + "x" * 1_000_000), "Forecast: n/a")
        big = FORECAST_HEAD + rows(("1.00", "2.00", "3.00")) * 50_000
        t0 = time.perf_counter()
        self.assertEqual(self.fc(big), "Forecast: Kp up to 3 on Nov 27")
        self.assertLess(time.perf_counter() - t0, 5)


class SolarHostileTests(ReplyCase):
    def test_hostile_strings_in_every_slot(self):
        for h in HOSTILE:
            with self.subTest(h=h[:20]):
                junk = [{"time_tag": h, "flux": h, "Kp": h, "a_running": h, "estimated_kp": h, "energy": h}]
                self.assertReply(solar(flux=junk, kp_3h=junk, kp_1m=junk, xray=junk,
                                       scales={"0": {"R": {"Scale": h}, "S": {"Scale": h}, "G": {"Scale": h}}},
                                       forecast_3day=h))

    def test_source_line_is_fixed(self):
        self.assertTrue(solar().endswith("\nSource: NOAA SWPC\n"))


# ------------------------------------------------------------- xray_class


class XrayClassTests(unittest.TestCase):
    CASES = [
        (1.25e-7, "B1.2"),             # the example in the spec: truncated, not rounded
        (1.2e-7, "B1.2"), (1.99e-7, "B1.9"), (2.14e-7, "B2.1"),
        (1e-7, "B1.0"), (9.99e-8, "A9.9"), (9.999999999e-8, "A9.9"),
        (1e-8, "A1.0"), (5e-9, "A0.5"), (1e-9, "A0.1"), (9.999999717180685e-10, "A0.0"), (1e-12, "A0.0"),
        (9.99e-7, "B9.9"), (9.999e-7, "B9.9"),
        (1e-6, "C1.0"), (3e-6, "C3.0"), (2.5e-6, "C2.5"), (9.99e-6, "C9.9"),
        (1e-5, "M1.0"), (5e-5, "M5.0"), (5.75e-5, "M5.7"), (9.99e-5, "M9.9"),
        (1e-4, "X1.0"), (1.0001e-4, "X1.0"), (3.2e-4, "X3.2"), (1e-3, "X10.0"), (2.85e-3, "X28.5"),
        (0.5, "X5000.0"), (0.9, "X9000.0"),
        (6.409514980987296e-07, "B6.4"), (2.1395466376361583e-07, "B2.1"),
        (3e-5, "M3.0"), (4.7e-5, "M4.7"), (1.1e-6, "C1.1"), (1.3e-7, "B1.3"),
    ]

    def test_classes(self):
        for flux, want in self.CASES:
            with self.subTest(flux=flux):
                self.assertEqual(xray_class(flux), want)

    def test_invalid_values(self):
        for bad in (0, 0.0, -0.0, -1e-7, 1, 1.0, 1.5, 1e300, float("nan"), float("inf"), float("-inf"),
                    None, True, False, "1e-7", "B1.0", [1e-7], b"1e-7"):
            with self.subTest(bad=bad):
                with self.assertRaises(WxFormatError):
                    xray_class(bad)

    def test_decimal_boundaries_do_not_wobble(self):
        # 3e-6 / 1e-6 and similar are not exact in binary floating point
        for n in range(1, 10):
            with self.subTest(n=n):
                self.assertEqual(xray_class(float("%de-8" % n)), "A%d.0" % n)
                self.assertEqual(xray_class(float("%de-7" % n)), "B%d.0" % n)
                self.assertEqual(xray_class(float("%de-6" % n)), "C%d.0" % n)
                self.assertEqual(xray_class(float("%de-5" % n)), "M%d.0" % n)
                self.assertEqual(xray_class(float("%de-4" % n)), "X%d.0" % n)
                self.assertEqual(xray_class(float("%de-3" % n)), "X%d0.0" % n)

    def test_every_tenth_of_every_class(self):
        for k in range(10, 100):
            want = "%d.%d" % (k // 10, k % 10)
            with self.subTest(k=k):
                self.assertEqual(xray_class(float("%de-9" % k)), "A" + want)
                self.assertEqual(xray_class(float("%de-8" % k)), "B" + want)
                self.assertEqual(xray_class(float("%de-7" % k)), "C" + want)
                self.assertEqual(xray_class(float("%de-6" % k)), "M" + want)
                self.assertEqual(xray_class(float("%de-5" % k)), "X" + want)


# --------------------------------------------------------- format_nws_error

NOT_FOUND = {
    "correlationId": "a31d5b6",
    "parameterErrors": [{"parameter": "path.zoneId", "message": "Does not match the regex pattern"}],
    "title": "Not Found",
    "type": "https://api.weather.gov/problems/NotFound",
    "status": 404,
    "detail": "Not Found",
    "instance": "https://api.weather.gov/requests/a31d5b6",
}


class NwsErrorTests(ReplyCase):
    def err(self, problem, service="WX", place="London", **kw):
        return format_nws_error(service, problem, place=place, node=NODE, **kw)

    def test_invalid_point_from_the_fixture(self):
        problem = load("nws", "problem_invalid_point.json")
        self.assertEqual(self.err(problem), "WX London: NWS covers the US only.\n"
                                            "Try a place in the US or its territories.\n")
        self.assertEqual(self.err(problem, "ALERTS", "51.5,-0.12"),
                         "ALERTS 51.5,-0.12: NWS covers the US only.\n"
                         "Try a place in the US or its territories.\n")

    def test_invalid_point_wins_whatever_the_status(self):
        self.assertIn("covers the US only", self.err({"type": "https://api.weather.gov/problems/InvalidPoint"}))
        self.assertIn("covers the US only", self.err({"type": "https://api.weather.gov/problems/InvalidPoint",
                                                      "status": 500}))

    def test_not_found(self):
        self.assertEqual(self.err(NOT_FOUND, place="MEZ999"), "WX MEZ999: NWS has no data for that place.\n")
        self.assertEqual(self.err({"status": 404}), "WX London: NWS has no data for that place.\n")
        self.assertEqual(self.err({"type": "https://api.weather.gov/problems/NotFound"}, "ALERTS"),
                         "ALERTS London: NWS has no data for that place.\n")

    def test_unavailable(self):
        want = "WX London: NWS unavailable, try later.\n"
        self.assertEqual(self.err(None), want)
        self.assertEqual(self.err({"status": 500}), want)
        self.assertEqual(self.err({"type": "https://api.weather.gov/problems/ServiceUnavailable",
                                   "title": "Service Unavailable", "status": 503}), want)
        self.assertEqual(self.err({"status": 599, "type": "x"}), want)
        self.assertEqual(self.err(None, "ALERTS"), "ALERTS London: NWS unavailable, try later.\n")

    def test_other_problems(self):
        self.assertEqual(self.err({"status": 400, "type": "https://api.weather.gov/problems/BadRequest"}),
                         "WX London: NWS refused the request (400).\n")
        self.assertEqual(self.err({"status": 429}), "WX London: NWS refused the request (429).\n")
        self.assertEqual(self.err({"status": 403}, "ALERTS"), "ALERTS London: NWS refused the request (403).\n")

    def test_unreadable_problems(self):
        for bad in ({}, {"status": "404"}, {"status": None}, {"status": True}, {"status": 404.0 + 0.5},
                    {"type": 5}, {"type": None, "status": []}, {"title": "x"}):
            with self.subTest(bad=bad):
                self.assertEqual(self.err(bad), "WX London: NWS could not answer.\n")

    def test_title_and_detail_are_not_echoed(self):
        out = self.err({"status": 400, "title": "SECRET-TITLE", "detail": "SECRET-DETAIL\nmore"})
        self.assertNotIn("SECRET", out)

    def test_unknown_fields_are_ignored(self):
        self.assertEqual(self.err(sprinkle(NOT_FOUND)), self.err(NOT_FOUND))

    def test_problem_is_not_changed(self):
        before = copy.deepcopy(NOT_FOUND)
        p = copy.deepcopy(NOT_FOUND)
        self.err(p)
        self.assertEqual(p, before)

    def test_bad_arguments(self):
        for bad in ("wx", "alerts", "SOLAR", "", None, 5):
            with self.subTest(service=bad):
                with self.assertRaises(WxFormatError):
                    self.err(None, service=bad)
        for bad in ([], "text", 5, True, b"{}"):
            with self.subTest(problem=bad):
                with self.assertRaises(WxFormatError):
                    self.err(bad)
        for bad in ("", None, 5, "\x00"):
            with self.subTest(place=bad):
                with self.assertRaises(WxFormatError):
                    self.err(None, place=bad)
                with self.assertRaises(WxFormatError):
                    format_nws_error("WX", None, place="x", node=bad)

    def test_long_and_hostile_places(self):
        out = self.assertReply(self.err(load("nws", "problem_invalid_point.json"), place="L" * 100))
        self.assertEqual(out.split("\n")[0], "WX " + "L" * 38 + "...: NWS covers the US only.")
        self.assertEqual(len(out.split("\n")[0]), 69)
        for h in HOSTILE:
            with self.subTest(h=h[:20]):
                for problem in (None, {"status": 404}, {"status": 400}, {}, load("nws", "problem_invalid_point.json")):
                    if clean(h):
                        self.assertReply(self.err(problem, place=h))
                    else:
                        with self.assertRaises(WxFormatError):
                            self.err(problem, place=h)

    def test_error_replies_have_no_attribution(self):
        for problem in (None, {"status": 404}, {"status": 400}, {}):
            self.assertNotIn("Source", self.err(problem))


class NwsErrorCachedTests(ReplyCase):
    def cached(self):
        return format_wx(points(), forecast(), place=PLACE, node=NODE, now=NOW)

    def call(self, problem=None, **kw):
        args = dict(cached=self.cached(), cached_at=NOW, now=NOW + timedelta(minutes=55))
        args.update(kw)
        return format_nws_error("WX", problem, place=PLACE, node=NODE, **args)

    def test_cached_reply_follows_with_its_age(self):
        want = ("WX Portland Head Light: NWS unavailable, try later.\n"
                "Older reply follows, age 55 min.\n"
                "\n" + WX_SHORT)
        self.assertEqual(self.call(None), want)
        self.assertEqual(self.call({"status": 503}), want)
        self.assertReply(self.call(None))

    def test_age_uses_the_shared_steps(self):
        out = self.call(None, now=NOW + timedelta(hours=5, minutes=3))
        self.assertIn("\nOlder reply follows, age 5 h.\n", out)
        out = self.call(None, now=NOW - timedelta(minutes=10))
        self.assertIn("\nOlder reply follows, age 0 min.\n", out)

    def test_cached_is_for_the_unavailable_row_only(self):
        junk = "not a valid reply ☃"
        self.assertEqual(self.call({"status": 404}, cached=junk), "WX Portland Head Light: NWS has no data for that place.\n")
        self.assertIn("covers the US only", self.call(load("nws", "problem_invalid_point.json"), cached=junk))
        self.assertEqual(self.call({"status": 400}, cached=junk),
                         "WX Portland Head Light: NWS refused the request (400).\n")

    def test_cached_needs_its_times(self):
        for kw in ({"cached_at": None}, {"now": None}, {"cached_at": datetime(2026, 11, 25, 13, 45)},
                   {"now": datetime(2026, 11, 25, 14, 40)}):
            with self.subTest(kw=kw):
                with self.assertRaises(WxFormatError):
                    self.call(None, **kw)

    def test_cached_must_be_a_valid_reply(self):
        good = self.cached()
        for bad in ("", "no newline", good + "\n", "\n" + good, good.replace("Source", "Söurce"),
                    "x" * 70 + "\n", "a \n", "tab\there\n", "cr\r\n", 5, b"abc\n", ["a\n"]):
            with self.subTest(bad=bad if not isinstance(bad, str) else bad[:20]):
                with self.assertRaises(WxFormatError):
                    self.call(None, cached=bad)

    def test_none_cached_is_the_plain_message(self):
        out = format_nws_error("WX", None, place=PLACE, node=NODE, cached=None, cached_at=NOW, now=NOW)
        self.assertEqual(out, "WX Portland Head Light: NWS unavailable, try later.\n")


if __name__ == "__main__":
    unittest.main()
