# Offline calculations (`federated_cms.geo`)

**Status:** specification with contract tests (`tests/test_geo.py`). The
tests are the contract; this document says what they pin down and why.

Maidenhead locators, great-circle distance and bearing, and sunrise,
sunset and civil twilight. Everything works with no Internet: the module
has no network, file, clock or environment access, and it imports
nothing from the rest of `federated_cms`, so it can move to a shared
package. A service that answers a plain-text request by radio mail (for
example a locator conversion, or the distance between two stations)
calls it and formats the reply.

Python 3.11+ standard library only.

## API

```python
from federated_cms.geo import (
    GeoError, Point, Box, Course, SunTimes,
    normalize_locator, locator_box, locator_center, to_locator,
    great_circle, sun_times,
    EARTH_RADIUS_KM, KM_PER_MILE,
)
```

`GeoError` subclasses `ValueError`. The four dataclasses are frozen.
`__all__` lists exactly the names above.

```python
EARTH_RADIUS_KM = 6371.0088      # float
KM_PER_MILE = 1.609344           # float; international statute mile

@dataclass(frozen=True)
class Point:
    lat: float    # degrees, -90..90, north positive
    lon: float    # degrees, -180..180, east positive

@dataclass(frozen=True)
class Box:
    south: float
    west: float
    north: float
    east: float

@dataclass(frozen=True)
class Course:
    distance_km: float
    distance_miles: float          # distance_km / KM_PER_MILE
    bearing: float | None          # degrees true, 0 <= bearing < 360

@dataclass(frozen=True)
class SunTimes:
    sunrise: datetime | None       # timezone-aware, UTC, whole seconds
    sunset: datetime | None
    civil_dawn: datetime | None
    civil_dusk: datetime | None
    solar_noon: datetime           # always present
    status: str                    # "normal", "polar_day", "polar_night"
    civil_status: str              # "normal", "always_light", "always_dark"
```

`Point(...)` validates its arguments (see Input rules) and stores `int`
values as `float`.

### Functions

| Function | Returns |
| --- | --- |
| `normalize_locator(text: str) -> str` | Canonical locator, for example `FN31pr21`. |
| `locator_box(text: str) -> Box` | The cell the locator names. |
| `locator_center(text: str) -> Point` | The centre of that cell. |
| `to_locator(lat, lon, length: int = 6) -> str` | Canonical locator of `length` 2, 4, 6 or 8 characters. |
| `great_circle(a, b) -> Course` | Distance and initial bearing from `a` to `b`. |
| `sun_times(where, day: date) -> SunTimes` | Sun events for a place and a UTC date. |

`a`, `b` and `where` are each a `Point` or a locator string. A string is
converted with `locator_center`, at the length it was given.

### Input rules (all functions)

The module is the first thing that sees text from a radio message, so it
is strict. Every failure raises `GeoError`. No other exception type
escapes for any input, including wrong types, `None`, `bytes`, huge
integers and very long strings.

- **Locator text** must be a `str` of 2, 4, 6 or 8 characters, ASCII
  only, with no spaces or other characters around it. The module does
  not strip. A longer string is refused on its length alone, before any
  other work (a 10 MB string must fail in well under a second). Letters
  and digits are checked as ASCII; a character such as `٣` (Arabic-Indic
  digit), `Ｆ` (fullwidth F) or `K` (Kelvin sign, which lowercases to
  `k`) is refused, not mapped.
- **Numbers:** `lat` and `lon` must be `int` or `float`; `length` must be an `int`.
  `bool`, `str`, `None`, `Decimal`, `Fraction`, NaN and infinities are
  refused. `lat` must be within -90..90 and `lon` within -180..180,
  inclusive; the range is checked on the value as given, so an integer
  like `10**400` is refused without being converted to `float`.
- **Dates** must be exactly `datetime.date` (not `datetime`), with a year
  from 1901 to 2099 inclusive. That is the range NOAA states for its
  spreadsheet (below).

## Maidenhead locators

Definition used: IARU Region 1 VHF Handbook, section 20, "The Locator
System" (see Sources). A locator is up to four pairs. In every pair the
longitude character comes first, then the latitude character.

