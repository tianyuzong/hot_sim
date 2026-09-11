import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const app=readFileSync('src/thermoflow/web/assets/app.js','utf8');
const code=app.slice(app.indexOf('async function request('),app.indexOf('async function loadWorkspace('));
let timer,cleared=false,signal,calls=0;
const ctx=vm.createContext({AbortController,setTimeout:fn=>{timer=fn;return 1;},clearTimeout:()=>{cleared=true;},
 fetch:async(path,options)=>{calls++;signal=options.signal;return{ok:true,json:()=>new Promise(()=>{})};}});
vm.runInContext(code,ctx);
const pending=ctx.request('/result');timer();
await assert.rejects(pending,error=>error.code==='request_timeout'&&/已完成/.test(error.message));
assert(signal.aborted);assert(cleared);assert.equal(calls,1);
cleared=false;const sending=ctx.request('/tasks',{method:'POST'});timer();
await assert.rejects(sending,error=>/可能已提交/.test(error.message));assert(cleared);assert.equal(calls,2,'A timeout must not retry a mutation');
ctx.fetch=async()=>({ok:true,json:async()=>({saved:true})});
assert.equal((await ctx.request('/result')).saved,true);assert(cleared);
console.log('Read/body timeouts abort and settle; mutation timeouts preserve ambiguity without duplicate submission');
