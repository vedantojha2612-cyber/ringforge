"""Unit tests for RingForge Chord DHT Protocol."""

import math
import unittest
from ringforge.chord import (
    ChordNetwork,
    ChordNode,
    circular_distance,
    in_closed_open_interval,
    in_half_open_interval,
    in_open_interval,
)


class TestChordIntervals(unittest.TestCase):
    def test_open_interval(self):
        mod = 16
        # Normal case
        self.assertTrue(in_open_interval(5, 2, 8, mod))
        self.assertFalse(in_open_interval(2, 2, 8, mod))
        self.assertFalse(in_open_interval(8, 2, 8, mod))
        self.assertFalse(in_open_interval(10, 2, 8, mod))

        # Wrap around case (14 to 3)
        self.assertTrue(in_open_interval(15, 14, 3, mod))
        self.assertTrue(in_open_interval(1, 14, 3, mod))
        self.assertFalse(in_open_interval(14, 14, 3, mod))
        self.assertFalse(in_open_interval(3, 14, 3, mod))
        self.assertFalse(in_open_interval(8, 14, 3, mod))

    def test_half_open_interval(self):
        mod = 16
        self.assertFalse(in_half_open_interval(2, 2, 8, mod))
        self.assertTrue(in_half_open_interval(5, 2, 8, mod))
        self.assertTrue(in_half_open_interval(8, 2, 8, mod))

        # Wrap around case
        self.assertTrue(in_half_open_interval(3, 14, 3, mod))
        self.assertFalse(in_half_open_interval(14, 14, 3, mod))

    def test_circular_distance(self):
        mod = 100
        self.assertEqual(circular_distance(10, 30, mod), 20)
        self.assertEqual(circular_distance(90, 10, mod), 20)
        self.assertEqual(circular_distance(50, 50, mod), 0)


class TestChordNetwork(unittest.TestCase):
    def setUp(self):
        self.net = ChordNetwork(m_bits=16)

    def test_single_node(self):
        node = self.net.create_node("n0", custom_chord_id=100)
        self.assertEqual(node.successor, node)
        traj = self.net.route_key("test_key")
        self.assertEqual(traj.responsible_node_id, "n0")
        self.assertEqual(traj.total_hops, 0)

    def test_finger_table_construction(self):
        node = ChordNode("test", m_bits=8, custom_chord_id=10)
        self.assertEqual(len(node.finger_table), 8)
        # finger[0].start = (10 + 2^0) % 256 = 11
        self.assertEqual(node.finger_table[0].start, 11)
        # finger[1].start = (10 + 2^1) % 256 = 12
        self.assertEqual(node.finger_table[1].start, 12)
        # finger[7].start = (10 + 2^7) % 256 = 138
        self.assertEqual(node.finger_table[7].start, 138)

    def test_multi_node_lookup_and_stabilization(self):
        # Create a network of 8 nodes with deterministic chord IDs
        # IDs: 0, 8192, 16384, 24576, 32768, 40960, 49152, 57344
        step = (1 << 16) // 8
        for i in range(8):
            self.net.create_node(f"node-{i}", custom_chord_id=i * step)

        self.net.fast_stabilize()

        # Check ring topology: node-0 successor must be node-1
        n0 = self.net.get_node("node-0")
        n1 = self.net.get_node("node-1")
        n7 = self.net.get_node("node-7")

        self.assertEqual(n0.successor, n1)
        self.assertEqual(n0.predecessor, n7)

        # Route keys and verify logarithmic hop bounds O(log N)
        keys = [f"sample_key_{k}" for k in range(50)]
        for k in keys:
            traj = self.net.route_key(k, initiator_id="node-0")
            self.assertIsNotNone(traj.responsible_node_id)
            # For 8 nodes, max hops should be <= log2(8) + 1 = 4
            self.assertLessEqual(traj.total_hops, 4)
            # Hops array matches total_hops
            self.assertEqual(len(traj.hops), traj.total_hops)

    def test_dynamic_node_departure(self):
        step = (1 << 16) // 4
        for i in range(4):
            self.net.create_node(f"node-{i}", custom_chord_id=i * step)
        self.net.fast_stabilize()

        # Remove node-1
        removed = self.net.remove_node("node-1")
        self.assertTrue(removed)
        self.assertEqual(self.net.node_count, 3)

        # Node-0 successor should now point to node-2
        n0 = self.net.get_node("node-0")
        n2 = self.net.get_node("node-2")
        self.assertEqual(n0.successor, n2)

    def test_hop_scaling_benchmark(self):
        for i in range(16):
            self.net.create_node(f"node-{i}")
        self.net.fast_stabilize()

        keys = [f"bench_key_{k}" for k in range(40)]
        bench = self.net.benchmark_hop_scaling(keys)

        self.assertEqual(bench["node_count"], 16)
        self.assertLessEqual(bench["avg_hops"], math.log2(16) + 1.0)


if __name__ == "__main__":
    unittest.main()