| Pair | Characters | Divides | Cell size (lon x lat) |
| --- | --- | --- | --- |
| 1, field | `A` to `R` (18) | the world | 20 degrees x 10 degrees |
| 2, square | `0` to `9` (10) | a field | 2 degrees x 1 degree |
| 3, subsquare | `A` to `X` (24) | a square | 5 minutes x 2.5 minutes |
| 4, extended square | `0` to `9` (10) | a subsquare | 0.5 minute x 0.25 minute |

Longitude counts from -180 east, latitude from -90 north. The handbook
allows pairs 3 and 4 to repeat for ten characters or more; this version
accepts 2, 4, 6 and 8 characters only.

Pair 4 is the "extended square": the handbook calls its cells "small
squares" of 0.5' x 0.25'; Wikipedia calls the same pair the "extended
square". It is two digits, `0` to `9`, east then north, so a subsquare
splits into 10 x 10 cells. (The IARU text and Wikipedia agree; the ARRL
page only describes pairs 1 to 3.)

**Case.** Input is case-insensitive. Output is canonical: field letters
upper case, digits as they are, subsquare letters lower case, as in the
ARRL's `FN44ig` and the usual `FN31pr`. So `normalize_locator("fn31PR21")`
is `"FN31pr21"`. The IARU text says "Locators shall use upper case
characters for all positions" while also recommending that software
accept any case; `.upper()` of the canonical form gives that notation.
See Open questions.

**Cell edges.** A cell includes its south and west edges and excludes its
north and east edges. A coordinate exactly on an edge belongs to the cell
to the north and east. Two exceptions, from the handbook's own test
table:

- Latitude 90 belongs to the top row (`R`, `9`, `X`, `9`).
- Longitude 180 is the same meridian as -180 and belongs to the first
  column (`A`, `0`, `A`, `0`).
- At a pole (`lat` of 90 or -90) longitude is undefined and the first
  column is used whatever `lon` is: the handbook says "At the poles,
  where longitude is undefined, the letter 'A' shall be used."

`to_locator` works from the exact value of the float it is given
(`fractions.Fraction(x)`), so a float that is exactly on an edge, such as
`-1.75`, lands on a predictable side. A float that only approximates an
edge (such as `4/3`) may land on either side; the tests do not depend on
it.

**`locator_box`** returns the cell. Its edges are floats, equal to the
exact rational edge to within one unit of floating-point rounding.
`RR99xx99` ends exactly at `north=90.0` and `east=180.0`; `AA00aa00`
starts exactly at `south=-90.0` and `west=-180.0`.

**`locator_center`** returns the centre of the cell. The handbook: "The
reference location for a given locator is the centroid of the locator's
squaroid." The centre is taken at the length given, so `FN43` is the
centre of the 2 x 1 degree square, not of `FN43mm`. (The handbook says to
use six characters when no length is defined; this module has no
context for that, so a caller that wants six-character behaviour should
pass a six-character locator.)

**`to_locator(lat, lon, length=6)`** returns the canonical locator of the
requested length for the point.

## Great-circle distance and bearing

**Earth model.** A sphere of radius 6371.0088 km, the IUGG arithmetic
mean radius R1 = (2a + b) / 3 for the WGS 84 radii. Wikipedia: "For
Earth, the arithmetic mean radius is published by IUGG and NGA as
6,371.0087714 km". Movable Type uses 6371 km; the 0.0088 km difference
is below any reply precision.

**Distance** (haversine), with latitudes and longitudes in radians:

```
a = sin^2(dphi / 2) + cos(phi1) * cos(phi2) * sin^2(dlambda / 2)
c = 2 * atan2(sqrt(a), sqrt(1 - a))
d = R * c
```

Clamp `a` to the range 0 to 1 before the square roots. Statute miles are
`d / 1.609344`, the international mile (exactly 1609.344 m; not the US
survey mile).

**Initial bearing** from point 1 to point 2, degrees true:

```
y = sin(dlambda) * cos(phi2)
x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(dlambda)
theta = (degrees(atan2(y, x)) + 360) mod 360
```

`bearing` is `None` (no meaningful direction) when:

- the distance is under 1 m (`distance_km < 0.001`), the same place;
- the points are antipodal to within 1 m
  (`distance_km > pi * R - 0.001`).

