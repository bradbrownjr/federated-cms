"""Parse service messages; see docs/INQUIRY-ADAPTER.md."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import re


@dataclass(frozen=True)
class InquiryItem:
    kind: str
    value: str
    arguments: str = ""
    choice: str | None = None


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
_LAT = re.compile(r"([0-9]{2})-([0-9]{2}\.[0-9]{1,4})([NS]?)\Z")
_LON = re.compile(r"([0-9]{3})-([0-9]{2}\.[0-9]{1,4})([EW]?)\Z")
_SPEED = re.compile(r"[0-9]+(?:\.[0-9]+)?\Z")
_COURSE = re.compile(r"([0-9]{1,3})([TM])\Z")
_LINES = re.compile(r"\r\n|\r|\n")
_ASCII_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_POSITION_KEYS = {"DATE", "LATITUDE", "LONGITUDE", "SPEED", "COURSE", "COMMENT"}


def _upper(value: str) -> str:
    """Uppercase ASCII letters without folding other characters."""
    return value.translate(_ASCII_UPPER)


def _check_body(body: str, error: type[ValueError]) -> str:
    """Check the UTF-8 size and remove a leading byte order mark."""
    if len(body) > 8192:
        raise error("body too large")
    try:
        size = len(body.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise error("invalid body") from exc
    if size > 8192:
        raise error("body too large")
    return body.removeprefix("\ufeff")


def _control(char: str) -> bool:
    """Identify control characters that must not appear in item lines."""
    return (ord(char) < 32 and char != "\t") or ord(char) == 127


def _item_text(line: str) -> str:
    """Remove comments while preserving semicolons in URLs."""
    line = line.strip(" \t")
    is_url = _upper(line).startswith(("HTTP://", "HTTPS://"))
    for index, char in enumerate(line):
        if char == ";" and (not is_url or (index and line[index - 1].isspace())):
            line = line[:index]
            break
    return line.strip(" \t")


def address_local_part(address: str) -> str:
    address = address.strip()
    if _upper(address[:5]) == "SMTP:":
        address = address[5:]
    local = _upper(address.split("@", 1)[0].strip())
    if not local:
        raise ValueError("empty local part")
    return local


def service_for(address: str) -> str | None:
    try:
        local = address_local_part(address)
    except ValueError:
        return None
    return local if local in ("INQUIRY", "QTH") else None


def _validate_services(services: set[str] | frozenset[str]) -> None:
    """Reject invalid service configuration before examining the request."""
    if not isinstance(services, (set, frozenset)):
        raise TypeError("services must be a set")
    for verb in services:
        if not isinstance(verb, str):
            raise TypeError("service verb must be a string")
        if not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,31}", verb) or verb in {"LIST", "UPDATE_CAT"}:
            raise ValueError("invalid service verb")


def _service_item(value: str, verb: str) -> tuple[InquiryItem | None, str]:
    """Parse a recognized service line and return its first error, if any."""
    if not value.isascii() or any(_control(char) for char in value):
        return None, "invalid characters"
    arguments, separator, choice = value[len(verb):].partition("|")
    arguments = " ".join(arguments.split())
    choice = " ".join(choice.split())
    if len(arguments) > 200:
        return None, "arguments too long"
    if "|" in choice:
        return None, "multiple choices"
    if separator and not choice:
        return None, "empty choice"
    if len(choice) > 80:
        return None, "choice too long"
    return InquiryItem("service", verb, arguments, choice if separator else None), ""


def parse_inquiry(subject: str, body: str, *, services: frozenset[str] = frozenset()) -> InquiryRequest:
    _validate_services(services)
    body = _check_body(body, InquiryError)
    if not subject.isascii() or _upper(subject.strip()) not in ("REQUEST", "//WL2K REQUEST"):
        raise InquiryError("invalid subject")
    items = []
    rejected = []
    seen = set()
    for number, line in enumerate(_LINES.split(body), 1):
        value = _item_text(line)
        if not value:
            continue
        upper = _upper(value)
        verb = re.split(r"[ \t|]", value, maxsplit=1)[0]
        verb = _upper(verb)
        reason = "invalid item"
        if verb in services:
            item, reason = _service_item(value, verb)
        elif not value.isascii() or any(_control(char) for char in value):
            item = None
        elif upper == "LIST":
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
            shown = "".join("?" if _control(char) else char for char in value[:80])
            rejected.append(RejectedLine(number, shown, reason))
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
    match = pattern.fullmatch(_upper(value))
    if not match:
        raise PositionReportError("invalid coordinate")
    degrees, minutes, hemisphere = match.groups()
    degrees, minutes = int(degrees), float(minutes)
    if minutes > 60:
        raise PositionReportError("invalid coordinate")
    if minutes == 60:
        degrees += 1
        minutes = 0
    if degrees > limit or (degrees == limit and minutes) or (not hemisphere and (degrees or minutes)):
        raise PositionReportError("invalid coordinate")
    result = degrees + minutes / 60
    return -result if result and hemisphere in ("S", "W") else result


def parse_position_report(body: str) -> PositionReport:
    body = _check_body(body, PositionReportError)
    fields = {}
    for line in _LINES.split(body):
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = _upper(key.strip())
        if key not in _POSITION_KEYS:
            continue
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
        if Decimal(fields["SPEED"]) > 10000:
            raise PositionReportError("invalid speed")
        speed = float(fields["SPEED"])
    course = None
    if "COURSE" in fields:
        match = _COURSE.fullmatch(_upper(fields["COURSE"]))
        if not match or int(match[1]) > 359:
            raise PositionReportError("invalid course")
        course = (int(match[1]), match[2] == "M")
    comment = fields.get("COMMENT", "")
    if len(comment) > 80:
        raise PositionReportError("comment too long")
    return PositionReport(date, latitude, longitude, speed, course, comment)
