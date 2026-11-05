/**
 * RingForge 3D WebGL Hologram & Trajectory Animation Engine.
 *
 * Implements interactive Three.js toroidal space visualization of Consistent Hash Rings,
 * virtual nodes (vnodes), Chord exponential skip finger tables, and real-time laser beam routing.
 */

// Global State
let scene, camera, renderer, controls;
let ringGroup, nodesGroup, vnodesGroup, fingersGroup, lasersGroup, dustGroup;
let currentTopology = null;
let currentTrajectory = null;
let isStreaming = false;
let streamTimer = null;
let raycaster, mouse;
let hoveredNode = null;
let nodeMeshMap = new Map(); // node_id -> Mesh/Group

// Node Color Palette
const PALETTE = [
  0x00f3ff, // Cyan
  0x00ff88, // Emerald
  0xffd700, // Gold
  0xd946ef, // Violet
  0x0088ff, // Azure
  0xffaa00, // Amber
  0xa855f7, // Purple
  0xff0055, // Crimson
  0x38bdf8, // Sky Blue
  0x4ade80, // Lime
];

const RING_RADIUS = 36;
const TWO_PI = Math.PI * 2;

// Utility: XSS Escaping
function escapeHTML(str) {
  if (typeof str !== 'string') return String(str);
  return str.replace(/[&<>'"]/g, tag => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;'
  }[tag] || tag));
}

// Map chord_id (0 .. 2^m - 1) to angle on circle (0 .. 2pi)
function idToAngle(id, ringSize) {
  return (id / ringSize) * TWO_PI - Math.PI / 2;
}

// Convert polar (radius, angle) to 3D coords
function polarTo3D(radius, angle, y = 0) {
  return new THREE.Vector3(
    radius * Math.cos(angle),
    y,
    radius * Math.sin(angle)
  );
}

// Initialize Three.js Scene
function initScene() {
  const container = document.getElementById('webgl-container');
  const w = window.innerWidth;
  const h = window.innerHeight;

  // Scene
  scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x050814, 0.008);

  // Camera
  camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 1000);
  camera.position.set(0, 52, 64);

  // Renderer
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setSize(w, h);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.2;
  container.appendChild(renderer.domElement);

  // Controls
  if (typeof THREE.OrbitControls !== 'undefined') {
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.maxDistance = 180;
    controls.minDistance = 10;
    controls.target.set(0, 0, 0);
  }

  // Lighting
  const ambient = new THREE.AmbientLight(0xffffff, 0.45);
  scene.add(ambient);

  const dirLight1 = new THREE.DirectionalLight(0x00f3ff, 0.8);
  dirLight1.position.set(40, 60, 40);
  scene.add(dirLight1);

  const dirLight2 = new THREE.DirectionalLight(0xffd700, 0.6);
  dirLight2.position.set(-40, 40, -40);
  scene.add(dirLight2);

  // Groups
  ringGroup = new THREE.Group();
  nodesGroup = new THREE.Group();
  vnodesGroup = new THREE.Group();
  fingersGroup = new THREE.Group();
  lasersGroup = new THREE.Group();
  dustGroup = new THREE.Group();

  scene.add(ringGroup);
  scene.add(nodesGroup);
  scene.add(vnodesGroup);
  scene.add(fingersGroup);
  scene.add(lasersGroup);
  scene.add(dustGroup);

  // Spatial Plane Grid
  const grid = new THREE.GridHelper(120, 40, 0x00f3ff, 0x1e293b);
  grid.position.y = -2;
  grid.name = 'spatial-grid';
  scene.add(grid);

  // Build Celestial Hash Ring Orbit & Star Dust
  buildOrbitRing();
  buildStarDust();

  // Raycaster & Mouse
  raycaster = new THREE.Raycaster();
  mouse = new THREE.Vector2();

  window.addEventListener('resize', onWindowResize);
  window.addEventListener('mousemove', onMouseMove);

  animate();
}

