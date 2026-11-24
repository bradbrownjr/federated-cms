# RFC 0003 — Federated service catalog and inquiry routing (draft)

**Status:** proposed research design, not implemented or interoperable with Winlink's Query Catalog. **Date:** 2026-11-23.
**Scope:** operator-controlled provider manifests, offline catalog distribution, and requests over BPQ, RNS, and an opt-in email/B2F adapter. See [roadmap](../ROADMAP.md) and [request envelope](REQUEST-ENVELOPE-v0.1.md).

## Why a catalog?

Operators should be able to discover **what services their node or trusted neighboring nodes offer** without memorizing provider callsigns, Internet hosts, or command syntax. Each independently operated CMS node can publish its own services, retain cached descriptions while disconnected, and queue requests until a provider is reachable. No mandatory global catalog authority is assumed.

Separate **service discovery** (a catalog record), **provider trust and routing** (local policy), **request execution** (a typed handler), and **content freshness** (response provenance). An advertisement is neither permission to fetch an arbitrary URL nor proof the provider is trustworthy.

Winlink's Query Catalog is a useful UX reference, not a protocol compatibility claim. We have **not** verified how its private production catalog is populated, all supported commands, or whether unmodified Winlink Express can be pointed at an alternate catalog. Any Winlink-specific inquiry mapping is a separate evidence-gathering task.

## Logical components

1. **Provider adapters** publish operator-approved manifests for narrowly defined operations: initially synthetic `weather.forecast`, then selected existing `bpq-apps` and `rns-apps` handlers. Those applications remain independent; only a restricted adapter invokes them. A manifest never grants permission to run arbitrary code.
2. **Local catalog store** persists versioned manifests, withdrawal/tombstones, validation results, last-seen and source-peer metadata, and optional cached result provenance. It serves locally installed entries even when disconnected.
3. **Catalog exchange** moves bounded snapshots/deltas among *configured, authenticated* peers (initially IP test links). New entries are quarantined or marked untrusted until an operator policy approves the provider; synchronization cannot silently widen permissions.
4. **Service resolver** selects an eligible service instance based on trust, supported parameters and format, local/peer reachability, permitted cache freshness, quotas, and estimated link cost. Unknown service IDs fail closed. An offline node retains durable requests rather than inventing a route.
5. **Ingress adapters** present the same logical operations via a local BPQ terminal command, RNS/NomadNet UI, or a future opt-in email inquiry endpoint. They never bypass the mailbox policy or treat a callsign/email From header as authenticated identity.
6. **Dispatcher** maps a validated catalog operation into the [application request envelope v0.1](REQUEST-ENVELOPE-v0.1.md), carrying correlation/expiry/budget constraints. The catalog is *metadata*, not a new required CMS↔CMS transport or the client↔RMS B2F wire protocol.

## Manifest draft v0.1

The companion [JSON Schema](../schema/service-manifest-v0.1.schema.json) and [synthetic example](../examples/service-weather-manifest-v0.1.json) illustrate **one provider advertising one service**. All field names are provisional:

| Field | Meaning |
| --- | --- |
| `catalog_version`, `service_id`, `provider_node`, `revision` | Format version; stable typed operation identifier; claimed publisher; strictly increasing version *within that provider/service tuple* |
| `published_at`, `expires_at` | Generation time and hard discovery expiry; expiry does not imply a valid signature or reliable clock |
| `display` | Short title, category, description for ASCII/mobile catalogs |
| `request` | Operation, bounded parameter descriptions, response media type and maximum response bytes |
| `cache` | Recommended freshness TTL and whether explicitly labeled stale results may be offered |
| `access` | Advertised policy classification and suggested request rate cap—not an authorization grant |

**Identity is the tuple (`provider_node`, `service_id`)**, not the service ID alone. Multiple nodes can advertise the same service without colliding. Local policy determines whether two offerings are equivalent or interchangeable. Changes must increase `revision`; the stored digest prevents a different body from silently replacing the same revision. Withdrawal is a separate versioned **tombstone**, with retention long enough to prevent old peers resurrecting deleted offers.

The initial manifest is human-readable JSON on localhost/IP; **it is not the packet-radio encoding**. A short list view might show `WX   weather.forecast   node-bpq-1   stale/online`, while a verbose request retrieves metadata. Measure serialized, delta, and RF-equivalent byte counts before choosing a compact radio representation.

No general HTTP endpoint, arbitrary URL, API token, executable path, or shell snippet is taken from an untrusted advertisement. The parameter list is informational; authoritative validation is performed by the selected installed handler, with explicit size/time/egress limits.

## Offline exchange and trust model (proposed)