Poles need their own rule because the formula uses the longitude of a
point where longitude is undefined:

- Starting at the north pole, the bearing is `180.0`; starting at the
  south pole, `0.0` (every way leads away from it). Ending at the north
  pole from another point it is `0.0`; ending at the south pole, `180.0`.
- Both rules apply after the two `None` checks above.

**Reply rounding** (for the service that formats the reply; the module
returns unrounded values): distance in whole kilometres and whole
statute miles (round half up), bearing to the nearest whole degree with
360 shown as 0. The spherical model is within about 0.5 percent of the
WGS 84 ellipsoid on any route, so a reply should not show decimals.

**Expected accuracy.** Against WGS 84 geodesics the distances in the
tests differ by 0.02 to 0.3 percent, and bearings by up to 0.4 degree.

## Sunrise, sunset and civil twilight

**Method.** The NOAA Solar Calculator equations (NOAA GML), which "are
based on equations from Astronomical Algorithms, by Jean Meeus". The
equations are in the spreadsheets linked from the calculation details
page and in the calculator's own `main.js`; this section restates the
parts used. All angles below are in degrees unless a function name says
otherwise.

**Julian day** at 00:00 UTC of the date (`y`, `m`, `d`), Gregorian:

```
if m <= 2: y = y - 1; m = m + 12
A = floor(y / 100);  B = 2 - A + floor(A / 4)
JD0 = floor(365.25 * (y + 4716)) + floor(30.6001 * (m + 1)) + d + B - 1524.5
```

**Sun position** at Julian day `JD`, with `T = (JD - 2451545) / 36525`:

```
L0 = (280.46646 + T * (36000.76983 + T * 0.0003032)) mod 360
M  = 357.52911 + T * (35999.05029 - 0.0001537 * T)
e  = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)
C  = sin(M) * (1.914602 - T * (0.004817 + 0.000014 * T))
     + sin(2M) * (0.019993 - 0.000101 * T) + sin(3M) * 0.000289
trueLong   = L0 + C
omega      = 125.04 - 1934.136 * T
appLong    = trueLong - 0.00569 - 0.00478 * sin(omega)
eps0       = 23 + (26 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60) / 60
eps        = eps0 + 0.00256 * cos(omega)
declination = asin(sin(eps) * sin(appLong))
y = tan(eps / 2)^2
E (minutes) = 4 * degrees( y * sin(2 L0) - 2 e sin(M)
              + 4 e y sin(M) cos(2 L0) - 0.5 y^2 sin(4 L0) - 1.25 e^2 sin(2M) )
```

`E` is the equation of time.

