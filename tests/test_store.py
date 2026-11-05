"""Unit tests for RingForge Distributed Store and Replication."""

import unittest
from ringforge.ring import ConsistentHashRing
from ringforge.store import DistributedStore


class TestDistributedStore(unittest.TestCase):
    def setUp(self):
        self.ring = ConsistentHashRing(bit_length=32, default_vnodes=32)
        for i in range(5):
            self.ring.add_node(f"node-{i}")
        self.store = DistributedStore(
            self.ring,
            replication_factor=3,
            write_quorum=2,
            read_quorum=1,
        )

    def test_put_and_get_success(self):
        receipt = self.store.put("user:42", {"name": "Alice", "role": "Engineer"})
        self.assertEqual(receipt.key, "user:42")
        self.assertEqual(receipt.success_count, 3)
        self.assertEqual(len(receipt.replica_node_ids), 3)

        result = self.store.get("user:42")
        self.assertTrue(result.found)
        self.assertEqual(result.value["name"], "Alice")
        self.assertEqual(result.version, 1)
        self.assertEqual(len(result.replica_node_ids), 3)

    def test_get_nonexistent_key(self):
        result = self.store.get("missing_key")
        self.assertFalse(result.found)
        self.assertIsNone(result.value)

    def test_overwrite_version_increment(self):
        self.store.put("counter", 1)
        r1 = self.store.get("counter")
        self.assertEqual(r1.value, 1)
        self.assertEqual(r1.version, 1)

        self.store.put("counter", 2)
        r2 = self.store.get("counter")
        self.assertEqual(r2.value, 2)
        self.assertEqual(r2.version, 2)

    def test_delete_key(self):
        self.store.put("to_delete", "secret")
        self.assertTrue(self.store.delete("to_delete"))
        result = self.store.get("to_delete")
        self.assertFalse(result.found)

    def test_rebalance_on_membership_change(self):
        # Insert 20 keys
        for i in range(20):
            self.store.put(f"key_{i}", f"val_{i}")

        stats_before = self.store.get_cluster_stats()
        self.assertEqual(stats_before["total_unique_keys"], 20)
        self.assertEqual(stats_before["total_stored_replicas"], 60)  # 20 * 3

        # Add a 6th node to ring
        self.ring.add_node("node-5")
        rebalance = self.store.rebalance_cluster()

        stats_after = self.store.get_cluster_stats()
        self.assertEqual(stats_after["total_unique_keys"], 20)
        # All 20 keys should still be retrievable
        for i in range(20):
            res = self.store.get(f"key_{i}")
            self.assertTrue(res.found)
            self.assertEqual(res.value, f"val_{i}")

    def test_anti_entropy_repair(self):
        self.store.put("audit_key", "important_data")
        # Intentionally delete from one replica to simulate loss
        prefs = self.ring.get_preference_list("audit_key", 3)
        node_to_corrupt = prefs[1]
        del self.store._node_storage[node_to_corrupt]["audit_key"]

        # Repair
        repair_res = self.store.anti_entropy_repair()
        self.assertGreaterEqual(repair_res["repaired_replicas"], 1)

        # Check repaired
        self.assertIn("audit_key", self.store._node_storage[node_to_corrupt])


if __name__ == "__main__":
    unittest.main()
