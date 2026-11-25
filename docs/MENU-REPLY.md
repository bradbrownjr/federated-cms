# Menu replies: parser contract

`federated_cms.menu_reply` reads the message a Winlink client sends when a
user answers a numbered menu (design: `INTERACTIVE-SERVICES.md`, branch
`docs/interactive-services`, "Menus" and "Reading a reply"). It does no I/O
and looks nothing up: it returns the menu code and the choice, and the caller
checks the code, the sender and the expiry. Tests: `tests/test_menu_reply.py`;
fixtures: `tests/fixtures/menu_reply/` (synthetic, callsign `N0CALL`).

## API

```python
def parse_menu_reply(message: bytes) -> MenuReply

@dataclass(frozen=True)
class MenuReply:
    code: str                 # 4 characters, upper case
    numbers: tuple[int, ...]  # sorted, unique, 1..999; empty when more is True
    more: bool                # the user asked for the next page

class MenuReplyError(ValueError):
    reason: str               # one of the reasons below; str(error) == reason
```

- `message` is the whole MIME message as bytes. Any other type raises
  `TypeError`.
- For every `bytes` input the function returns a `MenuReply` or raises
  `MenuReplyError`. No other exception may escape (no `RecursionError`,
  `UnicodeError`, `KeyError` or `email` internals).
- `MenuReplyError(reason)` takes the reason as its only argument.
- Standard library `email` only; parse with `email.policy.default` so that
  RFC 2047 encoded words in headers are decoded.

## What is read, in order

The first rule that fails raises its reason.

1. **Size.** More than 65536 bytes: `too large`, before any parsing. Exactly
   65536 is accepted.
2. **Parse.** A message with more than one `Subject` header, or with any
   part nested inside more than 10 `multipart` containers (the top-level
   message counts as one when it is multipart): `malformed message`. This is
   judged over the whole tree, before looking for the text part, and a
   `RecursionError` from deep nesting is reported the same way. Garbage with
   no headers is not malformed; it fails at the next step.
3. **Recipient.** The `To` headers (all of them; `Cc` and `Bcc` are ignored)
   must include an address whose local part is `SERVICE`: addresses are
   extracted with `email.utils.getaddresses` (display names ignored), then
   judged with `address_local_part` from `federated_cms.inquiry`, so the
   domain is ignored, matching is case-insensitive and ASCII only, and
   `SERVICES`, `SERVICE.X` and a non-ASCII look-alike do not match. Otherwise
   `no service address`.
4. **Code.** Take the `Subject` (empty when missing) and remove any number of
   leading `Re:` (case-insensitive, optional spaces around `Re` and the colon).
   In the rest, a *code-shaped token* is `[` + exactly four ASCII letters or
   digits + `]`. Exactly one must exist:
   - none: `no code`;
   - two or more, equal or not: `multiple codes`;
   - one, but a character is outside `23456789ABCDEFGHJKLMNPQRSTUVWXYZ`
     (case-insensitive): `invalid code`. `0`, `O`, `1` and `I` are outside.

   The code is returned upper-case. Other bracketed text (`[planet]`,
   `[ABCDE]`, `[ K7QX ]`) is ignored. A reply builder must therefore never
   echo `[` or `]` from a user's arguments into a subject: an argument such
   as `[Mars]` would look like a second code and the user's answer would be
   refused.
5. **Text part.** Walk the MIME tree depth first, in order, into `multipart/*`
   only (never into `message/*`), and take the first part whose content type is
   `text/plain` (case-insensitive) and whose `Content-Disposition` is not
   `attachment`. A message with no `Content-Type` is `text/plain`. A later
   `text/plain` part is never tried, even when the first one is empty. No such
   part (HTML only, attachments only, a forwarded `message/rfc822`): `no text
   part`.
6. **Transfer decoding.** `Content-Transfer-Encoding` (case-insensitive, absent
   means `7bit`): `7bit`, `8bit` and `binary` are used as they are,
   `quoted-printable` and `base64` are decoded. Anything else: `unsupported
   transfer encoding`. Decoding is lenient where the standard library is
   (invalid base64 may yield fewer bytes); what is left is judged by the next
   steps. The declared `charset` is ignored completely: Winlink Express
   declared `iso-8859-1` while the quoted text held UTF-8.
7. **Choice lines.** Work on the decoded bytes. Split into lines at CRLF, CR or
   LF. Stop at the first line that matches, in full, the bytes pattern
   `^----- Message from .+ sent \d{4}/\d{2}/\d{2} \d{2}:\d{2} -----$` (ASCII
   digits only; no trailing space; the line may hold non-ASCII bytes in the
   name). Without such a line, all lines are used. A separator in the middle of
   a line is not a separator, and neither is one with a trailing space.
8. **ASCII.** The lines before the separator must be ASCII bytes, else
   `non-ASCII`. Nothing from the separator on is decoded or read.
9. **Tokens.** Remove leading and trailing spaces and tabs from each line;
   skip lines that are then empty. Split the rest at runs of spaces, tabs and
   commas; empty pieces are dropped. Each token is one of:
   - a number `[1-9][0-9]{0,2}` (1 to 999, no leading zero, ASCII digits);
   - a range `a-b` of two such numbers with no spaces, `a <= b`;
   - `MORE`, case-insensitive, and then it must be the only token in the whole
     reply.

   Tokens are checked in reading order and the first bad token decides the
   reason (`5-4 x` is `invalid range`, `x 5-4` is `invalid choice`); the
   rules about `MORE` and the count come after every token is valid.
   Errors: no token at all (including no text, or a line holding only commas):
   `empty choice`. Any other token, `MORE` together with anything (even another
   `MORE`), a `-` with spaces around it, an en dash, a number above 999, a
   leading zero or a stray control character: `invalid choice`. A range with
   `a > b`: `invalid range`.
10. **Result.** The union of all numbers and ranges, sorted, without
    duplicates. More than 20 numbers after removing duplicates: `too many
    choices`. A range must not be expanded before this limit is known to hold
    (`1-999` has to fail quickly and cheaply).

`MORE` gives `MenuReply(code, (), True)`; otherwise `more` is `False`.

## Reasons

`too large`, `malformed message`, `no service address`, `no code`,
`multiple codes`, `invalid code`, `no text part`, `unsupported transfer
encoding`, `non-ASCII`, `empty choice`, `invalid choice`, `invalid range`,
`too many choices`.

## Observed message

The structure the fixture copies is recorded in `INTERACTIVE-SERVICES.md`
("Reading a reply"): Winlink Express, 2026-11-25. Headers `Subject`, `To`,
`Message-ID`, `X-Location`, `Content-Type: multipart/mixed`; one
`text/plain; charset="iso-8859-1"` quoted-printable part holding the choice,
the separator line, then the unmarked original, in which a UTF-8 non-breaking
space appears as `=C2=A0`. There is no `MIME-Version` header in the sample, so
the parser must not require one; there is no `In-Reply-To` or `References`, so
the subject code is the only link.

## Open points

- A reply that does not start with `Re:` is accepted when it has the code. The
  code, the callsign and the expiry are the real checks, not the prefix.
- Whether other clients (Pat, Outpost, Winlink Web) write the choice the same
  way is untested. Pat's reply format should be checked before a client other
  than Express is declared supported.
