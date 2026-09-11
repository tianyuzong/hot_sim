import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const app=readFileSync('src/thermoflow/web/assets/app.js','utf8');
const extract=(a,b)=>app.slice(app.indexOf(a),app.indexOf(b,app.indexOf(a)));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const study={study_id:'done',workpiece_id:'wp',project_id:'p',status:'succeeded'};
let selected=study,active=null;
const state={studies:[study],selectedProjectId:'p',selectedWorkpieceId:'wp',timeRequest:0,timeFrames:new Map(),timeIndex:0};
const elements=Object.fromEntries(['timeControls','resultEmpty','resultContent','retryResultButton','previousResultButton','resultStatusTitle','resultStatusMessage'].map(k=>[k,{}]));
const result={study_id:'done',time_steps:[{index:0,time_s:0,temperature_min_k:293.15,temperature_max_k:293.15},{index:1,time_s:60,temperature_min_k:293.15,temperature_max_k:310}]};
let bulk=deferred(),bulkOptions,metadataCalls=0,rendered=false;
const ctx=vm.createContext({state,elements,AbortController,selectedStudy:()=>selected,activeStudyTask:()=>active,
 stopTimePlayback(){},cancelScheduledTimeStep(){},renderTimeControls(){},clearComparison(){},renderPrimaryAction(){},showToast(){},
 render(){rendered=state.result===result;},ThermoFlowTransient:{diffusionScale:()=>({low:0,high:10})},
 selectTimeStep:async i=>{state.timeIndex=i;state.timeFrame=state.timeFrames.get(`done:${i}`)||{study_id:'done',step:result.time_steps[i]};return true;},
 request:async(path,options)=>{if(path.endsWith('/result')){metadataCalls++;return result;}bulkOptions=options;return bulk.promise;},
});
vm.runInContext(extract('async function loadSelectedResult(', 'async function loadSelectedMesh('),ctx);
await ctx.loadSelectedResult();
assert(rendered,'Result metrics must render without waiting for the pending bulk download');
assert.equal(state.resultLoading,false);assert.equal(state.playbackLoading,true);
assert.equal(state.timeFrame.step.index,0,'Initial metrics are from t=0, not the final frame');
assert.equal(elements.resultEmpty.hidden,true);
await ctx.loadSelectedResult();
assert.equal(metadataCalls,1,'Workspace refresh preserves an already loaded immutable result and its playback request');
// A user-selected time/view must survive a slow bulk response.
state.timeIndex=1;state.visualizationMode='slice';
bulk.resolve({vertices:[[0,0,0]],frames:[{index:0,time_s:0,temperature_k:[293.15]},{index:1,time_s:60,temperature_k:[310]}]});
await tick();
assert.equal(state.timeIndex,1);assert.equal(state.visualizationMode,'slice');
assert.equal(state.playbackStudyId,'done');assert.equal(state.playbackLoading,false);
// Reloading after a bulk error remains a read action and preserves the valid summary.
state.playbackError='slow network';bulk=deferred();
await ctx.loadSelectedResult();assert.equal(metadataCalls,1);assert.equal(state.playbackLoading,true);
bulk.reject(new Error('download failed'));await tick();
assert.equal(state.result,result);assert.equal(state.resultLoading,false);assert.match(state.playbackError,/download failed/);
// Switching away aborts the large transfer and prevents its completion from changing the new study.
bulk=deferred();await ctx.loadSelectedResult();
const oldSignal=bulkOptions.signal;
selected={study_id:'new',workpiece_id:'wp',project_id:'p',status:'running'};active={operation:'solve'};
await ctx.loadSelectedResult();
assert.equal(oldSignal.aborted,true);assert.equal(state.result,null);
assert.match(elements.resultStatusTitle.textContent,/正在求解/);assert.equal(elements.previousResultButton.hidden,false);
bulk.resolve({vertices:[[0,0,0]],frames:[]});await tick();assert.equal(state.result,null);
// Summary-read errors remain visibly retryable and do not claim that a completed study has no result.
active=null;selected=study;ctx.request=async()=>{throw new Error('connection timed out');};
await ctx.loadSelectedResult();
assert.equal(state.resultLoading,false);assert.match(elements.resultStatusTitle.textContent,/求解已完成/);
assert.equal(elements.retryResultButton.hidden,false);assert.match(elements.resultStatusMessage.textContent,/connection timed out/);
// Accepted tasks release the global button lock even if the ensuing workspace refresh is slow.
const workspace=deferred();const submitState={selectedStudyId:'new',tasks:[]};let busy=false;
const submit=vm.createContext({state:submitState,setBusy:v=>{busy=v;},scheduleTaskPoll(){},showToast(){},
 request:async()=>({task_id:'t',study_id:'new',status:'queued'}),loadWorkspace:()=>workspace.promise});
vm.runInContext(extract('async function submitComputation(', 'async function cancelComputation('),submit);
const sending=submit.submitComputation('new','solve');await tick();
assert.equal(submitState.tasks.length,1);assert.equal(busy,false,'An accepted task must not stay in generic busy while reading workspace metadata');
workspace.resolve();await sending;
console.log('Result summary/initial frame precede bulk playback; navigation, retry, progress and accepted-task locks recover correctly');
