"""Verify one uploaded synthetic lab message against local attachment fixtures."""

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys

from federated_cms.b2_message import B2MessageError, message_attachments


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="existing synthetic lab SQLite database")
    parser.add_argument("account", help="lab mailbox account")
    parser.add_argument("mid", help="incoming B2 message ID")
    parser.add_argument("files", nargs="+", type=Path, help="original fixture paths in B2 File header order")
    args = parser.parse_args(argv)

    try:
        database_uri = f"{args.database.absolute().as_uri()}?mode=ro"
        with closing(sqlite3.connect(database_uri, uri=True)) as database:
            row = database.execute(
                "SELECT raw FROM shared_lab_mailbox_messages WHERE account=? AND mid=? AND direction='in'",
                (args.account.upper(), args.mid),
            ).fetchone()
        if row is None:
            print("Incoming lab message was not found.", file=sys.stderr)
            return 1
        attachments = message_attachments(row[0])
        if len(attachments) != len(args.files):
            print("Attachment count differs from the fixture count.", file=sys.stderr)
            return 1
        for path, (name, content) in zip(args.files, attachments):
            if os.fsencode(path.name) != name:
                print("An attachment name differs from its fixture basename.", file=sys.stderr)
                return 1
            with path.open("rb") as fixture:
                if fixture.read(len(content) + 1) != content:
                    print("An attachment differs from its fixture bytes.", file=sys.stderr)
                    return 1
    except (B2MessageError, OSError, sqlite3.Error):
        print("Unable to read or validate the lab message or fixtures.", file=sys.stderr)
        return 1

    print(f"Verified {len(attachments)} incoming lab attachment(s) byte-for-byte.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
