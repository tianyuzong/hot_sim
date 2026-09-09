import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync("src/thermoflow/web/assets/app.js", "utf8");
const start = source.indexOf("async function applySourceChanges(");
const end = source.indexOf("function openWorkpieceDialog(", start);
assert(start >= 0 && end > start);

const plan = {
  material: {
    name: "C110 紫铜",
    thermal_conductivity_w_m_k: 391,
    density_kg_m3: 8940,
    specific_heat_j_kg_k: 385,
  },
  component_materials: [{ component_id: "component-1", material_id: "copper-c110" }],
  boundaries: [],
  surface_conditions: [],
  contacts: [],
  global_convection_enabled: true,
  mesh: { target_element_size_mm: 1, max_axis_intervals: 30 },
  solver: { relative_tolerance: 1e-8, max_iterations: 1000 },
  criteria: [],
};
const study = {
  study_id: "study-confirmed-source",
  purpose: "移动已确认材料工件上的热源",
  confirmation: { status: "confirmed" },
  plan,
};
const sourceDraft = {
  center: { x: 10, y: 5, z: 2.5 },
  end: { x: 12, y: 5, z: 2.5 },
  shape: "point",
  placement: "embedded",
  embeddingDepth: 0,
  power: 20,
  radius: 1,
  surfaceAxis: "z",
  surfaceWidth: 2,
  surfaceHeight: 2,
  surfaceThickness: 0.5,
  ambientTemperature: 293.15,
  convectionCoefficient: 10,
};
const requests = [];
let taskSubmissions = 0;
const toasts = [];
const context = vm.createContext({
  elements: { sourceEditor: { reportValidity: () => true } },
  state: { selectedStudyId: study.study_id, activeTab: "result" },
  selectedStudy: () => study,
  activeSourceDraft: () => sourceDraft,
  updateSourceDraftFromInputs() {},
  flushDraftBeforeNavigation: async () => true,
  switchTab() {},
  setBusy() {},
  simulationOverridesFromDraft: () => ({ component_materials: plan.component_materials }),
  discardSourceDraft() {},
  request: async (url, options) => {
    const body = JSON.parse(options.body);
    requests.push({ url, body });
    if (url.endsWith("/copy")) {
      return { study_id: "study-source-copy", status: "needs_input" };
    }
    if (url.endsWith("/confirm")) {
      if (body.materials_confirmed !== true) {
        throw new Error("请明确确认每个组件的材料及热物性后再确认仿真输入");
      }
      return { study_id: "study-source-copy", status: "ready" };
    }
    throw new Error(`Unexpected request: ${url}`);
  },
  submitComputation: async (studyId, operation) => {
    assert.equal(studyId, "study-source-copy");
    assert.equal(operation, "apply_and_solve");
    taskSubmissions += 1;
    return { operation };
  },
  showToast: (message, isError = false) => toasts.push({ message, isError }),
});
vm.runInContext(source.slice(start, end).replace(/async\s*$/, ""), context);

await context.applySourceChanges({ preventDefault() {} });

assert.equal(
  taskSubmissions,
  1,
  `A source-only change on a confirmed study must reach computation; toasts=${JSON.stringify(toasts)}`,
);
assert.equal(requests[1].body.materials_confirmed, true);
console.log("Source-only copies preserve explicit material confirmation and submit computation");
