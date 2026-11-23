# Mailbox identity and authorization: design proposal

**Status: draft for review; not implemented or approved for production.**
The current B2F endpoint is a loopback-only laboratory. Its single configured
test account and `b2f_lab_messages` table do not implement this design.

## Scope and threat boundary

Private mailbox access requires both an authenticated principal and an explicit
grant to a particular mailbox and operation. Callsigns in BPQ, B2 headers,
federation envelopes, and network addresses are claims, not proof of identity.
An authenticated ingress adapter must not be allowed to select an arbitrary
principal on its own. A successful request does not authorize another request,
mailbox, sender address, or future session.

The design must address forged callsigns, stolen credentials, replay, a malicious
or compromised adapter, concurrent sessions, crashed transfers, cross-mailbox
MID collisions, stolen storage/backups, and forged delivery acknowledgements.
No user is assumed to have a Winlink account or share a Winlink credential.

## Proposed objects and policies

* **Principal:** locally provisioned opaque ID, status and authentication
  methods. Callsign is an operator-managed label, not the primary key.
* **Mailbox:** stable opaque ID, owner principal, status, retention and quota.
* **Grant:** principal, mailbox, one or more explicitly scoped actions (list,
  read, deposit, send-as, delete, or administer), issue and expiry times, issuer,
  and revocation state. A grant to read does not imply send-as or administration.
* **Credential:** bound to one principal and one method. Enrollment, rotation,
  recovery and revocation are administrative actions, audited without secrets.
* **Session:** an adapter obtains a short-lived internal authorization context
  only after verifying a credential. It carries principal, authentication method,
  session identifier and expiry, not a caller-supplied list of privileges.

The service derives authorization from a trusted context and current grants on
each operation, including final delivery acknowledgement; it fails closed on
revocation, expiry, unavailable policy state or malformed identity. A process
boundary, when added, must authenticate the adapter and protect the internal
channel. Merely passing a Python object or string is not an authentication
boundary against other local processes. Account provisioning remains a separate
local sysop workflow, not a command available to an RF terminal session.

## Authentication profiles

1. **Protected local/IP administration:** use a separately reviewed,
   authenticated protected channel and a modern authentication scheme. Where
   passwords are used, store salted, costed password verifiers; rate-limit
   failed attempts and provide explicit credential recovery and revocation.
2. **Legacy B2F client compatibility:** the existing `;PQ:` / `;PR:` response
   depends on a recoverable shared secret, a short challenge and a short MD5-based
   response. A conventional salted password verifier cannot independently
   compute the required answer. It is vulnerable to offline guessing, challenge
   collisions and server impersonation, and does not encrypt mailbox contents.
   The current lab implementation is not suitable for production. Any future
   compatibility mode needs a separate operator risk decision, independent
   credentials, protected secret storage, throttling, replay analysis, and an
   explicitly scoped access profile; those controls cannot turn it into a
   modern phishing-resistant protocol. Never reuse Winlink or admin passwords.
3. **BPQ terminal:** a first-line callsign remains unverified context. No private
   mailbox access or delegation follows from it. Public weather receipts remain
   separate from private mailbox privileges.

Normal Internet-side transport protection cannot automatically be carried onto
amateur RF. Any proposed RF profile requires mode- and jurisdiction-specific
review and an operator-controlled transmit gate before on-air use.

## Shared mailbox service contract (proposed)

An adapter presents a trusted session context to `MailboxService`, which checks
the requested mailbox and action against current grants. The service offers
bounded operations such as `list_pending`, `read_message`, `deposit_message`,
`claim_delivery`, and `ack_delivery`. These names describe a proposed boundary,
not current Python APIs. Results are immutable records with a local message ID,
protocol MID where applicable, exact raw B2 bytes and attachment metadata.

Message bytes, mailbox delivery records, duplicate decisions, and acceptance
receipts must commit atomically before an acknowledgement. A delivery claim has
an unguessable lease token and deadline; only the owning authorized session can
acknowledge an unexpired claim. Completion after expiry, revocation, failure or
loss of the connection remains retryable. Retried messages preserve their MID;
an existing MID must not silently replace different bytes. A B2F peer that is
refused at proposal time never sends its bytes, so content collisions cannot
be detected from that exchange alone. A receipt proves only the stated local
or peer acceptance, not end-to-end delivery.

The existing `messages` table stores text rather than raw B2 and attachments;
`b2f_lab_messages` has no grants or authenticated delivery claims. Neither is a
production mailbox API. A migration must preserve byte-exact B2 data and avoid
promoting lab fixtures into private user mailboxes or treating an old callsign
as verified identity. Use an explicit schema version, tested rollback/backup
procedure, and a deliberate operator-controlled migration.

## Verification gates before connecting adapters to private mail

* Tests deny wrong, missing, revoked, expired and cross-mailbox grants, spoofed
  callsigns, altered contexts and untrusted adapters.
* Crash/restart, duplicate, lease-expiry, concurrent claim and acknowledgement
  tests establish transactional behavior and retained messages.
* Binary B2 fixtures, attachments, encoding, byte caps and malformed input are
  tested with independent client implementations without production systems.
* A security review covers credential provisioning, secret exposure, side
  channels, quotas, audit redaction, backups, key rotation and recovery.
* Separate reviews establish intended IP and RF deployment policies. No
  production or RMS gateway compatibility is inferred from local lab tests.

## Executable policy experiment

`federated_cms.policy_experiment` is a disconnected SQLite test model for
principal, mailbox, action, grant and session rules. It operates on synthetic
fixtures only and is not wired to B2F, BPQ, actual mail, or credential
verification. The fixture-provisioning and session-opening methods are
trusted in-process calls, not an authentication or administration boundary.
Use a dedicated temporary database; no existing lab or private mail is imported.

The experiment checks the current session, principal, mailbox and grant inside
the same SQLite write transaction as each fixture operation. It checks expiry
again immediately before commit, but the check and physical commit are not
simultaneous. Concurrent writes and revocations serialize: an operation that
has already acquired the write transaction may complete before a later revoke.
Its persisted clock high-water mark catches tested backward steps, but it does
not resolve clock tampering, restoration from backup, or distributed clocks.
It provides no per-message delivery claims, secret provisioning, rate limiting,
audit pipeline, protected adapter transport, or production deployment policy.

Run the deterministic, offline policy tests with:

```text
PYTHONPATH=src python -m unittest tests.test_policy_experiment -v
```

References: [threat model](THREAT-MODEL-M0.md), [BPQ interface](BPQ-LOCAL-INTERFACE.md),
[Winlink data flow](https://winlink.org/sites/default/files/downloads/winlink_data_flow_and_data_packaging.pdf),
[NIST SP 800-63B-4 authenticators](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/),
and [SQLite WAL](https://www.sqlite.org/wal.html). NIST is a security reference,
not a claim that this research prototype meets a federal assurance level.
