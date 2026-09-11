import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const app = readFileSync(process.env.AUDIT_APP || 'src/thermoflow/web/assets/app.js', 'utf8');
function extract(start, end) { const a = app.indexOf(start), b = app.indexOf(end, a); assert(a >= 0 && b > a); return app.slice(a, b); }
const helper = extract('function syncDurationTimeStep(', 'function syncTransientFields()');
function number(value, id = '') { return { value: String(value), id, min: '0.000000001',
  checkValidity() { return this.value !== '' && Number(this.value) >= 1e-9 && Number(this.value) <= 3600; },
  setCustomValidity(message) { this.error = message; },
}; }
const ctx = vm.createContext({}); vm.runInContext(helper, ctx);
for (const [duration, step, expected] of [[3600,.3,18],[60,1,1],[10,18,10],[3600,60,60],[1e-9,1,1e-9],[60,'',.3]]) {
  const d = number(duration), s = number(step);
  ctx.syncDurationTimeStep(d,s);
  assert.equal(Number(s.value), expected, `Duration ${duration}s with step ${step}s resolves to ${expected}s`);
  assert(duration / Number(s.value) <= 200 + 1e-9);
}
for (const duration of ['',0,-1,4000,'invalid']) {
  const d = number(duration), s = number(7);
  assert.equal(ctx.syncDurationTimeStep(d,s), false);
  assert.equal(s.value,'7','Invalid/incomplete duration stays available for correction without changing the other field');
}

// The scenario entry point synchronizes both the visible field and any open source draft.
const elements = { draftDuration: number(3600,'draftDuration'), draftTimeStep: number(.3,'draftTimeStep'),
  componentMaterialList: { contains: () => false }, confirmInputs: { checked: true } };
const collection = {timeWindow:{duration:60,timeStep:.3}};
const state = { draftEditSerial: 0 };
const queue = vm.createContext({ elements, state,
  selectedStudy: () => ({ plan: {}, confirmation: { status: 'needs_input' } }),
  activeSourceDraft: () => null, activeSourceCollection: () => collection,
  syncSourceEditor() {}, clearTimeout() {}, setTimeout() {return 1;},
  renderModeling() {}, renderPrimaryAction() {}, drawWorkpiece() {},
});
vm.runInContext(helper + extract('function queueDraftSave(', 'async function saveStructuredDraft()'), queue);
queue.queueDraftSave({target:elements.draftDuration});
assert.equal(elements.draftTimeStep.value,'18');
assert.equal(collection.timeWindow.duration,3600);
assert.equal(collection.timeWindow.timeStep,18,'Source editor must not put the previous invalid step back on the next render');
elements.draftTimeStep.value = '.001';
queue.queueDraftSave({target:elements.draftTimeStep});
assert.equal(elements.draftTimeStep.value,'.001','Explicit step edits are validated, never silently adjusted');

// The result-time dialog must preserve a valid explicit step, unlike the former unconditional duration/200 reset.
const handlers = {};
const dialogElements = {
  simulationDuration: { ...number(120), addEventListener: (type, listener) => {handlers.duration = listener;} },
  simulationTimeStep: number(1),
};
const dialog = vm.createContext({elements:dialogElements});
vm.runInContext(helper + extract('elements.simulationDuration.addEventListener(', 'elements.simulationTimeStep.addEventListener('),dialog);
handlers.duration(); assert.equal(dialogElements.simulationTimeStep.value,'1');
dialogElements.simulationDuration.value = '3600';
handlers.duration(); assert.equal(dialogElements.simulationTimeStep.value,'18');
assert.match(extract('function updateSourceDraftFromInputs(', 'function simulationOverridesFromDraft('), /event\?\.target === elements\.canvasDuration[\s\S]*syncDurationTimeStep\(elements\.canvasDuration, elements\.canvasTimeStep\)/);
console.log('Duration changes synchronize scenario/source/dialog step ranges while preserving explicit valid steps');
