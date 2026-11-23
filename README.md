# Federated CMS (research)

**Status: exploratory research. No production network or protocol is claimed.**

## M1 local prototype

The repository now includes a local SQLite-backed CMS core and an ASCII-only BPQ
terminal bridge for the first M1 increment. It is intentionally not federation
or a production B2F server. A separate loopback-only B2F laboratory exchanges
synthetic messages with an isolated test mailbox. See [development setup and BPQ examples](docs/DEVELOPMENT.md).

Federated CMS investigates a decentralized, self-hostable messaging and information network for amateur radio. Nodes remain useful when isolated, discover peers without a mandatory central coordinator, forward durable requests and messages when links return, and route client information requests to reachable providers or Internet egress gateways.

CMS is used here descriptively for **Common Message Server**; this project is independent of Winlink and is not affiliated with or endorsed by its operators. Compatibility is a research goal, not an existing feature.

## Principles

- Individuals, clubs, and regions can run autonomous nodes.
- Every radio-connected node can accept and forward requests; Internet connectivity is optional.
- Clients request content and services without manually selecting an Internet exit node.
- Mail, weather, RSS, Gopher, text web, bulletins, and forms are first-class applications.
- Local caches work during partitions, with explicit source and freshness metadata.
- Durable queues, bounded retries, deduplication, and delivery status survive restarts.
- Reuse existing radio applications and protocols before building replacements.
- Offer the same text-based CMS commands to LinBPQ (Linux inetd) and Windows BPQ32 (standalone local TCP listener), including BPQ-provided callsign context.
- Separate Internet security from jurisdiction-specific amateur-radio requirements.

## Documents

- [Research roadmap](ROADMAP.md)
- [Scope and interoperability charter](docs/SCOPE.md)
- [Architecture RFC 0001](docs/ARCHITECTURE-RFC-0001.md)
- [B2F interoperability investigation](docs/WINLINK-INTEROPERABILITY.md)
- [kissterm interoperability findings](docs/KISSTERM-B2F-FINDINGS.md)
- [M0 application integration inventory](docs/M0-APPLICATION-INVENTORY.md)
- [BPQ cross-platform Telnet/SSID CMS interface (draft)](docs/BPQ-LOCAL-INTERFACE.md)
- [M0 technology comparison and benchmark plan](docs/TECHNOLOGY-MATRIX.md)
- [Request envelope v0.1 (draft)](docs/REQUEST-ENVELOPE-v0.1.md) ([JSON Schema](schema/envelope-v0.1.schema.json))
- [M0 RF/IP threat model and airtime baseline](docs/THREAT-MODEL-M0.md)
- [Linux RMS Gateway source findings](docs/RMS-GATEWAY-FINDINGS.md)
- [Mailbox identity and authorization proposal](docs/MAILBOX-AUTHORIZATION-DESIGN.md)
- [Technology research](docs/RESEARCH.md)
- [Architecture decisions](docs/DECISIONS.md)

## Related projects

- [kissterm](https://github.com/bradbrownjr/kissterm): independent packet-radio mail client and terminal with an existing B2F implementation and documented experimental-CMS interoperability tests; useful as a reference client and test harness, not a Federated CMS component.

- [bpq-apps](https://github.com/bradbrownjr/bpq-apps): packet-radio information applications and topology utilities.
- [rns-apps](https://github.com/bradbrownjr/rns-apps): Reticulum/NomadNet applications and node discovery.

Distinguish **implemented**, **demonstrated**, **proposed**, and **unverified** capabilities in all contributions.
