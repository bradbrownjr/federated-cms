# Interactive services

**Status:** draft design. Nothing here is implemented. The section on
reading replies waits for a capture of a real Winlink Express reply
(test 2 in [ON-AIR-TESTS.md](ON-AIR-TESTS.md)).

A user asks for something, the node answers with the content or with a
short numbered menu, and the user replies with their choice. It works
from unmodified Winlink Express: requests are ordinary `INQUIRY`
messages and choices are ordinary replies.

Every round trip costs a radio connection, possibly hours apart, so the
design avoids asking when it doesn't need to: an exact match goes
straight to the content, and choices can be given in advance.

## Requests

A message to `INQUIRY` (any domain or none) with subject `REQUEST`, as
in [INQUIRY-ADAPTER.md](INQUIRY-ADAPTER.md). Besides Winlink catalog
IDs, URLs, `LIST` and `UPDATE_CAT`, a body line may be a **service
command**:

```text
VERB [arguments]
```

- `VERB` is one of the node's services, case-insensitive. Service verbs
  are reserved: the node's catalog must not use them as IDs.
- Arguments are free text, at most 200 characters, whitespace collapsed.
- Trailing numbers pick from the menu the command would produce, so one
  request can skip menus: `NEWS BBC 3` is "headlines from BBC, item 3".
- `HELP` lists the node's services; `HELP VERB` explains one.
- An unknown verb is rejected like any invalid line, and the reply
  includes the `HELP` list.

## Replies

One message per item, from `SERVICE`, as Winlink does today.

| Reply | Subject |
| --- | --- |
| Content | `INQUIRY - WIKI Mercury (planet)` |
| Menu | `INQUIRY - WIKI Mercury: choose [K7QX]` |
| Error | `INQUIRY - WIKI Mercury: not found` |

Subjects are at most 80 characters; a long argument is cut with `...`
before the code, never the code. The B2 limit on subject length is not
confirmed yet. UNVERIFIED.

Content bodies are rendered by `federated_cms.plaintext.render`: title,
`Source:`, `Fetched:`, then the text.

## Menus

```text
"Mercury" matches several articles.

1. Mercury (planet)
2. Mercury (element)
3. Mercury (mythology)
4. Freddie Mercury

Reply to this message with the numbers you want, for example 2 or 1 3.
Each number is the same as sending: WIKI <its title>
Menu K7QX for N0CALL, valid until 2026-12-02 01:00 UTC.
```

- At most 20 options. With more, the last line says `Reply MORE for the
  next 20.`
- Every option is also a command, so the menu still works after it
  expires, from any client. When an option's command is `VERB <label>`,
  one footer line says so (as above). Otherwise the command is printed
  under the option, indented three spaces.
- A content reply may end with a short menu of alternatives ("Other
  articles"), with its own code.

### Menu codes

- Four characters from `23456789ABCDEFGHJKLMNPQRSTUVWXYZ` (no `0`, `O`,
  `1`, `I`), drawn with `secrets`; about one million codes.
- Stored with the options, the callsign that asked, and an expiry
  (default 7 days, set by the sysop). Only that callsign can use it.
- An expired or unknown code gets a reply saying so, with the menu's
  commands if the node still has them.

### Reading a reply

**Pending the Express capture.** What is planned:

- The message is to `SERVICE` (any domain), and the subject contains
  `[CODE]`. A leading `Re:` or similar is ignored.
- The choice is taken from the first non-blank lines of the body, before
  any quoted original message: numbers, ranges such as `2-4`, or `MORE`,
  separated by spaces or commas. Anything else on those lines makes the
  reply invalid, and the user gets the menu again.
- To be confirmed from the capture: how Express marks quoted text,
  whether it changes the subject, and which headers carry the original
  message ID (which could replace the subject code).

## Services

Each node has a fixed table of services set up by its sysop: the verb, a
one-line description for `HELP`, the handler, and its limits. Requests
are dispatched only through that table; nothing in a message is ever run
as code. (radiomail, an earlier project, used `eval` on the subject;
this design must never do that.)

### `WIKI <subject> [| choice]`

Wikipedia articles as plain text, **summary by default**. Language set
per node (default `en`).

| Command | Reply |
| --- | --- |
| `WIKI Mercury (planet)` | Summary (the lead section), then a menu |
| `WIKI Mercury (planet) \| FULL` | The whole article, split across messages |
| `WIKI Mercury (planet) \| FACTS` | The infobox only |
| `WIKI Mercury (planet) \| Orbit` | One section, by name or menu number |

`|` separates the title from the choice; it cannot occur in a title
(MediaWiki *Manual:Page title*, naming restrictions).

**The summary's menu:**

```text
1. Full article (about 70,000 characters, 6 messages)
2. Facts box
3. Nomenclature
4. Physical characteristics
...
```

The full-article size is measured before the menu is sent, so the user
sees the cost in messages before asking for it. Sections listed are the
top-level ones; a section includes its subsections. `| 1`, `| 2` and so
on pick from this menu, so `WIKI Mercury (planet) | 1` is the full
article.

Steps:

1. **Search:** `action=query&list=search&srsearch=<subject>&srlimit=10`.
2. **Exact match:** when a result's title equals the subject
   (case-insensitive, after redirects), check it with
   `prop=pageprops&ppprop=disambiguation`. A page with the
   `disambiguation` property is not an article.
3. **No exact article match:** a menu of the search results, without
   disambiguation pages. A choice gives that article's summary.
4. **Summary:** `action=parse&page=<title>&prop=text&section=0`, plus
   `formatversion=2&redirects=1&disabletoc=1&disableeditsection=1` on
   every parse call. The infobox is left out. Then the menu above, plus
   up to five other search results as "Other articles".
5. **Sections:** `action=parse&page=<title>&prop=sections` gives each
   section's `index`, `toclevel` and `line`; a section is fetched with
   `section=<index>`.
6. **Full article:** `action=parse` without `section`, cleaned, then
   split at line breaks into parts of at most `max_chars`, one message
   each, subject `INQUIRY - WIKI Mercury (planet) (part 2 of 6)`. Every
   part counts against the callsign's message limit. Needs a `split`
   function beside the extractor's truncation (a change to
   [TEXT-EXTRACTOR.md](TEXT-EXTRACTOR.md)).
