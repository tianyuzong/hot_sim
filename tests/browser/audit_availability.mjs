import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync('src/thermoflow/web/assets/app.js', 'utf8');
function block(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert(a >= 0 && b > a);
  return source.slice(a, b);
}

// A small DOM tree keeps actual rendered controls and their state. Availability
// logic is taken from the application; no fake renderer enables buttons for it.
class Node {
  constructor(tag = 'div', className = '', text = '') {
    this.tagName = tag.toUpperCase(); this.className = className; this.textContent = text;
    this.children = []; this.dataset = {}; this.attributes = {}; this.disabled = false;
    this.hidden = false; this.value = ''; this.isConnected = true;
    this.classList = {
      toggle: (name, on) => {
        const names = new Set(this.className.split(/\s+/).filter(Boolean));
        if (on) names.add(name); else names.delete(name);
        this.className = [...names].join(' ');
      },
      add: name => this.classList.toggle(name, true),
      remove: name => this.classList.toggle(name, false),
      contains: name => this.className.split(/\s+/).includes(name),
    };
  }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener() {}
  matches(selector) {
    const atom = selector.trim().split(/\s+/).at(-1);
    const tag = atom.match(/^[a-z]+/i)?.[0];
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    const id = atom.match(/#([\w-]+)/)?.[1];
    if (id && this.id !== id) return false;
    for (const match of atom.matchAll(/\.([\w-]+)/g)) {
      if (!this.classList.contains(match[1])) return false;
    }
    for (const match of atom.matchAll(/\[data-([\w-]+)\]/g)) {
      const key = match[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      if (!(key in this.dataset)) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',');
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
    return descendants.filter(child => selectors.some(item => item.trim() === '*' || child.matches(item)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
const createElement = (...args) => new Node(...args);
const ids = [
  'submitWorkpieceButton', 'materialsCreateDraftButton', 'generateDraftButton', 'applySourceButton',
  'runAgentButton', 'editMaterialsButton', 'editParametersButton', 'manualDraftButton',
  'materialConfirmationNotice', 'materialConfirmationCheck', 'confirmMaterials', 'materialsEmpty',
  'materialsGoScenario', 'componentMaterialList', 'materialDetail', 'componentTree', 'componentCount',
  'componentList', 'primaryAction', 'copyStudyButton', 'repairThinMeshButton', 'sourceEditor', 'unitForm',
];
const elements = Object.fromEntries(ids.map(id => { const node = new Node(); node.id = id; return [id, node]; }));
const unitSubmit = new Node('button'); unitSubmit.type = 'submit'; elements.unitForm.append(unitSubmit);
const documentRoot = new Node(); documentRoot.append(...Object.values(elements));
const state = {busy: false, studies: [], materials: [], hiddenComponentIds: new Set()};
let workpiece = {workpiece_id: 'wp-fresh', unit_confirmed: false, components: [
  {component_id: 'one', name: '组件 1', triangle_count: 12},
  {component_id: 'two', name: '组件 2', triangle_count: 12},
]};
let study = null, task = null;
const context = vm.createContext({state, elements, createElement,
  document: {createElement, querySelectorAll: selector => documentRoot.querySelectorAll(selector)},
  selectedWorkpiece: () => workpiece, selectedStudy: () => study, activeStudyTask: () => task,
  formatInteger: String, materialSourceSummary: () => '', renderMaterialDetail() {},
  materialAssignmentFromRow: row => ({material: row.baseMaterial, material_id: row.materialId}),
  renderComparisonTool() {}, renderPrimaryAction() {}, renderModeling() {},
  renderStructuredDraft() {}, renderMeshPanel() {}, renderGeometryPanel() {}, renderTimeControls() {},
  syncSourceConvectionControls() {},
});
vm.runInContext(block('function renderComponents(', 'function selectComponent('), context);
vm.runInContext(block('function renderMaterialsPanel(', 'function resetMaterialConfirmation('), context);
vm.runInContext(block('function setBusy(', 'function syncVisualizationModeButtons('), context);
const deletions = () => elements.componentList.querySelectorAll('.component-tool-danger');
const editButtons = () => elements.componentMaterialList.querySelectorAll('.component-material-edit');
const materialInputs = () => elements.componentMaterialList.querySelectorAll('input, select');

context.setBusy(true);
context.renderComponents(); context.renderMaterialsPanel();
assert.equal(deletions().length, 2);
assert(deletions().every(button => button.disabled), 'Busy import must lock component deletion');
context.setBusy(false);
assert(deletions().every(button => !button.disabled), 'Fresh multi-component geometry must unlock after import finishes');
assert.equal(elements.materialsCreateDraftButton.disabled, false, 'Unconfirmed scale must still allow entry to the unit-confirmation flow');

const material = {name: 'Aluminum', thermal_conductivity_w_m_k: 167, density_kg_m3: 2700, specific_heat_j_kg_k: 896};
workpiece.unit_confirmed = true;
study = {study_id: 'confirmed', workpiece_id: workpiece.workpiece_id, status: 'succeeded',
  confirmation: {status: 'confirmed'}, plan: {material, component_materials: []}};
state.studies = [study];
context.setBusy(true);
context.renderComponents(); context.renderMaterialsPanel();
const originalRows = [...elements.componentMaterialList.children];
assert.equal(editButtons().length, 2);
assert(editButtons().every(button => button.disabled));
context.setBusy(false);
assert.equal(elements.editMaterialsButton.disabled, false, 'Global material-copy entry must unlock after result loading');
assert.equal(elements.editParametersButton.disabled, false);
assert(editButtons().every(button => !button.disabled), 'Every cached component-copy entry must unlock after result loading');
assert.deepEqual(elements.componentMaterialList.children, originalRows, 'Availability refresh must reuse the current rows');
assert(materialInputs().every(input => input.disabled), 'Confirmed study material fields remain immutable');
assert(deletions().every(button => button.disabled), 'Geometry with saved studies must remain immutable');

task = {task_id: 'running', status: 'running'};
context.setBusy(false);
assert.equal(elements.editMaterialsButton.disabled, true, 'An active computation keeps material-copy entry locked');
assert(editButtons().every(button => button.disabled));
task = null;
context.setBusy(false);
assert(editButtons().every(button => !button.disabled), 'Completion must unlock all component-copy entries');

study = {...study, study_id: 'editable', status: 'needs_input', confirmation: {status: 'needs_input'}};
state.studies = [study];
context.renderMaterialsPanel();
const editableRows = [...elements.componentMaterialList.children];
const conductivity = elements.componentMaterialList.querySelector('input[data-material-property]');
conductivity.value = '0.08';
context.setBusy(true); context.setBusy(false);
assert.deepEqual(elements.componentMaterialList.children, editableRows, 'Busy refresh must not reconstruct an edited material form');
assert.equal(conductivity.value, '0.08', 'Unsaved custom conductivity must survive availability changes');

study = null; state.studies = []; workpiece = {...workpiece, components: workpiece.components.slice(0, 1)};
context.renderComponents(); context.setBusy(false);
assert.equal(deletions()[0].disabled, true, 'At least one component must remain');
console.log('Busy-state recovery unlocks permitted copy/delete actions and preserves immutable geometry, active-task locks, and edited rows');
