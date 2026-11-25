"""Format NWS and SWPC data for narrow plain-text replies."""

import math
import re
import textwrap
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from federated_cms.plaintext import to_ascii

LINE_MAX = 69
DISCLAIMER = 'Text is shortened or reformatted. Not official NWS text.'


class WxFormatError(ValueError):
    """Invalid formatter input."""


def _clean(value):
    """Convert external text to single-spaced ASCII."""
    if not isinstance(value, str):
        raise WxFormatError('expected text')
    return ' '.join(to_ascii(value).split())


def _label(value):
    """Validate a required label."""
    result = _clean(value)
    if not result:
        raise WxFormatError('empty label')
    return result


def _object(value):
    """Require an object."""
    if not isinstance(value, dict):
        raise WxFormatError('expected object')
    return value


def _optional_text(value):
    """Clean optional text."""
    return '' if value is None else _clean(value)


def _aware(value):
    """Require an aware datetime."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise WxFormatError('expected aware datetime')
    return value


def _parse(value, *, swpc=False):
    """Parse a timestamp, accepting naive SWPC times as UTC."""
    if not isinstance(value, str):
        raise WxFormatError('invalid timestamp')
    try:
        dt = datetime.fromisoformat(value)
        if swpc and (dt.tzinfo is None or dt.utcoffset() is None):
            dt = dt.replace(tzinfo=timezone.utc)
        return _aware(dt)
    except (ValueError, OverflowError) as exc:
        raise WxFormatError('invalid timestamp') from exc


def _z(dt):
    """Render a UTC timestamp."""
    return _aware(dt).astimezone(timezone.utc).strftime('%Y-%m-%d %H%MZ')


def _local(dt):
    """Render a local timestamp with its numeric offset."""
    s = dt.strftime('%Y-%m-%d %H:%M %z')
    return s[:-2] + ':' + s[-2:]


def _age(now, then):
    """Render the elapsed time using the specified steps."""
    minutes = max(0, int((_aware(now) - _aware(then)).total_seconds() // 60))
    if minutes < 120:
        return f'{minutes} min'
    if minutes < 2880:
        return f'{minutes // 60} h'
    return f'{minutes // 1440} d'


def _wrap(text, first='', rest=''):
    """Wrap a clean text with the specified indents."""
    return textwrap.wrap(text, LINE_MAX, initial_indent=first, subsequent_indent=rest,
                         break_long_words=True, break_on_hyphens=False)


def _fit(prefix, part, suffix):
    """Fit a line by shortening its variable part."""
    excess = len(prefix) + len(part) + len(suffix) - LINE_MAX
    if excess > 0:
        part = part[:max(0, len(part) - excess - 3)].rstrip() + '...'
    return prefix + part + suffix


def _footer(node):
    """Build NWS attribution."""
    return [_fit('Source: NWS via ', node, ''), DISCLAIMER]


def _cut(text, limit):
    """Cut text at a word boundary if it exceeds its limit."""
    if len(text) <= limit:
        return text
    window = text[:limit - 2]
    return (window.rsplit(' ', 1)[0] if ' ' in window else text[:limit - 3]).rstrip() + '...'


def format_wx(points, forecast, *, place, node, now, full=False):
    """Format a forecast and its navigation menu."""
    place, node = _label(place), _label(node)
    _aware(now)
    if not isinstance(full, bool):
        raise WxFormatError('invalid full')
    pp = _object(_object(points).get('properties'))
    fp = _object(_object(forecast).get('properties'))
    office = pp.get('cwa') if pp.get('cwa') is not None else pp.get('gridId')
    if not isinstance(office, str) or not re.fullmatch(r'[A-Z]{3,4}', office):
        raise WxFormatError('invalid office')
    issue = fp.get('updateTime') if fp.get('updateTime') is not None else fp.get('generatedAt')
    stamp = _z(_parse(issue)) if issue is not None else 'n/a'
    lines = [_fit('WX ', place, f' NWS {office}  upd {stamp}')]
    zone = pp.get('forecastZone')
    zone = zone.rsplit('/', 1)[-1] if isinstance(zone, str) else ''
    rel = pp.get('relativeLocation')
    if rel is None:
        rel = {}
    else:
        rel = _object(rel)
    loc = rel.get('properties')
    loc = {} if loc is None else _object(loc)
    city, state = _optional_text(loc.get('city')), _optional_text(loc.get('state'))
    if re.fullmatch(r'[A-Z]{2}Z[0-9]{3}', zone):
        line = 'Zone ' + zone + ((' near ' + city + (' ' + state if state else '')) if city else '')
        lines.extend(_wrap(line, rest='  '))
    periods = fp.get('periods')
    if not isinstance(periods, list):
        raise WxFormatError('invalid periods')
    current = []
    for period in periods:
        p = _object(period)
        name = _label(p.get('name')).upper()
        text = _optional_text(p.get('detailedForecast'))
        if not text:
            text = _optional_text(p.get('shortForecast')) or 'n/a'
        end = p.get('endTime')
        end = _parse(end) if end is not None else None
        if end is None or end > now:
            current.append((name, text))
    if not current:
        lines.append('No current forecast periods. NWS data may be out of date.')
    else:
        for name, text in current if full else current[:3]:
            lines.extend(_wrap(f'{name}: {text}', rest='  '))
        if not full:
            count = len(current)
            lines.append(f'1. Full forecast ({count} {"period" if count == 1 else "periods"})')
            lines.extend(_wrap(f'WX {place} | FULL', '   ', '   '))
            lines.append('2. Alerts for this place')
            lines.extend(_wrap(f'WX {place} | ALERTS', '   ', '   '))
    return '\n'.join(lines + _footer(node)) + '\n'


def _paragraphs(value):
    """Split external prose into cleaned paragraphs."""
    if value is None:
        return []
    if not isinstance(value, str):
        raise WxFormatError('invalid prose')
    return [p for chunk in re.split(r'\n\s*\n', value) if (p := _clean(chunk))]


def _alerts(data):
    """Validate and collect active alert properties."""
    features = _object(data).get('features')
    if not isinstance(features, list):
        raise WxFormatError('invalid features')
    result = []
    for feature in features:
        p = _object(_object(feature).get('properties'))
        status = p.get('status')
        if status is not None and not isinstance(status, str):
            raise WxFormatError('invalid status')
        if status is not None and status != 'Actual':
            continue
        event = _label(p.get('event')).upper()
        severity, sender = _optional_text(p.get('severity')), _optional_text(p.get('senderName'))
        end = p.get('ends') if p.get('ends') is not None else p.get('expires')
        end = _parse(end) if end is not None else None
        description, instruction = _paragraphs(p.get('description')), _paragraphs(p.get('instruction'))
        result.append((event, ', '.join(x for x in (severity, sender) if x), end, description, instruction))
    return result


def _until(end):
    """Render an alert end time."""
    return f'Until {_local(end)} ({_z(end)})' if end else 'Until n/a'


def _alert_header(place, suffix):
    """Fit an alerts header."""
    return _fit('ALERTS ', place, suffix)


def format_alerts(alerts, *, place, node, checked_at, limit=5):
    """Format the active alerts and commands for full details."""
    place, node = _label(place), _label(node)
    checked = _aware(checked_at).astimezone(timezone.utc).strftime('%H:%MZ')
    if type(limit) is not int or not 1 <= limit <= 20:
        raise WxFormatError('invalid limit')
    entries = _alerts(alerts)
    n = len(entries)
    if not n:
        return _alert_header(place, f': no active alerts (checked {checked}).') + '\n'
    lines = [_alert_header(place, f': {n} active (checked {checked})')]
    if n > limit:
        lines.append(f'Showing the first {limit} of {n}.')
    for i, (event, severity, end, desc, instr) in enumerate(entries[:limit], 1):
        lines.extend(_wrap(f'{i}. {event}', rest='   '))
        if severity:
            lines.extend(_wrap(severity, '   ', '   '))
        lines.extend(_wrap(_until(end), '   ', '   '))
        for paragraphs, prefix, maximum in ((desc, '', 400), (instr, 'ACTION: ', 150)):
            text = ' '.join(p[2:] if p.startswith('* ') else p for p in paragraphs)
            if text:
                lines.extend(_wrap(prefix + _cut(text, maximum), '   ', '   '))
    lines.append('Read one in full:')
    for i in range(1, min(n, limit) + 1):
        lines.extend(_wrap(f'{i}. ALERTS {place} | {i}', rest='   '))
    return '\n'.join(lines + _footer(node)) + '\n'


def format_alert_full(alerts, number, *, place, node, checked_at):
    """Format every paragraph of one active alert."""
    place, node = _label(place), _label(node)
    checked = _aware(checked_at).astimezone(timezone.utc).strftime('%H:%MZ')
    entries = _alerts(alerts)
    if type(number) is not int or not 1 <= number <= len(entries):
        raise WxFormatError('invalid alert number')
    event, severity, end, desc, instr = entries[number - 1]
    lines = [_alert_header(place, f': alert {number} of {len(entries)} (checked {checked})')]
    lines.extend(_wrap(event))
    if severity:
        lines.extend(_wrap(severity))
    lines.extend(_wrap(_until(end)))
    for i, paragraph in enumerate(desc + instr):
        lines.append('')
        if i == len(desc):
            paragraph = 'ACTION: ' + paragraph
        lines.extend(_wrap(paragraph))
    return '\n'.join(lines + _footer(node)) + '\n'


def _number(value, low, high, inclusive_low=False, inclusive_high=False):
    """Check the range of a finite JSON number."""
    return (type(value) in (int, float) and math.isfinite(value)
            and (value >= low if inclusive_low else value > low)
            and (value <= high if inclusive_high else value < high))


def _decimal_text(value):
    """Round a number to one decimal, removing a zero fraction."""
    result = format(Decimal(repr(value)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP), 'f')
    return result[:-2] if result.endswith('.0') else result


def xray_class(flux):
    """Classify X-ray flux using truncated decimal arithmetic."""
    if not _number(flux, 0, 1):
        raise WxFormatError('invalid X-ray flux')
    value = Decimal(repr(flux))
    for letter, base in [('X', '1e-4'), ('M', '1e-5'), ('C', '1e-6'), ('B', '1e-7'), ('A', '1e-8')]:
        divisor = Decimal(base)
        if value >= divisor or letter == 'A':
            return letter + format((value / divisor).quantize(Decimal('0.1'), rounding=ROUND_DOWN), 'f')


def _solar_time(item):
    """Get a usable SWPC time, or None."""
    if not isinstance(item, dict):
        return None
    try:
        return _parse(item.get('time_tag'), swpc=True)
    except WxFormatError:
        return None


def _latest(items, key, valid, energy=False):
    """Select the latest usable value, with later list entries winning ties."""
    best_time, best = None, None
    for item in items or []:
        dt = _solar_time(item)
        if dt is None or (energy and item.get('energy') != '0.1-0.8nm'):
            continue
        value = item.get(key)
        if valid(value) and (best_time is None or dt >= best_time):
            best_time, best = dt, value
    return best


def _ssn(text):
    """Read the most recent usable sunspot number."""
    best_date, best = None, None
    pattern = re.compile(r'^(\d{4}) (\d{2}) (\d{2})\s+[-+]?\d+\s+([-+]?\d+)(?=\s|$)', re.ASCII)
    for line in (text or '').splitlines():
        match = pattern.match(line)
        if not match:
            continue
        try:
            day = date(*map(int, match.group(1, 2, 3)))
            value = int(match.group(4))
        except ValueError:
            continue
        if 0 <= value <= 999 and (best_date is None or day >= best_date):
            best_date, best = day, value
    return 'n/a' if best is None else str(best)


def _storms(scales):
    """Render current NOAA storm scales."""
    current = (scales or {}).get('0')
    if not isinstance(current, dict):
        return 'n/a'
    values = []
    for letter in 'RSG':
        entry = current.get(letter)
        value = entry.get('Scale') if isinstance(entry, dict) else None
        values.append(letter + (value if isinstance(value, str) and value in ('0', '1', '2', '3', '4', '5') else '?'))
    return ' '.join(values)


def _forecast(text):
    """Extract the maximum cell from the first Kp forecast table."""
    lines = iter((text or '').splitlines())
    for line in lines:
        if line.lstrip().startswith('NOAA Kp index breakdown'):
            break
    else:
        return 'Forecast: n/a'
    for line in lines:
        if line.strip():
            break
    else:
        return 'Forecast: n/a'
    date_pattern = r'[A-Z][a-z]{2} [0-9]{1,2}'
    if not re.fullmatch(r'\s*' + date_pattern + r'(?:\s+' + date_pattern + r'){2}\s*', line):
        return 'Forecast: n/a'
    dates = re.findall(date_pattern, line)
    cell = r'([0-9]+(?:\.[0-9]+)?)(?:\s+\((G[1-5])\))?'
    row = re.compile(r'^\d\d-\d\dUT\s+' + cell + r'\s+' + cell + r'\s+' + cell + r'\s*$')
    best, best_col, best_g = None, None, None
    started = False
    for line in lines:
        if not line.strip():
            if started:
                break
            continue
        match = row.fullmatch(line)
        if not match:
            continue
        pairs = [(Decimal(match.group(i)), match.group(i + 1)) for i in (1, 3, 5)]
        if any(v > 9 for v, _ in pairs):
            continue
        started = True
        for col, (value, g) in enumerate(pairs):
            if best is None or value > best or (value == best and col < best_col):
                best, best_col, best_g = value, col, g
    if best is None:
        return 'Forecast: n/a'
    value = _decimal_text(float(best))
    return f'Forecast: Kp up to {value}' + (f' ({best_g})' if best_g else '') + f' on {dates[best_col]}'


def format_solar(*, fetched_at, now, flux=None, kp_3h=None, kp_1m=None,
                 scales=None, xray=None, forecast_3day=None, daily_indices=None):
    """Format the latest usable space weather observations."""
    _aware(fetched_at)
    _aware(now)
    products = (flux, kp_3h, kp_1m, scales, xray, forecast_3day, daily_indices)
    if all(x is None for x in products):
        raise WxFormatError('no products')
    for value in (flux, kp_3h, kp_1m, xray):
        if value is not None and not isinstance(value, list):
            raise WxFormatError('invalid product')
    if scales is not None and not isinstance(scales, dict):
        raise WxFormatError('invalid scales')
    for value in (forecast_3day, daily_indices):
        if value is not None and not isinstance(value, str):
            raise WxFormatError('invalid text product')
    sfi = _latest(flux, 'flux', lambda v: _number(v, 0, 10000))
    a = _latest(kp_3h, 'a_running', lambda v: type(v) is int and 0 <= v <= 999)
    kp = _latest(kp_1m, 'estimated_kp', lambda v: _number(v, 0, 9, True, True))
    xr = _latest(xray, 'flux', lambda v: _number(v, 0, 1), energy=True)
    maximum = None
    for item in kp_3h or []:
        dt = _solar_time(item)
        if dt is not None and now - timedelta(hours=24) <= dt <= now:
            value = item.get('Kp')
            if _number(value, 0, 9, True, True) and (maximum is None or value > maximum):
                maximum = value
    line = f'SFI {_decimal_text(sfi) if sfi is not None else "n/a"}  SSN {_ssn(daily_indices)}  A {a if a is not None else "n/a"}  Kp {_decimal_text(kp) if kp is not None else "n/a"}'
    if maximum is not None:
        line += f' (3h max {_decimal_text(maximum)})'
    lines = [f'SOLAR {_z(now)} (data age {_age(now, fetched_at)})', line,
             f'X-ray {xray_class(xr) if xr is not None else "n/a"}  Storms now: {_storms(scales)}',
             _forecast(forecast_3day), 'Source: NOAA SWPC', DISCLAIMER]
    return '\n'.join(lines) + '\n'


def format_nws_error(service, problem, *, place, node, cached=None, cached_at=None, now=None):
    """Format an NWS error and optionally append a validated cached reply."""
    if service not in ('WX', 'ALERTS'):
        raise WxFormatError('invalid service')
    place, node = _label(place), _label(node)
    if problem is not None and not isinstance(problem, dict):
        raise WxFormatError('invalid problem')
    kind = problem.get('type') if problem else None
    status = problem.get('status') if problem else None
    kind = kind if isinstance(kind, str) else ''
    status = status if type(status) is int else None
    extra = []
    unavailable = False
    if kind.endswith('/InvalidPoint'):
        suffix = ': NWS covers the US only.'
        extra.append('Try a place in the US or its territories.')
    elif status == 404 or kind.endswith('/NotFound'):
        suffix = ': NWS has no data for that place.'
    elif problem is None or (status is not None and status >= 500):
        suffix = ': NWS unavailable, try later.'
        unavailable = True
    elif status is not None:
        suffix = f': NWS refused the request ({status}).'
    else:
        suffix = ': NWS could not answer.'
    result = '\n'.join([_fit(service + ' ', place, suffix)] + extra) + '\n'
    if unavailable and cached is not None:
        if (not isinstance(cached, str) or not cached.endswith('\n') or cached.startswith('\n')
                or cached.endswith('\n\n') or any(len(line) > LINE_MAX or line.rstrip() != line
                    or any(not 32 <= ord(c) <= 126 for c in line) for line in cached[:-1].split('\n'))):
            raise WxFormatError('invalid cached reply')
        result += f'Older reply follows, age {_age(now, cached_at)}.\n\n' + cached
    return result
