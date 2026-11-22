# M0 — Security, regulatory and airtime baseline

**Status:** preliminary engineering threat model and test budgets, 2026-11-22; **not a security audit, legal opinion, or authorization to transmit.** Applies to the proposed research prototypes, not existing Winlink infrastructure.

## Protected assets and trust boundaries

| Boundary | Data/assets at risk | Assumption permitted in M0/M1 |
|---|---|---|
| Operator/client -> BPQ, RNS or B2F edge | callsign, mailbox requests, attachments, transport credentials | Treat all client input as untrusted; callsign is not proof of account authority |
| Edge -> local CMS mailbox | queued mail, receipt state, identity mapping | Authenticate and authorize the ingress adapter; keep audit trail and identity mapping explicit |
| CMS -> peer CMS over IP | requests, routing hints, egress rights, content | M1 may use operator-configured peers in a private lab; no unauthenticated public routing |
| CMS -> RF adapter | outbound content, airtime, station ID, control information | Radio operator remains responsible for emissions; no automatic encrypted-RF path |
| Service handler -> Internet | DNS, HTTP, redirects, SMTP, API keys | Egress is explicitly authorized; client cannot turn a gateway into a generic proxy |
| Persistent database -> disk and backups | mail metadata, message content, queued state | Atomic local writes, correct sync settings, retention, physical security and recoverable backups |
| Discovery advertisements -> provider choice | false egress capability, route hijack | Advertisements are hints, not authorization or proof of service ownership |

## Threat register and test obligations

| Threat | Failure mode | Prototype guardrail | Test |
|---|---|---|---|
| Forged callsign or RNS identity binding | mailbox takeover / impersonation | No automatic cross-protocol account equality; require explicit authorization/delegation | Claim someone else's mailbox via self-reported callsign -> denied |
| Malicious or compromised CMS peer | false service advertisement, message interception, fake delivery | Operator-approved peers only in M1; peer ID bound to configured keys in future production design; receipts are scoped to acknowledged hops | Introduce unauthorized peer and bogus delivery receipts |
| Looping/replayed envelopes | exhausted RF airtime or disk | `(origin_node,id)` dedup, immutable digest checks, hop cap, expiry and per-peer quotas | Duplicate and modified-duplicate fault injection |
| Cross-partition expiry / clock drift | late side effects or indefinite queues | Bounded maximum TTL; monotonic local retry delays; detect implausible time and fail conservatively | Skew node clock and reconnect after expiry |
| Network / provider outage | lost queued mail, false success | Commit queue before durable ACK, preserve pending state across restart, distinguish accepted/provider-delivered/user-delivered | Terminate node at commit boundary and restart |
| URL-based SSRF and rebinding | access to RFC1918, loopback, link-local, cloud metadata | Restrict services to explicit operations/allowed origins; validate destination IP on every connection and redirect, DNS answers and redirects; block forbidden ranges | Redirect public URL to 127.0.0.1 or 169.254.169.254 -> denied |
| Oversized or hostile input | memory exhaustion, decompression bomb, parser crash | Bound input before parsing; cap decoded size, attachment count, nesting, timeout and mailbox quota | Send 1 MB wire payload when 16 KiB allowed -> reject early |
| Open SMTP relay / spam | reputation damage, unauthorized Internet mail | Authenticated sender delegation; domain/recipient/rate controls; signed-in operator permissions; abuse monitoring | Untrusted node requests third-party mass mail -> denied |
| RF secret leakage | passwords, API tokens or private data transmitted publicly | Redact secrets at edge and enforce RF-suitable schema/content policy | Submit token-shaped secret through simulated RF channel -> blocked/flagged |
| Client protocol confusion | Winlink client reaches unintended server or production CMS | Tests bind only to localhost, isolated addresses; never impersonate Winlink or reuse issued credentials | Network test forbids public CMS endpoints |
| Unsafe remote content | malicious URLs, executable attachments, ANSI/control-sequence injection | Render text safely, don't execute attachments; reject terminal control sequences for RF terminal output | Malformed document/control bytes -> inert output |

**Important finding from current source review:** `rns-apps/lib/rnsapps.py` uses `http_get()` with `urllib.request.urlopen(...).read()` before its callers can slice the response; a CMS adapter **must add bounded streaming reads and SSRF/redirect policy** rather than expose this helper unchanged to arbitrary federation requests. The existing page code's caching can be reused conceptually, but it is not a federated public HTTP proxy.

