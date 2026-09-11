import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const app = readFileSync('src/thermoflow/web/assets/app.js', 'utf8');
function block(start, end) {
  const a = app.indexOf(start), b = app.indexOf(end, a);
  assert(a >= 0 && b > a);
  return app.slice(a, b);
}
function fixture() {
  let releaseValidation, validationCalls = 0, postCalls = 0, computationCalls = 0;
  const state = {selectedStudyId: 'draft', draftBaseRevision: 3, activeTab: 'scenario', busy: false};
  const study = {study_id: 'draft', draft_revision: 3, status: 'needs_input',
    confirmation: {status: 'needs_input'}, plan: {}};
  const elements = {primaryAction: {},
    confirmInputs: {checked: true, focus() {}}, confirmMaterials: {checked: true, focus() {}},
    structuredInputs: {reportValidity: () => true}, componentMaterialList: {querySelectorAll: () => []},
    studyPurpose: {value: 'Explicit fixture heating inputs'},
  };
  const ctx = vm.createContext({state, elements, selectedStudy: () => study,
    selectedWorkpiece: () => ({unit_confirmed: true, cad_format: 'stl'}), activeStudyTask: () => null,
    renderValidationFeedback: () => [], flushDraftBeforeNavigation: async () => true,
    loadSelectedValidation: async () => {
      validationCalls++;
      return new Promise(resolve => {releaseValidation = resolve;});
    },
    locateValidationFields() {}, showToast() {}, switchTab(tab) {state.activeTab = tab;},
    setBusy(value) {state.busy = value;}, discardSourceDraft() {},
    confirmedOverrides: () => ({duration_s: 60}), loadWorkspace: async () => {},
    request: async (path, options) => {
      assert.equal(path, '/v1/studies/draft/confirm');
      assert.equal(options.method, 'POST');
      assert.equal(JSON.parse(options.body).expected_revision, 3);
      assert.equal(JSON.parse(options.body).materials_confirmed, true);
      postCalls++;
    },
    submitComputation: async (id, operation) => {
      assert.equal(id, 'draft'); assert.equal(operation, 'apply_and_solve'); computationCalls++;
    },
  });
  vm.runInContext(block('function renderPrimaryAction(', 'async function handlePrimaryAction('), ctx);
  vm.runInContext(block('async function confirmStudy(', 'async function generateMesh('), ctx);
  return {ctx, state, study, elements, release: value => releaseValidation(value),
    counts: () => ({validationCalls, postCalls, computationCalls})};
}

const duplicate = fixture();
const first = duplicate.ctx.confirmStudy('draft');
const second = duplicate.ctx.confirmStudy('draft');
await new Promise(resolve => setImmediate(resolve));
assert.equal(duplicate.state.confirmingStudyId, 'draft');
assert.equal(duplicate.elements.primaryAction.disabled, true, 'The primary button must be locked while fresh validation is pending');
assert.deepEqual(duplicate.counts(), {validationCalls: 1, postCalls: 0, computationCalls: 0});
duplicate.release(true);
await Promise.all([first, second]);
assert.deepEqual(duplicate.counts(), {validationCalls: 1, postCalls: 1, computationCalls: 1},
  'Rapid repeated clicks must produce one confirmation and one compute submission');
assert.equal(duplicate.state.confirmingStudyId, null);

for (const change of ['uncheck_inputs', 'uncheck_materials', 'edit_pending', 'saved_revision_changed']) {
  const current = fixture();
  const pending = current.ctx.confirmStudy('draft');
  await new Promise(resolve => setImmediate(resolve));
  if (change === 'uncheck_inputs') current.elements.confirmInputs.checked = false;
  if (change === 'uncheck_materials') current.elements.confirmMaterials.checked = false;
  if (change === 'edit_pending') current.state.draftDirty = true;
  if (change === 'saved_revision_changed') current.study.draft_revision++;
  current.release(true);
  await pending;
  assert.deepEqual(current.counts(), {validationCalls: 1, postCalls: 0, computationCalls: 0},
    `${change} during validation must prevent confirming changed or unapproved inputs`);
  assert.equal(current.state.confirmingStudyId, null, 'A canceled confirmation must release its pending guard');
}

const failed = fixture();
const failedAttempt = failed.ctx.confirmStudy('draft');
await new Promise(resolve => setImmediate(resolve));
failed.release(false);
await failedAttempt;
assert.equal(failed.state.confirmingStudyId, null, 'Unavailable validation must release the guard so the user can retry');
const retried = failed.ctx.confirmStudy('draft');
await new Promise(resolve => setImmediate(resolve));
failed.release(true);
await retried;
assert.deepEqual(failed.counts(), {validationCalls: 2, postCalls: 1, computationCalls: 1});
console.log('Pending confirmation blocks duplicate requests, rechecks current consent/revision, and allows retry after validation failure');