// Build the 3D Holographic Orbit Ring
function buildOrbitRing() {
  // Main glowing torus ring
  const torusGeom = new THREE.TorusGeometry(RING_RADIUS, 0.35, 16, 120);
  const torusMat = new THREE.MeshBasicMaterial({
    color: 0x00f3ff,
    transparent: true,
    opacity: 0.7,
    wireframe: true,
  });
  const torusMesh = new THREE.Mesh(torusGeom, torusMat);
  torusMesh.rotation.x = Math.PI / 2;
  ringGroup.add(torusMesh);

  // Outer faint boundary ring
  const outerGeom = new THREE.TorusGeometry(RING_RADIUS + 0.8, 0.05, 8, 120);
  const outerMat = new THREE.MeshBasicMaterial({
    color: 0x38bdf8,
    transparent: true,
    opacity: 0.3,
  });
  const outerMesh = new THREE.Mesh(outerGeom, outerMat);
  outerMesh.rotation.x = Math.PI / 2;
  ringGroup.add(outerMesh);
}

// Distant Star Dust
function buildStarDust() {
  const particleCount = 1200;
  const geom = new THREE.BufferGeometry();
  const positions = new Float32Array(particleCount * 3);
  const colors = new Float32Array(particleCount * 3);

  for (let i = 0; i < particleCount; i++) {
    const r = 40 + Math.random() * 120;
    const theta = Math.random() * TWO_PI;
    const phi = (Math.random() - 0.5) * Math.PI;

    positions[i * 3] = r * Math.cos(theta) * Math.cos(phi);
    positions[i * 3 + 1] = r * Math.sin(phi);
    positions[i * 3 + 2] = r * Math.sin(theta) * Math.cos(phi);

    colors[i * 3] = 0.5 + Math.random() * 0.5;
    colors[i * 3 + 1] = 0.7 + Math.random() * 0.3;
    colors[i * 3 + 2] = 1.0;
  }

  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geom.setAttribute('color', new THREE.BufferAttribute(colors, 3));

  const mat = new THREE.PointsMaterial({
    size: 1.2,
    vertexColors: true,
    transparent: true,
    opacity: 0.6,
  });

  const dust = new THREE.Points(geom, mat);
  dustGroup.add(dust);
}

// Clear dynamically generated groups
function clearGroup(grp) {
  while (grp.children.length > 0) {
    const obj = grp.children[0];
    grp.remove(obj);
    if (obj.geometry) obj.geometry.dispose();
    if (obj.material) {
      if (Array.isArray(obj.material)) obj.material.forEach(m => m.dispose());
      else obj.material.dispose();
    }
  }
}

