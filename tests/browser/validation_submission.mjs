import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync('src/thermoflow/web/assets/app.js', 'utf8');
const between = (a, b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)));
let finishValidation;
let selected = {study_id: 'current', draft_revision: 5, plan: {}, policy: {issues: []}};
let submissions = 0;
let validationRequests = 0;
const context = vm.createContext({
  state: {}, selectedStudy: () => selected,
  renderValidationFeedback: () => context.state.validationFailure
    ? [{severity: 'error', fields: []}] : selected.policy.issues,
  flushDraftBeforeNavigation: async () => true,
  locateValidationFields() {}, showToast() {}, switchTab() {}, setBusy() {},
  confirmedOverrides: () => ({}), loadWorkspace: async () => {}, submitComputation: async () => {},
  elements: {structuredInputs: {reportValidity: () => true}, confirmMaterials: {checked: true},
    componentMaterialList: {querySelectorAll: () => []}, studyPurpose: {value: ''}},
  request: async (url, options) => {
    if (options?.method === 'POST') {submissions++; return {};}
    assert.equal(new URL(url, 'http://localhost').searchParams.get('expected_revision'), '5');
    validationRequests++;
    return new Promise(resolve => {finishValidation = resolve;});
  },
});
vm.runInContext(between('async function loadSelectedValidation(', 'async function request('), context);
vm.runInContext(between('async function confirmStudy(', 'async function generateMesh('), context);
const pending = context.confirmStudy('current');
await new Promise(resolve => setImmediate(resolve));
assert.equal(validationRequests, 1, 'Confirmation must fetch current revision validation, not trust the cached policy');
assert.equal(submissions, 0, 'Nothing can be submitted while validation is pending');
finishValidation({issues: [{severity: 'error', fields: ['boundaries.0.selector']}]});
await pending;
assert.equal(submissions, 0, 'A newly discovered conflict must block confirmation');

selected.policy = {issues: []};
const changed = context.confirmStudy('current');
await new Promise(resolve => setImmediate(resolve));
selected = {...selected, study_id: 'different'};
finishValidation({issues: []});
await changed;
assert.equal(submissions, 0, 'Changing selection during validation must cancel confirmation');

let failValidation;
context.request = () => new Promise((resolve, reject) => {failValidation = reject;});
const failed = context.loadSelectedValidation();
selected = {...selected, draft_revision: 6};
failValidation(new Error('old connection error'));
await failed;
assert.equal(context.state.validationFailure, null, 'An obsolete request must not attach a failure to a newer revision');
console.log('Confirmation awaits fresh validation and ignores obsolete selection/revision responses.');
