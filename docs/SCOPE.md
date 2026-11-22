# Scope and interoperability charter

## What this project is

Federated CMS is research toward an open, self-hostable, federated alternative to the **centrally operated Winlink Common Message Server (CMS) backend**. The intended core includes independent mailbox hosting, store-and-forward message routing, peer and service discovery, Internet mail ingress/egress, information-request routing, gateway selection, durable queues, and recovery after partitions.

Individuals, clubs and regional operators should be able to host and federate servers without a mandatory central coordinating service. Each radio-connected access node should be able to submit and forward requests; nodes advertising authorized Internet egress may fulfill them. The platform must remain useful in isolated mode.

## What this project is not

- Not a replacement for LinBPQ, BPQ BBS, JNOS, packet radio nodes, RF modems or TNCs.
- Not a new interactive BBS or competing weather/RSS/Gopher/web application suite.
- Not a fork of Winlink's proprietary CMS infrastructure.
- Not a promise of drop-in compatibility with every existing Winlink client or RMS gateway.
- Not dependent on Reticulum, libp2p, BPv7, or any one transport until evaluated.

## Integration strategy

Preserve existing BPQ/JNOS/RNS deployments and expose narrow adapters. Reuse bpq-apps and rns-apps information services instead of rebuilding them. Prioritize compatibility studies for existing Winlink message formats, clients, and gateway behavior, documenting what is publicly specified, independently interoperable, or unavailable.

## Definition of success

An operator can connect through an existing radio node, send or receive Internet mail and request formatted information through a locally or independently hosted federated CMS, without relying on the Winlink production CMS. If the first CMS has no Internet, it may route to another authorized reachable egress provider. During a partition, it preserves local operation and queues work for later reconciliation.

**Project status:** research; all compatibility and production claims require validation.
