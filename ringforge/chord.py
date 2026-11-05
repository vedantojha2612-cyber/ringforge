"""RingForge Chord Distributed Hash Table (DHT) Protocol.

Implements the complete Stoica et al. (2001) Chord specification in pure Python stdlib:
- Modular identifier ring arithmetic modulo 2^m
- Dynamic finger tables with O(log N) exponential skip routing
- Successor & predecessor stabilization protocol
- Full routing trajectory capture (laser beam coordinates, hop distances, latency, finger indices)
- In-memory simulated network coordinator with dynamic node joins, leaves, and failure injection
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ringforge.ring import sha256_hash


def in_open_interval(val: int, start: int, end: int, mod: int) -> bool:
    """Checks if val in (start, end) on a circular ring of size mod.

    Returns True if val is strictly between start and end clockwise.
    """
    val %= mod
    start %= mod
    end %= mod
    if start < end:
        return start < val < end
    elif start > end:
        return val > start or val < end
    else:  # start == end
        return val != start


def in_half_open_interval(val: int, start: int, end: int, mod: int) -> bool:
    """Checks if val in (start, end] on a circular ring of size mod.

    Exclusive at start, inclusive at end.
    """
    val %= mod
    start %= mod
    end %= mod
    if start < end:
        return start < val <= end
    elif start > end:
        return val > start or val <= end
    else:  # start == end
        return True  # Covers the whole circle


def in_closed_open_interval(val: int, start: int, end: int, mod: int) -> bool:
    """Checks if val in [start, end) on a circular ring of size mod.

    Inclusive at start, exclusive at end.
    """
    val %= mod
    start %= mod
    end %= mod
    if start < end:
        return start <= val < end
    elif start > end:
        return val >= start or val < end
    else:  # start == end
        return True


def circular_distance(from_val: int, to_val: int, mod: int) -> int:
    """Computes clockwise distance from from_val to to_val modulo mod."""
    from_val %= mod
    to_val %= mod
    if to_val >= from_val:
        return to_val - from_val
    return (mod - from_val) + to_val


@dataclass
class FingerEntry:
    """Represents a single entry in a Chord node's finger table."""
    index: int
    start: int
    interval_end: int
    node: Optional[ChordNode] = None

    def to_dict(self, mod: int) -> Dict[str, Any]:
        """Serializes finger entry for API telemetry."""
        return {
            "index": self.index,
            "start": self.start,
            "interval_end": self.interval_end,
            "node_id": self.node.node_id if self.node else None,
            "node_chord_id": self.node.chord_id if self.node else None,
            "start_deg": math.degrees((self.start / mod) * 2 * math.pi),
        }


@dataclass
class RoutingHop:
    """Telemetry record for a single hop in a Chord routing lookup."""
    hop_number: int
    from_node_id: str
    from_chord_id: int
    to_node_id: str
    to_chord_id: int
    finger_index: int
    hop_distance: int
    hop_distance_ratio: float
    angle_from_deg: float
    angle_to_deg: float
    elapsed_us: float


