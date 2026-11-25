"""Contract tests for federated_cms.geo (docs/GEO.md).

Expected values come from sources other than the module under test. Each
group says where its values came from (2026-11-25):

IARU    IARU Region 1 VHF Handbook 10.03, section 20 (locator definition and
        its test table), https://www.iaru-r1.org/wp-content/uploads/2026/02/VHF_Handbook_V10_03_final.pdf
ARRL    https://www.arrl.org/locate/gridinfo.html and the AC5JW geotable
        for W1AW, https://qsl.net/ac5jw/geo/arrl-w1aw.html
MT      Movable Type Scripts reference implementation (npm package
        "geodesy" 2.4.0, LatLonSpherical, radius 6371008.8 m), run in Node.
WGS84   GeographicLib geodesic inverse on the WGS 84 ellipsoid (an
        independent model; used as a second reference within 0.5 percent).
EW      Ed Williams, Aviation Formulary 1.47, https://edwilliams.org/avform147.htm
NOAA    NOAA's published calculator code, https://gml.noaa.gov/grad/solcalc/main.js,
        run unmodified in Node (zenith 90.833; 96 for civil twilight).
SSORG   https://api.sunrise-sunset.org/v2 (checked by its authors against the
        JPL ephemerides), queried for the place's local calendar day and
        converted to UTC.
HAND    Arithmetic from the definition (cell sizes), cross-checked with exact
        fractions in a throwaway script outside the repository.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import math
import random
import socket
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

import federated_cms.geo as geo
from federated_cms.geo import (
    EARTH_RADIUS_KM,
    KM_PER_MILE,
    Box,
    Course,
    GeoError,
    Point,
    SunTimes,
    great_circle,
    locator_box,
    locator_center,
    normalize_locator,
    sun_times,
    to_locator,
)

UTC = timezone.utc
EPS = 1e-9  # degrees; box edges and centres


def approx_point(p: Point, lat: float, lon: float, tol: float = EPS) -> None:
    assert p.lat == pytest.approx(lat, abs=tol)
    assert p.lon == pytest.approx(lon, abs=tol)


def approx_box(b: Box, south: float, west: float, north: float, east: float,
               tol: float = EPS) -> None:
    assert b.south == pytest.approx(south, abs=tol)
    assert b.west == pytest.approx(west, abs=tol)
    assert b.north == pytest.approx(north, abs=tol)
    assert b.east == pytest.approx(east, abs=tol)


# ------------------------------------------------------------------ module


def test_public_names_and_error_class():
    assert issubclass(GeoError, ValueError)
    assert set(geo.__all__) == {
        "GeoError", "Point", "Box", "Course", "SunTimes", "normalize_locator",
        "locator_box", "locator_center", "to_locator", "great_circle",
        "sun_times", "EARTH_RADIUS_KM", "KM_PER_MILE",
    }
    assert EARTH_RADIUS_KM == 6371.0088
    assert KM_PER_MILE == 1.609344


def test_dataclasses_are_frozen():
    for obj in (
        Point(1.0, 2.0),
        Box(0.0, 0.0, 1.0, 1.0),
        Course(1.0, 0.6, 90.0),
        sun_times(Point(0.0, 0.0), date(2026, 3, 20)),
    ):
        assert dataclasses.is_dataclass(obj)
        field = dataclasses.fields(obj)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, None)


def test_dataclass_field_names():
    assert [f.name for f in dataclasses.fields(Point)] == ["lat", "lon"]
    assert [f.name for f in dataclasses.fields(Box)] == ["south", "west", "north", "east"]
    assert [f.name for f in dataclasses.fields(Course)] == [
        "distance_km", "distance_miles", "bearing"]
    assert [f.name for f in dataclasses.fields(SunTimes)] == [
        "sunrise", "sunset", "civil_dawn", "civil_dusk", "solar_noon",
        "status", "civil_status"]


def test_source_is_standard_library_only_and_independent():
    spec = importlib.util.find_spec("federated_cms.geo")
    tree = ast.parse(open(spec.origin, encoding="utf-8").read())
    banned = {"socket", "ssl", "urllib", "http", "subprocess", "ftplib",
              "smtplib", "requests", "os", "shutil", "pathlib", "random",
              "time"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import"
            names = [node.module or ""]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            assert top != "federated_cms", name
            assert top in sys.stdlib_module_names, name
            assert top not in banned, name


def test_works_with_no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert to_locator(41.714766666, -72.727183333) == "FN31pr"
    assert great_circle("FN31pr", "IO91wm").distance_km > 5000
    assert sun_times("FN31pr", date(2026, 6, 21)).status == "normal"


# ---------------------------------------------------------------- locators
# Canonical form: field upper case, digits, subsquare lower case.

# (input, canonical)
CANONICAL = [
    ("FN", "FN"), ("fn", "FN"), ("Fn", "FN"),
    ("FN31", "FN31"), ("fn31", "FN31"),
    ("FN31pr", "FN31pr"), ("FN31PR", "FN31pr"), ("fn31pr", "FN31pr"),
    ("fN31Pr", "FN31pr"),
    ("FN31pr21", "FN31pr21"), ("fn31PR21", "FN31pr21"),
    ("AA00AA00", "AA00aa00"), ("RR99XX99", "RR99xx99"),
    ("JN18du", "JN18du"), ("io90IV58", "IO90iv58"),
]


@pytest.mark.parametrize("given,expected", CANONICAL)
def test_normalize_locator(given, expected):
    assert normalize_locator(given) == expected
    assert normalize_locator(expected) == expected  # idempotent


# Locators that must be refused. Each is a str unless noted.
BAD_LOCATORS = [
    "", "F", "FN3", "FN31p", "FN31pr2", "FN31pr212",  # odd lengths
    "FN31pr2121", "JN18DU55IX", "FN31pr212121",       # 10 and 12 characters: not in this version
    "SN", "FS", "ZZ", "SN31",                          # field letters only A to R
    "F1", "1N", "NN3", "FN3a", "FNa1", "FN-1", "FN3.",  # wrong class in a position
    "FN31ys", "FN31py", "FN31yy", "FN31pZ",            # subsquare letters only A to X
    "FN31p1", "FN3111",                                # digit where a letter belongs
    "FN31prA1", "FN31pr2x", "FN31pr2.",                # extended pair is two digits
    " FN31", "FN31 ", "FN 31", "FN31pr ", "\tFN31", "FN31\n", "FN31pr21\n",
    "FN31\x00", "\x00\x00", "FN\x00\x00",
    # non-ASCII lookalikes and digits: refused, not mapped
    "\uff26\uff2e",                 # fullwidth FN
    "FN\uff13\uff11",               # fullwidth 31
    "FN\u0663\u0661",               # Arabic-Indic 31 (str.isdigit and int() accept these)
    "FN31p\u212a",                  # Kelvin sign: lower() is ASCII "k", a valid subsquare letter
    "FN31p\u0130",                  # dotted capital I
    "FN31\u017f\u017f",             # long s: upper() is "S"
    "F\u00cd", "\u00c9\u00c9", "FN31p\u00e9",
    "FN31p\u2160",                  # roman numeral one
    "\U0001f4e1\U0001f4e1",         # emoji
    "\ud800\ud800",                 # lone surrogates
    "FN" + "\u200b" + "31",         # zero-width space
    "FN31pr21" + "\u0301",          # combining accent
]


@pytest.mark.parametrize("text", BAD_LOCATORS)
def test_bad_locator_text(text):
    for fn in (normalize_locator, locator_box, locator_center):
        with pytest.raises(GeoError):
            fn(text)


@pytest.mark.parametrize("value", [None, 31, 3.1, b"FN31", bytearray(b"FN31"),
                                   ["FN31"], ("FN", "31"), {"FN31"}, object(),
                                   True, Point(1.0, 1.0)])
def test_locator_must_be_str(value):
    for fn in (normalize_locator, locator_box, locator_center):
        with pytest.raises(GeoError):
            fn(value)


def test_huge_string_refused_quickly():
    start = time.perf_counter()
    for fn in (normalize_locator, locator_box, locator_center):
        with pytest.raises(GeoError):
            fn("A" * 10_000_000)
    assert time.perf_counter() - start < 1.0


# Boxes and centres: (locator, south, west, north, east, centre lat, centre lon)

BOXES = [
    # HAND: field = 20 x 10 degrees from -180, -90. Field FN is the 6th column
    # and 14th row: lon -80..-60, lat 40..50.
    ("FN", 40.0, -80.0, 50.0, -60.0, 45.0, -70.0),
    ("JJ", 0.0, 0.0, 10.0, 20.0, 5.0, 10.0),
    # IARU text: "the squaroid whose lower left corner lies at 2 deg West and
    # 50 deg North becomes 'IO90'".
    ("IO90", 50.0, -2.0, 51.0, 0.0, 50.5, -1.0),
    ("FN43", 43.0, -72.0, 44.0, -70.0, 43.5, -71.0),   # HAND
    ("BL11", 21.0, -158.0, 22.0, -156.0, 21.5, -157.0),  # HAND; BL11bh16 is the Wikipedia example
    # IARU text: "'IO90IV' ... lower left corner sits at 1 degree 20 minutes
    # West and 50 degrees 52.5 minutes North" (-1.3333.. and 50.875); a
    # subsquare is 5' x 2.5'.
    ("IO90iv", 50.875, -1.0 - 1.0 / 3.0, 50.875 + 2.5 / 60.0, -1.25,
     50.875 + 1.25 / 60.0, -1.25 - 2.5 / 60.0),
    # IARU table, JN18DU (Eiffel Tower): centroid 48.8541666667 N, 2.2916666667 E;
    # squaroid lon 2.25 to 2.3333333333, lat 48.8333333333 to 48.875.
    ("JN18du", 48.8333333333, 2.25, 48.875, 2.3333333333, 48.8541666667, 2.2916666667),
    # HAND: IO90IV58, 0.5' x 0.25' cell inside IO90IV: west -1.2916666667,
    # south 50.9083333333.
    ("IO90iv58", 50.9083333333, -1.2916666667, 50.9125, -1.2833333333,
     50.9104166667, -1.2875),
    # IARU table, JN18DU55IX is inside JN18DU55 (west edge 2.2944444444 is
    # 8/24 of a cell east of 2.2916666667; north edge 48.8583333333 is the top
    # of the cell). HAND for the eight-character cell.
    ("JN18du55", 48.8541666667, 2.2916666667, 48.8583333333, 2.3, 48.85625, 2.2958333333),
    # ARRL: W1AW is in FN31pr. HAND for the cell; W1AW (41.7148, -72.7272) lies inside.
    ("FN31pr", 41.7083333333, -72.75, 41.75, -72.6666666667, 41.7291666667, -72.7083333333),
    # AC5JW geotable: every W1AW point is FN31PR21. HAND for the cell.
    ("FN31pr21", 41.7125, -72.7333333333, 41.7166666667, -72.725, 41.7145833333, -72.7291666667),
    # HAND: Wikipedia's example format BL11bh16 (near Honolulu).
    ("BL11bh16", 21.3166666667, -157.9083333333, 21.3208333333, -157.9,
     21.31875, -157.9041666667),
    # HAND: Portland, Maine (43.6591, -70.2568) is inside FN43up.
    ("FN43up", 43.625, -70.3333333333, 43.6666666667, -70.25, 43.6458333333, -70.2916666667),
    # IARU table, other rows (extremes), cut to the length shown.
    ("AA00aa", -90.0, -180.0, -90.0 + 2.5 / 60.0, -180.0 + 5.0 / 60.0,
     -90.0 + 1.25 / 60.0, -180.0 + 2.5 / 60.0),
    ("AR09ax", 90.0 - 2.5 / 60.0, -180.0, 90.0, -180.0 + 5.0 / 60.0,
     90.0 - 1.25 / 60.0, -180.0 + 2.5 / 60.0),
    ("RR99xx99", 90.0 - 0.25 / 60.0, 180.0 - 0.5 / 60.0, 90.0, 180.0,
     90.0 - 0.125 / 60.0, 180.0 - 0.25 / 60.0),
    ("AA00aa00", -90.0, -180.0, -90.0 + 0.25 / 60.0, -180.0 + 0.5 / 60.0,
     -90.0 + 0.125 / 60.0, -180.0 + 0.25 / 60.0),
    ("QF56od", -33.875, 151.1666666667, -33.8333333333, 151.25, -33.8541666667, 151.2083333333),  # HAND; Sydney
]


@pytest.mark.parametrize("loc,s,w,n,e,clat,clon", BOXES)
def test_box_and_centre(loc, s, w, n, e, clat, clon):
    tol = 2e-9
    for form in (loc, loc.upper(), loc.lower()):
        approx_box(locator_box(form), s, w, n, e, tol)
        approx_point(locator_center(form), clat, clon, tol)


def test_box_and_centre_types():
    box = locator_box("FN31pr")
    assert isinstance(box, Box)
    assert all(isinstance(v, float) for v in dataclasses.astuple(box))
    centre = locator_center("FN31pr")
    assert isinstance(centre, Point)
    assert isinstance(centre.lat, float) and isinstance(centre.lon, float)


@pytest.mark.parametrize("loc,width,height", [
    ("FN", 20.0, 10.0), ("FN31", 2.0, 1.0),
    ("FN31pr", 5.0 / 60.0, 2.5 / 60.0), ("FN31pr21", 0.5 / 60.0, 0.25 / 60.0),
])
def test_cell_sizes(loc, width, height):
    b = locator_box(loc)
    assert b.east - b.west == pytest.approx(width, abs=1e-9)
    assert b.north - b.south == pytest.approx(height, abs=1e-9)


def test_extremes_are_exact():
    top = locator_box("RR99xx99")
    assert top.north == 90.0 and top.east == 180.0
    bottom = locator_box("AA00aa00")
    assert bottom.south == -90.0 and bottom.west == -180.0
    assert locator_box("RR").north == 90.0 and locator_box("RR").east == 180.0
    assert locator_box("AA").south == -90.0 and locator_box("AA").west == -180.0


# IARU table (section 20.2.7): name, lat, lon, six-character locator, ten-character
# locator. Cut here to eight characters: the first eight of the ten-character form.
IARU_TABLE = [
    ("south pole", -90.0, 0.0, "AA00aa", "AA00aa00"),
    ("north pole", 90.0, 0.0, "AR09ax", "AR09ax09"),
    ("equator 0E", 0.0, 0.0, "JJ00aa", "JJ00aa00"),
    ("equator 180E", 0.0, 180.0, "AJ00aa", "AJ00aa00"),
    ("Antarctica", -80.0, -160.0, "BB00aa", "BB00aa00"),
    ("Eiffel Tower", 48.858222, 2.2945, "JN18du", "JN18du55"),
]


@pytest.mark.parametrize("name,lat,lon,six,eight", IARU_TABLE)
def test_iaru_table(name, lat, lon, six, eight):
    assert to_locator(lat, lon) == six
    assert to_locator(lat, lon, 6) == six
    assert to_locator(lat, lon, 8) == eight
    assert to_locator(lat, lon, 4) == six[:4]
    assert to_locator(lat, lon, 2) == six[:2]


@pytest.mark.parametrize("lon", [-180.0, -123.4, -0.0, 0.0, 77.0, 179.99, 180.0])
def test_poles_ignore_longitude(lon):
    # IARU: "At the poles, where longitude is undefined, the letter 'A' shall be used."
    assert to_locator(-90.0, lon, 8) == "AA00aa00"
    assert to_locator(90.0, lon, 8) == "AR09ax09"
    assert to_locator(90, lon, 2) == "AR"
    assert to_locator(-90, lon, 4) == "AA00"


def test_longitude_180_is_minus_180():
    for lat in (-89.5, 0.0, 41.7, 89.5):
        assert to_locator(lat, 180.0, 8) == to_locator(lat, -180.0, 8)
    assert to_locator(0.0, -180.0) == "AJ00aa"


def test_top_right_corner_cell():
    assert to_locator(89.99999, 179.99999, 6) == "RR99xx"
    assert to_locator(89.99999, 179.99999, 8) == "RR99xx99"
    assert to_locator(89.99999, 179.99999, 2) == "RR"


def test_edges_belong_to_the_north_east_cell():
    # HAND: the IARU example puts the lower left corner of IO90 at 2 W, 50 N,
    # so that point is IO90 and not a neighbour.
    assert to_locator(50.0, -2.0, 4) == "IO90"
    assert to_locator(50.0, -2.0, 8) == "IO90aa00"
    # Subsquare and extended-square edges that binary floats hold exactly:
    # lon -1.75 = IO90 + 3 subsquares of 5' (0.25 degree), lat 50.25 = 6 of 2.5'.
    assert to_locator(50.25, -1.75, 8) == "IO90dg00"
    just_below = math.nextafter(50.25, 0.0)
    just_left = math.nextafter(-1.75, -2.0)
    assert to_locator(just_below, just_left, 8) == "IO90cf99"
    assert to_locator(just_below, -1.75, 8) == "IO90df09"
    assert to_locator(50.25, just_left, 8) == "IO90cg90"
    # A field edge and a square edge.
    assert to_locator(0.0, 0.0, 2) == "JJ"
    assert to_locator(-0.0, 20.0, 4) == "KJ00"
    assert to_locator(math.nextafter(0.0, -1.0), 0.0, 4) == "JI09"
    assert to_locator(10.0, math.nextafter(20.0, 0.0), 4) == "JK90"
    assert to_locator(10.0, 20.0, 4) == "KK00"


@pytest.mark.parametrize("lat,lon", [
    # ARRL / AC5JW: W1AW-FAC is FN31PR21RN70 (cut to eight characters).
    (41.714766666, -72.727183333),
    # AC5JW: ARRL-E, 41.714716666, -72.728433333, is FN31PR21OM17.
    (41.714716666, -72.728433333),
])
def test_w1aw_points(lat, lon):
    assert to_locator(lat, lon, 2) == "FN"
    assert to_locator(lat, lon, 4) == "FN31"
    assert to_locator(lat, lon, 6) == "FN31pr"
    assert to_locator(lat, lon, 8) == "FN31pr21"


def test_portland_maine():
    # HAND: 43.6591 N, 70.2568 W is in FN43up.
    assert to_locator(43.6591, -70.2568, 4) == "FN43"
    assert to_locator(43.6591, -70.2568, 6) == "FN43up"
    eight = to_locator(43.6591, -70.2568, 8)
    assert eight[:6] == "FN43up"
    b = locator_box(eight)
    assert b.south <= 43.6591 < b.north and b.west <= -70.2568 < b.east


def test_default_length_is_six():
    assert to_locator(41.7, -72.7) == to_locator(41.7, -72.7, 6)
    assert len(to_locator(41.7, -72.7)) == 6


def test_integers_are_accepted():
    assert to_locator(0, 0) == "JJ00aa"
    assert to_locator(45, -70, 4) == "FN55"   # HAND: -70 is the west edge of square 5 in field F
    assert to_locator(45, -70, 4) == to_locator(45.0, -70.0, 4)


def test_round_trip_random_points():
    rng = random.Random(20261125)
    for _ in range(400):
        lat = rng.uniform(-90.0, 90.0)
        lon = rng.uniform(-180.0, 180.0)
        for length in (2, 4, 6, 8):
            loc = to_locator(lat, lon, length)
            assert len(loc) == length and loc == normalize_locator(loc)
            box = locator_box(loc)
            assert box.south - 1e-9 <= lat <= box.north + 1e-9
            assert box.west - 1e-9 <= lon <= box.east + 1e-9
            centre = locator_center(loc)
            assert box.south < centre.lat < box.north
            assert box.west < centre.lon < box.east
            assert to_locator(centre.lat, centre.lon, length) == loc
            # a longer locator refines a shorter one
            assert to_locator(lat, lon, 8)[:length].lower() == loc.lower()


def test_round_trip_every_square_in_a_field():
    # All 100 squares of field JN, and all 576 subsquares of one square.
    for lat_digit in range(10):
        for lon_digit in range(10):
            loc = f"JN{lon_digit}{lat_digit}"
            c = locator_center(loc)
            assert to_locator(c.lat, c.lon, 4) == loc
    seen = set()
    for lon_letter in "abcdefghijklmnopqrstuvwx":
        for lat_letter in "abcdefghijklmnopqrstuvwx":
            loc = f"JN18{lon_letter}{lat_letter}"
            c = locator_center(loc)
            assert to_locator(c.lat, c.lon, 6) == loc
            seen.add(loc)
    assert len(seen) == 576


def test_random_valid_locators_round_trip():
    rng = random.Random(7)
    for _ in range(500):
        length = rng.choice((2, 4, 6, 8))
        chars = [rng.choice("ABCDEFGHIJKLMNOPQR"), rng.choice("ABCDEFGHIJKLMNOPQR")]
        if length >= 4:
            chars += [rng.choice("0123456789"), rng.choice("0123456789")]
        if length >= 6:
            chars += [rng.choice("ABCDEFGHIJKLMNOPQRSTUVWX"), rng.choice("ABCDEFGHIJKLMNOPQRSTUVWX")]
        if length == 8:
            chars += [rng.choice("0123456789"), rng.choice("0123456789")]
        raw = "".join(chars)
        mixed = "".join(c.lower() if rng.random() < 0.5 else c for c in raw)
        canonical = normalize_locator(mixed)
        assert canonical.upper() == raw
        c = locator_center(mixed)
        assert to_locator(c.lat, c.lon, length) == canonical


@pytest.mark.parametrize("length", [0, 1, 3, 5, 7, 9, 10, 12, -2, 2.0, 6.0, "6",
                                    None, True, False, 10**400])
def test_bad_length(length):
    with pytest.raises(GeoError):
        to_locator(41.7, -72.7, length)


# Bad numbers for latitude and longitude.
BAD_NUMBERS = [
    None, "41.7", "", b"1", True, False, [1.0], (1.0,), {}, object(),
    float("nan"), float("inf"), float("-inf"), Decimal("1.5"), Fraction(1, 2),
    complex(1, 0), 10**400, -(10**400), 1e308, -1e308,
]


@pytest.mark.parametrize("bad", BAD_NUMBERS)
def test_bad_latitude_longitude_types(bad):
    with pytest.raises(GeoError):
        to_locator(bad, 0.0)
    with pytest.raises(GeoError):
        to_locator(0.0, bad)
    with pytest.raises(GeoError):
        Point(bad, 0.0)
    with pytest.raises(GeoError):
        Point(0.0, bad)


@pytest.mark.parametrize("lat,lon", [
    (90.0000001, 0.0), (-90.0000001, 0.0), (91, 0), (-91, 0), (1000, 0),
    (0.0, 180.0000001), (0.0, -180.0000001), (0, 181), (0, -181), (0, 360), (0, 3600),
])
def test_out_of_range(lat, lon):
    with pytest.raises(GeoError):
        to_locator(lat, lon)
    with pytest.raises(GeoError):
        Point(lat, lon)


def test_range_limits_are_inclusive():
    for lat in (-90, 90, -90.0, 90.0):
        for lon in (-180, 180, -180.0, 180.0):
            to_locator(lat, lon, 8)
            Point(lat, lon)


def test_point_stores_floats():
    p = Point(5, -7)
    assert p == Point(5.0, -7.0)
    assert isinstance(p.lat, float) and isinstance(p.lon, float)
    assert Point(0.0, 180.0).lon == 180.0


# ---------------------------------------------------------------- distance
# Spherical model, R = 6371.0088 km. Values are MT (R = 6371008.8 m), checked
# against WGS84 to within 0.5 percent. Miles are km / 1.609344.

# (name, point a, point b, km, miles, bearing a->b, WGS84 km)
ROUTES = [
    # EW: LAX 33 deg57'N 118 deg24'W to JFK 40 deg38'N 73 deg47'W is 0.623585 rad = 2144 nm
    # and initial course 1.150035 rad = 66 degrees. MT: 3972.8633 km, 65.8922.
    ("LAX-JFK", Point(33 + 57 / 60, -(118 + 24 / 60)), Point(40 + 38 / 60, -(73 + 47 / 60)),
     3972.8633, 2468.6228, 65.8922, 3981.601),
    ("JFK-LAX", Point(40 + 38 / 60, -(73 + 47 / 60)), Point(33 + 57 / 60, -(118 + 24 / 60)),
     3972.8633, 2468.6228, 273.8582, 3981.601),
    # MT page: 35N 45E to 35N 135E, "start on a heading of 60 deg and end up on a heading of 120 deg".
    ("Baghdad-Osaka", Point(35.0, 45.0), Point(35.0, 135.0), 7871.7800, 4891.2973, 60.1624, 7889.042),
    ("Osaka-Baghdad", Point(35.0, 135.0), Point(35.0, 45.0), 7871.7800, 4891.2973, 299.8376, 7889.042),
    ("Sydney-San Francisco", Point(-33.8688, 151.2093), Point(37.7749, -122.4194),
     11947.6770, 7423.9423, 55.7799, 11934.47),
    ("Paris-Tokyo", Point(48.8566, 2.3522), Point(35.6762, 139.6503),
     9711.7382, 6034.5944, 33.4700, 9735.309),
]


@pytest.mark.parametrize("name,a,b,km,miles,bearing,wgs84", ROUTES, ids=[r[0] for r in ROUTES])
def test_routes_between_points(name, a, b, km, miles, bearing, wgs84):
    c = great_circle(a, b)
    assert isinstance(c, Course)
    assert c.distance_km == pytest.approx(km, abs=0.002)
    assert c.distance_miles == pytest.approx(miles, abs=0.002)
    assert c.distance_miles == pytest.approx(c.distance_km / KM_PER_MILE, rel=1e-12)
    assert c.bearing == pytest.approx(bearing, abs=0.01)
    assert 0.0 <= c.bearing < 360.0
    # second reference: the ellipsoid, within 0.5 percent
    assert c.distance_km == pytest.approx(wgs84, rel=0.005)


def test_ed_williams_published_example():
    # 0.623585 rad on the sphere, and 66 degrees to the nearest degree.
    c = great_circle(Point(33 + 57 / 60, -(118 + 24 / 60)), Point(40 + 38 / 60, -(73 + 47 / 60)))
    assert c.distance_km == pytest.approx(0.623585 * EARTH_RADIUS_KM, rel=2e-6)
    assert c.distance_km == pytest.approx(2144 * 1.852, rel=0.005)
    assert math.radians(c.bearing) == pytest.approx(1.150035, abs=2e-4)
    assert round(c.bearing) == 66


# Locator inputs: distance is between the centres of the cells, at the length given.
LOCATOR_ROUTES = [
    # MT on the centre points (HAND centres); WGS84 second reference.
    ("FN31pr", "IO91wm", 5414.7298, 3364.5571, 52.2157, 5429.61),
    ("IO91wm", "FN31pr", 5414.7298, 3364.5571, 288.5747, 5429.61),
    ("JN18du", "QF56od", 16963.5763, 10540.6777, 68.4841, 16960.587),
    ("FN43", "IO90", 5165.2724, 3209.5515, 55.5545, 5179.905),
]


@pytest.mark.parametrize("a,b,km,miles,bearing,wgs84", LOCATOR_ROUTES)
def test_routes_between_locators(a, b, km, miles, bearing, wgs84):
    c = great_circle(a, b)
    assert c.distance_km == pytest.approx(km, abs=0.002)
    assert c.distance_miles == pytest.approx(miles, abs=0.002)
    assert c.bearing == pytest.approx(bearing, abs=0.01)
    assert c.distance_km == pytest.approx(wgs84, rel=0.005)
    # case does not matter, and a locator equals its centre point
    assert great_circle(a.upper(), b.lower()) == c
    assert great_circle(locator_center(a), locator_center(b)) == c
    assert great_circle(a, locator_center(b)) == c
    assert great_circle(locator_center(a), b) == c


def test_short_locator_uses_its_own_centre():
    assert great_circle("FN43", "IO90") != great_circle("FN43mm", "IO90mm")


def test_arc_length_cases():
    quarter = EARTH_RADIUS_KM * math.pi / 2          # equator to pole, by definition
    assert great_circle(Point(0, 0), Point(90, 0)).distance_km == pytest.approx(quarter, rel=1e-9)
    assert great_circle(Point(0, 0), Point(0, 90)).distance_km == pytest.approx(quarter, rel=1e-9)
    assert great_circle(Point(-90, 0), Point(90, 55)).distance_km == pytest.approx(
        2 * quarter, rel=1e-6)
    # one degree of arc on a meridian or the equator
    one_degree = EARTH_RADIUS_KM * math.pi / 180
    assert great_circle(Point(10, 10), Point(11, 10)).distance_km == pytest.approx(one_degree, rel=1e-9)
    assert great_circle(Point(0, 10), Point(0, 11)).distance_km == pytest.approx(one_degree, rel=1e-9)
    # 10 degrees of latitude: MT 1111.9508 km
    assert great_circle(Point(10, 10), Point(20, 10)).distance_km == pytest.approx(1111.9508, abs=0.001)


def test_across_the_date_line():
    c = great_circle(Point(0, 179), Point(0, -179))   # two degrees east, not 358
    assert c.distance_km == pytest.approx(2 * math.pi / 180 * EARTH_RADIUS_KM, rel=1e-9)
    assert c.distance_km == pytest.approx(222.3902, abs=0.001)  # MT
    assert c.bearing == pytest.approx(90.0, abs=1e-6)
    assert great_circle(Point(0, -179), Point(0, 179)).bearing == pytest.approx(270.0, abs=1e-6)
    assert great_circle(Point(5, 180), Point(5, -180)).distance_km == pytest.approx(0.0, abs=1e-6)


def test_cardinal_bearings():
    assert great_circle(Point(10, 10), Point(20, 10)).bearing == pytest.approx(0.0, abs=1e-9)
    assert great_circle(Point(20, 10), Point(10, 10)).bearing == pytest.approx(180.0, abs=1e-9)
    assert great_circle(Point(0, 0), Point(0, 90)).bearing == pytest.approx(90.0, abs=1e-9)
    assert great_circle(Point(0, 0), Point(0, -90)).bearing == pytest.approx(270.0, abs=1e-9)


def test_same_place_has_no_bearing():
    c = great_circle(Point(41.7, -72.7), Point(41.7, -72.7))
    assert c.distance_km == 0.0 and c.distance_miles == 0.0 and c.bearing is None
    assert great_circle("FN31pr", "fn31pr") == Course(0.0, 0.0, None)
    assert great_circle(Point(0, 180), Point(0, -180)).bearing is None
    assert great_circle(Point(90, 0), Point(90, 120)).bearing is None
    # 0.5 m apart: still "the same place"
    assert great_circle(Point(0, 0), Point(0, 0.5 / (EARTH_RADIUS_KM * 1000) * 180 / math.pi)
                        ).bearing is None
    # 20 m apart: a bearing is given
    far = great_circle(Point(0, 0), Point(0, 20 / (EARTH_RADIUS_KM * 1000) * 180 / math.pi))
    assert far.bearing == pytest.approx(90.0, abs=1e-6)


def test_antipodes_have_no_bearing():
    for a, b in [(Point(0, 0), Point(0, 180)), (Point(45, 10), Point(-45, -170)),
                 (Point(-90, 0), Point(90, 0)), (Point(10, 100), Point(-10, -80))]:
        c = great_circle(a, b)
        assert c.distance_km == pytest.approx(math.pi * EARTH_RADIUS_KM, abs=0.001)
        assert c.distance_km <= math.pi * EARTH_RADIUS_KM + 1e-6
        assert c.bearing is None
        assert math.isfinite(c.distance_km)


def test_pole_bearings():
    portland = Point(43.6591, -70.2568)
    north, south = Point(90, 0), Point(-90, 0)
    assert great_circle(north, portland).bearing == 180.0
    assert great_circle(Point(90, 99), portland).bearing == 180.0
    assert great_circle(south, portland).bearing == 0.0
    assert great_circle(portland, north).bearing == 0.0
    assert great_circle(portland, south).bearing == 180.0
    # distance to a pole: colatitude. MT: 90 - 43.6591 degrees.
    assert great_circle(portland, north).distance_km == pytest.approx(
        math.radians(90 - 43.6591) * EARTH_RADIUS_KM, rel=1e-9)
    assert great_circle(portland, south).distance_km == pytest.approx(
        math.radians(90 + 43.6591) * EARTH_RADIUS_KM, rel=1e-9)


def test_symmetry_and_range_on_random_pairs():
    rng = random.Random(99)
    limit = math.pi * EARTH_RADIUS_KM
    for _ in range(500):
        a = Point(rng.uniform(-90, 90), rng.uniform(-180, 180))
        b = Point(rng.uniform(-90, 90), rng.uniform(-180, 180))
        ab, ba = great_circle(a, b), great_circle(b, a)
        assert ab.distance_km == pytest.approx(ba.distance_km, rel=1e-9, abs=1e-9)
        assert 0.0 <= ab.distance_km <= limit + 1e-9
        assert ab.distance_miles == pytest.approx(ab.distance_km / KM_PER_MILE, rel=1e-12, abs=1e-12)
        if ab.bearing is not None:
            assert 0.0 <= ab.bearing < 360.0
        if ba.bearing is not None:
            assert 0.0 <= ba.bearing < 360.0


def test_triangle_inequality_on_random_triples():
    rng = random.Random(5)
    for _ in range(200):
        pts = [Point(rng.uniform(-90, 90), rng.uniform(-180, 180)) for _ in range(3)]
        d = lambda x, y: great_circle(x, y).distance_km  # noqa: E731
        assert d(pts[0], pts[2]) <= d(pts[0], pts[1]) + d(pts[1], pts[2]) + 1e-6


@pytest.mark.parametrize("bad", [None, 5, 4.5, (41.7, -72.7), [41.7, -72.7], b"FN31",
                                 "", "FN3", "FN31pr212", "FN31 ", "S1", "\uff26\uff2e",
                                 {"lat": 1, "lon": 2}, object()])
def test_great_circle_bad_arguments(bad):
    good = Point(1.0, 1.0)
    with pytest.raises(GeoError):
        great_circle(bad, good)
    with pytest.raises(GeoError):
        great_circle(good, bad)


# --------------------------------------------------------------------- sun


def at(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


TOLERANCE = timedelta(seconds=120)   # NOAA: "accurate to within a minute"


def check_time(actual, expected, label):
    if expected is None:
        assert actual is None, f"{label}: expected no event, got {actual}"
        return
    assert actual is not None, f"{label}: expected {expected}, got none"
    assert actual.tzinfo is not None and actual.utcoffset() == timedelta(0), label
    assert actual.microsecond == 0, label
    assert abs(actual - expected) <= TOLERANCE, f"{label}: {actual} vs {expected}"


PORTLAND = (43.6591, -70.2568)
TROMSO = (69.6492, 18.9553)
LONGYEARBYEN = (78.2232, 15.6267)
SYDNEY = (-33.8688, 151.2093)
SUVA = (-18.1416, 178.4415)
QUITO = (-0.1807, -78.4678)
MCMURDO = (-77.85, 166.67)

# (label, (lat, lon), date, sunrise, sunset, civil dawn, civil dusk, solar noon, status, civil status)
# Times from SSORG (UTC), each within about 20 s of NOAA, except where noted.
# A time on the next or previous UTC date is the event of the place's local day.
SUN_CASES = [
    ("Portland ME equinox", PORTLAND, date(2026, 3, 20),
     at(2026, 3, 20, 10, 44, 7), at(2026, 3, 20, 22, 53, 27),
     at(2026, 3, 20, 10, 15, 32), at(2026, 3, 20, 23, 22, 6),
     at(2026, 3, 20, 16, 48, 24), "normal", "normal"),
    # Sunset is after 00:00 UTC of the next date.
    ("Portland ME June solstice", PORTLAND, date(2026, 6, 21),
     at(2026, 6, 21, 8, 59, 32), at(2026, 6, 22, 0, 26, 15),
     at(2026, 6, 21, 8, 23, 29), at(2026, 6, 22, 1, 2, 17),
     at(2026, 6, 21, 16, 42, 54), "normal", "normal"),
    ("Portland ME December solstice", PORTLAND, date(2026, 12, 21),
     at(2026, 12, 21, 12, 11, 22), at(2026, 12, 21, 21, 7, 2),
     at(2026, 12, 21, 11, 38, 29), at(2026, 12, 21, 21, 39, 55),
     at(2026, 12, 21, 16, 39, 12), "normal", "normal"),
    ("Portland ME leap day", PORTLAND, date(2028, 2, 29),
     at(2028, 2, 29, 11, 18, 17), at(2028, 2, 29, 22, 29, 11),
     at(2028, 2, 29, 10, 49, 30), at(2028, 2, 29, 22, 58, 1),
     at(2028, 2, 29, 16, 53, 23), "normal", "normal"),
    ("Equator 0E March", (0.0, 0.0), date(2026, 3, 20),
     at(2026, 3, 20, 6, 4, 10), at(2026, 3, 20, 18, 10, 41),
     at(2026, 3, 20, 5, 43, 31), at(2026, 3, 20, 18, 31, 21),
     at(2026, 3, 20, 12, 7, 26), "normal", "normal"),
    ("Equator 0E September", (0.0, 0.0), date(2026, 9, 22),
     at(2026, 9, 22, 5, 49, 30), at(2026, 9, 22, 17, 55, 59),
     at(2026, 9, 22, 5, 28, 50), at(2026, 9, 22, 18, 16, 39),
     at(2026, 9, 22, 11, 52, 44), "normal", "normal"),
    ("Quito", QUITO, date(2026, 3, 20),
     at(2026, 3, 20, 11, 17, 59), at(2026, 3, 20, 23, 24, 30),
     at(2026, 3, 20, 10, 57, 19), at(2026, 3, 20, 23, 45, 9),
     at(2026, 3, 20, 17, 21, 14), "normal", "normal"),
    # Date line, west side: everything falls after the UTC date starts, noon on the next date.
    ("Equator 180W", (0.0, -180.0), date(2026, 3, 20),
     at(2026, 3, 20, 18, 4, 2), at(2026, 3, 21, 6, 10, 32),
     at(2026, 3, 20, 17, 43, 22), at(2026, 3, 21, 6, 31, 12),
     at(2026, 3, 21, 0, 7, 17), "normal", "normal"),
    # Date line, east side: sunrise is on the previous UTC date.
    ("Equator 179.9E", (0.0, 179.9), date(2026, 3, 20),
     at(2026, 3, 19, 18, 4, 43), at(2026, 3, 20, 6, 11, 14),
     at(2026, 3, 19, 17, 44, 3), at(2026, 3, 20, 6, 31, 54),
     at(2026, 3, 20, 0, 7, 59), "normal", "normal"),
    ("Sydney December solstice", SYDNEY, date(2026, 12, 21),
     at(2026, 12, 20, 18, 40, 39), at(2026, 12, 21, 9, 5, 26),
     at(2026, 12, 20, 18, 11, 29), at(2026, 12, 21, 9, 34, 36),
     at(2026, 12, 21, 1, 53, 2), "normal", "normal"),
    ("Suva June solstice", SUVA, date(2026, 6, 21),
     at(2026, 6, 20, 18, 36, 42), at(2026, 6, 21, 5, 39, 11),
     at(2026, 6, 20, 18, 12, 58), at(2026, 6, 21, 6, 2, 56),
     at(2026, 6, 21, 0, 7, 57), "normal", "normal"),
    ("Tromso equinox", TROMSO, date(2026, 3, 20),
     at(2026, 3, 20, 4, 43, 53), at(2026, 3, 20, 17, 1, 34),
     at(2026, 3, 20, 3, 43, 46), at(2026, 3, 20, 18, 2, 8),
     at(2026, 3, 20, 10, 51, 38), "normal", "normal"),
    ("Longyearbyen equinox", LONGYEARBYEN, date(2026, 3, 20),
     at(2026, 3, 20, 4, 51, 46), at(2026, 3, 20, 17, 22, 4),
     at(2026, 3, 20, 3, 6, 2), at(2026, 3, 20, 19, 9, 44),
     at(2026, 3, 20, 11, 4, 56), "normal", "normal"),
    # White night: the sun rises and sets, but never gets 6 degrees below the
    # horizon, so there is no civil dawn or dusk.
    ("Tromso white night", TROMSO, date(2026, 5, 10),
     at(2026, 5, 10, 0, 25, 9), at(2026, 5, 10, 21, 2, 18),
     None, None, at(2026, 5, 10, 10, 40, 36), "normal", "always_light"),
    # NOAA's own code is 54 s earlier than SSORG for this sunset; both are inside the tolerance.
    ("Tromso near midnight sun", TROMSO, date(2026, 5, 17),
     at(2026, 5, 16, 23, 17, 15), at(2026, 5, 17, 22, 29, 3),
     None, None, at(2026, 5, 17, 10, 40, 36), "normal", "always_light"),
    # Midnight sun.
    ("Tromso June solstice", TROMSO, date(2026, 6, 21),
     None, None, None, None, at(2026, 6, 21, 10, 46, 0), "polar_day", "always_light"),
    ("Longyearbyen June solstice", LONGYEARBYEN, date(2026, 6, 21),
     None, None, None, None, at(2026, 6, 21, 10, 59, 18), "polar_day", "always_light"),
    ("McMurdo December solstice", MCMURDO, date(2026, 12, 21),
     None, None, None, None, at(2026, 12, 21, 0, 51, 10), "polar_day", "always_light"),
    # Polar night, but civil twilight still happens: the sun's noon altitude at
    # Tromso on 21 December is about -3 degrees.
    ("Tromso December solstice", TROMSO, date(2026, 12, 21),
     None, None, at(2026, 12, 21, 8, 31, 13), at(2026, 12, 21, 12, 53, 13),
     at(2026, 12, 21, 10, 42, 14), "polar_night", "normal"),
    # Polar night with no civil twilight either (noon altitude about -11.7 degrees).
    ("Longyearbyen December solstice", LONGYEARBYEN, date(2026, 12, 21),
     None, None, None, None, at(2026, 12, 21, 10, 55, 33), "polar_night", "always_dark"),
    ("McMurdo June solstice", MCMURDO, date(2026, 6, 21),
     None, None, None, None, at(2026, 6, 21, 0, 55, 3), "polar_night", "always_dark"),
]


@pytest.mark.parametrize("case", SUN_CASES, ids=[c[0] for c in SUN_CASES])
def test_sun_times(case):
    label, (lat, lon), day, rise, sset, dawn, dusk, noon, status, civil = case
    t = sun_times(Point(lat, lon), day)
    assert isinstance(t, SunTimes)
    check_time(t.sunrise, rise, f"{label} sunrise")
    check_time(t.sunset, sset, f"{label} sunset")
    check_time(t.civil_dawn, dawn, f"{label} civil dawn")
    check_time(t.civil_dusk, dusk, f"{label} civil dusk")
    check_time(t.solar_noon, noon, f"{label} solar noon")
    assert t.status == status
    assert t.civil_status == civil


@pytest.mark.parametrize("case", [c for c in SUN_CASES if c[3] is not None and c[5] is not None],
                         ids=lambda c: c[0])
def test_event_order_on_normal_days(case):
    t = sun_times(Point(*case[1]), case[2])
    assert t.civil_dawn < t.sunrise < t.solar_noon < t.sunset < t.civil_dusk
    assert timedelta(hours=7) < t.sunset - t.sunrise < timedelta(hours=18)


def test_sunrise_sunset_without_civil_still_ordered():
    t = sun_times(Point(*TROMSO), date(2026, 5, 10))
    assert t.sunrise < t.solar_noon < t.sunset


# Poles. Geometry: the sun's altitude is the declination (sign by pole) all day.
# SSORG: lat 90, June and December solstice "midnight_sun" / "polar_night"; both
# poles are "midnight_sun" on 2026-03-20 (declination about -0.2 at 00:00 UTC).
POLES = [
    (90.0, date(2026, 6, 21), "polar_day", "always_light"),
    (90.0, date(2026, 12, 21), "polar_night", "always_dark"),
    (-90.0, date(2026, 6, 21), "polar_night", "always_dark"),
    (-90.0, date(2026, 12, 21), "polar_day", "always_light"),
    (90.0, date(2026, 3, 20), "polar_day", "always_light"),
    (-90.0, date(2026, 3, 20), "polar_day", "always_light"),
    (90, date(2026, 6, 21), "polar_day", "always_light"),
]


@pytest.mark.parametrize("lat,day,status,civil", POLES)
@pytest.mark.parametrize("lon", [0.0, 123.4, -180.0, 180.0])
def test_poles(lat, day, status, civil, lon):
    t = sun_times(Point(lat, lon), day)
    assert (t.sunrise, t.sunset, t.civil_dawn, t.civil_dusk) == (None, None, None, None)
    assert t.status == status
    assert t.civil_status == civil
    assert isinstance(t.solar_noon, datetime) and t.solar_noon.tzinfo is not None


def test_pole_solar_noon():
    # SSORG: 2026-06-21T12:01:49Z and 2026-12-21T11:58:05Z at lat 90, lon 0.
    check_time(sun_times(Point(90.0, 0.0), date(2026, 6, 21)).solar_noon,
               at(2026, 6, 21, 12, 1, 49), "north pole noon, June")
    check_time(sun_times(Point(90.0, 0.0), date(2026, 12, 21)).solar_noon,
               at(2026, 12, 21, 11, 58, 5), "north pole noon, December")


def test_near_pole_is_not_a_special_case():
    # SSORG at 89.5 N: midnight sun in June, polar night in December.
    assert sun_times(Point(89.5, 0.0), date(2026, 6, 21)).status == "polar_day"
    assert sun_times(Point(89.5, 0.0), date(2026, 12, 21)).status == "polar_night"
    assert sun_times(Point(89.5, 0.0), date(2026, 12, 21)).civil_status == "always_dark"


def test_longitude_180_is_minus_180():
    for lat in (0.0, 45.0, -33.0, 70.0):
        assert sun_times(Point(lat, 180.0), date(2026, 3, 20)) == sun_times(
            Point(lat, -180.0), date(2026, 3, 20))


def test_locator_as_place():
    d = date(2026, 6, 21)
    centre = locator_center("FN43up")
    assert sun_times("FN43up", d) == sun_times(centre, d)
    assert sun_times("fn43UP", d) == sun_times(centre, d)
    # Portland, Maine is inside FN43up, so it is within a minute of the point values.
    check_time(sun_times("FN43up", d).sunrise, at(2026, 6, 21, 8, 59, 32), "FN43up sunrise")
    check_time(sun_times("FN43up", d).sunset, at(2026, 6, 22, 0, 26, 15), "FN43up sunset")


def test_result_is_deterministic():
    d = date(2026, 6, 21)
    assert sun_times(Point(*PORTLAND), d) == sun_times(Point(*PORTLAND), d)


def test_date_range():
    sun_times(Point(*PORTLAND), date(1901, 1, 1))
    sun_times(Point(*PORTLAND), date(2099, 12, 31))
    for bad in (date(1900, 12, 31), date(2100, 1, 1), date(1, 1, 1), date(9999, 12, 31)):
        with pytest.raises(GeoError):
            sun_times(Point(*PORTLAND), bad)


@pytest.mark.parametrize("bad", [None, "2026-06-21", 20260621, 2026.0, (2026, 6, 21),
                                 datetime(2026, 6, 21), datetime(2026, 6, 21, tzinfo=UTC),
                                 b"2026-06-21", object(), True])
def test_bad_date(bad):
    with pytest.raises(GeoError):
        sun_times(Point(*PORTLAND), bad)


@pytest.mark.parametrize("bad", [None, 5, (43.6, -70.2), [43.6, -70.2], "", "FN3",
                                 "FN43up212", "FN43up ", "\uff26\uff2e", b"FN43", object()])
def test_bad_place(bad):
    with pytest.raises(GeoError):
        sun_times(bad, date(2026, 6, 21))


def test_every_day_of_a_year_at_three_latitudes():
    # No exceptions and consistent fields on every day, including the days
    # either side of each polar transition.
    day = date(2026, 1, 1)
    while day.year == 2026:
        for lat in (43.6591, 69.6492, -77.85):
            t = sun_times(Point(lat, 18.0), day)
            assert t.status in {"normal", "polar_day", "polar_night"}
            assert t.civil_status in {"normal", "always_light", "always_dark"}
            if t.status != "normal":
                assert t.sunrise is None and t.sunset is None
            if t.civil_status != "normal":
                assert t.civil_dawn is None and t.civil_dusk is None
            for value in (t.sunrise, t.sunset, t.civil_dawn, t.civil_dusk, t.solar_noon):
                if value is not None:
                    assert value.tzinfo is not None and value.microsecond == 0
        day += timedelta(days=1)


# ----------------------------------------------------------- hostile input

WEIRD_TEXT = ["", " ", "A", "FN", "FN31", "FN31pr", "FN31pr21", "fn31", "\u017f\u017f", "K",
              "\u0663", "\uff26", "\x00", "\n", "\U0001f600", "FN31pr21RN", "-", "AR09AX09AX",
              "AB12cd34", "SS99", "RR99xx99", "\ud800", "\u212a", "x"]
WEIRD_NUMBERS = [0, 1, -1, 90, -90, 180, -180, 91, -91, 181, 0.0, -0.0, 89.9999999, 1e-300,
                 1e308, -1e308, float("nan"), float("inf"), float("-inf"), 10**400,
                 -(10**400), True, None, "1", b"1", [], Decimal(1), Fraction(1, 3),
                 3 + 0j, object()]
WEIRD_DATES = [date(2026, 6, 21), date(1901, 1, 1), date(2099, 12, 31), date(1, 1, 1),
               date.max, datetime(2026, 6, 21), None, "2026-06-21", 5, object()]


def only_geo_error(fn, *args):
    try:
        return fn(*args)
    except GeoError:
        return None


def test_no_other_exception_for_any_input():
    rng = random.Random(1234)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 -.\n\x00\u017f\u212a\u0663\uff21\u00e9\U0001f600"
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
        for fn in (normalize_locator, locator_box, locator_center):
            only_geo_error(fn, text)
        only_geo_error(great_circle, text, rng.choice(WEIRD_TEXT))
        only_geo_error(sun_times, text, rng.choice(WEIRD_DATES))
    for _ in range(3000):
        lat, lon = rng.choice(WEIRD_NUMBERS), rng.choice(WEIRD_NUMBERS)
        length = rng.choice(WEIRD_NUMBERS + [2, 4, 6, 8])
        only_geo_error(to_locator, lat, lon)
        only_geo_error(to_locator, lat, lon, length)
        only_geo_error(Point, lat, lon)
        only_geo_error(sun_times, rng.choice(WEIRD_TEXT + WEIRD_NUMBERS), rng.choice(WEIRD_DATES))
        only_geo_error(great_circle, rng.choice(WEIRD_TEXT + WEIRD_NUMBERS),
                       rng.choice(WEIRD_TEXT + WEIRD_NUMBERS))


def test_valid_points_on_the_boundary_never_fail():
    rng = random.Random(42)
    lat_edge = [-90.0, 90.0, 0.0, -0.0, 89.99999999999999, -89.99999999999999]
    lon_edge = [-180.0, 180.0, 0.0, -0.0, 179.99999999999997, -179.99999999999997]

    def pick():
        return Point(rng.choice(lat_edge + [rng.uniform(-90, 90)]),
                     rng.choice(lon_edge + [rng.uniform(-180, 180)]))

    for _ in range(400):
        a, b = pick(), pick()
        loc = to_locator(a.lat, a.lon, 8)
        assert loc == normalize_locator(loc)
        great_circle(a, b)
        sun_times(a, date(2026, rng.randint(1, 12), rng.randint(1, 28)))
