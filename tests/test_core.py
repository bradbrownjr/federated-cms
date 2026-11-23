from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from federated_cms import CMSCore, SQLiteStore
from federated_cms.core import utc_now, wire_time
from federated_cms.store import StoreUnavailable


def make_core(tmp_path):
    return CMSCore(SQLiteStore(tmp_path / "fcms.sqlite3"))


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_persistence_and_message_schema(self):
        core = make_core(self.path)
        message_id = core.store_local_message("operator-a", "operator-b", "test", "hello")
        receipt = core.submit_weather("FN43", "scope-a")
        core.process_pending()
        restarted = make_core(self.path)
        item = restarted.request(receipt.request_id, "scope-a")
        self.assertEqual(item.status, "completed")
        self.assertTrue(item.result["content"].startswith("TEST DATA"))
        self.assertEqual(restarted.local_mailbox("operator-a")[0]["id"], message_id)

    def test_duplicate_request_is_idempotent(self):
        core = make_core(self.path)
        one = core.submit_weather("FN43", "scope-a", idempotency_key="test-key")
        two = core.submit_weather("FN43", "scope-a", idempotency_key="test-key")
        self.assertTrue(two.duplicate)
        self.assertEqual(two.request_id, one.request_id)
        self.assertEqual(core.process_pending(), 1)

    def test_duplicate_key_is_scoped_to_owner(self):
        core = make_core(self.path)
        one = core.submit_weather("FN43", "scope-a", idempotency_key="same-key")
        two = core.submit_weather("FN43", "scope-b", idempotency_key="same-key")
        self.assertNotEqual(one.request_id, two.request_id)
        self.assertFalse(two.duplicate)

    def test_expired_processing_lease_is_recovered(self):
        core = make_core(self.path)
        receipt = core.submit_weather("FN43", "scope-a")
        now = wire_time(utc_now())
        claimed = core.store.claim_next(now, now)
        self.assertEqual(claimed["id"], receipt.request_id)
        recovered = core.store.claim_next(now, wire_time(utc_now() + timedelta(seconds=30)))
        self.assertEqual(recovered["id"], receipt.request_id)

    def test_stale_worker_cannot_overwrite_new_lease_result(self):
        core = make_core(self.path)
        receipt = core.submit_weather("FN43", "scope-a")
        now = wire_time(utc_now())
        old = core.store.claim_next(now, now)
        new = core.store.claim_next(now, wire_time(utc_now() + timedelta(seconds=30)))
        self.assertNotEqual(old["processing_lease_token"], new["processing_lease_token"])
        self.assertTrue(core.store.finish(receipt.request_id, new["processing_lease_token"], "completed", {"worker": "new"}, now))
        self.assertFalse(core.store.finish(receipt.request_id, old["processing_lease_token"], "completed", {"worker": "old"}, now))
        self.assertEqual(core.request(receipt.request_id, "scope-a").result, {"worker": "new"})

    def test_expiration_prevents_execution(self):
        core = make_core(self.path)
        receipt = core.submit_weather("FN43", "scope-a", ttl=timedelta(seconds=-1))
        self.assertEqual(core.process_pending(), 0)
        self.assertEqual(core.request(receipt.request_id, "scope-a").status, "expired")

    def test_scope_isolation(self):
        core = make_core(self.path)
        receipt = core.submit_weather("FN43", "scope-a")
        core.process_pending()
        self.assertIsNone(core.request(receipt.request_id, "scope-b"))
        self.assertEqual(core.jobs("scope-b"), [])

    def test_unavailable_database_is_reported(self):
        with self.assertRaises(StoreUnavailable):
            CMSCore(SQLiteStore(self.path))
