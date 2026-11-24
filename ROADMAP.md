# Research roadmap

Milestone exit gates remain open until demonstrated. Checked items denote completed research deliverables, not an implemented CMS or proven interoperability. Prefer reproducible evidence to feature count.

## M0 — Inventory and requirements
- [x] Inventory BPQ/RNS service candidates and verify selected source interfaces ([inventory](docs/M0-APPLICATION-INVENTORY.md)).
- [ ] Complete BPQ/RNS service-handler extraction, authorization review and adapter tests.
- [x] Specify cross-platform BPQ/Telnet terminal entrypoints (Linux inetd/stdio and Windows self-listening TCP), caller callsign handoff, node-command/SSID access and trust boundaries ([local interface RFC](docs/BPQ-LOCAL-INTERFACE.md)).
- [ ] Complete Winlink CMS/client interoperability boundary investigation, especially RMS-to-CMS ([research plan](docs/WINLINK-INTEROPERABILITY.md)).
- [x] Write a qualitative comparison of BPv7/ION, libp2p, Reticulum/LXMF, UUCP-style and minimal forwarding ([technology matrix](docs/TECHNOLOGY-MATRIX.md)).
- [ ] Benchmark at least one standard DTN implementation against minimal store-and-forward, including resource and actual byte costs.
- [x] Draft versioned logical request/response envelope and machine-readable schema ([v0.1 spec](docs/REQUEST-ENVELOPE-v0.1.md), [schema](schema/envelope-v0.1.schema.json)).
- [x] Draft RF/IP threat baseline and theoretical 1200-baud airtime floor ([security and airtime](docs/THREAT-MODEL-M0.md)).
- [ ] Validate jurisdiction/mode-specific RF constraints and measure HF/IP/packet performance budgets.
**Exit:** technology decision matrix and versioned request envelope are in draft; M0 remains open pending interoperability details, benchmark evidence and adapter validation.

## M0.5 — Winlink interoperability feasibility (next priority)
- [x] Inventory existing kissterm client-side B2F implementation and documented experimental-CMS download (see [findings](docs/KISSTERM-B2F-FINDINGS.md)).
- [ ] Reuse kissterm protocol fixtures to build an offline, minimal independent B2F server and test with kissterm and Pat.
- [ ] Cross-check B2F client/RMS behavior against Pat and wl2k-go.
- [x] Inspect the Linux RMS Gateway transport, configuration and dependencies. Exercise the pinned gateway with kissterm and Pat, the test-only SGL adapter, and a persistent B2F mailbox on isolated loopback. [Gateway findings](docs/RMS-GATEWAY-FINDINGS.md).
- [x] Test independent B2F endpoint with Pat in an isolated lab (synthetic messages via test-only localhost Telnet shim; authentication remains open).
- [ ] Determine whether unmodified RMS software can use a self-hosted CMS; record blockers and alternatives.
- [ ] Document Winlink Query Catalog population, update and inquiry behavior from primary/public evidence, then map supported message formats into an opt-in local service-request adapter. Distinguish FCMS inquiry syntax from Winlink wire formats ([catalog RFC](docs/SERVICE-CATALOG-RFC.md)).
**Exit:** documented compatibility matrix and go/no-go recommendation for preserving existing clients and gateways. See [research plan](docs/WINLINK-INTEROPERABILITY.md).

## M1 — Three-node information-request simulation
- [x] Prototype one shared localhost terminal session handler with both Linux inetd/stdio and Windows self-listening TCP launch modes; queue a weather request and return an authorized result or status ([BPQ interface RFC](docs/BPQ-LOCAL-INTERFACE.md)); retain separate binary B2F endpoint.
- [ ] Independently configured nodes with persistent local queues and no mandatory coordinator.
- [ ] A configured synthetic weather provider publishes a validated local service manifest and remains discoverable after restart ([catalog draft](docs/SERVICE-CATALOG-RFC.md), [schema](schema/service-manifest-v0.1.schema.json)).
- [ ] Client requests weather or RSS from a node lacking Internet.
- [ ] Discover a reachable node with Internet egress or cached content.
- [ ] Return response asynchronously with source, timestamp and freshness.
- [ ] Bound response size and measure RF-equivalent bytes.
**Exit:** reproducible demo and automated tests.

