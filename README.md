# Federated CMS

**Status: research and laboratory prototype; not ready for production use.**

Federated CMS is a self-hosted mail and information server for amateur radio.

- **Operator-controlled nodes.** Clubs, regions and individual operators run their own. Each node fetches web pages, weather and feeds directly, so requests don't depend on one central service being up. The node's licensee decides what it offers, within their country's amateur rules.
- **Bring your client.** Any program that speaks the published B2F protocol can connect, and accounts are by callsign.
- **Bring your modem.** B2F runs over any link that carries a reliable byte stream.
- **Reuse what you have.** BPQ, packet applications and Reticulum services plug in as information sources.

## Why

- **Client choice.** Winlink's production servers accept only client programs they recognise. [kissterm](https://github.com/bradbrownjr/kissterm), an independent B2F client, is answered "Unknown client types are not allowed on production servers -- use cms-z.winlink.org", over the Internet and through an RMS gateway alike; the test server accepts it ([kissterm roadmap](https://github.com/bradbrownjr/kissterm/blob/main/docs/ROADMAP.md)).
- **Modem choice.** Winlink gateways offer a fixed set of modes: PACTOR, ARDOP, VARA, Robust Packet, AX.25 packet and Telnet ([Winlink sysop software](https://winlink.org/node/21)). New modems keep appearing, and a self-hosted node can carry B2F over any of them.
- **Independence.** A node that fetches its own information keeps working when a central service or its hosting provider is down.

## M1 local prototype

The repository now includes a local SQLite-backed CMS core and an ASCII-only BPQ
terminal bridge for the first M1 increment. It is intentionally not federation
or a production B2F server. A separate loopback-only B2F laboratory exchanges
synthetic messages with an isolated test mailbox. See [development setup and BPQ examples](docs/DEVELOPMENT.md).

CMS is used here descriptively for **Common Message Server**; this project is independent of Winlink and is not affiliated with or endorsed by its operators. Compatibility with existing clients is tested in the lab; see the [compatibility matrix](docs/WINLINK-INTEROPERABILITY.md).

No Winlink software has been decompiled or disassembled for this project. It is built from published specifications, open-source programs and compatibility tests of unmodified clients against our own server. See [how Federated CMS works with Winlink software](docs/INDEPENDENT-IMPLEMENTATION.md).

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
- [How Federated CMS works with Winlink software](docs/INDEPENDENT-IMPLEMENTATION.md)
- [Architecture RFC 0001](docs/ARCHITECTURE-RFC-0001.md)
- [B2F interoperability investigation](docs/WINLINK-INTEROPERABILITY.md)
- [Packet radio troubleshooting](docs/PACKET-TROUBLESHOOTING.md)
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
