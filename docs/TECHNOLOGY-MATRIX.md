# M0 — Federation technology decision matrix

**Status:** research comparison, **no stack selected**, 2026-11-22. This is a qualitative evaluation based on published standards, project documentation and Federated CMS requirements; it is **not** a performance benchmark or interoperability test.

## Decision to make

What should connect independently operated CMS mailbox and service providers in a way that survives partitions, supports small self-hosted deployments, does not mandate a central coordinator, and leaves existing radio clients and gateways alone?

**Separate four roles:**
1. **Edge compatibility:** B2F client or RMS integration, BPQ terminal adapters, NomadNet pages, SMTP gateways.
2. **Durable application semantics:** queues, IDs, expiry, deduplication, reply correlation, cache provenance. Proposed independently in [request envelope v0.1](REQUEST-ENVELOPE-v0.1.md).
3. **Inter-CMS transfer:** exchange queued envelopes over reachable links.
4. **Discovery and security:** choose trusted eligible peers/providers, authenticate control messages, apply per-link RF policies.

Do **not** assume a peer-discovery stack supplies durable mailboxes or that a bundle protocol supplies Winlink RMS compatibility.

## Candidate comparison

| Candidate | Natural fit | Strengths | Engineering / amateur-RF concerns | Current disposition |
|---|---|---|---|---|
| **Minimal store-and-forward + SQLite + explicit peers** | M0.5/M1/M2 IP lab, small self-hosting | Small auditable surface; directly exercises delivery invariants; no external coordinator; easy fault injection | Must design replay control, queue state, retries, identity, routing, peer sync; risks bespoke protocol debt | **Provisional M1 test harness only**; not a permanent federation decision |
| **BPv7 (RFC 9171)** | Delayed, intermittently connected CMS-to-CMS links | Published DTN standard, expiration, bundle identification, canonical CBOR structure; mature conceptual model | Bundle processing, endpoint naming and convergence layers add integration work; overhead and fragmentation on AX.25 remain unmeasured | **Benchmark for federation transport** |
| **ION-DTN / other BPv7 agent** | Mature BPv7 experimentation at gateways | Existing store-carry-forward implementation; can avoid implementing BPv7 from scratch | Operational footprint, integration with mailbox API, routing configuration and device resource use need proof on target Pi | **Prototype one CMS-to-CMS path** |
| **libp2p** | IP peer connectivity and optional discovery | Modular peer discovery/routing (mDNS, Kademlia), multiprotocol transport options, peer identifiers | Discovery requires reachable peers or bootstrap configuration; DHT/pubsub are not mail custody or store-and-forward; encrypted IP sessions do not imply RF compliance | **Candidate IP transport/discovery only** |
| **Reticulum + LXMF** | Interoperation with existing RNS ecosystem, delayed RNS messages | Native compatibility with rns-apps ecosystem; store-and-forward and identity features in LXMF | LXMF uses Reticulum encryption by default; jurisdiction/RF policy must be addressed. Identity model not Winlink callsign authentication; stack is not drop-in RMS backend | **Adapter or separately controlled backhaul**, not mandatory core |
| **UUCP-style scheduled forwarding** | Small known-peer federation, sparse scheduled contacts | Simple batch manifests, store-and-forward, can operate without constant connectivity | Routing/discovery and duplicate suppression must be built; batch schedule may add delay; historic UUCP itself is not automatically a modern secure CMS protocol | **Baseline batch comparison** |
| **Existing BPQ/JNOS BBS forwarding** | RF-access and operator deployments already in place | Widely used infrastructure; avoids replacing packet BBS or its node routing | BBS forward formats and callsign routing are not a substitute for autonomous federated CMS service discovery and B2F CMS backend | **Preserve and integrate at edges** |

### Relevant evidence (not measured performance)

