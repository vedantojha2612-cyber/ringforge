"""RingForge REST & Real-Time Telemetry Server.

Provides a pure Python stdlib HTTP API streaming consistent hash ring topology,
Chord DHT routing trajectories, distributed key-value storage endpoints, and
OWASP-hardened static web serving for the 3D WebGL Torus and Terminal HUD.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from ringforge.chord import ChordNetwork
from ringforge.ring import ConsistentHashRing
from ringforge.store import DistributedStore


class TelemetryCluster:
    """Manages the in-memory shared state for consistent hash ring and Chord DHT."""

    def __init__(self, node_count: int = 8, vnodes_per_node: int = 32, m_bits: int = 32):
        self.lock = threading.RLock()
        self.m_bits = m_bits
        self.ring = ConsistentHashRing(bit_length=m_bits, default_vnodes=vnodes_per_node)
        self.chord = ChordNetwork(m_bits=m_bits)
        self.store = DistributedStore(self.ring, replication_factor=3, write_quorum=2)

        # Seed initial cluster
        self._init_cluster(node_count, vnodes_per_node)

    def _init_cluster(self, node_count: int, vnodes: int) -> None:
        """Bootstraps default nodes into ring and Chord network."""
        with self.lock:
            for i in range(node_count):
                nid = f"node-{i:02d}"
                self.ring.add_node(nid, vnodes=vnodes)
                self.chord.create_node(nid)
            self.chord.fast_stabilize()

            # Seed a few demo keys into store
            demo_keys = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
            for k in demo_keys:
                self.store.put(k, f"payload_{k}_v1")

    def route(self, key: str) -> Dict[str, Any]:
        """Routes a key through Chord and Consistent Hash Ring."""
        with self.lock:
            chord_traj = self.chord.route_key(key)
            ring_target = self.ring.get_node(key)
            ring_vnode = self.ring.get_vnode(key)
            prefs = self.ring.get_preference_list(key, self.store.replication_factor)

            return {
                "key": key,
                "consistent_hash_target": ring_target,
                "consistent_hash_vnode": ring_vnode.vnode_tag if ring_vnode else None,
                "replication_preference_list": prefs,
                "chord_trajectory": chord_traj.to_dict(),
            }

    def add_node(self, node_id: str, vnodes: int = 32) -> Dict[str, Any]:
        """Dynamically adds a node to the cluster."""
        with self.lock:
            vnodes_created = self.ring.add_node(node_id, vnodes=vnodes)
            cnode = self.chord.create_node(node_id)
            self.chord.fast_stabilize()
            rebalance_info = self.store.rebalance_cluster()
            return {
                "node_id": node_id,
                "chord_id": cnode.chord_id,
                "vnodes_created": len(vnodes_created),
                "rebalance": rebalance_info,
            }

    def remove_node(self, node_id: str) -> Dict[str, Any]:
        """Dynamically removes a node from the cluster."""
        with self.lock:
            ring_removed = self.ring.remove_node(node_id)
            chord_removed = self.chord.remove_node(node_id)
            self.chord.fast_stabilize()
            rebalance_info = self.store.rebalance_cluster()
            return {
                "node_id": node_id,
                "ring_removed": ring_removed,
                "chord_removed": chord_removed,
                "rebalance": rebalance_info,
            }

    def get_topology(self) -> Dict[str, Any]:
        """Returns unified topology for Consistent Hash Ring and Chord DHT."""
        with self.lock:
            ring_topo = self.ring.export_topology()
            chord_topo = self.chord.export_topology()
            store_stats = self.store.get_cluster_stats()

            return {
                "bit_length": self.m_bits,
                "ring": ring_topo,
                "chord": chord_topo,
                "store": store_stats,
            }


class RingForgeHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler streaming topology, telemetry, and serving static files."""

    server_version = "RingForge/1.0"
    cluster: TelemetryCluster
    web_dir: str

    def _set_security_headers(self, content_type: str = "application/json; charset=utf-8") -> None:
        """Enforces OWASP defensive response headers."""
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, data: Any, status: int = 200) -> None:
        """Sends a JSON formatted HTTP response."""
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self._set_security_headers("application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message: str, status: int = 400) -> None:
        """Sends a formatted JSON error response."""
        self._send_json({"error": message, "status": status}, status=status)

    def do_OPTIONS(self) -> None:
        """Handles CORS pre-flight requests."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self._set_security_headers()
        self.end_headers()

    def do_GET(self) -> None:
        """Handles REST telemetry and static asset GET requests."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. API: Topology
        if path == "/api/topology":
            self._send_json(self.cluster.get_topology())
            return

        # 2. API: Key Lookup & Routing Trajectory
        if path == "/api/lookup":
            key = query.get("key", [""])[0]
            if not key:
                self._send_error("Missing 'key' query parameter", status=400)
                return
            self._send_json(self.cluster.route(key))
            return

        # 3. API: Key-Value Store Read
        if path == "/api/store":
            key = query.get("key", [""])[0]
            if not key:
                self._send_error("Missing 'key' query parameter", status=400)
                return
            result = self.cluster.store.get(key)
            self._send_json({
                "key": result.key,
                "found": result.found,
                "value": result.value,
                "version": result.version,
                "primary_node": result.primary_node_id,
                "replica_nodes": result.replica_node_ids,
                "latency_ms": result.latency_ms,
            })
            return

        # 4. API: Dynamic Benchmarks
        if path == "/api/benchmark":
            sample_count = int(query.get("n", [200])[0])
            sample_count = max(10, min(sample_count, 10000))
            sample_keys = [f"bench_key_{i}" for i in range(sample_count)]

            ring_dist = self.cluster.ring.analyze_distribution(sample_keys)
            chord_scaling = self.cluster.chord.benchmark_hop_scaling(sample_keys[:100])

            self._send_json({
                "ring_distribution": ring_dist,
                "chord_hop_scaling": chord_scaling,
            })
            return

        # 5. Static file serving (with strict path traversal boundary containment)
        self._serve_static_file(path)

    def do_POST(self) -> None:
        """Handles cluster mutations (store write, add node, remove node)."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Guard against DoS via memory exhaustion (10 MB cap)
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send_error("Invalid Content-Length", 400)
            return

        if content_length > 10 * 1024 * 1024:
            self._send_error("Payload too large (> 10MB)", 413)
            return

        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception as e:
            self._send_error(f"Malformed JSON payload: {e}", 400)
            return

        # Store Put
        if path == "/api/store":
            key = payload.get("key")
            value = payload.get("value")
            if not key:
                self._send_error("Field 'key' is required", 400)
                return
            receipt = self.cluster.store.put(key, value)
            self._send_json({
                "status": "success",
                "receipt": {
                    "key": receipt.key,
                    "primary_node": receipt.primary_node_id,
                    "replica_nodes": receipt.replica_node_ids,
                    "success_count": receipt.success_count,
                    "latency_ms": receipt.latency_ms,
                }
            })
            return

        # Dynamic Node Mutation: Add or Remove
        if path == "/api/nodes":
            action = payload.get("action", "add")
            node_id = payload.get("node_id")
            if not node_id:
                self._send_error("Field 'node_id' is required", 400)
                return

            if action == "add":
                vnodes = int(payload.get("vnodes", 32))
                res = self.cluster.add_node(node_id, vnodes=vnodes)
                self._send_json(res)
            elif action == "remove":
                res = self.cluster.remove_node(node_id)
                self._send_json(res)
            else:
                self._send_error(f"Unknown action '{action}'", 400)
            return

        self._send_error("Endpoint not found", 404)

    def _serve_static_file(self, path: str) -> None:
        """Serves static files strictly contained within web_dir (CWE-22 defense)."""
        rel = path.lstrip("/")
        if not rel or rel == "":
            rel = "index.html"

        real_web_dir = os.path.realpath(self.web_dir)
        file_path = os.path.realpath(os.path.join(self.web_dir, rel))

        # Strict containment verification
        if not (file_path == real_web_dir or file_path.startswith(real_web_dir + os.sep)):
            self._send_error("Access Forbidden", status=403)
            return

        if not os.path.exists(file_path) or os.path.isdir(file_path):
            self._send_error("File not found", status=404)
            return

        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                content = f.read()

            self.send_response(200)
            self._set_security_headers(mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self._send_error(f"Failed to read file: {e}", status=500)

    def log_message(self, format: str, *args: Any) -> None:
        """Suppresses default stderr spam during tests/CLI unless verbose."""
        return


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    web_dir: Optional[str] = None,
    cluster: Optional[TelemetryCluster] = None,
) -> ThreadingHTTPServer:
    """Builds and returns a ThreadingHTTPServer instance for RingForge."""
    if web_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        web_dir = os.path.join(base_dir, "web")

    if cluster is None:
        cluster = TelemetryCluster()

    handler_class = RingForgeHTTPHandler
    handler_class.cluster = cluster
    handler_class.web_dir = web_dir

    return ThreadingHTTPServer((host, port), handler_class)
