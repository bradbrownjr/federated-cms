"""Offline Maidenhead, great-circle, and solar calculations."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
import math

__all__ = ["GeoError", "Point", "Box", "Course", "SunTimes", "normalize_locator",
           "locator_box", "locator_center", "to_locator", "great_circle",
           "sun_times", "EARTH_RADIUS_KM", "KM_PER_MILE"]

EARTH_RADIUS_KM = 6371.0088
KM_PER_MILE = 1.609344


class GeoError(ValueError):
    """Invalid geographic input."""


def _coordinate(value, limit):
    """Validate and convert a coordinate without converting huge integers."""
    if type(value) not in (int, float) or not -limit <= value <= limit:
        raise GeoError("Invalid coordinate")
    return float(value)


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float

    def __post_init__(self):
        object.__setattr__(self, "lat", _coordinate(self.lat, 90))
        object.__setattr__(self, "lon", _coordinate(self.lon, 180))


@dataclass(frozen=True)
class Box:
    south: float
    west: float
    north: float
    east: float


@dataclass(frozen=True)
class Course:
    distance_km: float
    distance_miles: float
    bearing: float | None


@dataclass(frozen=True)
class SunTimes:
    sunrise: datetime | None
    sunset: datetime | None
    civil_dawn: datetime | None
    civil_dusk: datetime | None
    solar_noon: datetime
    status: str
    civil_status: str


def normalize_locator(text: str) -> str:
    """Validate a locator and return its canonical case."""
    if not isinstance(text, str) or len(text) not in (2, 4, 6, 8):
        raise GeoError("Invalid locator length or type")
    if not text.isascii():
        raise GeoError("Locator must be ASCII")
    result = []
    for index, char in enumerate(text):
        pair = index // 2
        if pair in (0, 2):
            char = char.upper()
            maximum = 'R' if pair == 0 else 'X'
            if not 'A' <= char <= maximum:
                raise GeoError("Invalid locator letter")
            result.append(char if pair == 0 else char.lower())
        else:
            if not '0' <= char <= '9':
                raise GeoError("Invalid locator digit")
            result.append(char)
    return ''.join(result)


def _locator_edges(text):
    """Return rational south, west, north and east cell edges."""
    text = normalize_locator(text)
    west, south = Fraction(-180), Fraction(-90)
    width, height = Fraction(20), Fraction(10)
    for pair in range(len(text) // 2):
        if pair:
            divisor = 24 if pair == 2 else 10
            width /= divisor
            height /= divisor
        base = ord('A') if pair == 0 else ord('a') if pair == 2 else ord('0')
        west += (ord(text[2 * pair]) - base) * width
        south += (ord(text[2 * pair + 1]) - base) * height
    return south, west, south + height, west + width


def locator_box(text: str) -> Box:
    """Return the boundaries of the named locator cell."""
    return Box(*(float(edge) for edge in _locator_edges(text)))


def locator_center(text: str) -> Point:
    """Return the centre of the named locator cell."""
    south, west, north, east = _locator_edges(text)
    return Point(float((south + north) / 2), float((west + east) / 2))


def to_locator(lat, lon, length: int = 6) -> str:
    """Convert a coordinate to a canonical Maidenhead locator."""
    point = Point(lat, lon)
    if type(length) is not int or length not in (2, 4, 6, 8):
        raise GeoError("Invalid locator length")
    latitude = Fraction(point.lat) + 90
    longitude = Fraction(point.lon) + 180
    if abs(point.lat) == 90 or point.lon == 180:
        longitude = Fraction(0)
    if point.lat == 90:
        # Select the final cell at each precision.
        latitude = Fraction(180) - Fraction(1, 1000000)
    width, height = Fraction(20), Fraction(10)
    result = []
    for pair in range(length // 2):
        if pair:
            divisor = 24 if pair == 2 else 10
            width /= divisor
            height /= divisor
        x, y = int(longitude // width), int(latitude // height)
        longitude -= x * width
        latitude -= y * height
        base = ord('A') if pair == 0 else ord('a') if pair == 2 else ord('0')
        result.extend((chr(base + x), chr(base + y)))
    return ''.join(result)


def _point(value):
    """Resolve a point or a locator."""
    if isinstance(value, Point):
        return value
    if isinstance(value, str):
        return locator_center(value)
    raise GeoError("Expected a point or locator")


def great_circle(a, b) -> Course:
    """Return spherical distance and initial bearing between two places."""
    a, b = _point(a), _point(b)
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dphi = phi2 - phi1
    dlambda = math.radians(b.lon - a.lon)
    haversine = (math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2)
                 * math.sin(dlambda / 2) ** 2)
    haversine = max(0.0, min(1.0, haversine))
    distance = EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
    bearing = None
    if 0.001 <= distance <= math.pi * EARTH_RADIUS_KM - 0.001:
        if a.lat == 90 or b.lat == -90:
            bearing = 180.0
        elif a.lat == -90 or b.lat == 90:
            bearing = 0.0
        else:
            y = math.sin(dlambda) * math.cos(phi2)
            x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
            bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    return Course(distance, distance / KM_PER_MILE, bearing)


def _julian_day(day):
    """Julian day at midnight UTC on a Gregorian date."""
    year, month = day.year, day.month
    if month <= 2:
        year -= 1
        month += 12
    century = year // 100
    correction = 2 - century + century // 4
    return (math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1))
            + day.day + correction - 1524.5)


def _sun_position(jd):
    """Return solar declination in radians and equation of time in minutes."""
    t = (jd - 2451545) / 36525
    mean_longitude = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    anomaly = math.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    center = (math.sin(anomaly) * (1.914602 - t * (0.004817 + 0.000014 * t))
              + math.sin(2 * anomaly) * (0.019993 - 0.000101 * t)
              + math.sin(3 * anomaly) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * t)
    apparent_longitude = math.radians(mean_longitude + center - 0.00569 - 0.00478 * math.sin(omega))
    obliquity = (23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
                 + 0.00256 * math.cos(omega))
    epsilon = math.radians(obliquity)
    declination = math.asin(math.sin(epsilon) * math.sin(apparent_longitude))
    y = math.tan(epsilon / 2) ** 2
    longitude = math.radians(mean_longitude)
    equation = 4 * math.degrees(
        y * math.sin(2 * longitude) - 2 * eccentricity * math.sin(anomaly)
        + 4 * eccentricity * y * math.sin(anomaly) * math.cos(2 * longitude)
        - 0.5 * y ** 2 * math.sin(4 * longitude)
        - 1.25 * eccentricity ** 2 * math.sin(2 * anomaly))
    return declination, equation


def _hour_angle_argument(latitude, declination, zenith):
    """Return the cosine of the event's hour angle."""
    phi = math.radians(latitude)
    return (math.cos(math.radians(zenith)) / (math.cos(phi) * math.cos(declination))
            - math.tan(phi) * math.tan(declination))


