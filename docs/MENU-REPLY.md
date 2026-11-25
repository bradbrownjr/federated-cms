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
- Standard library `email` only; parse with `email.policy.compat32` (see below);
  the Subject's RFC 2047 encoded words are decoded explicitly.

## What is read, in order

The first rule that fails raises its reason.

1. **Size.** More than 65536 bytes: `too large`, before any parsing. Exactly
   65536 is accepted.
2. **Parse.** Parse with `email.message_from_bytes(message,
   policy=email.policy.compat32)`. Do not use `email.policy.default`: its
   header parsers take tens of seconds on 64 KiB of quoted or parenthesised
   header values and raise `RecursionError` on long runs of `(`. A message
   with more than one top-level `Subject` header, or with any entity nested
   inside more than 10 containers, is `malformed message`. A container is a
   `multipart/*` or a `message/*` entity (`message/rfc822` and the rest), and
   the top-level message counts as one when it is a container. This is judged
   over the whole tree, before looking for the text part, and a
   `RecursionError` from deep nesting is reported the same way. Garbage with
   no headers is not malformed; it fails at the next step. Only the top-level
   header block is used for `Subject` and `To`: those headers on a part or on
   an embedded message are neither duplicates nor sources of a code or an
   address.
3. **Recipient.** Take the raw values of every top-level `To` header (there
   may be several; `Cc` and `Bcc` are ignored) and pass each to
   `email.utils.getaddresses`. A value that cannot be parsed, including one
   that makes `getaddresses` raise, contributes no address. Encoded words
   (`=?...?=`) are never decoded in addresses (RFC 2047 section 5), so
   `To: =?utf-8?b?U0VSVklDRUB4?=` is not `SERVICE@x`. The message must have
   at least one address whose local part, judged with `address_local_part`
   from `federated_cms.inquiry`, is `SERVICE`: the domain is ignored,
   matching is case-insensitive and ASCII only, and `SERVICES`, `SERVICE.X`
   and a non-ASCII look-alike do not match. Otherwise `no service address`.
   Tests use only well-formed addresses for the accepting cases, because
   `getaddresses` is stricter in some Python 3.11 patch releases than in
   others (gh-102988); the result for malformed lists is `no service
   address` whichever way it goes.
4. **Code.** Take the top-level `Subject` (empty when missing), unfold it
   (remove CR and LF) and decode RFC 2047 encoded words with
   `str(email.header.make_header(email.header.decode_header(value)))`; any
   exception there means the subject has no code. Search the whole decoded
   subject. A leading `Re:` needs no special handling, because the code is
   found anywhere. A *code-shaped token* is `[` + exactly four ASCII letters
   or digits + `]` (ASCII only: `[\u017f234]` has none). Exactly one must
   exist:
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
   `text/plain` and whose disposition is not `attachment`. Both come from the
   standard library as it reads the headers, with no extra trimming:
   `part.get_content_type()` (lower case; a value that is not of the form
   `a/b` falls back to the default, so `text/html/extra` is `text/plain`, while
   `text/plain (c)` is not `text/plain`) and `part.get_content_disposition()`
   (so `attachment (c)` is not `attachment`). With no `Content-Type` a part is
   `text/plain`, except a child of `multipart/digest`, which is
   `message/rfc822`. A later `text/plain` part is never tried, even when the
   first one is empty. No such part (HTML only, attachments only, a forwarded
   `message/rfc822`): `no text part`.
6. **Transfer decoding.** The value of `Content-Transfer-Encoding`, lower-cased
   and compared as a whole, the way `get_payload(decode=True)` reads it (no
   trimming, comments are not understood): absent or empty means `7bit`;
   `7bit`, `8bit` and `binary` are used as they are, `quoted-printable` and
   `base64` are decoded. Anything else, including `base64 (comment)` and the
   uuencode names: `unsupported transfer encoding`. The decoder is
   `part.get_payload(decode=True)`, and whatever bytes it returns are what is
   read. It is lenient: invalid base64 is not an error by itself. In the
   tested Python versions the base64 body `2` (one character, so invalid)
   comes back as `2` and is read as choice 2, and `Mg` (padding missing)
   decodes to `2`; characters outside the alphabet are dropped. What is left
   is judged by the next steps. The declared `charset` is ignored
   completely: Winlink Express declared `iso-8859-1` while the quoted text
   held UTF-8.
7. **Choice lines.** Work on the decoded bytes. Split into lines at CRLF, CR or
   LF. Stop at the first line that matches in full (`re.fullmatch`, no anchors,
   so no trailing space and no text after it) the bytes pattern
   `----- Message from .+ sent [0-9]{4}/[0-9]{2}/[0-9]{2} [0-9]{2}:[0-9]{2} -----`
   (ASCII digits only; the line may hold non-ASCII bytes in the name).
   Without such a line, all lines are used. A separator in the middle of a
   line is not a separator, and neither is one with a trailing space.
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
   reason (`5-4 x` is `invalid range`, `x 5-4` is `invalid choice`, `MORE 5-4`
   is `invalid range`). Only when every token is valid are the other rules
   applied, in this order: `MORE` together with any other token (even another
   `MORE`, even `MORE 1-999`) is `invalid choice`; then the count limit.
   Errors: no token at all (including no text, or a line holding only commas):
   `empty choice`. Any other token, a `-` with spaces around it, an en dash,
   a number above 999, a leading zero, an underscore (`1_0`, `1-1_0`: only
   ASCII digits are digits) or a stray control character: `invalid choice`. A
   range with `a > b`: `invalid range`.
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