## US amateur-radio regulatory boundary

- [47 CFR §97.113(a)(4)](https://www.law.cornell.edu/cfr/text/47/97.113) prohibits amateur-radio messages encoded for the purpose of obscuring their meaning, subject to enumerated exceptions. Do **not** assume encrypted Internet protocol defaults can be transferred onto US amateur RF.
- [47 CFR §97.309](https://www.law.cornell.edu/cfr/text/47/97.309) governs data emissions/codes. Any future RF encoding must be documented and reviewed for the actual permitted frequencies, emissions and station operations.
- Reticulum/LXMF normally uses encryption, as documented by [LXMF](https://github.com/markqvist/LXMF); its use on an Internet/private backhaul does **not** establish suitability on US amateur RF.
- RF station identification and station-control compliance must be handled by the responsible licensed station and adapter. Review §97.119 and other applicable provisions before RF field trials.
- Compression, checksums and signatures serve purposes different from secrecy; do not equate them categorically with encryption. Compliance requires review of the **actual configuration and purpose**, not just the project's labels.
- Part 97 is jurisdiction-specific: a deployment outside the US needs local licensing and regulation review. An information service must not bypass amateur-radio restrictions on traffic or commercial use.

The RF adapter must have an **explicit operator-authorized transmit gate** in tests and deployments, with emergency stop, byte/airtime limits and logs of what was sent. No autonomous public-RF test is authorized by this research.

## Performance budget: theoretical lower bound versus measured reality

For a nominal **1200-bit/s** packet link, ideal raw payload serialization at 8 bits per byte is **150 bytes per second** if every bit were application data, with zero framing, modem, channel access, ACK, error correction, retry, turnaround or contention overhead.

| Application bytes | Absolute ideal lower bound at 1200 bit/s |
|---:|---:|
| 128 | 0.85 seconds |
| 512 | 3.41 seconds |
| 1,024 | 6.83 seconds |
| 4,096 | 27.31 seconds |
| 16,384 | 109.23 seconds |

These are **not** actual packet airtime predictions. AX.25 headers, HDLC flags/bit stuffing, KISS being a TNC host interface (not itself on-air framing), modem preamble/tails, ACKs, link setup, turnarounds, shared-channel contention, repeats and request **plus** response add significant cost. Real throughput must be measured under each mode and topology.

**HF:** do not assume one fixed baud/bit-rate conversion across VARA, PACTOR, ARDOP and other modes. Benchmark with actual mode and RF permissions; compare IP-simulation bytes with physical on-air time, including retransmits.

**IP:** no universal bitrate target is needed for M0; measure queue latency, persistence write latency, peak resident memory, per-envelope overhead and size of replay traffic. A Raspberry Pi-scale node is the intended target, but hardware requirements are not yet measured.

### Initial simulator budgets (proposals, not network limits)

- Complete JSON logical envelope: <= 16 KiB; response **content** default <= 1 KiB and test cap <= 2 KiB.
- Peer routes: explicit list of three nodes, max 8 hops by default, hard cap 16.
- Request expiration: default 24 hours in fixture; observe expiry after partition.
- Per-peer storage, retry interval, concurrency and total disk quotas: **TBD from benchmark**, not arbitrary claims.
- Capture byte counters: (a) serialized envelope, (b) framing, (c) transfer metadata, (d) retransmission/ack, (e) total delivered content.
- For an offline weather request, the correct response may be a stale cached result with **fetched timestamp**, an error, or a queued request—not fabricated fresh information.

## Recommended validation sequence

1. Run isolated localhost B2F interop tests with no outbound Winlink connections.
2. Add malformed-envelope, duplicate, clock-skew and process-crash tests.
3. Add a URL fetch adapter only with allowlists/SSRF policy and explicit resource caps.
4. Run three-node queue/partition tests, record actual application and transfer byte counts.
5. Review the specific RF mode, license, station identification and content policies **before** any authorized on-air field test.

## Security decisions still open

Node trust roots and key distribution; peer revocation; mailbox delegation proofs; signed content vs link authentication; operator-side account recovery; encrypted storage/backups; abusive user reporting; antispam and bounce handling; retention policy; safe inter-jurisdiction routing; monitoring without exposing subscriber content.

References: [Request envelope](REQUEST-ENVELOPE-v0.1.md), [technology matrix](TECHNOLOGY-MATRIX.md), [scope](SCOPE.md), [SQLite WAL durability](https://www.sqlite.org/wal.html).
