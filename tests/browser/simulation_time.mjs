import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync("src/thermoflow/web/assets/app.js", "utf8");
const functions = source.slice(source.indexOf("function openSimulationTimeSettings("), source.indexOf("function stopTimePlayback("));
const requests = [];
const study = {
  study_id: "original", confirmation: { status: "confirmed" },
  plan: { initial_temperature_k: 300, duration_s: 60, time_step_s: 0.3 },
};
const original = JSON.stringify(study);
let valid = true;
let submissions = 0;
let validationMessage = "";
const context = vm.createContext({
  state: { busy: false }, selectedStudy: () => study,
  elements: {
    simulationTimeDialog: { dataset: { studyId: "original" }, close() {} },
    simulationTimeForm: { reportValidity: () => valid },
    simulationDuration: { value: "3600" },
    simulationTimeStep: { value: "18", setCustomValidity(value) { validationMessage = value; }, reportValidity() {} },
    applySimulationTime: { disabled: false },
  },
  setBusy(value) { context.state.busy = value; },
  discardSourceDraft() {},
  request: async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return { study_id: "time-copy", status: "needs_input" };
  },
  submitComputation: async (id, operation) => {
    assert.equal(id, "time-copy");
    assert.equal(operation, "apply_and_solve");
    submissions++;
    return { task_id: "new-task" };
  },
  showToast(message, error) { assert(!error, message); },
});
vm.runInContext(functions, context);
const submit = () => context.applySimulationTime({ preventDefault() {} });
await submit();
assert.equal(submissions, 1);
assert.deepEqual(requests[0].body.overrides, {
  analysis_type: "transient_conduction", initial_temperature_k: 300, duration_s: 3600, time_step_s: 18,
});
assert.deepEqual(requests[1].body.overrides, requests[0].body.overrides);
assert.equal(requests[1].body.materials_confirmed, true);
assert.equal(JSON.stringify(study), original, "Timing changes must preserve the original study");

context.elements.simulationTimeStep.value = "0.3";
await submit();
assert.match(validationMessage, /18/);
assert.equal(submissions, 1, "Invalid integration limits must not create a study");
valid = false;
context.elements.simulationDuration.value = "3601";
await submit();
assert.equal(requests.length, 2, "Out-of-range form values must not make an API request");
console.log("Simulation time settings preserve saved inputs and reject invalid timing before submission");