- **BPv7:** [RFC 9171](https://www.rfc-editor.org/rfc/rfc9171) defines bundles, primary/canonical blocks and CBOR representation. [RFC 9172](https://www.rfc-editor.org/rfc/rfc9172) defines BP security blocks. Use the actual specifications rather than labeling any generic JSON queue as BPv7.
- **ION-DTN:** [NASA/JPL ION-DTN](https://github.com/nasa-jpl/ION-DTN) provides a publicly available DTN implementation; its integration and platform requirements must be exercised, not inferred.
- **libp2p:** [peer discovery overview](https://docs.libp2p.io/concepts/discovery-routing/overview/), [Kademlia DHT docs](https://github.com/libp2p/docs/blob/master/content/concepts/discovery-routing/kaddht.md); peer bootstrap and the IP/non-IP deployment model matter. No promise of autonomous global discovery across a physically disconnected partition.
- **Reticulum/LXMF:** [LXMF project](https://github.com/markqvist/LXMF) documents transport encryption being used by default. See RF policy review.
- **SQLite:** [WAL durability notes](https://www.sqlite.org/wal.html) document the distinction between `synchronous=FULL` and `NORMAL`; persistence must be tested with the chosen journal and sync settings.
- **Winlink:** [Open B2F](https://winlink.org/B2F) concerns the client exchange; no conclusion about the undocumented/independently implemented RMS-to-CMS interface follows from this table.

## Requirements and trade-off scoring

Scores here are deliberately **not numerical**: no representative workloads have been benchmarked. Check candidates against the same functional gates:

| Requirement | Hard gate? | Verification |
|---|---|---|
| No mandatory global server in isolated mode | Yes | Disconnect all Internet; local acceptance/reading remain available |
| Durable acceptance after process failure | Yes | Kill/restart a node after ack and check queue recovery |
| Controlled duplicates and idempotent effect | Yes | Replay identical request plus altered-ID-collision test |
| Operator-chosen peer bootstrap | Yes | Two fresh nodes connect using manually configured peers |
| Transport-neutral B2F integration | Yes | B2F adapter writes an ordinary mailbox object without requiring a particular federation transport |
| Stale content provenance | Yes | Partition upstream and verify source, fetched timestamp and stale marker |
| Runs on modest small-club hardware | Yes for target | Memory, CPU, disk-writes and power-loss recovery on an actual Raspberry Pi model |
| Efficient over packet RF | Required eventually | Measure bytes, airtime, retransmits; no overhead estimates presented as evidence |
| Automatic provider discovery | Desired | TTL and trust validation; stale/false advertisement fault tests |
| Standards interoperability | Desired | Independent implementation exchange using a documented public protocol |

## Proposed comparative experiments (M0 -> M2)

**Experiment T1 — common message corpus.** Generate representative 128 B, 512 B, 1 KiB, 4 KiB and 16 KiB *application payloads*; also a weather request and full response. Measure **total transferred bytes**, metadata overhead and ACK/retry traffic separately for each candidate. Report both raw wire size and application content size.

**Experiment T2 — sparse topology.** A (no Internet) -> B (relay) -> C (Internet provider). Fixed 10-minute partition followed by recovery; inject loss, duplicate transfers, B restart and clock skew. Measure p50/p95 delivery when connected and completion after reconnection, not just throughput.

**Experiment T3 — resource profile.** Raspberry Pi or equivalent constrained VM, SQLite queue with crash recovery; inspect startup RAM, peak memory, database growth per 10,000 small envelopes, CPU per message, and durable ACK latency.

**Experiment T4 — peer control and threat cases.** Unknown peer, malicious service advertisement, expired/replayed request, redirecting SSRF URL, bulk flood, forged callsign-to-key mapping. Expect denial or resource budget limits.

**Experiment T5 — compatibility isolation.** Same B2F client/RMS-facing test harness for all backends. A backend fails the gate if selecting it forces a rewrite of existing radio client packet framing.

## M0 baseline recommendation (reversible)

Start M0.5 with a localhost-only B2F server and a persisted mailbox. For **M1**, use **Python + SQLite + a minimal explicit-peer IP relay** to demonstrate transport-neutral application semantics before layering in BPv7/ION, libp2p or LXMF. This is a **lab implementation recommendation**, *not* an architecture decision to invent a permanent custom federation protocol.

Build interfaces (`MessageStore`, `PeerTransport`, `PeerDirectory`, `ServiceHandler`, `MailboxAdapter`) so M1 can replace its peer transport and retain the same envelopes and tests. For IP, operator-configured peers are a viable seed list; a central discovery server must never be mandatory. RF support is **not** implied by the lab transport.

## Explicit RF/IP split

- US amateur transmissions must be examined under [47 CFR 97.113(a)(4)](https://www.law.cornell.edu/cfr/text/47/97.113) (messages encoded for the purpose of obscuring meaning) and [97.309](https://www.law.cornell.edu/cfr/text/47/97.309) (data-emission codes). **Do not deploy an encrypted LXMF or libp2p hop on amateur RF merely because it operates on IP.**
- RF messages should be publicly documented/decodable as needed; transport authentication and signatures need jurisdiction-specific review and published encoding. Compression is not inherently equivalent to secrecy, but choices require technical/regulatory review.
- TLS/mTLS and normal Internet-side access control are appropriate design candidates for private IP transport, **not a blanket permission for encrypted RF**.
- Callsigns identify operators on air; do not automatically treat a self-reported callsign as an authenticated federated account or delegated mailbox provider.

See [risk and performance baseline](THREAT-MODEL-M0.md) for abuse controls and a theoretical airtime floor.

## Decision gates

**Gate G0 (now):** request semantics and interface boundaries documented; qualitative matrix ready. **This does not satisfy completed M0**, because tests and measurements remain outstanding.

**Gate G1 (after M0.5):** independent B2F server interoperates with kissterm and Pat; separate RMS-to-CMS analysis documented.

**Gate G2 (after M1/M2):** test the same envelope and failure scenarios against **at least one existing DTN implementation**, compare against simple relay and batch forwarding, record resource and RF-equivalent-byte numbers. Only then accept a federation transport ADR.

## Open risks

Cross-operator trust, delegated mailbox authority, DNS/PKI outages, callsign to account verification, secure key storage, RF emissions/IDs, abuse reports and quota policies, long-offline time validation, network partition conflict resolution, Internet SMTP reputation and regulated traffic.

**Decision status:** open. This document intentionally avoids premature selection of BPv7, libp2p, RNS or a bespoke permanent protocol.
