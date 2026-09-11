import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const app=readFileSync(process.env.AUDIT_APP||'src/thermoflow/web/assets/app.js','utf8');
const extract=(a,b)=>{const start=app.indexOf(a),end=app.indexOf(b,start);assert(start>=0&&end>start);return app.slice(start,end);};
const requestContext=vm.createContext({AbortController,setTimeout,clearTimeout,fetch:async()=>({ok:false,status:422,statusText:'Unprocessable Entity',json:async()=>({detail:[
  {loc:['body','overrides','component_materials',2,'material','thermal_conductivity_w_m_k'],msg:'Input should be greater than 0'},
  {loc:['body','overrides','fixed_boundaries',0,'temperature_k'],msg:'Input should be greater than or equal to 1'},
  {loc:['body','overrides','heat_sources',2,'total_power_w'],msg:'Input should be greater than 0'},
  {loc:['body','overrides','ambient_temperature_k'],msg:'Input should be greater than or equal to 1'},
]})})});
vm.runInContext(extract('async function request(', 'async function loadWorkspace('),requestContext);
let serverError;
try{await requestContext.request('/v1/studies/example/draft');}catch(error){serverError=error;}
assert(serverError);assert.equal(serverError.status,422);
assert.match(serverError.message,/component_materials\.2\.material\.thermal_conductivity_w_m_k/);
assert.match(serverError.message,/heat_sources\.2\.total_power_w/);
assert.deepEqual([...serverError.fields],[
  'component_materials.2.material.thermal_conductivity_w_m_k','boundaries.0.temperature_k',
  'heat_sources.2.total_power_w','convection.ambient_temperature_k',
]);
assert.equal(serverError.validationIssues.length,4);
assert(serverError.validationIssues.every(issue=>issue.fields.length===1));

let presented;
const feedback=vm.createContext({state:{validationFailure:{studyId:'current',message:serverError.message,fields:serverError.fields,issues:serverError.validationIssues}},
  selectedStudy:()=>({study_id:'current',plan:{},confirmation:{status:'needs_input'}}),
  document:{getElementById:()=>({})},validationFields:()=>new Map(),
  validationFeedback:{render(study,options){presented=options;return options.inputIssues;}},
});
vm.runInContext(extract('function renderValidationFeedback()', 'async function loadSelectedValidation('),feedback);
feedback.renderValidationFeedback();
assert.equal(presented.failure,'','Field-specific server errors must not be duplicated as an unlocatable generic error');
assert.equal(presented.inputIssues.length,4);
assert.deepEqual([...presented.inputIssues[2].fields],['heat_sources.2.total_power_w']);
assert(presented.inputIssues.every(issue=>issue.severity==='error'));

// An invalid newly-added source exists only in the local draft; Locate must still open it.
const controls={};const elements=new Proxy(controls,{get(target,key){return target[key] ||= {id:key,children:[]};}});
let selectedIndex;
const fields=vm.createContext({elements,
  selectedStudy:()=>({plan:{criteria:[],heat_sources:[{}]}}),selectedWorkpiece:()=>({cad_format:'stl'}),
  ThermoFlowSources:{sourcesFromPlan:()=>[{}]},activeSourceCollection:()=>({sources:[{},{},{}],activeIndex:0}),
  selectHeatSource:index=>{selectedIndex=index;},
});
vm.runInContext(extract('function validationFields()', 'function locateValidationFields('),fields);
const mapping=fields.validationFields();
const target=mapping.get('heat_sources.2.total_power_w');
assert(target,'Server rejection of an unsaved newly-added source needs a navigation target');
assert.equal(target.activate(),elements.canvasSourcePower);assert.equal(selectedIndex,2);
assert.equal(mapping.get('solver.backend').node,elements.draftMeshMethod);
console.log('422 errors retain paths and field metadata, render individual locatable issues, and map unsaved heat sources');
