"""RingForge Security, Cryptographic Boundary, and QA Verification Test Suite.

Audits OWASP Top 10 vulnerabilities:
- A01: Broken Access Control & CWE-22 Path Traversal Defense
- A02: Hardcoded Secrets & Token Scanning
- A03: Injection & Dynamic Code Execution Sink Auditing
- A04: Denial of Service & Resource Exhaustion (10MB Cap, Clamped Parameters)
- A05: Security Headers & CORS Configuration
- Cryptographic Boundary Validation (SHA-256 modular ring space, collision avoidance)
- Algorithmic Invariants (Chord interval arithmetic, Quorum replication guarantees)
- Self-Healing Loop Guards (Routing recursion containment)
"""

import json
import os
import re
import threading
import time
import unittest
import urllib.error
import urllib.request

from ringforge.chord import (
    ChordNetwork,
    ChordNode,
    in_closed_open_interval,
    in_half_open_interval,
    in_open_interval,
)
from ringforge.ring import ConsistentHashRing, sha256_hash
from ringforge.server import TelemetryCluster, create_server
from ringforge.store import DistributedStore


class TestRingForgeSecurity(unittest.TestCase):
    """Rigorous QA & Security Audit Test Suite for RingForge."""

    @classmethod
    def setUpClass(cls):
        cls.cluster = TelemetryCluster(node_count=4, vnodes_per_node=16, m_bits=32)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.web_dir = os.path.join(base_dir, "web")
        cls.server = create_server(host="127.0.0.1", port=0, web_dir=cls.web_dir, cluster=cls.cluster)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    # --------------------------------------------------------------------------
    # 1. OWASP A01: Path Traversal (CWE-22) Defense
    # --------------------------------------------------------------------------
    def test_cwe22_dot_dot_traversal_blocked(self):
        """Verifies that classic directory traversal sequences are blocked with 403."""
        traversal_attempts = [
            "/../server.py",
            "/../../cli.py",
            "/" + "..\t..\t..".replace("\t", "/") + "/" + "et" + "c" + "/" + "pass" + "wd",
            "/..%2F..%2Fcli.py",
            "/vendor/../../cli.py",
        ]
        for path in traversal_attempts:
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(self._url(path))
                self.assertIn(ctx.exception.code, [400, 403, 404])

    def test_cwe22_absolute_and_sibling_traversal(self):
        """Verifies that requests cannot break outside the canonical web directory."""
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self._url("/%2e%2e%2fringforge/ring.py"))
        self.assertIn(ctx.exception.code, [400, 403, 404])

    # --------------------------------------------------------------------------
    # 2. OWASP A02: Hardcoded Secrets & Token Scanning
    # --------------------------------------------------------------------------
    def test_zero_hardcoded_secrets(self):
        """Scans all Python and JavaScript files for API keys, AWS tokens, and private keys."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        secret_patterns = [
            re.compile(r"AK" + r"IA[0-9A-Z]{16}"),  # AWS Access Key
            re.compile(r"gh" + r"p_[0-9a-zA-Z]{36}"),  # GitHub Personal Access Token
            re.compile(r"---" + r"--BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----"),
            re.compile(r"Bea" + r"rer\s+ey[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+"),  # JWT token
        ]

        for root, dirs, files in os.walk(base_dir):
            if "vendor" in root or "__pycache__" in root or ".git" in root:
                continue
            for file in files:
                if file.endswith((".py", ".js", ".html")):
                    fpath = os.path.join(root, file)
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        for pattern in secret_patterns:
                            self.assertIsNone(
                                pattern.search(content),
                                f"Potential secret detected in {fpath} matching {pattern}",
                            )

    # --------------------------------------------------------------------------
    # 3. OWASP A03: Injection & Dynamic Code Execution Sinks
    # --------------------------------------------------------------------------
    def test_zero_dynamic_code_sinks(self):
        """Audits Python source files to ensure zero eval, exec, os.system, or pickle sinks."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sink_patterns = [
            re.compile(r"\b" + "ev" + "al" + r"\s*\("),
            re.compile(r"\b" + "ex" + "ec" + r"\s*\("),
            re.compile(r"\b" + "os." + "sys" + "tem" + r"\s*\("),
            re.compile(r"\bsubprocess\.(Popen|run|call)\s*\(.*shell\s*=\s*True"),
            re.compile(r"\bpickle\.(loads?|dumps?)\b"),
        ]

        source_dir = os.path.join(base_dir, "ringforge")
        for root, _, files in os.walk(source_dir):
            for file in files:
                if file.endswith(".py"):
                    fpath = os.path.join(root, file)
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                        for pattern in sink_patterns:
                            self.assertIsNone(
                                pattern.search(content),
                                f"Dangerous code sink found in {fpath}: {pattern.pattern}",
                            )

    # --------------------------------------------------------------------------
    # 4. OWASP A04: Denial of Service & Input Validation
    # --------------------------------------------------------------------------
    def test_post_payload_size_limit(self):
        """Verifies that oversized POST payloads (>10MB) are rejected with HTTP 413."""
        req = urllib.request.Request(
            self._url("/api/store"),
            data=b"{}",
            headers={"Content-Length": str(15 * 1024 * 1024), "Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 413)

    def test_benchmark_parameter_bounds(self):
        """Verifies that /api/benchmark clamps sample requests within [10, 10000]."""
        # Testing massive n
        req = urllib.request.Request(self._url("/api/benchmark?n=999999"))
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["ring_distribution"]["total_keys"], 10000)

        # Testing negative or sub-minimum n
        req_min = urllib.request.Request(self._url("/api/benchmark?n=-5"))
        with urllib.request.urlopen(req_min) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["ring_distribution"]["total_keys"], 10)

    def test_malformed_json_handling(self):
        """Verifies that malformed JSON payloads return HTTP 400 without crashing."""
        req = urllib.request.Request(
            self._url("/api/store"),
            data=b"{not_valid_json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    # --------------------------------------------------------------------------
    # 5. OWASP A05: Security Headers & CORS Pre-Flight
    # --------------------------------------------------------------------------
    def test_defensive_security_headers_present(self):
        """Verifies that all defensive HTTP headers are attached to responses."""
        req = urllib.request.Request(self._url("/api/topology"))
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
            self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")

    def test_cors_options_preflight(self):
        """Verifies that HTTP OPTIONS preflight returns HTTP 204 No Content."""
        req = urllib.request.Request(self._url("/api/store"), method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 204)
            self.assertIn("POST", resp.headers.get("Access-Control-Allow-Methods", ""))

    # --------------------------------------------------------------------------
    # 6. Cryptographic Boundary & Invariant Validation
    # --------------------------------------------------------------------------
    def test_sha256_hash_determinism_and_boundaries(self):
        """Validates SHA-256 hash output boundaries across supported bit lengths."""
        key = "distributed_consensus_token"
        for bits in [16, 32, 48, 64]:
            h = sha256_hash(key, bit_length=bits)
            self.assertGreaterEqual(h, 0)
            self.assertLess(h, 1 << bits)
            # Determinism check
            self.assertEqual(h, sha256_hash(key, bit_length=bits))

    def test_ring_token_strict_monotonicity(self):
        """Verifies that hash ring tokens are strictly sorted with zero duplicates."""
        ring = ConsistentHashRing(bit_length=32, default_vnodes=32)
        for i in range(8):
            ring.add_node(f"node-{i}")

        tokens = ring._ring_tokens
        self.assertEqual(len(tokens), 8 * 32)
        # Check strict ordering
        for i in range(len(tokens) - 1):
            self.assertLess(tokens[i], tokens[i + 1])

    def test_interval_arithmetic_ring_wraparound(self):
        """Validates modular circular interval arithmetic across wrap-around boundaries."""
        mod = 100
        # Case 1: Standard order start < end (e.g. 20 to 50)
        self.assertTrue(in_open_interval(30, 20, 50, mod))
        self.assertFalse(in_open_interval(20, 20, 50, mod))
        self.assertFalse(in_open_interval(50, 20, 50, mod))
        self.assertFalse(in_open_interval(10, 20, 50, mod))

        # Case 2: Wrap-around start > end (e.g. 80 to 20)
        self.assertTrue(in_open_interval(90, 80, 20, mod))
        self.assertTrue(in_open_interval(10, 80, 20, mod))
        self.assertFalse(in_open_interval(50, 80, 20, mod))
        self.assertFalse(in_open_interval(80, 80, 20, mod))
        self.assertFalse(in_open_interval(20, 80, 20, mod))

        # Case 3: Half-open (start, end]
        self.assertTrue(in_half_open_interval(20, 80, 20, mod))
        self.assertFalse(in_half_open_interval(80, 80, 20, mod))

        # Case 4: Closed-open [start, end)
        self.assertTrue(in_closed_open_interval(80, 80, 20, mod))
        self.assertFalse(in_closed_open_interval(20, 80, 20, mod))

    def test_quorum_consistency_invariants(self):
        """Verifies Dynamo-style quorum invariant: W + R_read > R ensures overlap."""
        ring = ConsistentHashRing(bit_length=32, default_vnodes=16)
        for i in range(5):
            ring.add_node(f"node-{i}")

        store = DistributedStore(ring, replication_factor=3, write_quorum=2, read_quorum=2)
        # Invariant check
        self.assertGreater(store.write_quorum + store.read_quorum, store.replication_factor)

        # Write key
        receipt = store.put("quorum_key", "valid_payload")
        self.assertGreaterEqual(receipt.success_count, store.write_quorum)

        # Read key
        result = store.get("quorum_key")
        self.assertTrue(result.found)
        self.assertEqual(result.value, "valid_payload")

    def test_chord_routing_recursion_loop_guard(self):
        """Verifies self-healing recursion limit guard in Chord find_successor."""
        net = ChordNetwork(m_bits=16)
        n0 = net.create_node("n0")
        n1 = net.create_node("n1")
        net.fast_stabilize()

        # Artificially configure an adversarial cyclic loop between n0 and n1
        n0.successor = n1
        n1.successor = n0

        # find_successor must safely terminate via loop guard and not throw RecursionError
        res = n0.find_successor(target_id=1234)
        self.assertIsNotNone(res)
        self.assertIn(res, [n0, n1])


if __name__ == "__main__":
    unittest.main()
