# Technology research register

| Candidate | Potential reuse | Questions to test | Status |
|---|---|---|---|
| BPv7 (RFC 9171) | Delay-tolerant transport | Overhead on low-rate RF, routing, convergence layers | Evaluate |
| ION-DTN / other BPv7 agents | Existing bundle processing | Storage, restart recovery, integration complexity | Evaluate |
| libp2p | IP peer discovery and transport | Bootstrap dependencies, advertisements, footprint | Evaluate |
| Reticulum / LXMF | Existing RNS ecosystem, asynchronous delivery | Identity, compatibility, amateur RF encryption rules | Optional adapter |
| LinBPQ / BBS forwarding | RF access and mail forwarding | Existing hooks and interoperability | Inspect |
| bpq-apps | Weather, RSS, Gopher, text web, forms, caches | Separate content functions from interactive UI | Inspect |
| rns-apps | NomadNet pages, node announcements, cached content | Page execution, identities, service extraction | Inspect |
| SMTP / Postfix | Internet email | Egress authorization, bounces, queue behavior | Evaluate |
| SQLite | Durable local queues and state | Concurrency, crash recovery, migrations | Prototype |

## Key research questions

1. Should BPv7 run directly over constrained RF or only between gateways with compact RF framing?
2. How do nodes learn about reachable services without a central directory?
3. How are mailbox delegation and service advertisements authorized and revoked?
4. How should service selection weight airtime, link quality, latency, caching and gateway load?
5. How are duplicate side effects prevented after retries and partitions?
6. How do text fetchers prevent SSRF, private-network access, abuse and oversized responses?
7. How should stale content, timestamps, source and cache provenance appear to users?

## References

- [RFC 9171 — Bundle Protocol Version 7](https://www.rfc-editor.org/rfc/rfc9171)
- [libp2p](https://libp2p.io/)
- [Reticulum](https://reticulum.network/)
- [bpq-apps](https://github.com/bradbrownjr/bpq-apps)
- [rns-apps](https://github.com/bradbrownjr/rns-apps)

These are candidates, not endorsements or claims of proven suitability.
