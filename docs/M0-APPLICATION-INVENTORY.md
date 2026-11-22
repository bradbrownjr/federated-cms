# M0 application integration inventory

**Status:** preliminary code-backed inventory, 2026-11-22. Evidence includes repository READMEs and selected source functions; remaining functions and deployment contracts need further inspection. No adapter has been implemented.

## Existing application boundaries

| Capability | bpq-apps | rns-apps | Federated CMS opportunity |
|---|---|---|---|
| Weather | `apps/wx.py`, `apps/wx-me.py` | `pages/wx.mu`, `pages/wx-me.mu` | Request/response service with source, freshness and bounded output |
| News / RSS | `apps/rss-news.py` | `pages/news.mu` | Fetch or serve cached headlines asynchronously |
| Gopher | `apps/gopher.py` | `pages/gopher.mu` | Bounded text/menu response; guard remote targets |
| Wikipedia / text web | `apps/wiki.py` | `pages/wiki.mu`, `pages/www.mu` | Extract content retrieval, not terminal/page navigation |
| Space weather | `apps/space.py` | `pages/space.mu` | Cached advisory service |
| Propagation | `apps/predict.py` | `pages/predict.mu` | Structured query with explicit input units |
| Forms | `apps/forms.py` | `pages/forms.mu` | Preserve message formats; dispatch over existing delivery paths |
| Mail / bulletins | Existing BPQ BBS remains separate | `pages/mail.mu`, `pages/msg.mu` use SQLite; LXMF for some delivery | Adapter only; do not merge independent mailbox systems by assumption |

Sources:
- https://github.com/bradbrownjr/bpq-apps/blob/main/README.md
- https://github.com/bradbrownjr/rns-apps/blob/main/README.md

## Verified source-level extraction candidates

- [bpq-apps `apps/wx.py`](https://github.com/bradbrownjr/bpq-apps/blob/main/apps/wx.py): `get_forecast_7day(latlon)` and `get_forecast_hourly(latlon, hours=12)` return data rather than terminal pages, making them candidate service-handler seams. `load_cache()`, `save_cache(data)`, `get_cached_local_weather()` use an adjacent JSON file with `cache_timestamp`. These functions currently do direct network or file I/O and broadly catch exceptions; adapters need dependency injection, validation, error taxonomy and size limits.
- [bpq-apps `apps/rss-news.py`](https://github.com/bradbrownjr/bpq-apps/blob/main/apps/rss-news.py): `RSSReader.fetch_feed(url)` parses RSS/Atom to article dictionaries, while `get_cached_feed(category, feed_name)` accesses a local cache. `RSSReader` also owns interactive reader state; extract a pure feed service instead of instantiating a terminal UI in the CMS. The direct `urlopen(...).read()` path is not a bounded public-federation fetcher.
- [rns-apps `pages/wx.mu`](https://github.com/bradbrownjr/rns-apps/blob/main/pages/wx.mu): `fetch(kind, point, lat, lon)` dispatches current conditions/forecasts/alerts; `REPORTS` defines distinct TTLs (current 900 s, forecasts 1800 s, alerts 300 s, AFD/HWO 3600 s). Its `render()` gathers NomadNet form values and identity and should stay in the UI adapter.
- [rns-apps `pages/news.mu`](https://github.com/bradbrownjr/rns-apps/blob/main/pages/news.mu): `parse_feed(raw)`, `fetch_feed(url)`, and `fetch_article(url)` are service extraction candidates. The page uses feed TTL 1800 s, article TTL 6 h, and a per-page presentation limit; presentation pagination is not the same as federation response-byte budget.
- [rns-apps `lib/rnsapps.py`](https://github.com/bradbrownjr/rns-apps/blob/main/lib/rnsapps.py): `cached(key, ttl, fetch)` returns exactly `(data, fetched_epoch, stale)` and falls back to older cached data when fetch fails; `cache_peek(key)` reads without network access. These are directly useful semantics for federated responses. `http_get()` uses unbounded `resp.read()` and must **not** be exposed to untrusted remote URLs without restrictions, streaming limits and redirect validation.
- Verified the root `LICENSE` file of **both** bpq-apps and rns-apps is **CC0 1.0 Universal**, while Federated CMS is MIT. Review dependencies/assets individually; do not assume third-party content inherits CC0.

**Conclusion:** there are real extraction candidates already. Reuse the parsers, cache semantics and data fetchers only behind bounded interfaces, **not** the interactive terminal/page request handlers as-is. HTTP and caller identity must be validated at the CMS boundary.

## Host and identity constraints

**BPQ:** `bpq-apps` is primarily Python applications invoked from BPQ terminal/node contexts. Its README describes ASCII-oriented 1200-baud packet use, local JSON caches and cron refresh, with 2-hour weather/news caches, 4-hour space-weather caches and 6-hour calendar caches. These are current application conventions, not guaranteed service APIs.

**RNS:** `rns-apps` executes Python-backed NomadNet micron pages via `pages/*.mu` and `lib/rnsapps.py`. Identified visitors are represented by Reticulum identity hashes; self-reported callsigns are not proof of amateur-radio identity. Its local mail/board data is SQLite and some deliveries use LXMF. Public content may be read anonymously while writing requires identification.

**Do not equate identities:** BPQ callsigns, RNS identity hashes, B2F callsigns, and SMTP addresses need explicit, independently authorized mapping. A display handle or callsign string is not an authentication credential.

## Integration principle

Federated CMS owns request identity, durable queue, routing, expiration, deduplication and response delivery. Existing apps own content acquisition and formatting. Start with a **narrow service adapter** that accepts a typed request and returns bounded text plus provenance; do not run an interactive UI session inside the federation engine.

Candidate service contract (proposal, **not yet versioned wire format**):

- Input: `service`, `operation`, typed `parameters`, `request_id`, `deadline`, caller authorization context, response byte budget.
- Output: `status`, content bytes/media type, source, fetched-at, expires-at, truncation indicator, error category.
- Service execution must be idempotent for retries or use an explicit deduplication key for side effects.

## Remaining code-level inventory work

1. Finish inspecting `bpq-apps/apps/gopher.py` and `forms.py`, then establish testable wrappers for the verified weather/RSS functions.
2. Inspect `rns-apps/pages/forms.mu` and complete parameter/identity authorization review for the verified wx/news paths.
3. Audit dependency licenses and any externally sourced data/assets before copying code (both repository root LICENSE files verified CC0).
4. Implement a single weather-service adapter behind a mocked transport and compare outputs and freshness behavior.
5. Measure rendered response sizes for 1200-baud packet and identify content caps.

## Research cautions

- A cached page is not equivalent to an authorized, remotely callable API.
- RNS page identity must not silently authorize SMTP or B2F mailbox operations.
- Remote fetchers must reject loopback, link-local and private destinations unless explicitly allowed for a trusted deployment; apply redirect checks, timeouts, response size caps and per-caller limits.
- Never transmit authentication secrets or private data in an amateur RF message merely because an IP-side service accepts them.

**M0 inventory status:** service catalog and candidate boundary documented; code-level extraction and adapter validation remain open.
