"""Contract tests for the INQUIRY robot configuration (slice 1).

See docs/INQUIRY-ROBOT-SLICE1.md, "Configuration" and "Command line".
Expected problem strings are written from that contract. Do not change these
tests to make an implementation pass.
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from federated_cms.robot import main
from federated_cms.robot_config import CatalogEntry, ConfigError, Limits, RobotConfig, load_config
from tests.lab_mailbox_helpers import mailbox_call
from tests.robot_helpers import BASIC, BASIC_DIR, message

NODE_TOML = '[node]\nname = "N1TEST-10"\ncontact = "sysop@example.org"\n'
CATALOG_TOML = '[catalog]\nfile = "catalog.tsv"\n'


def top_level(extra: str) -> str:
    """Put bare top-level keys before [node]; tables after it."""
    return NODE_TOML + extra if extra.startswith("[") else extra + NODE_TOML


class ConfigCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.dir = self.root / "conf"
        self.dir.mkdir()
        (self.dir / "files").mkdir()
        (self.dir / "files" / "ok.txt").write_bytes(b"Fine.\n")

    def write(self, toml: str, catalog: str | bytes | None = None) -> Path:
        path = self.dir / "robot.toml"
        path.write_bytes(toml.encode("utf-8"))
        if catalog is not None:
            data = catalog if isinstance(catalog, bytes) else catalog.encode("utf-8")
            (self.dir / "catalog.tsv").write_bytes(data)
        return path

    def problems(self, toml: str, catalog: str | bytes | None = None) -> set[str]:
        path = self.write(toml, catalog)
        with self.assertRaises(ConfigError) as caught:
            load_config(path)
        error = caught.exception
        self.assertIsInstance(error, ValueError)
        self.assertEqual(str(error), "\n".join(error.problems))
        self.assertIsInstance(error.problems, tuple)
        return set(error.problems)

    def assertProblem(self, toml, expected, catalog=None):
        self.assertEqual(self.problems(toml, catalog), {expected})


class LoadTests(ConfigCase):
    def test_basic_fixture(self):
        config = load_config(BASIC)
        self.assertIsInstance(config, RobotConfig)
        self.assertEqual(config.config_dir, BASIC_DIR.resolve())
        self.assertEqual(config.node_name, "N1TEST-10")
        self.assertEqual(config.contact, "sysop@example.org")
        self.assertEqual(config.reply_from, "SERVICE")
        self.assertEqual((config.menu_expiry_days, config.max_wait_minutes, config.same_session_wait_seconds),
                         (7, 360, 2))
        self.assertIs(config.url_items, True)
        self.assertEqual(config.deny_networks, ())
        self.assertEqual(config.blocked, frozenset())
        self.assertEqual(config.limits, Limits())
        self.assertEqual(Limits(), Limits(32, 10, 100, 1_000_000, 12, 5, 100))
        self.assertEqual(dict(config.account_limits), {})
        self.assertEqual((config.node_fetches_per_hour, config.node_bytes_per_day), (600, 5_000_000))
        self.assertEqual(config.services, ())
        self.assertEqual(config.catalog, (
            CatalogEntry("NODE_INFO", "LOCAL", "text:files/node-info.txt", "About this node",
                         "This is a test node for the INQUIRY robot.\nIt answers HELP, LIST and the items in LIST.\n"),
            CatalogEntry("NET_TIMES", "NETS", "text:files/net-times.txt", "Weekly net schedule",
                         "Test net: Mondays 19:00 local time.\n"),
            CatalogEntry("BAND_PLAN", "LOCAL", "text:files/band-plan.txt", "Local band plan",
                         "Synthetic band plan for tests.\n"),
        ))

    def test_accepts_str_path(self):
        self.assertEqual(load_config(str(BASIC)).node_name, "N1TEST-10")

    def test_all_keys(self):
        config = load_config(self.write(
            '[node]\nname = "N1TEST"\ncontact = "https://example.org/contact"\nreply_from = "SERVICE"\n'
            '[robot]\nmenu_expiry_days = 90\nmax_wait_minutes = 10080\nsame_session_wait_seconds = 0\n'
            'url_items = false\ndeny_networks = ["192.0.2.0/24", "2001:db8::/32"]\nblocked = ["N0BAD", "N0BAD-1"]\n'
            '[limits]\nitems_per_request = 1\nrequests_per_hour = 1\nmessages_per_day = 50\n'
            'bytes_per_day = 1000000000\nparts_per_item = 50\ninvalid_per_day = 1\nundelivered_max = 1\n'
            'node_fetches_per_hour = 1\nnode_bytes_per_day = 1\n'
            '[limits.accounts.N0EOC]\nmessages_per_day = 300\nbytes_per_day = 3_000_000\n'
            '[limits.accounts."N0EOC-1"]\nitems_per_request = 32\n'))
        self.assertEqual(config.node_name, "N1TEST")
        self.assertEqual((config.menu_expiry_days, config.max_wait_minutes, config.same_session_wait_seconds),
                         (90, 10080, 0))
        self.assertIs(config.url_items, False)
        self.assertEqual(config.deny_networks, ("192.0.2.0/24", "2001:db8::/32"))
        self.assertEqual(config.blocked, frozenset({"N0BAD", "N0BAD-1"}))
        base = Limits(1, 1, 50, 1_000_000_000, 50, 1, 1)
        self.assertEqual(config.limits, base)
        self.assertEqual(config.limits_for("N0CALL"), base)
        self.assertEqual(config.limits_for("N0EOC"), Limits(1, 1, 300, 3_000_000, 50, 1, 1))
        self.assertEqual(config.limits_for("N0EOC-1"), Limits(32, 1, 50, 1_000_000_000, 50, 1, 1))
        self.assertEqual((config.node_fetches_per_hour, config.node_bytes_per_day), (1, 1))
        self.assertEqual(config.catalog, ())

    def test_contact_optional_without_url_items(self):
        config = load_config(self.write('[node]\nname = "N1TEST-10"\n[robot]\nurl_items = false\n'))
        self.assertIsNone(config.contact)

    def test_catalog_comments_blank_lines_crlf_and_text_normalised(self):
        (self.dir / "files" / "crlf.txt").write_bytes(b"one\r\ntwo\rthree")
        config = load_config(self.write(NODE_TOML + CATALOG_TOML,
                                        b"# comment\r\n\r\nA_1\tCAT\ttext:files/crlf.txt\tCRLF text\r\n"
                                        b"#\tnot\tan\tentry\n"))
        self.assertEqual(config.catalog, (CatalogEntry("A_1", "CAT", "text:files/crlf.txt", "CRLF text",
                                                       "one\ntwo\nthree\n"),))

    def test_text_file_at_size_limit(self):
        (self.dir / "files" / "big.txt").write_bytes((b"x" * 99 + b"\n") * 120)  # 12,000 bytes
        config = load_config(self.write(NODE_TOML + CATALOG_TOML, "BIG\tCAT\ttext:files/big.txt\tBig\n"))
        self.assertEqual(len(config.catalog[0].text), 12000)

    def test_ten_thousand_entries_load(self):
        rows = "".join(f"ID{n}\tCAT\ttext:files/ok.txt\tEntry\n" for n in range(10000))
        config = load_config(self.write(NODE_TOML + CATALOG_TOML, rows))
        self.assertEqual(len(config.catalog), 10000)

    def test_entry_count_ignores_lines_that_are_only_cr(self):
        rows = "".join(f"ID{n}\tCAT\ttext:files/ok.txt\tEntry\r\n" for n in range(10000))
        config = load_config(self.write(NODE_TOML + CATALOG_TOML, rows + "\r\n" * 5 + "#x\r\n"))
        self.assertEqual(len(config.catalog), 10000)

    def test_text_paths_resolve_against_config_directory(self):
        (self.dir / "cat").mkdir()
        (self.dir / "cat" / "catalog.tsv").write_bytes(b"OK1\tCAT\ttext:files/ok.txt\tEntry\n")
        toml = NODE_TOML + '[catalog]\nfile = "cat/catalog.tsv"\n'
        config = load_config(self.write(toml))
        self.assertEqual(config.catalog[0].text, "Fine.\n")
        # Relative to the catalog's directory this would be conf/files/ok.txt;
        # relative to the config directory it is outside.
        (self.dir / "cat" / "catalog.tsv").write_bytes(b"OK1\tCAT\ttext:../files/ok.txt\tEntry\n")
        self.assertProblem(toml, "catalog:1: text file outside config directory")
        (self.dir / "cat" / "files").mkdir()
        (self.dir / "cat" / "files" / "only-here.txt").write_bytes(b"x\n")
        (self.dir / "cat" / "catalog.tsv").write_bytes(b"OK1\tCAT\ttext:files/only-here.txt\tEntry\n")
        self.assertProblem(toml, "catalog:1: text file missing")

    def test_exact_size_limits_are_accepted(self):
        contact = "a@" + "b" * 122 + ".org"
        self.assertEqual(len(contact), 128)
        head = f'[node]\nname = "N1TEST-10"\ncontact = "{contact}"\n'
        padded = head + "#" * (1_000_000 - len(head) - 1) + "\n"
        self.assertEqual(len(padded.encode()), 1_000_000)
        config = load_config(self.write(padded))
        self.assertEqual(config.contact, contact)

    def test_config_is_frozen(self):
        config = load_config(BASIC)
        with self.assertRaises(Exception):
            config.node_name = "N0CALL"


class RefusalTests(ConfigCase):
    def test_file_level(self):
        with self.assertRaises(ConfigError) as caught:
            load_config(self.dir / "missing.toml")
        self.assertEqual(caught.exception.problems, ("config: cannot read",))
        self.assertProblem("[node\nname = 1", "config: invalid TOML")
        self.assertProblem(NODE_TOML + "x = 1\nx = 2\n", "config: invalid TOML")
        self.assertProblem(NODE_TOML + "#" * 1_000_001, "config: too large")
        self.assertProblem("#" * 1_000_001, "config: too large")

    def test_unknown_keys(self):
        cases = [
            ("[nodes]\nname = 1\n", "nodes: unknown key"),
            ("extra = 1\n", "extra: unknown key"),
            ("[robot]\nmenu_expiry_day = 7\n", "robot.menu_expiry_day: unknown key"),
            ("[node.sub]\nx = 1\n", "node.sub: unknown key"),
            ("[limits]\nfrobs = 1\n", "limits.frobs: unknown key"),
            ("[limits.accounts.N0EOC]\nnode_bytes_per_day = 1\n",
             "limits.accounts.N0EOC.node_bytes_per_day: unknown key"),
            ("[limits.accounts.N0EOC]\nnode_fetches_per_hour = 1\n",
             "limits.accounts.N0EOC.node_fetches_per_hour: unknown key"),
            ("[catalog]\nfile = \"catalog.tsv\"\nformat = \"tsv\"\n", "catalog.format: unknown key"),
            ('[[service]]\nverb = "AB"\nhandler = "x"\ndescription = "d"\ncolour = 1\n', None),
            ('"bad\\u0007key" = 1\n', "bad?key: unknown key"),
        ]
        for extra, expected in cases:
            with self.subTest(extra=extra):
                catalog = "" if "catalog" in extra else None
                toml = extra + NODE_TOML if not extra.startswith("[") else NODE_TOML + extra
                problems = self.problems(toml, catalog)
                if expected is None:
                    self.assertIn("service[0].colour: unknown key", problems)
                else:
                    self.assertEqual(problems, {expected})

    def test_node(self):
        cases = [
            ("", {"node.name: required", "node.contact: required"}),
            ('[node]\ncontact = "sysop@example.org"\n', {"node.name: required"}),
            ('[node]\nname = "N1TEST-10"\n', {"node.contact: required"}),
            ('node = 5\n', {"node: must be a table"}),  # keys of a mistyped table are not checked
            ('[node]\nname = 5\ncontact = "sysop@example.org"\n', {"node.name: must be a string"}),
            ('[node]\nname = "n1test-10"\ncontact = "sysop@example.org"\n', {"node.name: invalid value"}),
            ('[node]\nname = "N1TEST-100"\ncontact = "sysop@example.org"\n', {"node.name: invalid value"}),
            ('[node]\nname = "N1TESTX-1"\ncontact = "sysop@example.org"\n', {"node.name: invalid value"}),
            ('[node]\nname = "N1TEST-10"\ncontact = "nobody"\n', {"node.contact: invalid value"}),
            ('[node]\nname = "N1TEST-10"\ncontact = "ftp://example.org"\n', {"node.contact: invalid value"}),
            ('[node]\nname = "N1TEST-10"\ncontact = "https://example.org/ x"\n', {"node.contact: invalid value"}),
            ('[node]\nname = "N1TEST-10"\ncontact = "a@' + "b" * 123 + '.org"\n', {"node.contact: invalid value"}),  # 129 characters
            ('[node]\nname = "N1TEST-10"\ncontact = 3\n', {"node.contact: must be a string"}),
            ('[node]\nname = "N1TEST-10"\ncontact = "sysop@example.org"\nreply_from = "NOREPLY"\n',
             {"node.reply_from: invalid value"}),
            ('[node]\nname = "N1TEST-10"\ncontact = "sysop@example.org"\nreply_from = 1\n',
             {"node.reply_from: must be a string"}),
        ]
        for toml, expected in cases:
            with self.subTest(toml=toml):
                self.assertEqual(self.problems(toml), expected)

    def test_contact_rule_checked_when_url_items_off(self):
        self.assertProblem('[node]\nname = "N1TEST-10"\ncontact = "nobody"\n[robot]\nurl_items = false\n',
                           "node.contact: invalid value")

    def test_robot_table(self):
        cases = [
            ("robot = 1\n", "robot: must be a table"),
            ("[robot]\nmenu_expiry_days = 0\n", "robot.menu_expiry_days: out of range"),
            ("[robot]\nmenu_expiry_days = 91\n", "robot.menu_expiry_days: out of range"),
            ("[robot]\nmenu_expiry_days = true\n", "robot.menu_expiry_days: must be an integer"),
            ('[robot]\nmenu_expiry_days = "7"\n', "robot.menu_expiry_days: must be an integer"),
            ("[robot]\nmenu_expiry_days = 7.0\n", "robot.menu_expiry_days: must be an integer"),
            ("[robot]\nmax_wait_minutes = 0\n", "robot.max_wait_minutes: out of range"),
            ("[robot]\nmax_wait_minutes = 10081\n", "robot.max_wait_minutes: out of range"),
            ("[robot]\nsame_session_wait_seconds = -1\n", "robot.same_session_wait_seconds: out of range"),
            ("[robot]\nsame_session_wait_seconds = 11\n", "robot.same_session_wait_seconds: out of range"),
            ('[robot]\nurl_items = "yes"\n', "robot.url_items: must be a boolean"),
            ("[robot]\nurl_items = 1\n", "robot.url_items: must be a boolean"),
            ('[robot]\ndeny_networks = "192.0.2.0/24"\n', "robot.deny_networks: must be a list of strings"),
            ("[robot]\ndeny_networks = [1]\n", "robot.deny_networks: must be a list of strings"),
            ('[robot]\ndeny_networks = ["192.0.2.0/24", "192.0.2.1/24"]\n', "robot.deny_networks[1]: invalid value"),
            ('[robot]\ndeny_networks = ["example.org"]\n', "robot.deny_networks[0]: invalid value"),
            ('[robot]\nblocked = ["n0bad"]\n', "robot.blocked[0]: invalid value"),
            ('[robot]\nblocked = ["N0BAD", "N0BAD BAD"]\n', "robot.blocked[1]: invalid value"),
            ("[robot]\nblocked = [1]\n", "robot.blocked: must be a list of strings"),
            ('[robot]\nblocked = "N0BAD"\n', "robot.blocked: must be a list of strings"),
        ]
        for extra, expected in cases:
            with self.subTest(extra=extra):
                self.assertEqual(self.problems(top_level(extra)), {expected})

    def test_limits(self):
        cases = [
            ("limits = 1\n", "limits: must be a table"),
            ("[limits]\nmessages_per_day = true\n", "limits.messages_per_day: must be an integer"),
            ("[limits]\nmessages_per_day = 1.5\n", "limits.messages_per_day: must be an integer"),
            ('[limits]\nmessages_per_day = "100"\n', "limits.messages_per_day: must be an integer"),
            ("[limits]\nmessages_per_day = 0\n", "limits.messages_per_day: out of range"),
            ("[limits]\nmessages_per_day = -1\n", "limits.messages_per_day: out of range"),
            ("[limits]\nmessages_per_day = 1_000_000_001\n", "limits.messages_per_day: out of range"),
            ("[limits]\nmessages_per_day = 9223372036854775808\n", "limits.messages_per_day: out of range"),
            ("[limits]\nmessages_per_day = 99999999999999999999999999999999\n",
             "limits.messages_per_day: out of range"),
            ("[robot]\nmenu_expiry_days = -99999999999999999999999\n", "robot.menu_expiry_days: out of range"),
            ("[limits]\nitems_per_request = 33\n", "limits.items_per_request: out of range"),
            ("[limits]\nparts_per_item = 51\n", "limits.parts_per_item: out of range"),
            ("[limits]\nrequests_per_hour = 0\n", "limits.requests_per_hour: out of range"),
            ("[limits]\nbytes_per_day = 0\n", "limits.bytes_per_day: out of range"),
            ("[limits]\ninvalid_per_day = 0\n", "limits.invalid_per_day: out of range"),
            ("[limits]\nundelivered_max = 0\n", "limits.undelivered_max: out of range"),
            ("[limits]\nnode_fetches_per_hour = 0\n", "limits.node_fetches_per_hour: out of range"),
            ("[limits]\nnode_bytes_per_day = 0\n", "limits.node_bytes_per_day: out of range"),
            ("[limits]\naccounts = 3\n", "limits.accounts: must be a table"),
            ("[limits.accounts]\nN0EOC = 5\n", "limits.accounts.N0EOC: must be a table"),
            ("[limits.accounts.n0eoc]\nmessages_per_day = 5\n", "limits.accounts.n0eoc: invalid value"),
            ('[limits.accounts."N0EOC-100"]\nmessages_per_day = 5\n', "limits.accounts.N0EOC-100: invalid value"),
            ("[limits.accounts.N0EOC]\nbytes_per_day = 0\n", "limits.accounts.N0EOC.bytes_per_day: out of range"),
            ("[limits.accounts.N0EOC]\nitems_per_request = 33\n",
             "limits.accounts.N0EOC.items_per_request: out of range"),
            ("[limits.accounts.N0EOC]\nmessages_per_day = false\n",
             "limits.accounts.N0EOC.messages_per_day: must be an integer"),
        ]
        for extra, expected in cases:
            with self.subTest(extra=extra):
                self.assertEqual(self.problems(top_level(extra)), {expected})

    def test_catalog_file(self):
        (self.root / "outside.tsv").write_bytes(b"")
        cases = [
            ("catalog = 1\n", "catalog: must be a table"),
            ("[catalog]\n", "catalog.file: required"),
            ("[catalog]\nfile = 3\n", "catalog.file: must be a string"),
            ('[catalog]\nfile = "nothere.tsv"\n', "catalog.file: not found"),
            ('[catalog]\nfile = "files"\n', "catalog.file: not found"),
            ('[catalog]\nfile = "../outside.tsv"\n', "catalog.file: outside config directory"),
            (f'[catalog]\nfile = "{(self.root / "outside.tsv").as_posix()}"\n', "catalog.file: outside config directory"),
        ]
        for extra, expected in cases:
            with self.subTest(extra=extra):
                self.assertEqual(self.problems(top_level(extra)), {expected})

    def test_catalog_file_too_large(self):
        row = "ID1\tCAT\ttext:files/ok.txt\tEntry\n"
        data = row + "#" * 2_000_000 + "\n"
        self.assertProblem(NODE_TOML + CATALOG_TOML, "catalog.file: too large", data)

    def test_too_many_entries(self):
        rows = "".join(f"ID{n}\tCAT\ttext:files/ok.txt\tEntry\n" for n in range(10001))
        self.assertProblem(NODE_TOML + CATALOG_TOML, "catalog: too many entries", rows)

    def test_catalog_rows(self):
        files = self.dir / "files"
        (files / "big.txt").write_bytes(b"x" * 12001)
        (files / "latin.txt").write_bytes(b"caf\xe9\n")
        (files / "tab.txt").write_bytes(b"a\tb\n")
        (files / "bell.txt").write_bytes(b"ding\x07\n")
        (files / "empty.txt").write_bytes(b"")
        (self.root / "secret.txt").write_bytes(b"outside\n")
        cases = [
            (b"CAF\xc3\x89\tCAT\ttext:files/ok.txt\tEntry\n", "not ASCII"),
            (b"OK1\tCAT\ttext:files/ok.txt\tEntry\r\r\n", "invalid characters"),  # only one CR is removed
            (b"OK1\tCAT\ttext:files/ok.txt\tEnt\x07ry\n", "invalid characters"),
            (b"OK1\tCAT\ttext:files/ok.txt\n", "wrong number of fields"),
            (b"OK1\tCAT\ttext:files/ok.txt\tEntry\textra\n", "wrong number of fields"),
            (b"OK1  CAT  text:files/ok.txt  Entry\n", "wrong number of fields"),
            (b"ok1\tCAT\ttext:files/ok.txt\tEntry\n", "invalid ID"),
            (b"_OK\tCAT\ttext:files/ok.txt\tEntry\n", "invalid ID"),
            (b" OK1\tCAT\ttext:files/ok.txt\tEntry\n", "invalid ID"),
            (b"A" * 65 + b"\tCAT\ttext:files/ok.txt\tEntry\n", "invalid ID"),
            (b"LIST\tCAT\ttext:files/ok.txt\tEntry\n", "reserved ID"),
            (b"UPDATE_CAT\tCAT\ttext:files/ok.txt\tEntry\n", "reserved ID"),
            (b"HELP\tCAT\ttext:files/ok.txt\tEntry\n", "reserved ID"),
            (b"MORE\tCAT\ttext:files/ok.txt\tEntry\n", "reserved ID"),
            (b"OK1\tcat\ttext:files/ok.txt\tEntry\n", "invalid category"),
            (b"OK1\t\ttext:files/ok.txt\tEntry\n", "invalid category"),
            (b"OK1\t" + b"C" * 33 + b"\ttext:files/ok.txt\tEntry\n", "invalid category"),
            (b"OK1\tCAT\ttext:files/ok.txt\t\n", "invalid description"),
            (b"OK1\tCAT\ttext:files/ok.txt\t" + b"d" * 81 + b"\n", "invalid description"),
            (b"OK1\tCAT\turl:https://example.org/x.txt\tEntry\n", "unsupported target"),
            (b"OK1\tCAT\tcmd:HELP\tEntry\n", "unsupported target"),
            (b"OK1\tCAT\tfile:files/ok.txt\tEntry\n", "invalid target"),
            (b"OK1\tCAT\ttext:\tEntry\n", "invalid target"),
            (b"OK1\tCAT\tTEXT:files/ok.txt\tEntry\n", "invalid target"),
            (b"OK1\tCAT\ttext:../secret.txt\tEntry\n", "text file outside config directory"),
            (b"OK1\tCAT\ttext:" + (self.root / "secret.txt").as_posix().encode() + b"\tEntry\n",
             "text file outside config directory"),
            (b"OK1\tCAT\ttext:files/nothere.txt\tEntry\n", "text file missing"),
            (b"OK1\tCAT\ttext:files\tEntry\n", "text file missing"),
            (b"OK1\tCAT\ttext:files/big.txt\tEntry\n", "text file too large"),
            (b"OK1\tCAT\ttext:files/latin.txt\tEntry\n", "text file not ASCII"),
            (b"OK1\tCAT\ttext:files/tab.txt\tEntry\n", "text file has control characters"),
            (b"OK1\tCAT\ttext:files/bell.txt\tEntry\n", "text file has control characters"),
            (b"OK1\tCAT\ttext:files/empty.txt\tEntry\n", "text file empty"),
        ]
        for row, reason in cases:
            with self.subTest(row=row[:60]):
                catalog = b"# header\n" + row
                self.assertEqual(self.problems(NODE_TOML + CATALOG_TOML, catalog), {f"catalog:2: {reason}"})

    def test_byte_order_mark(self):
        self.assertProblem(NODE_TOML + CATALOG_TOML, "catalog:1: not ASCII",
                           b"\xef\xbb\xbfOK1\tCAT\ttext:files/ok.txt\tEntry\n")

    def test_one_problem_per_row_first_wins(self):
        # Bad ID and bad category and unsupported target: only "invalid ID".
        self.assertProblem(NODE_TOML + CATALOG_TOML, "catalog:1: invalid ID",
                           "bad id\tcat\turl:x\t\n")

    def test_duplicate_id_names_later_row(self):
        catalog = "OK1\tCAT\ttext:files/ok.txt\tOne\nOK2\tCAT\ttext:files/ok.txt\tTwo\nOK1\tCAT\ttext:files/ok.txt\tAgain\n"
        self.assertProblem(NODE_TOML + CATALOG_TOML, "catalog:3: duplicate ID", catalog)

    @unittest.skipIf(os.name == "nt", "symbolic links need privileges on Windows")
    def test_symlink_escape(self):
        (self.root / "secret.txt").write_bytes(b"outside\n")
        os.symlink(self.root / "secret.txt", self.dir / "files" / "link.txt")
        self.assertProblem(NODE_TOML + CATALOG_TOML, "catalog:1: text file outside config directory",
                           "OK1\tCAT\ttext:files/link.txt\tEntry\n")
        os.symlink(self.root, self.dir / "up")
        (self.root / "other.tsv").write_bytes(b"")
        self.assertProblem(NODE_TOML + '[catalog]\nfile = "up/other.tsv"\n', "catalog.file: outside config directory")

    def test_services(self):
        def service(verb='"AB"', handler='"x"', description='"Test"', extra=""):
            parts = ["[[service]]"]
            if verb is not None:
                parts.append(f"verb = {verb}")
            if handler is not None:
                parts.append(f"handler = {handler}")
            if description is not None:
                parts.append(f"description = {description}")
            return "\n".join(parts) + "\n" + extra
        unknown = "service[0].handler: unknown handler"
        cases = [
            ('service = "x"\n', {"service: must be a list of tables"}),
            ("service = [1]\n", {"service: must be a list of tables"}),
            (service(), {unknown}),
            (service(verb=None), {"service[0].verb: required", unknown}),
            (service(handler=None), {"service[0].handler: required"}),
            (service(description=None), {"service[0].description: required", unknown}),
            (service(verb="3"), {"service[0].verb: must be a string", unknown}),
            (service(handler="3"), {"service[0].handler: must be a string"}),
            (service(description="3"), {"service[0].description: must be a string", unknown}),
            (service(verb='"wx"'), {"service[0].verb: invalid value", unknown}),
            (service(verb='"W"'), {"service[0].verb: invalid value", unknown}),
            (service(verb='"ABCDEFGHIJK"'), {"service[0].verb: invalid value", unknown}),
            (service(verb='"W2"'), {"service[0].verb: invalid value", unknown}),
            (service(verb='"UPDATE_CAT"'), {"service[0].verb: invalid value", unknown}),
            (service(verb='"HELP"'), {"service[0].verb: reserved", unknown}),
            (service(verb='"LIST"'), {"service[0].verb: reserved", unknown}),
            (service(verb='"MORE"'), {"service[0].verb: reserved", unknown}),
            (service(description='""'), {"service[0].description: invalid value", unknown}),
            (service(description='"' + "d" * 61 + '"'), {"service[0].description: invalid value", unknown}),
            (service(description='"tab\\there"'), {"service[0].description: invalid value", unknown}),
            (service(extra="options = 3\n"), {"service[0].options: must be a table", unknown}),
            (service(extra="colour = 3\n"), {"service[0].colour: unknown key", unknown}),
            (service() + service(), {unknown, "service[1].verb: duplicate", "service[1].handler: unknown handler"}),
        ]
        for extra, expected in cases:
            with self.subTest(extra=extra):
                self.assertEqual(self.problems(top_level(extra)), expected)

    def test_catalog_id_equal_to_configured_verb(self):
        problems = self.problems(NODE_TOML + CATALOG_TOML + '[[service]]\nverb = "WX"\nhandler = "x"\ndescription = "d"\n',
                                 "WX\tCAT\ttext:files/ok.txt\tEntry\n")
        self.assertEqual(problems, {"service[0].handler: unknown handler", "catalog:1: reserved ID"})

    def test_every_problem_is_reported(self):
        problems = self.problems(
            '[node]\nname = "bad"\n[robot]\nmenu_expiry_days = 0\nfoo = 1\n[limits]\nbytes_per_day = 0\n'
            + CATALOG_TOML,
            "OK1\tCAT\ttext:files/ok.txt\tEntry\nbad\tCAT\ttext:files/ok.txt\tEntry\nOK2\tCAT\turl:x\tEntry\n")
        self.assertEqual(problems, {
            "node.name: invalid value", "node.contact: required", "robot.menu_expiry_days: out of range",
            "robot.foo: unknown key", "limits.bytes_per_day: out of range",
            "catalog:2: invalid ID", "catalog:3: unsupported target",
        })


class CommandLineTests(ConfigCase):
    def call(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_check_ok(self):
        self.assertEqual(self.call(["check", "--config", str(BASIC)]), (0, "ok\n", ""))

    def test_check_refuses(self):
        path = self.write('[node]\nname = "bad"\n[robot]\nurl_items = false\nfoo = 1\n')
        code, out, err = self.call(["check", "--config", str(path)])
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(set(err.splitlines()), {"node.name: invalid value", "robot.foo: unknown key"})

    def test_run_refuses_before_opening_database(self):
        path = self.write("[node]\n")
        database = self.root / "never.sqlite"
        code, out, err = self.call(["run", "--config", str(path), "--database", str(database), "--once"])
        self.assertEqual(code, 2)
        self.assertEqual(set(err.splitlines()), {"node.name: required", "node.contact: required"})
        self.assertFalse(database.exists())

    def lab_database(self):
        from federated_cms import CMSCore, SQLiteStore
        database = self.root / "lab.sqlite"
        CMSCore(SQLiteStore(database)).lab_mailbox()
        return database

    def test_run_once_reports_a_failing_pass(self):
        database = self.lab_database()
        with patch("federated_cms.robot.Robot.run_until_idle", side_effect=ValueError("secret-detail")):
            with self.assertLogs("federated_cms.robot", level="ERROR") as logs:
                code, _, _ = self.call(["run", "--config", str(BASIC), "--database", str(database), "--once"])
        self.assertEqual(code, 1)
        messages = [record.getMessage() for record in logs.records]
        self.assertEqual([r.levelname for r in logs.records
                          if r.getMessage().split()[0] == "event=run_error"
                          and "error_class=ValueError" in r.getMessage().split()], ["ERROR"])
        self.assertNotIn("secret-detail", "\n".join(messages))

    def test_run_loop_survives_a_failing_pass(self):
        database = self.lab_database()
        # A third pass would mean the loop ignored KeyboardInterrupt; fail fast instead of spinning.
        with patch("federated_cms.robot.Robot.run_until_idle",
                   side_effect=[ValueError("x"), 0, KeyboardInterrupt]) as passes, \
                patch("federated_cms.robot.time.sleep", side_effect=[None, KeyboardInterrupt]) as sleep:
            with self.assertLogs("federated_cms.robot", level="ERROR"):
                code, _, _ = self.call(["run", "--config", str(BASIC), "--database", str(database)])
        self.assertEqual(code, 0)
        self.assertEqual(passes.call_count, 2)
        self.assertEqual([call.args for call in sleep.call_args_list], [(1,), (1,)])

    def test_run_once_answers(self):
        from federated_cms import CMSCore, SQLiteStore
        database = self.root / "lab.sqlite"
        store = SQLiteStore(database)
        CMSCore(store).lab_mailbox()
        raw = message("CLI1", "HELP\r\n")
        mailbox_call(store, "N0CALL", "store_message", "CLI1", raw, "in")
        code, _, _ = self.call(["run", "--config", str(BASIC), "--database", str(database), "--once"])
        self.assertEqual(code, 0)
        pending = mailbox_call(store, "N0CALL", "pending")
        self.assertEqual(len(pending), 1)
        self.assertIn(b"\r\nSubject: INQUIRY - HELP\r\n", pending[0][1])


if __name__ == "__main__":
    unittest.main()
