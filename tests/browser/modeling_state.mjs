import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync("src/thermoflow/web/assets/app.js", "utf8");
function definition(name, next) {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf(`function ${next}(`, start);
  assert(start >= 0 && end > start);
  const chunk = source.slice(start, end).replace(/async\s*$/, "");
  return source.slice(start - 6, start) === "async " ? `async ${chunk}` : chunk;
}

const element = (value = "") => ({ value, checked: false, disabled: false,
  validity: { badInput: false }, checkValidity: () => true });
const elements = Object.fromEntries(["draftAnalysisType", "draftInitialTemperature", "draftDuration", "draftTimeStep",
  "draftHeatAxis", "draftCriterionMax", "draftEnableHeatSource", "draftEnableGlobalConvection", "draftSourcePower",
  "draftSourceRadius", "draftAmbient", "draftConvection", "draftMeshSize", "studyPurpose", "draftSaveStatus"].map(id => [id, element()]));
elements.componentMaterialList = { querySelectorAll: () => [] };
elements.structuredInputs = { querySelectorAll: () => [elements.draftTimeStep] };
elements.sourceEditor = { hidden: true };
elements.draftAnalysisType.value = "transient_conduction";
elements.draftTimeStep.id = "draftTimeStep";
elements.draftTimeStep.checkValidity = () => false;
elements.draftCriterionMax.value = "400";
elements.draftMeshSize.value = "1";
elements.studyPurpose.value = "Edited scenario";
const state = { selectedStudyId: "study-a", studies: [], draftBaseRevision: 0, draftDirty: true,
  draftSaveError: false, draftEditSerial: 1, draftSavePromise: null, modelingSessionHistory: new Map() };
const study = { study_id: "study-a", draft_revision: 0, confirmation: { status: "needs_input" },
  plan: { component_materials: [], contacts: [{ kind: "thermal_contact", source_component_id: "component-a",
    target_component_id: "component-b", source_region_id: "region-111111111111",
    target_region_id: "region-222222222222", contact_resistance_m2_k_w: 0.0001 }], criteria: [
    { metric: "max_temperature", operator: "less_than", target: { value: 400, unit: "K" } },
    { metric: "energy_balance_error", operator: "less_or_equal", target: { value: 1e-6, unit: "1" } },
  ] } };
state.studies.push(study);
const requests = [];
let resolveRequest;
const context = vm.createContext({ state, elements, clearTimeout, console,
  selectedStudy: () => state.studies.find(item => item.study_id === state.selectedStudyId),
  selectedWorkpiece: () => ({ cad_format: "stl" }), activeSourceDraft: () => null,
  activeSourceCollection: () => null,
  fixedBoundaryValues: () => [], surfaceConditionValues: () => [],
  thermalContactValues: () => study.plan.contacts,
  materialAssignmentFromRow: () => assert.fail("No material row in this fixture"),
  renderModeling() {}, showToast() {}, discardSourceDraft() {}, renderInspector() {}, drawWorkpiece() {},
  request: async (url, options) => {
    requests.push(JSON.parse(options.body));
    return new Promise(resolve => { resolveRequest = resolve; });
  },
});
for (const [name, next] of [["draftFieldsValid", "rememberModelingStudy"],
  ["rememberModelingStudy", "queueDraftSave"], ["saveStructuredDraft", "flushDraftBeforeNavigation"],
  ["confirmedOverrides", "confirmStudy"]]) vm.runInContext(definition(name, next), context);

assert.equal(context.draftFieldsValid(), true, "Missing transient parameters remain editable draft inputs");
elements.draftTimeStep.validity.badInput = true;
assert.equal(context.draftFieldsValid(), false, "Malformed numerical input must not be treated as an absent value");
elements.draftTimeStep.validity.badInput = false;
const overrides = context.confirmedOverrides();
assert.equal(overrides.time_step_s, null);
assert.equal(overrides.duration_s, null);
assert.equal(overrides.initial_temperature_k, null);
assert.equal(overrides.criteria, study.plan.criteria, "Untouched criteria keep other metrics and the exact operator");
assert(!("material_name" in overrides), "Autosave must not change material source provenance implicitly");
assert.deepEqual(overrides.contacts, study.plan.contacts, "Autosave and confirmation must preserve thermal contacts");
elements.draftCriterionMax.value = "410";
const changed = context.confirmedOverrides();
assert.equal(changed.criteria.length, 2);
assert.equal(changed.criteria[0].metric, "energy_balance_error");
assert.equal(changed.criteria[1].target.value, 410);

const first = context.saveStructuredDraft();
const concurrent = context.saveStructuredDraft();
assert.equal(requests.length, 1, "Concurrent save callers must share the in-flight request");
state.draftEditSerial++;
elements.studyPurpose.value = "A later edit";
resolveRequest({ ...study, draft_revision: 1 });
await new Promise(resolve => setImmediate(resolve));
assert.equal(requests.length, 2, "Edits made during a save must be sent once after its acknowledgement");
assert.equal(requests[1].expected_revision, 1);
assert.equal(requests[1].purpose, "A later edit");
resolveRequest({ ...study, draft_revision: 2 });
await Promise.all([first, concurrent]);
assert.equal(requests.length, 2);
assert.equal(state.draftDirty, false);
assert.equal(state.draftBaseRevision, 2);
context.rememberModelingStudy({ ...study, draft_revision: 1 });
assert.equal(state.studies[0].draft_revision, 2, "A late model response must not regress the saved revision");
context.rememberModelingStudy({ ...study, draft_revision: 3,
  modeling: { history_retained: false, messages: [{ role: "user", content: "Session only" }] } });
assert.equal(state.modelingSessionHistory.get(study.study_id).length, 1);
context.rememberModelingStudy({ ...study, draft_revision: 4,
  modeling: { history_retained: false, messages: [{ role: "assistant", content: "Dismissed" }] } }, { appendHistory: true });
assert.equal(state.modelingSessionHistory.get(study.study_id).length, 2);
console.log("Modeling form state: nullable inputs, criteria/provenance preservation, serialized saves, stale responses and session history passed");

for (const id of ["serviceDot", "serviceLabel", "plannerLabel", "cadflowLabel", "diagnosticService", "diagnosticCompute", "diagnosticAgent"]) {
  elements[id] = { textContent: "", className: "" };
}
vm.runInContext(definition("renderHealth", "renderWorkpieces"), context);
state.health = { status: "ok", planner_mode: "codex", compute: { ready: true, selected_backend: "cpu" } };
context.renderHealth();
assert.equal(elements.plannerLabel.textContent, "Codex");
assert.equal(elements.diagnosticAgent.textContent, "Codex · 服务端配置");
state.health.planner_mode = "deterministic";
context.renderHealth();
assert.equal(elements.plannerLabel.textContent, "离线规划器");
state.health.planner_mode = "openai";
state.health.openai_model = "configured-model";
context.renderHealth();
assert.equal(elements.plannerLabel.textContent, "configured-model");
console.log("Planner labels distinguish Codex, OpenAI and offline mode without configuration paths");
