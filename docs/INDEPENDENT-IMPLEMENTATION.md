# How Federated CMS works with Winlink software

Federated CMS works with Winlink client programs, but nobody on this project
has read, decompiled or disassembled Winlink's own software. It is built from
documents Winlink and others publish, from open-source programs used under
their licenses, and from watching how unmodified clients behave when they
connect to our own test server. This page lists those sources, the things we
do not do, and why.

Winlink is volunteer work by the Amateur Radio Safety Foundation (ARSFI) and
its developers. Federated CMS is not affiliated with or endorsed by them.

## Is this a clean-room implementation?

In a classic [clean-room design](https://en.wikipedia.org/wiki/Clean-room_design),
one team studies the original program and writes a plain description of what
it does. A second team, which has never seen the original, writes new code from
that description. Keeping the teams apart shows that the new code was written
independently, not copied.

This project does not need two teams, because nobody on it has studied
Winlink's code at all. Everyone works the way the second team does: from
published descriptions and observed behaviour. "Clean room" is a fair
shorthand for that. The precise description is an **independent
implementation built from public specifications and black-box
interoperability testing**.

## What we work from

| Source | What it is | How we use it |
| --- | --- | --- |
| [Open B2F specification](https://winlink.org/B2F) | Winlink's published "specification for the transfer of messages between Winlink Radio Mail Server (RMS) gateway stations and Winlink compatible user terminal programs". It also says it is published so that compliance with FCC rules is clear and on-air traffic can be monitored. | Basis of the B2F server and message handling. |
| [Winlink FAQ](https://winlink.org/sites/default/files/download/wl2k_faq_20230311.pdf) and [inquiry guide](https://catalog.winlink.org/inquiries.txt) | Winlink's public user documentation. | Catalog request (`INQUIRY`) and position report (`QTH`) formats. |
| [ARSFI Winlink-Compression](https://github.com/ARSFI/Winlink-Compression) | Compression source that the Open B2F page says is public. | Referenced only. Our LZHUF code is a port of JNOS 2's `lzhuf.c` (its authors: "use, distribute, and modify this program freely") with wl2k-go's container handling. |
| [Pat](https://github.com/la5nta/pat) and [wl2k-go](https://github.com/la5nta/wl2k-go) (MIT) | Open-source Winlink-compatible client and protocol library. | Pinned test client in CI; reference for message, compression and position-report formats. |
| [Linux RMS Gateway](https://github.com/nwdigitalradio/rmsgw) (GPL-2.0) | Open-source gateway software. | Built from a pinned upstream checkout for isolated tests. Its source is not copied into this repository; the one small fix we tested is kept as a patch to offer upstream. |
| [kissterm](https://github.com/bradbrownjr/kissterm) (MIT) | Independent client by this project's author. | Pinned test client and AX.25 stack for lab bridges. |
| Unmodified Winlink Express | Winlink's client program, used normally. | Connected to our own lab server to see which responses it accepts. Findings are recorded with the version tested, such as Express 1.8.0.0 accepting the lab only when the server's identifier included `W`. |
| Our own Winlink account | A licensed operator's normal sessions with the production system. | To see what real replies look like, for example the `SERVICE` reply to a catalog request. Only short examples needed to describe a format are recorded. |

## What we do not do

- Decompile, disassemble or debug Winlink Express, RMS Packet, RMS Relay,
  RMS Trimode, the Winlink servers, or any other Winlink program, or inspect
  their files beyond normal use.
- Bypass any registration, licence or other protection.
- Copy Winlink's catalog, documents or server replies into this repository,
  beyond the short examples needed to describe a format. Our catalog maps IDs
  to sources we choose and fetch ourselves.
- Point test software at Winlink's production servers. Production sessions are
  ordinary use by a licensed operator of their own account.
- Present our server as Winlink. It identifies itself as `FCMS`, uses its own
  addresses and domains, and does not use `winlink.org` addresses as its own.
- Use anyone else's credentials, accounts or captured traffic.

## Why we work this way

- **Respect for Winlink's work.** The goal is for people's existing radios and
  programs to keep working, not to take Winlink's code. Users should be able to
  use Winlink and Federated CMS side by side.
- **Build to the published specification.** Open B2F describes how gateways
  and Winlink-compatible client programs exchange messages. Winlink's own
  [client comparison](https://winlink.org/node/17) lists third-party
  programs alongside its own. We build to that specification and test against
  real clients to confirm it.
- **Clear ownership.** Federated CMS code is its own, under its MIT licence.
  Open-source components keep their own licences and are credited above.
- **Anyone can check.** Every compatibility claim links to a test or a
  recorded observation, so operators and Winlink developers can confirm what
  was done and how.

## Limits

- This page describes how the project is built. It is not legal advice.
- Observed client behaviour can change with new Winlink Express versions; each
  observation names the version tested.
- Winlink Express's own licence terms are shown inside the program (Help, then
  License). They have not been reviewed for this page yet.
