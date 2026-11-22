# Federated CMS (research)

**Status: exploratory research. No production network or protocol is claimed.**

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
- Separate Internet security from jurisdiction-specific amateur-radio requirements.

## Documents

- [Research roadmap](ROADMAP.md)
- [Architecture RFC 0001](docs/ARCHITECTURE-RFC-0001.md)
- [Technology research](docs/RESEARCH.md)
- [Architecture decisions](docs/DECISIONS.md)

## Related projects

- [bpq-apps](https://github.com/bradbrownjr/bpq-apps): packet-radio information applications and topology utilities.
- [rns-apps](https://github.com/bradbrownjr/rns-apps): Reticulum/NomadNet applications and node discovery.

Distinguish **implemented**, **demonstrated**, **proposed**, and **unverified** capabilities in all contributions.
