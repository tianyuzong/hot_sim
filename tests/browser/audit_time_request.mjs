import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const app=readFileSync(process.env.AUDIT_APP || 'src/thermoflow/web/assets/app.js','utf8');
const start=app.indexOf('async function selectTimeStep('),end=app.indexOf('async function playNextTimeStep()',start);
assert(start>=0&&end>start);
const frame=index=>({study_id:'result',step:{index,time_s:index},surface:{temperature_k:[293.15+index]}});
const cached=frame(2);
const state={result:{study_id:'result',time_steps:[{index:0},{index:1},{index:2}]},timeFrames:new Map([['result:2',cached]]),timeRequest:0,timeIndex:0};
let completeRequest;
const ctx=vm.createContext({state,cancelScheduledTimeStep(){},renderTimeControls(){},renderResult(){},drawWorkpiece:()=>true,stopTimePlayback(){},showToast(){},
  request:()=>new Promise(resolve=>{completeRequest=resolve;})});
vm.runInContext(app.slice(start,end),ctx);
const oldRequest=ctx.selectTimeStep(1);
await ctx.selectTimeStep(2);
assert.equal(state.timeFrame,cached);
completeRequest(frame(1));
assert.equal(await oldRequest,false,'An earlier in-flight frame is invalidated even when the latest frame came from cache');
assert.equal(state.timeFrame,cached,'Scrubbing back to a cached frame must not jump forward when an old response arrives');
assert.equal(state.timeIndex,2);
console.log('Cached time-step selection supersedes earlier network frame requests');
