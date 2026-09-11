import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const app = readFileSync(process.env.AUDIT_APP || 'src/thermoflow/web/assets/app.js', 'utf8');
function extract(start, end) {
  const a = app.indexOf(start), b = app.indexOf(end, a);
  assert(a >= 0 && b > a);
  return app.slice(a, b);
}
function control(value = '', required = false) {
  return { value, required, disabled: false, classList: { toggle() {} }, setAttribute() {} };
}
function field(dataset, ...inputs) {
  return { dataset, hidden: false, querySelectorAll: () => inputs, querySelector: () => inputs[0] };
}
const lineInput = control('', false);
const surfaceInput = control('', false);
const radius = control('-1');
const depth = control('-1');
const conductivity = control('-1');
const uploadLine = field({ uploadSource: 'line' }, lineInput);
const uploadSurface = field({ uploadSource: 'surface' }, surfaceInput);
const uploadRadius = field({ uploadHideFor: 'surface volume' }, radius);
const uploadDepth = field({ uploadPlacement: 'embedded' }, depth);
const customInputs = [lineInput, surfaceInput, radius, depth, conductivity];
const elements = {
  heatSourceShape: control('line'), heatSourcePlacement: control('embedded'),
  autoModeButton: control(), customModeButton: control(),
  customParameterFields: { hidden: false, querySelectorAll: () => customInputs },
};
const state = { parameterMode: 'custom' };
const document = {
  querySelectorAll: selector => ({
    '[data-upload-source]': [uploadLine, uploadSurface],
    '[data-upload-hide-for]': [uploadRadius], '[data-upload-placement]': [uploadDepth],
  })[selector] || [],
};
const ctx = vm.createContext({ state, elements, document });
vm.runInContext(extract('function updateUploadSourceFields()', 'function closeWorkpieceDialog()'), ctx);
vm.runInContext(extract('function setParameterMode(', 'function collectUploadOverrides()'), ctx);
ctx.updateUploadSourceFields();
assert.equal(lineInput.required, true);
assert.equal(lineInput.disabled, false);
assert.equal(surfaceInput.disabled, true, 'Inactive source shape must be excluded from native form validation');
ctx.setParameterMode('auto');
assert(customInputs.every(input => input.disabled), 'Auto upload must exclude all custom inputs including invalid numeric values');
assert.equal(lineInput.required, false, 'Switching back to automatic mode must release hidden required controls');
ctx.setParameterMode('custom');
assert.equal(lineInput.required, true);
assert.equal(lineInput.disabled, false);
assert.equal(conductivity.disabled, false);
elements.heatSourceShape.value = 'surface';
elements.heatSourcePlacement.value = 'surface';
ctx.updateUploadSourceFields();
assert.equal(lineInput.disabled, true);
assert.equal(surfaceInput.disabled, false);
assert.equal(radius.disabled, true);
assert.equal(depth.disabled, true);

const draft = { shape: 'line', placement: 'embedded' };
const sourceElements = { canvasSourceRadiusLabel: {} };
for (const name of ['Depth','Radius','EndX','EndY','EndZ','SurfaceAxis','Width','Height','Thickness','VolumeWidth','VolumeHeight','VolumeDepth']) {
  const input = control('-1');
  sourceElements[`canvasSource${name}`] = input;
  sourceElements[`canvasSource${name}Field`] = field({}, input);
}
const sourceCtx = vm.createContext({ elements: sourceElements, state: {}, activeSourceDraft: () => draft });
vm.runInContext(extract('function syncSourceShapeFields()', 'function syncSourceList()'), sourceCtx);
sourceCtx.syncSourceShapeFields();
assert.equal(sourceElements.canvasSourceEndX.disabled, false);
assert.equal(sourceElements.canvasSourceWidth.disabled, true);
draft.shape = 'surface'; draft.placement = 'surface';
sourceCtx.syncSourceShapeFields();
for (const name of ['Depth','Radius','EndX','EndY','EndZ','VolumeWidth']) {
  assert.equal(sourceElements[`canvasSource${name}`].disabled, true, `${name}: hidden source input must not block Apply`);
}
assert.equal(sourceElements.canvasSourceWidth.disabled, false);
assert.equal(sourceElements.canvasSourceWidth.required, true);
draft.shape = 'point';
sourceCtx.syncSourceShapeFields();
assert.equal(sourceElements.canvasSourceWidth.disabled, true);
assert.equal(sourceElements.canvasSourceRadius.disabled, false);

const duration = control('4000');
const transientElements = {
  draftAnalysisType: control('steady_state_conduction'), draftInitialTemperature: control('293.15'),
  draftDuration: duration, draftTimeStep: control('1'),
};
let confirmed = false;
const transientCtx = vm.createContext({
  elements: transientElements,
  document: { querySelectorAll: () => [field({}, duration)] },
  selectedStudy: () => ({ plan: {}, confirmation: { status: confirmed ? 'confirmed' : 'needs_input' } }),
  ThermoFlowSources: { longTransientWindow: () => ({ initialTemperature: 293.15, duration: 60, timeStep: 1 }) },
});
vm.runInContext(extract('function syncTransientFields()', 'function renderReviewList('), transientCtx);
transientCtx.syncTransientFields();
assert.equal(duration.disabled, true, 'An invalid old transient duration must not block switching to steady analysis');
transientElements.draftAnalysisType.value = 'transient_conduction';
transientCtx.syncTransientFields();
assert.equal(duration.disabled, false);
confirmed = true;
transientCtx.syncTransientFields();
assert.equal(duration.disabled, true, 'Confirmed study timing remains read-only');
console.log('Upload mode, source shape/placement, and transient input validation scopes passed');
