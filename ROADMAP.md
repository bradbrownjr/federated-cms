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
- [ ] Inspect Linux RMS Gateway source for actual RMS-to-CMS transport, configuration and dependencies.
- [ ] Test independent B2F endpoint with Pat in an isolated lab.
- [ ] Determine whether unmodified RMS software can use a self-hosted CMS; record blockers and alternatives.
- [ ] Map Winlink email-based information requests into a generic service-request adapter.
**Exit:** documented compatibility matrix and go/no-go recommendation for preserving existing clients and gateways. See [research plan](docs/WINLINK-INTEROPERABILITY.md).

## M1 — Three-node information-request simulation
- [x] Prototype one shared localhost terminal session handler with both Linux inetd/stdio and Windows self-listening TCP launch modes; queue a weather request and return an authorized result or status ([BPQ interface RFC](docs/BPQ-LOCAL-INTERFACE.md)); retain separate binary B2F endpoint.
- [ ] Independently configured nodes with persistent local queues and no mandatory coordinator.
- [ ] Client requests weather or RSS from a node lacking Internet.
- [ ] Discover a reachable node with Internet egress or cached content.
- [ ] Return response asynchronously with source, timestamp and freshness.
- [ ] Bound response size and measure RF-equivalent bytes.
**Exit:** reproducible demo and automated tests.

## M2 — Partition and recovery
- [ ] Inject Internet outages, peer-link loss, and process restarts.
- [ ] Persist requests and replies; reconcile when links return.
- [ ] Verify deduplication, expiration, retries, alternate paths, and local service continuity.
**Exit:** fault-injection results with delivery, recovery and airtime metrics.

## M3 — BPQ and RNS adapters
- [ ] Validate Linux LinBPQ and Windows BPQ32 node-command and dedicated application-SSID routes; confirm callsign-first-line behavior, bridge concurrency and private-mail authorization ([BPQ interface RFC](docs/BPQ-LOCAL-INTERFACE.md)).
- [ ] BPQ terminal: submit requests, check status, retrieve results.
- [ ] NomadNet/RNS: equivalent requests through existing page interfaces.
- [ ] Share service definitions for weather, RSS, Gopher, text web and bulletins.
- [ ] Reuse caches with explicit freshness and provenance.
**Exit:** identical request works through both client environments.

## M4 — Federated messaging and Internet mail
- [ ] Durable mailboxes, operator identity, delegated mailbox providers.
- [ ] SMTP ingress and egress via authorized gateways.
- [ ] Idempotent at-least-once forwarding and explicit delivery states.
- [ ] Evaluate a Winlink-compatible adapter without depending on Winlink production infrastructure.
**Exit:** tested mail exchange through a partition and recovery.

## M5 — Field validation
- [ ] Authorized RF tests; compare real airtime with simulation.
- [ ] Validate distributed bootstrap, node trust, rate limits and malformed-input handling.
- [ ] Publish small-club and regional deployment/recovery guidance.
**Exit:** repeatable field-trial report and security review.
