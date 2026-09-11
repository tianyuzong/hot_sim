import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const app=readFileSync(process.env.AUDIT_APP||'src/thermoflow/web/assets/app.js','utf8');
const extract=(a,b)=>{const start=app.indexOf(a),end=app.indexOf(b,start);assert(start>=0&&end>start);return app.slice(start,end);};
class Element {
  constructor(tag,className=''){this.tag=tag;this.className=className;this.children=[];this.dataset={};this.events={};this.value='';}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(){}
  addEventListener(event,callback){this.events[event]=callback;}
  querySelectorAll(selector){return this.children.flatMap(child=>[...(selector==='input'&&child.tag==='input'?[child]:[]),...child.querySelectorAll(selector)]);}
}
const rows=new Element('div');
const surface=vm.createContext({elements:{surfaceConditionRows:rows},state:{},
  createElement:(tag,cls)=>new Element(tag,cls),populateBoundaryRegions(){},drawWorkpiece(){},
  SURFACE_FIELDS:{heat_flux:[['heat_flux_w_m2','Flux',-1e9,1e9]],convection:[['ambient_temperature_k','Ambient',1,5000],['heat_transfer_coefficient_w_m2_k','Coefficient',1e-6,1e6]],radiation:[['emissivity','Emissivity',1e-6,1],['emissivity_source','Source']]},
});
vm.runInContext(extract('function appendSurfaceCondition(', 'elements.addSurfaceCondition.addEventListener('),surface);
surface.appendSurfaceCondition({kind:'heat_flux',heat_flux_w_m2:10});
const row=rows.children[0],kind=row.children[0].children[0],fields=row.children[2];
fields.querySelectorAll('input')[0].value='25';
kind.value='convection';kind.events.change();
const conv=fields.querySelectorAll('input');conv[0].value='312.4';conv[1].value='18';
kind.value='heat_flux';kind.events.change();
assert.equal(fields.querySelectorAll('input')[0].value,'25','Switching boundary type must preserve the user-edited original value');
kind.value='convection';kind.events.change();
assert.deepEqual(fields.querySelectorAll('input').map(input=>input.value),['312.4','18']);
kind.value='radiation';kind.events.change();
fields.querySelectorAll('input')[1].value='User source reference';
kind.value='heat_flux';kind.events.change();kind.value='radiation';kind.events.change();
assert.equal(fields.querySelectorAll('input')[1].value,'User source reference');

let study={study_id:'selected',plan:{convection:null,global_convection_enabled:false},confirmation:{status:'needs_input'}};
const elements={draftEnableGlobalConvection:{checked:false},canvasAmbientTemperature:{value:'-1'},canvasConvectionCoefficient:{value:'-1'},resultHotspot:{}};
const notice={},maximum={};const state={};
const controls=vm.createContext({elements,state,selectedStudy:()=>study,document:{getElementById:id=>id==='focusMaximum'?maximum:notice}});
vm.runInContext(extract('function syncSourceConvectionControls()', 'function syncSourceEditor()')+extract('function syncMaximumResultControl()', 'function renderWorkspace()'),controls);
controls.syncSourceConvectionControls();
assert.equal(elements.canvasAmbientTemperature.disabled,true);assert.equal(elements.canvasConvectionCoefficient.required,false);assert.equal(notice.hidden,false);
elements.draftEnableGlobalConvection.checked=true;controls.syncSourceConvectionControls();
assert.equal(elements.canvasAmbientTemperature.disabled,false);assert.equal(elements.canvasAmbientTemperature.value,'-1','Availability updates preserve user input for correction');
state.busy=true;controls.syncSourceConvectionControls();assert.equal(elements.canvasAmbientTemperature.disabled,true);
state.busy=false;controls.syncSourceConvectionControls();assert.equal(elements.canvasAmbientTemperature.disabled,false);
study.confirmation.status='confirmed';controls.syncSourceConvectionControls();assert.equal(elements.canvasAmbientTemperature.disabled,true,'Confirmed study uses its recorded convection flag');
controls.syncMaximumResultControl();assert.equal(maximum.disabled,true);
state.result={study_id:'selected'};controls.syncMaximumResultControl();assert.equal(maximum.disabled,false);
state.comparison={};controls.syncMaximumResultControl();assert.equal(maximum.disabled,true);assert.match(maximum.title,/退出比较/);
state.comparison=null;state.result={study_id:'other'};controls.syncMaximumResultControl();assert.equal(maximum.disabled,true,'Another study result cannot activate hotspot focus');
console.log('Surface-condition type memory, convection availability, and hotspot action guards passed');
