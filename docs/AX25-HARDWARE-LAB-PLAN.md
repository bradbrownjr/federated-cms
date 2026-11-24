# AX.25 hardware interoperability: proposed lab

**Status:** planning only. No physical TNC, modem or RF exchange has been
verified. The existing [guest harness](../tests/ax25_guest/README.md) uses
virtual KISS links and cannot establish physical-device compatibility.

## Equipment and topology to establish

Record the client and gateway hosts and operating systems; TNC or sound modem
models, firmware and interfaces; radios and audio/PTT connections; packet
mode and speed; and the operator's available cabled, shielded or dummy-load
arrangement. Identify whether each host uses Linux AX.25, BPQ or another node.
Do not put station credentials, serial numbers or private message contents in
the public test report.

The proposed path is a synthetic B2F client account through a physical packet
modem/link and a gateway to an independently hosted laboratory CMS. The
existing SGL adapter and mailbox are test fixtures, not production services;
adapting the lab setup to a physical interface and proving network isolation
remain work to be done. Use disposable accounts and secrets; do not reuse
Winlink or administrative credentials. No production Winlink endpoint is part
of this plan.

## Proposed staged checks

1. Inventory devices and software without opening transmit paths. Record exact
   versions, physical connections, callsign/SSID roles and how the operator
   will stop a test.
2. Establish a contained local setup appropriate to the equipment, with the
   operator confirming the physical isolation and transmit controls before
   any keyed operation. Exercise link setup and release with small synthetic
   traffic; do not assume a dummy load or cable alone proves zero radiation.
3. Configure an independent local CMS laboratory endpoint and verify its
   host table, routing and traffic isolation. Confirm gateway and mailbox
   authentication are separate and use only synthetic credentials. Exercise
   message upload, download, attachment bytes and persistence after restart;
   include rejected credentials and interrupted transfer recovery.
4. Record the gateway process exit, link disconnect, client outcomes, mailbox
   state, timestamps, retries and bytes. Distinguish a successful transfer
   from clean shutdown and from end-to-end delivery to a human recipient.
5. Only after the local setup and an explicit operator decision, design any
   separate on-air test for the actual station, jurisdiction and emission.
   The existing [threat baseline](THREAT-MODEL-M0.md) calls for an explicit
   operator-authorized transmit gate and airtime limits.

Results apply only to the tested device versions and topology. The virtual
guest findings, including the optional SIGPIPE patch, do not establish that a
stock physical gateway is supported or safe for production traffic.
