# SWPC fixtures

Public domain US Government data from NOAA's Space Weather Prediction
Center (`https://services.swpc.noaa.gov/`), fetched 2026-11-25 about
04:33Z with the User-Agent `fcms-research/0.1 (research)`. Used by
`tests/test_wx_format.py`; see `docs/WX-FORMATTERS.md`.

| File | Product | Trimmed |
| --- | --- | --- |
| `10cm-flux.json` | `products/summary/10cm-flux.json` | none (one entry) |
| `noaa-planetary-k-index.json` | `products/noaa-planetary-k-index.json` | last 10 of the 3-hour entries (2026-11-23 21:00 to 2026-11-25 00:00) |
| `planetary_k_index_1m.json` | `json/planetary_k_index_1m.json` | last 6 of the 357 one-minute entries (04:23 to 04:28) |
| `noaa-scales.json` | `products/noaa-scales.json` | none |
| `xrays-6-hour.json` | `json/goes/primary/xrays-6-hour.json` | last 40 of the 716 entries (04:11 to 04:30, both bands), one entry per line |
| `3-day-forecast.txt` | `text/3-day-forecast.txt` | none |
| `daily-solar-indices.txt` | `text/daily-solar-indices.txt` (issued 0225 UT 25 Nov 2026) | the 13 header lines are kept; of the 30 daily data lines only the last 6 (2026-11-19 to 2026-11-24) |

Lists are rewritten with one entry per line, which changes layout only.