**Event time** for a place (`lat` phi, `lon` lambda, east positive) and a
zenith angle `z` (the sun's centre is `z` degrees from straight up):

```
arg = cos(z) / (cos(phi) * cos(declination)) - tan(phi) * tan(declination)
HA  = degrees(acos(arg))                       # hour angle, needs -1 <= arg <= 1
sunrise minutes = 720 - 4 * (lambda + HA) - E
sunset  minutes = 720 - 4 * (lambda - HA) - E
```

Minutes count from 00:00 UTC of the given date and may be negative or 1440
or more, so an event can fall on the previous or next UTC date. This is
deliberate: the date selects the local solar day of the place, as the
NOAA calculator does for a local date. Portland, Maine on 2026-06-21
has its sunset at 2026-06-22 00:26 UTC; Sydney on 2026-12-21 has its
sunrise at 2026-12-20 18:40 UTC.

NOAA's code runs each event twice. Pass 1 takes `declination` and `E`
at `JD0`. Pass 2 repeats at `JD0 + t1 / 1440`, with `t1` the pass-1
minutes, and its result is the answer. Do the same, separately for each
of the four events. Round the final minutes to whole seconds and add
them to 00:00 UTC of the date.

**Zenith angles.**

| Event | `z` | Meaning |
| --- | --- | --- |
| sunrise, sunset | 90.833 | sun's centre 0.833 degrees below the horizon: refraction plus the sun's radius |
| civil dawn, civil dusk | 96 | sun's centre 6 degrees below the horizon |

NOAA: "For sunrise and sunset calculations, we assume 0.833° of
atmospheric refraction." The 0.833 is used as a depression below the
horizon (`cos(90.833)` in the code). Civil twilight is the standard
definition of 6 degrees below the horizon; NOAA's code has no civil
twilight, so the module uses the same equation with 96.

**Solar noon.** `n1 = 720 - 4 * lambda - E(JD0)`, then
`solar_noon = 720 - 4 * lambda - E(JD0 + n1 / 1440)` minutes after 00:00
UTC of the date, not wrapped into the date (at longitude -180 it falls on
the next UTC date). It always exists.

**Longitude 180** is converted to -180 first, as for locators, so that
the same place gives the same answer.

**Polar day and night.** Look at `arg` from pass 1:

- `arg > 1`: the sun never reaches the threshold. Both events are `None`.
  For `z = 90.833` the `status` is `"polar_night"`; for `z = 96` the
  `civil_status` is `"always_dark"`.
- `arg < -1`: the sun never goes below it. Both events are `None`.
  `status` is `"polar_day"`; `civil_status` is `"always_light"`.
- Otherwise the status is `"normal"`. If the pass-2 `arg` is out of
  range, that one event is `None` and the status stays `"normal"`. This
  can only happen within about a day of a polar transition, and NOAA's
  calculator behaves the same.

The two statuses are independent. Sunrise and sunset can exist while
civil twilight does not (`civil_status="always_light"` on a white night),
and civil twilight can exist while the sun never rises (Tromso, 21
December: `status="polar_night"`, `civil_status="normal"`). Never infer
one event from another.

At a pole (`lat` of 90 or -90) the equation divides by `cos(phi) = 0`,
so use geometry instead: the sun's altitude is `sign(lat) * declination`
all day, with `declination` at 12:00 UTC of the date. If that altitude is
greater than -0.833 the `status` is `"polar_day"`, otherwise
`"polar_night"`; for civil, greater than -6 gives `"always_light"`,
otherwise `"always_dark"`. All four events are `None`. `solar_noon` uses
the given longitude as usual.

**Returned times** are `datetime` objects with `tzinfo=timezone.utc` and
`microsecond=0`. Converting to local time and choosing the reply format
(for example `HH:MMZ`, rounded to the nearest minute) belong to the
service.

**Accuracy.** NOAA states that "The sunrise and sunset results are
theoretically accurate to within a minute for locations between +/- 72°
latitude, and within 10 minutes outside of those latitudes." The tests
allow 2 minutes (120 s) against independent references, which is wide
enough for the references' own differences (a few seconds) and for a
legitimate implementation of NOAA's two-pass method at 78 degrees north.
Treat reported times as good to about a minute and say so in replies if
precision matters. Real sunrise also depends on weather, terrain and
pressure. NOAA also states that the calculator "is no longer actively
supported or maintained", so the equations are a frozen reference, not a
moving target.

## Behaviour that applies to every function

- Pure and deterministic: the same arguments give the same result. No
  clock, no random numbers, no files, no network. A test makes
  `socket` fail and checks the functions still work.
- Imports: standard library only, none of `socket`, `ssl`, `urllib`,
  `http`, `subprocess`; nothing from `federated_cms`, absolute or
  relative. A test reads the module's source to check.
- Speed: no call does work proportional to the size of its input; an
  over-long string is refused on its length.

## Sources

Retrieved 2026-11-25.

### Locators

- IARU Region 1 VHF Handbook, version 10.03 (February 2026), section 20
  "The Locator System", pages 106 to 109 of 132,
  <https://www.iaru-r1.org/wp-content/uploads/2026/02/VHF_Handbook_V10_03_final.pdf>.
  This is the primary definition used, including the pair 4 definition
  and the test table. Quotes:
  - "There are 18 of these subdivisions for latitude and longitude; each
    is allocated a letter of the English alphabet from 'A' to 'R'."
  - "These large squaroids are subdivided into 24 x 24 'sub-squares',
    with dimensions 5 x 2.5 minutes of arc for longitude/latitude
    respectively."
  - "These 'sub-squares' are divided into 10 'small squares' of 0.5' x
    0.25' for roughly 300 metres positioning accuracy. These appear as
    another pair of numbers from '0' to '9', for example 'IO90IV58'."
  - "The reference location for a given locator is the centroid of the
    locator's squaroid."
  - "It is recommended that software implementations accept any case or
    mixed case input"
  - "A locator implementation shall be verified for correctness using at
    least the following test cases:" followed by a table (poles,
    equator, 180 E, Antarctica, Eiffel Tower). The tests use every row.
    The table's "Centroid Longitude" and "Centroid Latitude" columns are
    swapped in the PDF (the "longitude" column holds 48.85...); the
    values are used with the right meaning.
