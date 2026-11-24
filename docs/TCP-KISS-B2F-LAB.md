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

## Prerequisites

* A trusted local checkout or installation of kissterm at tested commit
  `f3310c71efa8e3e23263ed25745a9c365a9efc74`. This bridge uses its
  AX.25, TCP KISS and transmit-gate interfaces, including the pinned link's
  outbound buffer for bounded flow control. Other versions are unverified.
* A TCP KISS endpoint and its host, port and KISS channel, plus the exact
  station and peer callsigns/SSIDs. Direct paths only; no digipeaters.
* A separately running loopback-only Federated CMS B2F lab server with a
  dedicated database and disposable password. Its configured lab account
  must match the client B2F account. The AX.25 peer callsign is separately
  configured and is not proof of mailbox ownership.

The local automated test uses a fake TCP KISS hub, the pinned kissterm AX.25
state machine and the real B2F lab, entirely on localhost. It checks a
bidirectional message and attachment exchange and reopened SQLite state. It
does not validate a particular physical modem or radio.

## Operator-controlled use

Before any operation that could key a transmitter, establish the actual
hardware configuration and operator authorization. The bridge opens its
transmit gate only when explicitly started with `--enable-tx`; without that
flag it exits before opening the KISS connection. It stops after one session,
on modem connection loss, or when its frame or wall-time limit is reached.
Ctrl-C closes its gate and stops the bridge. These software limits do not
replace station control, identification, or a physical emergency stop.

Set `PYTHONPATH` to include both this repository's `src` directory and the
trusted kissterm checkout (use `;` rather than `:` on Windows). Start the B2F
server separately as described in [DEVELOPMENT.md](DEVELOPMENT.md), then,
only for an authorized test, run the following with the placeholders replaced:

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
Retaining that output is the operator's choice; these counts do not measure
actual RF airtime or establish that the frame was transmitted over the air.

This path does not exercise RMS gateway SGL or a remote LinBPQ installation.
See the [interoperability research plan](WINLINK-INTEROPERABILITY.md) for the
remaining boundaries.
