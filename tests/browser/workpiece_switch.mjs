import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync('src/thermoflow/web/assets/app.js', 'utf8');
const fragment = (from, to) => source.slice(source.indexOf(from), source.indexOf(to));
let study = null;
let received;
let drawings = 0;
const context = vm.createContext({
  state: {}, selectedStudy: () => study,
  document: {getElementById: () => ({})}, validationFields: () => new Map(),
  validationFeedback: {render: (s, options) => { received = options; return []; }},
  renderHealth() {}, renderWorkpieces() {}, renderComponents() {}, renderWorkspace() {},
  renderStudies() {}, renderPrimaryAction() {}, drawWorkpiece() { drawings++; },
  renderInspector() { context.renderValidationFeedback(); },
  elements: {draftEnableHeatSource: {checked: false}},
  ThermoFlowSources: {sourcesFromPlan: p => p?.heat_sources || [], toPlanSource: s => s},
  activeSourceCollection: () => ({sources: [{total_power_w: 1}]}),
});
vm.runInContext(fragment('function renderValidationFeedback()', 'async function loadSelectedValidation()'), context);
vm.runInContext(fragment('function render() {', 'function renderHealth()'), context);
// Fresh imports have neither a study nor validation state. Rendering must reach the canvas.
for (const absent of [undefined, null]) {
  context.state.validationFailure = absent;
  context.state.validationPending = absent;
  context.render();
  assert.equal(received.failure, '');
  assert.equal(received.pending, false);
}
assert.equal(drawings, 2);
context.state.validationFailure = {studyId: 'old', message: 'old error'};
context.render();
assert.equal(received.failure, '');
study = {study_id: 'old', plan: {}, draft_revision: 1};
context.render();
assert.equal(received.failure, 'old error');

vm.runInContext(fragment('function heatSourceEnabled(', 'function displayedHeatSource('), context);
study = {study_id: 'new', plan: {heat_source_enabled: false, heat_sources: []}};
context.state.draftSyncedFor = 'new';
assert.equal(context.displayedHeatSources(study.plan).length, 0, 'Disabled source must hide even a cached editor marker');
context.elements.draftEnableHeatSource.checked = true;
assert.equal(context.heatSourceEnabled(study.plan), true, 'Explicitly enabling an empty draft must allow adding sources');
assert.equal(context.displayedHeatSources(study.plan).length, 1);

let resolveMesh;
context.request = () => new Promise(resolve => { resolveMesh = resolve; });
context.showToast = message => assert.fail(message);
vm.runInContext(fragment('async function loadSelectedMesh()', 'async function loadSelectedAgentRun()'), context);
study = {study_id: 'old', mesh_status: 'ready'};
const pendingMesh = context.loadSelectedMesh();
study = null; // The user selects a newly imported STL while the previous mesh is loading.
resolveMesh({study_id: 'old'});
await pendingMesh;
assert.equal(context.state.mesh, null);
console.log('Fresh imports render, old validation/mesh state stays with its study, and heat sources require explicit enabling.');
