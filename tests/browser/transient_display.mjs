import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const context = vm.createContext({ window: {} });
vm.runInContext(
  readFileSync("src/thermoflow/web/assets/transient-display.js", "utf8"),
  context,
);
const display = context.window.ThermoFlowTransient;

const scale = display.diffusionScale([
  { temperature_min_k: 293, temperature_max_k: 293 },
  { temperature_min_k: 294, temperature_max_k: 300 },
  { temperature_min_k: 500, temperature_max_k: 501 },
]);
assert.deepEqual(
  JSON.parse(JSON.stringify(scale)),
  { low: 0, high: 6, uniform: false, toleranceK: 1e-9 },
  "The diffusion scale must use the largest within-frame spatial spread",
);

const absolute = [311.5, 319.0, 315.25];
const derived = display.diffusionScalars(absolute, 311.5);
assert.deepEqual([...derived], [0, 7.5, 3.75]);
assert.deepEqual(absolute, [311.5, 319.0, 315.25],
  "Deriving diffusion colors must not mutate the absolute playback frame");

const uniform = display.diffusionScale([
  { temperature_min_k: 293.15, temperature_max_k: 293.1500000005 },
]);
assert.equal(uniform.uniform, true,
  "Numerical noise below tolerance must not be visually amplified");

assert.throws(
  () => display.diffusionScalars([293, Number.NaN], 293),
  /有限温度/,
  "Malformed playback temperatures must stop the derived view",
);

const copper = {
  name: "C110 紫铜",
  source_version: "demo-v1",
  valid_temperature_min_k: 273.15,
  valid_temperature_max_k: 473.15,
};
const warnings = display.materialRangeWarnings({
  material: copper,
  component_materials: [
    { component_id: "left", material: copper },
    { component_id: "right", material: copper },
  ],
}, {
  time_steps: [{ index: 0 }],
  temperature_min_over_time_k: 293.15,
  temperature_max_over_time_k: 1519.15,
  temperature_min_k: 1510,
  temperature_max_k: 1519.15,
});
assert.equal(warnings.length, 1, "Identical component material records must share one warning");
assert.deepEqual([...warnings[0].componentIds], ["left", "right"]);
assert.equal(warnings[0].validMinimumK, 273.15);
assert.equal(warnings[0].validMaximumK, 473.15);
assert.equal(warnings[0].resultMinimumK, 293.15);
assert.equal(warnings[0].resultMaximumK, 1519.15);
for (const phrase of ["常物性外推", "未建模温度相关物性和相变", "不可直接作为工程结论"]) {
  assert(warnings[0].message.includes(phrase));
}

const missingRange = display.materialRangeWarnings({
  material: {
    name: "用户材料",
    source_version: null,
    valid_temperature_min_k: null,
    valid_temperature_max_k: null,
  },
  component_materials: [],
}, {
  time_steps: [], temperature_min_k: 300, temperature_max_k: 350,
});
assert.equal(missingRange.length, 1);
assert.match(missingRange[0].message, /材料有效温区未提供/);

console.log("Transient diffusion scale, immutable derived colors and material-range warnings passed");
