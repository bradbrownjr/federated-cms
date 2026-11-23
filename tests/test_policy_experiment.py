"""Offline tests for the isolated, synthetic authorization experiment."""
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from federated_cms.policy_experiment import (
    PolicyDenied, PolicyExperiment, PolicySession, PolicyUnavailable,
)


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class PolicyExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "policy.sqlite"
        self.clock = Clock()
        self.policy = PolicyExperiment(self.path, clock=self.clock)
        self.policy.add_principal("alice")
        self.policy.add_principal("bob")
        self.policy.add_mailbox("mail-a")
        self.policy.add_mailbox("mail-b")
        self.alice = self.policy.open_session("alice", ttl=300)
        self.bob = self.policy.open_session("bob", ttl=300)

    def grant(self, principal="alice", mailbox="mail-a", action="deposit", expires=1100):
        self.policy.set_grant(principal, mailbox, action, expires=expires)

    def test_deny_by_default_and_scoped_actions(self):
        with self.assertRaises(PolicyDenied):
            self.policy.deposit_fixture(self.alice, "mail-a", "one", b"synthetic")
        self.grant()
        self.policy.deposit_fixture(self.alice, "mail-a", "one", b"synthetic")
        for session, mailbox in ((self.alice, "mail-a"), (self.alice, "mail-b"), (self.bob, "mail-a")):
            with self.subTest(session=session, mailbox=mailbox), self.assertRaises(PolicyDenied):
                self.policy.read_fixture(session, mailbox, "one")
        with self.assertRaises(PolicyDenied):
            self.policy.deposit_fixture(self.bob, "mail-a", "two", b"wrong principal")
        self.grant(action="read")
        self.assertEqual(self.policy.read_fixture(self.alice, "mail-a", "one"), b"synthetic")

    def test_revocation_and_principal_mailbox_status_are_rechecked(self):
        self.grant()
        self.policy.revoke_grant("alice", "mail-a", "deposit")
        with self.assertRaises(PolicyDenied):
            self.policy.deposit_fixture(self.alice, "mail-a", "one", b"x")
        self.grant()
        self.policy.disable_mailbox("mail-a")
        with self.assertRaises(PolicyDenied):
            self.policy.deposit_fixture(self.alice, "mail-a", "one", b"x")
        self.grant(principal="bob", mailbox="mail-b", action="read")
        self.assertIsNone(self.policy.read_fixture(self.bob, "mail-b", "one"))
        self.policy.disable_principal("bob")
        with self.assertRaises(PolicyDenied):
            self.policy.read_fixture(self.bob, "mail-b", "one")
        with self.assertRaises(PolicyDenied):
            self.policy.open_session("bob")

    def test_session_and_grant_expire_at_deadline(self):
        self.grant(expires=1100)
        self.clock.now = 1100
        with self.assertRaises(PolicyDenied):
            self.policy.deposit_fixture(self.alice, "mail-a", "one", b"x")
        self.policy.set_grant("bob", "mail-b", "deposit", expires=1400)
        self.clock.now = 1300
        with self.assertRaises(PolicyDenied):
            self.policy.deposit_fixture(self.bob, "mail-b", "one", b"x")

    def test_grant_expiring_during_write_rolls_back_data(self):
        self.grant(expires=1100)
        ticks = iter((1099.0, 1100.0))
        self.policy._clock = lambda: next(ticks)
        with self.assertRaises(PolicyDenied):
            self.policy.deposit_fixture(self.alice, "mail-a", "one", b"must not persist")
        self.policy._clock = self.clock
        with self.assertRaises(PolicyUnavailable):
            self.policy.deposit_fixture(self.alice, "mail-a", "two", b"clock rolled back")
        self.clock.now = 1100
        self.grant(action="read", expires=1200)
        self.assertIsNone(self.policy.read_fixture(self.alice, "mail-a", "one"))

    def test_forged_and_revoked_sessions_fail_closed(self):
        self.grant()
        for session in (None, PolicySession("not-a-valid-token"), PolicySession("\u2603")):
            with self.subTest(session=session), self.assertRaises(PolicyDenied):
                self.policy.deposit_fixture(session, "mail-a", "one", b"x")
        self.policy.revoke_session(self.alice)
        with self.assertRaises(PolicyDenied):
            self.policy.deposit_fixture(self.alice, "mail-a", "one", b"x")

    def test_restart_persists_grants_and_revocations(self):
        self.grant(action="deposit")
        self.grant(action="read")
        self.policy.deposit_fixture(self.alice, "mail-a", "one", b"synthetic")
        reopened = PolicyExperiment(self.path, clock=self.clock)
        self.assertEqual(reopened.read_fixture(self.alice, "mail-a", "one"), b"synthetic")
        reopened.revoke_grant("alice", "mail-a", "read")
        with self.assertRaises(PolicyDenied):
            PolicyExperiment(self.path, clock=self.clock).read_fixture(self.alice, "mail-a", "one")

    def test_clock_rollback_and_unavailable_database_fail_closed(self):
        self.grant()
        self.clock.now = 1010
        self.policy.deposit_fixture(self.alice, "mail-a", "one", b"x")
        self.clock.now = 1009
        with self.assertRaises(PolicyUnavailable):
            self.policy.deposit_fixture(self.alice, "mail-a", "two", b"x")
        self.clock.now = 1010
        self.policy.path = str(self.path.parent)
        with self.assertRaises(PolicyUnavailable):
            self.policy.deposit_fixture(self.alice, "mail-a", "two", b"x")
        self.policy.path = str(self.path)
        self.grant(action="read")
        self.assertIsNone(self.policy.read_fixture(self.alice, "mail-a", "two"))

    def test_mid_transaction_clock_rollback_retains_start_high_water(self):
        self.grant(expires=1200)
        ticks = iter((1099.0, 1098.0))
        self.policy._clock = lambda: next(ticks)
        with self.assertRaises(PolicyUnavailable):
            self.policy.deposit_fixture(self.alice, "mail-a", "one", b"must not persist")
        self.policy._clock = self.clock
        self.clock.now = 1098
        with self.assertRaises(PolicyUnavailable):
            self.policy.deposit_fixture(self.alice, "mail-a", "two", b"clock rolled back")
        self.clock.now = 1099
        self.grant(action="read", expires=1200)
        self.assertIsNone(self.policy.read_fixture(self.alice, "mail-a", "one"))
        self.assertIsNone(self.policy.read_fixture(self.alice, "mail-a", "two"))

    def test_mid_transaction_clock_errors_retain_start_high_water(self):
        self.grant(expires=1200)
        for error in (OSError("clock failed"), ValueError("clock failed"), RuntimeError("clock failed")):
            with self.subTest(error=type(error).__name__):
                samples = iter((1099.0, error))

                def failing_clock():
                    sample = next(samples)
                    if isinstance(sample, Exception):
                        raise sample
                    return sample

                self.policy._clock = failing_clock
                with self.assertRaises(PolicyUnavailable):
                    self.policy.deposit_fixture(self.alice, "mail-a", "one", b"must not persist")
                self.policy._clock = self.clock
                self.clock.now = 1098
                with self.assertRaises(PolicyUnavailable):
                    self.policy.deposit_fixture(self.alice, "mail-a", "two", b"clock rolled back")
                self.clock.now = 1099
        self.grant(action="read", expires=1200)
        self.assertIsNone(self.policy.read_fixture(self.alice, "mail-a", "one"))
        self.assertIsNone(self.policy.read_fixture(self.alice, "mail-a", "two"))

    def test_concurrent_write_and_revoke_serialize_without_sleeps(self):
        self.grant(expires=1200)

        class NoWaitPolicy(PolicyExperiment):
            def _connect(self):
                con = sqlite3.connect(self.path, timeout=0, isolation_level=None)
                con.execute("PRAGMA foreign_keys=ON")
                return con

        other = NoWaitPolicy(self.path, clock=self.clock)
        at_final_check = threading.Event()
        continue_write = threading.Event()
        reads = 0
        errors = []

        def gated_clock():
            nonlocal reads
            reads += 1
            if reads == 2:
                at_final_check.set()
                if not continue_write.wait(5):
                    raise RuntimeError("write was not released")
            return 1000.0

        def deposit():
            try:
                self.policy.deposit_fixture(self.alice, "mail-a", "one", b"synthetic")
            except Exception as exc:
                errors.append(exc)

        self.policy._clock = gated_clock
        worker = threading.Thread(target=deposit)
        worker.start()
        try:
            self.assertTrue(at_final_check.wait(3), "write did not reach final check")
            with self.assertRaises(PolicyUnavailable):
                other.revoke_grant("alice", "mail-a", "deposit")
        finally:
            continue_write.set()
            worker.join(timeout=5)
            self.policy._clock = self.clock
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        other.revoke_grant("alice", "mail-a", "deposit")
        with self.assertRaises(PolicyDenied):
            self.policy.deposit_fixture(self.alice, "mail-a", "two", b"denied")
        self.grant(action="read")
        self.assertEqual(self.policy.read_fixture(self.alice, "mail-a", "one"), b"synthetic")
        self.assertIsNone(self.policy.read_fixture(self.alice, "mail-a", "two"))

    def test_limits_invalid_clock_and_unknown_principals(self):
        for ttl in (0, -1, 3601, float("nan"), float("inf")):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                self.policy.open_session("alice", ttl=ttl)
        with self.assertRaises(PolicyDenied):
            self.policy.open_session("unknown")
        self.clock.now = float("nan")
        with self.assertRaises(PolicyUnavailable):
            self.policy.read_fixture(self.alice, "mail-a", "one")
