import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync('src/thermoflow/web/assets/app.js','utf8');
const ctx=vm.createContext({window:{},state:{draftSyncedFor:'draft'},
  elements:{draftDuration:{value:'3600'},draftTimeStep:{value:'18'},draftInitialTemperature:{value:'300'},
    draftAmbient:{value:'295'},draftConvection:{value:'20'},draftSourcePower:{value:''},draftSourceRadius:{value:''}},
  selectedWorkpiece:()=>({dimensions_mm:{x:10,y:10,z:10}}),
});
vm.runInContext(readFileSync('src/thermoflow/web/assets/source-editor-model.js','utf8'),ctx);
ctx.ThermoFlowSources=ctx.window.ThermoFlowSources;
vm.runInContext(source.slice(source.indexOf('function sourceDraftFromPlan('),source.indexOf('function activeSourceCollection(')),ctx);
const study={study_id:'draft',confirmation:{status:'needs_input'},plan:{duration_s:60,time_step_s:.3,initial_temperature_k:293.15}};
const collection=ctx.sourceDraftFromPlan(study);
assert.equal(collection.timeWindow.duration,3600,'Immediate heat editor opening must use the unsaved current duration');
assert.equal(collection.timeWindow.timeStep,18);assert.equal(collection.timeWindow.initialTemperature,300);
assert.equal(collection.ambientTemperature,295);assert.equal(collection.convectionCoefficient,20);
assert.equal(collection.sources[0].power,10,'An empty disabled primary field must not create a zero-power source');
assert.equal(study.plan.duration_s,60,'Hydrating the editing view must not mutate the saved plan');
ctx.elements.draftSourcePower.value='4';ctx.elements.draftSourceRadius.value='-1';
study.plan.heat_sources=[{name:'first',shape:'point',total_power_w:1,radius_mm:2,center_mm:{x:0,y:0,z:0}}];
const reentered=ctx.sourceDraftFromPlan(study);
assert.equal(reentered.sources[0].power,1,'Last edited source controls must not overwrite the first source on re-entry');
assert.equal(reentered.sources[0].radius,2,'Inactive shape fields must not leak into another source');
study.confirmation.status='confirmed';
assert.equal(ctx.sourceDraftFromPlan(study).timeWindow.duration,60,'Confirmed result settings must use the saved inputs');
console.log('Immediate source editor opening preserves current unsaved timing and convection without changing confirmed results');
