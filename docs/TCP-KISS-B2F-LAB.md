# TCP KISS to B2F laboratory bridge

This experimental bridge connects to a TCP KISS modem and accepts **one**
direct AX.25 session from a configured callsign and SSID. It forwards that
binary stream to the existing B2F lab server on the same machine's loopback
interface. It reuses kissterm's AX.25 implementation; it does not implement a
BPQ terminal application or an RMS gateway/SGL connection.

The bridge is a research fixture, not a production mailbox gateway. The peer
callsign filter is not authentication. The B2F lab's legacy password challenge
is not a secure modern authentication method; use a separate database and
disposable lab credentials, with synthetic messages only. Do not reuse Winlink
credentials or expose private mail. TCP KISS gives network clients access to a
modem; use a trusted network and protect that access separately.

**Winlink Express account warning:** changing a password in Winlink Express
can also change the password on the real Winlink account. Do not enter the
laboratory password into a production-linked Winlink Express setup. Merely
using a different local profile must not be assumed to prevent an account
update. Winlink Express has not been validated for isolated testing here;
use an independently configured test client with disposable credentials
until its account-update behavior and isolation are verified. See Winlink's
[password guidance](https://winlink.org/content/password_problems).

## Prerequisites

* A trusted local checkout or installation of kissterm at tested commit
  `f3310c71efa8e3e23263ed25745a9c365a9efc74`. This bridge uses its
  AX.25, TCP KISS and transmit-gate interfaces, including the pinned link's
  outbound buffer for bounded flow control. Other versions are unverified.
* A TCP KISS endpoint and its host, port and KISS channel, plus the exact
  station and peer callsigns/SSIDs. Direct paths only; no digipeaters.
  Do not run another AX.25 station with the same callsign/SSID on that modem;
  simultaneous TCP-client behavior depends on the modem.
* A separately running loopback-only Federated CMS B2F lab server with a
  dedicated database and disposable password. Its configured lab account
  must match the client B2F account. The AX.25 peer callsign is separately
  configured and is not proof of mailbox ownership.

The local automated test uses a fake TCP KISS hub, the pinned kissterm AX.25
state machine and the real B2F lab, entirely on localhost. It checks a
bidirectional message and attachment exchange and reopened SQLite state. It
does not validate a particular physical modem or radio.

In one operator-initiated physical-radio trial, Winlink Express in Packet
Winlink mode established a direct AX.25 connection to the lab. Express
reported that the lab's `FCMS` SID was invalid, classified the endpoint as a
peer-to-peer station and disconnected without sending an authentication
response. Its session window also displayed the challenge and prompt. The
server's synthetic message remained pending. This demonstrates the radio and
link path, not Express B2F compatibility. The reason for Express's endpoint
classification is not yet established; do not infer that the password was
wrong or change the SID to claim another service's identity based on this one
observation. The corresponding offline regression verifies that an
unauthenticated client refusal cannot retrieve the pending message.

An operator-supplied successful Packet Winlink reference session displayed
`[WL2K-5.0-B2FWIHJM$]`, which also appears as an example in the public
[Open B2F specification](https://winlink.org/B2F). Both identifiers contain
`B2` and `F`, but their software names, versions and other feature characters
differ. This comparison alone does not establish which difference caused the
rejection, or whether Express checks something else.

In a subsequent virtual TCP KISS experiment with Express 1.8.0.0, the
reference SID and `[FCMS-0.1-B2FWIHJM$]` both led to a client handshake.
The otherwise identical pair `[FCMS-0.1-B2FWHM$]` and
`[FCMS-0.1-B2FHM$]` respectively led to a handshake and a peer-to-peer/SID
rejection. This indicates `W` controls that decision in the tested version
and setup; it does not establish full protocol interoperability or define the
complete semantics of that feature. These comparisons used a probe without a
challenge, authentication, mailbox or radio.

For further controlled testing, `tests.virtual_kiss_sid` can connect its
virtual KISS network to a loopback B2F lab endpoint with `--b2f-port`. Its
test-only proxy substitutes the selected SID and relays the remaining bytes
unchanged. The proxy is an experiment, not evidence that all features in a
selected SID are implemented. Bind its virtual KISS listener to loopback by
default; a LAN bind must be selected explicitly for an operator's client.
Use a separate lab mailbox and synthetic messages. The KISS network does not
connect to a physical modem.

On 2026-11-24, the operator used Express 1.8.0.0 Packet Winlink over this
virtual KISS network and the experimental `W` SID proxy to connect to the
password-protected lab. Express received a synthetic message, displayed it in
the inbox and disconnected; the reopened lab database had no pending outgoing
messages. This demonstrates a lab download and authentication in this setup,
and a subsequent session uploaded a synthetic message. Express reported a
successful send, and a newly opened database connection confirmed the message
in the lab inbox. These sessions do not demonstrate a radio exchange with the
modified SID or production Winlink compatibility. No authentication challenges
or responses are recorded here.

A simulated-KISS attachment session exposed an Express 1.8.0.0 message ending
with CRLF and an additional NUL after the final attachment. The lab validator
now accepts that specific observed ending for messages with attachments,
preserving the received bytes; other unexplained trailing data is rejected.
Express subsequently reported an upload containing two attachments and a
download of a message with a synthetic attachment. A reopened lab database
contained the upload with both declared attachments and no pending outgoing
messages. The operator also opened the downloaded synthetic text attachment
in Express and confirmed that its contents matched the sent fixture.

The one-session `tests.radio_sid_relay` is a loopback-only, experimental SID
adapter for a physical-modem trial. It substitutes the observed `W` SID for
the lab SID while passing the remaining stream through to the lab. It does
not establish support for every capability that a client may associate with
`W`. Pair it only with the operator-controlled KISS bridge and synthetic lab
mail; it is not a production gateway or a connection to Winlink servers.

On 2026-11-24, the operator ran one low-power direct radio session between
Express Packet Winlink and this lab through two sound modems using that SID
adapter. Express reported a synthetic attachment upload and download and a
clean disconnect. A new database connection confirmed the upload, containing
two attachments, and no pending outbound message. Express reported 2,153
upload bytes in 88 seconds. The operator's monitor showed retransmitted I
frames and REJ responses during the upload; the source of the retransmissions
has not yet been determined. The lab log confirmed the final DISC/UA. This is
one physical-radio experiment, not general compatibility or throughput proof.
The receiving-modem trace shows sequences of RR acknowledgments emitted in
bursts, plus repeated sender frames and REJ responses. The pinned kissterm
link defaults to a one-second delayed-acknowledgment timer and immediately
answers polls. A short acknowledgment delay relative to frame airtime and
half-duplex modem queuing is a plausible contributor, but the trace alone
does not establish cause; radio losses, turnaround and sender timers also need
to be controlled in a comparison. A separate trace supplied by the operator
shows retries with another gateway, but differs in station, session and client
version, so it is not a controlled benchmark.

For controlled identifier experiments, `python -m tests.sid_probe --sid
'[FCMS-0.1-B2FHM$]'` starts a one-connection diagnostic on `127.0.0.1:63192`.
Run it on the client computer on a private loopback connection only; never
forward it to a radio or external network. It sends the selected identifier
and a diagnostic prompt, has no password, challenge or mailbox, and reports
only whether the client sent data. It cannot complete a B2F session. A test
against a Telnet session may provide a clue, but cannot establish Packet
Winlink behavior because the client may treat the transports differently.

## Operator-controlled use

Before any operation that could key a transmitter, establish the actual
hardware configuration and operator authorization. The bridge opens its
transmit gate only when explicitly started with `--enable-tx`. Without that
flag it exits before opening the KISS connection unless `--check-only` is
selected, which sends no data. It stops after one session,
on modem connection loss, or when its frame or wall-time limit is reached.
Ctrl-C closes its gate and stops the bridge. These software limits do not
replace station control, identification, or a physical emergency stop.

Set `PYTHONPATH` to include both this repository's `src` directory and the
trusted kissterm checkout (use `;` rather than `:` on Windows). Start the B2F
server separately as described in [DEVELOPMENT.md](DEVELOPMENT.md). To check
the modem TCP connection without sending data or arming transmit, run:

```text
python -m federated_cms.kiss_lab --kiss-host MODEM_HOST --kiss-port MODEM_PORT --station STATION_CALL --peer CLIENT_CALL --check-only
```

For an authorized test, run the following with the placeholders replaced:

```text
python -m federated_cms.kiss_lab --kiss-host MODEM_HOST --kiss-port MODEM_PORT --station STATION_CALL --peer CLIENT_CALL --b2f-port 63191 --enable-tx
```

The station and peer are operator-selected AX.25 addresses; do not transmit
using example or synthetic station identifiers. The modem's KISS port and the
KISS channel number are different: `--channel` selects the channel and
defaults to zero. `--seconds` defaults to 300 and `--max-frames` to 500;
both may be reduced for a shorter test. Each direction is capped at 65536
bytes. The B2F lab has its own connection timeout and mailbox lease; large or
slow transfers may time out and need separate testing. Stop and inspect the
logs and mailbox after a test; a disconnect alone does not prove delivery.
Each accepted outbound frame is logged to stdout with UTC time, KISS channel,
frame type and byte count, without message content or challenge responses.
Received frames addressed to the lab station are logged with source, channel,
type and whether the configured direct-peer filter accepted them; payloads are
not logged.
Retaining that output is the operator's choice; these counts do not measure
actual RF airtime or establish that the frame was transmitted over the air.

For a small synthetic download, seed the dedicated database while no B2F lab
session is active. The example uses `LAB_ACCOUNT` and `LAB_DATABASE` from
the operator's environment and the same Python environment as the bridge:

```python
import os, secrets
from federated_cms import CMSCore, SQLiteStore
from kissterm.winlink.message import build, serialize

account = os.environ['LAB_ACCOUNT'].upper()
service = CMSCore(SQLiteStore(os.environ['LAB_DATABASE'])).lab_mailbox()
message = build(mid=secrets.token_hex(6).upper(), sender=account, to=[account],
                subject='Federated CMS lab', body='Synthetic test message.')
session = service.open_session(account)
try:
    service.store_message(session, message.mid, serialize(message), 'out')
    print('Queued synthetic test message:', message.mid)
finally:
    service.close_session(session)
```

After the session has ended, `service.list_incoming(session)` and
`service.pending(session)` report the lab's inbound and pending outbound
messages inside a new trusted session. Close that session afterward. Check the
client's received and sent records as well as the server database; a local
gateway acceptance is not a delivery receipt from another human recipient.

This path does not exercise RMS gateway SGL or a remote LinBPQ installation.
See the [interoperability research plan](WINLINK-INTEROPERABILITY.md) for the
remaining boundaries.
