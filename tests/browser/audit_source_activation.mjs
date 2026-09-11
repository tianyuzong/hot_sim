import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const app = readFileSync(process.env.AUDIT_APP || 'src/thermoflow/web/assets/app.js', 'utf8');
const model = readFileSync(process.env.AUDIT_MODEL || 'src/thermoflow/web/assets/source-editor-model.js', 'utf8');
function extract(start, end) { const a = app.indexOf(start), b = app.indexOf(end, a); assert(a >= 0 && b > a); return app.slice(a, b); }
const modelContext = vm.createContext({ window: {} });
vm.runInContext(model, modelContext);
const ThermoFlowSources = modelContext.window.ThermoFlowSources;
const plan = { analysis_type: 'transient_conduction', heat_source_enabled: false, heat_sources: [], initial_temperature_k: 293.15 };
const study = { study_id: 'draft', plan, confirmation: { status: 'needs_input' } };
const collection = ThermoFlowSources.createCollection(plan, [0,0,0,10,10,10]);
const draft = collection.sources[0];
const controls = {};
const elements = new Proxy(controls, { get(target, key) {
  return target[key] ||= { value: '1', checked: false, hidden: true, focus() {}, setAttribute() {}, checkValidity() { return true; }, setCustomValidity() {}, classList: { add() {}, remove() {} } };
} });
let saves = 0;
const state = {};
const ctx = vm.createContext({ elements, state, ThermoFlowSources,
  selectedWorkpiece: () => ({ unit_confirmed: true }), selectedStudy: () => study,
  activeSourceDraft: () => draft, activeSourceCollection: () => collection,
  syncSourceEditor() {}, positionSourceEditor() {}, syncSourceShapeFields() {}, syncSourceList() {},
  updateSourcePositionReadout() {}, startHeatAnimation() {}, queueDraftSave() { saves++; },
});
vm.runInContext(extract('function openSourceEditor()', 'function discardSourceDraft()'), ctx);
vm.runInContext(extract('function syncDurationTimeStep(', 'function syncTransientFields()'), ctx);
vm.runInContext(extract('function updateSourceDraftFromInputs(', 'function simulationOverridesFromDraft('), ctx);
ctx.openSourceEditor();
assert.equal(elements.draftEnableHeatSource.checked, false, 'Opening heat settings must not turn a no-heat simulation into a heated simulation');
assert.equal(elements.sourceEditor.hidden, false);
ctx.closeSourceEditor();
assert.equal(elements.draftEnableHeatSource.checked, false);
assert.equal(saves, 0);

elements.canvasSourceShape.value = 'point'; elements.canvasSourcePlacement.value = 'embedded';
ctx.updateSourceDraftFromInputs(undefined, { markDirty: false });
assert.equal(elements.draftEnableHeatSource.checked, false, 'Selecting an existing heat source is read-only');
assert.equal(saves, 0, 'Selecting a source must not invalidate input confirmation or enqueue a write');
ctx.updateSourceDraftFromInputs({ target: elements.canvasDuration });
assert.equal(elements.draftEnableHeatSource.checked, false, 'Editing duration must not implicitly enable a heat source');
ctx.updateSourceDraftFromInputs({ target: elements.canvasAmbientTemperature });
assert.equal(elements.draftEnableHeatSource.checked, false, 'Editing ambient temperature must not implicitly enable a heat source');
ctx.updateSourceDraftFromInputs({ target: elements.canvasSourcePower });
assert.equal(elements.draftEnableHeatSource.checked, true, 'Explicit source edits enable the source in an unconfirmed draft');
elements.draftEnableHeatSource.checked = false;
ctx.updateSourceDraftFromInputs();
assert.equal(elements.draftEnableHeatSource.checked, true, 'Explicit Apply enables the edited source');

const surface = { ...draft, shape: 'surface', placement: 'surface', radius: -1, embeddingDepth: -1,
  surfaceWidth: 5, surfaceHeight: 4, surfaceThickness: 1 };
const surfacePlan = ThermoFlowSources.toPlanSource(surface);
assert(surfacePlan.radius_mm > 0, 'An inactive invalid radius cannot poison a surface-source submission');
assert.equal(surfacePlan.embedding_depth_mm, 0, 'Surface placement cannot submit a stale hidden embedding depth');
const invalidPoint = ThermoFlowSources.toPlanSource({ ...surface, shape: 'point', placement: 'embedded' });
assert.equal(invalidPoint.radius_mm, -1, 'Active invalid radius still reaches validation without silently changing it');
assert.equal(invalidPoint.embedding_depth_mm, -1, 'Active invalid depth still reaches validation without silently changing it');
console.log('Read-only source opening/selection, explicit activation, and inactive-field normalization passed');
