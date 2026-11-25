# Reply formatters for WX, ALERTS and SOLAR (`federated_cms.wx_format`)

**Status:** specification with contract tests (`tests/test_wx_format.py`).
Nothing in `src/` implements it yet.

Turns parsed NWS and SWPC responses into the plain-text reply bodies of
the `WX`, `ALERTS` and `SOLAR` services in
[INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md) (on the
`docs/interactive-services` branch). Replies go out over slow radio
links, so every reply is ASCII with lines of at most 69 characters (the
same width as [TEXT-EXTRACTOR.md](TEXT-EXTRACTOR.md)).

The module has no network, mail, storage or clock code. The robot
fetches and caches, resolves the place, and chooses the node name; this
module only formats. Time is always passed in. Inputs are never changed.
The same inputs give the same output.

## Dependencies

Python 3.11+ standard library, and `federated_cms.plaintext.to_ascii`
for every string that comes from outside (see
[TEXT-EXTRACTOR.md](TEXT-EXTRACTOR.md)).

## API

```python
from federated_cms.wx_format import (
    LINE_MAX, WxFormatError, format_wx, format_alerts, format_alert_full,
    format_solar, format_nws_error, xray_class,
)
```

`LINE_MAX` is `69`. `WxFormatError` subclasses `ValueError`, and it is
the only exception these functions raise for bad input.

```python
format_wx(points: dict, forecast: dict, *, place: str, node: str,
          now: datetime, full: bool = False) -> str
format_alerts(alerts: dict, *, place: str, node: str,
              checked_at: datetime, limit: int = 5) -> str
format_alert_full(alerts: dict, number: int, *, place: str, node: str,
                  checked_at: datetime) -> str
format_solar(*, fetched_at: datetime, now: datetime,
             flux: list | None = None, kp_3h: list | None = None,
             kp_1m: list | None = None, scales: dict | None = None,
             xray: list | None = None,
             forecast_3day: str | None = None) -> str
format_nws_error(service: str, problem: dict | None, *, place: str,
                 node: str, cached: str | None = None,
                 cached_at: datetime | None = None,
                 now: datetime | None = None) -> str
xray_class(flux: int | float) -> str
```

`points`, `forecast`, `alerts`, `problem` and the SWPC arguments are the
JSON documents already parsed (`json.loads`). `place` is a label the
robot can also use as a command argument (`Waterboro ME`, `43.6,-70.7`,
`MEZ023`). `node` is the sysop's node name (`fcms.example.net`). All
`datetime` arguments must be timezone-aware, any zone.

## Rules for every function

1. **Result.** A `str` of ASCII only (0x20 to 0x7E and `\n`), every
   line at most 69 characters, no trailing spaces, no `\r` or tab, ending
   in exactly one `\n`, no leading blank line and no blank line at the
   end. Blank lines inside appear only where stated below.
2. **Cleaning.** Every string taken from an argument or a response
   (`place`, `node`, forecast text, event names, sender names, and so on)
   goes through `plaintext.to_ascii`, then every run of whitespace,
   newlines included, becomes one space, then the ends are stripped.
   Call this *clean*. `place` and `node` must be strings that are not
   empty after cleaning.
3. **Wrap.** Text is wrapped to 69 characters exactly as
   `textwrap.wrap(text, 69, initial_indent=first, subsequent_indent=rest,
   break_long_words=True, break_on_hyphens=False)` does, on clean text.
   The indents count toward the 69. Words are split only at spaces; a
   word longer than a line is broken at the line width, so nothing is
   dropped.
4. **Cut** (`text`, `limit`). When `len(text) <= limit` the text is
   unchanged. Otherwise let `window = text[:limit - 2]`; if `window`
   contains a space, the result is `window` up to its last space, else
   `text[:limit - 3]`; then trailing spaces are removed and `...` is
   added. The result is at most `limit` characters and ends at a word
   boundary when the text has one.
5. **Fit.** A header line, an error line and the `Source:` line are never
   wrapped. When one is longer than 69 characters, its variable part
   (`place`, or `node` in the `Source:` line) is shortened: with `excess`
   the characters over 69, `part[:len(part) - excess - 3]`, trailing
   spaces removed, plus `...`. Other lines that carry free text wrap as
   stated below.
