# Architecture decisions

## Accepted principles — 2026-11-22

- **Autonomy:** local operation and peer federation must not require a mandatory central coordinator.
- **Self-hosting:** individuals, clubs, and regional organizations can operate nodes.
- **Universal onramp:** every radio-connected node can accept and forward requests; Internet egress is an advertised optional capability.
- **Service-oriented routing:** clients request content/services, not a specific Internet exit. Prefer lowest-cost reachable eligible provider, not geographic distance alone.
- **Offline-first:** cache locally, queue durably, reconcile after partitions, and label stale data.
- **Multi-application:** mail and information retrieval are equal first-class workloads.
- **Reuse:** bpq-apps and rns-apps remain standalone; integrate through adapters.
- **Research:** federation technology is not selected until benchmarked.

## Provisional laboratory direction — 2026-11-22

This is **not** an accepted choice of permanent federation stack. For M0.5, prioritize a localhost-only independent B2F endpoint and mailbox roundtrip using kissterm and Pat. For the M1/M2 simulator, provisionally use a minimal explicit-peer IP transfer and SQLite queues to test persistence, routing and replay behavior while preserving replaceable `PeerTransport`/store interfaces.

The M0 [technology matrix](TECHNOLOGY-MATRIX.md), [versioned request envelope](REQUEST-ENVELOPE-v0.1.md), [formal JSON Schema](../schema/envelope-v0.1.schema.json), and [threat/airtime baseline](THREAT-MODEL-M0.md) are drafts to test. **No performance winner, RF deployment authorization, or interoperability guarantee is declared.** Production adoption requires measured results and a separate accepted decision record.

## Open decisions

- BPv7 versus a smaller transfer envelope.
- libp2p versus simpler peer discovery for IP links.
- RF service announcements and packet-size constraints.
- Callsign verification, portable identity, mailbox delegation and revocation.
- Provider ranking and advertisement expiration.
- Application provider API and cache sharing.
- Winlink client compatibility boundaries.

## Non-negotiable limitations

No system can fetch fresh Internet data from a partition with no egress or usable cached data. Delivery across disconnected partitions cannot be guaranteed without eventual connectivity. RF cryptography and content require jurisdiction-specific review. Internet fetch services must not become unrestricted proxies.
