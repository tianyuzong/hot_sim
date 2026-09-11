import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("src/thermoflow/web/assets/app.js", "utf8");
const between = (a, b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)));
let finishLoading;
const slowResult = new Promise(resolve => {finishLoading = resolve;});
let initialRender = false;
const loading = vm.createContext({
  state: {},
  request: async path => path === '/health' ? {status: 'ok'} : [],
  selectedProject: () => null, selectedProjectWorkpieces: () => [],
  renderUploadMaterialOptions() {}, selectLatestStudy() {}, persistWorkspaceState() {},
  loadSelectedMesh: async () => {}, loadSelectedResult: () => slowResult,
  loadSelectedAgentRun: async () => {}, render: () => {initialRender = true;},
  loadSelectedValidation: async () => {},
  startHeatAnimation() {}, scheduleTaskPoll() {}, renderHealth() {},
  showToast: message => {throw new Error(message);},
});
vm.runInContext(between('async function loadWorkspace(', 'function renderUploadMaterialOptions('), loading);
const loadingPromise = loading.loadWorkspace();
await new Promise(resolve => setImmediate(resolve));
assert(initialRender, 'Projects and service health must render before the slow playback request completes');
finishLoading();
await loadingPromise;

const frameContext = vm.createContext({state: {
  timeFrame: {study_id: 'test', step: {temperature_min_k: 293.15, temperature_max_k: 293.15}},
}});
vm.runInContext(between('function displayedTimeResult(', 'function renderTimeControls('), frameContext);
assert.equal(frameContext.displayedTimeResult({study_id: 'test', heat_flux_w_m2: 12345}).heat_flux_w_m2, null,
  'An initial frame without computed flux must not inherit the final-frame heat flux');
const selection = vm.createContext({
  state: {selectedWorkpieceId: 'new-upload', selectedStudyId: null},
  selectedProjectStudies: () => [{study_id: 'old-study', workpiece_id: 'old-upload'}],
  selectedStudy: () => selection.state.selectedStudyId ? {workpiece_id: 'old-upload'} : null,
});
vm.runInContext(between('function selectLatestStudy(', 'async function loadSelectedResult('), selection);
selection.selectLatestStudy();
assert.equal(selection.state.selectedWorkpieceId, 'new-upload', 'Upload must not jump back to an older geometry that already has a study');
assert.equal(selection.state.selectedStudyId, null);
const study = {study_id: "study-test", status: "succeeded", plan: {}};
const state = {studies:[study],timeRequest: 0, timeFrames: new Map(), visualizationMode: "diffusion"};
const result = {study_id:study.study_id,time_steps: [{index: 0, time_s: 0}, {index: 1, time_s: 60}]};
const context = vm.createContext({
  AbortController, state, elements: {timeControls: {},resultEmpty:{},resultContent:{},retryResultButton:{},
    previousResultButton:{},resultStatusTitle:{},resultStatusMessage:{}}, selectedStudy: () => study,
  activeStudyTask:()=>null,render(){},renderPrimaryAction(){},
  stopTimePlayback() {}, cancelScheduledTimeStep() {}, renderTimeControls() {},
  clearComparison() {}, persistWorkspaceState() {},
  ThermoFlowTransient: {diffusionScale: () => ({low: 0, high: 10})},
  request: async url => url.endsWith('/result') ? result : {
    vertices: [[0, 0, 0]], frames: [
      {index: 0, time_s: 0, temperature_k: [293.15]},
      {index: 1, time_s: 60, temperature_k: [313.15]},
    ],
  },
  selectTimeStep: async i => {state.selectedFrame = i;},
  showToast: message => {throw new Error(message);},
});
vm.runInContext(between('async function loadSelectedResult(', 'async function loadSelectedMesh('), context);
await context.loadSelectedResult();
assert.equal(state.selectedFrame, 0, "Loading a transient result must show the actual initial state, not the final frame");
assert.equal(state.visualizationMode, "thermal", "The default must not subtract a changing minimum from the physical temperature");