6. **Times.** *Z-stamp* is the instant in UTC as `YYYY-MM-DD HHMMZ`
   (`2026-11-25 0653Z`). *Local stamp* is the wall-clock time of the
   value's own offset as `YYYY-MM-DD HH:MM +HH:MM`
   (`2026-11-24 22:00 -08:00`). A timestamp string from a response is
   parsed with `datetime.fromisoformat` and must carry an offset. The one
   exception is SWPC `time_tag` values, which have none and are UTC.
   This module has no time zone database, so it prints numeric offsets,
   not `PST`.
7. **Age.** `minutes = max(0, floor((now - then) / 60 s))`. Under 120:
   `N min`. Under 2,880: `N h` (`minutes // 60`). Otherwise `N d`
   (`minutes // 1440`).
8. **Bad input.** A value that is absent or `null` takes the default
   named below. A value that is present but has the wrong type, cannot
   be parsed, or breaks a stated rule raises `WxFormatError`. Keys that
   are not named here are ignored at every level, so NWS and SWPC can
   add fields. `bool` is never a number. A `datetime` that is not
   timezone-aware raises `WxFormatError`, as does a `place` or `node` that
   is not a string or is empty after cleaning.
9. **Cost.** Output size depends only on the stated limits, not on the
   size of the input. An input of a million characters, or a list of a
   hundred thousand entries, formats in well under a second.