@dataclass
class RoutingTrajectory:
    """Complete trajectory and telemetry for a routed Chord query."""
    key: str
    key_hash: int
    initiator_node_id: str
    responsible_node_id: str
    total_hops: int
    hops: List[RoutingHop] = field(default_factory=list)
    total_latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serializes trajectory for WebGL laser beams and TUI sparklines."""
        return {
            "key": self.key,
            "key_hash": self.key_hash,
            "initiator_node_id": self.initiator_node_id,
            "responsible_node_id": self.responsible_node_id,
            "total_hops": self.total_hops,
            "total_latency_ms": round(self.total_latency_ms, 3),
            "hops": [
                {
                    "hop": h.hop_number,
                    "from_node_id": h.from_node_id,
                    "from_chord_id": h.from_chord_id,
                    "to_node_id": h.to_node_id,
                    "to_chord_id": h.to_chord_id,
                    "finger_index": h.finger_index,
                    "hop_distance": h.hop_distance,
                    "hop_distance_ratio": round(h.hop_distance_ratio, 4),
                    "angle_from_deg": round(h.angle_from_deg, 2),
                    "angle_to_deg": round(h.angle_to_deg, 2),
                    "elapsed_us": round(h.elapsed_us, 1),
                }
                for h in self.hops
            ],
        }


class ChordNode:
    """A single node participating in the Chord DHT protocol."""

    def __init__(self, node_id: str, m_bits: int = 32, custom_chord_id: Optional[int] = None):
        """Initializes a Chord node.

        Args:
            node_id: Logical human-readable identifier (e.g. 'node-alpha').
            m_bits: Number of bits in the identifier space (default: 32).
            custom_chord_id: Explicit chord identifier; if None, hashed from node_id.
        """
        self.node_id = node_id
        self.m_bits = m_bits
        self.ring_size = 1 << m_bits

        if custom_chord_id is not None:
            self.chord_id = custom_chord_id % self.ring_size
        else:
            self.chord_id = sha256_hash(node_id, m_bits)

        # Chord pointers
        self.successor: ChordNode = self
        self.predecessor: Optional[ChordNode] = None

        # Successor list for redundancy (r = min(4, 2^m))
        self.successor_list: List[ChordNode] = [self]

        # Finger table with m entries: finger[i].start = (n + 2^i) mod 2^m
        self.finger_table: List[FingerEntry] = []
        for i in range(self.m_bits):
            start = (self.chord_id + (1 << i)) % self.ring_size
            interval_end = (self.chord_id + (1 << (i + 1))) % self.ring_size
            self.finger_table.append(FingerEntry(index=i, start=start, interval_end=interval_end, node=self))

        # Local key-value store storage
        self.storage: Dict[str, Any] = {}
        self.is_alive = True

    @property
    def angle_radians(self) -> float:
        """Angular coordinate in radians on the 2D/3D circle."""
        return (self.chord_id / self.ring_size) * 2 * math.pi

    @property
    def angle_degrees(self) -> float:
        """Angular coordinate in degrees [0, 360)."""
        return math.degrees(self.angle_radians)

    def find_successor(self, target_id: int, trajectory: Optional[RoutingTrajectory] = None, hop_count: int = 0) -> ChordNode:
        """Finds the node responsible for target_id using Chord exponential skip routing.

        Complexity: O(log N) hops.

        Args:
            target_id: Target key or node identifier in [0, 2^m - 1].
            trajectory: Optional RoutingTrajectory collector to record hop telemetry.
            hop_count: Current recursion depth.

        Returns:
            Responsible ChordNode instance.
        """
        target_id %= self.ring_size
        t0 = time.perf_counter()

        # If target falls between this node and its immediate successor:
        # target in (self.chord_id, self.successor.chord_id]
        if self.successor == self or in_half_open_interval(target_id, self.chord_id, self.successor.chord_id, self.ring_size):
            if trajectory is not None and self != self.successor:
                dist = circular_distance(self.chord_id, self.successor.chord_id, self.ring_size)
                hop = RoutingHop(
                    hop_number=hop_count,
                    from_node_id=self.node_id,
                    from_chord_id=self.chord_id,
                    to_node_id=self.successor.node_id,
                    to_chord_id=self.successor.chord_id,
                    finger_index=0,
                    hop_distance=dist,
                    hop_distance_ratio=dist / self.ring_size,
                    angle_from_deg=self.angle_degrees,
                    angle_to_deg=self.successor.angle_degrees,
                    elapsed_us=(time.perf_counter() - t0) * 1e6,
                )
                trajectory.hops.append(hop)
            return self.successor

        # Otherwise, forward query to closest preceding node in finger table
        n_prime, finger_idx = self.closest_preceding_node(target_id)
        if n_prime == self:
            # Fallback to successor if no closer preceding finger exists
            dest = self.successor
            finger_used = 0
        else:
            dest = n_prime
            finger_used = finger_idx

        dist = circular_distance(self.chord_id, dest.chord_id, self.ring_size)
        if trajectory is not None:
            hop = RoutingHop(
                hop_number=hop_count,
                from_node_id=self.node_id,
                from_chord_id=self.chord_id,
                to_node_id=dest.node_id,
                to_chord_id=dest.chord_id,
                finger_index=finger_used,
                hop_distance=dist,
                hop_distance_ratio=dist / self.ring_size,
                angle_from_deg=self.angle_degrees,
                angle_to_deg=dest.angle_degrees,
                elapsed_us=(time.perf_counter() - t0) * 1e6,
            )
            trajectory.hops.append(hop)

        # Recursively route query from dest (with self-healing loop guard)
        if dest == self or hop_count >= max(2 * self.m_bits, 64):
            return self.successor
        return dest.find_successor(target_id, trajectory, hop_count + 1)

    def closest_preceding_node(self, target_id: int) -> Tuple[ChordNode, int]:
        """Scans finger table backwards to find the highest finger preceding target_id.

        Finger node must be strictly in (self.chord_id, target_id).
        """
        for i in range(self.m_bits - 1, -1, -1):
            finger_node = self.finger_table[i].node
            if finger_node and finger_node.is_alive and finger_node != self:
                if in_open_interval(finger_node.chord_id, self.chord_id, target_id, self.ring_size):
                    return (finger_node, i)
        return (self, -1)

    def join(self, bootstrap_node: Optional[ChordNode]) -> None:
        """Joins an existing Chord network via a known bootstrap peer."""
        if bootstrap_node is not None and bootstrap_node != self:
            self.predecessor = None
            # Find our successor in the network
            self.successor = bootstrap_node.find_successor(self.chord_id)
            self.finger_table[0].node = self.successor
            self.successor_list = [self.successor]
        else:
            # First node creates a new ring
            self.predecessor = None
            self.successor = self
            for entry in self.finger_table:
                entry.node = self
            self.successor_list = [self]

    def stabilize(self) -> None:
        """Periodic stabilization: verifies successor and notifies it."""
        if not self.is_alive or not self.successor.is_alive:
            self._handle_dead_successor()

        # Ask successor for its predecessor
        x = self.successor.predecessor
        if x is not None and x.is_alive and in_open_interval(x.chord_id, self.chord_id, self.successor.chord_id, self.ring_size):
            self.successor = x
            self.finger_table[0].node = x

        self.successor.notify(self)
        self._update_successor_list()

    def notify(self, candidate_predecessor: ChordNode) -> None:
        """Notified by candidate_predecessor that it thinks it is our predecessor."""
        if not candidate_predecessor.is_alive:
            return
        if self.predecessor is None or not self.predecessor.is_alive or in_open_interval(candidate_predecessor.chord_id, self.predecessor.chord_id, self.chord_id, self.ring_size):
            self.predecessor = candidate_predecessor

    def fix_fingers(self, index: Optional[int] = None) -> None:
        """Refreshes finger table entries using find_successor."""
        indices = [index] if index is not None else list(range(self.m_bits))
        for i in indices:
            start = self.finger_table[i].start
            found = self.find_successor(start)
            if found and found.is_alive:
                self.finger_table[i].node = found

    def _update_successor_list(self) -> None:
        """Maintains the redundant list of immediate successors."""
        curr = self.successor
        new_list = [curr]
        for _ in range(3):
            if curr and curr.successor and curr.successor != curr and curr.successor not in new_list:
                new_list.append(curr.successor)
                curr = curr.successor
            else:
                break
        self.successor_list = new_list

    def _handle_dead_successor(self) -> None:
        """Failover to next live node in successor list if successor died."""
        for candidate in self.successor_list:
            if candidate and candidate.is_alive and candidate != self:
                self.successor = candidate
                self.finger_table[0].node = candidate
                return
        self.successor = self
        self.finger_table[0].node = self

    def export_node_info(self) -> Dict[str, Any]:
        """Serializes node state, coordinates, finger table, and storage stats."""
        rad = self.angle_radians
        return {
            "node_id": self.node_id,
            "chord_id": self.chord_id,
            "angle_rad": rad,
            "angle_deg": self.angle_degrees,
            "coord_x": math.cos(rad),
            "coord_y": math.sin(rad),
            "is_alive": self.is_alive,
            "successor_id": self.successor.node_id if self.successor else None,
            "successor_chord_id": self.successor.chord_id if self.successor else None,
            "predecessor_id": self.predecessor.node_id if self.predecessor else None,
            "predecessor_chord_id": self.predecessor.chord_id if self.predecessor else None,
            "keys_stored": len(self.storage),
            "finger_table": [f.to_dict(self.ring_size) for f in self.finger_table],
        }


class ChordNetwork:
    """Simulator and coordinator for a multi-node Chord Distributed Hash Table."""

    def __init__(self, m_bits: int = 32):
        """Initializes a Chord network simulator.

        Args:
            m_bits: Number of bits in identifier circle (e.g. 16, 32).
        """
        self.m_bits = m_bits
        self.ring_size = 1 << m_bits
        self.nodes: Dict[str, ChordNode] = {}
        self._ordered_nodes: List[ChordNode] = []

    @property
    def node_count(self) -> int:
        return len([n for n in self.nodes.values() if n.is_alive])

    def create_node(self, node_id: str, custom_chord_id: Optional[int] = None) -> ChordNode:
        """Instantiates and joins a new Chord node into the network."""
        if node_id in self.nodes:
            raise ValueError(f"Node '{node_id}' already exists in the Chord network.")

        node = ChordNode(node_id, m_bits=self.m_bits, custom_chord_id=custom_chord_id)
        bootstrap = self.get_random_live_node() if self.nodes else None

        node.join(bootstrap)
        self.nodes[node_id] = node
        self._sync_network_state()
        return node

    def remove_node(self, node_id: str) -> bool:
        """Simulates graceful or ungraceful node departure."""
        node = self.nodes.get(node_id)
        if not node:
            return False

        node.is_alive = False
        del self.nodes[node_id]
        self._sync_network_state()
        self.stabilize_network(rounds=2)
        return True

    def get_node(self, node_id: str) -> Optional[ChordNode]:
        """Retrieves a ChordNode by its string ID."""
        return self.nodes.get(node_id)

    def get_random_live_node(self) -> Optional[ChordNode]:
        """Picks an arbitrary active node to serve as a query initiator."""
        live = [n for n in self.nodes.values() if n.is_alive]
        return live[0] if live else None

    def _sync_network_state(self) -> None:
        """Updates internal ordered cache sorted by chord_id."""
        self._ordered_nodes = sorted(
            [n for n in self.nodes.values() if n.is_alive],
            key=lambda n: n.chord_id,
        )

    def fast_stabilize(self) -> None:
        """Fast ground-truth stabilization across all nodes for instant test/demo setups."""
        self._sync_network_state()
        nodes = self._ordered_nodes
        n = len(nodes)
        if n == 0:
            return

        for idx, node in enumerate(nodes):
            succ = nodes[(idx + 1) % n]
            pred = nodes[(idx - 1) % n]
            node.successor = succ
            node.predecessor = pred
            node._update_successor_list()

            # Accurate finger calculation
            for entry in node.finger_table:
                # Find first node with chord_id >= entry.start (with wrap)
                found = None
                for candidate in nodes:
                    if candidate.chord_id >= entry.start:
                        found = candidate
                        break
                if found is None:
                    found = nodes[0]  # Wrap around
                entry.node = found

    def stabilize_network(self, rounds: int = 2) -> None:
        """Executes full distributed stabilization passes across all live nodes."""
        for _ in range(rounds):
            for node in list(self.nodes.values()):
                if node.is_alive:
                    node.stabilize()
            for node in list(self.nodes.values()):
                if node.is_alive:
                    node.fix_fingers()

    def route_key(self, key: str, initiator_id: Optional[str] = None) -> RoutingTrajectory:
        """Executes a Chord lookup and records high-fidelity trajectory telemetry.

        Args:
            key: Target key string to locate.
            initiator_id: Node ID where the lookup originates (random live node if None).

        Returns:
            Complete RoutingTrajectory with every hop, distance, and angle.
        """
        if not self.nodes:
            raise ValueError("Chord network is empty.")

        initiator = self.nodes.get(initiator_id) if initiator_id else self.get_random_live_node()
        if not initiator or not initiator.is_alive:
            initiator = self.get_random_live_node()
        if not initiator:
            raise ValueError("No live nodes in Chord network.")

        key_hash = sha256_hash(key, self.m_bits)
        trajectory = RoutingTrajectory(
            key=key,
            key_hash=key_hash,
            initiator_node_id=initiator.node_id,
            responsible_node_id="",
            total_hops=0,
        )

        t_start = time.perf_counter()
        responsible = initiator.find_successor(key_hash, trajectory=trajectory)
        trajectory.responsible_node_id = responsible.node_id
        trajectory.total_hops = len(trajectory.hops)
        trajectory.total_latency_ms = (time.perf_counter() - t_start) * 1000.0

        return trajectory

    def benchmark_hop_scaling(self, sample_keys: List[str]) -> Dict[str, Any]:
        """Empirically validates Chord's O(log N) hop count scaling."""
        if not self.nodes:
            return {"error": "Network empty"}

        hop_counts: List[int] = []
        latencies_us: List[float] = []

        for k in sample_keys:
            traj = self.route_key(k)
            hop_counts.append(traj.total_hops)
            latencies_us.append(traj.total_latency_ms * 1000.0)

        n = len(self._ordered_nodes)
        avg_hops = sum(hop_counts) / len(hop_counts) if hop_counts else 0.0
        theoretical_half_log2_n = 0.5 * math.log2(n) if n > 1 else 0.0

        return {
            "node_count": n,
            "key_count": len(sample_keys),
            "min_hops": min(hop_counts) if hop_counts else 0,
            "max_hops": max(hop_counts) if hop_counts else 0,
            "avg_hops": round(avg_hops, 3),
            "theoretical_0.5_log2_N": round(theoretical_half_log2_n, 3),
            "log2_N": round(math.log2(n), 3) if n > 0 else 0,
            "avg_latency_us": round(sum(latencies_us) / len(latencies_us), 1) if latencies_us else 0.0,
        }

    def export_topology(self) -> Dict[str, Any]:
        """Exports full network graph with finger tables and chord rings for 3D/2D UI."""
        nodes_dict = {}
        for nid, node in self.nodes.items():
            nodes_dict[nid] = node.export_node_info()

        return {
            "m_bits": self.m_bits,
            "ring_size": self.ring_size,
            "node_count": len(nodes_dict),
            "nodes": nodes_dict,
            "ordered_ids": [n.chord_id for n in self._ordered_nodes],
        }
