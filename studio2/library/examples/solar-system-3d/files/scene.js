import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// Figures: NASA Planetary Fact Sheet (s1) and Sun Fact Sheet (s2).
export const BODIES = [
  { id: "sun", name: "Sun", color: 0xffb627, diameter: 1391400, distance: null, period: null, day: null, src: "s2",
    note: "Diameter = 2 × volumetric mean radius (695,700 km). Rotation period 609.12 h at 16° latitude (the Sun spins faster at the equator)." },
  { id: "mercury", name: "Mercury", color: 0xa79d94, diameter: 4879, distance: 57.9, period: 88.0, day: 4222.6, src: "s1", phase: 0.6 },
  { id: "venus", name: "Venus", color: 0xe2b873, diameter: 12104, distance: 108.2, period: 224.7, day: 2802.0, src: "s1", phase: 2.4,
    note: "Rotates backwards (retrograde): rotation period −5832.5 h." },
  { id: "earth", name: "Earth", color: 0x3f7fd6, diameter: 12756, distance: 149.6, period: 365.2, day: 24.0, src: "s1", phase: 4.1 },
  { id: "moon", name: "Moon", color: 0xc9c9c9, diameter: 3475, distance: 0.384, period: 27.3, day: 708.7, src: "s1", phase: 1.0,
    note: "Distance is from Earth, not the Sun. Orbit is shown much wider than true scale." },
  { id: "mars", name: "Mars", color: 0xc1593a, diameter: 6792, distance: 228.0, period: 687.0, day: 24.7, src: "s1", phase: 5.3 },
];

const K = 10 / 57.9;                       // scene units per million km (Sun–planet distances are true to scale)
const planetR = d => 1.15 * Math.pow(d / 12756, 0.7); // exaggerated radii
const SUN_R = 4;
const MOON_ORBIT = 2.3;                    // scene units around Earth (exaggerated)

