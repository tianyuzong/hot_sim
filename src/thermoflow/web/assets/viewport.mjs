import * as THREE from "./vendor/three/three.module.min.js";
import { OrbitControls } from "./vendor/three/OrbitControls.js";

const PALETTE = [0x247b72, 0xd17a32, 0x5d76b8, 0xb35176, 0x6d8843];
const THERMAL = [0x2d5ea2, 0x2a9d8f, 0xeebe49, 0xca4330].map(value => new THREE.Color(value));

export function fieldColor(value, low, high, difference = false) {
  const fraction = high === low ? 0.5 : THREE.MathUtils.clamp((value - low) / (high - low), 0, 1);
  if (difference) {
    return new THREE.Color(0xf5f6f4).lerp(new THREE.Color(fraction < 0.5 ? 0x2d5ea2 : 0xbe3830), Math.abs(fraction * 2 - 1));
  }
  const at = fraction * (THERMAL.length - 1);
  const index = Math.min(THERMAL.length - 2, Math.floor(at));
  return THERMAL[index].clone().lerp(THERMAL[index + 1], at - index);
}

function sourceDistance(point, source) {
  const center = Object.values(source.center_mm);
  if (source.shape === "line" && source.end_mm) {
    const end = Object.values(source.end_mm);
    const direction = end.map((value, axis) => value - center[axis]);
    const squaredLength = direction.reduce((sum, value) => sum + value * value, 0);
    const fraction = squaredLength ? THREE.MathUtils.clamp(point.reduce((sum, value, axis) =>
      sum + (value - center[axis]) * direction[axis], 0) / squaredLength, 0, 1) : 0;
    return Math.hypot(...point.map((value, axis) => value - center[axis] - fraction * direction[axis]));
  }
  if (source.shape === "surface") {
    const normal = "xyz".indexOf(source.surface_normal_axis || "z");
    const tangent = [0, 1, 2].filter(axis => axis !== normal);
    const halfSizes = [0, 0, 0];
    halfSizes[normal] = (source.surface_thickness_mm || 0) / 2;
    halfSizes[tangent[0]] = (source.surface_width_mm || 0) / 2;
    halfSizes[tangent[1]] = (source.surface_height_mm || 0) / 2;
    return Math.hypot(...point.map((value, axis) =>
      Math.max(0, Math.abs(value - center[axis]) - halfSizes[axis])));
  }
  if (source.shape === "volume") {
    const halfSizes = [source.volume_width_mm, source.volume_height_mm, source.volume_depth_mm]
      .map(value => (value || 0) / 2);
    return Math.hypot(...point.map((value, axis) =>
      Math.max(0, Math.abs(value - center[axis]) - halfSizes[axis])));
  }
  return Math.hypot(...point.map((value, axis) => value - center[axis]));
}

function sourceSize(source) {
  if (source.shape === "line" && source.end_mm) {
    const center = Object.values(source.center_mm);
    const end = Object.values(source.end_mm);
    return Math.max(source.radius_mm || 0, Math.hypot(...end.map((value, axis) => value - center[axis])) / 5);
  }
  if (source.shape === "surface") {
    return Math.max(source.surface_thickness_mm || 0,
      Math.sqrt((source.surface_width_mm || 0) * (source.surface_height_mm || 0)) / 3);
  }
  if (source.shape === "volume") {
    return Math.cbrt(Math.max(
      (source.volume_width_mm || 0) * (source.volume_height_mm || 0) * (source.volume_depth_mm || 0),
      0,
    ));
  }
  return source.radius_mm || 0;
}

function sourceSignature(source) {
  if (!source) return "";
  if (Array.isArray(source)) return JSON.stringify(source.map(item => sourceSignature(item)));
  return JSON.stringify({
    shape: source.shape,
    center_mm: source.center_mm,
    end_mm: source.end_mm,
    radius_mm: source.radius_mm,
    surface_normal_axis: source.surface_normal_axis,
    surface_width_mm: source.surface_width_mm,
    surface_height_mm: source.surface_height_mm,
    surface_thickness_mm: source.surface_thickness_mm,
    volume_width_mm: source.volume_width_mm,
    volume_height_mm: source.volume_height_mm,
    volume_depth_mm: source.volume_depth_mm,
    total_power_w: source.total_power_w,
  });
}

