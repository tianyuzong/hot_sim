import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const context = vm.createContext({window: {}});
vm.runInContext(readFileSync("src/thermoflow/web/assets/transient-display.js", "utf8"), context);
const display = context.window.ThermoFlowTransient;
assert.equal(typeof display.legendValues, "function", "Legends must distinguish absolute temperature and differences");
assert.deepEqual(JSON.parse(JSON.stringify(display.legendValues(293.15, 313.15))),
  {values: [20, 30, 40], unit: "℃"});
assert.deepEqual(JSON.parse(JSON.stringify(display.legendValues(0, 10, true))),
  {values: [0, 5, 10], unit: "Δ℃"});
console.log("Absolute room temperature is 20 ℃; a zero difference is explicitly Δ℃");
