import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const base='src/thermoflow/web/';
const html=readFileSync(base+'index.html','utf8');
const registry=new Map();
class Node {
  constructor(id='',tag='div'){this.id=id;this.tagName=tag.toUpperCase();this.children=[];this.childNodes=this.children;this.dataset={};this.style={};this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.events={};this.attributes={};this.options=[];this.files=[];this.isConnected=true;this.willValidate=false;
    this.classList={add(){},remove(){},toggle(){},contains:()=>false};}
  append(...nodes){this.children.push(...nodes);this.options=this.children;}
  prepend(...nodes){this.children.unshift(...nodes);this.options=this.children;}
  replaceChildren(...nodes){this.children=nodes;this.childNodes=nodes;this.options=nodes;}
  setAttribute(key,value){this.attributes[key]=value;}
  getAttribute(key){return this.attributes[key]??null;}
  removeAttribute(key){delete this.attributes[key];}
  addEventListener(type,listener){(this.events[type] ||= []).push(listener);}
  querySelector(selector){if(selector.startsWith('#'))return registry.get(selector.slice(1))||null;return this.querySelectorAll(selector)[0]||new Node('',selector.includes('input')?'input':'div');}
  querySelectorAll(selector){
    if(selector==='dd')return Array.from({length:4},()=>new Node());
    const matches=node=>selector.split(',').some(part=>{
      part=part.trim();const tag=part.match(/^[a-z]+/)?.[0],cls=part.match(/^\.([\w-]+)/)?.[1],data=part.match(/\[data-([\w-]+)/)?.[1];
      if(!tag&&!cls&&!data)return false;
      if(tag&&node.tagName!==tag.toUpperCase())return false;
      if(cls&&!String(node.className||'').split(/\s+/).includes(cls))return false;
      if(data){const key=data.replace(/-([a-z])/g,(_,char)=>char.toUpperCase());if(!(key in node.dataset))return false;}
      return true;
    });
    return this.children.flatMap(child=>[...(matches(child)?[child]:[]),...child.querySelectorAll(selector)]);
  }
  closest(){return this;}
  focus(){} select(){} scrollIntoView(){} insertAdjacentElement(){} remove(){}
  checkValidity(){return true;} reportValidity(){return true;} setCustomValidity(){}
  contains(){return false;}
  showModal(){this.open=true;} close(){this.open=false;}
  getBoundingClientRect(){return{x:Number.parseFloat(this.style.left)||800,y:Number.parseFloat(this.style.top)||128,width:48,height:68};}
  setPointerCapture(){} hasPointerCapture(){return false;} releasePointerCapture(){}
}
for(const match of html.matchAll(/<([a-z][\w-]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)){
  assert(!registry.has(match[2]),`Duplicate static DOM id ${match[2]}`);
  const node=new Node(match[2],match[1]);node.hidden=/\bhidden\b/.test(match[0]);registry.set(match[2],node);
}
const document={body:new Node('body','body'),documentElement:new Node('html','html'),
  getElementById:id=>registry.get(id)||null,
  querySelector:selector=>selector.startsWith('#')?registry.get(selector.slice(1))||null:null,
  querySelectorAll:()=>[],createElement:tag=>new Node('',tag),addEventListener(){}};
const requests=[];
const sandbox={AbortController,document,innerWidth:1440,innerHeight:1000,addEventListener(){},
  localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:value=>value},
  clearTimeout(){},setTimeout:()=>1,cancelAnimationFrame(){},requestAnimationFrame:()=>1,
  fetch:async path=>{requests.push(path);return{ok:true,json:async()=>path==='/health'?{status:'ok'}:[]};},
};
sandbox.window=sandbox;
const context=vm.createContext(sandbox);
const assets=[...html.matchAll(/<script\b[^>]*src="\/assets\/([^"?]+)[^"]*"[^>]*>/g)].map(match=>match[1]);
assert(assets.indexOf('floating-button.js')<assets.indexOf('app.js'),'Floating helper must load before the app binds Agent');
for(const asset of assets){
  vm.runInContext(readFileSync(base+'assets/'+asset,'utf8'),context,{filename:asset,
    importModuleDynamically:()=>Promise.reject(new Error('WebGL module is outside this DOM bootstrap test'))});
}
for(let i=0;i<40;i++)await Promise.resolve();
assert.equal(vm.runInContext('state.health?.status',context),'ok',registry.get('toast').textContent||'Empty workspace must finish initialization');
assert(requests.includes('/v1/workpieces')&&requests.includes('/v1/studies'));
for(const id of ['heatSourceNav','materialsNav','closeSourceEditorButton','modelingNav','closeModelingSidebar']){
  assert(registry.get(id).events.click?.length,`${id} must have its actual click handler registered`);
}
assert(registry.get('modelingNav').events.pointerdown?.length,'Agent must register dragging, not only a click');
assert(registry.get('modelingNav').events.pointermove?.length);
for(const id of ['heatSourceNav','materialsNav']){
  for(const listener of registry.get(id).events.click)await listener({preventDefault(){}});
  assert.match(registry.get('toast').textContent,/导入 STL/,'Missing geometry must produce an actionable prerequisite, not throw or silently return');
}
assert.equal(registry.get('sourceEditor').hidden,true);