// Update 3D Scene with Topology
function updateSceneWithTopology(topo) {
  currentTopology = topo;
  clearGroup(nodesGroup);
  clearGroup(vnodesGroup);
  clearGroup(fingersGroup);
  nodeMeshMap.clear();

  const ringTopo = topo.ring;
  const chordTopo = topo.chord;
  const storeTopo = topo.store;
  const ringSize = ringTopo.ring_size;
  const nodes = chordTopo.nodes;
  const sortedNids = Object.keys(nodes).sort();

  // 1. Create Virtual Node (Vnode) Particles along Orbit Ring
  const tokens = ringTopo.tokens || [];
  const vnodeGeom = new THREE.SphereGeometry(0.28, 8, 8);

  tokens.forEach((tEntry) => {
    const tokVal = tEntry.token;
    const pNodeId = tEntry.physical_node_id;
    const nIdx = sortedNids.indexOf(pNodeId);
    const colorHex = PALETTE[(nIdx >= 0 ? nIdx : 0) % PALETTE.length];

    const angle = idToAngle(tokVal, ringSize);
    const pos = polarTo3D(RING_RADIUS, angle, (Math.sin(angle * 8) * 0.4));

    const vMat = new THREE.MeshBasicMaterial({
      color: colorHex,
      transparent: true,
      opacity: 0.75,
    });
    const vMesh = new THREE.Mesh(vnodeGeom, vMat);
    vMesh.position.copy(pos);
    vMesh.userData = { isVnode: true, tag: tEntry.vnode_tag, node_id: pNodeId };
    vnodesGroup.add(vMesh);
  });

  // 2. Create Physical Chord Stations
  sortedNids.forEach((nid, idx) => {
    const nInfo = nodes[nid];
    const cid = nInfo.chord_id;
    const angle = idToAngle(cid, ringSize);
    const pos = polarTo3D(RING_RADIUS, angle, 0);

    const stationGroup = new THREE.Group();
    stationGroup.position.copy(pos);

    const colorHex = PALETTE[idx % PALETTE.length];

    // Core Sphere
    const coreGeom = new THREE.SphereGeometry(1.4, 24, 24);
    const coreMat = new THREE.MeshStandardMaterial({
      color: colorHex,
      emissive: colorHex,
      emissiveIntensity: 0.6,
      roughness: 0.2,
      metalness: 0.8,
    });
    const coreMesh = new THREE.Mesh(coreGeom, coreMat);
    coreMesh.userData = { isNode: true, node_id: nid, chord_id: cid, angle_deg: nInfo.angle_deg, info: nInfo };
    stationGroup.add(coreMesh);

    // Orbiting Energy Ring
    const stationRingGeom = new THREE.TorusGeometry(2.3, 0.08, 12, 32);
    const stationRingMat = new THREE.MeshBasicMaterial({
      color: colorHex,
      transparent: true,
      opacity: 0.8,
      wireframe: true,
    });
    const stationRing = new THREE.Mesh(stationRingGeom, stationRingMat);
    stationRing.rotation.x = Math.PI / 3;
    stationRing.userData = { isStationRing: true };
    stationGroup.add(stationRing);

    // Light
    const pointLight = new THREE.PointLight(colorHex, 1.2, 18);
    stationGroup.add(pointLight);

    nodesGroup.add(stationGroup);
    nodeMeshMap.set(nid, stationGroup);

    // 3. Construct Chord Finger Arcs
    const fingers = nInfo.finger_table || [];
    const seenTargets = new Set();

    fingers.forEach((f) => {
      const tgtNid = f.node_id;
      if (!tgtNid || tgtNid === nid || seenTargets.has(tgtNid)) return;
      seenTargets.add(tgtNid);

      const tgtNode = nodes[tgtNid];
      if (!tgtNode) return;\n      const tgtAngle = idToAngle(tgtNode.chord_id, ringSize);
      const tgtPos = polarTo3D(RING_RADIUS, tgtAngle, 0);

      // Arc through center
      const midPoint = new THREE.Vector3()
        .addVectors(pos, tgtPos)
        .multiplyScalar(0.5)
        .multiplyScalar(0.35); // curve inwards towards ring center
      midPoint.y = Math.sin(Math.abs(angle - tgtAngle)) * 4.0;

      const curve = new THREE.QuadraticBezierCurve3(pos, midPoint, tgtPos);
      const points = curve.getPoints(24);
      const arcGeom = new THREE.BufferGeometry().setFromPoints(points);
      const arcMat = new THREE.LineBasicMaterial({
        color: colorHex,
        transparent: true,
        opacity: 0.18,
      });
      const arcLine = new THREE.Line(arcGeom, arcMat);
      fingersGroup.add(arcLine);
    });
  });

  // Update DOM Panels
  updateDOMPanels(topo);
}