7. **Facts:** the infobox table only (the element with class
   `infobox`).
8. **Cleanup:** before the HTML reaches the extractor, remove
   Wikipedia's own clutter by class: reference markers and lists,
   navboxes, sidebars, hatnotes, maintenance boxes, anything marked
   `noprint`, and (except for `FACTS`) infoboxes. This needs a
   `drop_classes` option in `html_to_text`. The exact class list comes
   from saved real pages, kept as test fixtures.
9. **Attribution:** every reply, and every part of a split article, ends
   with `From Wikipedia: <article URL>. Text CC BY-SA 4.0
   (https://creativecommons.org/licenses/by-sa/4.0/); converted to plain
   text.` Wikipedia's copyright page asks for a link to the article, a
   licence notice, and a note that the text was changed.
10. **API manners** (API:Etiquette and the Wikimedia User-Agent policy):
    one request at a time, `Accept-Encoding: gzip`, and a User-Agent of
    the form `<client>/<version> (<contact>)`. The sysop must configure
    the contact (an email address or website); the service refuses to
    start without it, because Wikimedia may reject generic or empty
    agents with HTTP 403.

Measured on `Mercury (planet)`, 2026-11-25, before any class cleanup:
the lead section is 67 KB of HTML and 8,870 characters of text, mostly
infobox; the full article is 643 KB of HTML, 26 sections and about
111,000 characters. The menu figures above are illustrative until
cleanup is in place.

The TextExtracts API (`prop=extracts`) is not used: its documentation
says it is not actively maintained and asks that no new features be
built on it.

Checked against the live API on 2026-11-25: `Mercury` has
`pageprops.disambiguation` and `Mercury (planet)` does not;
`action=parse` with `formatversion=2` returns the HTML as a single
string in `parse.text`.

### `NEWS [source or topic] [| choice]`

RSS and Atom feeds chosen by the sysop, each with a short name and
topics. It works like `WIKI`: a short version first, the full text on
request.

| Command | Reply |
| --- | --- |
| `NEWS` | Menu of sources and topics |
| `NEWS BBC` | Menu of headlines, newest first (title and date) |
| `NEWS BBC \| DIGEST` | Every headline with its summary, in one message |
| `NEWS BBC \| 3` | Item 3: its summary, then a menu |
| `NEWS BBC \| 3 FULL` | Item 3: the full article, split across messages |

`NEWS WEATHER` works the same when `WEATHER` is a topic: headlines from
every source tagged with it, merged and newest first.

**An item's menu:**

```text
1. Full article (about 6,000 characters, 1 message)
2. More from BBC
```

- **Summary:** the item's `description` (RSS) or `summary` (Atom), as
  HTML through the extractor. When the feed gives none, the first two
  paragraphs of the fetched article.
- **Full article:** the feed's own full text when it carries one
  (`content:encoded` in RSS, `content` in Atom); otherwise the page at
  the item's link, fetched and run through the extractor. Split like
  `WIKI | FULL`, and the size is measured before the menu is sent.
- **Digest:** suits radio best: every headline in one message, one
  round trip. The menu numbers in it still work.
- **Attribution:** each reply names the source and the item's link.
  Copyright stays with the publisher; the sysop chooses feeds the node
  may relay.

**Feed reader** (its own module, spec and tests come first):

- RSS 2.0 (rssboard.org specification): channel `title`, `link`,
  `description`; item `title`, `link`, `description`, `pubDate`
  (RFC 822 dates, two- or four-digit years), `guid`. "At least one of
  title or description must be present." Full text in `content:encoded`
  (namespace `http://purl.org/rss/1.0/modules/content/`).
- Atom (RFC 4287, namespace `http://www.w3.org/2005/Atom`): entry `id`,
  `title`, `updated` (RFC 3339) required; `summary`, `content`, and
  `link rel="alternate"` (an entry without `content` must have one).
- Untrusted XML: a feed containing `<!DOCTYPE` or `<!ENTITY` is refused
  before parsing. Feeds have no need for a DTD, and this blocks entity
  expansion attacks whatever the installed Expat version (Python's XML
  documentation says Expat below 2.7.2 may be vulnerable). Size is
  capped after decompression.
- Feeds are fetched on a schedule set by the sysop and cached with
  their fetch time, so a request is answered from the cache, and still
  answered while the Internet link is down, with the age shown.

## Limits

Set per node, applied per callsign:

- items per request: 32 (the parser's limit);
- requests per hour and messages per day (defaults to be chosen from lab
  measurements);
- reply size per item: the extractor's `max_chars` (default 12,000);
- fetches: a timeout, `max_input_bytes`, and no private, loopback or
  link-local addresses (rns-apps' Gopher page refuses private addresses
  for the same reason).

## Compatibility

- Requests and choices use only what Express already does: a message to
  `INQUIRY`, and Reply.
- Service verbs are not added to the Winlink catalog file. How Express
  reads a `LIST` reply is untested (see ON-AIR-TESTS.md), so services
  are listed only through `HELP` for now.

## Open questions

- Reply parsing details (above), from the Express capture.
- Default rate limits.
- Whether other services also split long content across messages, as
  `WIKI | FULL` does, or cut it with a note (the extractor cuts today).
