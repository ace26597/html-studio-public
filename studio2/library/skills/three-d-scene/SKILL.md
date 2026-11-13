---
name: three-d-scene
description: Build a 3D scene a person can turn, zoom and click - a product or object viewer, a building or site model, a molecule, a globe, a 3D chart of spatial data, an animated 3D hero. Use whenever the person asks for 3D, three.js, WebGL, "rotate it", a model viewer or a 3D animation.
---

# 3D scenes (three.js)

Use 3D when the subject has real depth: an object, a place, a structure, positions in space.
Quantities compare better in a flat chart; say so if asked for a "3D bar chart".

## Set-up that works here
```html
<script type="importmap">{ "imports": {
  "three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
  "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/" } }</script>
<script type="module">
  import * as THREE from "three";
  import { OrbitControls } from "three/addons/controls/OrbitControls.js";
  import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
</script>
```
Pin the exact version in both lines (0.170.0 is known to load here). One `<canvas>` in a
container with a set height (60 to 80vh, or the stage); never the whole page unless asked.

## The scene
- **Renderer:** `antialias: true`, `setPixelRatio(Math.min(devicePixelRatio, 2))`, size from the
  container with a `ResizeObserver` (update `camera.aspect` and `updateProjectionMatrix()`).
- **Light:** an environment (`scene.environment = pmrem.fromScene(new RoomEnvironment()).texture`)
  plus one directional key light. `MeshStandardMaterial` with sensible `roughness` and
  `metalness`. One soft shadow at most (`renderer.shadowMap`), or a blurred disc under the object.
- **Camera:** frame the subject from its bounding box (`Box3().setFromObject`), three-quarter
  view, 35 to 50° field of view. Set `controls.target` to the centre.
- **Controls:** `OrbitControls` with `enableDamping`, sensible `minDistance`/`maxDistance`, and
  `maxPolarAngle` so the camera cannot go under the floor. Drag turns, wheel or pinch zooms,
  right-drag or two fingers pans. Add buttons for the views that matter (Front, Top, Reset)
  and a one-line hint ("Drag to turn, scroll to zoom").
- **Draw on demand:** render when the controls change or something animates, not sixty times a
  second for a still scene (`controls.addEventListener("change", render)`); it saves the fan.
- **Picking:** `Raycaster` on pointer move and click; highlight what is under the pointer and
  show its facts in an HTML panel beside the canvas, not in a texture.
- **Labels:** HTML over the canvas (`CSS2DRenderer` from the add-ons, or absolutely placed
  elements positioned from `vector.project(camera)`), so they stay sharp and selectable.
- **Models:** build from primitives, extrusions and curves when you can. A `.glb` only when the
  person supplies it or it is in the project (`GLTFLoader`, served from the project, not
  hot-linked). Say what is a simplified stand-in.
- **Data:** positions and sizes come from the project's data with their sources, and the same
  values are in a table under the scene. Units and scale are stated.
- **Fallback:** if `WebGLRenderer` throws, show a picture of the scene (`render_image`) and the
  table. Give the canvas `role="img"` and an `aria-label` that says what it shows.
- **Cost:** under about 200,000 triangles and a few dozen draw calls; merge repeated things
  with `InstancedMesh`. Dispose of geometries and materials you replace.

## Check it
`look_at_page` draws WebGL, so look at the scene, then click each view button with `actions`
and look again. Look for a black or empty canvas (check the console list), the subject cut
off or tiny, labels on top of each other, harsh or flat lighting. For motion (an intro turn,
an exploded view) use `render_video` with `check: true` and a `duration`.

## In a video
On a stage (the `motion-video` skill) do not use `OrbitControls` or a render loop: move the
camera and the parts from `t` in `studioStage.onFrame(t => { …; renderer.render(scene, camera); })`,
and hold the first frame with `studioStage.ready(promise)` until models have loaded.