// Update DOM Dashboard Elements
function updateDOMPanels(topo) {
  const ringTopo = topo.ring;
  const chordTopo = topo.chord;
  const storeTopo = topo.store;

  document.getElementById('val-nodes').innerText = chordTopo.node_count;
  document.getElementById('val-vnodes').innerText = ringTopo.total_vnodes;
  document.getElementById('val-repl').innerText = storeTopo.replication_factor;
  document.getElementById('val-keys').innerText = storeTopo.total_unique_keys || 0;
  document.getElementById('lbl-bit-length').innerText = `${topo.bit_length}-BIT SHA256`;

  const log2Bound = chordTopo.node_count > 0 ? Math.log2(chordTopo.node_count).toFixed(2) : '0';
  document.getElementById('lbl-log2-bound').innerText = `LOG2(N) BOUND: ${log2Bound}`;

  // Populate Nodes List
  const container = document.getElementById('nodes-container');
  container.innerHTML = '';

  const sortedNids = Object.keys(chordTopo.nodes).sort();
  sortedNids.forEach((nid, idx) => {
    const nInfo = chordTopo.nodes[nid];
    const clr = '#' + PALETTE[idx % PALETTE.length].toString(16).padStart(6, '0');

    const pill = document.createElement('div');
    pill.className = 'node-pill';
    pill.dataset.nodeId = nid;
    pill.innerHTML = `
      <div class="node-tag">
        <div class="node-color-dot" style="background: ${clr}; box-shadow: 0 0 6px ${clr};"></div>
        <span style="font-weight: 700;">${escapeHTML(nid)}</span>
      </div>
      <div style="color: var(--slate); font-size: 10px;">
        ID: <span style="color: var(--gold);">${nInfo.chord_id}</span>
      </div>
    `;

    pill.addEventListener('click', () => selectNode(nid));
    container.appendChild(pill);
  });

  // Calculate and populate entropy distribution
  updateEntropyBars(ringTopo, sortedNids);
}

// Update Entropy Distribution Bar Chart
function updateEntropyBars(ringTopo, sortedNids) {
  const container = document.getElementById('entropy-bars');
  container.innerHTML = '';

  const nodesInfo = ringTopo.nodes || {};
  const totalVnodes = ringTopo.total_vnodes || 1;

  sortedNids.forEach((nid, idx) => {
    const ndata = nodesInfo[nid] || { token_count: 0 };
    const pct = ((ndata.token_count / totalVnodes) * 100).toFixed(1);
    const clr = '#' + PALETTE[idx % PALETTE.length].toString(16).padStart(6, '0');

    const row = document.createElement('div');
    row.style.fontSize = '10px';
    row.innerHTML = `
      <div style="display: flex; justify-content: space-between; margin-bottom: 2px;">
        <span style="color: #fff;">${escapeHTML(nid)}</span>
        <span style="color: var(--slate);">${ndata.token_count} vnodes (${pct}%)</span>
      </div>
      <div style="background: rgba(255,255,255,0.06); height: 5px; border-radius: 3px; overflow: hidden;">
        <div style="width: ${pct}%; height: 100%; background: ${clr}; border-radius: 3px;"></div>
      </div>
    `;
    container.appendChild(row);
  });
}

// Select a node to view its full Finger Table
function selectNode(nid) {
  document.querySelectorAll('.node-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.nodeId === nid);
  });

  const card = document.getElementById('finger-card');
  const tbody = document.getElementById('finger-table-body');
  document.getElementById('finger-card-title').innerText = `FINGER TABLE (${nid})`;

  const chordNode = currentTopology?.chord?.nodes[nid];
  if (!chordNode) return;

  card.style.display = 'block';
  tbody.innerHTML = '';

  const fingers = chordNode.finger_table || [];
  fingers.forEach((f) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="color: var(--gold);">f[${f.index}]</td>
      <td>${f.start_deg.toFixed(1)}°</td>
      <td style="color: var(--cyan); font-weight: 600;">${escapeHTML(f.node_id || '-')}</td>
    `;
    tbody.appendChild(tr);
  });

  // Focus camera on node station
  const stationMesh = nodeMeshMap.get(nid);
  if (stationMesh && controls) {
    const targetPos = stationMesh.position.clone();
    controls.target.lerp(targetPos, 0.4);
  }
}

// Trigger Key Routing and Animate Chord Skip Laser Beams
async function routeKey(key) {
  if (!key) return;

  try {
    const res = await fetch(`/api/lookup?key=${encodeURIComponent(key)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    currentTrajectory = data;

    renderRouteTrajectory(data);
    animateLaserTrajectory(data);
  } catch (err) {
    console.error('Failed to route key:', err);
  }
}

