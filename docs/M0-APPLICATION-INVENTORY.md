# M0 application integration inventory

**Status:** preliminary source inventory, 2026-11-22. Evidence is from the current repository READMEs; function signatures and deployment contracts still require code-level inspection. This is not a claim that any adapter is implemented.

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

1. Inspect `bpq-apps/apps/wx.py`, `rss-news.py`, `gopher.py`, `forms.py` for entrypoints, subprocess use, cache paths and functions separable from terminal I/O.
2. Inspect `rns-apps/pages/wx.mu`, `news.mu`, `forms.mu` and `lib/rnsapps.py` for page environment, request parameters and shared functions.
3. Determine actual license terms per repository and third-party modules before copying code; both READMEs identify rns-apps and bpq-apps as CC0, while Federated CMS is MIT.
4. Implement a single weather-service adapter behind a mocked transport and compare outputs and freshness behavior.
5. Measure rendered response sizes for 1200-baud packet and identify content caps.

## Research cautions

- A cached page is not equivalent to an authorized, remotely callable API.
- RNS page identity must not silently authorize SMTP or B2F mailbox operations.
- Remote fetchers must reject loopback, link-local and private destinations unless explicitly allowed for a trusted deployment; apply redirect checks, timeouts, response size caps and per-caller limits.
- Never transmit authentication secrets or private data in an amateur RF message merely because an IP-side service accepts them.

**M0 inventory status:** service catalog and candidate boundary documented; code-level extraction and adapter validation remain open.
