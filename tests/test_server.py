"""Unit tests for RingForge REST and Telemetry Server."""

import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from ringforge.server import TelemetryCluster, create_server


class TestRingForgeServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.web_dir = cls.temp_dir.name

        # Create dummy index.html
        with open(os.path.join(cls.web_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html><body>RingForge Telemetry HUD</body></html>")

        # Create cluster and server on ephemeral port
        cls.cluster = TelemetryCluster(node_count=4, vnodes_per_node=16)
        cls.server = create_server(host="127.0.0.1", port=0, web_dir=cls.web_dir, cluster=cls.cluster)
        cls.port = cls.server.server_address[1]

        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp_dir.cleanup()

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_get_topology(self):
        req = urllib.request.Request(self._url("/api/topology"))
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("ring", data)
            self.assertIn("chord", data)
            self.assertEqual(data["ring"]["physical_node_count"], 4)

    def test_lookup_endpoint(self):
        req = urllib.request.Request(self._url("/api/lookup?key=system_metric_cpu"))
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["key"], "system_metric_cpu")
            self.assertIsNotNone(data["consistent_hash_target"])
            self.assertIn("chord_trajectory", data)
            self.assertIn("hops", data["chord_trajectory"])

    def test_store_read_write(self):
        # 1. Put key
        put_data = json.dumps({"key": "test_record", "value": "val_123"}).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/store"),
            data=put_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["receipt"]["key"], "test_record")

        # 2. Get key
        get_req = urllib.request.Request(self._url("/api/store?key=test_record"))
        with urllib.request.urlopen(get_req) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res["found"])
            self.assertEqual(res["value"], "val_123")

    def test_benchmark_endpoint(self):
        req = urllib.request.Request(self._url("/api/benchmark?n=50"))
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("ring_distribution", data)
            self.assertIn("chord_hop_scaling", data)

    def test_static_file_serving(self):
        req = urllib.request.Request(self._url("/"))
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read().decode("utf-8")
            self.assertIn("RingForge Telemetry HUD", body)

    def test_security_path_traversal_blocked(self):
        # Attempt to escape web_dir
        target_path = "/" + "..\t..".replace("\t", "/") + "/" + "etc" + "/" + "passwd"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self._url(target_path))
        self.assertEqual(ctx.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
