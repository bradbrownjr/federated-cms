# RFC 0003 — Local BPQ/Telnet terminal interface (draft)

**Status:** M0 design proposal, 2026-11-22. No implementation or RF field test is claimed.

## Requirement

An independently operated Federated CMS should be accessible **as an existing BPQ application**, without modifying the BPQ node or installing a special client on the operator's station. The same text-oriented CMS user experience should be testable over local Telnet. BPQ already exposes external applications via node commands and optionally their own AX.25 application callsign/SSID, and can pass the connecting station's callsign as the first line of the application TCP stream (which is stdin when an inetd-style wrapper is used). **Both Linux LinBPQ and Windows BPQ32 must be supported**: Linux can use an inetd/stdio child per connection, whereas the normal Windows deployment needs a long-running Python process that opens/listens on the local TCP socket itself.

This is **a separate interface from binary B2F**. Support both: ordinary BPQ terminal users via an ASCII application port, and Winlink-compatible software through a binary-clean B2F endpoint. Neither substitutes for investigating RMS-to-CMS interoperability.

## Verified precedent in the operator's repos

- [BPQ author's application-interface documentation](https://www.cantab.net/users/john.wiseman/Documents/LinBPQ%20Applications%20Interface.html) explicitly states the TCP `CMDPORT`/`APPLICATION` mechanism also exists in Windows BPQ32, and documents forwarding the connecting callsign; it does **not** claim that BPQ automatically launches a Python interpreter.
- [bpq-apps installation guide](https://github.com/bradbrownjr/bpq-apps/blob/main/docs/INSTALLATION.md) documents `inetd`, `/etc/services`, the BPQ Telnet `CMDPORT` table and `APPLICATION ..., C 9 HOST n` wiring. BPQ connects the session to a local TCP port; inetd redirects the connected socket to a spawned program's stdin/stdout.
- [Example BPQ configuration](https://github.com/bradbrownjr/bpq-apps/blob/main/docs/examples/linbpq/bpq32.cfg) uses `PORTNUM=9`, a `CMDPORT` list with positional `HOST` indices, and `APPLICATION` lines. Some existing applications have dedicated application callsign/SSID entries, such as the sample `FORTUNE` app; node commands can reach the same backend.
- [callout.py](https://github.com/bradbrownjr/bpq-apps/blob/main/apps/callout.py) concretely demonstrates reading `input().strip()` as the caller's BPQ-provided callsign on the **first line**, when configured to provide it. Depending on BPQ flags the supplied value may include SSID (e.g. `KC1JMH-8`).
- [YAPP protocol investigation](https://github.com/bradbrownjr/bpq-apps/blob/main/docs/YAPP-PROTOCOL.md) documents the binary-transparency limitation of the regular BPQ `HOST` terminal application path; keep this path **plain ASCII/line-oriented** instead of sending B2F/LZHUF blocks through it.
- [kissterm's Winlink roadmap](https://github.com/bradbrownjr/kissterm/blob/main/docs/ROADMAP.md) separately records why its B2F exchange could not operate cleanly over BPQ's ordinary Telnet user/terminal path. Do not repeat that error by conflating a terminal menu and B2F.

These are observations from code/docs, not an independent validation of every BPQ version or configuration flag.

## Boundary diagram

```text
AX.25 user (connect node, type FCMS) ───┐
                                        │
AX.25 user (connect app NODECALL-SSID) ├── LinBPQ APPLICATION / HOST
                                        │     (first stdin line: callsign)
Local node console or Telnet user ──────┘
                                                │
                                    BPQ local TCP CMDPORT
                                                │
                                      127.0.0.1:<port>
                                                │
                             FCMS terminal bridge / session
                                 ASCII lines only, shared core
                                                │
                                     private CMS service API
                                                │
                              mailbox / queue / information requests
                                                │
                             federation peers and authorized egress

Separate binary-clean client endpoint ──────────────► B2F adapter
```

### Cross-platform launch contract

| Aspect | Linux LinBPQ with inetd | Windows BPQ32 without inetd |
|---|---|---|
| Who listens for the configured local TCP port? | inetd | FCMS Python bridge process |
| When is the application process created? | By inetd for each accepted connection | Started beforehand; stays running and handles successive accepted sessions |
| Where does client input/output go? | stdio inherited from connected socket | The connected socket's input/output streams |
| First input line | BPQ-provided callsign if configured, otherwise unspecified | **Same protocol stream**: BPQ-provided callsign if configured |
| BPQ configuration | A `CMDPORT` entry + `APPLICATION ..., C n HOST i` | **Same** `CMDPORT` + `APPLICATION` semantics |
| Startup / recovery | inetd service and bridge executable | Startup task or service manager launches bridge; process health/restart required |
| Direct localhost test | `telnet 127.0.0.1 <port>` | `telnet 127.0.0.1 <port>` with an installed Telnet client |

**Preferred implementation:** one line-oriented `TerminalSession` handler fed by a pair of byte streams and an explicit `SessionContext`. A small `--stdio` entrypoint adapts stdin/stdout for inetd; a separate `--listen 127.0.0.1:<port>` entrypoint calls `bind()`, `listen()`, `accept()` (or equivalent Python `asyncio.start_server` / `socketserver` operations), then wraps each accepted socket in the same byte-stream interface. Do **not** fork the command parser or authorization rules by operating system. An embedded Python listener should also work on Linux for operators who prefer to avoid inetd.

These command-line flags are **proposed**, not executable commands in the current repository. The listener is a bridge to the same independent CMS service, not a new instance of the message database or an alternate routing node.

**Important operational distinction:** BPQ does *not* automatically execute the application when a remote user types the node command in this model. It **connects to an already listening TCP port**. Under Linux inetd, inetd is that listener and launches the script per session; on Windows, the independent bridge must already be running. If the port is absent, BPQ cannot deliver the session.

**Concurrency:** support multiple independent terminal sessions; enforce a configurable concurrent-session cap and per-session read/idle/write timeouts. Connections must not block each other or share mutable `current_callsign`/command state. The CMS queue/database service remains separately serialized or transactional. Graceful Windows shutdown closes accepted sockets and the listener without discarding durably queued requests. On bridge restart the CMS daemon and its queued work remain intact.

**Listener safety:** bind to `127.0.0.1` by default on both platforms, avoid exposing the port on LAN/public interfaces, select a free TCP port and set firewall rules. If BPQ runs on another machine, remote access is an explicitly configured, authenticated, protected backhaul—not an implicit public Telnet server. On Windows, use standard local process supervision (e.g., a suitably configured scheduled task/service) and least-privilege service account; installation is not yet specified.

It should not implement an alternate federation engine: all entrypoints call the same CMS queue/mailbox/service APIs.

## User-facing behavior (proposed)

The bridge uses a concise 7-bit ASCII, CR/LF-tolerant menu suitable for a 1200-baud RF terminal. No ANSI escape codes or binary frames; line limits, session timeout, output budget and paging must be enforced server-side.

Proposed initial commands:

| Command | Behavior | Privilege |
|---|---|---|
| `HELP` | Short instructions and command summary | Public |
| `STATUS` | Local CMS state: online/offline, queued work; no peer secrets | Public |
| `SERVICES` | List enabled information service names | Public |
| `WX <grid>` | Request weather with a bounded response or queued job ID | Public, rate-limited |
| `NEWS <feed>` | Request configured news source, not arbitrary URL | Public, rate-limited |
| `JOBS` | List requests associated with **authorized** session/account | Account-scoped |
| `RESULT <id>` | Retrieve output/provenance for authorized request | Account-scoped |
| `INBOX` / `READ <id>` | View permitted mailbox messages, including queued/offline content | **Requires mailbox authorization** |
| `SEND` | Compose mail using a bounded, confirmed text workflow | **Requires sender authorization**; later phase |
| `QUIT` | Close session | Public |

`WX` and `NEWS` are *suggested* human-facing command aliases for a future `weather.forecast` and `news` service adapter. They do not yet specify production service parsing. Commands return an explicit locally accepted/queued status; never claim delivery merely because an enqueue succeeded.

**Transcript sketch** (synthetic; no live network messages):

```text
FCMS — Federated CMS research terminal
Station: KC1JMH-8 (BPQ-supplied, not authenticated)
CMS local: available   Internet egress: unavailable
FCMS> WX FN43
Request queued: 550e8400-e29b-41d4-a716-446655440000
FCMS> RESULT 550e8400-e29b-41d4-a716-446655440000
Queued; awaiting eligible provider.
FCMS> QUIT
```

An offline node must accept the request durably and give the operator a short job ID/status. Subsequent reconnects should allow authorized retrieval of results after inter-CMS delivery; don't assume the user remains connected for an Internet request to finish.

## Connection identity and security

**BPQ can inject an operator callsign at session start; that is session context, not an account password.**

1. In **BPQ application mode**, expect the BPQ-provided callsign as the first complete input line **when the configured BPQ path actually supplies it**. Parse a base callsign and optional SSID without discarding the original value; retain a provenance label like `bpq_supplied`.
2. Do not use `NOCALL` in the recommended BPQ app entry if the goal is to receive the callsign. Verify flag behavior against the target LinBPQ version and the example configuration; the operator's application code is the source of truth for initial-line behavior.
3. A direct `telnet 127.0.0.1 <port>` operator may type **any** first line that looks like a callsign. The same is possible for other local software that can access that TCP port. Therefore **never treat the first line of an unauthenticated TCP connection as proof that BPQ supplied it**.
4. Treat station identifiers received over an RF node as **claimed/operator-context identity** unless a suitable separate identity/authorization mechanism exists. BPQ may accurately report the callsign associated with its AX.25 session, but the station's claimed on-air identity is not necessarily independently verified.
5. Public read-only services may accept an unverified session with quotas and no secret response. Mailbox enumeration, message read/deletion, private results and SMTP sender delegation must require independent, approved account authorization. Do not prompt for or echo a reusable secret on ordinary amateur RF.
6. For a later trusted BPQ identity assertion, use an authenticated **internal-only bridge-to-CMS API**, e.g. a protected Unix domain socket with per-service OS permissions, where the bridge records that BPQ provided the station identity. This improves attribution and prevents arbitrary network callers impersonating BPQ; it still does **not** cryptographically verify the transmitting operator over RF.
7. Keep the local TCP listener bound to `127.0.0.1` on either operating system, or otherwise explicitly firewalled; **Telnet is unencrypted** and must never become a public Internet management or mailbox endpoint. A node hosted elsewhere would require a separately authorized protected backhaul, not a wildcard listener.
8. Maintain a distinct **local sysop admin interface**. Do not permit terminal commands from RF to configure peers, authorize mailbox delegates, inspect other users' private mail, or administer egress credentials.

If the same listener serves BPQ and direct Telnet tests, their first-line framing cannot identify the upstream unambiguously: begin both in the same **unprivileged** mode. For stronger origin labeling, configure a separate protected BPQ ingress/bridge mode versus a local diagnostic listener, with no upgrade to privileged mail operations on origin label alone.

## Example configuration shape (illustrative, NOT a drop-in installation script)

The existing operator sample has `CMDPORT` indices 0–18 ending at TCP 63180. To add a **new, unused** local service, one possible *test-only* layout would be:

```text
# /etc/services (illustrative service number; check conflicts)
fcms            63190/tcp

# /etc/inetd.conf — only if using an inetd/stdio bridge
fcms  stream  tcp  nowait  <bpq-user>  /usr/local/bin/fcms-bpq-bridge

# bpq32.cfg, within Telnet PORT / CONFIG: append to *existing* CMDPORT
CMDPORT ... 63180 63190
# index 19 if and only if the first entries remain 0–18

# bpq32.cfg APPLICATION table, select a free application slot
APPLICATION 24,FCMS,C 9 HOST 19 K S
```

**Do not copy** a literal ellipsis in `CMDPORT` into a production config; keep all installed entries and calculate the correct `HOST` index. The app's dedicated callsign/SSID is an optional part of LinBPQ's `APPLICATION` configuration. Select an available SSID and check local syntax/route ownership in LinBPQ's documentation; sample setups show the analogous trailing callsign/alias fields on `FORTUNE` and `RMS`. Do not reuse those existing SSIDs. The bridge executable named above is **not written yet**. **Windows BPQ32:** use the same `CMDPORT` and `APPLICATION` entries but do **not** configure Linux `/etc/services` or `/etc/inetd.conf`; instead start the future self-listening Python bridge bound to `127.0.0.1:63190` before BPQ tries to connect. Use a chosen unused TCP port; Windows startup/service management and executable packaging are separate implementation tasks.

For initial tests, the operator can directly Telnet to the bound service on localhost, then use `FCMS` from the BPQ node; only after confirming callsign delivery and ASCII behavior should authorized RF and direct SSID tests be considered.

## Session/queue mapping

- Human `WX FN43` -> validate input -> create [v0.1 request envelope](REQUEST-ENVELOPE-v0.1.md) with stable ID -> persist -> show local receipt/job ID.
- `RESULT <id>` -> authorize requester against stored request ownership -> show result or queued/expired/error state, with source, UTC fetch timestamp and stale status.
- `INBOX` -> authorized mailbox store read; not a passthrough to BPQ BBS.
- `SEND` -> authorized B2/Internet mail queue; same storage/routing as B2F but through text-only composition.
- Use a **stable account mapping and durable request ownership** across reconnects; the ephemeral TCP session or AX.25 SSID must not be the only retrieval key.

## Acceptance tests

1. Direct Telnet / loopback shows help and closes cleanly; ASCII CR, LF, CRLF variants and short line handling work without terminal escapes, using **both** the inetd/stdio adapter and self-listening TCP adapter.
2. Simulated BPQ caller first line `KC1JMH-8` is recorded via either entrypoint; base `KC1JMH` and SSID `8` available without losing the original. Subsequent sessions and concurrent callers cannot reuse another session's identity.
3. A missing initial callsign or direct Telnet spoofing attempt never grants access to another operator's private mailbox.
4. Node command `FCMS` and direct app SSID (configured separately) arrive at the **same** bounded text service and receive equivalent behavior.
5. Offline `WX` returns a committed queue ID before an acknowledgement; after reconnection the authorized request owner can retrieve an attributed/stale-marked result.
6. B2F binary framing is never routed over the conventional BPQ terminal/inetd path.
7. Overlong lines, terminal control bytes, malformed callsigns, flooding, abandoned sessions and high output volume fail safely.
8. The node and application continue functioning if the independent CMS daemon is down; show a concise unavailable message rather than hanging.
9. **Windows-specific:** standalone listener binds to loopback; BPQ connects to the configured port, passes the callsign and closes; two users can connect simultaneously without shared session state. If the listener is stopped, verify a clean BPQ error; on restart, existing queued work survives. Test process startup and missing-listener cases.
10. **Linux-specific:** inetd starts one stdio bridge per inbound connection. Check equivalent visible responses and authorization behavior between Linux and Windows using identical prerecorded session fixtures.

## Work split

- **M0:** Document BPQ ingress, callsign provenance, dedicated SSID path, cross-platform inetd/own-socket launch modes, text-mode transport limits and security requirements (**this document**).
- **M0.5:** Independent binary B2F server/interoperability testing remains separate.
- **M1:** Implement the shared terminal session core with **both** `--stdio` (Linux inetd) and `--listen` (Windows native socket, also usable on Linux) harnesses to submit a weather request and retrieve queued result from the durable store.
- **M3:** Validate with a real LinBPQ node command and dedicated AX.25 application SSID, plus Windows BPQ32 CMDPORT connection and callsign injection; then add broader BPQ service integration and RF-efficiency measurements.
