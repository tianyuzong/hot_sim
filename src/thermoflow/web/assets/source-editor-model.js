(function attachThermoFlowSourceModel(root) {
  "use strict";

  const axes = ["x", "y", "z"];

  function numeric(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function geometryDefaults(bounds, plan) {
    const safeBounds = Array.isArray(bounds) && bounds.length === 6
      ? bounds.map(Number) : [0, 0, 0, 1, 1, 1];
    const spans = axes.map((_, index) => Math.max(safeBounds[index + 3] - safeBounds[index], 1e-6));
    const center = Object.fromEntries(axes.map((axis, index) => [
      axis, (safeBounds[index] + safeBounds[index + 3]) / 2,
    ]));
    const radius = Math.max(Math.min(...spans) / 10, numeric(plan?.mesh?.target_element_size_mm, 0.1));
    return { bounds: safeBounds, spans, center, radius };
  }

  function sourceToDraft(source, index, defaults) {
    const center = { ...(source?.center_mm || defaults.center) };
    const radius = numeric(source?.radius_mm, defaults.radius);
    return {
      sourceId: source?.source_id || `source-${index + 1}`,
      name: source?.name || `热源 ${index + 1}`,
      center,
      end: source?.end_mm ? { ...source.end_mm } : {
        x: Math.min(center.x + radius * 4, defaults.bounds[3]),
        y: center.y,
        z: center.z,
      },
      shape: source?.shape || "point",
      placement: source?.placement || "embedded",
      embeddingDepth: numeric(source?.embedding_depth_mm, 0),
      power: numeric(source?.total_power_w, 10),
      radius,
      surfaceAxis: source?.surface_normal_axis || "z",
      surfaceWidth: numeric(source?.surface_width_mm, radius * 4),
      surfaceHeight: numeric(source?.surface_height_mm, radius * 4),
      surfaceThickness: numeric(source?.surface_thickness_mm, radius),
      volumeWidth: numeric(source?.volume_width_mm, radius * 4),
      volumeHeight: numeric(source?.volume_height_mm, radius * 4),
      volumeDepth: numeric(source?.volume_depth_mm, radius * 4),
    };
  }

  function sourcesFromPlan(plan) {
    if (Array.isArray(plan?.heat_sources) && plan.heat_sources.length) return plan.heat_sources;
    return plan?.heat_source ? [plan.heat_source] : [];
  }

  function createCollection(plan, bounds) {
    const defaults = geometryDefaults(bounds, plan);
    let sources = sourcesFromPlan(plan).map((source, index) => sourceToDraft(source, index, defaults));
    if (!sources.length) sources = [sourceToDraft(null, 0, defaults)];
    return {
      studyId: null,
      activeIndex: 0,
      sources,
      ambientTemperature: numeric(
        plan?.convection?.ambient_temperature_k,
        numeric(plan?.initial_temperature_k, numeric(plan?.boundaries?.[0]?.temperature_k, 293.15)),
      ),
      convectionCoefficient: numeric(plan?.convection?.heat_transfer_coefficient_w_m2_k, 10),
      timeWindow: longTransientWindow(plan),
    };
  }

  function nextSourceId(collection) {
    const used = new Set(collection.sources.map(source => source.sourceId));
    let number = 1;
    while (used.has(`source-${number}`)) number += 1;
    return `source-${number}`;
  }

  function addSource(collection, bounds, plan) {
    if (collection.sources.length >= 16) return false;
    const defaults = geometryDefaults(bounds, plan);
    const draft = sourceToDraft(null, collection.sources.length, defaults);
    draft.sourceId = nextSourceId(collection);
    draft.name = `热源 ${collection.sources.length + 1}`;
    const offset = Math.min(defaults.spans[0] * 0.08 * collection.sources.length, defaults.spans[0] * 0.35);
    draft.center.x = Math.min(defaults.bounds[3], draft.center.x + offset);
    draft.end.x = Math.min(defaults.bounds[3], draft.end.x + offset);
    collection.sources.push(draft);
    collection.activeIndex = collection.sources.length - 1;
    return true;
  }

  function removeActiveSource(collection) {
    return removeSource(collection, collection.activeIndex);
  }

  function selectSource(collection, index) {
    if (!Number.isInteger(index) || index < 0 || index >= collection.sources.length) return false;
    collection.activeIndex = index;
    return true;
  }

  function removeSource(collection, index) {
    if (collection.sources.length <= 1 || !Number.isInteger(index)
      || index < 0 || index >= collection.sources.length) return false;
    collection.sources.splice(index, 1);
    if (collection.activeIndex > index) collection.activeIndex -= 1;
    collection.activeIndex = Math.min(collection.activeIndex, collection.sources.length - 1);
    return true;
  }

  function toPlanSource(draft) {
    const usesRadius = draft.shape === "point" || draft.shape === "line";
    const inactiveRadius = draft.shape === "surface"
      ? Math.min(draft.surfaceWidth, draft.surfaceHeight, draft.surfaceThickness) / 2
      : Math.min(draft.volumeWidth, draft.volumeHeight, draft.volumeDepth) / 2;
    return {
      kind: "volumetric_power",
      source_id: draft.sourceId,
      name: draft.name,
      shape: draft.shape,
      placement: draft.placement,
      center_mm: { ...draft.center },
      total_power_w: Number(draft.power),
      radius_mm: usesRadius ? Number(draft.radius)
        : Number(draft.radius) > 0 ? Number(draft.radius) : Math.max(numeric(inactiveRadius, 1), 0.000001),
      embedding_depth_mm: draft.placement === "embedded" ? Number(draft.embeddingDepth) : 0,
      end_mm: draft.shape === "line" ? { ...draft.end } : null,
      surface_normal_axis: draft.shape === "surface" ? draft.surfaceAxis : null,
      surface_width_mm: draft.shape === "surface" ? Number(draft.surfaceWidth) : null,
      surface_height_mm: draft.shape === "surface" ? Number(draft.surfaceHeight) : null,
      surface_thickness_mm: draft.shape === "surface" ? Number(draft.surfaceThickness) : null,
      volume_width_mm: draft.shape === "volume" ? Number(draft.volumeWidth) : null,
      volume_height_mm: draft.shape === "volume" ? Number(draft.volumeHeight) : null,
      volume_depth_mm: draft.shape === "volume" ? Number(draft.volumeDepth) : null,
    };
  }

  function toOverrides(collection, time = {}) {
    const overrides = { heat_sources: collection.sources.map(toPlanSource) };
    if (Number.isFinite(Number(time.duration))) overrides.duration_s = Number(time.duration);
    if (Number.isFinite(Number(time.timeStep))) overrides.time_step_s = Number(time.timeStep);
    if (Number.isFinite(Number(time.initialTemperature))) {
      overrides.initial_temperature_k = Number(time.initialTemperature);
    }
    return overrides;
  }

  function longTransientWindow(plan) {
    const duration = numeric(plan?.duration_s, 60);
    return {
      initialTemperature: numeric(plan?.initial_temperature_k, 293.15),
      duration,
      timeStep: numeric(plan?.time_step_s, duration / 200),
    };
  }

  root.ThermoFlowSources = {
    addSource,
    createCollection,
    longTransientWindow,
    removeActiveSource,
    removeSource,
    selectSource,
    sourcesFromPlan,
    toOverrides,
    toPlanSource,
  };
}(window));