- Maidenhead Locator System, Wikipedia,
  <https://en.wikipedia.org/wiki/Maidenhead_Locator_System>: "The fourth
  pair (extended square) encodes with base 10 and the digits '0' to
  '9'." Cites IARU (Talbot, G4JNT, 2017) and QST (Overbeck, 1986).
- ARRL, <https://www.arrl.org/locate/gridinfo.html>: "A grid square is
  indicated by two letters (the field) and two numbers (the square), as
  in FN31." "Each subsquare is designated by the addition of two letters
  after the grid square, as FN44ig." The ARRL page says W1AW is in FN31.
- AC5JW geotable for W1AW, <https://qsl.net/ac5jw/geo/arrl-w1aw.html>:
  W1AW-FAC at 41.714766666, -72.727183333 is `FN31PR21RN70`. Seven
  points on the site, including ARRL-E at 41.714716666, -72.728433333,
  all fall in `FN31PR21`.

### Distance and bearing

- Movable Type Scripts, "Calculate distance, bearing and more between
  Latitude/Longitude points",
  <https://www.movable-type.co.uk/scripts/latlong.html>: haversine
  `a = sin²(Δφ/2) + cos φ1 ⋅ cos φ2 ⋅ sin²(Δλ/2)`, `c = 2 ⋅ atan2( √a,
  √(1−a) )`, `d = R ⋅ c`; bearing `θ = atan2( sin Δλ ⋅ cos φ2 , cos φ1 ⋅
  sin φ2 − sin φ1 ⋅ cos φ2 ⋅ cos Δλ )`; "mean radius = 6,371km"; "you
  would start on a heading of 60° and end up on a heading of 120°" for
  35°N,45°E to 35°N,135°E.
- Ed Williams, Aviation Formulary v1.47,
  <https://edwilliams.org/avform147.htm>: distance
  `d=acos(sin(lat1)*sin(lat2)+cos(lat1)*cos(lat2)*cos(lon1-lon2))`;
  course formula (west-positive longitudes); "one nautical mile subtends
  one minute (=1/60 degree) of arc"; worked example LAX 33°57'N 118°24'W
  to JFK 40°38'N 73°47'W: 0.623585 radians = 2144 nm, initial course
  1.150035 radians = 66 degrees; "Other choices of the earth's radius in
  this range are consistent with the spherical approximation".
- Earth radius, Wikipedia, <https://en.wikipedia.org/wiki/Earth_radius>:
  IUGG "arithmetic mean radius (denoted R1)", R1 = (2a + b)/3,
  "6,371.0087714 km".
- Mile, Wikipedia, <https://en.wikipedia.org/wiki/Mile>: the
  international mile is exactly 1609.344 m (1959 agreement). A secondary
  source; NIST SP 811 lists the same factor but was not retrieved.

### Sun

- NOAA GML Solar Calculator, <https://gml.noaa.gov/grad/solcalc/>, and
  "Solar Calculation Details",
  <https://gml.noaa.gov/grad/solcalc/calcdetails.html>: equations "based
  on equations from Astronomical Algorithms, by Jean Meeus"; "accurate to
  within a minute for locations between +/- 72° latitude, and within 10
  minutes outside of those latitudes"; "we assume 0.833° of atmospheric
  refraction"; spreadsheets "only valid for dates between 1901 and 2099";
  "no longer actively supported or maintained".
