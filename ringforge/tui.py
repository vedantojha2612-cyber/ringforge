"""RingForge 15 FPS ANSI/Braille Terminal Radar HUD.

Zero-dependency pure Python stdlib real-time cyberpunk terminal dashboard
for Consistent Hash Ring topology, Chord DHT logarithmic skip-routing trajectories,
virtual node balancing, and replication telemetry.
"""

from __future__ import annotations

import math
import os
import random
import re
import select
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from ringforge.server import TelemetryCluster

# ANSI Escape Codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"

# Cyberpunk Color Palette
CYAN = "\033[38;5;51m"
BRIGHT_CYAN = "\033[38;5;87m"
GOLD = "\033[38;5;220m"
AMBER = "\033[38;5;214m"
YELLOW = "\033[38;5;226m"
EMERALD = "\033[38;5;48m"
GREEN = "\033[38;5;82m"
VIOLET = "\033[38;5;177m"
MAGENTA = "\033[38;5;201m"
PURPLE = "\033[38;5;141m"
CRIMSON = "\033[38;5;197m"
AZURE = "\033[38;5;39m"
SLATE = "\033[38;5;244m"
DARK_SLATE = "\033[38;5;238m"
WHITE = "\033[38;5;255m"

# Sparkline Unicode Glyphs
BAR_GLYPHS = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
BRAILLE_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

NODE_COLORS = [
    CYAN,
    EMERALD,
    GOLD,
    PURPLE,
    AZURE,
    MAGENTA,
    AMBER,
    VIOLET,
    BRIGHT_CYAN,
    GREEN,
]