let requestedCopy;
const copyContext = vm.createContext({
  state: {}, selectedStudy: () => study, setBusy() {},
  flushDraftBeforeNavigation: async () => true,
  request: async (url, options) => {requestedCopy = {url, body: JSON.parse(options.body)}; return {study_id: 'copy'};},
  loadWorkspace: async () => {}, showToast() {},
});
vm.runInContext(between('async function copySelectedStudy(', 'async function compareSelectedStudies('), copyContext);
await copyContext.copySelectedStudy('materials');
assert.equal(copyContext.state.activeTab, 'materials', 'Edit materials must remain on the material panel after creating an editable revision');
assert.equal(requestedCopy.url, '/v1/studies/study-test/copy', 'An edit must preserve the immutable original result');
let sourceTab;
const sourceContext = vm.createContext({
  state: {sourcePreviewDirty: true, sourceDraft: {studyId: "draft"}},
  elements: {sourceEditor: {reportValidity: () => true}},
  selectedStudy: () => ({study_id: 'draft', plan: {}, confirmation: {status: 'needs_input'}}),
  updateSourceDraftFromInputs() {}, flushDraftBeforeNavigation: async () => true,
  switchTab: value => {sourceTab = value;}, showToast() {}, closeSourceEditor() {},
});
vm.runInContext(between('function discardSourceDraft(', 'function resetSourceEditor('), sourceContext);
vm.runInContext(between('async function applySourceChanges(', 'function openWorkpieceDialog('), sourceContext);
await sourceContext.applySourceChanges({preventDefault() {}});
assert.equal(sourceTab, 'scenario', 'Applying sources must lead to the visible confirmation form');
console.log('Initial actual-temperature playback and editable material revision navigation passed');

assert.equal(sourceContext.state.sourcePreviewDirty, false, 'Saved heat inputs must stop perturbing the eventual solver field');
assert.equal(sourceContext.state.sourceDraft, null);
sourceContext.state.sourcePreviewDirty = true;
sourceContext.state.sourceDraft = {studyId: 'draft'};
sourceContext.flushDraftBeforeNavigation = async () => false;
await sourceContext.applySourceChanges({preventDefault() {}});
assert.equal(sourceContext.state.sourcePreviewDirty, true, 'A failed save must preserve the unsaved preview');
assert(sourceContext.state.sourceDraft);

let confirmationFails = false;
let submitted = 0;
const confirmState = {selectedStudyId: 'draft', sourcePreviewDirty: true, sourceDraft: {studyId: 'draft'}, draftBaseRevision: 0};
const confirmStudy = {study_id: 'draft', draft_revision: 0};
const confirmation = vm.createContext({
  state: confirmState, selectedStudy: () => confirmStudy,
  elements: {structuredInputs: {reportValidity: () => true}, confirmMaterials: {checked: true},
             confirmInputs: {checked: true},
             componentMaterialList: {querySelectorAll: () => []}, studyPurpose: {value: 'heating demonstration'}},
  renderValidationFeedback: () => [], flushDraftBeforeNavigation: async () => true,
  loadSelectedValidation: async () => true, setBusy() {}, confirmedOverrides: () => ({}),
  closeSourceEditor() {}, showToast() {}, renderPrimaryAction() {},
  request: async () => {if (confirmationFails) throw new Error('confirmation failed');},
  loadWorkspace: async () => {
    assert.equal(confirmState.sourcePreviewDirty, false, 'Confirmed inputs must clear the preview before loading results');
    assert.equal(confirmState.sourceDraft, null);
  },
  submitComputation: async () => {submitted++;},
});
vm.runInContext(between('function discardSourceDraft(', 'function resetSourceEditor('), confirmation);
vm.runInContext(between('async function confirmStudy(', 'async function generateMesh('), confirmation);
await confirmation.confirmStudy('draft');
assert.equal(submitted, 1);
confirmationFails = true;
confirmState.sourcePreviewDirty = true;
confirmState.sourceDraft = {studyId: 'draft'};
await confirmation.confirmStudy('draft');
assert.equal(submitted, 1, 'Failed confirmation must not submit another task');
assert.equal(confirmState.sourcePreviewDirty, true);
assert(confirmState.sourceDraft);
console.log('Source application and input confirmation clear preview state only after successful persistence');