// Exercise the actual connected renderer/actions with five component materials and a source-free manual plan.
vm.runInContext(`
  state.workpieces=[{workpiece_id:'wp',project_id:'project',cad_format:'stl',unit_confirmed:true,
    dimensions_mm:{x:10,y:10,z:10},geometry:{summary:{bbox:[0,0,0,10,10,10]}},
    components:Array.from({length:5},(_,i)=>({component_id:'component-'+(i+1),name:'组件 '+(i+1)}))}];
  state.selectedWorkpieceId='wp';state.selectedProjectId='project';state.selectedStudyId='draft';
  const testMaterial={name:'Fabric',thermal_conductivity_w_m_k:1,density_kg_m3:300,specific_heat_j_kg_k:1300,source_type:'user'};
  state.studies=[{study_id:'draft',workpiece_id:'wp',project_id:'project',status:'needs_input',confirmation:{status:'needs_input'},plan:{
    analysis_type:'transient_conduction',initial_temperature_k:293.15,duration_s:60,time_step_s:1,heat_sources:[],heat_source_enabled:false,
    material:testMaterial,component_materials:state.workpieces[0].components.map(c=>({component_id:c.component_id,material:{...testMaterial}})),
    convection:{ambient_temperature_k:293.15,heat_transfer_coefficient_w_m2_k:10},global_convection_enabled:true,
    mesh:{target_element_size_mm:1},solver:{backend:'voxel_stl_v1'},criteria:[]}}];
  renderMaterialsPanel();
`,context);
let materialRows=registry.get('componentMaterialList').querySelectorAll('.component-material-row');
assert.equal(materialRows.length,5);
for(const row of materialRows){
  assert.equal(row.querySelector('select').disabled,false);
  assert.equal(row.querySelectorAll('input[data-material-property]').length,3);
  assert(row.querySelectorAll('input').every(input=>!input.disabled));
}
materialRows[2].querySelector('input[data-material-property]').value='.08';
const assignments=vm.runInContext('[...elements.componentMaterialList.children].map(materialAssignmentFromRow)',context);
assert.equal(assignments[2].material.thermal_conductivity_w_m_k,.08);
for(const index of [0,1,3,4])assert.equal(assignments[index].material.thermal_conductivity_w_m_k,1);
for(const listener of registry.get('heatSourceNav').events.click)await listener({preventDefault(){}});
assert.equal(registry.get('sourceEditor').hidden,false,'Actual heat entry must open even when no source is configured');
assert.equal(registry.get('canvasDuration').value,'60');
assert.equal(registry.get('draftEnableHeatSource').checked,false,'Opening heat settings still has no activation side effect');
for(const listener of registry.get('materialsNav').events.click)await listener({preventDefault(){}});
assert.equal(vm.runInContext('state.activeTab',context),'materials');
assert.equal(registry.get('sourceEditor').hidden,true);
vm.runInContext(`state.studies[0].confirmation.status='confirmed';state.materialsSyncedFor=null;state.busy=true;renderMaterialsPanel();state.busy=false;renderMaterialsPanel();`,context);
materialRows=registry.get('componentMaterialList').querySelectorAll('.component-material-row');
assert.equal(materialRows.length,5);
for(const row of materialRows){
  assert.equal(row.querySelector('select').disabled,true);
  const edit=row.querySelector('.component-material-edit');
  assert.equal(edit.disabled,false,'Per-component edit action must recover after a busy render');
  assert(edit.events.click?.length);
}
assert.equal(registry.get('editMaterialsButton').hidden,false);
for(const listener of registry.get('closeModelingSidebar').events.click)await listener({preventDefault(){}});
const agent=registry.get('modelingNav');assert.equal(agent.hidden,false);
function emit(type,extra={}){for(const listener of agent.events[type]||[])listener({button:0,pointerId:1,isPrimary:true,detail:1,preventDefault(){},stopImmediatePropagation(){},...extra});}
const before=agent.style.left;
emit('pointerdown',{clientX:1400,clientY:150});emit('pointermove',{clientX:1100,clientY:400});emit('pointerup');emit('click');
assert.notEqual(agent.style.left,before,'The Agent control attached by the actual app must move');
assert.equal(vm.runInContext('state.modelingSidebarOpen',context),false,'Actual dragging cannot open the sidebar');
emit('pointerdown',{clientX:1100,clientY:400});emit('pointerup');emit('click');
assert.equal(vm.runInContext('state.modelingSidebarOpen',context),true,'A normal click still opens Agent');
console.log('Complete asset bootstrap, empty/5-component/confirmed source-material entries, busy recovery, and actual attached Agent drag/click handlers passed');
