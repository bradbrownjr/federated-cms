# Existing B2F interoperability evidence from kissterm

**Status:** historical source-backed findings. No new live Winlink tests
were performed for this document. For subsequent offline laboratory results,
see the [compatibility matrix](WINLINK-INTEROPERABILITY.md).

Source: https://github.com/bradbrownjr/kissterm

## Access to Winlink's own servers

- **The production CMS accepts only client programs it recognises,** by the
  program name in the client's SID. An independent client is refused with
  "Unknown client types are not allowed on production servers -- use
  cms-z.winlink.org", over the Internet (2026-09-28) and through an RMS
  gateway alike (WS1EC-10, 2026-10-07): the gateway makes its own connection
  to the production servers, so the client cannot get around the check by
  going over the radio.
- **The test server, `cms-z.winlink.org`, accepts the same client** with the
  same login (2026-10-02). The refusal is the client-name check alone, not
  the protocol or the login.
- **How a program becomes known is not published.** The Open B2F
  specification names no developer registry; the request goes to the Winlink
  Development Team.
- **Winlink's API needs an access key** issued to the program by a Winlink
  administrator (kissterm's RMS gateway list, 2026-09-27).
- **The CMS keyboard prompt needs no client name,** but it is a person
  reading mail, not B2F Send/Receive, and the CMS discourages automating it
  (2026-11-18).
- **No program borrows another program's SID name** to pass the check.

So Pat, kissterm or any other client works with Winlink's production servers
only once Winlink knows its name, while any B2F client can connect to a
Federated CMS node, which runs no such check. Anything a node does with
Winlink's servers as a client or gateway needs the same recognition. Plain
Internet email to `CALL@winlink.org` does not, subject to each user's accept
list. Sources: kissterm `docs/ROADMAP.md` (Blockers), `docs/GUIDE.md`,
`docs/PROTOCOL_GUIDE.md` ("Client identity").

## Reusable code and documentation

- `kissterm/winlink/b2f.py`: pure client-side B2F state machine, transport independent; handshake, proposal exchange, framing, checksums, delivery events.
- `kissterm/winlink/message.py`: B2 message serialization/parsing and attachments.
- `kissterm/winlink/lzhuf.py`: compression and decompression.
- `kissterm/winlink/secure.py`: challenge-response handling.
- `kissterm/mail/winlink_collect.py`: integration with message store and transports.
- `docs/PROTOCOL_GUIDE.md`: published specification cross-check, known differences, research questions.
- `tests/unit/test_winlink_b2f.py` and related tests: protocol fixtures.

## Documented operator observations

1. **2026-09-28:** production CMS refused `[kissterm-0.1.325-B2FHM$]` with “Unknown client types are not allowed on production servers -- use cms-z.winlink.org”.
2. **2026-10-02:** experimental CMS at `cms-z.winlink.org` accepted `[kissterm-0.1.349-B2FHM$]`, negotiated secure login, and delivered a real message with MID `ED1VSIAJZVYF`, filed by kissterm. Source: kissterm `docs/ROADMAP.md`, transcript `20261002-134724_KC1JMH_WL2K.log` as referenced there.
3. **2026-10-07:** production CMS refused the same unrecognized client type through RMS WS1EC-10. Source: kissterm `docs/ROADMAP.md`, cited session logs.
4. **2026-11-18:** CMS keyboard mode accepted interactive commands without a client SID; this is not a substitute for binary B2F. Source: kissterm `docs/ROADMAP.md`.
5. **2026-09-27:** the Winlink API used for the RMS gateway list needs an access key issued to the program by a Winlink administrator. Source: kissterm `docs/ROADMAP.md`.

These findings support that the observed production restriction is a **client identifier policy** in addition to protocol compatibility. They do **not** prove that an independent RMS-to-CMS protocol is documented, nor that arbitrary third-party clients can access the production system.

## Important technical distinctions

- The implementation in kissterm is a **client**, not a CMS/RMS server. An
  independent B2F laboratory server has since been built and tested offline;
  it is not a production CMS.
- Published Open B2F describes client ↔ RMS sessions; gateway ↔ CMS behavior is a separate research task.
- A successful message download from the experimental CMS does not prove outgoing Internet mail delivery or complete feature compatibility.
- The documented Winlink documents differ in details (speaker order, MIME description, block size and resume behavior). Use kissterm's `docs/PROTOCOL_GUIDE.md` as the discrepancy register and verify unresolved points with test fixtures.
- Do not modify kissterm merely to advance this project. Use its existing tests and fixtures as reference data, respecting MIT licensing and attribution.

## Originally proposed experiment

This list records the research plan at the time of the source review. The
current matrix tracks what later offline work has demonstrated and what
remains open.

1. Build a **minimal independent B2F server** that sends a server SID and challenge, accepts kissterm's own SID, and handles a test mailbox with one inbound and one outbound message.
2. Test with kissterm's existing client-side state machine and captured/fixture-driven cases, entirely offline.
3. Add Pat as a second client to detect accidental kissterm-specific behavior.
4. Separately trace Linux RMS Gateway and LinBPQ's Winlink/RMS integration to identify which can target an independent CMS.
5. Record interoperability matrix, known gaps, and precise protocol traces before choosing a federation transport.

## Design implication

The project should first implement the smallest independent CMS service boundary that existing clients can use, rather than building a new BBS or a new packet client. Federation then connects multiple such CMS servers.