export function previewTemperatureField(surface, solvedSource, previewSource) {
  const temperatures = [...(surface.temperature_k || [])];
  if (!temperatures.length || !previewSource || sourceSignature(solvedSource) === sourceSignature(previewSource)) {
    return { scalars: temperatures, low: Math.min(...temperatures), high: Math.max(...temperatures) };
  }
  const spans = [0, 1, 2].map(axis => {
    const values = surface.vertices.map(vertex => vertex[axis]);
    return Math.max(...values) - Math.min(...values);
  }).filter(value => value > 0);
  const domainScale = spans.length ? Math.min(...spans) : 1;
  const solvedSources = (Array.isArray(solvedSource) ? solvedSource : [solvedSource]).filter(Boolean);
  const previewSources = (Array.isArray(previewSource) ? previewSource : [previewSource]).filter(Boolean);
  const low = Math.min(...temperatures);
  const high = Math.max(...temperatures);
  const amplitude = Math.max(high - low, Math.max(Math.abs(high), 1) * 0.05);
  const oldPower = Math.max(
    solvedSources.reduce((sum, source) => sum + Math.abs(Number(source.total_power_w || 0)), 0)
      || previewSources.reduce((sum, source) => sum + Math.abs(Number(source.total_power_w || 0)), 0)
      || 1,
    1e-12,
  );
  const scalars = temperatures.map((temperature, index) => {
    const point = surface.vertices[index];
    const influence = sources => sources.reduce((sum, source) => {
      const decay = Math.max(sourceSize(source) * 2.5, domainScale / 4, 1e-9);
      return sum + Math.max(0, Number(source.total_power_w || 0)) / oldPower
        * Math.exp(-sourceDistance(point, source) / decay);
    }, 0);
    return Math.max(low, temperature + amplitude * (influence(previewSources) - influence(solvedSources)));
  });
  return { scalars, low: Math.min(...scalars), high: Math.max(...scalars) };
}

