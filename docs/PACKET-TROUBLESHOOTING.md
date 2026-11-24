# Packet radio troubleshooting

The TCP KISS to B2F bridge is an experimental, operator-controlled lab. The
results below are not a production compatibility claim. For its requirements,
authentication limitations, and operation, see the
[TCP KISS laboratory guide](TCP-KISS-B2F-LAB.md).

## Repeated frames or slow transfers

If a packet session connects but repeatedly sends the same frames, shows
`REJ` responses, or takes much longer than expected, first verify which
station is sending and receiving each frame. A single monitor may display the
same frame on more than one channel; those duplicate displays are not by
themselves retransmissions. Repeated sequence numbers can also occur normally
after the AX.25 sequence number wraps. Compare payload and timing as well.

With Winlink Express and a modem that supports the extended KISS ACKMODE,
compare `NORMAL` and `ACKMODE` as a troubleshooting test. In the Packet
Winlink session, select **Settings → Packet TNC Model**, record the current
setting, and change only that setting for the comparison. The
[Winlink SoundModem setup guide](https://winlink.org/sites/default/files/RMSE_FORMS/quick_setup_guide_for_winlink_with_signalink_on_windows_v20190311a.pdf)
recommends ACKMODE for most SoundModem setups and suggests NORMAL as a
troubleshooting alternative. Ensure your particular modem supports the mode
you select; restore the original setting if it works better.

ACKMODE lets a compatible modem report locally that a frame has been
transmitted. That information can help the application time retries when
frames wait in a modem queue. The local notification does not mean the remote
station received the frame; AX.25 acknowledgments still serve that purpose.
See the [G8BPQ KISS options](https://www.cantab.net/users/john.wiseman/Documents/BPQCFGFile.html)
and the [extended KISS description](https://files.tapr.org/meetings/CNC_1991/CNC1991-MultidropKISSmode-WK5M.pdf).

For a useful comparison, use the same radios, frequency, power, packet
settings and similarly sized synthetic messages. Use a new message ID for
each completed upload, or the mailbox may correctly treat it as a duplicate.
Record completion, transfer time, retransmissions and the modem settings.
Repeat each mode when practical; one successful run does not establish a
cause or a general performance advantage. Do not change a working station
permanently based on a single trial.

## Strong signals in a close-range lab

Very close stations can produce signals strong enough to overload a receiver
or interfere with nearby equipment. This is a possible cause of poor decoding,
not something the presence of retries alone can diagnose. A timing or modem
configuration issue can produce similar symptoms. ACKMODE does not correct
receiver overload; any relationship between the two must be tested.

For an RF comparison, keep modem and packet settings fixed and vary one
physical factor at a time, such as lowering transmitter power within the
radio's supported range or increasing antenna separation. Compare the frame
decode and retry counts in both directions. If using attenuation or a
conducted test, use equipment rated for the transmitter power and an
appropriate RF test arrangement; do not directly connect one radio's
transmitter output to another radio's receiver input.

## Example: the initial home lab

One trial used two radios set to 5 W, one with a roof antenna and the other
with an indoor antenna. The indoor antenna was approximately four feet from
the radio-room radio; the radios themselves were approximately ten feet apart.
The separation between the two antennas and their received signal levels were
not measured. Receiver overload is therefore an operator hypothesis, not an
established finding.

In this setup, a NORMAL-mode attempt repeatedly retransmitted and did not
complete. A subsequent ACKMODE attempt with the same synthetic upload and
the lab's original acknowledgment delay completed; Express reported 2,109
bytes sent in 44 seconds. Both directions exchanged lab messages, and the
server confirmed the upload in SQLite. RF conditions were not controlled
across the two attempts. See the [lab findings](TCP-KISS-B2F-LAB.md) for the
full context and limitations.
