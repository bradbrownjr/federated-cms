# Existing B2F interoperability evidence from kissterm

**Status:** source-backed research findings; no new live tests performed.

Source: https://github.com/bradbrownjr/kissterm

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

These findings support that the observed production restriction is a **client identifier policy** in addition to protocol compatibility. They do **not** prove that an independent RMS-to-CMS protocol is documented, nor that arbitrary third-party clients can access the production system.

## Important technical distinctions

- The implementation in kissterm is a **client**, not a CMS/RMS server. A server-side counterpart must still be built and validated.
- Published Open B2F describes client ↔ RMS sessions; gateway ↔ CMS behavior is a separate research task.
- A successful message download from the experimental CMS does not prove outgoing Internet mail delivery or complete feature compatibility.
- The documented Winlink documents differ in details (speaker order, MIME description, block size and resume behavior). Use kissterm's `docs/PROTOCOL_GUIDE.md` as the discrepancy register and verify unresolved points with test fixtures.
- Do not modify kissterm merely to advance this project. Use its existing tests and fixtures as reference data, respecting MIT licensing and attribution.

## Revised immediate experiment

1. Build a **minimal independent B2F server** that sends a server SID and challenge, accepts kissterm's own SID, and handles a test mailbox with one inbound and one outbound message.
2. Test with kissterm's existing client-side state machine and captured/fixture-driven cases, entirely offline.
3. Add Pat as a second client to detect accidental kissterm-specific behavior.
4. Separately trace Linux RMS Gateway and LinBPQ's Winlink/RMS integration to identify which can target an independent CMS.
5. Record interoperability matrix, known gaps, and precise protocol traces before choosing a federation transport.

## Design implication

The project should first implement the smallest independent CMS service boundary that existing clients can use, rather than building a new BBS or a new packet client. Federation then connects multiple such CMS servers.