export function createScene(container, opts = {}) {
  const interactive = opts.interactive !== false;
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: !!opts.card });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  container.appendChild(renderer.domElement);
  renderer.domElement.setAttribute("role", "img");
  renderer.domElement.setAttribute("aria-label", "3D model of the Sun, Mercury, Venus, Earth with the Moon, and Mars on their orbits");
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x03050c);
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 2000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.minDistance = 6; controls.maxDistance = 220;
  controls.enabled = interactive;

  scene.add(new THREE.AmbientLight(0x6070a0, 0.35));
  const sunLight = new THREE.PointLight(0xfff1d6, 3.2, 0, 0); scene.add(sunLight);

  // stars
  const sp = []; for (let i = 0; i < 1500; i++) {
    const v = new THREE.Vector3().randomDirection().multiplyScalar(600 + Math.random() * 300); sp.push(v.x, v.y, v.z);
  }
  const sg = new THREE.BufferGeometry(); sg.setAttribute("position", new THREE.Float32BufferAttribute(sp, 3));
  scene.add(new THREE.Points(sg, new THREE.PointsMaterial({ color: 0xffffff, size: 1.6, sizeAttenuation: false })));

  const meshes = {}; const pivots = {};
  const sun = new THREE.Mesh(new THREE.SphereGeometry(SUN_R, 48, 32), new THREE.MeshBasicMaterial({ color: 0xffc23d }));
  sun.userData.id = "sun"; scene.add(sun); meshes.sun = sun;
  // glow
  const c = document.createElement("canvas"); c.width = c.height = 128; const g = c.getContext("2d");
  const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  gr.addColorStop(0, "rgba(255,200,80,0.9)"); gr.addColorStop(0.35, "rgba(255,160,40,0.3)"); gr.addColorStop(1, "rgba(255,140,0,0)");
  g.fillStyle = gr; g.fillRect(0, 0, 128, 128);
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), blending: THREE.AdditiveBlending, depthWrite: false }));
  glow.scale.setScalar(SUN_R * 5); scene.add(glow);

  const mk = b => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(planetR(b.diameter), 40, 28),
      new THREE.MeshStandardMaterial({ color: b.color, roughness: 0.85, metalness: 0 }));
    m.userData.id = b.id; meshes[b.id] = m; return m;
  };
  const ring = (r, parent) => {
    const pts = []; for (let i = 0; i <= 256; i++) { const a = i / 256 * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r)); }
    const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x8899bb, transparent: true, opacity: 0.35 }));
    parent.add(l);
  };
  for (const b of BODIES.filter(b => b.id !== "sun" && b.id !== "moon")) {
    const m = mk(b); b.r = b.distance * K; ring(b.r, scene); scene.add(m); pivots[b.id] = m;
  }
  const moon = mk(BODIES.find(b => b.id === "moon")); scene.add(moon);
  const earth = pivots.earth; ring; // moon orbit ring around earth
  const moonRing = new THREE.Group(); scene.add(moonRing); ring(MOON_ORBIT, moonRing);

  // Saturn-free: labels
  const labels = {};
  for (const b of BODIES) {
    const el = document.createElement("div"); el.className = "lbl"; el.textContent = b.name;
    if (!interactive && !opts.labels) el.style.display = "none";
    container.appendChild(el); labels[b.id] = el;
  }

  let t = 0; // simulated days
  const state = { speed: opts.speed ?? 20, paused: false, selected: null, labels: true };
  const axis = {};
  function place() {
    for (const b of BODIES) {
      if (b.id === "sun" || b.id === "moon") continue;
      const a = b.phase + 2 * Math.PI * t / b.period;
      pivots[b.id].position.set(Math.cos(a) * b.r, 0, -Math.sin(a) * b.r);
    }
    const mb = BODIES.find(b => b.id === "moon");
    const a = mb.phase + 2 * Math.PI * t / mb.period;
    moon.position.set(earth.position.x + Math.cos(a) * MOON_ORBIT, 0, earth.position.z - Math.sin(a) * MOON_ORBIT);
    moonRing.position.copy(earth.position);
  }
  place();

  // selection highlight
  const halo = new THREE.Mesh(new THREE.RingGeometry(1, 1.12, 64), new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide, transparent: true, opacity: 0.9 }));
  halo.visible = false; scene.add(halo);

  function size() {
    const w = container.clientWidth, h = container.clientHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  new ResizeObserver(size).observe(container); size();

  const home = opts.camera || { pos: [0, 48, 62], target: [0, 0, 0] };
  camera.position.set(...home.pos); controls.target.set(...home.target); controls.update();
  let follow = null; // body id the camera target tracks
  const listeners = { select: [] };
  function select(id) {
    state.selected = id; follow = id;
    const m = meshes[id];
    if (id) {
      const rr = id === "sun" ? SUN_R : planetR(BODIES.find(b => b.id === id).diameter);
      halo.scale.setScalar(rr * 1.35); halo.visible = true;
      const off = camera.position.clone().sub(controls.target);
      const want = Math.max(rr * 6, 8); if (off.length() > want * 3 || off.length() < want) off.setLength(want * 2);
      controls.target.copy(m.position); camera.position.copy(m.position).add(off);
    } else halo.visible = false;
    listeners.select.forEach(f => f(id));
  }
  function reset() { follow = null; state.selected = null; halo.visible = false; camera.position.set(...home.pos); controls.target.set(...home.target); listeners.select.forEach(f => f(null)); }
  function view(name) {
    follow = null;
    controls.target.set(0, 0, 0);
    if (name === "top") camera.position.set(0.01, 100, 0.01);
    else if (name === "side") camera.position.set(0, 4, 90);
    else camera.position.set(...home.pos);
  }

  const ray = new THREE.Raycaster(), pt = new THREE.Vector2();
  const targets = Object.values(meshes).concat([moon]);
  function pick(e) {
    const r = renderer.domElement.getBoundingClientRect();
    pt.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(pt, camera);
    const hit = ray.intersectObjects(targets, false)[0];
    return hit ? hit.object.userData.id : null;
  }
  let down = null;
  if (interactive) {
    renderer.domElement.addEventListener("pointerdown", e => { down = [e.clientX, e.clientY]; });
    renderer.domElement.addEventListener("pointerup", e => {
      if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 5) { const id = pick(e); if (id) select(id); }
    });
    renderer.domElement.addEventListener("pointermove", e => { renderer.domElement.style.cursor = pick(e) ? "pointer" : "grab"; });
  }

  const v = new THREE.Vector3();
  const clock = new THREE.Clock();
  function frame() {
    const dt = Math.min(clock.getDelta(), 0.1);
    if (!state.paused) t += dt * state.speed;
    place();
    const body = id => id === "moon" ? moon : meshes[id];
    if (follow) {
      const p = body(follow).position, prev = controls.target.clone();
      const d = p.clone().sub(prev); controls.target.copy(p); camera.position.add(d);
    }
    if (state.selected) { halo.position.copy(body(state.selected).position); halo.quaternion.copy(camera.quaternion); }
    controls.update();
    renderer.render(scene, camera);
    const w = container.clientWidth, h = container.clientHeight;
    for (const b of BODIES) {
      const el = labels[b.id]; if (!el) continue;
      const m = body(b.id);
      v.copy(m.position); v.y += (b.id === "sun" ? SUN_R : planetR(b.diameter)) + 0.6; v.project(camera);
      const vis = state.labels && v.z < 1 && (interactive || opts.labels);
      el.style.display = vis ? "block" : "none";
      el.style.transform = `translate(-50%,-100%) translate(${(v.x * 0.5 + 0.5) * w}px,${(-v.y * 0.5 + 0.5) * h}px)`;
      el.classList.toggle("on", state.selected === b.id);
    }
    state.days = t;
    if (!opts.card) requestAnimationFrame(frame);
  }
  if (opts.card) { t = opts.t ?? 0; place(); frame(); frame(); } else frame();

  return { state, select, reset, view, on: (k, f) => listeners[k].push(f), getDays: () => t, bodyPos: id => body_(id) };
  function body_(id) { return id === "moon" ? moon.position : meshes[id].position; }
}