## M1.5 — Federated service catalog and offline discovery (proposed)

- [x] Draft a provider-published service manifest, offline synchronization/trust boundaries, inquiry interface mapping, and synthetic fixture ([catalog RFC](docs/SERVICE-CATALOG-RFC.md), [schema](schema/service-manifest-v0.1.schema.json), [example](examples/service-weather-manifest-v0.1.json)). This is **documentation only**, not an implemented catalog.
- [ ] Implement SQLite-backed local manifest registry, strict validation, revision and withdrawal tombstones, approvals, expiration and paginated listings.
- [ ] Expose operator-approved synthetic weather, one reviewed `bpq-apps` handler, and one reviewed `rns-apps` handler via restricted provider adapters.
- [ ] Sync bounded snapshots and deltas across three configured peers; prove replay, collision, withdrawal, restart, partition, stale-catalog and untrusted-advertisement behavior.
- [ ] Resolve a typed service request to a trusted eligible provider or cache; queue during outage, label stale results and measure discovery bytes plus RF-equivalent costs.
**Exit:** three nodes publish/reconcile approved catalog records without a mandatory central directory; all acceptance tests are reproducible. BPQ/RNS user-facing discovery is covered under M3; email inquiry ingress remains under M4.

## M2 — Partition and recovery
- [ ] Inject Internet outages, peer-link loss, and process restarts.
- [ ] Persist requests and replies; reconcile when links return.
- [ ] Verify deduplication, expiration, retries, alternate paths, and local service continuity.
**Exit:** fault-injection results with delivery, recovery and airtime metrics.

## M3 — BPQ and RNS adapters
- [ ] Validate Linux LinBPQ and Windows BPQ32 node-command and dedicated application-SSID routes; confirm callsign-first-line behavior, bridge concurrency and private-mail authorization ([BPQ interface RFC](docs/BPQ-LOCAL-INTERFACE.md)).
- [ ] BPQ terminal: submit requests, check status, retrieve results.
- [ ] NomadNet/RNS: equivalent requests through existing page interfaces.
- [ ] Present the local/peer catalog consistently in BPQ `SERVICES`/`SERVICE` commands and RNS/NomadNet pages; preserve provider identity, reachability and stale markers ([catalog RFC](docs/SERVICE-CATALOG-RFC.md)).
- [ ] Share service definitions for weather, RSS, Gopher, text web and bulletins.
- [ ] Reuse caches with explicit freshness and provenance.
**Exit:** identical request works through both client environments.

## M4 — Federated messaging and Internet mail
- [ ] Durable mailboxes, operator identity, delegated mailbox providers.
- [ ] Prototype an opt-in, isolated FCMS email/B2F inquiry address: parse bounded catalog/query messages, validate ingress permissions, persist requests, return correlated asynchronous replies; test synthetic mail before any Winlink Express compatibility claim ([catalog RFC](docs/SERVICE-CATALOG-RFC.md)).
- [ ] SMTP ingress and egress via authorized gateways.
- [ ] Idempotent at-least-once forwarding and explicit delivery states.
- [ ] Evaluate a Winlink-compatible adapter without depending on Winlink production infrastructure.
**Exit:** tested mail exchange through a partition and recovery.

## M5 — Field validation
- [ ] Authorized RF tests; compare real airtime with simulation.
- [ ] Validate distributed bootstrap, node trust, rate limits and malformed-input handling.
- [ ] Publish small-club and regional deployment/recovery guidance.
**Exit:** repeatable field-trial report and security review.
