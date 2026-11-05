"""Unit tests for RingForge Consistent Hash Ring."""

import unittest
from ringforge.ring import ConsistentHashRing, VirtualNode, sha256_hash


class TestConsistentHashRing(unittest.TestCase):
    def setUp(self):
        self.ring = ConsistentHashRing(bit_length=32, default_vnodes=32)

    def test_sha256_hash_in_bounds(self):
        h = sha256_hash("test-key", bit_length=32)
        self.assertGreaterEqual(h, 0)
        self.assertLess(h, 1 << 32)

        h16 = sha256_hash("test-key", bit_length=16)
        self.assertGreaterEqual(h16, 0)
        self.assertLess(h16, 1 << 16)

    def test_empty_ring(self):
        self.assertIsNone(self.ring.get_node("any_key"))
        self.assertIsNone(self.ring.get_vnode("any_key"))
        self.assertEqual(self.ring.get_preference_list("any_key", 3), [])

    def test_add_and_remove_nodes(self):
        vnodes = self.ring.add_node("node-1", vnodes=16)
        self.assertEqual(len(vnodes), 16)
        self.assertEqual(self.ring.total_tokens, 16)
        self.assertEqual(self.ring.physical_nodes, ["node-1"])

        # Every key must map to node-1
        self.assertEqual(self.ring.get_node("key_a"), "node-1")
        self.assertEqual(self.ring.get_node("key_b"), "node-1")

        # Add node-2
        self.ring.add_node("node-2", vnodes=16)
        self.assertEqual(self.ring.total_tokens, 32)
        self.assertEqual(self.ring.physical_nodes, ["node-1", "node-2"])

        # Remove node-1
        removed = self.ring.remove_node("node-1")
        self.assertTrue(removed)
        self.assertEqual(self.ring.physical_nodes, ["node-2"])
        self.assertEqual(self.ring.total_tokens, 16)
        self.assertEqual(self.ring.get_node("key_a"), "node-2")

        # Removing non-existent node
        self.assertFalse(self.ring.remove_node("non-existent"))

    def test_duplicate_node_fails(self):
        self.ring.add_node("node-alpha")
        with self.assertRaises(ValueError):
            self.ring.add_node("node-alpha")

    def test_invalid_node_args(self):
        with self.assertRaises(ValueError):
            self.ring.add_node("")
        with self.assertRaises(ValueError):
            self.ring.add_node("valid", vnodes=0)

    def test_preference_list_uniqueness(self):
        # Add 5 nodes
        for i in range(5):
            self.ring.add_node(f"node-{i}", vnodes=20)

        # Preference list with count=3 must have 3 distinct physical nodes
        prefs = self.ring.get_preference_list("user:10492", count=3)
        self.assertEqual(len(prefs), 3)
        self.assertEqual(len(set(prefs)), 3)

        # Preference list asking for more than total physical nodes
        prefs_all = self.ring.get_preference_list("user:10492", count=10)
        self.assertEqual(len(prefs_all), 5)
        self.assertEqual(len(set(prefs_all)), 5)

    def test_token_range(self):
        self.ring.add_node("node-a", vnodes=4)
        tokens = sorted(self.ring._ring_tokens)
        first_token = tokens[0]
        pred, curr = self.ring.get_token_range(first_token)
        self.assertEqual(curr, first_token)
        self.assertEqual(pred, tokens[-1])  # circular wrap

    def test_key_distribution_statistics(self):
        for i in range(4):
            self.ring.add_node(f"node-{i}", vnodes=64)

        keys = [f"test_item_{k}" for k in range(400)]
        stats = self.ring.analyze_distribution(keys)

        self.assertEqual(stats["total_keys"], 400)
        self.assertEqual(stats["physical_node_count"], 4)
        self.assertEqual(stats["mean_keys_per_node"], 100.0)
        # Normalized entropy should be close to 1.0 (uniform)
        self.assertGreater(stats["normalized_entropy"], 0.90)

    def test_topology_export(self):
        self.ring.add_node("node-0", vnodes=8)
        topo = self.ring.export_topology()
        self.assertEqual(topo["physical_node_count"], 1)
        self.assertEqual(topo["total_vnodes"], 8)
        self.assertEqual(len(topo["tokens"]), 8)
        self.assertIn("node-0", topo["nodes"])


if __name__ == "__main__":
    unittest.main()
