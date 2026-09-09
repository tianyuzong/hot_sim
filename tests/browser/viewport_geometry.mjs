import assert from "node:assert/strict";
import * as THREE from "../../src/thermoflow/web/assets/vendor/three/three.module.min.js";
import * as viewportModule from "../../src/thermoflow/web/assets/viewport.mjs";
import { EngineeringViewport, fieldColor, surfaceGeometry } from "../../src/thermoflow/web/assets/viewport.mjs";

assert.equal(typeof viewportModule.previewTemperatureField, "function",
  "The viewport must expose the live temperature preview calculation");
const pointField = {
  vertices: [[0, 0, 0], [5, 0, 0], [10, 0, 0]],
  temperature_k: [400, 350, 300],
};
const oldPoint = {
  shape: "point", center_mm: { x: 0, y: 0, z: 0 }, radius_mm: 1, total_power_w: 1,
};
const newPoint = { ...oldPoint, center_mm: { x: 10, y: 0, z: 0 } };
const unchangedPoint = viewportModule.previewTemperatureField(pointField, oldPoint, oldPoint);
assert.deepEqual(unchangedPoint.scalars, pointField.temperature_k,
  "Opening the source editor without a change must retain the solved field exactly");
const movedPoint = viewportModule.previewTemperatureField(pointField, oldPoint, newPoint);
assert(movedPoint.scalars[2] > movedPoint.scalars[0],
  "Moving a point source must move the approximate hotspot");

const oldLine = {
  shape: "line", center_mm: { x: 0, y: 0, z: 0 }, end_mm: { x: 0, y: 10, z: 0 },
  radius_mm: 1, total_power_w: 1,
};
const newLine = {
  ...oldLine, center_mm: { x: 10, y: 0, z: 0 }, end_mm: { x: 10, y: 10, z: 0 },
};
const movedLine = viewportModule.previewTemperatureField({
  vertices: [[0, 5, 0], [10, 5, 0], [5, 20, 0]], temperature_k: [400, 300, 300],
}, oldLine, newLine);
assert(movedLine.scalars[1] > movedLine.scalars[0],
  "Moving a line source must move the approximate hot region along the full line");

const oldSurface = {
  shape: "surface", center_mm: { x: 0, y: 0, z: 0 }, surface_normal_axis: "x",
  surface_width_mm: 4, surface_height_mm: 4, surface_thickness_mm: 1, total_power_w: 1,
};
const newSurface = { ...oldSurface, center_mm: { x: 10, y: 0, z: 0 } };
const movedSurface = viewportModule.previewTemperatureField({
  vertices: [[0, 1, 1], [10, 1, 1], [5, 8, 8]], temperature_k: [400, 300, 300],
}, oldSurface, newSurface);
assert(movedSurface.scalars[1] > movedSurface.scalars[0],
  "Moving a surface source must move the approximate hot region across the patch");

const oldVolume = {
  shape: "volume", center_mm: { x: 0, y: 0, z: 0 }, volume_width_mm: 4,
  volume_height_mm: 4, volume_depth_mm: 4, total_power_w: 1,
};
const newVolume = { ...oldVolume, center_mm: { x: 10, y: 0, z: 0 } };
const movedVolume = viewportModule.previewTemperatureField({
  vertices: [[0, 1, 1], [10, 1, 1], [5, 8, 8]], temperature_k: [400, 300, 300],
}, oldVolume, newVolume);
assert(movedVolume.scalars[1] > movedVolume.scalars[0],
  "Moving a volume source must move the approximate hot region across the block");

const movedMultiple = viewportModule.previewTemperatureField(pointField, [oldPoint], [
  newPoint,
  { ...oldPoint, center_mm: { x: 5, y: 0, z: 0 } },
]);
assert(movedMultiple.scalars[2] > movedMultiple.scalars[0],
  "A multi-source preview must combine all active source influences");

const strongerPoint = viewportModule.previewTemperatureField(pointField, oldPoint,
  { ...oldPoint, total_power_w: 2 });
assert(strongerPoint.high > Math.max(...pointField.temperature_k),
  "Increasing source power must raise the approximate field scale");

