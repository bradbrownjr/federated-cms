# Research roadmap

All milestones are proposed, not completed. Prefer reproducible evidence to feature count.

## M0 — Inventory and requirements
- [ ] Inspect bpq-apps and rns-apps application interfaces and extract reusable service handlers.
- [ ] Document Winlink CMS/client interoperability boundaries without assuming undocumented behavior.
- [ ] Compare BPv7 implementations, libp2p, Reticulum/LXMF, UUCP-style forwarding, and a minimal store-and-forward implementation.
- [ ] Define RF/Internet regulatory and abuse threat models, and performance budgets for 1200-baud packet, HF and IP.
**Exit:** technology decision matrix and versioned request envelope.

## M0.5 — Winlink interoperability feasibility (next priority)
- [ ] Trace Open B2F client/RMS sessions and authentication using Pat and wl2k-go.
- [ ] Inspect Linux RMS Gateway source for actual RMS-to-CMS transport, configuration and dependencies.
- [ ] Test independent B2F endpoint with Pat in an isolated lab.
- [ ] Determine whether unmodified RMS software can use a self-hosted CMS; record blockers and alternatives.
- [ ] Map Winlink email-based information requests into a generic service-request adapter.
**Exit:** documented compatibility matrix and go/no-go recommendation for preserving existing clients and gateways. See [research plan](docs/WINLINK-INTEROPERABILITY.md).

## M1 — Three-node information-request simulation
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
