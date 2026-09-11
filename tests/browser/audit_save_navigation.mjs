import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync('src/thermoflow/web/assets/app.js','utf8');
function block(start,end){const a=source.indexOf(start),b=source.indexOf(end,a);assert(a>=0&&b>a);return source.slice(a,b);}
const state={selectedStudyId:'a',draftDirty:true,draftEditSerial:1,draftBaseRevision:2};
let failures=1,calls=0,editedValue=25;
const study={study_id:'a',plan:{},confirmation:{status:'needs_input'}};
const recovery=vm.createContext({state,elements:{draftSaveStatus:{},studyPurpose:{value:'manual heat'}},clearTimeout,
  selectedStudy:()=>study,draftFieldsValid:()=>true,confirmedOverrides:()=>({heat_source_power_w:editedValue}),
  renderModeling(){},renderPrimaryAction(){},renderValidationFeedback(){},showToast(){},
  rememberModelingStudy(updated){state.draftBaseRevision=updated.draft_revision;},
  request:async(path,options)=>{
    calls++;
    if(failures-->0)throw Object.assign(new Error('connection lost'),{status:503});
    assert.equal(JSON.parse(options.body).overrides.heat_source_power_w,editedValue);
    return {draft_revision:3};
  },
});
vm.runInContext(block('async function saveStructuredDraft(', 'async function flushDraftBeforeNavigation('),recovery);
await assert.rejects(recovery.saveStructuredDraft(),/connection lost/);
assert.equal(state.draftDirty,true,'A failed request must preserve the user edit');
assert.equal(state.draftSaveConflict,false,'A network failure must not require discarding edits');
editedValue=37;
await recovery.saveStructuredDraft();
assert.equal(calls,2,'Retry must actually send the preserved/corrected fields');
assert.equal(state.draftDirty,false);assert.equal(state.draftSaveError,false);
state.draftDirty=true;
recovery.request=async()=>{throw Object.assign(new Error('newer server draft'),{status:409});};
await assert.rejects(recovery.saveStructuredDraft(),/newer server draft/);
assert.equal(state.draftSaveConflict,true);
let overwrites=0;
recovery.request=async()=>{overwrites++;return{};};
await assert.rejects(recovery.saveStructuredDraft(),/服务器草案/);
assert.equal(overwrites,0,'A revision conflict must not overwrite another editor');

// Start a refresh on project A, select B before the list request finishes.
const ws={selectedProjectId:'pa',selectedWorkpieceId:'wa',selectedStudyId:'sa',studies:[]};
let releaseLists;
const slowLists=new Promise(resolve=>{releaseLists=resolve;});
const projects=[{project_id:'pa'},{project_id:'pb'}];
const workpieces=[{project_id:'pa',workpiece_id:'wa'},{project_id:'pb',workpiece_id:'wb'}];
const refresh=vm.createContext({state:ws,
  request:async path=>{
    if(path==='/health')return{status:'ok'};
    if(path==='/v1/projects')return projects;
    if(path==='/v1/workpieces'){await slowLists;return workpieces;}
    if(path==='/v1/studies')return[{study_id:'sa',draft_revision:1},{study_id:'sb',draft_revision:1}];
    return[];
  },
  renderUploadMaterialOptions(){},selectedProject:()=>projects.find(p=>p.project_id===ws.selectedProjectId),
  selectedProjectWorkpieces:()=>workpieces.filter(w=>w.project_id===ws.selectedProjectId),
  selectLatestStudy(id){ws.selectedStudyId=id;},persistWorkspaceState(){},
  loadSelectedMesh:async()=>{},loadSelectedResult:async()=>{},loadSelectedAgentRun:async()=>{},loadSelectedValidation:async()=>{},
  render(){},startHeatAnimation(){},scheduleTaskPoll(){},renderHealth(){},showToast(m){throw Error(m);},
});
vm.runInContext(block('async function loadWorkspace(', 'function renderUploadMaterialOptions('),refresh);
const pendingRefresh=refresh.loadWorkspace();
ws.selectedProjectId='pb';ws.selectedWorkpieceId='wb';ws.selectedStudyId='sb';
ws.studies=[{study_id:'sb',draft_revision:3,plan:{purpose:'latest saved edit'}}];
releaseLists();await pendingRefresh;
assert.equal(ws.selectedProjectId,'pb');assert.equal(ws.selectedWorkpieceId,'wb');assert.equal(ws.selectedStudyId,'sb');
assert.equal(ws.studies.find(s=>s.study_id==='sb').draft_revision,3,'Refresh must not regress an acknowledged save');

const recoveryStudy={study_id:'failed',status:'failed',plan:{},confirmation:{status:'confirmed'},mesh_status:'ready'};
let retried=null;
const primary=vm.createContext({state:{activeTab:'solve'},selectedStudy:()=>recoveryStudy,
  selectedWorkpiece:()=>({unit_confirmed:true,cad_format:'stl'}),activeStudyTask:()=>null,
  runStudy:async id=>{retried=id;},elements:{primaryAction:{}},switchTab(){},showToast(){},
});
vm.runInContext(block('function renderPrimaryAction(', 'async function createStudy('),primary);
primary.renderPrimaryAction();assert.equal(primary.elements.primaryAction.textContent,'重试求解');
await primary.handlePrimaryAction();assert.equal(retried,'failed');
console.log('Autosave recovery, conflict protection, refresh selection/revision ownership and failed-solve retry passed');
