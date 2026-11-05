"""Unit and Security Tests for RingForge Static Web Serving and 3D WebGL Assets."""

import json
import os
import threading
import time
import unittest
import urllib.request
import urllib.error

from ringforge.server import TelemetryCluster, create_server


class TestRingForgeWeb(unittest.TestCase):
    """Test suite for static web serving, CWE-22 path traversal defense, and REST APIs."""

    @classmethod
    def setUpClass(cls):
        cls.cluster = TelemetryCluster(node_count=6, vnodes_per_node=16, m_bits=32)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.web_dir = os.path.join(base_dir, "web")
        cls.port = 18765
        cls.server = create_server(host="127.0.0.1", port=cls.port, web_dir=cls.web_dir, cluster=cls.cluster)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url)
        return urllib.request.urlopen(req, timeout=3.0)

    def _post(self, path: str, payload: dict):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=3.0)

    def test_static_index_html_serving(self):
        resp = self._get("/")
        self.assertEqual(resp.status, 200)
        content = resp.read().decode("utf-8")
        self.assertIn("RINGFORGE", content)
        self.assertIn("webgl-container", content)
        # Security headers
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")

    def test_static_app_js_serving(self):
        resp = self._get("/app.js")
        self.assertEqual(resp.status, 200)
        content = resp.read().decode("utf-8")
        self.assertIn("initScene", content)
        self.assertIn("updateSceneWithTopology", content)

    def test_vendor_assets_exist(self):
        resp_ctrl = self._get("/vendor/OrbitControls.js")
        self.assertEqual(resp_ctrl.status, 200)
        self.assertGreater(len(resp_ctrl.read()), 1000)

        resp_three = self._get("/vendor/three.min.js")
        self.assertEqual(resp_three.status, 200)
        self.assertGreater(len(resp_three.read()), 50000)

    def test_cwe22_path_traversal_blocked(self):
        # Attempting directory traversal outside web_dir
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/../server.py")
        self.assertEqual(ctx.exception.code, 403)

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/../../cli.py")
        self.assertEqual(ctx.exception.code, 403)

    def test_sibling_path_traversal_blocked(self):
        # Attempting sibling traversal
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/..%2F..%2Fcli.py")
        self.assertIn(ctx.exception.code, [400, 403, 404])

    def test_api_topology(self):
        resp = self._get("/api/topology")
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertIn("ring", data)
        self.assertIn("chord", data)
        self.assertIn("store", data)
        self.assertEqual(data["chord"]["node_count"], 6)

    def test_api_lookup_trajectory(self):
        resp = self._get("/api/lookup?key=quantum_entropy_shard")
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data["key"], "quantum_entropy_shard")
        self.assertIn("consistent_hash_target", data)
        self.assertIn("chord_trajectory", data)
        traj = data["chord_trajectory"]
        self.assertIn("hops", traj)
        self.assertIn("total_hops", traj)

    def test_api_store_put_and_get(self):
        # PUT
        put_resp = self._post("/api/store", {"key": "test_kv_key", "value": "payload_12345"})
        self.assertEqual(put_resp.status, 200)
        put_data = json.loads(put_resp.read().decode("utf-8"))
        self.assertEqual(put_data["status"], "success")

        # GET
        get_resp = self._get("/api/store?key=test_kv_key")
        self.assertEqual(get_resp.status, 200)
        get_data = json.loads(get_resp.read().decode("utf-8"))
        self.assertTrue(get_data["found"])
        self.assertEqual(get_data["value"], "payload_12345")

    def test_api_nodes_mutation(self):
        # ADD
        add_resp = self._post("/api/nodes", {"action": "add", "node_id": "node-dynamo", "vnodes": 16})
        self.assertEqual(add_resp.status, 200)
        add_data = json.loads(add_resp.read().decode("utf-8"))
        self.assertEqual(add_data["node_id"], "node-dynamo")

        # REMOVE
        rem_resp = self._post("/api/nodes", {"action": "remove", "node_id": "node-dynamo"})
        self.assertEqual(rem_resp.status, 200)
        rem_data = json.loads(rem_resp.read().decode("utf-8"))
        self.assertTrue(rem_data["ring_removed"])
        self.assertTrue(rem_data["chord_removed"])


if __name__ == "__main__":
    unittest.main()