def strip_ansi(text: str) -> str:
    """Removes ANSI escape codes to compute visible character length."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


def pad_ansi(text: str, width: int, align: str = "left") -> str:
    """Pads ANSI formatted string to desired terminal column width."""
    visible_len = len(strip_ansi(text))
    pad = max(0, width - visible_len)
    if align == "right":
        return (" " * pad) + text
    if align == "center":
        left = pad // 2
        right = pad - left
        return (" " * left) + text + (" " * right)
    return text + (" " * pad)


def generate_sparkline(values: List[float], width: int = 24, color: str = CYAN) -> str:
    """Renders a dynamic ASCII/Unicode sparkline from a series of floats."""
    if not values:
        return f"{DARK_SLATE}{'─' * width}{RESET}"
    recent = values[-width:]
    if len(recent) < width:
        recent = [recent[0]] * (width - len(recent)) + recent

    min_v = min(recent)
    max_v = max(recent)
    span = max_v - min_v if (max_v - min_v) > 1e-6 else 1.0

    chars = []
    for v in recent:
        norm = max(0.0, min(1.0, (v - min_v) / span))
        idx = int(norm * (len(BAR_GLYPHS) - 1))
        chars.append(BAR_GLYPHS[idx])
    return f"{color}{''.join(chars)}{RESET}"


def build_ring_radar(
    cluster: TelemetryCluster,
    last_route: Optional[Dict[str, Any]] = None,
    grid_w: int = 50,
    grid_h: int = 23,
) -> List[str]:
    """Generates a 2D ANSI/ASCII spatial projection of the circular hash ring."""
    # 2D character grid: (char, color, priority)
    grid = [[(" ", DARK_SLATE, 0) for _ in range(grid_w)] for _ in range(grid_h)]

    cx = grid_w // 2
    cy = grid_h // 2
    rx = (grid_w // 2) - 3
    ry = (grid_h // 2) - 2

    # Draw the circular orbit ring perimeter
    ring_points = 180
    for step in range(ring_points):
        theta = (step / ring_points) * 2 * math.pi
        gx = int(round(cx + rx * math.cos(theta)))
        gy = int(round(cy + ry * math.sin(theta)))
        if 0 <= gx < grid_w and 0 <= gy < grid_h:
            grid[gy][gx] = ("·", DARK_SLATE, 1)

    # Plot virtual node density markers along the ring
    ring_topo = cluster.ring.export_topology()
    tokens = ring_topo.get("tokens", [])
    bit_length = ring_topo.get("bit_length", 32)
    ring_size = 1 << bit_length

    # Hash physical node to color
    node_color_map: Dict[str, str] = {}
    for idx, (nid, _) in enumerate(sorted(cluster.ring._nodes.items())):
        node_color_map[nid] = NODE_COLORS[idx % len(NODE_COLORS)]

    for tok_entry in tokens:
        token_val = tok_entry["token"]
        nid = tok_entry["physical_node_id"]
        theta = (token_val / ring_size) * 2 * math.pi - (math.pi / 2)
        gx = int(round(cx + rx * math.cos(theta)))
        gy = int(round(cy + ry * math.sin(theta)))
        if 0 <= gx < grid_w and 0 <= gy < grid_h:
            if grid[gy][gx][2] <= 2:
                grid[gy][gx] = ("•", node_color_map.get(nid, SLATE), 2)

    # Plot physical Chord nodes as prominent stations
    chord_topo = cluster.chord.export_topology()
    for idx, (nid, cinfo) in enumerate(chord_topo.get("nodes", {}).items()):
        cid = cinfo["chord_id"]
        theta = (cid / ring_size) * 2 * math.pi - (math.pi / 2)
        gx = int(round(cx + rx * math.cos(theta)))
        gy = int(round(cy + ry * math.sin(theta)))
        clr = node_color_map.get(nid, GOLD)

        # Plot station icon
        icon = str(idx % 10)
        if 0 <= gx < grid_w and 0 <= gy < grid_h:
            grid[gy][gx] = (icon, f"{BOLD}{clr}", 6)

    # Draw trajectory routing path if last_route exists
    if last_route:
        chord_traj = last_route.get("chord_trajectory", {})
        hops = chord_traj.get("hops", [])
        key_hash = chord_traj.get("key_hash", 0)

        # Target key position
        k_theta = (key_hash / ring_size) * 2 * math.pi - (math.pi / 2)
        kx = int(round(cx + rx * math.cos(k_theta)))
        ky = int(round(cy + ry * math.sin(k_theta)))
        if 0 <= kx < grid_w and 0 <= ky < grid_h:
            grid[ky][kx] = ("★", f"{BOLD}{GOLD}", 10)

        # Draw hop skip vectors across ring
        for h in hops:
            from_cid = h["from_chord_id"]
            to_cid = h["to_chord_id"]
            th1 = (from_cid / ring_size) * 2 * math.pi - (math.pi / 2)
            th2 = (to_cid / ring_size) * 2 * math.pi - (math.pi / 2)
            x1 = cx + rx * math.cos(th1)
            y1 = cy + ry * math.sin(th1)
            x2 = cx + rx * math.cos(th2)
            y2 = cy + ry * math.sin(th2)

            # Bresenham-like linear interpolation across interior chords
            line_steps = 14
            for s in range(1, line_steps):
                t = s / line_steps
                lx = int(round(x1 + t * (x2 - x1)))
                ly = int(round(y1 + t * (y2 - y1)))
                if 0 <= lx < grid_w and 0 <= ly < grid_h:
                    if grid[ly][lx][2] < 5:
                        grid[ly][lx] = ("-", AMBER, 4)

            # Mark destination station of hop
            to_gx = int(round(x2))
            to_gy = int(round(y2))
            if 0 <= to_gx < grid_w and 0 <= to_gy < grid_h:
                grid[to_gy][to_gx] = ("▲", f"{BOLD}{GREEN}", 8)

    # Format into ANSI output lines
    output_lines = []
    top_border = f"{CYAN}┌" + ("─" * grid_w) + f"┐{RESET}"
    output_lines.append(top_border)

    for row in grid:
        line_chars = []
        current_clr = None
        for char, clr, _ in row:
            if clr != current_clr:
                line_chars.append(clr)
                current_clr = clr
            line_chars.append(char)
        line_chars.append(RESET)
        output_lines.append(f"{CYAN}│{RESET}" + "".join(line_chars) + f"{CYAN}│{RESET}")

    bot_border = f"{CYAN}└" + ("─" * grid_w) + f"┘{RESET}"
    output_lines.append(bot_border)
    return output_lines


def format_radar_dashboard(
    cluster: TelemetryCluster,
    last_route: Optional[Dict[str, Any]],
    rolling_latencies: List[float],
    rolling_hops: List[int],
    tick_count: int = 0,
) -> str:
    """Assembles the full multi-tier ANSI terminal HUD frame."""
    topo = cluster.get_topology()
    ring_info = topo["ring"]
    chord_info = topo["chord"]
    store_info = topo["store"]

    spinner = BRAILLE_SPINNER[tick_count % len(BRAILLE_SPINNER)]
    line_w = 108
    lines: List[str] = []

    # 1. Header Bar
    title = f"{BOLD}{MAGENTA}⚡ RINGFORGE // 15 FPS ANSI/BRAILLE CONSISTENT HASH & CHORD RADAR ⚡{RESET}"
    status_tag = f"{EMERALD}● LIVE TELEMETRY{RESET} {spinner}"
    hdr = f" {title}  {pad_ansi(status_tag, line_w - len(strip_ansi(title)) - 4, 'right')}"
    lines.append(hdr)
    lines.append(f"{CYAN}{'═' * line_w}{RESET}")

    # 2. Top Summary Metrics
    node_cnt = chord_info["node_count"]
    vnodes_tot = ring_info["total_vnodes"]
    repl_factor = store_info["replication_factor"]
    stored_keys = store_info.get("total_unique_keys", 0)
    max_hops_bound = round(math.log2(node_cnt), 2) if node_cnt > 0 else 0.0

    m_info = (
        f" {WHITE}NODES:{RESET} {GOLD}{node_cnt}{RESET}  "
        f"│ {WHITE}VNODES:{RESET} {CYAN}{vnodes_tot}{RESET}  "
        f"│ {WHITE}MODULUS:{RESET} {EMERALD}2^{topo['bit_length']}{RESET}  "
        f"│ {WHITE}REPLICATION R:{RESET} {AZURE}{repl_factor}{RESET}  "
        f"│ {WHITE}KEYS STORED:{RESET} {VIOLET}{stored_keys}{RESET}  "
        f"│ {WHITE}LOG2(N) BOUND:{RESET} {GOLD}{max_hops_bound}{RESET}"
    )
    lines.append(pad_ansi(m_info, line_w))
    lines.append(f"{DARK_SLATE}{'─' * line_w}{RESET}")

    # 3. Main Split View: Left = Circular Radar (50x23), Right = Telemetry Cockpit
    radar_lines = build_ring_radar(cluster, last_route, grid_w=50, grid_h=21)

    right_lines: List[str] = []

    # Trajectory Telemetry Box
    if last_route:
        ctraj = last_route.get("chord_trajectory", {})
        rk = last_route.get("key", "")
        p_node = last_route.get("consistent_hash_target", "")
        p_vnode = last_route.get("consistent_hash_vnode", "")
        pref_list = last_route.get("replication_preference_list", [])
        hops = ctraj.get("hops", [])
        tot_hops = ctraj.get("total_hops", 0)
        tot_lat = ctraj.get("total_latency_ms", 0.0)

        right_lines.append(f"{BOLD}{GOLD}▶ LAST ROUTED KEY TELEMETRY:{RESET}")
        right_lines.append(f"  • Key: {CYAN}{BOLD}'{rk}'{RESET} ➔ Target: {EMERALD}{BOLD}{p_node}{RESET}")
        right_lines.append(f"  • Vnode: {SLATE}{p_vnode}{RESET} │ Replicas: {YELLOW}{', '.join(pref_list)}{RESET}")
        right_lines.append(f"  • Chord Hops: {BOLD}{tot_hops}{RESET} │ Latency: {CYAN}{tot_lat:.3f} ms{RESET}")
        right_lines.append("")

        right_lines.append(f"{BOLD}{PURPLE}CHORD LOGARITHMIC SKIP HOPS:{RESET}")
        if not hops:
            right_lines.append(f"  {DIM}└─ [Local Initiator Hit - 0 Hops]{RESET}")
        else:
            for h in hops[:4]:
                h_num = h["hop"]
                fn = h["from_node_id"]
                tn = h["to_node_id"]
                f_idx = h["finger_index"]
                dist_pct = h["hop_distance_ratio"] * 100.0
                us = h["elapsed_us"]
                right_lines.append(
                    f"  {BOLD}#{h_num}{RESET} {CYAN}{fn}{RESET} ➔ {GREEN}{tn}{RESET} "
                    f"f[{f_idx:>2}] {AMBER}{dist_pct:>4.1f}% arc{RESET} ({us:>5.1f} μs)"
                )
            if len(hops) > 4:
                right_lines.append(f"  {DIM}... +{len(hops) - 4} intermediate hops{RESET}")
    else:
        right_lines.append(f"{BOLD}{GOLD}▶ ROUTING TELEMETRY:{RESET} {SLATE}[Awaiting Key Probe]{RESET}")
        right_lines.append(f"  Press {BOLD}[SPACE]{RESET} to route random synthetic keys")
        right_lines.append("")

    right_lines.append("")
    # Real-Time Telemetry Sparklines
    lat_spark = generate_sparkline(rolling_latencies, width=24, color=CYAN)
    hop_spark = generate_sparkline([float(x) for x in rolling_hops], width=24, color=GOLD)

    right_lines.append(f"{BOLD}{AZURE}REAL-TIME TELEMETRY SPARKLINES:{RESET}")
    cur_lat = rolling_latencies[-1] if rolling_latencies else 0.0
    cur_hop = rolling_hops[-1] if rolling_hops else 0
    right_lines.append(f"  • Latency (ms):  [{lat_spark}] {CYAN}{cur_lat:.3f} ms{RESET}")
    right_lines.append(f"  • Hop Count:     [{hop_spark}] {GOLD}{cur_hop} hops{RESET}")
    right_lines.append("")

    # Node Distribution Entropy Bar
    right_lines.append(f"{BOLD}{EMERALD}NODE BALANCING & VNODE DISTRIBUTION:{RESET}")
    nodes_info = ring_info.get("nodes", {})
    for idx, (nid, ndata) in enumerate(sorted(nodes_info.items())[:5]):
        tok_cnt = ndata["token_count"]
        pct = (tok_cnt / vnodes_tot * 100.0) if vnodes_tot > 0 else 0.0
        bar_len = int(pct / 5)
        bar = "█" * bar_len + "░" * max(0, 10 - bar_len)
        clr = NODE_COLORS[idx % len(NODE_COLORS)]
        right_lines.append(f"  {clr}{nid:<8}{RESET} [{bar}] {tok_cnt:>3} vnodes ({pct:>4.1f}%)")
    if len(nodes_info) > 5:
        right_lines.append(f"  {DIM}... +{len(nodes_info) - 5} additional nodes{RESET}")

    # Merge left radar and right telemetry side-by-side
    max_h = max(len(radar_lines), len(right_lines))
    for i in range(max_h):
        left_str = radar_lines[i] if i < len(radar_lines) else (" " * 52)
        right_str = right_lines[i] if i < len(right_lines) else ""
        lines.append(f"{left_str}  {right_str}")

    # 4. Bottom Controls & Command Strip
    lines.append(f"{DARK_SLATE}{'─' * line_w}{RESET}")
    ctrl_str = (
        f" {BOLD}[SPACE]{RESET} Route Key  "
        f"│ {BOLD}[+] / [A]{RESET} Add Node  "
        f"│ {BOLD}[-] / [R]{RESET} Remove Node  "
        f"│ {BOLD}[B]{RESET} Run Benchmark  "
        f"│ {BOLD}[Q]{RESET} Exit HUD"
    )
    fps_indicator = f"{DIM}Target: 15.0 FPS{RESET}"
    lines.append(f"{ctrl_str}  {pad_ansi(fps_indicator, line_w - len(strip_ansi(ctrl_str)) - 4, 'right')}")

    return "\n".join(lines)


def run_terminal_radar(
    cluster: Optional[TelemetryCluster] = None,
    target_fps: float = 15.0,
    max_ticks: Optional[int] = None,
) -> None:
    """Runs the 15 FPS ANSI/Braille Terminal Radar HUD loop."""
    if cluster is None:
        cluster = TelemetryCluster(node_count=8, vnodes_per_node=32, m_bits=32)

    sample_probes = [
        "quantum_state_vector",
        "dht_routing_table",
        "crypto_key_store",
        "cache_chunk_4096",
        "hyper_ledger_block",
        "satellite_telemetry",
        "vector_embedding_norm",
        "distributed_lock_tx",
        "neural_weight_shard",
        "matrix_factor_core",
    ]

    rolling_latencies: List[float] = [0.12, 0.15, 0.11, 0.14, 0.10]
    rolling_hops: List[int] = [3, 2, 4, 3, 2]
    last_route: Optional[Dict[str, Any]] = None

    # Initial route
    last_route = cluster.route(sample_probes[0])
    rolling_latencies.append(last_route["chord_trajectory"]["total_latency_ms"])
    rolling_hops.append(last_route["chord_trajectory"]["total_hops"])

    tick_count = 0
    frame_interval = 1.0 / target_fps
    auto_route_interval = 2.0  # seconds between auto-injected probes
    last_auto_route = time.perf_counter()

    # Clear terminal screen
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

    try:
        while True:
            t0 = time.perf_counter()

            # Auto inject key routes periodically to keep display dynamic
            if time.perf_counter() - last_auto_route >= auto_route_interval:
                probe_key = random.choice(sample_probes) + f"_{random.randint(100, 999)}"
                last_route = cluster.route(probe_key)
                traj = last_route["chord_trajectory"]
                rolling_latencies.append(traj["total_latency_ms"])
                rolling_hops.append(traj["total_hops"])
                if len(rolling_latencies) > 30:
                    rolling_latencies.pop(0)
                    rolling_hops.pop(0)
                last_auto_route = time.perf_counter()

            # Check for non-blocking keyboard input if in a real interactive terminal
            if sys.stdin.isatty():
                r, _, _ = select.select([sys.stdin], [], [], 0.0)
                if r:
                    ch = sys.stdin.read(1)
                    if ch in ("q", "Q", "\x03"):
                        break
                    elif ch == " ":
                        probe_key = random.choice(sample_probes) + f"_{random.randint(1000, 9999)}"
                        last_route = cluster.route(probe_key)
                        traj = last_route["chord_trajectory"]
                        rolling_latencies.append(traj["total_latency_ms"])
                        rolling_hops.append(traj["total_hops"])
                    elif ch in ("+", "a", "A"):
                        new_nid = f"node-{len(cluster.ring._nodes):02d}"
                        cluster.add_node(new_nid)
                    elif ch in ("-", "r", "R"):
                        if len(cluster.ring._nodes) > 3:
                            rem_nid = sorted(cluster.ring._nodes.keys())[-1]
                            cluster.remove_node(rem_nid)

            # Assemble & render frame
            frame = format_radar_dashboard(
                cluster,
                last_route,
                rolling_latencies,
                rolling_hops,
                tick_count=tick_count,
            )

            # Home cursor & write frame
            sys.stdout.write("\033[H" + frame + "\n")
            sys.stdout.flush()

            tick_count += 1
            if max_ticks and tick_count >= max_ticks:
                break

            elapsed = time.perf_counter() - t0
            sleep_time = max(0.001, frame_interval - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\n")  # ensure cursor visible
        sys.stdout.flush()
