"""Parse service messages; see docs/INQUIRY-ADAPTER.md."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re


@dataclass(frozen=True)
class InquiryItem:
    kind: str
    value: str


@dataclass(frozen=True)
class RejectedLine:
    line_number: int
    text: str
    reason: str


@dataclass(frozen=True)
class InquiryRequest:
    items: tuple[InquiryItem, ...]
    rejected: tuple[RejectedLine, ...]


@dataclass(frozen=True)
class PositionReport:
    date: datetime
    latitude: float | None
    longitude: float | None
    speed: float | None
    course: tuple[int, bool] | None
    comment: str


class InquiryError(ValueError):
    pass


class PositionReportError(ValueError):
    pass


_CATALOG = re.compile(r"[A-Z0-9][A-Z0-9_.-]{0,63}\Z", re.ASCII)
_URL = re.compile(r"https?://\S+\Z", re.IGNORECASE)
_DATE = re.compile(r"[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9]{2}:[0-9]{2}\Z")
_LAT = re.compile(r"([0-9]{2})-([0-9]{2}\.[0-9]{4})([NS]?)\Z")
_LON = re.compile(r"([0-9]{3})-([0-9]{2}\.[0-9]{4})([EW]?)\Z")
_SPEED = re.compile(r"[0-9]+(?:\.[0-9]+)?\Z")
_COURSE = re.compile(r"([0-9]{1,3})([TM])\Z")
_LINES = re.compile(r"\r\n|\r|\n")


def address_local_part(address: str) -> str:
    address = address.strip()
    if address[:5].upper() == "SMTP:":
        address = address[5:]
    local = address.split("@", 1)[0].strip().upper()
    if not local:
        raise ValueError("empty local part")
    return local


def service_for(address: str) -> str | None:
    try:
        local = address_local_part(address)
    except ValueError:
        return None
    return local if local in ("INQUIRY", "QTH") else None


def parse_inquiry(subject: str, body: str) -> InquiryRequest:
    if len(body.encode("utf-8")) > 8192:
        raise InquiryError("body too large")
    if subject.strip().upper() not in ("REQUEST", "//WL2K REQUEST"):
        raise InquiryError("invalid subject")
    items = []
    rejected = []
    seen = set()
    for number, line in enumerate(_LINES.split(body), 1):
        value = line.split(";", 1)[0].strip()
        if not value:
            continue
        upper = value.upper()
        if upper == "LIST":
            item = InquiryItem("list", upper)
        elif upper == "UPDATE_CAT":
            item = InquiryItem("update_catalog", upper)
        elif upper.startswith(("HTTP://", "HTTPS://")):
            item = InquiryItem("url", value) if len(value) <= 2048 and _URL.fullmatch(value) else None
        elif _CATALOG.fullmatch(upper):
            item = InquiryItem("catalog", upper)
        else:
            item = None
        if item is None:
            rejected.append(RejectedLine(number, value[:80], "invalid item"))
        elif item not in seen:
            if len(items) == 32:
                rejected.append(RejectedLine(number, value[:80], "too many items"))
            else:
                items.append(item)
                seen.add(item)
    if not items and not rejected:
        raise InquiryError("empty request")
    return InquiryRequest(tuple(items), tuple(rejected))


def _coordinate(value: str, pattern: re.Pattern, limit: int) -> float:
    match = pattern.fullmatch(value.upper())
    if not match:
        raise PositionReportError("invalid coordinate")
    degrees, minutes, hemisphere = match.groups()
    degrees, minutes = int(degrees), float(minutes)
    if minutes >= 60 or degrees > limit or (degrees == limit and minutes) or (not hemisphere and (degrees or minutes)):
        raise PositionReportError("invalid coordinate")
    result = degrees + minutes / 60
    return -result if hemisphere in ("S", "W") else result


def parse_position_report(body: str) -> PositionReport:
    if len(body.encode("utf-8")) > 8192:
        raise PositionReportError("body too large")
    fields = {}
    for line in _LINES.split(body):
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip().upper()
        if key in fields:
            raise PositionReportError("duplicate key")
        fields[key] = value.strip()
    date = fields.get("DATE", "")
    if not _DATE.fullmatch(date):
        raise PositionReportError("invalid date")
    try:
        date = datetime.strptime(date, "%Y/%m/%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PositionReportError("invalid date") from exc
    if ("LATITUDE" in fields) != ("LONGITUDE" in fields):
        raise PositionReportError("coordinates must be paired")
    latitude = longitude = None
    if "LATITUDE" in fields:
        latitude = _coordinate(fields["LATITUDE"], _LAT, 90)
        longitude = _coordinate(fields["LONGITUDE"], _LON, 180)
    speed = None
    if "SPEED" in fields:
        if not _SPEED.fullmatch(fields["SPEED"]):
            raise PositionReportError("invalid speed")
        speed = float(fields["SPEED"])
    course = None
    if "COURSE" in fields:
        match = _COURSE.fullmatch(fields["COURSE"].upper())
        if not match or int(match[1]) > 359:
            raise PositionReportError("invalid course")
        course = (int(match[1]), match[2] == "M")
    comment = fields.get("COMMENT", "")
    if len(comment) > 80:
        raise PositionReportError("comment too long")
    return PositionReport(date, latitude, longitude, speed, course, comment)