- NOAA spreadsheet `NOAA_Solar_Calculations_day.ods`,
  <https://gml.noaa.gov/grad/solcalc/NOAA_Solar_Calculations_day.ods>:
  the formulas for geometric mean longitude and anomaly, eccentricity,
  equation of centre, apparent longitude, obliquity, declination,
  equation of time, `ACOS(COS(RADIANS(90.833))/(COS(RADIANS(lat)) *
  COS(RADIANS(dec))) - TAN(RADIANS(lat)) * TAN(RADIANS(dec)))` for the
  sunrise hour angle, and solar noon `(720 - 4 * lon - EoT + tz * 60) /
  1440`. This document's equations were checked against them.
- NOAA calculator code, <https://gml.noaa.gov/grad/solcalc/main.js>:
  `getJD`, `calcSunriseSetUTC` (`720 - (4.0 * delta) - eqTime`), the
  second pass in `calcSunriseSet`, and `calcHourAngleSunrise` with
  `Math.cos(degToRad(90.833))`.
- sunrise-sunset.org API v2, <https://sunrise-sunset.org/api>: an
  independent source for the expected times. It states it was "checked
  against an independent astronomical reference, the JPL planetary
  ephemerides" and gives civil twilight as "sun at -6°", sunrise and
  sunset at -0.833°. It also states that "All events are independently
  nullable" near the polar circles.

## How the expected values in the tests were obtained

Each value in `tests/test_geo.py` has a comment saying where it came
from. In short:

| Area | Source of the expected value |
| --- | --- |
| Locators from the handbook table | Copied from the table (IARU R1 handbook 10.03, section 20.2.7), with the ten-character examples cut to eight characters. |
| `IO90`, `IO90IV`, `IO90IV58`, `JN18DU55` boxes | Handbook text ("lower left corner lies at 2° West and 50° North", "1 degree 20 minutes West and 50 degrees 52.5 minutes North") and its Eiffel Tower centroid table; the eight-character values follow from the cell sizes and agree with the table's `JN18DU55IX` row. |
| `FN31pr`, `FN31pr21` | ARRL (W1AW is in FN31) and the AC5JW geotable (`FN31PR21`); boxes by hand arithmetic from the cell sizes. |
| `BL11bh16`, `FN43up` | Example format from Wikipedia; boxes by hand arithmetic. |
| Distances and bearings | Movable Type's reference implementation (the `geodesy` package by the same author) run on 2026-11-25 with R = 6371008.8 m. Each route is also checked against the WGS 84 ellipsoid (GeographicLib) to within 0.5 percent, and LAX to JFK against Ed Williams's published 0.623585 radians. The equator, meridian and date-line cases follow from the arc-length definition (`R * angle`). |
| Sun times | NOAA's published `main.js` run unmodified with Node (zenith 90.833, and 96 for civil twilight) and the sunrise-sunset.org v2 API (JPL-checked); the two agree within about 20 seconds in every case used except one high-latitude day (54 s), and the tests use the API's value with a 120 second tolerance. The API's local calendar day equals the UTC-date convention above for every case used. |
| Polar and status cases | The same two sources; `civil_status` for the poles and for days where only one status changes follows the `arg` rule above. |

The throwaway scripts were kept outside the repository and the module
under test does not exist yet; nothing here was computed with it.

## Not in this version

Locators longer than 8 characters; ellipsoidal (Vincenty or Karney)
distance; rhumb lines and midpoints; nautical and astronomical twilight;
moon times; time zones and daylight saving (the service converts UTC);
formatting of replies; parsing coordinates from text such as `43 39.5N
70 15.4W`.

## Open questions

1. **Canonical case.** The brief asks for `FN43mo` style output (upper
   case field, lower case subsquare), which is common practice and
   matches ARRL. IARU says all upper case. This spec follows the brief;
   change `normalize_locator`, `to_locator` and the tests together if
   upper case is wanted.
2. **Centre of a short locator.** `FN43` is measured from the middle of
   the 2 x 1 degree square. IARU says to use six characters if no length
   is defined. Decide whether the service should widen short locators.
3. **Date meaning for sun times.** "UTC date" is implemented as NOAA's
   minutes-from-00:00-UTC convention, which follows the local solar day.
   If the service wants events inside the UTC calendar day instead, the
   result needs a different rule and different tests.
4. **Longer locators.** Ten and twelve characters (AC5JW publishes them)
   are refused for now.
5. **Bearing at the poles** is a convention here (180 or 0 degrees); no
   source defines it.
