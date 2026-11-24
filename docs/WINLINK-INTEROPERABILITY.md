# Winlink interoperability research plan

**Status:** laboratory interoperability demonstrated for the specific
clients and gateway build below; deployment compatibility remains under investigation.

## Primary question

Can an existing Winlink-compatible client exchange B2F messages with an independently operated CMS through existing radio access equipment, without using the production Winlink CMS?

## Separate interfaces

1. **Client ↔ RMS / radio access:** documented B2F/B2 message exchange, session negotiation, compression, authentication challenge, attachment and address handling. Preserve ordinary clients and RF modems.
2. **RMS ↔ CMS:** inspect actual gateway software, configuration, authorization, service dependencies, API calls and possible rerouting to independent servers. Do **not** assume this interface is the same as client B2F.
3. **Direct client ↔ CMS:** investigate Telnet sessions, server selection, client validation, authentication and alternate endpoints.
4. **Independent CMS ↔ CMS:** design federation ourselves; not necessarily Winlink protocol.
5. **Information requests:** examine how existing Winlink weather/web request messages are recognized and answered, then map them to our asynchronous service model.

## Public starting points

- [Winlink Open B2F specification](https://winlink.org/B2F): documents RMS-to-client exchange, including B2 message packaging.
- [Winlink data flow and packaging](https://winlink.org/sites/default/files/downloads/winlink_data_flow_and_data_packaging.pdf): describes callsign/SID, password challenge/response and B2F.
- [Winlink RMS Packet](https://winlink.org/content/rms_packet): documents RMS Packet's CMS and RMS Relay roles.
- [Linux RMS Gateway](https://winlink.org/content/linux_rms_gateway): identifies the independently developed gateway and source repository.
- [rmsgw source](https://github.com/nwdigitalradio/rmsgw): candidate for tracing gateway-to-CMS calls and dependencies.
- [Pat](https://getpat.io/) and [wl2k-go](https://github.com/la5nta/wl2k-go): open client and protocol implementation to study.

## M0.5 compatibility evidence

These results use synthetic accounts and disposable credentials, without RF
or production Winlink systems. They are evidence for the tested configurations,
not a claim that an installed gateway or client is supported in production.

| Interface or configuration | Evidence | Remaining limitation |
| --- | --- | --- |
| kissterm B2F client, pinned `f3310c7` → local B2F lab | Bidirectional messages, attachments and durable SQLite state tested locally; client interoperability also runs in CI. | Terminal UI and RF path not exercised. |
| Pat CLI, pinned `2ad31e8` → local B2F lab | Bidirectional message and attachment exchange tested locally and in CI. | Does not establish behavior of other client versions. |
| Both pinned clients → pinned `rmsgw` `3147f01` → test SGL adapter → B2F lab | Full isolated local chain tested with separate gateway and mailbox authentication, binary attachments, rejection cases and reopened SQLite. | Full chain is not in CI; the Pat case observed gateway SIGPIPE during teardown. Client-facing streams were simulated sockets, not AX.25. |
| Pat CLI and kissterm → virtual KISS → guest-kernel AX.25 → `ax25d` → pinned `rmsgw` → lab mailbox | Separate isolated, no-NIC QEMU guest runs exchanged synthetic messages and attachments in both directions and checked reopened SQLite state. A separate kissterm run rejected a wrong mailbox password. | Exploratory fixture is not yet a reproducible repository harness or CI job; shutdown errors were observed. No TNC or RF was used. |
| Stock installed Linux RMS gateway, TNC, RF, BPQ32 or RMS Packet deployment | Not demonstrated. | Requires controlled deployment-specific testing; no production compatibility is claimed. |
| Winlink Express | Not demonstrated. | Client behavior and authentication require separate tests. |

The gateway experiment compiles the pinned upstream session and relay code
with lab-specific paths and logging shims; it is not a stock installed binary.
See [the harness instructions](../tests/rmsgw_full/README.md) and
[gateway findings](RMS-GATEWAY-FINDINGS.md) for setup and evidence.

## Research work packages

### W1: B2F client-side compatibility
- Identify minimum handshake, login, B2 message, attachment and receipt support.
- Build a test-only server endpoint for Pat; test in a private lab.
- Record exact behaviors and discrepancies; do not infer compatibility with other clients.

### W2: Gateway-to-CMS investigation
- The pinned Linux gateway relayed bidirectional kissterm and Pat B2F exchanges to the shared lab mailbox through a test-only SGL adapter. Separate wrong-secret cases exercised gateway and client authentication. [RMS gateway findings](RMS-GATEWAY-FINDINGS.md).
- Inspect rmsgw and publicly available BPQ integration/configuration.
- Map protocol, authentication, API/endpoint dependencies, routing, server selection and failure behavior.
- Classify each dependency: documented/open, reproducible, configurable, closed, or unknown.
- Determine whether existing gateways can target an independent CMS without modifying software.

### W3: Information request handling
- Document existing Winlink email request patterns for weather and web content.
- Identify how request/response messages are dispatched and formatted.
- Prototype a B2 message-to-service-request bridge reusing bpq-apps/rns-apps.

### W4: Decision gate
- **Path A:** unmodified client and gateway supported, if independently demonstrated.
- **Path B:** unmodified client with a lightweight independent RMS adapter.
- **Path C:** client changes required, documented as a compatibility limitation.

## Definition of done

A private, repeatable test demonstrates Pat submitting and receiving a B2 message through an independently operated endpoint; a separate report establishes whether an unmodified RMS gateway can target that endpoint. No traffic is sent to or redirected from production Winlink infrastructure without authorization.

## Important distinctions

B2F being publicly documented does not imply the RMS-to-CMS API is public. A compatible client handshake does not prove production RMS compatibility. Avoid copying proprietary server code, impersonating Winlink infrastructure or relying on borrowed credentials.
