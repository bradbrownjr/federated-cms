# Tests for the operator

Tests that need the operator's radios, Winlink Express or a Winlink
account. Each says what to do and what to report. When one is done, move
its result into the document it names and delete it here.

## Open

### 1. Express over RF against the lab server, no relay

**Why:** the server now sends its own identifier, `[FCMS-0.1-B2FWHM$]`,
with no relay changing it. Express accepted that identifier before, but
only through the relay. This checks the direct path on the air.

**Needs:** the radio room PC with its radio and KISS modem; Express on a
second computer with its own radio and TNC; the lab database and lab
password file; synthetic messages only.

1. On the radio room PC, update to `main` (`git pull`). Set `PYTHONPATH`
   to this repository's `src` and the kissterm checkout (`;` between them
   on Windows).
2. Queue a synthetic download: run the seeding snippet in
   [TCP-KISS-B2F-LAB.md](TCP-KISS-B2F-LAB.md) ("Operator-controlled use").
   Note the message ID it prints.
3. Start the B2F server:
   ```text
   python -m federated_cms.b2f --port 63191 --lab-account LAB_ACCOUNT --password-file LAB_PASSWORD_FILE --database LAB_DATABASE
   ```
4. In another window, start the bridge:
   ```text
   python -m federated_cms.kiss_lab --kiss-host MODEM_HOST --kiss-port MODEM_PORT --station STATION_CALL --peer CLIENT_CALL --b2f-port 63191 --ack-delay 1 --enable-tx
   ```
5. In Express: Packet Winlink session, TNC model `ACKMODE`, lab password
   in a profile you will reset afterwards. Put one message with two
   small synthetic attachments in the Outbox.
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

### 2. What Express sends when you reply to a SERVICE message

**Why:** interactive services send a menu and you reply with a number.
The server has to find your choice in the reply, so we need to know
exactly what Express puts in one. No radio needed.

1. In Express, open an earlier `INQUIRY - ...` reply from `SERVICE`.
2. Press Reply. Note any dialog or option about including the original
   message, and what you choose.
3. Type `2` as the only text above anything Express adds. Do not change
   the subject.
4. Post it to the Outbox. **Do not send it**; delete it from the Outbox
   after step 5 so it does not go out on the next connection.
5. Copy the newest `.mime` file from `C:\RMS Express\<CALL>\Messages\`
   (sort by date). If the Outbox copy is not there, say so.

**Report:** the `.mime` file content (headers and body). Remove any
password or unrelated personal details first. Also the subject line
exactly as Express shows it, and the reply option you chose in step 2.

**Result goes to:** a new "Replies" section of
`docs/INTERACTIVE-SERVICES.md` (to be written).

## Later (not ready yet)

- **Express reads our own catalog.** Needs the INQUIRY robot on the lab
  server. Send `LIST` to `INQUIRY` over the lab, then check whether
  Express rewrites `Data\Winlink Queries.txt` from our reply.
- **A menu round trip.** Needs the robot and `WIKI`. Request
  `WIKI Mercury`, reply with a number, and check the article that comes
  back.