const surface = {
  vertices: [[10, 20, 30], [12, 20, 30], [12, 22, 30], [10, 22, 30]],
  triangles: [[0, 1, 2], [0, 2, 3]], component_ids: ["a", "b"], cell_ids: [17, 24],
};
const geometry = surfaceGeometry(surface, [10, 20, 30], {
  scalars: [300, 320, 320, 300], low: 300, high: 320,
});
assert.equal(geometry.getAttribute("position").count, 6);
assert.deepEqual([...geometry.getAttribute("position").array].slice(0, 9), [0, 0, 0, 2, 0, 0, 2, 2, 0]);
assert.deepEqual(geometry.userData.cellIds, [17, 24]);
assert(geometry.boundingSphere.radius > 1);
const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
mesh.updateMatrixWorld();
const ray = new THREE.Raycaster(new THREE.Vector3(1.5, 0.5, 5), new THREE.Vector3(0, 0, -1));
const hit = ray.intersectObject(mesh)[0];
assert(hit, "The actual triangle geometry must be raycastable");
assert.equal(geometry.userData.cellIds[hit.faceIndex], 17);
const hidden = surfaceGeometry(surface, [10, 20, 30], { hidden: new Set(["a"]) });
assert.deepEqual(hidden.userData.cellIds, [24]);
const isolated = surfaceGeometry(surface, [10, 20, 30], { isolated: "a" });
assert.deepEqual(isolated.userData.cellIds, [17]);

function sourceViewport(source) {
  const viewport = Object.assign(Object.create(EngineeringViewport.prototype), {
    options: {
      source,
      workpiece: { geometry: { summary: { bbox: [-5, -5, -5, 5, 5, 5] } }, regions: [] },
      probe: null, boundaries: [], surfaceConditions: [], contacts: [], selectionMode: "components", mode: "model",
    },
    size: 10, origin: [0, 0, 0], markerGroup: new THREE.Group(),
  });
  viewport.addMarkers();
  viewport.markerGroup.updateMatrixWorld(true);
  return viewport;
}

function draggableHit(viewport, position) {
  const sourceRay = new THREE.Raycaster(new THREE.Vector3(...position), new THREE.Vector3(0, 0, -1));
  sourceRay.params.Line.threshold = 0.2;
  return sourceRay.intersectObjects(viewport.markerGroup.children, true)
    .find(hit => hit.object.userData.source);
}

const pointViewport = sourceViewport({
  shape: "point", placement: "embedded", center_mm: { x: 0, y: 0, z: 0 }, radius_mm: 1,
});
assert.equal(draggableHit(pointViewport, [0.3, 0, 5])?.object.userData.source, "center",
  "The point source must have a forgiving pick area around its visible marker");
const lineViewport = sourceViewport({
  shape: "line", placement: "embedded", center_mm: { x: 0, y: 0, z: 0 },
  end_mm: { x: 2, y: 0, z: 0 }, radius_mm: 1,
});
assert.equal(draggableHit(lineViewport, [1, 0, 5])?.object.userData.source, "center",
  "Dragging the visible line must move the whole line source");
const surfaceViewport = sourceViewport({
  shape: "surface", placement: "surface", center_mm: { x: 0, y: 0, z: 0 },
  surface_width_mm: 2, surface_height_mm: 2, surface_thickness_mm: 0.2, surface_normal_axis: "z",
});
assert.equal(draggableHit(surfaceViewport, [0.8, 0.8, 5])?.object.userData.source, "center",
  "Dragging anywhere on the visible patch must move the surface source");
const multipleViewport = sourceViewport(null);
multipleViewport.options.sources = [oldPoint, oldVolume];
multipleViewport.options.activeSourceIndex = 1;
multipleViewport.addMarkers();
assert(multipleViewport.markerGroup.children.some(object => object.userData.sourceIndex === 0));
assert(multipleViewport.markerGroup.children.some(object => object.userData.sourceIndex === 1));
assert(multipleViewport.markerGroup.children.some(object => object.geometry?.type === "BoxGeometry"),
  "A volume source must be rendered as a draggable block");

function boundaryLabels(boundaries) {
  const labels = [];
  const viewport = Object.assign(Object.create(EngineeringViewport.prototype), {
    options: {
      source: null,
      workpiece: { geometry: { summary: { bbox: [0, 0, 0, 10, 5, 2] } }, regions: [] },
      probe: null, boundaries, surfaceConditions: [], contacts: [], selectionMode: "components", mode: "model",
    },
    size: 10, origin: [5, 2.5, 1], markerGroup: new THREE.Group(),
    addBoundaryLabel(position, kinds) { labels.push(kinds); },
  });
  viewport.addMarkers();
  return labels;
}

assert.deepEqual(boundaryLabels([
  { kind: "fixed_temperature", selector: "face.xmin", temperature_k: 293.15 },
]), [], "Fixed-temperature boundaries must not create white T labels on the workpiece");
assert.deepEqual(boundaryLabels([
  { kind: "heat_flux", selector: "face.xmin", heat_flux_w_m2: 1000 },
]), [["heat_flux"]], "Hiding T labels must preserve other boundary-condition labels");