export function surfaceGeometry(surface, origin, options = {}) {
  const visible = [];
  const components = options.componentIds || [];
  const positions = [], colors = [], triangleIds = [];
  surface.triangles.forEach((triangle, index) => {
    const id = surface.component_ids[index];
    if (options.isolated && options.isolated !== id || options.hidden?.has(id)) return;
    visible.push(index);
    triangleIds.push(surface.cell_ids[index] ?? null);
    for (const vertex of triangle) {
      positions.push(...surface.vertices[vertex].map((value, axis) => value - origin[axis]));
      let color;
      if (options.scalars) {
        let value = options.scalars[vertex];
        if (options.bands && options.high !== options.low) {
          value = options.low + Math.round((value - options.low) / (options.high - options.low) * 12) / 12 * (options.high - options.low);
        }
        color = fieldColor(value, options.low, options.high, options.difference);
      } else {
        color = new THREE.Color(options.selectedFaces?.has(index) ? 0xf2bf45
          : options.regionFaces?.has(index) ? 0x10a89b : options.boundaryFaces?.has(index) ? 0x558bd0
          : id === options.selected ? 0x10a89b : PALETTE[Math.max(0, components.indexOf(id)) % PALETTE.length]);
      }
      colors.push(color.r, color.g, color.b);
    }
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  geometry.userData = { visibleTriangles: visible, cellIds: triangleIds };
  return geometry;
}

function clearGroup(group) {
  group.traverse(object => {
    object.geometry?.dispose();
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    materials.forEach(material => { material?.map?.dispose(); material?.dispose(); });
  });
  group.clear();
}

function clampSourcePosition(position, source, handle, bounds) {
  if (!bounds) return position;
  const values = position.toArray();
  const center = Object.values(source.center_mm);
  const end = source.shape === "line" && source.end_mm ? Object.values(source.end_mm) : null;
  return new THREE.Vector3(...values.map((value, axis) => {
    if (handle === "center" && end) {
      const minimumDelta = bounds[axis] - Math.min(center[axis], end[axis]);
      const maximumDelta = bounds[axis + 3] - Math.max(center[axis], end[axis]);
      return center[axis] + THREE.MathUtils.clamp(value - center[axis], minimumDelta, maximumDelta);
    }
    return THREE.MathUtils.clamp(value, bounds[axis], bounds[axis + 3]);
  }));
}

export class EngineeringViewport {
  constructor(canvas, callbacks) {
    this.canvas = canvas;
    this.callbacks = callbacks;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor(0xf7f8f7);
    this.scene = new THREE.Scene();
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x72808a, 2.2));
    const light = new THREE.DirectionalLight(0xffffff, 2);
    light.position.set(2, -3, 5);
    this.scene.add(light);
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.001, 100);
    this.camera.up.set(0, 0, 1);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = false;
    this.controls.addEventListener("change", () => this.render());
    this.geometryGroup = new THREE.Group();
    this.markerGroup = new THREE.Group();
    this.scene.add(this.geometryGroup, this.markerGroup);
    this.raycaster = new THREE.Raycaster();
    this.origin = [0, 0, 0];
    this.size = 1;
    this.cache = new Map();
    this.ticket = 0;
    canvas.addEventListener("webglcontextlost", event => {
      event.preventDefault();
      callbacks.status("三维图形服务已中断，请刷新页面恢复", true);
    });
    canvas.addEventListener("pointerdown", event => this.pointerDown(event), true);
    canvas.addEventListener("pointermove", event => this.pointerMove(event), true);
    canvas.addEventListener("pointerup", event => this.pointerUp(event), true);
    canvas.addEventListener("pointercancel", () => { this.drag = null; this.controls.enabled = true; });
    canvas.addEventListener("keydown", event => {
      if (event.key === "Home") { event.preventDefault(); this.fit(); }
    });
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas.parentElement);
    this.fit();
  }

  async fetchSurface(path, body, signal, key) {
    if (this.cache.has(key)) return this.cache.get(key);
    const response = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal } : { signal });
    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(typeof error?.detail === "string" ? error.detail : "三维数据读取失败，请重新打开研究");
    }
    const data = await response.json();
    this.cache.set(key, data);
    if (this.cache.size > 3) this.cache.delete(this.cache.keys().next().value);
    return data;
  }

  update(options) {
    const pending = this.updateScene(options);
    this.pendingPromise = pending;
    return pending;
  }

  async updateScene(options) {
    this.options = options;
    const { workpiece, study, result, mode } = options;
    if (!workpiece) {
      this.ticket++;
      this.abort?.abort();
      this.pendingKey = this.dataKey = null;
      this.surface = null;
      clearGroup(this.geometryGroup);
      clearGroup(this.markerGroup);
      this.callbacks.status("");
      this.render();
      return false;
    }
    const showMesh = mode === "mesh" && options.mesh;
    const showResult = Boolean(result && mode !== "model" && !showMesh);
    const playbackSurface = showResult && options.playbackSurface
      && !options.differenceStudyId && !["flux", "slice"].includes(mode)
      ? options.playbackSurface : null;
    if (playbackSurface) {
      const key = `playback:${study.study_id}:${options.frameIndex}:${mode}`;
      if (key === this.dataKey && this.surface) { this.rebuild(); return true; }
      this.ticket++;
      this.abort?.abort();
      this.pendingKey = null;
      this.dataKey = key;
      this.surface = playbackSurface;
      const geometryKey = workpiece.workpiece_id + workpiece.length_unit;
      if (geometryKey !== this.geometryKey) {
        this.geometryKey = geometryKey;
        const box = new THREE.Box3();
        const bbox = workpiece.geometry?.summary?.bbox;
        if (bbox) {
          box.expandByPoint(new THREE.Vector3(...bbox.slice(0, 3)));
          box.expandByPoint(new THREE.Vector3(...bbox.slice(3, 6)));
        } else playbackSurface.vertices.forEach(vertex => box.expandByPoint(new THREE.Vector3(...vertex)));
        if (!box.isEmpty()) {
          this.origin = box.getCenter(new THREE.Vector3()).toArray();
          this.size = Math.max(box.getSize(new THREE.Vector3()).length(), 1e-8);
        }
        this.fit();
      }
      this.callbacks.status("");
      this.rebuild();
      return true;
    }
    const request = showMesh ? { kind: "mesh" } : { kind: "result", frame_index: options.frameIndex };
    if (options.differenceStudyId) request.difference_study_id = options.differenceStudyId;
    if (mode === "slice" && showResult) {
      request.section_axis = options.sliceAxis;
      request.section_position = { value: options.slicePosition, unit: "mm" };
    }
    const path = showMesh || showResult ? `/v1/studies/${study.study_id}/view`
      : `/v1/workpieces/${workpiece.workpiece_id}/view`;
    const body = showMesh || showResult ? request : null;
    const key = path + JSON.stringify(body) + workpiece.length_unit + (showMesh ? options.mesh.generated_at : "");
    if (key === this.dataKey && this.surface) { this.rebuild(); return true; }
    if (key === this.pendingKey) return this.pendingPromise;
    this.pendingKey = key;
    const ticket = ++this.ticket;
    this.abort?.abort();
    this.abort = new AbortController();
    // Clear stale numerical colors while loading a different study or time step.
    this.surface = null;
    clearGroup(this.geometryGroup);
    clearGroup(this.markerGroup);
    this.callbacks.status(showResult ? "读取数值场" : "读取三维网格");
    this.render();
    try {
      const surface = await this.fetchSurface(path, body, this.abort.signal, key);
      if (ticket !== this.ticket) return false;
      const geometryKey = workpiece.workpiece_id + workpiece.length_unit;
      if (geometryKey !== this.geometryKey) {
        this.geometryKey = geometryKey;
        const box = new THREE.Box3();
        const bbox = workpiece.geometry?.summary?.bbox;
        if (bbox) {
          box.expandByPoint(new THREE.Vector3(...bbox.slice(0, 3)));
          box.expandByPoint(new THREE.Vector3(...bbox.slice(3, 6)));
        } else surface.vertices.forEach(vertex => box.expandByPoint(new THREE.Vector3(...vertex)));
        if (!box.isEmpty()) {
          this.origin = box.getCenter(new THREE.Vector3()).toArray();
          this.size = Math.max(box.getSize(new THREE.Vector3()).length(), 1e-8);
        }
        this.fit();
      }
      this.dataKey = key;
      this.surface = surface;
      this.callbacks.status(surface.vertices.length ? "" : "当前截面未穿过数值网格");
      this.rebuild();
      return true;
    } catch (error) {
      if (ticket === this.ticket && error.name !== "AbortError") this.callbacks.status(error.message, true);
      return false;
    } finally {
      if (ticket === this.ticket) this.pendingKey = null;
    }
  }

  rebuild() {
    if (!this.surface) return;
    clearGroup(this.geometryGroup);
    clearGroup(this.markerGroup);
    const { surface, options } = this;
    const hasField = Boolean(surface.temperature_k);
    const showFlux = options.mode === "flux" && surface.heat_flux_w_m2;
    const diffusion = Boolean(options.diffusion && !showFlux && !options.differenceStudyId);
    let scalars = showFlux ? surface.heat_flux_w_m2.map(v => Math.hypot(...v)) : surface.temperature_k;
    let low = options.low ?? surface.temperature_min_k;
    let high = options.high ?? surface.temperature_max_k;
    const previewSource = options.previewSources?.length ? options.previewSources : options.previewSource;
    const solvedSource = options.solvedSources?.length ? options.solvedSources : options.solvedSource;
    const preview = Boolean(!showFlux && hasField && previewSource);
    if (preview) {
      const field = previewTemperatureField(surface, solvedSource, previewSource);
      scalars = field.scalars;
      if (!options.lockTemperatureScale) {
        low = field.low;
        high = field.high;
      }
    }
    let referenceTemperatureK = null;
    if (diffusion && scalars?.length) {
      referenceTemperatureK = Number.isFinite(options.diffusionReferenceK)
        && !preview ? options.diffusionReferenceK : Math.min(...scalars);
      scalars = options.diffusionUniform
        ? scalars.map(() => low + (high - low) / 2)
        : scalars.map(value => value - referenceTemperatureK);
      this.callbacks.status(options.diffusionUniform
        ? "该结果在当前网格与容差下近似等温" : "");
    }
    if (showFlux && scalars.length) {
      low = surface.heat_flux_min_w_m2;
      high = surface.heat_flux_max_w_m2;
    }
    if (options.differenceStudyId) {
      const span = Math.max(Math.abs(surface.temperature_min_k), Math.abs(surface.temperature_max_k), 1e-12);
      low = -span; high = span;
    }
    const geometry = surfaceGeometry(surface, this.origin, {
      componentIds: options.workpiece.components.map(c => c.component_id), selected: options.selectedComponent,
      hidden: options.hiddenComponents, isolated: options.isolatedComponent,
      scalars, low, high, difference: Boolean(options.differenceStudyId), bands: options.mode === "contour",
      ...(options.mode === "model" ? {
        selectedFaces: options.selectedFaces,
        regionFaces: this.regionFaces(options.selectedRegion),
        boundaryFaces: new Set([...(options.boundaries || []), ...(options.surfaceConditions || []),
          ...(options.contacts || []).flatMap(contact => [
            { region_id: contact.source_region_id }, { region_id: contact.target_region_id },
          ])]
          .flatMap(boundary => [...this.regionFaces(boundary.region_id)])),
      } : {}),
    });
    const Material = hasField ? THREE.MeshBasicMaterial : THREE.MeshStandardMaterial;
    const material = new Material({ vertexColors: true, side: THREE.DoubleSide,
      transparent: options.opacity < 1, opacity: options.opacity, ...(hasField ? {} : { roughness: 0.65, metalness: 0.08 }) });
    this.mesh = new THREE.Mesh(geometry, material);
    this.geometryGroup.add(this.mesh);
    if (options.mode === "mesh") {
      this.geometryGroup.add(new THREE.LineSegments(new THREE.WireframeGeometry(geometry),
        new THREE.LineBasicMaterial({ color: 0x37454b, transparent: true, opacity: 0.55 })));
    }
    if (showFlux) this.addFlux(surface, geometry.userData.visibleTriangles);
    if (!options.differenceStudyId) this.addMarkers();
    this.callbacks.legend({ hasField, low, high, flux: Boolean(showFlux), time: surface.time_s,
      difference: Boolean(options.differenceStudyId), bands: options.mode === "contour", preview,
      locked: Boolean(options.lockTemperatureScale && !showFlux && !options.differenceStudyId),
      diffusion, referenceTemperatureK, uniform: Boolean(diffusion && options.diffusionUniform) });
    this.callbacks.loaded(surface);
    this.render();
  }

  addFlux(surface, visible) {
    const stride = Math.max(1, Math.ceil(visible.length / 120));
    for (let i = 0; i < visible.length; i += stride) {
      const nodes = surface.triangles[visible[i]];
      const vector = new THREE.Vector3(...surface.heat_flux_w_m2[nodes[0]]);
      if (vector.lengthSq() === 0) continue;
      const center = nodes.reduce((v, node) => v.add(this.local(surface.vertices[node])), new THREE.Vector3()).multiplyScalar(1 / 3);
      this.geometryGroup.add(new THREE.ArrowHelper(vector.normalize(), center, this.size * 0.045, 0x253940, this.size * 0.012, this.size * 0.006));
    }
  }

  local(point) { return new THREE.Vector3(...point).sub(new THREE.Vector3(...this.origin)); }

  regionFaces(regionId) {
    const region = this.options.workpiece.regions?.find(item => item.region_id === regionId);
    if (region?.kind === "surface_patch") return new Set(region.triangle_ids);
    if (region?.kind === "component_surface") return new Set(this.surface.component_ids
      .flatMap((id, index) => region.component_ids.includes(id) ? [index] : []));
    if (region?.kind === "bounding_plane") {
      const axis = "xyz".indexOf(region.selector[5]);
      const bounds = this.options.workpiece.geometry.summary.bbox;
      const at = bounds[axis + (region.selector.endsWith("max") ? 3 : 0)];
      return new Set(this.surface.triangles.flatMap((triangle, index) =>
        triangle.every(node => Math.abs(this.surface.vertices[node][axis] - at) < this.size * 1e-8) ? [index] : []));
    }
    return new Set();
  }

  addBoundaryLabel(position, kinds, regionId) {
    const canvas = document.createElement("canvas");
    canvas.width = 192; canvas.height = 48;
    const context = canvas.getContext("2d");
    context.fillStyle = "#ffffff"; context.fillRect(0, 0, canvas.width, canvas.height);
    context.font = "600 28px sans-serif"; context.fillStyle = "#243b40";
    context.textAlign = "center"; context.textBaseline = "middle";
    context.fillText(kinds.map(kind => ({ fixed_temperature: "T", heat_flux: "q", convection: "h", radiation: "R", thermal_contact: "C" })[kind]).join(" + "), 96, 24);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), depthTest: false }));
    sprite.position.copy(position).add(new THREE.Vector3(0, 0, this.size * 0.035));
    sprite.scale.set(this.size * 0.14, this.size * 0.035, 1);
    sprite.userData.regionId = regionId; sprite.renderOrder = 4;
    sprite.userData.tooltip = kinds.map(kind => ({ fixed_temperature: "固定温度", heat_flux: "表面热流", convection: "区域对流", radiation: "环境辐射", thermal_contact: "组件热接触" })[kind]).join(" + ");
    this.markerGroup.add(sprite);
  }

  addMarkers() {
    const { source, workpiece, probe } = this.options;
    const sources = this.options.sources?.length ? this.options.sources : (source ? [source] : []);
    const activeSourceIndex = Math.max(0, Math.min(
      Number(this.options.activeSourceIndex || 0),
      Math.max(sources.length - 1, 0),
    ));
    const radius = this.size * 0.012;
    const dot = (position, color, data) => {
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 12, 8),
        new THREE.MeshBasicMaterial({ color, depthTest: false }));
      mesh.position.copy(this.local(position));
      mesh.renderOrder = 3;
      mesh.userData = data;
      this.markerGroup.add(mesh);
      return mesh;
    };
    const sourceHandle = (position, handle, sourceIndex, color, sourceName) => {
      const visible = dot(position, color, { source: handle, sourceIndex, sourceName });
      const target = new THREE.Mesh(new THREE.SphereGeometry(radius * 3, 12, 8),
        new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthTest: false, depthWrite: false }));
      target.position.copy(visible.position);
      target.userData = { source: handle, sourceIndex, sourceName, sourceHitTarget: true };
      this.markerGroup.add(target);
      return visible;
    };
    sources.forEach((item, sourceIndex) => {
      const selected = sourceIndex === activeSourceIndex;
      const color = selected ? 0xc74330 : 0xdf8b24;
      sourceHandle(Object.values(item.center_mm), "center", sourceIndex, color, item.name);
      if (item.shape === "line" && item.end_mm) {
        sourceHandle(Object.values(item.end_mm), "end", sourceIndex, color, item.name);
        const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([
          this.local(Object.values(item.center_mm)), this.local(Object.values(item.end_mm)),
        ]), new THREE.LineBasicMaterial({ color }));
        line.userData.source = "center";
        line.userData.sourceIndex = sourceIndex;
        this.markerGroup.add(line);
      }
      if (item.shape === "surface") {
        const patch = new THREE.Mesh(new THREE.PlaneGeometry(item.surface_width_mm, item.surface_height_mm),
          new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, transparent: true, opacity: selected ? 0.38 : 0.24 }));
        patch.position.copy(this.local(Object.values(item.center_mm)));
        if (item.surface_normal_axis === "x") patch.rotation.y = Math.PI / 2;
        if (item.surface_normal_axis === "y") patch.rotation.x = Math.PI / 2;
        patch.userData.source = "center";
        patch.userData.sourceIndex = sourceIndex;
        this.markerGroup.add(patch);
      }
      if (item.shape === "volume") {
        const block = new THREE.Mesh(new THREE.BoxGeometry(
          item.volume_width_mm, item.volume_height_mm, item.volume_depth_mm,
        ), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: selected ? 0.34 : 0.2 }));
        block.position.copy(this.local(Object.values(item.center_mm)));
        block.userData.source = "center";
        block.userData.sourceIndex = sourceIndex;
        this.markerGroup.add(block);
        const outline = new THREE.LineSegments(new THREE.EdgesGeometry(block.geometry),
          new THREE.LineBasicMaterial({ color }));
        outline.position.copy(block.position);
        outline.userData.source = "center";
        outline.userData.sourceIndex = sourceIndex;
        this.markerGroup.add(outline);
      }
    });
    if (probe) dot(Object.values(probe.world), 0x172d32, { probe: true });
    const bounds = workpiece.geometry?.summary?.bbox;
    if (!bounds) return;
    const contactSurfaces = (this.options.contacts || []).flatMap(contact => [
      { kind: "thermal_contact", region_id: contact.source_region_id },
      { kind: "thermal_contact", region_id: contact.target_region_id },
    ]).filter(contact => contact.region_id);
    const boundaries = [...(this.options.boundaries || []), ...(this.options.surfaceConditions || []), ...contactSurfaces];
    const shown = new Set();
    for (const boundary of boundaries) {
      const identity = boundary.region_id || boundary.selector;
      if (shown.has(identity)) continue;
      shown.add(identity);
      const kinds = [...new Set(boundaries.filter(item => (item.region_id || item.selector) === identity)
        .map(item => item.kind || "fixed_temperature"))];
      const labelKinds = kinds.filter(kind => kind !== "fixed_temperature");
      const region = boundary.region_id ? workpiece.regions.find(r => r.region_id === boundary.region_id)
        : workpiece.regions.find(r => r.selector === boundary.selector);
      if (boundary.region_id && region?.kind !== "bounding_plane") {
        if (this.options.mode !== "model" || this.options.selectionMode === "faces") continue;
        const faces = this.regionFaces(boundary.region_id);
        const index = this.mesh.geometry.userData.visibleTriangles.find(index => faces.has(index));
        if (index == null) continue;
        const triangle = this.surface.triangles[index];
        const center = [0, 1, 2].map(axis => triangle.reduce((sum, vertex) => sum + this.surface.vertices[vertex][axis], 0) / 3);
        dot(center, region.region_id === this.options.selectedRegion ? 0x0b8176 : 0x325a9a, { regionId: region.region_id });
        if (labelKinds.length) this.addBoundaryLabel(this.local(center), labelKinds, region.region_id);
        continue;
      }
      const selector = (region?.selector || boundary.selector).split(".")[1];
      const axis = "xyz".indexOf(selector[0]);
      const position = [0, 1, 2].map(i => (bounds[i] + bounds[i + 3]) / 2);
      position[axis] = bounds[axis + (selector.endsWith("max") ? 3 : 0)];
      const normal = new THREE.Vector3();
      normal.setComponent(axis, selector.endsWith("max") ? 1 : -1);
      if (boundary.kind === "heat_flux" && boundary.heat_flux_w_m2 > 0) normal.negate();
      const arrow = new THREE.ArrowHelper(normal, this.local(position), this.size * 0.08,
        region?.region_id === this.options.selectedRegion ? 0x0b8176 : 0x325a9a, this.size * 0.018, this.size * 0.009);
      arrow.traverse(object => { object.userData.regionId = region?.region_id; });
      this.markerGroup.add(arrow);
      if (this.options.selectionMode !== "faces" && labelKinds.length) {
        this.addBoundaryLabel(this.local(position), labelKinds, region?.region_id);
      }
    }
  }

  resize() {
    const { width, height } = this.canvas.getBoundingClientRect();
    if (!width || !height) return;
    this.renderer.setSize(width, height, false);
    const halfHeight = this.size * 0.68;
    const halfWidth = halfHeight * width / height;
    this.camera.left = -halfWidth;
    this.camera.right = halfWidth;
    this.camera.top = halfHeight;
    this.camera.bottom = -halfHeight;
    this.camera.updateProjectionMatrix();
    this.render();
  }

  fit(view = "iso") {
    const directions = { iso: [1, -1, 0.8], front: [0, -1, 0], right: [1, 0, 0], top: [0, 0, 1] };
    this.camera.up.set(...(view === "top" ? [0, 1, 0] : [0, 0, 1]));
    this.camera.position.copy(new THREE.Vector3(...(directions[view] || directions.iso)).normalize().multiplyScalar(this.size * 3));
    this.camera.near = this.size * 0.001;
    this.camera.far = this.size * 100;
    this.camera.zoom = Math.min(1, Math.max(0.15, this.canvas.clientWidth / Math.max(this.canvas.clientHeight, 1)));
    this.controls.target.set(0, 0, 0);
    this.controls.update();
    this.resize();
  }

  focus(world) {
    const position = this.local(world);
    const offset = this.camera.position.clone().sub(this.controls.target);
    this.controls.target.copy(position);
    this.camera.position.copy(position).add(offset);
    this.camera.zoom = Math.max(this.camera.zoom, 1.8);
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.render();
  }

  hits(event, group) {
    const box = this.canvas.getBoundingClientRect();
    this.raycaster.params.Line.threshold = this.size * 0.025;
    this.raycaster.setFromCamera(new THREE.Vector2((event.clientX - box.left) / box.width * 2 - 1,
      1 - (event.clientY - box.top) / box.height * 2), this.camera);
    return this.raycaster.intersectObjects(group.children, true);
  }

  sourceHit(event) {
    const box = this.canvas.getBoundingClientRect();
    const pointer = new THREE.Vector2(
      (event.clientX - box.left) / box.width * 2 - 1,
      1 - (event.clientY - box.top) / box.height * 2,
    );
    const sourceHits = this.hits(event, this.markerGroup)
      .filter(item => item.object.userData.source);
    return sourceHits.sort((left, right) => {
      const screenDistance = hit => {
        const projected = hit.object.getWorldPosition(new THREE.Vector3()).project(this.camera);
        return (projected.x - pointer.x) ** 2 + (projected.y - pointer.y) ** 2;
      };
      return screenDistance(left) - screenDistance(right);
    })[0];
  }

  pointerDown(event) {
    this.pointerStart = [event.clientX, event.clientY];
    if (event.button !== 0) return;
    if (this.options.selectionMode === "faces") return;
    const hit = this.sourceHit(event);
    if (!hit) return;
    this.controls.enabled = false;
    const normal = this.camera.getWorldDirection(new THREE.Vector3());
    const handle = hit.object.userData.source;
    const sourceIndex = Number.isInteger(hit.object.userData.sourceIndex)
      ? hit.object.userData.sourceIndex : Number(this.options.activeSourceIndex || 0);
    const sources = this.options.sources?.length ? this.options.sources : [this.options.source].filter(Boolean);
    const draggedSource = sources[sourceIndex] || this.options.source;
    if (!draggedSource) return;
    this.callbacks.sourceSelect?.(sourceIndex);
    const sourcePosition = handle === "end" ? draggedSource.end_mm : draggedSource.center_mm;
    this.drag = { handle, sourceIndex, source: draggedSource,
      plane: new THREE.Plane().setFromNormalAndCoplanarPoint(normal,
      this.local(Object.values(sourcePosition))) };
    this.canvas.setPointerCapture(event.pointerId);
    this.canvas.classList.add("is-over-source", "is-dragging");
    event.stopImmediatePropagation();
  }

  pointerMove(event) {
    if (!this.drag) {
      const hits = this.hits(event, this.markerGroup);
      const source = this.sourceHit(event);
      const marker = hits.find(hit => hit.object.userData.tooltip);
      this.canvas.classList.toggle("is-over-source", Boolean(source));
      this.canvas.title = source
        ? `拖动${source.object.userData.sourceName || "热源"}（单击可选择）`
        : marker?.object.userData.tooltip || "";
      return;
    }
    let position;
    const draggedSource = this.drag.source || this.options.source;
    if (draggedSource?.placement === "surface" && this.mesh) {
      position = this.hits(event, this.geometryGroup).find(hit => hit.object === this.mesh)?.point?.clone();
    } else {
      this.hits(event, this.markerGroup);
      position = this.raycaster.ray.intersectPlane(this.drag.plane, new THREE.Vector3());
    }
    if (position) {
      const world = position.add(new THREE.Vector3(...this.origin));
      const bounded = clampSourcePosition(world, draggedSource, this.drag.handle,
        this.options.workpiece.geometry?.summary?.bbox);
      this.callbacks.source(this.drag.handle, bounded.toArray(), this.drag.sourceIndex);
    }
    event.stopImmediatePropagation();
  }

  pointerUp(event) {
    if (this.drag) {
      this.drag = null;
      this.controls.enabled = true;
      if (this.canvas.hasPointerCapture(event.pointerId)) this.canvas.releasePointerCapture(event.pointerId);
      this.canvas.classList.remove("is-dragging");
      event.stopImmediatePropagation();
      return;
    }
    if (event.button !== 0) return;
    if (!this.pointerStart || Math.hypot(event.clientX - this.pointerStart[0], event.clientY - this.pointerStart[1]) > 4) return;
    const marker = this.options.selectionMode === "faces" ? null
      : this.hits(event, this.markerGroup).find(hit => hit.object.userData.regionId);
    if (marker) { this.callbacks.region(marker.object.userData.regionId); return; }
    const hit = this.mesh ? this.hits(event, this.geometryGroup).find(item => item.object === this.mesh) : null;
    if (!hit) return;
    if (this.options.sourcePlacement && this.options.selectionMode !== "faces") {
      const world = hit.point.clone().add(new THREE.Vector3(...this.origin)).toArray();
      this.callbacks.source("center", world, Number(this.options.activeSourceIndex || 0));
      return;
    }
    const index = this.mesh.geometry.userData.visibleTriangles[hit.faceIndex];
    const cellId = this.surface.cell_ids[index];
    if (this.options.mode === "model" && this.options.selectionMode === "faces" && cellId == null) {
      this.callbacks.face(index);
    } else if (cellId != null && this.surface.temperature_k && !this.options.differenceStudyId) {
      this.callbacks.probe(cellId, this.surface.source_sha256, this.options);
    } else if (this.surface.component_ids[index]) this.callbacks.component(this.surface.component_ids[index]);
  }

  render() {
    this.renderer.render(this.scene, this.camera);
    this.canvas.dataset.renderer = "three-webgl";
    this.canvas.dataset.renderedTriangles = String(this.renderer.info.render.triangles);
    this.canvas.dataset.fieldTime = this.surface?.time_s == null ? "" : String(this.surface.time_s);
    this.canvas.dataset.sourceHash = this.surface?.source_sha256 || "";
    this.canvas.dataset.temperaturePreview = this.options?.previewSource || this.options?.previewSources?.length
      ? "approximate" : "";
  }
}