// Render Trajectory Details in UI Panel
function renderRouteTrajectory(data) {
  const traj = data.chord_trajectory;
  const hops = traj.hops || [];

  document.getElementById('val-hops').innerText = traj.total_hops;
  document.getElementById('val-target-node').innerText = data.consistent_hash_target;
  document.getElementById('val-key-hash').innerText = traj.key_hash;
  document.getElementById('route-latency').innerText = `${traj.total_latency_ms.toFixed(3)} ms`;

  const prefs = data.replication_preference_list || [];
  document.getElementById('val-replicas').innerText = prefs.join(', ');

  const container = document.getElementById('hop-timeline-container');
  container.innerHTML = '';

  if (hops.length === 0) {
    container.innerHTML = `
      <div class="hop-step">
        <div class="hop-num">#0</div>
        <div class="hop-nodes">
          <span style="color: var(--emerald); font-weight: 700;">${escapeHTML(traj.initiator_node_id)}</span>
          <span style="color: var(--slate);">(Local Initiator Hit)</span>
        </div>
        <div class="hop-meta">0.0 μs</div>
      </div>
    `;
    return;
  }

  hops.forEach((h) => {
    const step = document.createElement('div');
    step.className = 'hop-step';
    step.innerHTML = `
      <div class="hop-num">#${h.hop}</div>
      <div class="hop-nodes">
        <span style="color: var(--cyan);">${escapeHTML(h.from_node_id)}</span>
        <span style="color: var(--gold);">➔</span>
        <span style="color: var(--emerald); font-weight: 700;">${escapeHTML(h.to_node_id)}</span>
      </div>
      <div class="hop-meta">
        f[${h.finger_index}] • ${(h.hop_distance_ratio * 100).toFixed(1)}% arc<br>
        <span style="color: var(--cyan);">${h.elapsed_us.toFixed(1)} μs</span>
      </div>
    `;
    container.appendChild(step);
  });
}

// Animate Laser Beams Hopping from Node to Node
function animateLaserTrajectory(data) {
  clearGroup(lasersGroup);

  const ringSize = currentTopology.ring.ring_size;
  const traj = data.chord_trajectory;
  const hops = traj.hops || [];

  // 1. Plot Golden Target Reticle for Key Hash
  const keyAngle = idToAngle(traj.key_hash, ringSize);
  const keyPos = polarTo3D(RING_RADIUS, keyAngle, 0);

  const targetRingGeom = new THREE.RingGeometry(1.8, 2.2, 32);
  const targetRingMat = new THREE.MeshBasicMaterial({
    color: 0xffd700,
    side: THREE.DoubleSide,
    transparent: true,
    opacity: 0.9,
  });
  const targetRing = new THREE.Mesh(targetRingGeom, targetRingMat);
  targetRing.rotation.x = Math.PI / 2;
  targetRing.position.copy(keyPos);
  targetRing.position.y += 0.5;
  lasersGroup.add(targetRing);

  // 2. Sequential Laser Hop Rays
  if (hops.length === 0) return;

  hops.forEach((h, hIdx) => {
    const fromAngle = idToAngle(h.from_chord_id, ringSize);
    const toAngle = idToAngle(h.to_chord_id, ringSize);

    const fromPos = polarTo3D(RING_RADIUS, fromAngle, 0);
    const toPos = polarTo3D(RING_RADIUS, toAngle, 0);

    // Elevated chord arc
    const midPoint = new THREE.Vector3()
      .addVectors(fromPos, toPos)
      .multiplyScalar(0.5)
      .multiplyScalar(0.4);
    midPoint.y = 5.0 + hIdx * 2.0;

    const curve = new THREE.QuadraticBezierCurve3(fromPos, midPoint, toPos);
    const points = curve.getPoints(36);
    const lineGeom = new THREE.BufferGeometry().setFromPoints(points);

    // Glowing Laser Arc Material
    const laserMat = new THREE.LineBasicMaterial({
      color: 0xffd700,
      linewidth: 3,
      transparent: true,
      opacity: 0.9,
    });
    const laserLine = new THREE.Line(lineGeom, laserMat);
    lasersGroup.add(laserLine);
  });
}

