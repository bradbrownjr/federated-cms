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