- **Bootstrap:** each operator ships local manifests and optionally approves known neighbor-node advertisements. No automatic global directory, Internet lookup, or central sign-up.
- **Sync:** peers exchange summary keys `(provider_node, service_id, revision, content_digest)`, request missing manifests/tombstones, and acknowledge durable receipt. Idempotent replays are harmless; equal-revision/different-digest records are quarantined as conflicts. Link-layer transfer details are not chosen here.
- **Expiration:** expiration removes a service from *active provider selection*; the last description can remain visible as an explicitly expired/offline item. Cached results are governed by separate per-result freshness metadata and requester policy, not the manifest TTL.
- **Partitions:** a node may expose a stale catalog snapshot for discovery but must not claim the service is currently reachable. Queued requests retain an expiry and can be matched to trusted providers after reconnection.
- **Trust:** separate authenticated peer identity, signed-manifest format, provider delegation, key rotation, revocation and origin verification are **unresolved**; an unauthenticated advertisement cannot authorize execution or remote mailbox access. The first simulation uses explicit local trust mappings, not a production PKI.
- **Abuse controls:** bounded records per peer, metadata size and response budgets, authentication/rate limits where required, loop prevention, failure backoff, malformed-delta rejection and disclosure-aware catalog visibility.

## Inquiry interfaces (all proposed, none currently deployed)

| Interface | Illustrative commands or request | Notes |
| --- | --- | --- |
| BPQ text | `SERVICES`, `SERVICE weather.forecast`, `WX FN43`, `RESULT <id>` | Extend the existing ASCII handler incrementally. Keep 1200-baud pagination/limits. |
| RNS/NomadNet | Local service list and structured `forecast(grid=FN43)` | Reuse existing page UI and independent handlers. |
| FCMS email/B2F | `CATALOG LIST`, `CATALOG UPDATE <cursor>`, `QUERY weather.forecast grid=FN43` | **Proposed FCMS inquiry syntax, not Winlink commands**. Route only to an explicitly configured local inquiry address; parse text from authorized mail ingress and send bounded asynchronous reply messages with request IDs. |
| Winlink Query Catalog | Research mapping of documented inquiry messages and updates | Test via synthetic mail fixtures before claiming that an unmodified Winlink Express catalog UI can target FCMS. Do not use production Winlink infrastructure or imply `INQUIRY@winlink.org` belongs to FCMS. |

For the email adapter, treat **addressing, sender identity, message integrity, and permitted operation as separate checks**. Restrict recipients to a configured local alias, reject attachments/oversize requests and arbitrary URLs initially, deduplicate by accepted B2 message identity plus normalized request, persist the request before returning an acceptance receipt, then dispatch using the common envelope. Replies must have a verified delivery/authorization context, safe source attribution and size limits. Never echo request content to an untrusted arbitrary reply address.

## Initial implementation sequence and acceptance tests

**C0 — Manifest contract / local registry (after review):**
- Verify the sample against the draft JSON Schema; add invalid version, duplicate key, oversized field, invalid parameter and malformed-timestamp fixtures.
- Implement a durable SQLite registry, approved local provider publication, revision/tombstone handling, pagination and a read-only `SERVICES` listing. Local restart must preserve the catalog.

**C1 — Provider integration and provider selection:**
- Wrap the deterministic weather fixture, then **one reviewed** `bpq-apps` handler and **one reviewed** `rns-apps` handler behind restricted typed adapters. Do not copy whole apps or launch arbitrary untrusted commands.
- Exercise unknown service, invalid grid, quota/size cap, a disabled provider, cache-only/stale-allowed/fresh-only selections and queueing while offline.

**C2 — Catalog federation (M1/M2):**
- Three independently configured nodes; publish, sync, expire, withdraw and reconcile manifests after a simulated partition. Check equal-revision collisions, replay, restart, untrusted offers and rollback of stale updates. Measure bytes across a low-baud-rate-equivalent link.

**C3 — Email inquiry research and adapter (M0.5/M3/M4):**
- Independently document Winlink's publicly evidenced catalog/update/inquiry formats and distinguish them from FCMS grammar. Build an isolated local B2 message inquiry adapter with no production Winlink contact.
- Feed synthetic B2 messages; verify `CATALOG LIST` and a weather query create durable, bounded, authorized replies; test malformed mail, unauthorized sender claims, duplicate MIDs, stale catalog and delayed delivery. Only then decide whether any unmodified client Query Catalog experience can be supported.

**Exit criterion:** two independent service providers can publish operator-approved manifests to a local catalog; three nodes can exchange and reconcile catalog records under partition and restart; the same weather request resolves through BPQ, RNS and a synthetic local email inquiry with visible freshness, provenance, byte budgets and access controls. **All of this remains unchecked until implemented and tested.**

## Open design decisions

Signed manifest syntax and key lifecycle; provider trust delegation; public versus private catalog scopes; binary/compact transport; catalog query cursor semantics; host routing and B2F inquiry-address conventions; acceptable email/RF authentication profile; clock failure and signed-expiry policy; handler sandboxing; exact Winlink Query Catalog interoperability.

**Related:** [architecture](ARCHITECTURE-RFC-0001.md), [authorization design](MAILBOX-AUTHORIZATION-DESIGN.md), [Winlink investigation](WINLINK-INTEROPERABILITY.md), [threat model](THREAT-MODEL-M0.md), [technology comparison](TECHNOLOGY-MATRIX.md).