// Fetch Initial Topology from Server
async function fetchTopology() {
  try {
    const res = await fetch('/api/topology');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    updateSceneWithTopology(data);
  } catch (err) {
    console.warn('Initial topology fetch failed (offline mode):', err);
    // Offline Mock Topology Fallback
    buildMockTopology();
  }
}

// Offline Mock Fallback
function buildMockTopology() {
  const mockNodes = {};
  const mockTokens = [];
  const n = 8;
  const ringSize = 4294967296;

  for (let i = 0; i < n; i++) {
    const nid = `node-${String(i).padStart(2, '0')}`;
    const cid = Math.floor((i / n) * ringSize);
    mockNodes[nid] = {
      node_id: nid,
      chord_id: cid,
      angle_deg: (i / n) * 360,
      finger_table: [],
    };
    for (let v = 0; v < 32; v++) {
      mockTokens.push({
        token: Math.floor(Math.random() * ringSize),
        physical_node_id: nid,
        vnode_tag: `${nid}#vn${v}`,
      });
    }
  }

  const mock = {
    bit_length: 32,
    ring: { ring_size: ringSize, total_vnodes: mockTokens.length, tokens: mockTokens, nodes: {} },
    chord: { node_count: n, nodes: mockNodes },
    store: { replication_factor: 3, total_unique_keys: 8 },
  };
  updateSceneWithTopology(mock);
}

