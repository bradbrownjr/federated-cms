# Tests for the operator

Tests that need the operator's radios, Winlink Express or a Winlink
account. Each says what to do and what to report. When one is done, move
its result into the document it names and delete it here.

## Open

### Express over RF against the lab server, no relay

**Why:** the server sends its own identifier, `[FCMS-0.1-B2FWHM$]`.
This checks that Express accepts it directly over RF.

**Needs:** the lab host (runs the B2F server and bridge); soundmodem on
the radio room PC, reached from the lab host as TCP KISS over the
network, with the radio room radio; the Express laptop with its own
radio and TNC; the lab database and lab password file; synthetic
messages only.

**Before starting: credentials.** Read the *Winlink Express account
warning* in [TCP-KISS-B2F-LAB.md](TCP-KISS-B2F-LAB.md). Changing a
password in Express can also change it on the real Winlink account, and
a separate local profile is not assumed to prevent that. Use an
independently configured test client with disposable credentials, and
confirm it is isolated from real account updates before entering them.
If that cannot be confirmed, do not run this test.

Steps 1 to 4 and 7 run on the lab host. The operator keeps control of
both transmitters.

1. Update the lab host's checkout to `main`. Set `PYTHONPATH` to this
   repository's `src` and a kissterm checkout at the tested commit named
   under *Prerequisites* in TCP-KISS-B2F-LAB.md.
2. Queue a synthetic download with the download seeding snippet under
   *Operator-controlled use* in TCP-KISS-B2F-LAB.md. Note the message ID
   it prints.
3. Start the B2F server:
   ```text
   python -m federated_cms.b2f --port 63191 --lab-account LAB_ACCOUNT --password-file LAB_PASSWORD_FILE --database LAB_DATABASE
   ```
4. In another window, start the bridge, pointing `MODEM_HOST` and
   `MODEM_PORT` at soundmodem's KISS port on the radio room PC:
   ```text
   python -m federated_cms.kiss_lab --kiss-host MODEM_HOST --kiss-port MODEM_PORT --station STATION_CALL --peer CLIENT_CALL --b2f-port 63191 --ack-delay 1 --enable-tx
   ```
   Running it first with `--check-only` instead of `--enable-tx` checks
   the network path to soundmodem without arming transmit. The bridge
   stops after `--seconds` (default 300) or `--max-frames` (default
   500), so connect from Express within that time.
5. In Express on the laptop: Packet Winlink session, TNC model
   `ACKMODE`, the disposable test credentials from above. Put one message
   with two small synthetic attachments in the Outbox.
6. Connect directly to the lab station. Low power, antennas apart.
7. After the session, verify the upload:
   ```text
   python -m tests.verify_lab_attachments LAB_DATABASE LAB_ACCOUNT UPLOAD_MID FILE1 FILE2
   ```

**Report:**
- Did Express connect, or refuse with "peer-to-peer" / "invalid SID"?
- Did the upload and the download both complete? Does the downloaded
  attachment open and match?
- Was there a final DISC/UA?
- Bytes and duration from the Express session log; number of retries.
- The `verify_lab_attachments` output.
- The bridge's console output (frame counts; it logs no message content).

**Result goes to:** the Express rows of
[WINLINK-INTEROPERABILITY.md](WINLINK-INTEROPERABILITY.md).

### Express addresses the node's service address, lab server only

**Why:** native requests go to the node's own service address, `FCMS` by
default, which has no Winlink account
([INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md#two-layers)). This
checks whether Express lets a user address it, bare and with a domain,
what it puts on the wire, and whether Reply to a message From `FCMS`
goes back to `FCMS`.

**Needs:** the lab host running the B2F server over Telnet (no radio
needed), the Express laptop, the lab database and lab password file;
synthetic messages only. **Never against Winlink's production servers**:
every session in this test connects to the lab server.

**Before starting: credentials.** As for the RF test above: read the
*Winlink Express account warning* in [TCP-KISS-B2F-LAB.md](TCP-KISS-B2F-LAB.md),
use an independently configured test client with disposable credentials,
and confirm it is isolated from real account updates. If that cannot be
confirmed, do not run this test.

1. On the lab host, update the checkout to `main` and start the B2F
   server as in step 3 above.
2. Queue one synthetic download addressed to the lab account, with
   `From: FCMS` and subject `FCMS - lab test [K7QX]`, using the download
   seeding snippet under *Operator-controlled use* in
   TCP-KISS-B2F-LAB.md with those two headers changed. Note its message
   ID.
3. In Express, compose a new message `To: FCMS`, subject `lab 1`, body
   `HELP`. Compose a second `To: FCMS@LAB_DOMAIN` (the lab server's
   domain), subject `lab 2`, body `HELP`. Note any warning or change
   Express makes to either address while composing or saving.
4. Connect to the lab server (Telnet Winlink session pointed at the lab
   host) and send both. The download from step 2 arrives in the same
   session.
5. Open the downloaded message. Note how Express shows its sender. Press
   Reply, type `2` above the quoted text, put it in the Outbox, and send
   it in a second lab session.
6. On the lab host, print the stored `in` rows' `To:` and `Subject:`
   lines and the first line of each body for the three uploads (no other
   content).

**Report:**
- Did Express accept `FCMS` and `FCMS@LAB_DOMAIN` without complaint? Any
  warning, and did it rewrite either address?
- The `To:` header of each upload as stored by the server (for example
  `FCMS`, `FCMS@winlink.org` or `SMTP:FCMS@LAB_DOMAIN`).
- How Express showed the sender of the `From: FCMS` message.
- For the Reply: its `To:`, its subject, and the separator line above the
  quoted text, as stored. Does `parse_menu_reply` on the stored message
  return code `K7QX` and choice 2?
- The Express version.

**Result goes to:** [INTERACTIVE-SERVICES.md](INTERACTIVE-SERVICES.md#reading-a-reply)
(the UNVERIFIED note there) and [INQUIRY-ADAPTER.md](INQUIRY-ADAPTER.md#check_service_address).

## Later (not ready yet)

- **Express reads a catalog from this server.** Needs the INQUIRY robot on the lab
  server. Send `LIST` to `INQUIRY` over the lab, then check whether
  Express rewrites `Data\Winlink Queries.txt` from our reply.
- **A menu round trip.** Needs the robot and `WIKI`. Send `WIKI Mercury`
  to `FCMS`, reply with a number, and check the article that comes back.