10. **Attribution.** WX and ALERTS replies, except the one-line
    no-alerts reply, end with these two lines (`NODE` is the clean
    `node`, fitted per rule 5):

    ```text
    Source: NWS via NODE
    Text is shortened or reformatted. Not official NWS text.
    ```

    The NWS disclaimer forbids presenting modified content as official
    (<https://www.weather.gov/disclaimer>), and the design asks for
    `Source: NWS via <node>`. SOLAR ends with `Source: NOAA SWPC`.

## WX: `format_wx`

Inputs are the `/points/{lat},{lon}` response and the response from the
`forecast` URL it names (the NWS web service documentation,
<https://www.weather.gov/documentation/services-web-api>). Fixtures:
`tests/fixtures/nws/points_portland_head_light.json` and
`forecast_portland_head_light.json`.

Used fields:

| Field | Use | When absent |
| --- | --- | --- |
| `points.properties.cwa`, else `gridId` (when `cwa` is absent or `null`) | Office, must match `[A-Z]{3,4}` entirely | error |
| `points.properties.forecastZone` | Zone ID, the last path segment (the whole value when it has no `/`). Any value that is not a string ending in a match of `[A-Z]{2}Z\d{3}` is ignored, with no error | no zone line |
| `points.properties.relativeLocation.properties.city`, `state` | After `near`; a value that is present and not a string raises | left out |
| `forecast.properties.updateTime`, else `generatedAt` (when `updateTime` is absent or `null`) | Issue time | `upd n/a` |
| `forecast.properties.periods` | A list of objects, kept in the order given | error |
| period `name` | Clean, upper-cased, must not be empty | error |
| period `detailedForecast`, else `shortForecast` | The text | `n/a` |
| period `endTime` | A period whose `endTime` is at or before `now` is dropped | kept |

Every period is validated, whether or not it is shown or has expired:
one bad period raises `WxFormatError`. The current periods are the ones
left after dropping. The forecast cache lasts an hour, so a reply may be built from a forecast that has
already started. If the current periods are empty, there is no menu and
the body is the single line `No current forecast periods. NWS data may
be out of date.` after the header and zone line.

Layout of the short reply (`full=False`), from the fixture with
`place="Portland Head Light"`, `node="fcms.example.net"` and `now` at
2026-11-25 13:45Z:

```text
WX Portland Head Light NWS GYX  upd 2026-11-25 0653Z
Zone MEZ024 near Portland ME
TODAY: Mostly sunny, with a high near 43. East wind 0 to 5 mph.
TONIGHT: Mostly cloudy, with a low around 26. North wind 0 to 5 mph.
THANKSGIVING DAY: Mostly cloudy, with a high near 46. Northeast wind
  5 to 15 mph, with gusts as high as 30 mph.
1. Full forecast (14 periods)
2. Alerts for this place
Source: NWS via fcms.example.net
Text is shortened or reformatted. Not official NWS text.
```

1. Header: `WX {place} NWS {office}  upd {Z-stamp of the issue time}` (two
   spaces before `upd`), fitted per rule 5.
2. Zone line: `Zone {ID}`, then ` near {city}` and ` {state}` when they
   exist (a state without a city is not shown), wrapped with a two-space
   continuation indent. Left out when there is no valid zone ID.
3. Periods: the first three current periods. Each is
   `{NAME}: {text}`, wrapped with a two-space continuation indent.
4. Menu: `1. Full forecast (N periods)` where N is the number of current
   periods (`1 period` when N is 1), then `2. Alerts for this place`.
   The menu code and the commands are the menu layer's business, not
   this module's.
5. Attribution (rule 10).

`full=True` is the same without the menu: header, zone line, every
current period, attribution.

## ALERTS: `format_alerts` and `format_alert_full`

Input is the `/alerts/active` response (a GeoJSON feature collection).
Fixtures: `alerts_ca.json` (three alerts, trimmed from the live
`area=CA` response), `alerts_none.json`.

`alerts.features` must be a list of objects, each with a `properties`
object. A feature whose `properties.status` is present and is not
`Actual` (Test, Exercise, System, Draft) is skipped and counts for
nothing; it is not validated further. Every other feature needs a
non-empty `event`. API order is kept.

Used fields: `event`, `severity`, `senderName`, `ends`, `expires`,
`description`, `instruction`. The date is `ends`, the expected end of
the event, when it is not `null`, else `expires`, the expiry of the
message. They differ: in the Coastal Flood Watch fixture `expires` is
a day before `ends`, and the NWS headline says "until" the `ends` time.
(OpenAPI document, `https://api.weather.gov/openapi.json`, schema
`Alert`: `ends` is "the expected end time of the subject event",
`expires` is "the expiry time of the information of the alert
message".) The design said "expiry"; this is a deliberate change.

**None active** (no features left after skipping): exactly one line,
without attribution, with `checked_at` in UTC:

```text
ALERTS ME: no active alerts (checked 04:33Z).
```

**Short reply** (`format_alerts`), `limit` an integer from 1 to 20
(`WxFormatError` otherwise), default 5. With `n` alerts left, the first
`min(n, limit)` get blocks:

```text
ALERTS CA: 3 active (checked 04:33Z)
1. HIGH WIND WARNING
   Severe, NWS Medford OR
   Until 2026-11-24 22:00 -08:00 (2026-11-25 0600Z)
   WHAT...South winds 30 to 40 mph with gusts up to 60 mph.
   WHERE...Most of the Shasta Valley in central Siskiyou County. This
   includes portions of Interstate 5 as well as the communities of
   Weed, Gazelle, Grenada and Montague. WHEN...Until 10 PM PST this
   evening. IMPACTS...Damaging winds could blow down trees, power
   lines, and unsecured objects. Power outages are possible. Travel
   could be...
   ACTION: Winds this strong can make driving difficult, especially
   for high profile vehicles. Use extra caution. People are urged to
   secure loose objects,...
2. ...
Read one in full:
1. ALERTS CA | 1
2. ALERTS CA | 2
3. ALERTS CA | 3
Source: NWS via fcms.example.net
Text is shortened or reformatted. Not official NWS text.
```

1. Header: `ALERTS {place}: {n} active (checked {HH:MM}Z)`, fitted per rule 5.
   `checked_at` is converted to UTC.
2. When `n > limit`, the next line is `Showing the first {limit} of {n}.`
3. Block `k` (1-based):
   - `{k}. {EVENT}`, the clean event upper-cased, wrapped with a
     three-space continuation indent;
   - `   {severity}, {senderName}` (three-space indent, wrapped the same
     way), with whichever is missing left out, and no line when both are
     missing;
   - `   Until {local stamp} ({Z-stamp})` of the date above, or
     `   Until n/a` when both `ends` and `expires` are absent or `null`;
   - the description: split into paragraphs at blank lines (lines that
     are empty or only whitespace), each paragraph clean, a leading `* `
     removed, the paragraphs joined with one space, then cut to 400,
     then wrapped with a three-space indent. No line when it is empty or
     absent. The bullets of NWS descriptions (`* WHAT...`) become
     `WHAT...`;
   - `   ACTION: {instruction}`, built the same way, cut to 150 (the cut
     applies to the instruction alone, not the prefix), wrapped with a
     three-space indent. No line when it is empty or absent.
4. `Read one in full:`, then one line per block, `{k}. ALERTS {place} |
   {k}`, with the clean place (never shortened), wrapped with a
   three-space continuation indent. The design's menu rule prints the
   command under the option when it is not `VERB <label>`; here the
   option is the block number and the command is the line.
5. Attribution (rule 10).

**Full alert** (`format_alert_full(alerts, number, ...)`): `number` is the
1-based position among the alerts that were not skipped (`WxFormatError`
for a `bool`, a non-integer, or a position out of range). Nothing is
cut and nothing is indented. The event, the severity line and the `Until`
line are wrapped to 69 like any other text:

```text
ALERTS CA: alert 1 of 3 (checked 04:33Z)
HIGH WIND WARNING
Severe, NWS Medford OR
Until 2026-11-24 22:00 -08:00 (2026-11-25 0600Z)

* WHAT...South winds 30 to 40 mph with gusts up to 60 mph.

* WHERE...Most of the Shasta Valley in central Siskiyou County. This
includes portions of Interstate 5 as well as the communities of Weed,
...
ACTION: Winds this strong can make driving difficult, especially for
high profile vehicles. Use extra caution.

People are urged to secure loose objects, ...
Source: NWS via fcms.example.net
Text is shortened or reformatted. Not official NWS text.
```

After the `Until` line, each description paragraph, then each instruction
paragraph (the first one prefixed `ACTION: `), is preceded by one blank
line and wrapped to 69 with no indent. Bullets are kept. Paragraphs are
defined as above; empty ones are dropped.

## SOLAR: `format_solar`

All arguments are keyword-only. `flux`, `kp_3h`, `kp_1m` and `xray` are
the parsed JSON lists, `scales` the parsed JSON object, and
`forecast_3day` the text, of these SWPC products under
`https://services.swpc.noaa.gov/` (design table). Fixtures are in
`tests/fixtures/swpc/`.

| Argument | Product | Fixture |
| --- | --- | --- |
| `flux` | `products/summary/10cm-flux.json` | `10cm-flux.json` |
| `kp_3h` | `products/noaa-planetary-k-index.json` | `noaa-planetary-k-index.json` |
| `kp_1m` | `json/planetary_k_index_1m.json` | `planetary_k_index_1m.json` |
| `scales` | `products/noaa-scales.json` | `noaa-scales.json` |
| `xray` | `json/goes/primary/xrays-6-hour.json` | `xrays-6-hour.json` |
| `forecast_3day` | `text/3-day-forecast.txt` | `3-day-forecast.txt` |

`fetched_at` is when the products were fetched (the oldest, when they
were fetched at different times). `now` is the time of the reply.

```text
SOLAR 2026-11-25 0433Z (data age 12 min)
SFI 88  A 15  Kp 0 (3h max 3)
X-ray B2.1  Storms now: R0 S0 G0
Forecast: Kp up to 5 (G1) on Nov 27
Source: NOAA SWPC
```

1. Line 1: `SOLAR {Z-stamp of now} (data age {age of fetched_at})`, rule 7.
2. Line 2: `SFI {sfi}  A {a}  Kp {kp}` and, when there is a 3-hour maximum,
   ` (3h max {max})`. Two spaces between the fields.
3. Line 3: `X-ray {class}  Storms now: {R} {S} {G}`.
4. Line 4: the forecast line.
5. Line 5: `Source: NOAA SWPC`.

**Partial data.** The five lines are always there, so a client or a
person can read them by position. A product that is `None`, or has no
usable entry, makes its field `n/a`: `SFI n/a`, `A n/a`, `Kp n/a`,
`X-ray n/a`, `Storms now: n/a`, `Forecast: n/a`. The `(3h max ...)`
part is left out when there is none. All six arguments `None` raises
`WxFormatError` (nothing to report). With no products usable, line 2 is
`SFI n/a  A n/a  Kp n/a`.

**Shape errors.** A list product that is not a list, `scales` that is
not an object, or `forecast_3day` that is not a string raises
`WxFormatError`. Inside a list, an entry that is not an object, or lacks
a usable value or `time_tag`, is skipped. Skipping is silent, and if no
entry is usable the field is `n/a`.

**Usable entries and the latest one.** An entry has a `time_tag` that
parses as ISO 8601 (no offset means UTC). The latest usable entry is the
one with the greatest instant, and the later one in the list on a tie.
List order is not trusted.

| Field | Entry | Usable when | Shown as |
| --- | --- | --- | --- |
| SFI | `flux` list item, `flux` | number, `0 < v < 10000` | integer when whole (`88`), else one decimal, half up |
| A | `kp_3h` item, `a_running` | an `int` (not `bool`, not a float), `0 <= v <= 999` | the integer |
| Kp | `kp_1m` item, `estimated_kp` | number, `0 <= v <= 9` | *Kp text* |
| 3h max | `kp_3h` items, `Kp` | number, `0 <= v <= 9`, `time_tag` in `[now - 24 h, now]` | *Kp text* of the largest |
| X-ray | `xray` item, `flux`, only `energy == "0.1-0.8nm"` | number, `0 < v < 1` | `xray_class` |

An item of `kp_3h` that has a usable `Kp` but no usable `a_running`
still counts for the 3-hour maximum, but not for `A`; and the reverse.
*Kp text* is the value rounded half up to one decimal from its shortest
`repr` (so `2.67` gives `2.7` and `0.05` gives `0.1`), with a trailing
`.0` removed (`3.00` gives `3`, `0.0` gives `0`).

**X-ray class.** SWPC classifies flares by peak flux in the 0.1 to 0.8 nm
band: "A" starts nominally at 1e-8 W/m2, "B" at 1e-7, "C" at 1e-6, "M" at
1e-5 and "X" at 1e-4, and the number is the flux in units of the class
base, so M5 is 5e-5 W/m2 and X10 is 1e-3 W/m2 (SWPC, "Solar Flares (Radio
Blackouts)",
<https://www.swpc.noaa.gov/phenomena/solar-flares-radio-blackouts>).
`xray_class(flux)` picks the largest base that is at most `flux`
(`A` for anything below 1e-7), divides, and **truncates** to one decimal,
never rounds, so 1.25e-7 is `B1.2` and 9.99e-7 is `B9.9`. The division
uses `decimal.Decimal(repr(flux))`, so 3e-6 is exactly `C3.0`. There is
no upper class: 2.85e-3 is `X28.5`. Below 1e-8 the letter is `A` with a
number under 1 (`A0.5`). Anything else (`bool`, a non-number, `nan`,
infinity, `flux <= 0`, `flux >= 1`) raises `WxFormatError`. The `flux`
field is used, not `observed_flux`; the difference is the electron
correction in the SWPC file. Which one SWPC plots is UNVERIFIED.

**Storms now.** `scales["0"]` is the current observation. Each of
`R`, `S` and `G` is shown as the letter and `["Scale"]` when that is a
string of one ASCII digit 0 to 5 (`R0`, `G1`), and as the letter and `?`
(`G?`) when the value is missing, `null` or anything else. When
`scales` has no `"0"` object, the field is `n/a`. The forecast days
(`"1"` to `"3"`) are not used.

**Forecast line.** From `forecast_3day`, the line is `Forecast: Kp up
to {Kp text} ({G}) on {date}`. The table that follows the first line
that starts (after spaces) with `NOAA Kp index breakdown` is read: the
first non-blank line after it is the date header, which must be exactly
three dates like `Nov 25` separated by spaces; the rows are the lines
after that which match `^\d\d-\d\dUT` followed by exactly three cells,
each a decimal Kp value of 9 or less and an optional ` (G1)` to
`(G5)`; other lines are ignored, and reading stops at the first blank
line after the first row. `\r\n` line endings are accepted. The largest
value is used; on a tie, the earliest column, then the earliest row.
` ({G})` is shown only when that cell has the annotation. With no
header or no rows the line is `Forecast: n/a`.

## Errors: `format_nws_error`

`service` is `"WX"` or `"ALERTS"` (anything else raises
`WxFormatError`). `problem` is the parsed `application/problem+json`
body (RFC 7807), or `None` when there was no response (timeout). The
NWS body fields used are `type` and `status`; `title` and `detail` are
not shown, because they repeat the request and carry text from outside.
The first matching row applies:

| Condition | Reply (`{S}` the service, `{P}` the clean place) |
| --- | --- |
| `type` ends in `/InvalidPoint` | `{S} {P}: NWS covers the US only.` then `Try a place in the US or its territories.` |
| `status == 404`, or `type` ends in `/NotFound` | `{S} {P}: NWS has no data for that place.` |
| `problem is None`, or `status >= 500` | `{S} {P}: NWS unavailable, try later.` |
| any other object | `{S} {P}: NWS refused the request ({status}).`, or `{S} {P}: NWS could not answer.` when `status` is not an integer |

The first line is fitted per rule 5. Error replies have no attribution
line. `type` and `status` of the wrong type count as absent. `problem`
that is neither a dict nor `None` raises `WxFormatError`.

Observed bodies: `tests/fixtures/nws/problem_invalid_point.json` (a
point in London) and the 404 `NotFound` body for a malformed zone ID in
the tests. The design asks for "NWS unavailable, try later, plus the
cached forecast with its age if one exists". For the unavailable row,
when `cached` is given it must be a reply that satisfies rule 1 (it is
checked, including the line width, the trailing spaces and the blank
lines at either end, and `WxFormatError` is raised otherwise), `cached_at` and `now`
must be given, and the reply is:

```text
WX Portland Head Light: NWS unavailable, try later.
Older reply follows, age 55 min.

<cached, unchanged>
```

`cached` is ignored for the other rows.

## Differences from the design

The design draft (INTERACTIVE-SERVICES.md) is the source, with these
changes for the maintainer to confirm:

- **Times.** There is no time zone database in this module, and an
  offset does not name a zone (`-05:00` is EST or CDT). Update and
  issue times are UTC (`upd 2026-11-25 0653Z`, not `upd ... EST`), and
  alert times show the local wall clock with its numeric offset plus the
  UTC time. The design showed `EST`.
- **`Until` is the event end** (`ends`), not the message expiry
  (`expires`); see above.
- **Alert blocks** are limited to `limit` (default 5) of the alerts, with
  a `Showing the first N of M.` line; the design printed a block for
  every alert, and a state-wide area can return dozens.
- **Alert description** drops the `* ` bullets of NWS text in the short
  reply, so a bullet does not end a line alone.
- **WX menu** has no command lines, as in the design's layout. The ALERTS
  menu prints its command lines, as the design says. The menu layer
  (not here) should decide whether the two are made alike.
- **SSN** is left out of SOLAR (see below).

## Fixtures

Public domain US Government data (NWS and NOAA SWPC), fetched
2026-11-25 with `fcms-research/0.1 (research)` as the User-Agent.
`tests/fixtures/nws/README.md` and `tests/fixtures/swpc/README.md` list
what was trimmed. Real values are kept, so the expected strings in the
tests derive from them by the rules above.

## Not decided here

- Which place label the robot passes, and how it is shortened for the
  subject line.
- The menu code, the menu footer and `MORE`, which belong to the menu
  layer.
- Fetching, caching, rate limits and the cached forecast's storage.
- A `SSN` figure. The design example shows one, but none of its
  products carries the current sunspot number (the daily indices text
  file does). Not in the layout until the design names a product.