// Event Listeners & Buttons
function setupEventListeners() {
  const btnProbe = document.getElementById('btn-probe');
  const btnStream = document.getElementById('btn-stream');
  const btnResetCam = document.getElementById('btn-reset-cam');
  const btnRoute = document.getElementById('btn-route');
  const inputKey = document.getElementById('input-key');

  const sampleProbes = [
    'quantum_state_vector',
    'distributed_hash_shard',
    'satellite_telemetry_stream',
    'neural_embedding_weights',
    'crypto_signature_key',
    'hyperledger_block_4096',
    'cache_session_vnode',
  ];

  btnProbe.addEventListener('click', () => {
    const randomKey = sampleProbes[Math.floor(Math.random() * sampleProbes.length)] + '_' + Math.floor(Math.random() * 9999);
    inputKey.value = randomKey;
    routeKey(randomKey);
  });

  btnRoute.addEventListener('click', () => {
    routeKey(inputKey.value.trim());
  });

  inputKey.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') routeKey(inputKey.value.trim());
  });

  btnStream.addEventListener('click', () => {
    isStreaming = !isStreaming;
    btnStream.classList.toggle('btn-primary', isStreaming);
    btnStream.innerHTML = isStreaming
      ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> Pause Stream`
      : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Auto Stream`;

    if (isStreaming) {
      streamTimer = setInterval(() => {
        const randKey = sampleProbes[Math.floor(Math.random() * sampleProbes.length)] + '_' + Math.floor(Math.random() * 9999);
        inputKey.value = randKey;
        routeKey(randKey);
      }, 2400);
    } else {
      clearInterval(streamTimer);
    }
  });

  btnResetCam.addEventListener('click', () => {
    camera.position.set(0, 52, 64);
    if (controls) controls.target.set(0, 0, 0);
  });

  // Churn Controls
  document.getElementById('btn-add-node').addEventListener('click', async () => {
    const count = Object.keys(currentTopology?.chord?.nodes || {}).length;
    const newNid = `node-${String(count).padStart(2, '0')}`;
    try {
      const res = await fetch('/api/nodes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'add', node_id: newNid, vnodes: 32 }),
      });
      if (res.ok) {
        document.getElementById('churn-status').innerText = `Node ${newNid} successfully joined cluster.`;
        fetchTopology();
      }
    } catch (err) {\n      console.error('Failed to add node:', err);\n    }\n  });\n\n  document.getElementById('btn-rem-node').addEventListener('click', async () => {\n    const nids = Object.keys(currentTopology?.chord?.nodes || {}).sort();\n    if (nids.length <= 3) {\n      document.getElementById('churn-status').innerText = 'Minimum cluster size reached (3 nodes).';\n      return;\n    }\n    const remNid = nids[nids.length - 1];\n    try {\n      const res = await fetch('/api/nodes', {\n        method: 'POST',\n        headers: { 'Content-Type': 'application/json' },\n        body: JSON.stringify({ action: 'remove', node_id: remNid }),\n      });\n      if (res.ok) {\n        document.getElementById('churn-status').innerText = `Node ${remNid} departed. Keys dynamically migrated.`;\n        fetchTopology();\n      }\n    } catch (err) {\n      console.error('Failed to remove node:', err);\n    }\n  });\n\n  // Toggles\n  document.getElementById('toggle-vnodes').addEventListener('change', (e) => {\n    vnodesGroup.visible = e.target.checked;\n  });\n  document.getElementById('toggle-fingers').addEventListener('change', (e) => {\n    fingersGroup.visible = e.target.checked;\n  });\n  document.getElementById('toggle-grid').addEventListener('change', (e) => {\n    const g = scene.getObjectByName('spatial-grid');\n    if (g) g.visible = e.target.checked;\n  });\n}\n\nfunction onWindowResize() {\n  const w = window.innerWidth;\n  const h = window.innerHeight;\n  camera.aspect = w / h;\n  camera.updateProjectionMatrix();\n  renderer.setSize(w, h);\n}\n\nfunction onMouseMove(event) {\n  mouse.x = (event.clientX / window.innerWidth) * 2 - 1;\n  mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;\n\n  raycaster.setFromCamera(mouse, camera);\n  const intersects = raycaster.intersectObjects(nodesGroup.children, true);\n\n  const tooltip = document.getElementById('tooltip');\n  if (intersects.length > 0) {\n    const hit = intersects[0].object;\n    if (hit.userData && hit.userData.isNode) {\n      const u = hit.userData;\n      tooltip.style.display = 'block';\n      tooltip.style.left = `${event.clientX}px`;\n      tooltip.style.top = `${event.clientY}px`;\n      tooltip.innerHTML = `\n        <div class=\"tooltip-title\">${escapeHTML(u.node_id)}</div>\n        <div class=\"tooltip-row\">Chord ID: <span>${u.chord_id}</span></div>\n        <div class=\"tooltip-row\">Angle: <span>${u.angle_deg.toFixed(1)}°</span></div>\n        <div class=\"tooltip-row\">Successor: <span>${escapeHTML(u.info.successor_id || '-')}</span></div>\n      `;\n      return;\n    }\n  }\n  tooltip.style.display = 'none';\n}\n\n// Animation Loop\nfunction animate() {\n  requestAnimationFrame(animate);\n\n  const time = performance.now() * 0.001;\n\n  // Gentle autorotation if checked\n  const autoRotate = document.getElementById('toggle-autorotate')?.checked;\n  if (autoRotate && ringGroup) {\n    ringGroup.rotation.y = time * 0.05;\n    nodesGroup.rotation.y = time * 0.05;\n    vnodesGroup.rotation.y = time * 0.05;\n    fingersGroup.rotation.y = time * 0.05;\n    lasersGroup.rotation.y = time * 0.05;\n  }\n\n  // Station rings rotation animation\n  nodesGroup.children.forEach((stn) => {\n    stn.children.forEach((child) => {\n      if (child.userData?.isStationRing) {\n        child.rotation.z += 0.015;\n      }\n    });\n  });\n\n  // Slow star dust drift\n  if (dustGroup) dustGroup.rotation.y = time * 0.01;\n\n  if (controls) controls.update();\n  renderer.render(scene, camera);\n}\n\n// Bootstrap on DOM Ready\nwindow.addEventListener('DOMContentLoaded', () => {\n  initScene();\n  setupEventListeners();\n  fetchTopology();\n\n  // Initial demo probe\n  setTimeout(() => {\n    routeKey('quantum_state_vector');\n  }, 600);\n});\n