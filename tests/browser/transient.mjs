import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { execFile, spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { promisify } from "node:util";

const require = createRequire(process.env.THERMOFLOW_PLAYWRIGHT_PACKAGE || import.meta.url);
const { chromium } = require("playwright");
const python = process.env.THERMOFLOW_PYTHON || "python";
const fixture = JSON.parse((await promisify(execFile)(python, ["tests/browser/seed_transient.py"], { encoding: "utf8" })).stdout);
const port = Number((await promisify(execFile)(python, ["-c",
  "import socket\nwith socket.socket() as listener:\n    listener.bind(('127.0.0.1', 0))\n    print(listener.getsockname()[1])",
], { encoding: "utf8" })).stdout.trim());
const url = `http://127.0.0.1:${port}`;
const server = spawn(python, ["-m", "thermoflow"], {
  env: { ...process.env, THERMOFLOW_DATA_DIR: fixture.data_dir, THERMOFLOW_PORT: String(port),
    THERMOFLOW_PLANNER: "deterministic", THERMOFLOW_COMPUTE: "cpu" },
  stdio: ["ignore", "pipe", "pipe"],
});
let browser;
try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      if ((await fetch(`${url}/health`)).ok) { ready = true; break; }
    } catch {}
    if (server.exitCode != null) throw new Error(`Server exited: ${server.exitCode}`);
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert(ready, "The local server must pass its health check before browser verification");
  console.log(`Local verification server ready at ${url}/docs`);
  const seededResult = await (await fetch(`${url}/v1/studies/${fixture.study_id}/result`)).json();
  const seededTimes = seededResult.time_steps.map(step => step.time_s);
  assert.equal(seededTimes.length, 201);
  browser = await chromium.launch({ headless: true, args: ["--no-sandbox", ...(process.env.THERMOFLOW_BROWSER_ARGS?.split(",") || [])],
    ...(process.env.THERMOFLOW_CHROMIUM ? { executablePath: process.env.THERMOFLOW_CHROMIUM } : {}) });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(f => {
    if (!localStorage.getItem("thermoflow.workspace.v1")) {
      localStorage.setItem("thermoflow.workspace.v1", JSON.stringify({
        selectedProjectId: f.project_id, selectedWorkpieceId: f.workpiece_id,
        selectedStudyId: f.study_id, activeTab: "result", visualizationMode: "thermal",
      }));
    }
  }, fixture);
  await mkdir("docs/verification", { recursive: true });
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await page.goto(`${url}/docs`);
    await page.locator("#timeControls").waitFor({ state: "visible" });
    await page.waitForFunction(() => document.querySelector("#resultMax").textContent !== "—");
    await page.waitForFunction(() => Number(document.querySelector("#workpieceCanvas").dataset.renderedTriangles) > 0
      && document.querySelector("#workpieceCanvas").dataset.sourceHash);
    await page.waitForFunction(() => document.querySelector("#timeControls").dataset.playbackReady === "true",
      null, { timeout: 8_000 });
    assert(await page.locator("#diffusionViewButton").isVisible());
    assert.equal(await page.locator("#diffusionViewButton").getAttribute("aria-pressed"), "true",
      "A transient study must initially open in the spatial diffusion view");
    assert.match(await page.locator("#thermalLegendTitle").textContent(),
      /空间温差 ΔT = T − 本帧最低温度 · 全时段固定/);
    assert(await page.locator("#diffusionReference").isVisible());
    assert.match(await page.locator("#diffusionReference").textContent(), /当前帧参考 Tmin =/);
    assert(await page.locator("#materialRangeWarning").isVisible(),
      "A result outside the selected material range must display a prominent warning");
    assert.match(await page.locator("#materialRangeWarning").textContent(), /常物性外推/);
    assert(await page.locator("#timeLockScale").isChecked());
    assert(await page.locator("#timeLockScale").isDisabled(),
      "Transient playback must keep one precomputed full-duration color scale");
    const legendBeforeScrub = await page.locator("#thermalLegend b").allTextContents();
    assert(await page.locator("#resultCompute").isVisible(),
      "The actual CPU/GPU result backend must be visible in result details");
    assert.match(await page.locator("#resultCompute").textContent(), /^cpu-scipy-/);
    const toolbarsDoNotOverlap = await page.evaluate(() => {
      const first = document.querySelector(".viewport-tools").getBoundingClientRect();
      const second = document.querySelector(".visualization-modes").getBoundingClientRect();
      return first.bottom <= second.top || second.bottom <= first.top
        || first.right <= second.left || second.right <= first.left;
    });
    assert(toolbarsDoNotOverlap, "Viewport controls and visualization modes must not overlap");
    const before = await page.locator("#workpieceCanvas").evaluate(canvas => canvas.toDataURL());
    await page.locator("#timePosition").fill("0");
    await page.waitForFunction(() => document.querySelector("#timeLabel").textContent === "0 s");
    await page.waitForFunction(() => Number(document.querySelector("#resultMax").textContent.replaceAll(",", "")) === 480);
    await page.waitForFunction(() => document.querySelector("#workpieceCanvas").dataset.fieldTime === "0");
    const after = await page.locator("#workpieceCanvas").evaluate(canvas => canvas.toDataURL());
    assert.notEqual(before, after, "Selecting a real time step must change the rendered field");
    if (viewport.width === 1440) {
      const playbackNetworkRequests = [];
      const recordFrameRequest = request => {
        const path = new URL(request.url()).pathname;
        if (/\/frames\/\d+$/.test(path) || path.endsWith("/view")) {
          playbackNetworkRequests.push(`${request.method()} ${path}`);
        }
      };
      page.on("request", recordFrameRequest);
      await page.locator("#timePosition").evaluate((slider) => {
        for (const index of [1, 7, 3]) {
          slider.value = String(index);
          slider.dispatchEvent(new Event("input", { bubbles: true }));
        }
      });
      await page.waitForFunction(expected => Number(document.querySelector("#workpieceCanvas").dataset.fieldTime) === expected,
        seededTimes[3]);
      assert.deepEqual(await page.locator("#thermalLegend b").allTextContents(), legendBeforeScrub,
        "Diffusion legend values must stay fixed while transient frame colors change");
      page.off("request", recordFrameRequest);
      assert.deepEqual(playbackNetworkRequests, [],
        "Timeline scrubbing must render entirely from the preloaded numerical frames");
      assert.equal(await page.locator("#timeControls").getAttribute("aria-busy"), "false");
      await page.locator("#timePosition").fill("0");
      await page.waitForFunction(() => document.querySelector("#workpieceCanvas").dataset.fieldTime === "0");
      await page.locator("#timePlay").click();
      await page.waitForFunction(expected => Number(document.querySelector("#workpieceCanvas").dataset.fieldTime) === expected,
        seededTimes[1], { timeout: 300 });
      await page.locator("#timePlay").click();

      const frameBeforeModeSwitch = await page.locator("#timePosition").inputValue();
      const modeRequests = [];
      const recordModeRequest = request => {
        const path = new URL(request.url()).pathname;
        if (/\/frames\/\d+$/.test(path) || path.endsWith("/view") || path.endsWith("/playback")) {
          modeRequests.push(`${request.method()} ${path}`);
        }
      };
      page.on("request", recordModeRequest);
      await page.locator("#thermalViewButton").click();
      await page.waitForFunction(() => document.querySelector("#thermalViewButton").getAttribute("aria-pressed") === "true");
      assert.equal(await page.locator("#timePosition").inputValue(), frameBeforeModeSwitch,
        "Switching temperature modes must preserve the selected real frame");
      const absoluteLegend = await page.locator("#thermalLegend b").allTextContents();
      await page.locator("#timePosition").fill("7");
      await page.waitForFunction(expected => Number(document.querySelector("#workpieceCanvas").dataset.fieldTime) === expected,
        seededTimes[7]);
      assert.deepEqual(await page.locator("#thermalLegend b").allTextContents(), absoluteLegend,
        "Absolute-temperature legend values must stay fixed during playback");
      await page.locator("#diffusionViewButton").click();
      await page.waitForFunction(() => document.querySelector("#diffusionViewButton").getAttribute("aria-pressed") === "true");
      page.off("request", recordModeRequest);
      assert.deepEqual(modeRequests, [], "Switching derived and absolute temperature modes must stay local");
    }
    const dimensions = await page.locator("#workpieceCanvas").evaluate(canvas => {
      const { width, height } = canvas;
      const gl = canvas.getContext("webgl2");
      if (!gl) throw new Error("Expected a real Three.js WebGL context");
      const pixels = new Uint8Array(width * height * 4);
      gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      let colored = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        if (Math.max(pixels[i], pixels[i + 1], pixels[i + 2]) - Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) > 35) colored++;
      }
      return { width, height, colored };
    });
    assert(dimensions.colored > 1000, "Canvas must contain a nonblank numerical color field");
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "No horizontal overflow");
    await page.screenshot({ path: `docs/verification/transient-${viewport.width}.png`, fullPage: true });
    await page.locator("#timePlay").click();
    await page.waitForFunction(() => document.querySelector("#timeLabel").textContent !== "0 s");
    await page.locator("#timePlay").click();
    await page.waitForFunction(() => document.querySelector("#workpieceCanvas").dataset.sourceHash);
    const beforeView = await page.locator("#workpieceCanvas").evaluate(canvas => canvas.toDataURL());
    await page.locator("#standardView").selectOption("top");
    const topView = await page.locator("#workpieceCanvas").evaluate(canvas => canvas.toDataURL());
    assert.notEqual(beforeView, topView, "A standard camera view must change the actual scene");
    await page.locator("#standardView").selectOption("iso");
    const box = await page.locator("#workpieceCanvas").boundingBox();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.6);
    const beforeZoom = await page.locator("#workpieceCanvas").evaluate(canvas => canvas.toDataURL());
    await page.mouse.wheel(0, -150);
    await page.waitForTimeout(200);
    const afterZoom = await page.locator("#workpieceCanvas").evaluate(canvas => canvas.toDataURL());
    assert.notEqual(beforeZoom, afterZoom, "Wheel zoom must change the unframed 3D scene");
    await page.locator("#resetViewButton").click();
    console.log(JSON.stringify({ viewport, canvas: dimensions }));
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  const fixedTransientLegend = await page.locator("#thermalLegend b").allTextContents();
  await page.locator("#sourceEditButton").click();
  await page.locator('[data-source-index="0"]').click();
  assert(!(await page.locator("#thermalLegendTitle").textContent()).includes("近似预览"),
    "Selecting a source without editing it must keep the real numerical result visible");
  await page.locator("#addHeatSource").click();
  await page.locator("#addHeatSource").click();
  assert.equal(await page.locator(".source-list-row").count(), 3,
    "Every source must have a dedicated row");
  assert(await page.locator("#sourceList").evaluate(list => {
    const listRect = list.getBoundingClientRect();
    return [...list.querySelectorAll(".source-list-row")].every(row => {
      const rect = row.getBoundingClientRect();
      return rect.left >= listRect.left && rect.right <= listRect.right;
    });
  }), "All source rows must be visible without a hidden horizontal scroll target");
  await page.locator('[data-source-index="2"]').click();
  await page.waitForFunction(() => document.querySelector("#canvasSourceName").value === "热源 3");
  assert.equal(await page.locator('[data-source-index="2"]').getAttribute("aria-selected"), "true",
    "Clicking a source row must select that exact source for editing");
  await page.locator('[data-source-index="0"]').click();
  const secondSourceMarker = await page.evaluate(() => {
    const source = engineeringViewport.options.sources[1];
    const projected = engineeringViewport.local(Object.values(source.center_mm))
      .project(engineeringViewport.camera);
    const rect = engineeringViewport.canvas.getBoundingClientRect();
    return { x: rect.x + (projected.x + 1) * rect.width / 2,
      y: rect.y + (1 - projected.y) * rect.height / 2 };
  });
  await page.mouse.click(secondSourceMarker.x, secondSourceMarker.y);
  await page.waitForFunction(() => document.querySelector("#canvasSourceName").value === "热源 2");
  assert.equal(await page.locator("#canvasSourceName").inputValue(), "热源 2");
  assert.equal(await page.locator('[data-source-index="1"]').getAttribute("aria-selected"), "true");
  await page.locator('[data-source-delete="1"]').click();
  assert.equal(await page.locator(".source-list-row").count(), 2,
    "Deleting one source must retain its siblings");
  await page.locator('[data-source-delete="1"]').click();
  assert.equal(await page.locator(".source-list-row").count(), 1);
  await page.waitForFunction(() => engineeringViewport?.options?.source?.center_mm);
  const livePreviewStart = await page.evaluate(() => {
    const projected = engineeringViewport.local(Object.values(engineeringViewport.options.source.center_mm))
      .project(engineeringViewport.camera);
    const rect = engineeringViewport.canvas.getBoundingClientRect();
    return { x: rect.x + (projected.x + 1) * rect.width / 2,
      y: rect.y + (1 - projected.y) * rect.height / 2 };
  });
  const beforeLivePreview = await page.locator("#workpieceCanvas").evaluate(canvas => canvas.toDataURL());
  await page.mouse.move(livePreviewStart.x, livePreviewStart.y);
  await page.mouse.down();
  await page.mouse.move(livePreviewStart.x + 40, livePreviewStart.y, { steps: 8 });
  await page.mouse.up();
  await page.waitForFunction(() => document.querySelector("#thermalLegendTitle").textContent.includes("近似预览"));
  assert.deepEqual(await page.locator("#thermalLegend b").allTextContents(), fixedTransientLegend,
    "Transient temperature legend must remain fixed even while previewing an edited heat source");
  const afterLivePreview = await page.locator("#workpieceCanvas").evaluate(canvas => canvas.toDataURL());
  assert.notEqual(beforeLivePreview, afterLivePreview,
    "Dragging a source must redraw the temperature field before a new solve is submitted");
  await page.locator("#canvasSourcePower").fill("2");
  let copiedStudyResponse = null;
  const recordCopyResponse = async response => {
    if (response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/copy")) {
      copiedStudyResponse = await response.json();
    }
  };
  page.on("response", recordCopyResponse);
  const submission = page.waitForResponse(response => response.request().method() === "POST"
    && /\/v1\/studies\/[^/]+\/tasks$/.test(new URL(response.url()).pathname), { timeout: 8_000 })
    .catch(() => null);
  await page.locator("#applySourceButton").click();
  const response = await submission;
  if (!response) {
    console.log(JSON.stringify({ sourceEditorValid: await page.locator("#sourceEditor").evaluate(form => form.checkValidity()),
      invalidFields: await page.locator("#sourceEditor :invalid").evaluateAll(nodes => nodes.map(node => node.id)),
      toast: await page.locator("#toast").textContent(), copiedStudyResponse, errors }));
  }
  page.off("response", recordCopyResponse);
  assert(response, "Applying the selected source must submit a solve task");
  assert.equal(response.status(), 202);
  const task = await response.json();
  assert.equal(task.operation, "apply_and_solve");
  assert.notEqual(task.study_id, fixture.study_id, "Changing a source must create an independent study");
  await page.waitForFunction(id => JSON.parse(localStorage.getItem("thermoflow.workspace.v1")).selectedStudyId === id,
    task.study_id);
  await page.reload();
  await page.locator("#computationStatus").waitFor({ state: "visible" });
  await page.waitForFunction(() => ["待检查网格", "已完成"].includes(document.querySelector("#computationState").textContent),
    null, { timeout: 60_000 });
  if (await page.locator("#computationState").textContent() === "待检查网格") {
    await page.locator("#meshNav").click();
    await page.locator("#acceptMeshWarnings").check();
    await page.locator("#primaryAction").click();
    await page.waitForFunction(() => document.querySelector("#primaryAction").textContent === "开始求解");
    await page.locator("#primaryAction").click();
  }
  await page.locator("#timeControls").waitFor({ state: "visible", timeout: 60_000 });
  const completed = await (await page.request.get(`${url}/v1/studies/${task.study_id}`)).json();
  assert.equal(completed.status, "succeeded");
  const result = await (await page.request.get(`${url}/v1/studies/${task.study_id}/result`)).json();
  assert(result.time_steps.length > 1, "A copied source change must preserve transient analysis");
  const baseline = await (await page.request.get(`${url}/v1/studies/${fixture.study_id}`)).json();
  assert.equal(baseline.status, "succeeded", "The baseline study must remain unchanged");
  await page.screenshot({ path: "docs/verification/background-workflow.png", fullPage: true });

  await page.route("**/v1/studies/*/tasks", route => route.fulfill({ status: 409,
    contentType: "application/json", body: JSON.stringify({ detail: "计算队列已满，请稍后重试" }) }));
  await page.locator("#sourceEditButton").click();
  await page.locator("#canvasSourcePower").fill("3");
  await page.locator("#applySourceButton").click();
  await page.waitForFunction(() => document.querySelector("#toast").textContent.startsWith("任务提交失败"));
  assert(!(await page.locator("#toast").textContent()).includes("后台计算已提交"));
  await page.unroute("**/v1/studies/*/tasks");

  const draftResponse = await page.request.post(`${url}/v1/studies/${fixture.study_id}/copy`, { data: {} });
  assert(draftResponse.ok());
  const draft = await draftResponse.json();
  await page.evaluate(id => {
    const workspace = JSON.parse(localStorage.getItem("thermoflow.workspace.v1"));
    localStorage.setItem("thermoflow.workspace.v1", JSON.stringify({ ...workspace,
      selectedStudyId: id, activeTab: "scenario", visualizationMode: "model" }));
  }, draft.study_id);
  await page.reload();
  await page.locator("#fixedBoundaryRows select").first().waitFor({ state: "visible" });
  await page.locator("#draftSourcePower").fill("7");
  await page.locator("#geometrySelectionMode").selectOption("faces");
  await page.locator("#standardView").selectOption("top");
  await page.waitForFunction(() => engineeringViewport?.surface && engineeringViewport.options.mode === "model");
  const hitPoint = await page.evaluate(() => {
    const viewport = engineeringViewport;
    const surface = viewport.surface;
    const top = Math.max(...surface.vertices.map(vertex => vertex[2]));
    const face = surface.triangles.find(triangle => triangle.every(vertex => Math.abs(surface.vertices[vertex][2] - top) < 1e-8));
    const center = [0, 1, 2].map(axis => face.reduce((sum, node) => sum + surface.vertices[node][axis], 0) / 3);
    const projected = viewport.local(center).project(viewport.camera);
    const rect = viewport.canvas.getBoundingClientRect();
    return { x: rect.x + (projected.x + 1) * rect.width / 2, y: rect.y + (1 - projected.y) * rect.height / 2 };
  });
  await page.mouse.click(hitPoint.x, hitPoint.y);
  await page.waitForFunction(() => document.querySelector("#surfaceSelectionCount").textContent === "1 个面");
  await page.locator("#saveSurfaceSelection").click();
  await page.locator("#surfaceRegionName").fill("Top cooling patch");
  const savedResponse = page.waitForResponse(response => response.request().method() === "POST"
    && /\/workpieces\/[^/]+\/regions$/.test(new URL(response.url()).pathname));
  await page.locator("#submitSurfaceRegion").click();
  const saved = await (await savedResponse).json();
  await page.locator("#surfaceRegionDialog").waitFor({ state: "hidden" });
  assert.equal(await page.locator("#draftSourcePower").inputValue(), "7", "Saving a region must retain unconfirmed form edits");
  assert.equal(saved.triangle_ids.length, 1);
  await page.locator("#fixedBoundaryRows select").first().selectOption(saved.region_id);
  await page.locator("#fixedBoundaryRows input").first().fill("310");
  await page.locator("#fixedBoundaryRows button").last().click();
  await page.locator("#solveNav").click();
  await page.locator("#confirmInputs").check();
  assert(await page.locator("#primaryAction").isDisabled(),
    "Global input confirmation must not bypass material review");
  await page.locator("#materialsNav").click();
  await page.locator("#confirmMaterials").check();
  await page.locator("#solveNav").click();
  assert(!(await page.locator("#primaryAction").isDisabled()),
    "Explicit material review and global confirmation must enable confirmation");
  await page.locator("#primaryAction").click();
  await page.waitForFunction(() => document.querySelector("#draftState").textContent === "已确认");
  const confirmed = await (await page.request.get(`${url}/v1/studies/${draft.study_id}`)).json();
  assert.equal(confirmed.plan.boundaries.length, 1);
  assert.equal(confirmed.plan.boundaries[0].region_id, saved.region_id);
  assert.equal(confirmed.plan.heat_source.total_power_w, 7);
  await page.screenshot({ path: "docs/verification/surface-region-confirmation.png", fullPage: true });

  const modelingDraft = await (await page.request.post(`${url}/v1/studies/${fixture.study_id}/copy`, { data: {} })).json();
  await page.evaluate(id => {
    const workspace = JSON.parse(localStorage.getItem("thermoflow.workspace.v1"));
    localStorage.setItem("thermoflow.workspace.v1", JSON.stringify({ ...workspace,
      selectedStudyId: id, activeTab: "scenario", visualizationMode: "model" }));
  }, modelingDraft.study_id);
  await page.reload();
  await page.locator("#draftSourcePower").fill("7");
  await page.locator("#modelingDrawer summary").click();
  await page.waitForFunction(() => document.querySelector("#draftSaveStatus").textContent === "草案已保存");
  await page.locator("#materialsNav").click();
  const conductivity = page.locator('[data-material-property="thermal_conductivity_w_m_k"]').first();
  await conductivity.fill("160");
  await page.locator("#scenarioNav").click();
  await page.locator("#materialsNav").click();
  assert.equal(await conductivity.inputValue(), "160", "Panel navigation must not reset unconfirmed material edits");
  await page.locator("#scenarioNav").click();
  await page.locator("#modelingInput").fill("热源功率 9 W");
  const suggestionResponse = page.waitForResponse(response => response.request().method() === "POST"
    && response.url().endsWith("/modeling/messages"));
  await page.locator("#sendModeling").click();
  const suggestion = await (await suggestionResponse).json();
  assert.equal(suggestion.plan.heat_source.total_power_w, 7);
  assert.equal(suggestion.modeling.proposal.plan.heat_source.total_power_w, 9);
  assert.equal(suggestion.modeling.proposal.plan.component_materials[0].material.thermal_conductivity_w_m_k, 160);
  assert.equal(await page.locator("#draftSourcePower").inputValue(), "7", "Pending suggestions do not silently apply");
  await page.locator("#applyModeling").click();
  await page.waitForFunction(() => document.querySelector("#draftSourcePower").value === "9");
  await page.locator("#undoModeling").click();
  await page.waitForFunction(() => document.querySelector("#draftSourcePower").value === "7");
  await page.locator("#modelingInput").fill("热源功率 11 W");
  await page.locator("#sendModeling").click();
  await page.locator("#modelingProposal").waitFor({ state: "visible" });
  await page.reload();
  await page.locator("#modelingDrawer summary").click();
  await page.locator("#modelingProposal").waitFor({ state: "visible" });
  assert.equal(await page.locator("#draftSourcePower").inputValue(), "7");
  assert((await page.locator("#modelingChanges").textContent()).includes("11 W"));
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({ path: `docs/verification/modeling-${viewport.width}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.locator("#dismissModeling").click();
  await page.locator("#modelingProposal").waitFor({ state: "hidden" });
  const savedModeling = await (await page.request.get(`${url}/v1/studies/${modelingDraft.study_id}`)).json();
  assert.equal(savedModeling.confirmation.status, "needs_input");
  assert.equal(savedModeling.input_snapshot_sha256, null);
  assert.equal(savedModeling.mesh_status, "not_generated");
  assert.equal(savedModeling.plan.heat_source.total_power_w, 7);

  const exchangeDraft = await (await page.request.post(`${url}/v1/studies/${fixture.study_id}/copy`, { data: {} })).json();
  const part = await (await page.request.get(`${url}/v1/workpieces/${fixture.workpiece_id}`)).json();
  const componentRegion = part.regions.find(region => region.kind === "component_surface");
  await page.evaluate(id => {
    const workspace = JSON.parse(localStorage.getItem("thermoflow.workspace.v1"));
    localStorage.setItem("thermoflow.workspace.v1", JSON.stringify({ ...workspace,
      selectedStudyId: id, activeTab: "scenario", visualizationMode: "model" }));
  }, exchangeDraft.study_id);
  await page.reload();
  await page.locator("#draftEnableHeatSource").uncheck();
  await page.locator("#draftEnableGlobalConvection").uncheck();
  while (await page.locator("#fixedBoundaryRows button").count()) await page.locator("#fixedBoundaryRows button").first().click();
  await page.locator("#addSurfaceCondition").click();
  const fluxRow = page.locator(".surface-boundary-row").nth(0);
  await fluxRow.locator(".region-target").selectOption(componentRegion.region_id);
  await fluxRow.locator('[data-property="heat_flux_w_m2"]').fill("5000");
  await page.locator("#addSurfaceCondition").click();
  const radiationRow = page.locator(".surface-boundary-row").nth(1);
  await radiationRow.locator(".condition-kind").selectOption("radiation");
  await radiationRow.locator(".region-target").selectOption(componentRegion.region_id);
  await radiationRow.locator('[data-property="radiation_temperature_k"]').fill("300");
  await radiationRow.locator('[data-property="emissivity"]').fill("0.8");
  await radiationRow.locator('[data-property="emissivity_source"]').fill("Browser verification reference");
  await page.locator("#materialsNav").click();
  await page.locator("#confirmMaterials").check();
  await page.locator("#solveNav").click();
  await page.locator("#confirmInputs").check();
  await page.locator("#primaryAction").click();
  await page.waitForFunction(() => document.querySelector("#primaryAction").textContent === "生成网格");
  const exchange = await (await page.request.get(`${url}/v1/studies/${exchangeDraft.study_id}`)).json();
  assert.equal(exchange.plan.heat_source_enabled, false);
  assert.equal(exchange.plan.global_convection_enabled, false);
  assert.equal(exchange.plan.boundaries.length, 0);
  assert.equal(exchange.plan.surface_conditions.length, 2);
  await page.locator("#primaryAction").click();
  await page.locator("#acceptMeshWarnings").waitFor({ state: "visible", timeout: 60_000 });
  await page.locator("#acceptMeshWarnings").check();
  await page.locator("#primaryAction").click();
  await page.waitForFunction(() => document.querySelector("#primaryAction").textContent === "开始求解");
  await page.locator("#primaryAction").click();
  await page.locator("#boundaryPowerBalance").waitFor({ state: "visible", timeout: 60_000 });
  await page.waitForFunction(() => Number(document.querySelector("#resultMax").textContent.replaceAll(",", "")) > 420);
  assert((await page.locator("#boundaryPowerBalance").textContent()).includes("辐射净输入"));
  await page.screenshot({ path: "docs/verification/regional-radiation-result.png", fullPage: true });

  const boxResponse = await page.request.post(`${url}/v1/workpieces/boxes`, { data: {
    name: "Direct heat-source box", dimensions_mm: { x: 20, y: 10, z: 5 },
  } });
  assert(boxResponse.ok());
  const box = await boxResponse.json();
  const sourceFreeResponse = await page.request.post(`${url}/v1/studies`, { data: {
    workpiece_id: box.workpiece_id, purpose: "两端定温，不设置内部热源",
    overrides: { enable_heat_source: false }, require_confirmation: true,
  } });
  assert(sourceFreeResponse.ok());
  const sourceFree = await sourceFreeResponse.json();
  assert.equal(sourceFree.plan.heat_source, null);
  const confirmedResponse = await page.request.post(`${url}/v1/studies/${sourceFree.study_id}/confirm`, {
    data: { materials_confirmed: true },
  });
  assert(confirmedResponse.ok());
  await page.evaluate(({ projectId, workpieceId, studyId }) => {
    localStorage.setItem("thermoflow.workspace.v1", JSON.stringify({
      selectedProjectId: projectId, selectedWorkpieceId: workpieceId,
      selectedStudyId: studyId, activeTab: "scenario", visualizationMode: "model",
    }));
  }, { projectId: box.project_id, workpieceId: box.workpiece_id, studyId: sourceFree.study_id });
  await page.reload();
  await page.locator("#sourceEditButton").waitFor({ state: "visible" });
  await page.locator("#sourceEditButton").click();
  await page.locator("#sourceEditor").waitFor({ state: "visible" });
  assert.equal(await page.locator("#canvasSourceShape").inputValue(), "point");
  await page.locator("#canvasSourceShape").selectOption("line");
  assert(await page.locator("#canvasSourceEndXField").isVisible());
  await page.locator("#canvasSourceShape").selectOption("surface");
  assert(await page.locator("#canvasSourceWidthField").isVisible());
  await page.locator("#canvasSourceShape").selectOption("point");
  await page.locator("#standardView").selectOption("top");
  const sourceTarget = await page.evaluate(() => {
    const projected = engineeringViewport.local([15, 5, 5]).project(engineeringViewport.camera);
    const rect = engineeringViewport.canvas.getBoundingClientRect();
    return { x: rect.x + (projected.x + 1) * rect.width / 2,
      y: rect.y + (1 - projected.y) * rect.height / 2 };
  });
  await page.mouse.click(sourceTarget.x, sourceTarget.y);
  await page.waitForFunction(() => Math.abs(Number(document.querySelector("#canvasSourceX").value) - 15) < 0.1);
  const dragStart = await page.evaluate(() => {
    const source = engineeringViewport.options.source.center_mm;
    const projected = engineeringViewport.local(Object.values(source)).project(engineeringViewport.camera);
    const rect = engineeringViewport.canvas.getBoundingClientRect();
    return { x: rect.x + (projected.x + 1) * rect.width / 2 + 8,
      y: rect.y + (1 - projected.y) * rect.height / 2 };
  });
  const sourceXBeforeDrag = Number(await page.locator("#canvasSourceX").inputValue());
  await page.mouse.move(dragStart.x, dragStart.y);
  await page.mouse.down();
  await page.mouse.move(dragStart.x + 45, dragStart.y, { steps: 8 });
  await page.mouse.up();
  await page.waitForFunction(before => Math.abs(Number(document.querySelector("#canvasSourceX").value) - before) > 0.2,
    sourceXBeforeDrag);
  const boxSubmission = page.waitForResponse(response => response.request().method() === "POST"
    && /\/v1\/studies\/[^/]+\/tasks$/.test(new URL(response.url()).pathname));
  await page.locator("#applySourceButton").click();
  const boxTaskResponse = await boxSubmission;
  assert.equal(boxTaskResponse.status(), 202);
  const boxTask = await boxTaskResponse.json();
  assert.equal(boxTask.operation, "apply_and_solve");
  assert.notEqual(boxTask.study_id, sourceFree.study_id);
  assert.deepEqual(errors, []);
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
  await new Promise(resolve => { if (server.exitCode != null) resolve(); else server.once("exit", resolve); });
}
