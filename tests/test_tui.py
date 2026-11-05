"""Unit and Visual Rendering Tests for RingForge 15 FPS ANSI Terminal Radar HUD."""

import unittest
from ringforge.server import TelemetryCluster
from ringforge.tui import (
    strip_ansi,
    pad_ansi,
    generate_sparkline,
    build_ring_radar,
    format_radar_dashboard,
    run_terminal_radar,
    CYAN,
    GOLD,
)


class TestRingForgeTUI(unittest.TestCase):
    """Test suite for ANSI radar, sparkline generators, and HUD dashboard assembly."""

    def setUp(self):
        self.cluster = TelemetryCluster(node_count=8, vnodes_per_node=16, m_bits=32)

    def test_strip_ansi(self):
        text_with_ansi = f"{CYAN}Hello {GOLD}RingForge{CYAN}! \033[0m"
        plain = strip_ansi(text_with_ansi)
        self.assertEqual(plain, "Hello RingForge! ")

    def test_pad_ansi(self):
        text = f"{CYAN}Node{CYAN}"
        # Visible length is 4 ("Node")
        padded_left = pad_ansi(text, 10, align="left")
        self.assertEqual(len(strip_ansi(padded_left)), 10)
        self.assertTrue(padded_left.endswith("      "))

        padded_right = pad_ansi(text, 10, align="right")
        self.assertEqual(len(strip_ansi(padded_right)), 10)
        self.assertTrue(padded_right.startswith("      "))

        padded_center = pad_ansi(text, 10, align="center")
        self.assertEqual(len(strip_ansi(padded_center)), 10)

    def test_generate_sparkline(self):
        # Empty values
        spark_empty = generate_sparkline([], width=10)
        self.assertEqual(len(strip_ansi(spark_empty)), 10)

        # Sequence of values
        vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        spark = generate_sparkline(vals, width=8)
        self.assertEqual(len(strip_ansi(spark)), 8)
        # Should contain bar glyphs
        self.assertTrue(any(c in spark for c in [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]))

    def test_build_ring_radar(self):
        radar_lines = build_ring_radar(self.cluster, last_route=None, grid_w=40, grid_h=15)
        self.assertGreater(len(radar_lines), 10)
        # Check border
        self.assertTrue("┌" in radar_lines[0])
        self.assertTrue("└" in radar_lines[-1])

        # Test with active trajectory route
        route = self.cluster.route("test_key_quantum")
        radar_with_route = build_ring_radar(self.cluster, last_route=route, grid_w=40, grid_h=15)
        # Key star or hop marker should be present
        full_grid_text = "".join(radar_with_route)
        self.assertTrue("★" in full_grid_text or "▲" in full_grid_text)

    def test_format_radar_dashboard(self):
        route = self.cluster.route("probe_alpha")
        frame = format_radar_dashboard(
            self.cluster,
            last_route=route,
            rolling_latencies=[0.1, 0.2, 0.15],
            rolling_hops=[2, 3, 2],
            tick_count=1,
        )
        self.assertIn("RINGFORGE // 15 FPS ANSI/BRAILLE", frame)
        self.assertIn("NODES:", frame)
        self.assertIn("VNODES:", frame)
        self.assertIn("probe_alpha", frame)
        self.assertIn("REAL-TIME TELEMETRY SPARKLINES:", frame)

    def test_run_terminal_radar_headless(self):
        # Test running 3 ticks headless without error
        run_terminal_radar(cluster=self.cluster, target_fps=30.0, max_ticks=3)


if __name__ == "__main__":
    unittest.main()