def _event_minutes(jd, point, zenith, rising):
    """Calculate one event with two passes, or None at a transition."""
    current_jd = jd
    for _ in range(2):
        declination, equation = _sun_position(current_jd)
        argument = _hour_angle_argument(point.lat, declination, zenith)
        if not -1 <= argument <= 1:
            return None
        angle = math.degrees(math.acos(argument))
        if not rising:
            angle = -angle
        minutes = 720 - 4 * (point.lon + angle) - equation
        current_jd = jd + minutes / 1440
    return minutes


def _events(jd, point, zenith, light_status, dark_status):
    """Compute a pair of events and their independent threshold status."""
    declination, _ = _sun_position(jd)
    argument = _hour_angle_argument(point.lat, declination, zenith)
    if argument > 1:
        return None, None, dark_status
    if argument < -1:
        return None, None, light_status
    return (_event_minutes(jd, point, zenith, True),
            _event_minutes(jd, point, zenith, False), "normal")


def _event_time(midnight, minutes):
    """Convert minutes from midnight to a whole-second UTC datetime."""
    if minutes is None:
        return None
    return midnight + timedelta(seconds=round(minutes * 60))


def sun_times(where, day: date) -> SunTimes:
    """Calculate sun and civil twilight events for a local solar day."""
    point = _point(where)
    if type(day) is not date or not 1901 <= day.year <= 2099:
        raise GeoError("Invalid date")
    if point.lon == 180:
        point = Point(point.lat, -180)
    jd = _julian_day(day)
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    _, equation = _sun_position(jd)
    first_noon = 720 - 4 * point.lon - equation
    _, equation = _sun_position(jd + first_noon / 1440)
    noon = _event_time(midnight, 720 - 4 * point.lon - equation)
    if abs(point.lat) == 90:
        declination, _ = _sun_position(jd + 0.5)
        altitude = math.degrees(declination) * (1 if point.lat > 0 else -1)
        status = "polar_day" if altitude > -0.833 else "polar_night"
        civil = "always_light" if altitude > -6 else "always_dark"
        return SunTimes(None, None, None, None, noon, status, civil)
    rise, setting, status = _events(jd, point, 90.833, "polar_day", "polar_night")
    dawn, dusk, civil = _events(jd, point, 96, "always_light", "always_dark")
    return SunTimes(_event_time(midnight, rise), _event_time(midnight, setting),
                    _event_time(midnight, dawn), _event_time(midnight, dusk),
                    noon, status, civil)
