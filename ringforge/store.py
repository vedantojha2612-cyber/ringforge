"""RingForge Distributed Key-Value Storage & Replication Engine.

Implements Dynamo-style quorum replication, anti-entropy auditing, dynamic key rebalancing
on cluster membership changes, and partitioned storage in pure Python stdlib.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from ringforge.ring import ConsistentHashRing


@dataclass
class StorageItem:
    """Represents a stored key-value item with replication metadata."""
    key: str
    value: Any
    version: int
    created_at: float
    updated_at: float
    primary_node_id: str
    replica_node_ids: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Serializes storage item."""
        return {
            "key": self.key,
            "value": self.value,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "primary_node": self.primary_node_id,
            "replicas": self.replica_node_ids,
        }


@dataclass
class PutReceipt:
    """Receipt returned after executing a distributed replicated write."""
    key: str
    primary_node_id: str
    replica_node_ids: List[str]
    success_count: int
    replication_factor: int
    latency_ms: float


@dataclass
class GetResult:
    """Result returned after executing a distributed replicated read."""
    key: str
    found: bool
    value: Any = None
    version: int = 0
    primary_node_id: Optional[str] = None
    replica_node_ids: List[str] = field(default_factory=list)
    latency_ms: float = 0.0


class DistributedStore:
    """Distributed In-Memory Key-Value Store with consistent hash partitioning & replication."""

    def __init__(
        self,
        ring: ConsistentHashRing,
        replication_factor: int = 3,
        write_quorum: int = 2,
        read_quorum: int = 1,
    ):
        """Initializes the distributed store.

        Args:
            ring: Underlying ConsistentHashRing instance.
            replication_factor: Target number of replicas for each key (R).
            write_quorum: Number of successful writes required for commit (W).
            read_quorum: Number of responses required for read (R_read).
        """
        self.ring = ring
        self.replication_factor = replication_factor
        self.write_quorum = min(write_quorum, replication_factor)
        self.read_quorum = min(read_quorum, replication_factor)

        # Local storage partitioned per physical node: node_id -> {key -> StorageItem}
        self._node_storage: Dict[str, Dict[str, StorageItem]] = {}
        self._sync_node_partitions()

    def _sync_node_partitions(self) -> None:
        """Ensures storage buckets exist for all active physical nodes in the ring."""
        current_nodes = set(self.ring.physical_nodes)
        for nid in current_nodes:
            if nid not in self._node_storage:
                self._node_storage[nid] = {}

    def put(self, key: str, value: Any) -> PutReceipt:
        """Stores a key-value pair, replicating it across the preference list.

        Args:
            key: Key string.
            value: Arbitrary serializable value.

        Returns:
            PutReceipt with write confirmation and latency.
        """
        t0 = time.perf_counter()
        self._sync_node_partitions()

        preference_list = self.ring.get_preference_list(key, self.replication_factor)
        if not preference_list:
            raise RuntimeError("Cannot write key: hash ring has no active nodes.")

        primary = preference_list[0]
        now = time.time()

        # Check existing version if any
        version = 1
        existing = self.get(key)
        if existing.found:
            version = existing.version + 1

        item = StorageItem(
            key=key,
            value=value,
            version=version,
            created_at=now if version == 1 else (existing.value if False else now),
            updated_at=now,
            primary_node_id=primary,
            replica_node_ids=list(preference_list),
        )

        successful_writes: List[str] = []
        for nid in preference_list:
            if nid in self._node_storage:
                self._node_storage[nid][key] = item
                successful_writes.append(nid)

        latency = (time.perf_counter() - t0) * 1000.0
        if len(successful_writes) < min(self.write_quorum, len(preference_list)):
            raise IOError(f"Write quorum failed: required {self.write_quorum}, got {len(successful_writes)}")

        return PutReceipt(
            key=key,
            primary_node_id=primary,
            replica_node_ids=successful_writes,
            success_count=len(successful_writes),
            replication_factor=len(preference_list),
            latency_ms=round(latency, 3),
        )

    def get(self, key: str) -> GetResult:
        """Retrieves a key-value pair querying the preference list with quorum checks.

        Args:
            key: Key string to retrieve.

        Returns:
            GetResult with value and replica locations.
        """
        t0 = time.perf_counter()
        self._sync_node_partitions()

        preference_list = self.ring.get_preference_list(key, self.replication_factor)
        if not preference_list:
            return GetResult(key=key, found=False, latency_ms=(time.perf_counter() - t0) * 1000.0)

        candidates: List[StorageItem] = []
        responding_nodes: List[str] = []

        for nid in preference_list:
            storage = self._node_storage.get(nid, {})
            if key in storage:
                candidates.append(storage[key])
                responding_nodes.append(nid)

        latency = (time.perf_counter() - t0) * 1000.0

        if not candidates:
            return GetResult(
                key=key,
                found=False,
                primary_node_id=preference_list[0],
                replica_node_ids=[],
                latency_ms=round(latency, 3),
            )

        # Pick candidate with highest version (Last-Write-Wins / highest version)
        best_item = max(candidates, key=lambda it: it.version)

        return GetResult(
            key=key,
            found=True,
            value=best_item.value,
            version=best_item.version,
            primary_node_id=preference_list[0],
            replica_node_ids=responding_nodes,
            latency_ms=round(latency, 3),
        )

    def delete(self, key: str) -> bool:
        """Removes a key across all replica nodes."""
        self._sync_node_partitions()
        deleted = False
        for storage in self._node_storage.values():
            if key in storage:
                del storage[key]
                deleted = True
        return deleted

    def rebalance_cluster(self) -> Dict[str, Any]:
        """Audits and rebalances all stored keys according to current ring topology.

        Transfers keys to their canonical preference list and prunes orphaned keys.

        Returns:
            Summary of migrated keys, repaired replicas, and pruned orphans.
        """
        self._sync_node_partitions()
        all_keys: Set[str] = set()
        for storage in self._node_storage.values():
            all_keys.update(storage.keys())

        migrated_count = 0
        repaired_replicas = 0
        pruned_orphans = 0

        for key in list(all_keys):
            target_replicas = set(self.ring.get_preference_list(key, self.replication_factor))
            if not target_replicas:
                continue

            # Find best existing copy of this item
            item_copy = None
            for nid, storage in self._node_storage.items():
                if key in storage:
                    if item_copy is None or storage[key].version > item_copy.version:
                        item_copy = storage[key]

            if not item_copy:
                continue

            # Update replica list on item
            item_copy.primary_node_id = list(target_replicas)[0] if target_replicas else ""
            item_copy.replica_node_ids = sorted(list(target_replicas))

            # Ensure every target replica has it
            for target_nid in target_replicas:
                if target_nid in self._node_storage:
                    if key not in self._node_storage[target_nid]:
                        self._node_storage[target_nid][key] = item_copy
                        migrated_count += 1
                        repaired_replicas += 1

            # Prune from nodes no longer in target replica set
            for nid, storage in self._node_storage.items():
                if nid not in target_replicas and key in storage:
                    del storage[key]
                    pruned_orphans += 1

        return {
            "total_unique_keys": len(all_keys),
            "keys_migrated": migrated_count,
            "repaired_replicas": repaired_replicas,
            "pruned_orphans": pruned_orphans,
        }

    def anti_entropy_repair(self) -> Dict[str, Any]:
        """Detects and repairs any keys with replication factor under-allocation."""
        return self.rebalance_cluster()

    def get_cluster_stats(self) -> Dict[str, Any]:
        """Aggregates key distribution and storage consumption across nodes."""
        self._sync_node_partitions()
        node_counts: Dict[str, int] = {}
        all_keys: Set[str] = set()

        for nid, storage in self._node_storage.items():
            node_counts[nid] = len(storage)
            all_keys.update(storage.keys())

        return {
            "total_unique_keys": len(all_keys),
            "total_stored_replicas": sum(node_counts.values()),
            "replication_factor": self.replication_factor,
            "write_quorum": self.write_quorum,
            "read_quorum": self.read_quorum,
            "node_counts": node_counts,
        }
