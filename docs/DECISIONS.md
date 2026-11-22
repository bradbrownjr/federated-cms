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
