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
  state: { busy: false, modelingBusy: false, draftSaveError: false },
  selectedStudy: () => study,
  selectedWorkpiece: () => ({ unit_confirmed: true, cad_format: "stl" }),
  activeStudyTask: () => null,
});
vm.runInContext(source.slice(start, end), context);

context.renderPrimaryAction();
assert.equal(elements.primaryAction.textContent, "确认仿真输入");
assert.equal(
  elements.primaryAction.disabled,
  true,
  "Global confirmation alone must not enable confirmation before materials are reviewed",
);

elements.confirmMaterials.checked = true;
context.renderPrimaryAction();
assert.equal(
  elements.primaryAction.disabled,
  false,
  "Explicit material review plus global confirmation must enable confirmation",
);

console.log("Material review independently gates study confirmation");
