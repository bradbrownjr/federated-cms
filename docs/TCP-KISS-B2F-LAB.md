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
update. Use an independently configured test client with disposable
credentials. Before entering those credentials in Express, independently
verify that the test setup is isolated from real account updates. See Winlink's
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
server's synthetic message remained pending. The initial trial established
the radio and link path. The corresponding offline regression verifies that a
client refusal before authentication leaves the pending message in the
mailbox.

An operator-supplied successful Packet Winlink reference session displayed
`[WL2K-5.0-B2FWIHJM$]`, which also appears as an example in the public
[Open B2F specification](https://winlink.org/B2F). Both identifiers contain
`B2` and `F`, but their software names, versions and other feature characters
differ, so the follow-up probes kept the FCMS name and version and varied the
feature characters.

In a subsequent virtual TCP KISS experiment with Express 1.8.0.0, the
reference SID and `[FCMS-0.1-B2FWIHJM$]` both led to a client handshake.
The otherwise identical pair `[FCMS-0.1-B2FWHM$]` and
`[FCMS-0.1-B2FHM$]` respectively led to a handshake and a peer-to-peer/SID
rejection. Adding `W` changed the decision for that pair in the tested
version and setup. These comparisons used a probe without a challenge,
authentication, mailbox or radio. The meaning of `W` beyond that observed
decision remains open.

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
in the lab inbox. The radio trials below exercised the modified SID on the
physical link. The session record omits authentication challenges and responses.

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
The opt-in bridge accepts `--ack-delay` (seconds, 0.1 to 10) for controlled
T2 experiments. The default remains one second; changing this timer is not a
general performance recommendation, and polls still receive prompt replies.
In one subsequent over-air comparison, a six-second T2 did not complete the
roughly 2 KB compressed upload; repeated windows and REJs continued and the
session disconnected. The lab database contained no complete upload and kept
the download pending. The bridge ended with a timeout, but this observation
does not identify which timer or radio condition caused the failure. Do not
interpret the longer T2 as an improvement or use it as a new default.

The offline `tests/test_half_duplex_model.py` drives the pinned AX.25 station
with a scaled four-frame burst while a fake busy modem queues outgoing frames.
It shows that a short T2 can enqueue several progressively newer RRs during a
burst, while a longer T2 reduces that case to the final poll response. It also
shows that repeated incoming polls queue repeated final responses even with a
long T2. This is a deterministic model of queueing, not a simulation of
Express timers, actual modem carrier detection, RF loss or their interaction;
it cannot establish the root cause of the physical trial or validate a fix.
`tests.radio_timing_model` extends this with two kissterm AX.25 stations, a
lossless FIFO half-duplex channel with scaled per-frame airtime and turnaround,
and a 2,048-byte synthetic stream. In this model a short sender T1 produces
retransmissions; a longer sender T1 can complete with the original sixteen
data frames. It assumes that queued KISS frames are accepted immediately, has
no actual Express implementation, carrier detection, RF loss or modem timing,
and its timing values are scaled test parameters, not field recommendations.
The tests do not establish the configuration of the real Express sender.

The operator's Surface settings screenshots show Express configured for KISS
`NORMAL`, 1200 baud, 128-byte maximum packets, four maximum frames, Frack 2,
five retries and IPoll enabled. Surface SoundModem identifies itself as
UZ7HO 1.13 and shows KISS Optimization unchecked. The public
[Winlink setup guide](https://winlink.org/sites/default/files/RMSE_FORMS/quick_setup_guide_for_winlink_with_signalink_on_windows_v20190311a.pdf)
recommends `ACKMODE` for most SoundModem setups. These screenshots do not prove
why the observed retries occurred, and NORMAL/ACKMODE must be compared in
controlled sessions before drawing a conclusion.

One subsequent physical-radio trial used the same previously unsuccessful
upload with Express set to ACKMODE and the lab T2 returned to one second.
Express reported a 2,109-byte upload in 44 seconds and downloaded one
synthetic attachment message; the trace showed no repeated upload windows and
the lab observed the final DISC/UA. A newly opened database connection found
the uploaded message and both attachments, and the download was no longer
pending. This favorable result supports further ACKMODE testing but is a
single comparison with uncontrolled radio conditions, not a proven cause or
general throughput guarantee.
The operator later reported that both radios were set to 5 W, with one roof
antenna and one indoor antenna approximately four feet from the radio-room
radio; the radios were approximately ten feet apart. Antenna-to-antenna
separation and received signal levels were not measured. Receiver overload is
a possible contributor, not an established cause. See
[packet troubleshooting](PACKET-TROUBLESHOOTING.md) for controlled comparisons.

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

For a small synthetic attachment download, seed the dedicated database while
no B2F lab session is active. The example uses `LAB_ACCOUNT` and `LAB_DATABASE`
from the operator's environment and the same Python environment as the bridge.

```python
import os, secrets
from federated_cms import CMSCore, SQLiteStore
from kissterm.winlink.message import build, serialize

account = os.environ['LAB_ACCOUNT'].upper()
service = CMSCore(SQLiteStore(os.environ['LAB_DATABASE'])).lab_mailbox()
message = build(mid=secrets.token_hex(6).upper(), sender=account, to=[account],
                subject='Federated CMS lab', body='Synthetic test message.',
                files=[('lab-fixture.txt', b'Synthetic lab attachment.\n')])
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

### Repeat the Express attachment trial

Use the isolated Express setup and station controls described above. Queue a
synthetic attachment message in the dedicated lab database, then close the
seeding session. Queue a separate synthetic attachment message for upload in
Express with two synthetic files. Start a loopback B2F server from a checkout
that accepts the extra NUL after the final attachment observed in Express
1.8.0.0. This example uses port 63194 for that server. The general bridge
example above connects directly to port 63191.

```text
python -m federated_cms.b2f --port 63194 --lab-account LAB_ACCOUNT --password-file LAB_PASSWORD_FILE --database LAB_DATABASE
```

Run the one-session SID relay from the repository root and point it at the
updated server on port 63194.

```text
python -m tests.radio_sid_relay --port 63195 --b2f-port 63194 --seconds 600
```

The relay checks the B2F listener and its SID before printing its readiness
message. Once it prints that message, start the bridge with the operator's
modem and direct station addresses. Point the bridge at the relay port to
present the experimental FCMS SID used in the successful Express sessions.

```text
python -m federated_cms.kiss_lab --kiss-host MODEM_HOST --kiss-port MODEM_PORT --station STATION_CALL --peer CLIENT_CALL --b2f-port 63195 --ack-delay 1 --enable-tx
```

Set the Express Packet TNC Model to `ACKMODE` with a compatible modem, then
initiate one direct Packet Winlink session to the lab station. Check for the
final DISC/UA and verify that the downloaded attachment opens in Express and
matches the fixture. After disconnect, reopen the SQLite database and confirm
that the download is no longer pending. Find the new upload's message ID in
the lab inbox and verify the attachment bytes using the original files in
their B2 `File` header order.

```text
python -m tests.verify_lab_attachments LAB_DATABASE LAB_ACCOUNT UPLOAD_MID FIXTURE_PATH_1 FIXTURE_PATH_2
```

For a NORMAL/ACKMODE comparison, keep T2 and the other packet and radio
settings fixed, restart the bridge and relay for each run, and use comparably
sized synthetic fixtures with fresh message IDs. Record completion, duration
and retransmissions.

This path does not exercise RMS gateway SGL or a remote LinBPQ installation.
See the [interoperability research plan](WINLINK-INTEROPERABILITY.md) for the
remaining boundaries.
