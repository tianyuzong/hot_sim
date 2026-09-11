import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const app=readFileSync('src/thermoflow/web/assets/app.js','utf8');
function extract(a,b) {const start=app.indexOf(a);assert(start>=0,a);const end=app.indexOf(b,start);assert(end>start,b);return app.slice(start,end);}
let workpiece={workpiece_id:'wp-one',unit_confirmed:false},study=null,tab,created=0,sourceOpened=0,geometryShown=0;
let resolveDraft;
const state={};
const ctx=vm.createContext({state,Promise,
  elements:{unitForm:{scrollIntoView(){}},lengthUnit:{focus(){}}},
  selectedWorkpiece:()=>workpiece,selectedStudy:()=>study,
  switchTab:t=>{tab=t;},renderGeometryPanel(){geometryShown++;},showToast(){},
  openSourceEditor(){sourceOpened++;},closeSourceEditor(){},
  createStudy:(id,purpose,overrides,mode)=>{
    assert.equal(mode,'manual','Opening inputs must not depend on a model service');
    assert.equal(id,workpiece.workpiece_id);created++;
    return new Promise(resolve=>{resolveDraft=()=>{study={plan:{},confirmation:{status:'needs_input'}};resolve(study);};});
  },
});
vm.runInContext(extract('function queueEditorAfterScale(', 'async function editComponentMaterial('),ctx);
await ctx.prepareStudyEditor('source');
assert.equal(created,0,'Unknown units must never be silently confirmed');
assert.equal(state.pendingInputTarget.target,'source');assert.equal(tab,'geometry');assert.equal(geometryShown,1);
workpiece.unit_confirmed=true;
const pending=ctx.prepareStudyEditor('source');
const repeated=ctx.prepareStudyEditor('materials');
assert.equal(created,1,'Repeated entry clicks must not create duplicate studies');
resolveDraft();await Promise.all([pending,repeated]);
assert.equal(tab,'materials');assert.equal(sourceOpened,0,'The latest requested editor wins');
assert.equal(state.inputPreparationPromise,null);assert.equal(state.pendingInputTarget,null);
await ctx.prepareStudyEditor('source');
assert.equal(sourceOpened,1);assert.equal(created,1);
assert.equal(study.confirmation.status,'needs_input','Opening editors must not confirm materials or start a solve');

// A successful unit confirmation resumes the original destination automatically.
let confirms=0,openedAfterScale=0;
const confirmState={pendingInputTarget:{workpieceId:'wp-one',target:'source'},pendingUploadOverrides:null};
const confirm=vm.createContext({state:confirmState,elements:{lengthUnit:{value:'mm'}},
  selectedWorkpiece:()=>workpiece,setBusy(){},resetStlView(){},loadWorkspace:async()=>{},
  request:async(path,options)=>{assert(path.endsWith('/unit'));assert.equal(JSON.parse(options.body).unit,'mm');confirms++;},
  createStudy:async()=>({status:'needs_input'}),switchTab(){},showToast(){},openSourceEditor(){openedAfterScale++;},
});
vm.runInContext(extract('async function confirmUnit(', 'function draftFieldsValid('),confirm);
await confirm.confirmUnit({preventDefault(){}});
assert.equal(confirms,1);assert.equal(openedAfterScale,1);assert.equal(confirmState.pendingInputTarget,null);
console.log('Unit prerequisite, automatic editor continuation, manual draft creation and duplicate-click protection passed');
