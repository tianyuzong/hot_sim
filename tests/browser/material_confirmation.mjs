import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync("src/thermoflow/web/assets/app.js", "utf8");
const start = source.indexOf("function renderPrimaryAction(");
const end = source.indexOf("async function handlePrimaryAction(", start);
assert(start >= 0 && end > start);

const elements = {
  primaryAction: { textContent: "", disabled: false },
  confirmInputs: { checked: true },
  confirmMaterials: { checked: false },
};
const study = {
  study_id: "study-material-review",
  status: "needs_input",
  confirmation: { status: "needs_input" },
  modeling: { proposal: null },
};
const context = vm.createContext({
  elements,
  state: { busy: false, modelingBusy: false, draftSaveError: false, activeTab: "scenario" },
  selectedStudy: () => study,
  selectedWorkpiece: () => ({ unit_confirmed: true, cad_format: "stl" }),
  activeStudyTask: () => null,
});
vm.runInContext(source.slice(start, end), context);

context.renderPrimaryAction();
assert.equal(elements.primaryAction.textContent, "先选择并确认材料");
assert.equal(
  elements.primaryAction.disabled,
  false,
  "A missing material confirmation must offer navigation to the material panel",
);

elements.confirmMaterials.checked = true;
context.renderPrimaryAction();
assert.equal(elements.primaryAction.textContent, "确认并开始仿真");
assert.equal(
  elements.primaryAction.disabled,
  false,
  "Explicit material review plus global confirmation must enable confirmation",
);

context.state.activeTab = "materials";
context.renderPrimaryAction();
assert.equal(elements.primaryAction.textContent, "下一步：热源与时长");
elements.confirmMaterials.checked = false;
context.renderPrimaryAction();
assert.equal(elements.primaryAction.disabled, true, "Material review is still explicit before advancing");

console.log("Material review independently gates study confirmation");
