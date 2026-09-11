import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const app=readFileSync(process.env.AUDIT_APP||'src/thermoflow/web/assets/app.js','utf8');
const extract=(a,b)=>{const start=app.indexOf(a),end=app.indexOf(b,start);assert(start>=0&&end>start);return app.slice(start,end);};
const clone=value=>JSON.parse(JSON.stringify(value));
const initial={studyId:'draft',activeIndex:0,sources:[{name:'Heater',power:10,radius:2,center:{x:1,y:2,z:3},end:{x:2,y:2,z:3}}],
  ambientTemperature:293.15,convectionCoefficient:10,timeWindow:{duration:60,timeStep:1}};
const state={sourceDraft:clone(initial),sourcePreviewDirty:false};
const controls={};
const elements=new Proxy(controls,{get(target,key){return target[key] ||= {value:'',checked:false,hidden:true,focus(){},setAttribute(){},classList:{add(){},remove(){}}};}});
elements.draftEnableHeatSource.checked=false;
const study={study_id:'draft',plan:{analysis_type:'transient_conduction',component_materials:[{name:'User material'}]},confirmation:{status:'needs_input'}};
let saves=0;
const context=vm.createContext({state,elements,
  selectedStudy:()=>study,selectedWorkpiece:()=>({unit_confirmed:true}),
  activeSourceCollection:()=>state.sourceDraft,activeSourceDraft:()=>state.sourceDraft.sources[state.sourceDraft.activeIndex],
  syncSourceEditor(){const collection=state.sourceDraft;elements.canvasDuration.value=String(collection.timeWindow.duration);elements.canvasTimeStep.value=String(collection.timeWindow.timeStep);elements.canvasAmbientTemperature.value=String(collection.ambientTemperature);elements.canvasSourcePower.value=String(collection.sources[collection.activeIndex].power);},
  positionSourceEditor(){},startHeatAnimation(){},showToast(){},queueDraftSave(){saves++;},
});
vm.runInContext(extract('function openSourceEditor()', 'function selectHeatSource('),context);
context.openSourceEditor();
state.sourceDraft.sources[0].power=80;
state.sourceDraft.timeWindow={duration:3600,timeStep:18};
state.sourceDraft.ambientTemperature=320;
state.sourceDraft.convectionCoefficient=30;
elements.draftEnableHeatSource.checked=true;
state.sourcePreviewDirty=true;
study.plan.component_materials[0].name='Independent material edit';
study.plan.heat_source={total_power_w:80}; // Simulates the autosave having already updated the authoritative plan.
context.openSourceEditor();
context.resetSourceEditor();
assert.equal(state.sourceDraft.sources[0].power,10,'Undo must restore opening values even after an autosave changed the plan');
assert.equal(state.sourceDraft.timeWindow.duration,60);
assert.equal(elements.canvasDuration.value,'60');
assert.equal(elements.draftDuration.value,'60','Both duration editors must agree after Undo');
assert.equal(elements.draftTimeStep.value,'1');
assert.equal(elements.draftAmbient.value,'293.15');
assert.equal(elements.draftConvection.value,'10');
assert.equal(elements.draftSourcePower.value,'10');
assert.equal(elements.draftSourceRadius.value,'2');
assert.equal(elements.draftEnableHeatSource.checked,false,'Undo restores the original disabled heat state');
assert.equal(study.plan.component_materials[0].name,'Independent material edit','Heat undo cannot roll back unrelated material edits');
assert.equal(saves,1,'Unconfirmed undo must persist its restored values');
context.closeSourceEditor();
assert.equal(state.sourceEditorBaseline,null);
state.sourceDraft.sources[0].power=25;
context.openSourceEditor();
state.sourceDraft.sources[0].power=99;
study.confirmation.status='confirmed';
context.resetSourceEditor();
assert.equal(state.sourceDraft.sources[0].power,25,'Reopening starts a fresh undo session');
assert.equal(saves,1,'Undoing a confirmed result preview cannot write to its immutable study');

const mutableOverrides={heat_sources:[{total_power_w:10}],component_materials:[{material:{thermal_conductivity_w_m_k:167}}]};
const requests=[];
const apply=vm.createContext({state:{},elements:{sourceEditor:{reportValidity:()=>true}},
  selectedStudy:()=>({study_id:'solved',plan:{},confirmation:{status:'confirmed'}}),activeSourceDraft:()=>({}),
  updateSourceDraftFromInputs(){},setBusy(){},simulationOverridesFromDraft:()=>mutableOverrides,
  request:async(path,options)=>{requests.push(JSON.parse(options.body));if(path.endsWith('/copy')){mutableOverrides.heat_sources[0].total_power_w=999;mutableOverrides.component_materials[0].material.thermal_conductivity_w_m_k=999;return{study_id:'copy',status:'needs_input'};}return{};},
  submitComputation:async()=>({}),discardSourceDraft(){},showToast(){},
});
vm.runInContext(extract('async function applySourceChanges(', 'function openWorkpieceDialog()'),apply);
await apply.applySourceChanges({preventDefault(){}});
assert.equal(requests.length,2);
assert.deepEqual(requests[0].overrides,requests[1].overrides,'Copy and confirm must receive one immutable payload despite concurrent UI edits');
assert.equal(requests[1].overrides.heat_sources[0].total_power_w,10);
console.log('Source session undo restores both editors, preserves other material edits, and uses immutable Apply payloads');
