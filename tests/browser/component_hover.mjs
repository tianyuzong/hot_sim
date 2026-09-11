import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import * as THREE from '../../src/thermoflow/web/assets/vendor/three/three.module.min.js';
import {EngineeringViewport,surfaceGeometry} from '../../src/thermoflow/web/assets/viewport.mjs';

const surface={vertices:[],triangles:[],component_ids:[],cell_ids:[]};
for(let i=0;i<5;i++){
  const x=-4+i*2,n=surface.vertices.length;
  surface.vertices.push([x-.7,-.7,0],[x+.7,-.7,0],[x,.7,0]);
  surface.triangles.push([n,n+1,n+2]); surface.component_ids.push(`c${i+1}`); surface.cell_ids.push(null);
}
const camera=new THREE.OrthographicCamera(-6,6,3,-3,.1,100);
camera.position.set(0,0,10);camera.lookAt(0,0,0);camera.updateMatrixWorld();
const group=new THREE.Group();
let last, picks=0, next=0;
const frames=new Map();
globalThis.requestAnimationFrame=fn=>{frames.set(++next,fn);return next;};
globalThis.cancelAnimationFrame=id=>frames.delete(id);
const flush=()=>{const pending=[...frames.values()];frames.clear();pending.forEach(fn=>fn());};
const viewport=Object.assign(Object.create(EngineeringViewport.prototype),{
  camera,raycaster:new THREE.Raycaster(),size:12,geometryGroup:group,markerGroup:new THREE.Group(),surface,
  options:{workpiece:{workpiece_id:'wp'},selectedComponent:'c2'},
  canvas:{getBoundingClientRect:()=>({left:0,top:0,width:600,height:300}),classList:{toggle(){}},title:''},
  callbacks:{componentHover:hit=>{last=hit;picks++;},component:()=>assert.fail('Hover must not select or edit')},
  sourceHit:()=>null,
});
function setGeometry(options={}){
  group.clear();
  viewport.mesh=new THREE.Mesh(surfaceGeometry(surface,[0,0,0],options),new THREE.MeshBasicMaterial({side:THREE.DoubleSide}));
  group.add(viewport.mesh);group.updateMatrixWorld(true);
}
function hover(x,y=150){viewport.pointerMove({clientX:x,clientY:y,buttons:0,pointerType:'mouse'});flush();return last;}
setGeometry();
for(let i=0;i<5;i++)assert.equal(hover(100+i*100)?.componentId,`c${i+1}`);
assert.equal(viewport.options.selectedComponent,'c2');
assert.equal(hover(20),null,'Empty canvas clears tooltip');
setGeometry({hidden:new Set(['c1','c2'])});
assert.equal(hover(100),null,'Hidden components cannot be picked');
assert.equal(hover(300)?.componentId,'c3','Filtered triangle 0 belongs to original component 3');
setGeometry({isolated:'c5'});
assert.equal(hover(500)?.componentId,'c5');assert.equal(hover(300),null);
setGeometry();
viewport.queueComponentHover({clientX:100,clientY:150});viewport.queueComponentHover({clientX:500,clientY:150});
assert.equal(frames.size,1,'Coalesce rapid mouse movement');flush();assert.equal(last.componentId,'c5');
viewport.queueComponentHover({clientX:300,clientY:150});viewport.clearComponentHover();flush();assert.equal(last,null);
assert.equal(frames.size,0,'Leaving/changing the model cancels stale pending hover');
hover(300);viewport.pointerMove({buttons:1,clientX:400,clientY:150});assert.equal(last,null,'Rotation hides hover');
viewport.pointerStart=[350,150];viewport.pointerUp({button:0,pointerType:'mouse',clientX:300,clientY:150});flush();
assert.equal(last.componentId,'c3','Releasing rotation identifies the component now under the stationary pointer');
viewport.sourceHit=()=>({object:{userData:{sourceName:'Heater'}}});hover(300);assert.equal(last,null,'Source handles retain their own interaction');

const source=readFileSync('src/thermoflow/web/assets/app.js','utf8');
const a=source.indexOf('function showComponentHover('),b=source.indexOf('function selectComponent(',a);
const nodes=Object.fromEntries(['componentHoverTooltip','componentHoverName','componentHoverMaterial','componentHoverConductivity','componentHoverHint'].map(id=>[id,{textContent:'',hidden:true,dataset:{},style:{},offsetWidth:240,offsetHeight:120}]));
const row={classList:{add(){},remove(){}}};
const workpiece={workpiece_id:'wp',components:[{component_id:'c1',name:'组件 1'}]};
const study={study_id:'s',plan:{component_materials:[{component_id:'c1',material:{name:'Saved old value',thermal_conductivity_w_m_k:1}}]}};
const ctx=vm.createContext({document:{getElementById:id=>nodes[id],querySelectorAll:()=>[row]},elements:{componentMaterialList:{querySelector:()=>row}},
  CSS:{escape:s=>s},window:{innerWidth:800,innerHeight:600},selectedWorkpiece:()=>workpiece,selectedStudy:()=>study,
  materialAssignmentFromRow:()=>({material:{name:'Current draft <fabric>',thermal_conductivity_w_m_k:.12}}),formatNumber:String});
vm.runInContext(source.slice(a,b),ctx);
ctx.showComponentHover({componentId:'c1',clientX:790,clientY:590,options:{workpiece,study,mode:'model'}});
assert.equal(nodes.componentHoverName.textContent,'组件 1');
assert.equal(nodes.componentHoverMaterial.textContent,'材料：Current draft <fabric>','Tooltip must show current edited row, safely as plain text');
assert.equal(nodes.componentHoverConductivity.textContent,'导热系数：0.12 W/(m·K)');
assert(parseFloat(nodes.componentHoverTooltip.style.left)+240<=800);
assert(parseFloat(nodes.componentHoverTooltip.style.top)+120<=600);
ctx.showComponentHover(null);assert.equal(nodes.componentHoverTooltip.hidden,true);
console.log('Five-component hover raycasts, hidden/isolate mapping, cancellation, live draft labels and edge positioning passed');
