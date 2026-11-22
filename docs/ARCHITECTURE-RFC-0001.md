# RFC 0001 — Federated CMS research architecture

**Status:** proposed, not implemented. **Date:** 2026-11-22.
**Scope:** independently operated, federated CMS backends; preserve existing radio clients, RMS access, BPQ/JNOS nodes, BBS software and RNS applications.

## Problem

A centralized CMS service is an availability and operator-control dependency for conventional Winlink mail flow. This project asks whether autonomous operators can run compatible mailbox and information-service backends that continue working locally and exchange queued work when peers become reachable.

The **first external compatibility gate** is an existing B2F client talking to an independent endpoint. No claim is made that production Winlink RMS gateway-to-CMS traffic is documented or that existing gateways can redirect to this implementation.

## System boundaries

```text
Existing radio clients / equipment             Independent federation nodes
───────────────────────────────────             ─────────────────────────────
kissterm / Pat / other B2F clients               [CMS A: no Internet]
             │                                       │
   existing AX.25 / modem / TNC                     local mailbox
             │                                       durable outbox
     existing BPQ / RMS gateway    ◄── research ──►  service adapter
             │                                       │
      edge protocol adapter                         │ store/forward
             │                                       ▼
      local CMS service                         [CMS B: relay/cache]
             │                                       │
             └───────────────────────────────────────┤
                                                     │
                                                [CMS C: egress]
                                                     │
                                            Internet SMTP / APIs
```

Diagram shows **conceptual** access and federation paths, not proven RMS interchange. RMS-to-CMS adaptation is an unresolved interface; no obligation exists for packet nodes to run the CMS code themselves. Existing radios, nodes and page/terminal apps remain separate systems.

## Per-node components (logical interfaces, no code API selected)

| Component | Responsibility | Non-responsibility |
|---|---|---|
| **Ingress adapters** | Decode authorized B2F/BPQ/RNS/SMTP operations and map to internal mailbox/service requests | Do not independently choose unrestricted Internet egress |
| **Mailbox service** | Store B2 messages, enforce mailbox authorization, accept outbound mail for queueing | Not an interactive packet BBS replacement |
| **Durable message store** | Atomic queue, envelope, attachment metadata, dedup/tombstones, request correlation | Not route discovery |
| **Service dispatcher** | Typed weather, RSS, Gopher and other request handlers via existing app adapters | Not a shell/URL proxy to arbitrary targets |
| **Federation router** | Choose eligible peers, create hop-transfer records, retry, expire, reconcile after partitions | Not client B2F protocol or public-key identity proof |
| **Peer directory** | Operator-configured peers initially; later authenticated, expiring capability adverts | Not mandatory centralized naming |
| **Peer transport** | Transfer message/receipt bytes over supported medium | Does not change logical application semantics |
| **Egress adapters** | Authorized SMTP or information-service Internet access; annotate source/freshness | Not open relay or unchecked SSRF fetcher |
| **Policy engine** | Peer authorization, mailbox delegation, content/byte budgets and per-link restrictions | Cannot guarantee legal status in every jurisdiction |

All components are conceptual seams. The research protocol is [request envelope v0.1](REQUEST-ENVELOPE-v0.1.md), which describes **application-level** requests and responses rather than a chosen RFC/transport.

## Distinct interfaces to investigate

