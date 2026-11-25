# NWS fixtures

Public domain US Government data from the National Weather Service API
(<https://api.weather.gov>), fetched 2026-11-25 with the User-Agent
`fcms-research/0.1 (research)`. Used by `tests/test_wx_format.py`; see
`docs/WX-FORMATTERS.md`.

| File | Source | Trimmed |
| --- | --- | --- |
| `points_portland_head_light.json` | `/points/43.623,-70.208` (Portland Head Light, Cape Elizabeth ME) | `@context` removed |
| `forecast_portland_head_light.json` | `/gridpoints/GYX/74,56/forecast` (the `forecast` URL from the points file), all 14 periods | `@context` and the polygon `geometry` removed |
| `alerts_ca.json` | `/alerts/active?area=CA`, 3 of the 34 features: the 1st (High Wind Warning, NWS Medford), the 17th (Wind Advisory, NWS Eureka) and the 27th (Coastal Flood Watch, NWS Eureka) | `@context` removed; per feature `geometry`, `references`, `parameters`, `@id` and `@type` removed; the other 31 features dropped |
| `alerts_none.json` | `/alerts/active?area=ME`, an empty feature collection | `@context` removed |
| `problem_invalid_point.json` | `/points/51.5,-0.12` (a point in London), HTTP 404 `InvalidPoint` | none |

Every value that remains is as served. The forecast has been superseded
by newer ones; the fixtures are snapshots.
