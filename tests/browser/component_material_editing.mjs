import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const app = readFileSync('src/thermoflow/web/assets/app.js', 'utf8');
const between = (a,b) => app.slice(app.indexOf(a),app.indexOf(b,app.indexOf(a)));
const ctx = vm.createContext({});
vm.runInContext(between('function materialAssignmentFromRow(', 'function materialSourceSummary('),ctx);
const base = {name:'Catalog material',thermal_conductivity_w_m_k:167,density_kg_m3:2700,specific_heat_j_kg_k:896,source_type:'catalog',source_reference:'catalog'};
const rows = Array.from({length:5},(_,i)=>({
  baseMaterial: {...base}, materialId:'catalog', dataset:{componentId:`component-${i+1}`},
  name:base.name, values:{...base},
  querySelector(){return {value:this.name};},
  querySelectorAll(){return ['thermal_conductivity_w_m_k','density_kg_m3','specific_heat_j_kg_k'].map(p=>({dataset:{materialProperty:p},value:this.values[p]}));},
}));
const original=rows.map(r=>JSON.stringify(ctx.materialAssignmentFromRow(r)));
rows[2].name='Torso fabric'; rows[2].values.thermal_conductivity_w_m_k=.08;
rows[2].values.density_kg_m3=380; rows[2].values.specific_heat_j_kg_k=1300;
const updated=rows.map(r=>ctx.materialAssignmentFromRow(r));
for(const i of [0,1,3,4]) assert.equal(JSON.stringify(updated[i]),original[i],'Editing one component must not change its neighbors');
assert.equal(updated[2].material.name,'Torso fabric');
assert.equal(updated[2].material.thermal_conductivity_w_m_k,.08);
assert.equal(updated[2].material_id,null);
assert.equal(updated[2].material.source_type,'user');
assert.equal(updated[2].material.source_reference,null,'Edited values must not retain catalog provenance');
rows[1].name='Name-only override';
assert.equal(ctx.materialAssignmentFromRow(rows[1]).material_id,null);

let workpiece={unit_confirmed:true}, study={plan:{},confirmation:{status:'needs_input'}},opened=0,prepared=0,tab;
const control=()=>({hidden:true,setAttribute(){},classList:{add(){},remove(){}}});
const elements={sourceEditButton:control(),heatSourceNav:control(),sourceEditor:control(),sourceEditorSlot:control(),draftEnableHeatSource:{checked:false},canvasSourceName:{focus(){}}};
const heat=vm.createContext({elements,state:{},selectedWorkpiece:()=>workpiece,selectedStudy:()=>study,
  activeSourceDraft:()=>{opened++; return {};},syncSourceEditor(){},positionSourceEditor(){},
  activeSourceCollection:()=>({sources:[],activeIndex:0}),
  switchTab:t=>{tab=t;},showToast(){},clearComparison(){},
  queueEditorAfterScale:()=>{tab='geometry';},prepareStudyEditor:()=>{prepared++;}});
vm.runInContext(between('function openSourceEditor(', 'function discardSourceDraft('),heat);
heat.openSourceEditor();
assert.equal(opened,1,'A draft with no enabled heat source must still open the settings');
assert.equal(elements.sourceEditor.hidden,false);
assert.equal(elements.draftEnableHeatSource.checked,false,'Viewing source settings must not enable an unrequested default source');
heat.closeSourceEditor(); assert.equal(elements.sourceEditor.hidden,true);
workpiece.unit_confirmed=false; heat.openSourceEditor();
assert.equal(opened,1); assert.equal(tab,'geometry','Unconfirmed units must lead to the visible scale form');
workpiece.unit_confirmed=true; study=null; heat.openSourceEditor();
assert.equal(prepared,1,'Opening source settings prepares an editable draft automatically'); assert.equal(opened,1);
console.log('Five independent component material values/names and discoverable empty-source entry passed');
