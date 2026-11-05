"""RingForge Consistent Hash Ring Engine.

Implements high-performance, deterministic consistent hashing with virtual nodes (vnodes),
logarithmic binary-search lookups, replication preference lists, and dynamic key migration
telemetry in pure Python standard library.
"""

from __future__ import annotations

import bisect
import hashlib
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple


def sha256_hash(key: str | bytes, bit_length: int = 32) -> int:
    """Computes a deterministic integer hash from a key using SHA-256.

    Args:
        key: Input string or bytes to hash.
        bit_length: Bit width for the ring space (e.g. 32 or 64).

    Returns:
        Integer in [0, 2^bit_length - 1].
    """
    if isinstance(key, str):
        key = key.encode("utf-8")
    digest = hashlib.sha256(key).digest()
    # Read the required number of bytes
    num_bytes = (bit_length + 7) // 8
    val = int.from_bytes(digest[:num_bytes], byteorder="big")
    mask = (1 << bit_length) - 1
    return val & mask


@dataclass(frozen=True)
class VirtualNode:
    """Represents a single virtual node placed on the consistent hash ring."""
    physical_node_id: str
    vnode_index: int
    token: int
    vnode_tag: str

    @property
    def angle_radians(self) -> float:
        """Returns the angular position on the ring in radians [0, 2pi)."""
        # Assuming 32-bit ring by default; dynamically calculated
        return (self.token / (1 << 32)) * 2 * math.pi

    def to_dict(self, bit_length: int = 32) -> Dict[str, Any]:
        """Serializes virtual node details with spatial coordinates."""
        max_val = 1 << bit_length
        angle = (self.token / max_val) * 2 * math.pi
        return {
            "physical_node_id": self.physical_node_id,
            "vnode_index": self.vnode_index,
            "vnode_tag": self.vnode_tag,
            "token": self.token,
            "angle_rad": angle,
            "angle_deg": math.degrees(angle),
            "coord_x": math.cos(angle),
            "coord_y": math.sin(angle),
        }