1. **BPQ terminal ↔ local CMS text bridge:** a BPQ `APPLICATION` command or optional dedicated AX.25 application SSID routes via `C 9 HOST` and locally configured TCP/inetd. In the user's BPQ apps configuration, the connecting station callsign can arrive as the **first input line**; retain this unverified session context while enforcing separate authorization for private mail. This is **plain text**, not binary B2F. See [local BPQ interface RFC](BPQ-LOCAL-INTERFACE.md).
2. **Client ↔ RMS / independent B2F server:** reproduce the documented B2F/B2 session, using [kissterm](https://github.com/bradbrownjr/kissterm) as an initial reference test client. Existing client support is a test result, not an assumption.
3. **RMS gateway ↔ CMS:** investigate live open-source gateway implementations such as [rmsgw](https://github.com/nwdigitalradio/rmsgw) and relevant BPQ integrations. This is **not necessarily B2F**.
4. **CMS ↔ CMS:** a separate protocol design. Keep the application envelope independent of whether transport uses BPv7, libp2p, LXMF, or a simpler forwarding connection.
5. **CMS ↔ Internet:** SMTP and narrowly authorized information providers, not a general-purpose proxy.
6. **BPQ/RNS application adapters:** reuse service handlers and caches, keep existing UIs and station identity rules intact.

See [interoperability plan](WINLINK-INTEROPERABILITY.md) and [kissterm evidence](KISSTERM-B2F-FINDINGS.md).

## Data flow: request from an isolated access node

1. Client submits service request via an available edge adapter to CMS A.
2. CMS A validates size, format and local authorization; commits a durable record; returns local acceptance status. **Not** an end-to-end delivery receipt.
3. A may serve a sufficiently fresh (or permitted stale) local cache. Otherwise it discovers an eligible trusted peer/provider.
4. Request remains queued if A has no reachable peer; A → B → C transfer occurs when connectivity permits.
5. C executes an authorized service operation or returns a typed error, with source/fetched timestamp/stale marker.
6. Response is a separate immutable envelope correlated with the original request ID, queued and forwarded back to A; A makes it retrievable by the initiating client.
7. Retries must not cause duplicate side effects; failed/expired states are visible.

**Guarantees to investigate:** durable at-least-once transfer attempts, bounded retries, deduplication, best-effort eventual delivery when links become available. No guaranteed fresh content while disconnected; no magical delivery across permanently disconnected partitions.

## Protocol layering and state

- **Application envelope:** IDs, expiry, requested service, response correlation, origin, budget and provenance. [Draft v0.1](REQUEST-ENVELOPE-v0.1.md).
- **Local durable store:** SQLite is a test baseline; restart/power-loss behavior and backups require measurement.
- **Peer transfer:** initial lab transport uses explicitly configured IP peers; replaceability is mandatory in the design, not a claim of deployed plugins.
- **Link adapters:** B2F sessions, a BPQ local TCP/inetd ASCII terminal bridge (with first-line callsign context), NomadNet pages and future HF paths remain independent. BPQ's ordinary terminal route is not binary-safe.
- **External delivery:** SMTP or existing mail transport is a privileged adapter with its own receipts and retry semantics.

## Security and regulatory boundaries

Unauthenticated callsigns, RNS hashes, email addresses and peer-advertised capabilities are **not interchangeable identities**. Trust must be established at the appropriate boundary, with explicit delegation for remote mailboxes. Restrict Internet fetch destinations, connection redirects, mail egress, attachments, quotas and resource use. RF-facing encoding, encryption, station control and traffic rules need review specific to the jurisdiction and mode.

See [M0 threat model](THREAT-MODEL-M0.md).

## Milestone gates

| Gate | Outcome required | Status |
|---|---|---|
| **M0** | App inventory, comparison matrix, request-envelope draft, threat and airtime budgets | Research documents in progress; benchmarks open |
| **M0.5** | Offline B2F server roundtrip with kissterm and Pat; separate gateway-to-CMS compatibility report | Planned |
| **M1** | Three nodes with persisted queue, async info request, cache/provenance and byte accounting | Planned |
| **M2** | Loss, partitions, duplicate delivery, restart and expiry stress tests | Planned |
| **M3–M5** | BPQ/RNS adapters, SMTP/multi-node mail, authorized RF trials | Planned |

## Decisions not made

Federation technology, standardized peer identity, authenticated delegation, signed envelope encoding, compact on-air representation, which legacy RMS can target independent servers, production SMTP policy and deployment footprint.

No transport selection should become an irreversible architecture requirement before comparative measurements. See [technology matrix](TECHNOLOGY-MATRIX.md) and [architecture decisions](DECISIONS.md).
