import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const context = vm.createContext({ window: {} });
vm.runInContext(
  readFileSync("src/thermoflow/web/assets/source-editor-model.js", "utf8"),
  context,
);
const model = context.window.ThermoFlowSources;

const legacyPlan = {
  analysis_type: "transient_conduction",
  initial_temperature_k: 293.15,
  duration_s: 5,
  time_step_s: 0.5,
  heat_source_enabled: true,
  heat_source: {
    shape: "point",
    placement: "embedded",
    center_mm: { x: 10, y: 5, z: 2.5 },
    total_power_w: 14,
    radius_mm: 2,
    embedding_depth_mm: 0,
  },
  heat_sources: [],
};
const bounds = [0, 0, 0, 20, 10, 5];
const collection = model.createCollection(legacyPlan, bounds);
assert.equal(collection.sources.length, 1, "Legacy single-source studies must remain editable");
assert.equal(collection.sources[0].sourceId, "source-1");

model.addSource(collection, bounds);
assert.equal(collection.sources.length, 2, "Adding a source must retain its sibling");
assert.equal(collection.activeIndex, 1);
Object.assign(collection.sources[1], {
  shape: "volume",
  name: "体热源 2",
  power: 30,
  volumeWidth: 4,
  volumeHeight: 3,
  volumeDepth: 2,
});
const overrides = model.toOverrides(collection, { duration: 60, timeStep: 1 });
assert.equal(overrides.heat_sources.length, 2);
assert.equal(overrides.heat_sources[1].shape, "volume");
assert.deepEqual(JSON.parse(JSON.stringify([
  overrides.heat_sources[1].volume_width_mm,
  overrides.heat_sources[1].volume_height_mm,
  overrides.heat_sources[1].volume_depth_mm,
])), [4, 3, 2]);
assert.equal(overrides.duration_s, 60);
assert.equal(overrides.time_step_s, 1);

assert.equal(model.selectSource(collection, 0), true,
  "A source can be selected explicitly instead of relying on the current index");
assert.equal(collection.activeIndex, 0);
assert.equal(model.selectSource(collection, 99), false,
  "An invalid source selection must leave the active source unchanged");
assert.equal(collection.activeIndex, 0);
assert.equal(model.removeSource(collection, 0), true,
  "Deleting a named source must remove that exact source");
assert.equal(collection.sources.length, 1);
assert.equal(collection.sources[0].sourceId, "source-2");
assert.equal(collection.activeIndex, 0);

model.addSource(collection, bounds);
assert.equal(collection.sources.length, 2);
model.removeActiveSource(collection);
assert.equal(collection.sources.length, 1, "Removing the active source must preserve the others");
assert.equal(collection.activeIndex, 0);

const defaultWindow = model.longTransientWindow({
  analysis_type: "transient_conduction",
  initial_temperature_k: null,
  duration_s: null,
  time_step_s: null,
});
assert.deepEqual(JSON.parse(JSON.stringify(defaultWindow)), {
  initialTemperature: 293.15,
  duration: 60,
  timeStep: 0.3,
});

const extendedWindow = model.longTransientWindow({
  analysis_type: "transient_conduction",
  initial_temperature_k: 420,
  duration_s: 2,
  time_step_s: 0.25,
});
assert.deepEqual(JSON.parse(JSON.stringify(extendedWindow)), {
  initialTemperature: 420,
  duration: 2,
  timeStep: 0.25,
}, "Opening the editor must preserve the user-selected duration and integration limit");

for (const duration of [0.1, 5, 60, 600, 3600]) {
  const configured = model.longTransientWindow({ duration_s: duration, time_step_s: duration / 200 });
  assert.equal(configured.duration, duration);
  assert.equal(configured.timeStep, duration / 200);
}

console.log("Multiple-source editor model and long transient defaults passed");