const draggedPositions = [];
const dragViewport = Object.assign(Object.create(EngineeringViewport.prototype), {
  drag: { handle: "center", plane: {} },
  options: {
    source: { shape: "point", placement: "surface", center_mm: { x: 1, y: 1, z: 1 } },
    workpiece: { geometry: { summary: { bbox: [0, 0, 0, 5, 5, 5] } } },
  },
  origin: [0, 0, 0], mesh: {}, markerGroup: {}, geometryGroup: {},
  raycaster: { ray: { intersectPlane: () => new THREE.Vector3(9, -2, 3) } },
  hits(event, group) {
    return group === this.geometryGroup ? [{ object: this.mesh, point: new THREE.Vector3(4, 3, 5) }] : [];
  },
  callbacks: { source: (handle, position) => draggedPositions.push({ handle, position }) },
});
dragViewport.pointerMove({ stopImmediatePropagation() {} });
assert.deepEqual(draggedPositions.at(-1), { handle: "center", position: [4, 3, 5] },
  "A surface source must follow the mesh raycast instead of leaving the workpiece");

dragViewport.options.source.placement = "embedded";
dragViewport.pointerMove({ stopImmediatePropagation() {} });
assert.deepEqual(draggedPositions.at(-1), { handle: "center", position: [5, 0, 3] },
  "An embedded source drag must be clamped to the workpiece bounds");

const dragClasses = new Set();
const planeViewport = Object.assign(Object.create(EngineeringViewport.prototype), {
  options: {
    selectionMode: "components",
    source: { shape: "point", placement: "embedded", center_mm: { x: 0, y: 0, z: 0 } },
  },
  origin: [0, 0, 0], markerGroup: {}, controls: { enabled: true },
  callbacks: { sourceSelect() {} },
  camera: { getWorldDirection: target => target.set(0, 0, -1) },
  canvas: {
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; },
    setPointerCapture() {},
    classList: { add: (...names) => names.forEach(name => dragClasses.add(name)) },
  },
  hits: () => [{ object: { userData: { source: "center" } }, point: new THREE.Vector3(0.3, 0, 0.25) }],
});
planeViewport.pointerDown({ button: 0, pointerId: 7, clientX: 10, clientY: 20, stopImmediatePropagation() {} });
assert(Math.abs(planeViewport.drag.plane.distanceToPoint(new THREE.Vector3(0, 0, 0))) < 1e-12,
  "Grabbing the edge of a large handle must retain the source center's view depth");
const selectedFaces = surfaceGeometry({ ...surface, cell_ids: [] }, [10, 20, 30], {
  hidden: new Set(["a"]), selectedFaces: new Set([1]),
});
assert.deepEqual(selectedFaces.userData.visibleTriangles, [1], "Filtering must preserve original STL triangle indices");
assert.deepEqual([...selectedFaces.getAttribute("color").array].slice(0, 3),
  new THREE.Color(0xf2bf45).toArray().map(Math.fround), "Selected faces must be highlighted on their actual triangles");
const selectionViewport = Object.assign(Object.create(EngineeringViewport.prototype), {
  options: { mode: "model", selectionMode: "faces" },
  surface: { ...surface, cell_ids: [] }, pointerStart: [10, 20],
  mesh: { geometry: selectedFaces }, geometryGroup: {}, markerGroup: {},
  callbacks: { face: index => assert.equal(index, 1), component: () => assert.fail("Face selection must not select a component") },
  hits() { return [{ object: this.mesh, faceIndex: 0 }]; },
});
selectionViewport.pointerUp({ button: 0, clientX: 10, clientY: 20 });
selectedFaces.dispose();
assert.notDeepEqual(fieldColor(300, 300, 320).toArray(), fieldColor(320, 300, 320).toArray());
assert(fieldColor(300, 300, 300).toArray().every(Number.isFinite));
assert.deepEqual(fieldColor(0, -1, 1, true).toArray(), new THREE.Color(0xf5f6f4).toArray());
geometry.dispose(); hidden.dispose(); isolated.dispose(); mesh.material.dispose();
let resolveSurface;
let requests = 0;
const viewport = Object.assign(Object.create(EngineeringViewport.prototype), {
  ticket: 0, geometryKey: "wpmm", geometryGroup: new THREE.Group(), markerGroup: new THREE.Group(),
  callbacks: { status() {} }, render() {}, rebuild() {},
  fetchSurface() {
    requests++;
    return new Promise(resolve => { resolveSurface = resolve; });
  },
});
const options = { workpiece: { workpiece_id: "wp", length_unit: "mm" }, mode: "model" };
const first = viewport.update(options);
const repeated = viewport.update(options);
assert.equal(requests, 1, "Repeated redraws must reuse the in-flight field request");
resolveSurface(surface);
assert.equal(await first, true);
assert.equal(await repeated, true, "Playback must wait for the actual field, including a repeated redraw");
console.log(`Three.js r${THREE.REVISION}: geometry, field colors, component filtering and real-cell raycasts passed`);