class ConsistentHashRing:
    """Production-grade Consistent Hash Ring with configurable virtual nodes."""

    def __init__(self, bit_length: int = 32, default_vnodes: int = 64):
        """Initializes the consistent hash ring.

        Args:
            bit_length: Number of bits in the identifier ring space (default: 32).
            default_vnodes: Default number of virtual nodes per physical node.
        """
        if bit_length not in (16, 32, 48, 64, 128, 160):
            raise ValueError(f"Unsupported bit_length {bit_length}; choose 16, 32, 48, 64, 128, or 160.")
        self.bit_length = bit_length
        self.ring_size = 1 << bit_length
        self.default_vnodes = default_vnodes

        # Ordered ring token positions for O(log N) bisect binary search
        self._ring_tokens: List[int] = []
        # Mapping token -> VirtualNode
        self._token_to_vnode: Dict[int, VirtualNode] = {}
        # Mapping physical_node_id -> set of tokens
        self._node_tokens: Dict[str, Set[int]] = {}
        # Physical node metadata
        self._nodes: Dict[str, Dict[str, Any]] = {}

    @property
    def total_tokens(self) -> int:
        """Returns the total number of virtual node tokens on the ring."""
        return len(self._ring_tokens)

    @property
    def physical_nodes(self) -> List[str]:
        """Returns a sorted list of unique physical node IDs."""
        return sorted(self._nodes.keys())

    def hash_key(self, key: str | bytes) -> int:
        """Hashes a key into the ring space [0, 2^bit_length - 1]."""
        return sha256_hash(key, self.bit_length)

    def add_node(self, node_id: str, vnodes: Optional[int] = None, metadata: Optional[Dict[str, Any]] = None) -> List[VirtualNode]:
        """Adds a physical node to the ring with the specified number of virtual nodes.

        Args:
            node_id: Unique string identifier for the physical node.
            vnodes: Number of virtual nodes (defaults to self.default_vnodes).
            metadata: Optional dictionary of node attributes (e.g. host, port, weight).

        Returns:
            List of generated VirtualNode instances.
        """
        if not node_id:
            raise ValueError("node_id cannot be empty.")
        if node_id in self._nodes:
            raise ValueError(f"Node '{node_id}' already exists on the hash ring.")

        vnode_count = vnodes if vnodes is not None else self.default_vnodes
        if vnode_count < 1:
            raise ValueError("vnodes must be at least 1.")

        created_vnodes: List[VirtualNode] = []
        tokens_for_node: Set[int] = set()

        for idx in range(vnode_count):
            tag = f"{node_id}#vn{idx}"
            token = self.hash_key(tag)
            # Resolve collision if token already present
            salt = 0
            while token in self._token_to_vnode:
                salt += 1
                token = self.hash_key(f"{tag}_{salt}")

            vnode = VirtualNode(
                physical_node_id=node_id,
                vnode_index=idx,
                token=token,
                vnode_tag=tag,
            )
            self._token_to_vnode[token] = vnode
            tokens_for_node.add(token)
            created_vnodes.append(vnode)

        self._node_tokens[node_id] = tokens_for_node
        self._nodes[node_id] = {
            "vnode_count": vnode_count,
            "metadata": metadata or {},
        }

        # Rebuild sorted token list
        self._rebuild_tokens()
        return created_vnodes

    def remove_node(self, node_id: str) -> bool:
        """Removes a physical node and all its virtual tokens from the ring.

        Args:
            node_id: Unique string identifier for the physical node.

        Returns:
            True if removed, False if node did not exist.
        """
        if node_id not in self._nodes:
            return False

        tokens = self._node_tokens.pop(node_id, set())
        for token in tokens:
            self._token_to_vnode.pop(token, None)

        self._nodes.pop(node_id, None)
        self._rebuild_tokens()
        return True

    def _rebuild_tokens(self) -> None:
        """Sorts the ring tokens for fast O(log N) binary search."""
        self._ring_tokens = sorted(self._token_to_vnode.keys())

    def get_node(self, key: str | bytes) -> Optional[str]:
        """Maps an arbitrary key to the responsible physical node on the ring.

        Uses logarithmic binary search (bisect_right) to find the first token >= key_hash.
        Wraps around to the first token if key_hash > all tokens.

        Args:
            key: Key string or raw bytes.

        Returns:
            Physical node ID, or None if the ring is empty."""
        if not self._ring_tokens:
            return None

        token = self.hash_key(key)
        idx = bisect.bisect_right(self._ring_tokens, token)
        if idx == len(self._ring_tokens):
            idx = 0  # Wrap around

        matched_token = self._ring_tokens[idx]
        return self._token_to_vnode[matched_token].physical_node_id

    def get_vnode(self, key: str | bytes) -> Optional[VirtualNode]:
        """Maps a key to the exact responsible VirtualNode instance."""
        if not self._ring_tokens:
            return None

        token = self.hash_key(key)
        idx = bisect.bisect_right(self._ring_tokens, token)
        if idx == len(self._ring_tokens):
            idx = 0

        matched_token = self._ring_tokens[idx]
        return self._token_to_vnode[matched_token]

    def get_preference_list(self, key: str | bytes, count: int) -> List[str]:
        """Returns the ordered list of distinct physical nodes responsible for a key (Replication).

        Walks the ring forward from the key's position, collecting unique physical nodes
        until `count` distinct nodes are found (or all physical nodes are exhausted).

        Args:
            key: Key to locate replicas for.
            count: Desired number of distinct replica nodes (Replication Factor R).

        Returns:
            Ordered list of distinct physical node IDs.
        """
        if not self._ring_tokens or count < 1:
            return []

        token = self.hash_key(key)
        idx = bisect.bisect_right(self._ring_tokens, token)

        total = len(self._ring_tokens)
        result: List[str] = []
        seen: Set[str] = set()

        for step in range(total):
            current_idx = (idx + step) % total
            vnode = self._token_to_vnode[self._ring_tokens[current_idx]]
            if vnode.physical_node_id not in seen:
                seen.add(vnode.physical_node_id)
                result.append(vnode.physical_node_id)
                if len(result) >= count:
                    break

        return result

    def get_token_range(self, token: int) -> Tuple[int, int]:
        """Returns the start (exclusive) and end (inclusive) token range assigned to a token.

        In a circular ring, a token is responsible for (predecessor_token, token].
        """
        if not self._ring_tokens:
            return (0, 0)
        idx = bisect.bisect_left(self._ring_tokens, token)
        if idx >= len(self._ring_tokens) or self._ring_tokens[idx] != token:
            raise ValueError(f"Token {token} not found on ring.")

        pred_idx = (idx - 1) % len(self._ring_tokens)
        pred_token = self._ring_tokens[pred_idx]
        return (pred_token, token)

    def analyze_distribution(self, sample_keys: List[str]) -> Dict[str, Any]:
        """Computes statistical uniformity and balance metrics for a set of sample keys.

        Args:
            sample_keys: Collection of keys to test.

        Returns:
            Dictionary with mean, standard deviation, min, max, coefficient of variation,
            and key count distribution per physical node.
        """
        if not self._nodes:
            return {"error": "Ring is empty"}

        counts: Dict[str, int] = {node_id: 0 for node_id in self._nodes}
        for k in sample_keys:
            assigned = self.get_node(k)
            if assigned:
                counts[assigned] += 1

        total_keys = len(sample_keys)
        n = len(self._nodes)
        if n == 0 or total_keys == 0:
            return {"total_keys": total_keys, "node_counts": counts}

        mean = total_keys / n
        variance = sum((c - mean) ** 2 for c in counts.values()) / n
        std_dev = math.sqrt(variance)
        cv = (std_dev / mean) if mean > 0 else 0.0  # Coefficient of Variation

        # Distribution entropy: H = -sum(p_i * log2(p_i))
        entropy = 0.0
        for c in counts.values():
            if c > 0:
                p = c / total_keys
                entropy -= p * math.log2(p)
        max_entropy = math.log2(n) if n > 1 else 1.0
        normalized_entropy = entropy / max_entropy if max_entropy > 0 else 1.0

        return {
            "total_keys": total_keys,
            "physical_node_count": n,
            "total_vnodes": len(self._ring_tokens),
            "counts_per_node": counts,
            "mean_keys_per_node": mean,
            "std_dev": std_dev,
            "coefficient_of_variation": cv,
            "entropy": entropy,
            "normalized_entropy": normalized_entropy,
            "min_node_keys": min(counts.values()),
            "max_node_keys": max(counts.values()),
        }

    def export_topology(self) -> Dict[str, Any]:
        """Exports the complete topological and spatial graph for 3D/2D visualization."""
        tokens_info = []
        for t in self._ring_tokens:
            vn = self._token_to_vnode[t]
            tokens_info.append(vn.to_dict(self.bit_length))

        nodes_info = {}
        for nid, data in self._nodes.items():
            tokens = sorted(list(self._node_tokens.get(nid, set())))
            nodes_info[nid] = {
                "id": nid,
                "vnode_count": data["vnode_count"],
                "token_count": len(tokens),
                "tokens": tokens,
                "metadata": data.get("metadata", {}),
            }

        return {
            "bit_length": self.bit_length,
            "ring_size": self.ring_size,
            "physical_node_count": len(self._nodes),
            "total_vnodes": len(self._ring_tokens),
            "nodes": nodes_info,
            "tokens": tokens_info,
        }
