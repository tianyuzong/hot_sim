import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const app=readFileSync(process.env.AUDIT_APP||'src/thermoflow/web/assets/app.js','utf8');
const extract=(a,b)=>{const start=app.indexOf(a),end=app.indexOf(b,start);assert(start>=0&&end>start);return app.slice(start,end);};
const functions=extract('async function recoverCopiedStudy(', 'function stopTimePlayback()')
  +extract('async function applySourceChanges(', 'function openWorkpieceDialog()');
function fixture(confirmed=true) {
  const original={study_id:'original',project_id:'project',workpiece_id:'workpiece',plan:{initial_temperature_k:293.15},confirmation:{status:confirmed?'confirmed':'needs_input'}};
  const other={study_id:'other',plan:{},confirmation:{status:'needs_input'}};
  const copy={study_id:'copy',project_id:'project',workpiece_id:'workpiece',status:'needs_input',plan:{},confirmation:{status:'needs_input'}};
  const state={selectedStudyId:original.study_id,studies:[original,other],navigationRequest:0,sourceDraft:{user:'original source'}};
  const calls=[],toasts=[],tasks=[];
  let discards=0,closed=0,flushed=0;
  const context=vm.createContext({state,
    elements:{sourceEditor:{reportValidity:()=>true},simulationTimeDialog:{dataset:{studyId:'original'},close(){closed++;}},
      simulationTimeStep:{value:'1',setCustomValidity(){},reportValidity:()=>true},simulationDuration:{value:'120'},simulationTimeForm:{reportValidity:()=>true},applySimulationTime:{}},
    selectedStudy:()=>state.studies.find(study=>study.study_id===state.selectedStudyId),
    activeSourceDraft:()=>({}),updateSourceDraftFromInputs(){},simulationOverridesFromDraft:()=>({heat_sources:[{total_power_w:10}]}),
    setBusy(value){state.busy=value;},discardSourceDraft(){state.sourceDraft=null;discards++;},switchTab(value){state.activeTab=value;},
    showToast(message){toasts.push(message);},render(){},
    flushDraftBeforeNavigation:async()=>{flushed++;return true;},
    loadWorkspace:async()=>{if(!state.studies.some(study=>study.study_id==='copy'))state.studies.push(copy);},
    request:async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return path.endsWith('/copy')?copy:{};},
    submitComputation:async(id,operation)=>{tasks.push({id,operation});return{};},
  });
  vm.runInContext(functions,context);
  return {context,state,calls,toasts,tasks,copy,original,get discards(){return discards;},get closed(){return closed;},get flushed(){return flushed;}};
}
for (const action of ['applySourceChanges','applySimulationTime']) {
  const f=fixture(); let resolveCopy;
  f.context.request=async(path,options)=>{f.calls.push({path,body:JSON.parse(options.body)});if(path.endsWith('/copy'))return new Promise(resolve=>{resolveCopy=resolve;});return{};};
  const pending=f.context[action]({preventDefault(){}});
  assert(resolveCopy,action);
  f.state.selectedStudyId='other';f.state.navigationRequest++;
  f.state.sourceDraft={user:'new study edits'};
  resolveCopy(f.copy);await pending;
  assert.equal(f.state.selectedStudyId,'other',`${action} cannot steal selection after navigating to another study`);
  assert.deepEqual(f.state.sourceDraft,{user:'new study edits'});
  assert.equal(f.discards,0);
  assert.deepEqual(f.tasks,[{id:'copy',operation:'apply_and_solve'}],'The already authorized original computation still runs');
  assert.equal(f.calls[1].path,'/v1/studies/copy/confirm');
}
const draft=fixture(false);
draft.context.flushDraftBeforeNavigation=async()=>{
  draft.state.studies[0]={...draft.original,plan:{initial_temperature_k:315}};
  return true;
};
await draft.context.applySimulationTime({preventDefault(){}});
assert.equal(draft.calls.length,1,'An unconfirmed draft must not auto-confirm material choices through the time dialog');
assert.equal(draft.calls[0].body.overrides.initial_temperature_k,315,'Copy uses the flushed current initial temperature');
assert.equal(draft.tasks.length,0);
assert.equal(draft.state.selectedStudyId,'copy');
assert.equal(draft.state.activeTab,'scenario');

for (const navigate of [false,true]) {
  const f=fixture();
  f.context.request=async(path,options)=>{
    f.calls.push({path,body:JSON.parse(options.body)});
    if(path.endsWith('/copy'))return f.copy;
    if(navigate){f.state.selectedStudyId='other';f.state.navigationRequest++;f.state.sourceDraft={user:'safe'};}
    throw new Error('confirmation rejected');
  };
  await f.context.applySourceChanges({preventDefault(){}});
  assert(f.state.studies.some(study=>study.study_id==='copy'),'A failed confirmation must leave the created copy discoverable');
  assert(f.toasts.some(text=>text.includes('已保留副本')));
  assert.equal(f.tasks.length,0);
  assert.equal(f.state.selectedStudyId,navigate?'other':'copy');
  if(!navigate)assert.equal(f.state.activeTab,'scenario','Recovery should open the copied editable inputs');
  else assert.deepEqual(f.state.sourceDraft,{user:'safe'});
}
console.log('Source/time copy actions preserve navigation, require draft material confirmation, and recover created copies after errors');
