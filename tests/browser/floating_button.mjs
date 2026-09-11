import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const script=readFileSync('src/thermoflow/web/assets/floating-button.js','utf8');
const store=new Map(),windowEvents={};
const window={innerWidth:1000,innerHeight:700,addEventListener:(type,fn)=>{windowEvents[type]=fn;},
  localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v)}};
const ctx=vm.createContext({window});vm.runInContext(script,ctx);
function button(){
  const handlers={},captured=new Set();
  return {hidden:false,style:{left:'944px',top:'128px'},classList:{add(){},remove(){}},
    getBoundingClientRect(){return {x:parseFloat(this.style.left),y:parseFloat(this.style.top),width:48,height:68};},
    addEventListener:(t,fn)=>{handlers[t]=fn;},setPointerCapture:id=>captured.add(id),
    hasPointerCapture:id=>captured.has(id),releasePointerCapture:id=>captured.delete(id),
    emit(t,props={}){handlers[t]?.({button:0,pointerId:1,isPrimary:true,detail:1,preventDefault(){},stopImmediatePropagation(){},...props});},
  };
}
const first=button();let opened=0;
window.ThermoFlowFloatingButton.attach(first,{onActivate:()=>opened++});
first.emit('pointerdown',{clientX:960,clientY:140});
first.emit('pointermove',{clientX:700,clientY:400});
first.emit('pointerup');first.emit('click');
assert.equal(opened,0,'Dragging must not open the sidebar');
assert.equal(first.style.left,'684px');assert.equal(first.style.top,'388px');
const second=button();window.ThermoFlowFloatingButton.attach(second,{onActivate:()=>opened++});
assert.equal(second.style.left,first.style.left,'Saved position survives a new page');
assert.equal(second.style.top,first.style.top);
second.emit('pointerdown',{clientX:690,clientY:390});second.emit('pointerup');second.emit('click');
assert.equal(opened,1,'A normal click still opens Agent');
second.emit('pointerdown',{clientX:690,clientY:390});second.emit('pointermove',{clientX:-1000,clientY:3000});second.emit('pointerup');
assert.equal(second.style.left,'8px');assert.equal(second.style.top,'624px');
window.innerWidth=320;window.innerHeight=240;windowEvents.resize();
const rect=second.getBoundingClientRect();assert(rect.x>=8&&rect.x+rect.width<=312);assert(rect.y>=8&&rect.y+rect.height<=232);
second.emit('keydown',{key:'ArrowRight'});assert.equal(second.style.left,'18px');
const before={...second.style};
second.emit('pointerdown',{clientX:20,clientY:165});second.emit('pointermove',{clientX:100,clientY:100});second.emit('pointercancel');
assert.deepEqual(second.style,before,'Pointer cancellation restores the prior location');
second.emit('click',{detail:0});assert.equal(opened,2,'Keyboard activation remains available after dragging');
console.log('Agent drag/click separation, saved position, resize bounds, keyboard and pointer cancellation passed');
