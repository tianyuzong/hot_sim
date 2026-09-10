"use strict";

const STATUS_LABELS = {
  needs_input: "待确认输入",
  ready: "已准备求解",
  planned: "方案就绪",
  rejected: "方案驳回",
  running: "求解中",
  succeeded: "已完成",
  failed: "运行失败",
};

const WORKSPACE_STATE_KEY = "thermoflow.workspace.v1";
const restoredWorkspace = readWorkspaceState();

const state = {
  health: null,
  materials: [],
  projects: [],
  workpieces: [],
  studies: [],
  selectedProjectId: restoredWorkspace.selectedProjectId || null,
  selectedWorkpieceId: restoredWorkspace.selectedWorkpieceId || null,
  selectedStudyId: restoredWorkspace.selectedStudyId || null,
  selectedComponentId: null,
  editingComponentId: null,
  selectedRegionId: null,
  selectedSurfaceFaces: new Set(),
  selectionMode: "component",
  savingRegion: false,
  hiddenComponentIds: new Set(),
  isolatedComponentId: null,
  componentHitTriangles: [],
  boundaryMarkers: [],
  mesh: null,
  result: null,
  timeFrame: null,
  timeIndex: 0,
  timeRequest: 0,
  timeScrubTimer: null,
  timeLoading: false,
  timeFrames: new Map(),
  playbackStudyId: null,
  diffusionScale: null,
  diffusionDefaultedStudyId: restoredWorkspace.diffusionDefaultedStudyId || null,
  timePlaying: false,
  timeTimer: null,
  comparison: null,
  comparisonCandidateResult: null,
  comparisonMode: "baseline",
  agentRun: null,
  parameterMode: "auto",
  view: { yaw: -Math.PI / 5, pitch: 0.42 },
  viewDrag: null,
  sourceDrag: null,
  sourceDraft: null,
  sourcePreviewDirty: false,
  sourceMarker: null,
  pendingUploadOverrides: null,
  visualizationMode: ["model", "mesh", "diffusion", "thermal", "flux", "contour", "slice"].includes(
    restoredWorkspace.visualizationMode,
  ) ? restoredWorkspace.visualizationMode : "thermal",
  heatAnimationFrame: null,
  sliceAxis: "x",
  sliceFraction: 0.5,
  temperaturePoints: [],
  probe: null,
  suppressProbeClick: false,
  activeTab: ["geometry", "materials", "scenario", "mesh", "solve", "result", "evaluation", "agent"].includes(
    restoredWorkspace.activeTab,
  ) ? restoredWorkspace.activeTab : "geometry",
  draftSyncedFor: null,
  materialsSyncedFor: null,
  materialsConfirmedFor: null,
  draftBaseRevision: 0,
  draftDirty: false,
  draftEditSerial: 0,
  draftSaveTimer: null,
  draftSavePromise: null,
  draftSaveError: false,
  modelingBusy: false,
  modelingDecisionBusy: false,
  modelingSessionHistory: new Map(),
  busy: false,
  tasks: [],
  taskPollTimer: null,
  taskPollRunning: false,
  taskConnectionLost: false,
  assistantSessionId: restoredWorkspace.assistantSessionId || null,
  assistantSession: null,
  assistantSessions: [],
  assistantBusy: false,
  assistantPendingMessage: "",
  assistantPendingAnswers: [],
  assistantStreamAnswer: "",
  assistantStreamStatus: "",
  assistantRequestGeneration: 0,
  assistantCollapsed: Boolean(restoredWorkspace.assistantCollapsed),
  sidebarView: restoredWorkspace.sidebarView === "assistant" ? "assistant" : "project",
};

function readWorkspaceState() {
  try {
    const value = JSON.parse(window.localStorage.getItem(WORKSPACE_STATE_KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function persistWorkspaceState() {
  try {
    window.localStorage.setItem(WORKSPACE_STATE_KEY, JSON.stringify({
      selectedProjectId: state.selectedProjectId,
      selectedWorkpieceId: state.selectedWorkpieceId,
      selectedStudyId: state.selectedStudyId,
      activeTab: state.activeTab,
      visualizationMode: state.visualizationMode,
      diffusionDefaultedStudyId: state.diffusionDefaultedStudyId,
      assistantSessionId: state.assistantSessionId,
      assistantCollapsed: state.assistantCollapsed,
      sidebarView: state.sidebarView,
    }));
  } catch {
    // The workbench remains usable when browser storage is unavailable.
  }
}

const elements = Object.fromEntries(
  [
    "assistantRail", "assistantConversation", "assistantStatus", "assistantHistory", "assistantBinding", "assistantMessages", "assistantAnswer", "assistantCitations", "assistantQuestionForm",
    "assistantForm", "assistantInput", "assistantSend", "assistantRetry", "assistantNew", "assistantReview", "assistantReadiness",
    "assistantSummary", "assistantSummaryConfirmed", "assistantMaterialsConfirmed", "assistantOpenStudyForm", "assistantLaunch",
    "modelingDrawer", "draftSaveStatus", "modelingMessages", "modelingPrivacy", "modelingProposal",
    "modelingChanges", "modelingValidation", "modelingForm", "modelingInput", "applyModeling",
    "dismissModeling", "undoModeling", "reloadDraft", "sendModeling",
    "geometrySelectionMode", "surfaceSelectionTools", "surfaceSelectionCount", "saveSurfaceSelection",
    "clearSurfaceSelection", "surfaceRegionDialog", "surfaceRegionForm", "surfaceRegionName",
    "cancelSurfaceRegion", "submitSurfaceRegion", "fixedBoundaryEditor", "fixedBoundaryRows",
    "addFixedBoundary", "boundaryMapping",
    "draftEnableHeatSource", "draftEnableGlobalConvection", "surfaceThermalEditor",
    "surfaceConditionRows", "addSurfaceCondition", "thermalContactEditor",
    "thermalContactRows", "addThermalContact",
    "boundaryPowerBalance",
    "draftAnalysisType", "draftInitialTemperature", "draftDuration", "draftTimeStep",
    "timeControls", "timePlay", "timePosition", "timeLabel", "timeLoading", "timeLockScale",
    "resultResistanceLabel", "resultTimePeakRow", "resultTimePeak",
    "computationStatus", "computationTitle", "computationState", "computationMessage",
    "computationProgress", "computationElapsed", "cancelComputation", "resultAnalysisLabel",
    "serviceDot",
    "serviceLabel",
    "plannerLabel",
    "cadflowLabel",
    "refreshButton",
    "diagnosticsButton",
    "diagnosticsDialog",
    "closeDiagnosticsButton",
    "diagnosticService",
    "diagnosticCompute",
    "diagnosticAgent",
    "geometryNav",
    "materialsNav",
    "scenarioNav",
    "meshNav",
    "solveNav",
    "resultNav",
    "evaluationNav",
    "agentNav",
    "newWorkpieceButton",
    "importProjectMode",
    "currentProjectOption",
    "workpieceSearch",
    "workpieceList",
    "projectSidebarPane",
    "projectSidebarTab",
    "assistantSidebarTab",
    "componentTree",
    "componentCount",
    "componentList",
    "componentDialog",
    "componentRenameForm",
    "componentNameInput",
    "closeComponentDialogButton",
    "cancelComponentRenameButton",
    "workpieceCount",
    "studyCount",
    "workspaceKind",
    "workspaceTitle",
    "workspaceState",
    "workpieceCanvas",
    "resetViewButton",
    "sourceEditButton",
    "visualizationModes",
    "modelViewButton",
    "meshViewButton",
    "diffusionViewButton",
    "thermalViewButton",
    "heatFluxViewButton",
    "contourViewButton",
    "sliceViewButton",
    "sliceControls",
    "sliceAxis",
    "slicePosition",
    "slicePositionLabel",
    "probeReadout",
    "sourceEditorSlot",
    "sourceEditor",
    "closeSourceEditorButton",
    "sourcePositionReadout",
    "sourceList",
    "addHeatSource",
    "deleteHeatSource",
    "canvasSourceName",
    "canvasSourceShape",
    "canvasSourcePlacement",
    "canvasSourceDepthField",
    "canvasSourceDepth",
    "canvasSourcePower",
    "canvasSourceRadiusField",
    "canvasSourceRadiusLabel",
    "canvasSourceRadius",
    "canvasSourceX",
    "canvasSourceY",
    "canvasSourceZ",
    "canvasSourceEndXField",
    "canvasSourceEndYField",
    "canvasSourceEndZField",
    "canvasSourceEndX",
    "canvasSourceEndY",
    "canvasSourceEndZ",
    "canvasSourceSurfaceAxisField",
    "canvasSourceSurfaceAxis",
    "canvasSourceWidthField",
    "canvasSourceWidth",
    "canvasSourceHeightField",
    "canvasSourceHeight",
    "canvasSourceThicknessField",
    "canvasSourceThickness",
    "canvasSourceVolumeWidthField",
    "canvasSourceVolumeHeightField",
    "canvasSourceVolumeDepthField",
    "canvasSourceVolumeWidth",
    "canvasSourceVolumeHeight",
    "canvasSourceVolumeDepth",
    "canvasAmbientTemperature",
    "canvasConvectionCoefficient",
    "canvasDurationField",
    "canvasTimeStepField",
    "canvasDuration",
    "canvasTimeStep",
    "resetSourceButton",
    "applySourceButton",
    "emptyStage",
    "thermalLegend",
    "thermalLegendTitle",
    "diffusionReference",
    "coldTemperature",
    "midTemperature",
    "hotTemperature",
    "geometryStep",
    "planStep",
    "meshStep",
    "solveStep",
    "geometryEngine",
    "propertyGrid",
    "selectedStudyId",
    "studyList",
    "planTab",
    "resultTab",
    "agentTab",
    "planPane",
    "resultPane",
    "evaluationPane",
    "agentPane",
    "geometryPanel",
    "materialsPanel",
    "scenarioPanel",
    "meshPanel",
    "solvePanel",
    "geometryQuality",
    "unitNotice",
    "unitForm",
    "lengthUnit",
    "unitDimensions",
    "geometryChecks",
    "geometryRegions",
    "geometryRegionCount",
    "geometryRegionList",
    "geometryLimitations",
    "materialsEmpty",
    "materialsCreateDraftButton",
    "editMaterialsButton",
    "editParametersButton",
    "manualDraftButton",
    "materialsGoScenario",
    "componentMaterialList",
    "materialConfirmationNotice",
    "materialConfirmationCheck",
    "confirmMaterials",
    "materialDetail",
    "materialConductivity",
    "materialDensitySummary",
    "materialHeatCapacity",
    "materialEmissivity",
    "materialSource",
    "studyDraftForm",
    "studyPurpose",
    "generateDraftButton",
    "draftState",
    "structuredInputs",
    "draftSourcePower",
    "draftSourceRadius",
    "sourceQuickActions",
    "openSourceEditorFromScenario",
    "draftAmbient",
    "draftConvection",
    "draftHeatAxis",
    "draftMinTemperature",
    "draftMaxTemperature",
    "draftCriterionMax",
    "missingInformation",
    "unsupportedPhysics",
    "draftMeshSize",
    "effectiveMeshSize",
    "meshCellCountLabel",
    "estimatedCells",
    "meshPointCountLabel",
    "estimatedPoints",
    "surfaceCells",
    "occupiedLayers",
    "meshConnectivity",
    "meshVolumeDeviation",
    "meshRisk",
    "meshQuality",
    "meshWarnings",
    "meshReviewCheck",
    "acceptMeshWarnings",
    "meshArtifact",
    "confirmationCheck",
    "confirmInputs",
    "evaluationTitle",
    "evaluationBadge",
    "evaluationCallout",
    "evaluationList",
    "evaluationAssumptions",
    "planEmpty",
    "planContent",
    "planName",
    "planConfidence",
    "planMaterial",
    "planConductivity",
    "planBoundaries",
    "planSource",
    "planConvection",
    "planMesh",
    "planTolerance",
    "planSummary",
    "planAssumptions",
    "resultEmpty",
    "resultContent",
    "resultMin",
    "resultMax",
    "resultHeatRate",
    "resultResistance",
    "resultFlux",
    "resultRise",
    "resultSpan",
    "resultHotspot",
    "resultPoints",
    "resultCells",
    "resultCompute",
    "resultError",
    "materialRangeWarning",
    "copyStudyButton",
    "comparisonTool",
    "comparisonAvailability",
    "comparisonCandidate",
    "compareStudiesButton",
    "exitComparisonButton",
    "comparisonContent",
    "comparisonScale",
    "comparisonBaselineMax",
    "comparisonBaselineName",
    "comparisonCandidateMax",
    "comparisonCandidateName",
    "comparisonMaxDelta",
    "comparisonHeatDelta",
    "comparisonResistanceDelta",
    "comparisonBaselineMode",
    "comparisonCandidateMode",
    "comparisonDifferenceMode",
    "comparisonNotice",
    "artifactList",
    "agentEmpty",
    "agentContent",
    "agentMode",
    "agentForm",
    "agentInstruction",
    "agentTargetMax",
    "agentTargetMin",
    "agentTemperatureTolerance",
    "agentEnergyTolerance",
    "agentMaxRounds",
    "agentAllowPower",
    "agentAllowMesh",
    "runAgentButton",
    "agentRun",
    "agentRunStatus",
    "agentRoundCount",
    "agentRunSummary",
    "agentSelectedStudy",
    "agentTrace",
    "primaryAction",
    "workpieceDialog",
    "workpieceForm",
    "closeDialogButton",
    "cancelDialogButton",
    "cadFile",
    "fileLabel",
    "autoModeButton",
    "customModeButton",
    "customParameterFields",
    "materialName",
    "thermalConductivity",
    "materialDensity",
    "specificHeat",
    "heatAxis",
    "minFaceTemperature",
    "maxFaceTemperature",
    "heatSourceX",
    "heatSourceY",
    "heatSourceZ",
    "heatSourceShape",
    "heatSourcePlacement",
    "heatSourceDepth",
    "heatSourcePower",
    "heatSourceRadius",
    "heatSourceEndX",
    "heatSourceEndY",
    "heatSourceEndZ",
    "heatSourceSurfaceAxis",
    "heatSourceWidth",
    "heatSourceHeight",
    "heatSourceThickness",
    "heatSourceVolumeWidth",
    "heatSourceVolumeHeight",
    "heatSourceVolumeDepth",
    "ambientTemperature",
    "convectionCoefficient",
    "targetElementSize",
    "maxAxisIntervals",
    "relativeTolerance",
    "maxIterations",
    "submitWorkpieceButton",
    "toast",
  ].map((id) => [id, document.getElementById(id)]),
);

if (elements.assistantRail) elements.assistantRail.open = !state.assistantCollapsed;

elements.sourceEditorSlot.append(elements.sourceEditor);

let toastTimer = null;
let engineeringViewport = null;
let probeRequestTicket = 0;
let validationFeedback = null;

function validationFields() {
  const fields = new Map();
  const add = (path, node, tab = 'scenario', activate = null) => {
    if (node || activate) fields.set(path, {node, tab, activate});
  };
  for (const [path, id] of Object.entries({
    analysis_type: 'draftAnalysisType', analyses: 'draftAnalysisType',
    initial_temperature_k: 'draftInitialTemperature', duration_s: 'draftDuration', time_step_s: 'draftTimeStep',
    'heat_source.total_power_w': 'draftSourcePower', 'heat_source.radius_mm': 'draftSourceRadius',
    'convection.ambient_temperature_k': 'draftAmbient',
    'convection.heat_transfer_coefficient_w_m2_k': 'draftConvection',
  })) add(path, elements[id]);
  add('mesh.target_element_size_mm', elements.draftMeshSize, 'mesh');
  add('length_unit', elements.lengthUnit, 'geometry');
  [...elements.fixedBoundaryRows.children].forEach((row, index) => {
    add(`boundaries.${index}.selector`, row.querySelector('select'));
    add(`boundaries.${index}.region_id`, row.querySelector('select'));
    add(`boundaries.${index}.temperature_k`, row.querySelector('input'));
  });
  if (selectedWorkpiece()?.cad_format !== 'stl') {
    (selectedStudy()?.plan?.boundaries || []).forEach((boundary, index) => {
      add(`boundaries.${index}.selector`, elements.draftHeatAxis);
      add(`boundaries.${index}.temperature_k`, boundary.selector?.endsWith('min')
        ? elements.draftMinTemperature : elements.draftMaxTemperature);
    });
  }
  [...elements.surfaceConditionRows.children].forEach((row, index) => {
    add(`surface_conditions.${index}.region_id`, row.querySelector('.region-target'));
    add(`surface_conditions.${index}.kind`, row.querySelector('.condition-kind'));
    row.querySelectorAll('[data-property]').forEach(node => add(`surface_conditions.${index}.${node.dataset.property}`, node));
  });
  [...elements.thermalContactRows.children].forEach((row, index) => {
    for (const [property, selector] of Object.entries({
      source_component_id: '.source-component', target_component_id: '.target-component',
      source_region_id: '.source-region', target_region_id: '.target-region',
      contact_resistance_m2_k_w: '.contact-resistance', contact_area_m2: '.contact-area', max_gap_mm: '.contact-gap',
    })) add(`contacts.${index}.${property}`, row.querySelector(selector));
  });
  (selectedStudy()?.plan?.criteria || []).forEach((criterion, index) => {
    if (criterion.metric !== 'max_temperature') return;
    add(`criteria.${index}.target.value`, elements.draftCriterionMax);
    add(`criteria.${index}.target.unit`, elements.draftCriterionMax);
  });
  [...elements.componentMaterialList.children].forEach((row, index) => {
    add(`component_materials.${index}.material_id`, row.querySelector('select'), 'materials');
    row.querySelectorAll('[data-material-property]').forEach(node => {
      add(`component_materials.${index}.material.${node.dataset.materialProperty}`, node, 'materials');
      if (index === 0) add(`material.${node.dataset.materialProperty}`, node, 'materials');
    });
  });
  const sourceFields = {
    shape: 'canvasSourceShape', center_mm: 'canvasSourceX', end_mm: 'canvasSourceEndX',
    'center_mm.x': 'canvasSourceX', 'center_mm.y': 'canvasSourceY', 'center_mm.z': 'canvasSourceZ',
    'end_mm.x': 'canvasSourceEndX', 'end_mm.y': 'canvasSourceEndY', 'end_mm.z': 'canvasSourceEndZ',
    total_power_w: 'canvasSourcePower', radius_mm: 'canvasSourceRadius',
    surface_normal_axis: 'canvasSourceSurfaceAxis', surface_width_mm: 'canvasSourceWidth',
    surface_height_mm: 'canvasSourceHeight', surface_thickness_mm: 'canvasSourceThickness',
    volume_width_mm: 'canvasSourceVolumeWidth', volume_height_mm: 'canvasSourceVolumeHeight',
    volume_depth_mm: 'canvasSourceVolumeDepth', embedding_depth_mm: 'canvasSourceDepth',
  };
  ThermoFlowSources.sourcesFromPlan(selectedStudy()?.plan).forEach((source, index) => {
    for (const [property, id] of Object.entries(sourceFields)) {
      add(`heat_sources.${index}.${property}`, activeSourceCollection()?.activeIndex === index ? elements[id] : null,
        'scenario', () => {
          activeSourceCollection(true);
          selectHeatSource(index, {openEditor: true});
          return elements[id];
        });
      if (index === 0 && !fields.has(`heat_source.${property}`)) {
        fields.set(`heat_source.${property}`, fields.get(`heat_sources.${index}.${property}`));
      }
    }
  });
  return fields;
}

function locateValidationFields(paths) {
  const fields = validationFields();
  const target = paths.map(path => fields.get(path)).find(Boolean);
  if (!target) {
    switchTab('scenario');
    document.getElementById('validationFeedback').scrollIntoView({block: 'nearest'});
    return;
  }
  switchTab(target.tab);
  const node = target.activate ? target.activate() : target.node;
  renderValidationFeedback();
  if (node) {
    node.scrollIntoView({block: 'center', behavior: 'instant'});
    node.focus({preventScroll: true});
  }
}

function renderValidationFeedback() {
  const root = document.getElementById('validationFeedback');
  if (!validationFeedback) validationFeedback = new ThermoFlowValidation.Feedback(root, locateValidationFields);
  const study = selectedStudy();
  const failure = state.validationFailure?.studyId === study?.study_id ? state.validationFailure.message : '';
  const fields = validationFields();
  const inputIssues = [];
  const inspected = new Set();
  if (study?.plan && study.confirmation?.status !== 'confirmed') for (const [path, {node}] of fields) {
    if (!node || inspected.has(node) || node.disabled || node.closest('label')?.hidden
      || node.closest('#sourceEditor')?.hidden || !node.willValidate) continue;
    inspected.add(node);
    if (node.checkValidity()) continue;
    const label = node.closest('label');
    const name = [...(label?.childNodes || [])]
      .filter(child => child.nodeType === 3 || child.tagName === 'SPAN').map(child => child.textContent).join('').trim() || path;
    const prefix = path.startsWith('boundaries.') ? `定温边界第 ${Number(path.split('.')[1]) + 1} 行 · ` : '';
    const reason = node.validity.valueMissing ? '这是必填项，当前未填写'
      : node.validity.badInput ? '请输入有效数字'
      : node.validity.rangeUnderflow ? `当前 ${node.value}，不能小于 ${node.min}`
      : node.validity.rangeOverflow ? `当前 ${node.value}，不能大于 ${node.max}` : '格式不符合要求';
    inputIssues.push({severity: 'error', message: `${prefix}${name}：${reason}。`,
      suggestion: path.endsWith('temperature_k') ? '请填写有效的 K 温度；20 ℃ 应填写 293.15 K。' : '请填写符合范围的有效参数。',
      fields: [path]});
  }
  const checking = state.validationPending?.studyId === study?.study_id
    && state.validationPending?.revision === study?.draft_revision;
  const issues = validationFeedback.render(study, {pending: state.draftDirty || checking, failure, fields, inputIssues});
  // Make Kelvin inputs legible without changing their stored unit or value.
  for (const [path, {node}] of fields) {
    if (!node || !path.endsWith('_temperature_k') && !path.endsWith('.temperature_k')) continue;
    const label = node.closest('label');
    if (!label) continue;
    let equivalent = label.querySelector('.temperature-equivalent');
    if (!equivalent) {
      equivalent = createElement('small', 'temperature-equivalent');
      label.append(equivalent);
    }
    equivalent.textContent = node.value !== '' && Number.isFinite(Number(node.value))
      ? `相当于 ${formatNumber(Number(node.value) - 273.15)} ℃` : '';
  }
  return issues;
}

async function loadSelectedValidation() {
  const study = selectedStudy();
  if (!study?.plan || study.confirmation?.status === 'confirmed') return false;
  const revision = study.draft_revision;
  const pending = {studyId: study.study_id, revision};
  state.validationPending = pending;
  const isCurrent = () => state.validationPending === pending
    && selectedStudy()?.study_id === study.study_id
    && selectedStudy()?.draft_revision === revision && !state.draftDirty;
  renderValidationFeedback();
  try {
    const policy = await request(`/v1/studies/${study.study_id}/validation?expected_revision=${revision}`);
    if (!isCurrent()) return false;
    selectedStudy().policy = policy;
    state.validationFailure = null;
    return true;
  } catch (error) {
    if (isCurrent()) {
      state.validationFailure = {studyId: study.study_id, message: `参数检查读取失败：${error.message}`};
    }
    return false;
  } finally {
    if (state.validationPending === pending) {
      state.validationPending = null;
      renderValidationFeedback();
    }
  }
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        message = payload.detail.map((item) => item?.msg || item?.message || String(item)).join("；");
      } else if (payload.detail?.message) {
        const diagnostics = payload.detail.diagnostics?.join("；");
        message = diagnostics ? `${payload.detail.message}：${diagnostics}` : payload.detail.message;
      }
    } catch {
      // Keep the HTTP fallback when an error response is not JSON.
    }
    throw new Error(message);
  }
  return response.json();
}

function errorMessage(error, fallback = "未知错误") {
  if (typeof error === "string" && error.trim()) return error;
  if (error && typeof error.message === "string" && error.message.trim()) return error.message;
  return fallback;
}

async function loadWorkspace({ preserveSelection = true } = {}) {
  if (typeof loadAssistantSession === "function") await loadAssistantSession();
  const previousProject = preserveSelection ? state.selectedProjectId : null;
  const previousWorkpiece = preserveSelection ? state.selectedWorkpieceId : null;
  const previousStudy = preserveSelection ? state.selectedStudyId : null;
  try {
    const [health, projects, materials] = await Promise.all([
      request("/health"),
      request("/v1/projects"),
      request("/v1/materials"),
    ]);
    let assistantSessions = [];
    try {
      const records = await request("/v1/assistant-sessions");
      assistantSessions = Array.isArray(records) ? records : [];
    } catch (error) {
      console.warn("Agent 历史会话暂时不可用", error);
    }
    state.assistantSessions = assistantSessions;
    if (!state.assistantSessionId && assistantSessions.length) {
      state.assistantSessionId = assistantSessions[0].session_id;
    }
    if (typeof loadAssistantSession === "function") await loadAssistantSession();
    if (typeof renderAssistant === "function") renderAssistant();
    const [workpieces, studies, tasks] = await Promise.all([
      request("/v1/workpieces"),
      request("/v1/studies"),
      request("/v1/tasks"),
    ]);
    state.health = health;
    state.projects = projects;
    state.workpieces = workpieces;
    state.studies = studies;
    state.tasks = tasks;
    state.materials = materials;
    renderUploadMaterialOptions();
    const previousProjectStillExists = projects.some(
      (item) => item.project_id === previousProject,
    );
    const previousWorkpieceRecord = workpieces.find(
      (item) => item.workpiece_id === previousWorkpiece,
    );
    state.selectedProjectId = previousProjectStillExists
      ? previousProject
      : previousWorkpieceRecord?.project_id || projects[0]?.project_id || null;
    const project = selectedProject();
    const projectWorkpieces = selectedProjectWorkpieces();
    state.selectedWorkpieceId = projectWorkpieces.some(
      (item) => item.workpiece_id === previousWorkpiece,
    )
      ? previousWorkpiece
      : projectWorkpieces.find((item) => item.workpiece_id === project?.active_workpiece_id)
        ?.workpiece_id || projectWorkpieces[0]?.workpiece_id || null;
    selectLatestStudy(previousStudy);
    persistWorkspaceState();
    const selectedData = Promise.all([loadSelectedMesh(), loadSelectedResult(), loadSelectedAgentRun(), loadSelectedValidation()]);
    render();
    await selectedData;
    render();
    startHeatAnimation();
    scheduleTaskPoll();
  } catch (error) {
    state.health = null;
    renderHealth();
    console.error("工作区数据加载失败", error);
    showToast(`数据加载失败：${errorMessage(error)}`, true);
  }
}

function renderUploadMaterialOptions() {
  const select = elements.materialName;
  if (!select) return;
  const current = select.value;
  select.replaceChildren(
    createElement("option", "", "自动补齐（导入后可修改）"),
    ...state.materials.map((entry) => {
      const option = createElement("option", "", entry.material.name);
      option.value = entry.material.name;
      return option;
    }),
  );
  if (state.materials.some((entry) => entry.material.name === current)) select.value = current;
}

function selectLatestStudy(preferredId = null, workpieceId = state.selectedWorkpieceId) {
  const relevant = selectedProjectStudies().filter(
    (item) => workpieceId === null || item.workpiece_id === workpieceId,
  );
  state.selectedStudyId = relevant.some((item) => item.study_id === preferredId)
    ? preferredId
    : relevant[0]?.study_id || null;
  const selected = selectedStudy();
  if (selected) state.selectedWorkpieceId = selected.workpiece_id;
}

async function loadSelectedResult() {
  stopTimePlayback();
  cancelScheduledTimeStep();
  state.timeRequest += 1;
  state.timeFrame = null;
  state.timeLoading = false;
  state.timeFrames.clear();
  state.playbackStudyId = null;
  state.diffusionScale = null;
  elements.timeControls.hidden = true;
  state.result = null;
  renderTimeControls();
  clearComparison();
  state.probe = null;
  state.temperaturePoints = [];
  const study = selectedStudy();
  if (study?.status !== "succeeded") return;
  try {
    const result = await request(`/v1/studies/${study.study_id}/result`);
    if (selectedStudy()?.study_id !== study.study_id) return;
    state.result = result;
    state.timeIndex = 0;
    renderTimeControls();
    if (result.time_steps?.length) {
      state.timeLoading = true;
      renderTimeControls();
      const playback = await request(`/v1/studies/${study.study_id}/playback`);
      if (selectedStudy()?.study_id !== study.study_id) return;
      if (playback.frames.length !== result.time_steps.length) {
        throw new Error("批量播放帧与求解时间步数量不一致，请重新求解");
      }
      const geometry = {
        coordinate_unit: playback.coordinate_unit,
        vertices: playback.vertices,
        triangles: playback.triangles,
        component_ids: playback.component_ids,
        cell_ids: playback.cell_ids,
        total_cells: playback.total_cells,
        sampled: false,
      };
      for (const frame of playback.frames) {
        const step = result.time_steps.find(item => item.index === frame.index);
        if (!step || !Array.isArray(frame.temperature_k)
          || frame.temperature_k.length !== geometry.vertices.length) {
          throw new Error("批量播放帧缺失或温度数组与共享网格不一致，请重新求解");
        }
        state.timeFrames.set(`${study.study_id}:${frame.index}`, {
          study_id: study.study_id,
          step,
          surface: {
            ...geometry,
            source_sha256: frame.source_sha256,
            time_s: frame.time_s,
            temperature_k: frame.temperature_k,
            heat_flux_w_m2: null,
            minimum_position_mm: frame.minimum_position_mm,
            maximum_position_mm: frame.maximum_position_mm,
            temperature_min_k: frame.temperature_min_k,
            temperature_max_k: frame.temperature_max_k,
            heat_flux_min_w_m2: null,
            heat_flux_max_w_m2: null,
          },
        });
      }
      state.diffusionScale = ThermoFlowTransient.diffusionScale(playback.frames);
      state.visualizationMode = "thermal";
      persistWorkspaceState();
      state.playbackStudyId = study.study_id;
      state.timeLoading = false;
      await selectTimeStep(state.timeIndex);
    }
  } catch (error) {
    state.timeLoading = false;
    renderTimeControls();
    showToast(`结果读取失败：${error.message}`, true);
  }
}

async function loadSelectedMesh() {
  state.mesh = null;
  const study = selectedStudy();
  if (!study || !["needs_review", "ready", "blocked"].includes(study.mesh_status)) return;
  try {
    state.mesh = await request(`/v1/studies/${study.study_id}/mesh`);
  } catch (error) {
    showToast(`网格读取失败：${error.message}`, true);
  }
}

async function loadSelectedAgentRun() {
  state.agentRun = null;
  if (!state.selectedProjectId || !state.selectedStudyId) return;
  try {
    const runs = await request(
      `/v1/agent-runs?project_id=${encodeURIComponent(state.selectedProjectId)}`,
    );
    state.agentRun = runs.find((run) => [
      run.base_study_id,
      run.selected_study_id,
      ...(run.candidate_study_ids || []),
    ].includes(state.selectedStudyId)) || null;
  } catch (error) {
    showToast(`Agent 记录读取失败：${error.message}`, true);
  }
}

function selectedProject() {
  return state.projects.find((item) => item.project_id === state.selectedProjectId) || null;
}

function selectedProjectWorkpieces() {
  return state.workpieces.filter((item) => item.project_id === state.selectedProjectId);
}

function selectedWorkpiece() {
  return state.workpieces.find((item) => item.workpiece_id === state.selectedWorkpieceId) || null;
}

function selectedStudy() {
  return state.studies.find((item) => item.study_id === state.selectedStudyId) || null;
}

function selectedProjectStudies() {
  return state.studies.filter((item) => item.project_id === state.selectedProjectId);
}

function pendingAssistantAnswer(questionId) {
  return (state.assistantPendingAnswers || []).find(
    (answer) => answer.question_id === questionId,
  ) || null;
}

function invalidateAgentStudyForm(studyId) {
  if (!studyId || state.draftSyncedFor !== studyId) return;
  clearTimeout(state.draftSaveTimer);
  state.draftSaveTimer = null;
  state.draftDirty = false;
  state.draftSaveError = false;
  state.draftEditSerial += 1;
  state.draftSyncedFor = null;
  state.materialsSyncedFor = null;
  if (state.materialsConfirmedFor === studyId) state.materialsConfirmedFor = null;
  discardSourceDraft();
}

function renderAssistant() {
  const session = state.assistantSession
    || state.assistantSessions.find(item => item.session_id === state.assistantSessionId)
    || null;
  if (!elements.assistantStatus) return;
  const conversation = elements.assistantConversation || elements.assistantMessages;
  const sessionKey = state.assistantSessionId || "current";
  const wasAtBottom = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 24;
  const shouldFollow = conversation.dataset.sessionKey !== sessionKey || wasAtBottom;
  const currentSessionOption = createElement("option", "", "当前会话");
  currentSessionOption.value = "";
  elements.assistantHistory.replaceChildren(currentSessionOption);
  state.assistantSessions.slice(0, 30).forEach(item => {
    const first = item.messages?.find(message => message.role === "user")?.content || item.answer || item.session_id;
    const option = createElement("option", "", `${first.slice(0, 42)}${first.length > 42 ? "..." : ""}`);
    option.value = item.session_id;
    elements.assistantHistory.append(option);
  });
  elements.assistantHistory.value = state.assistantSessionId || "";
  const boundStudy = session?.study_id
    ? state.studies.find(item => item.study_id === session.study_id)
    : null;
  elements.assistantBinding.hidden = !session;
  elements.assistantBinding.textContent = session?.study_id
    ? `已绑定研究：${boundStudy?.plan?.study_name || session.study_id}（${session.study_id}）`
    : session?.workpiece_id
    ? `已绑定工件：${session.workpiece_id}；尚未创建研究`
    : "未绑定工件或研究";
  const statuses = {
    awaiting_goal: "等待描述", awaiting_geometry: "等待工件", awaiting_unit: "等待尺度",
    clarifying: "需要补充", ready_for_review: "待确认", queued: "排队中", running: "计算中",
    needs_mesh_review: "待检查网格", completed: "已完成", failed: "未完成",
  };
  elements.assistantStatus.textContent = state.assistantBusy
    ? "处理中..."
    : session ? (statuses[session.status] || session.status) : "未开始";
  elements.assistantMessages.replaceChildren();
  if (session?.messages) {
    const messages = session.messages;
    elements.assistantMessages.replaceChildren(...messages.map(message => {
      const item = createElement("p", "assistant-message", message.content);
      item.dataset.role = message.role;
      return item;
    }));
  }
  if (state.assistantPendingMessage) {
    const item = createElement("p", "assistant-message", state.assistantPendingMessage);
    item.dataset.role = "user";
    elements.assistantMessages.append(item);
  }
  if (state.assistantBusy && state.assistantStreamAnswer) {
    const item = createElement("p", "assistant-message", state.assistantStreamAnswer);
    item.dataset.role = "assistant";
    elements.assistantMessages.append(item);
  }
  if (state.assistantBusy) {
    if (!state.assistantStreamAnswer) {
      elements.assistantMessages.append(createElement(
        "p", "assistant-message assistant-message-busy",
        state.assistantStreamStatus || "正在请求 Agent，请稍候…",
      ));
    }
  }
  conversation.dataset.sessionKey = sessionKey;
  if (shouldFollow) {
    requestAnimationFrame(() => {
      conversation.scrollTop = conversation.scrollHeight;
    });
  }
  const answerIsInConversation = session?.messages?.at(-1)?.role === "assistant"
    && session.messages.at(-1).content === session.answer;
  elements.assistantAnswer.hidden = !session?.answer || answerIsInConversation;
  elements.assistantAnswer.textContent = session?.answer || "";
  elements.assistantCitations.hidden = !session?.citations?.length;
  elements.assistantCitations.replaceChildren(...(session?.citations || []).map(citation => {
    const item = createElement("span", "assistant-citation", citation.label);
    item.title = citation.source_type === "general_knowledge" ? "通用知识，未由 ThermoFlow 求解器验证" : citation.label;
    return item;
  }));
  elements.assistantQuestionForm.hidden = !session?.questions?.length;
  const questionFields = (session?.questions || []).map(question => {
    const pendingAnswer = pendingAssistantAnswer(question.question_id);
    const fieldset = document.createElement("fieldset");
    fieldset.className = "assistant-question";
    fieldset.dataset.questionId = question.question_id;
    const legend = document.createElement("legend");
    legend.textContent = question.prompt + (question.unit ? ` (${question.unit})` : "");
    fieldset.append(legend, createElement("small", "assistant-question-rationale", question.rationale));
    if (question.type === "single_choice" || question.type === "multi_choice") {
      (question.options || []).forEach(option => {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = question.type === "single_choice" ? "radio" : "checkbox";
        input.name = question.question_id;
        input.value = option.option_id;
        input.checked = Boolean(pendingAnswer?.option_ids?.includes(option.option_id));
        input.disabled = state.assistantBusy;
        label.append(input, document.createTextNode(` ${option.label}`));
        fieldset.append(label);
      });
    } else if (question.type === "number" || question.type === "text") {
      const input = document.createElement(question.type === "number" ? "input" : "textarea");
      input.dataset.answerValue = "true";
      input.name = question.question_id;
      if (question.type === "number") {
        input.type = "number";
        if (question.minimum != null) input.min = question.minimum;
        if (question.maximum != null) input.max = question.maximum;
        if (question.step != null) input.step = question.step;
      } else input.rows = 2;
      input.required = question.required;
      if (question.type === "number" && pendingAnswer?.number_value != null) {
        input.value = String(pendingAnswer.number_value);
      } else if (question.type === "text" && pendingAnswer?.text_value) {
        input.value = pendingAnswer.text_value;
      }
      input.disabled = state.assistantBusy;
      fieldset.append(input);
    } else {
      const button = createElement("button", "source-reset-button", "打开几何导入");
      button.type = "button";
      button.disabled = state.assistantBusy;
      button.addEventListener("click", openWorkpieceDialog);
      fieldset.append(button);
    }
    return fieldset;
  });
  if (questionFields.length) {
    const actions = document.createElement("div");
    actions.className = "assistant-question-actions";
    const submit = createElement("button", "primary-button", "确认并继续");
    submit.type = "submit";
    submit.disabled = state.assistantBusy;
    actions.append(submit);
    elements.assistantQuestionForm.replaceChildren(...questionFields, actions);
  } else {
    elements.assistantQuestionForm.replaceChildren();
  }
  const reviewable = session?.status === "ready_for_review" && session.study_id;
  const reviewSessionId = reviewable ? session.session_id : "";
  if (elements.assistantReview.dataset.sessionId !== reviewSessionId) {
    elements.assistantReview.dataset.sessionId = reviewSessionId;
    elements.assistantSummaryConfirmed.checked = false;
    elements.assistantMaterialsConfirmed.checked = false;
  }
  elements.assistantReview.hidden = !reviewable;
  const study = session?.study_id ? state.studies.find(item => item.study_id === session.study_id) : null;
  const plan = study?.plan;
  if (plan) {
    const sources = plan.heat_sources?.length ? plan.heat_sources : plan.heat_source ? [plan.heat_source] : [];
    const boundary = (plan.boundaries || []).map(item => `${item.selector || item.region_id || "区域"} ${item.temperature_k} K`);
    const rows = [
      ["分析", plan.analysis_type === "transient_conduction" ? "瞬态导热" : "稳态导热"],
      ["材料", (plan.component_materials || []).map(item => item.material.name).filter(Boolean).join("、") || plan.material?.name || "未设置"],
      ["边界", boundary.join("；") || `${plan.surface_conditions?.length || 0} 个区域条件`],
      ["热源", plan.heat_source_enabled && sources.length ? `${sources.length} 个，总功率 ${sources.reduce((sum, item) => sum + Number(item.total_power_w || 0), 0)} W` : "未启用"],
      ["网格", plan.mesh ? `${plan.mesh.target_element_size_mm} mm` : "未设置"],
      ["判据", plan.criteria?.length ? `${plan.criteria.length} 条` : "未设置（探索性仿真）"],
    ];
    elements.assistantSummary.replaceChildren(...rows.flatMap(([label, value]) => [
      createElement("dt", "", label), createElement("dd", "", value),
    ]));
  } else elements.assistantSummary.replaceChildren();
  elements.assistantReadiness.textContent = session?.readiness?.length
    ? `仍需处理：${session.readiness.join("；")}`
    : "参数已通过当前检查。请先查看右侧表单，确认最终输入后再提交仿真任务。";
  elements.assistantOpenStudyForm.hidden = !reviewable;
  elements.assistantLaunch.disabled = state.assistantBusy || !reviewable
    || !elements.assistantSummaryConfirmed.checked || !elements.assistantMaterialsConfirmed.checked;
  elements.assistantSend.disabled = state.assistantBusy;
  elements.assistantSend.textContent = state.assistantBusy ? "处理中..." : "发送";
  elements.assistantSend.setAttribute("aria-busy", state.assistantBusy ? "true" : "false");
  elements.assistantRetry.hidden = !session?.failure;
  elements.assistantRetry.disabled = state.assistantBusy;
}

async function streamAssistant(path, options, {onDelta, onStatus}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      message = typeof payload.detail === "string" ? payload.detail : payload.detail?.message || message;
    } catch { /* Keep the HTTP fallback. */ }
    throw new Error(message);
  }
  if (!response.body) throw new Error("浏览器不支持流式响应");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = null;
  const consume = (block) => {
    let event = "message";
    const data = [];
    block.split("\n").forEach(line => {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    });
    if (!data.length) return;
    const payload = JSON.parse(data.join("\n"));
    if (event === "delta") onDelta?.(payload.text || "");
    else if (event === "status") onStatus?.(payload.message || "正在处理…");
    else if (event === "error") throw new Error(payload.message || "Agent 请求失败");
    else if (event === "complete") completed = payload;
  };
  while (true) {
    const {value, done} = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    blocks.filter(Boolean).forEach(consume);
    if (done) break;
  }
  if (buffer.trim()) consume(buffer);
  if (!completed) throw new Error("Agent 流式响应未完成");
  return completed;
}

function setSidebarView(view, {persist = true} = {}) {
  state.sidebarView = view === "assistant" ? "assistant" : "project";
  const assistant = state.sidebarView === "assistant";
  elements.projectSidebarPane.hidden = assistant;
  elements.assistantRail.hidden = !assistant;
  elements.projectSidebarTab.classList.toggle("is-active", !assistant);
  elements.assistantSidebarTab.classList.toggle("is-active", assistant);
  elements.projectSidebarTab.setAttribute("aria-selected", String(!assistant));
  elements.assistantSidebarTab.setAttribute("aria-selected", String(assistant));
  if (persist) persistWorkspaceState();
  if (assistant) renderAssistant();
}

function assistantQuestionAnswers() {
  return [...elements.assistantQuestionForm.querySelectorAll("fieldset[data-question-id]")].map(fieldset => {
    const questionId = fieldset.dataset.questionId;
    const checked = [...fieldset.querySelectorAll("input[type=radio]:checked, input[type=checkbox]:checked")];
    const number = fieldset.querySelector("input[type=number]");
    const text = fieldset.querySelector("textarea");
    const answer = {
      question_id: questionId,
      option_ids: checked.map(input => input.value),
      ...(number ? { number_value: number.value === "" ? null : Number(number.value) } : {}),
      ...(text ? { text_value: text.value.trim() || null } : {}),
    };
    return answer;
  }).filter(answer => answer.option_ids.length || answer.number_value != null || answer.text_value);
}

async function submitAssistantTurn(event) {
  return submitAssistantInput(event);
}

async function submitAssistantAnswers(event) {
  return submitAssistantInput(event, {answersOnly: true});
}

async function submitAssistantInput(event, {answersOnly = false, retry = false} = {}) {
  event.preventDefault();
  if (state.assistantBusy) return;
  const message = answersOnly || retry ? "" : elements.assistantInput.value.trim();
  const answers = retry ? [] : assistantQuestionAnswers();
  if (!retry && !message && !answers.some(answer => answer.option_ids.length || answer.number_value != null || answer.text_value)) {
    showToast("请输入问题或回答当前选项", true);
    return;
  }
  const requestGeneration = ++state.assistantRequestGeneration;
  // Clear immediately so the submitted text cannot remain in the composer while the Agent works.
  if (!answersOnly) elements.assistantInput.value = "";
  state.assistantPendingMessage = message;
  state.assistantPendingAnswers = answers;
  state.assistantStreamAnswer = "";
  state.assistantStreamStatus = "正在连接 Agent…";
  state.assistantBusy = true;
  renderAssistant();
  try {
    let session;
    if (!state.assistantSession) {
      session = await streamAssistant("/v1/assistant-sessions/stream", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message, project_id: state.selectedProjectId || undefined,
          workpiece_id: state.selectedWorkpieceId || undefined,
          study_id: state.selectedStudyId || undefined,
        }),
      }, {
        onDelta(delta) {
          if (requestGeneration !== state.assistantRequestGeneration) return;
          state.assistantStreamAnswer += delta;
          renderAssistant();
        },
        onStatus(message) {
          if (requestGeneration !== state.assistantRequestGeneration) return;
          state.assistantStreamStatus = message;
          renderAssistant();
        },
      });
    } else {
      if (!state.assistantSession.workpiece_id && state.selectedWorkpieceId && message) {
        state.assistantSession = await request(`/v1/assistant-sessions/${state.assistantSession.session_id}/workpiece`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expected_revision: state.assistantSession.revision, workpiece_id: state.selectedWorkpieceId }),
        });
      }
      session = await streamAssistant(`/v1/assistant-sessions/${state.assistantSession.session_id}/turns/stream`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: state.assistantSession.revision,
          message: message || undefined,
          answers,
          retry: retry || undefined,
        }),
      }, {
        onDelta(delta) {
          if (requestGeneration !== state.assistantRequestGeneration) return;
          state.assistantStreamAnswer += delta;
          renderAssistant();
        },
        onStatus(message) {
          if (requestGeneration !== state.assistantRequestGeneration) return;
          state.assistantStreamStatus = message;
          renderAssistant();
        },
      });
    }
    if (requestGeneration !== state.assistantRequestGeneration) return;
    state.assistantSession = session;
    state.assistantSessionId = session.session_id;
    state.assistantSessions = [session, ...state.assistantSessions.filter(item => item.session_id !== session.session_id)];
    if (session.study_id) {
      state.selectedStudyId = session.study_id;
      invalidateAgentStudyForm(session.study_id);
    }
    state.assistantPendingMessage = "";
    state.assistantPendingAnswers = [];
    state.assistantStreamAnswer = "";
    state.assistantStreamStatus = "";
    persistWorkspaceState();
    renderAssistant();
    if (session.study_id) await loadWorkspace();
  } catch (error) {
    if (requestGeneration !== state.assistantRequestGeneration) return;
    showToast(`Agent 请求失败：${error.message}`, true);
    state.assistantPendingMessage = "";
    state.assistantStreamAnswer = "";
    state.assistantStreamStatus = "";
  } finally {
    if (requestGeneration === state.assistantRequestGeneration) {
      state.assistantBusy = false;
      renderAssistant();
    }
  }
}

async function launchAssistant() {
  const session = state.assistantSession;
  if (!session) return;
  state.assistantBusy = true;
  renderAssistant();
  try {
    state.assistantSession = await request(`/v1/assistant-sessions/${session.session_id}/launch`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: session.revision,
        expected_study_revision: session.study_revision,
        summary_confirmed: elements.assistantSummaryConfirmed.checked,
        materials_confirmed: elements.assistantMaterialsConfirmed.checked,
        form_reviewed: elements.assistantSummaryConfirmed.checked }),
    });
    persistWorkspaceState();
    await loadWorkspace();
  } catch (error) { showToast(`仿真启动失败：${error.message}`, true); }
  finally { state.assistantBusy = false; renderAssistant(); }
}

async function openAssistantStudyForm() {
  const session = state.assistantSession;
  if (!session?.study_id) return;
  await selectStudy(session.study_id, {activeTab: "scenario"});
  elements.structuredInputs.scrollIntoView({block: "start", behavior: "smooth"});
  showToast("请在右侧表单核对材料、边界、热源、网格和判据后，再回到 Agent 确认。");
}

function upsertAssistantSession(session) {
  state.assistantSession = session;
  state.assistantSessionId = session.session_id;
  state.assistantSessions = [session, ...state.assistantSessions.filter(item => item.session_id !== session.session_id)];
}

async function refreshAssistantTaskStatus() {
  const session = state.assistantSession;
  if (!session?.task_id) return;
  const before = session.status;
  try {
    const refreshed = await request(`/v1/assistant-sessions/${session.session_id}`);
    upsertAssistantSession(refreshed);
    if (before !== refreshed.status && ["completed", "needs_mesh_review", "failed"].includes(refreshed.status)) {
      showToast(refreshed.answer || "Agent 已更新仿真任务状态", refreshed.status !== "completed");
    }
    renderAssistant();
  } catch (error) {
    console.warn("Agent 任务状态读取失败", error);
  }
}

async function loadAssistantSession() {
  if (!state.assistantSessionId) return;
  if (!/^assistant-[0-9a-f]{32}$/.test(state.assistantSessionId)) {
    state.assistantSession = null;
    state.assistantSessionId = null;
    persistWorkspaceState();
    return;
  }
  try {
    state.assistantSession = await request(`/v1/assistant-sessions/${state.assistantSessionId}`);
    if (state.assistantSession.project_id) state.selectedProjectId = state.assistantSession.project_id;
    if (state.assistantSession.workpiece_id) state.selectedWorkpieceId = state.assistantSession.workpiece_id;
    if (state.assistantSession.study_id) state.selectedStudyId = state.assistantSession.study_id;
  } catch {
    const cached = state.assistantSessions.find(item => item.session_id === state.assistantSessionId);
    if (cached) {
      state.assistantSession = cached;
    } else {
      state.assistantSession = null;
      state.assistantSessionId = null;
      persistWorkspaceState();
    }
  }
}

function render() {
  renderHealth();
  renderWorkpieces();
  renderComponents();
  renderWorkspace();
  renderStudies();
  renderInspector();
  renderAssistant();
  renderPrimaryAction();
  drawWorkpiece();
}

function renderHealth() {
  const online = state.health?.status === "ok";
  elements.serviceDot.className = `status-dot ${online ? "is-online" : "is-offline"}`;
  elements.serviceLabel.textContent = online ? "服务在线" : "连接中断";
  if (!online) {
    elements.plannerLabel.textContent = "规划器未知";
    elements.cadflowLabel.textContent = "CadFlow 未知";
    return;
  }
  const planner = state.health.planner_mode === "codex" ? "Codex"
    : state.health.planner_mode === "openai" ? state.health.openai_model : "离线规划器";
  elements.plannerLabel.textContent = planner || "OpenAI";
  elements.cadflowLabel.textContent = state.health.cadflow_repo_found
    ? "CadFlow 已连接"
    : "CadFlow 未连接";
  elements.diagnosticService.textContent = online ? "正常" : "不可用";
  elements.diagnosticCompute.textContent = state.health.compute?.ready
    ? `${state.health.compute.selected_backend} · 就绪`
    : "不可用";
  elements.diagnosticAgent.textContent = state.health.planner_mode === "codex" ? "Codex · 服务端配置"
    : state.health.planner_mode === "openai"
    ? `OpenAI · ${state.health.openai_model || "已配置模型"}`
    : "本地规则模式";
}

function renderWorkpieces() {
  const query = elements.workpieceSearch.value.trim().toLocaleLowerCase("zh-CN");
  const filtered = state.projects.filter((project) => {
    const workpieceNames = state.workpieces
      .filter((item) => item.project_id === project.project_id)
      .map((item) => item.name)
      .join(" ");
    return `${project.name} ${workpieceNames}`.toLocaleLowerCase("zh-CN").includes(query);
  });
  elements.workpieceList.replaceChildren();
  if (!filtered.length) {
    elements.workpieceList.append(createElement("div", "empty-list", query ? "没有匹配项目" : "暂无项目"));
  } else {
    for (const project of filtered) {
      const projectWorkpieces = state.workpieces.filter(
        (item) => item.project_id === project.project_id,
      );
      const projectStudies = state.studies.filter(
        (item) => item.project_id === project.project_id,
      );
      const projectRow = createElement("div", "project-row-wrap");
      const button = createElement("button", "workpiece-row project-row");
      button.type = "button";
      button.dataset.projectId = project.project_id;
      button.classList.toggle("is-selected", project.project_id === state.selectedProjectId);
      button.setAttribute("aria-pressed", String(project.project_id === state.selectedProjectId));

      const glyph = createElement("span", "workpiece-glyph project-glyph", "PRJ");
      const copy = createElement("span", "workpiece-copy");
      copy.append(
        createElement("strong", "", project.name),
        createElement("span", "", `${projectWorkpieces.length} 个几何版本 · ${projectStudies.length} 项研究`),
      );
      const status = createElement("span", "row-state");
      status.classList.toggle("is-ready", projectWorkpieces.some(
        (item) => item.geometry.available || item.kind === "box",
      ));
      button.append(glyph, copy, status);
      button.addEventListener("click", () => selectProject(project.project_id));
      const deleteButton = createElement("button", "project-delete-button", "删除");
      deleteButton.type = "button";
      deleteButton.title = `删除项目：${project.name}`;
      deleteButton.setAttribute("aria-label", deleteButton.title);
      deleteButton.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteProject(project);
      });
      projectRow.append(button, deleteButton);
      elements.workpieceList.append(projectRow);

      if (project.project_id !== state.selectedProjectId) continue;
      for (const workpiece of projectWorkpieces) {
        const geometryButton = createElement("button", "workpiece-row geometry-version-row");
        geometryButton.type = "button";
        geometryButton.dataset.workpieceId = workpiece.workpiece_id;
        geometryButton.classList.toggle(
          "is-selected",
          workpiece.workpiece_id === state.selectedWorkpieceId,
        );
        geometryButton.setAttribute(
          "aria-pressed",
          String(workpiece.workpiece_id === state.selectedWorkpieceId),
        );
        const geometryGlyph = createElement(
          "span",
          "workpiece-glyph",
          workpiece.kind === "box" ? "BOX" : workpiece.cad_format?.toUpperCase() || "CAD",
        );
        const geometryCopy = createElement("span", "workpiece-copy");
        geometryCopy.append(
          createElement("strong", "", workpiece.name),
          createElement("span", "", workpieceSummary(workpiece)),
        );
        geometryButton.append(geometryGlyph, geometryCopy);
        geometryButton.addEventListener("click", () => selectWorkpiece(workpiece.workpiece_id));
        elements.workpieceList.append(geometryButton);
      }
    }
  }
  elements.workpieceCount.textContent = `${state.projects.length} 个项目`;
  elements.studyCount.textContent = `${selectedProjectStudies().length} 项研究`;
}

function workpieceSummary(workpiece) {
  const dimensions = workpiece.dimensions_mm || workpiece.source_dimensions;
  if (dimensions) {
    const { x, y, z } = dimensions;
    if (!workpiece.unit_confirmed) {
      return `${formatNumber(x)} × ${formatNumber(y)} × ${formatNumber(z)} · 待确认单位`;
    }
    return `${formatNumber(x)} × ${formatNumber(y)} × ${formatNumber(z)} mm`;
  }
  return `${workpiece.cad_format?.toUpperCase() || "CAD"} · ${formatBytes(workpiece.size_bytes)}`;
}

async function deleteProject(project) {
  if (state.busy) return;
  if (!window.confirm(
    `确定删除项目“${project.name}”吗？项目中的几何、研究、结果和任务记录都会永久删除。`,
  )) return;
  if (!await flushDraftBeforeNavigation()) return;
  setBusy(true);
  try {
    await request(`/v1/projects/${encodeURIComponent(project.project_id)}`, { method: "DELETE" });
    if (state.selectedProjectId === project.project_id) {
      state.selectedProjectId = null;
      state.selectedWorkpieceId = null;
      state.selectedStudyId = null;
      state.activeTab = "geometry";
      discardSourceDraft();
      resetComponentView();
    }
    await loadWorkspace({ preserveSelection: false });
    showToast(`项目“${project.name}”已删除`);
  } catch (error) {
    showToast(`项目删除失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

async function deleteComponent(component) {
  const workpiece = selectedWorkpiece();
  if (!workpiece || state.busy || workpiece.components.length <= 1) return;
  if (!window.confirm(
    `确定从 STL 几何中删除“${component.name}”吗？删除后该组件及其材料分配不可恢复。已有研究的工件不能删除组件。`,
  )) return;
  if (!await flushDraftBeforeNavigation()) return;
  setBusy(true);
  try {
    await request(
      `/v1/workpieces/${encodeURIComponent(workpiece.workpiece_id)}/components/${encodeURIComponent(component.component_id)}`,
      { method: "DELETE" },
    );
    resetComponentView();
    discardSourceDraft();
    await loadWorkspace();
    showToast(`组件“${component.name}”已从 STL 中删除`);
  } catch (error) {
    showToast(`组件删除失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

function renderComponents() {
  const workpiece = selectedWorkpiece();
  const components = workpiece?.components || [];
  const componentIds = new Set(components.map((component) => component.component_id));
  if (!componentIds.has(state.selectedComponentId)) state.selectedComponentId = null;
  if (!componentIds.has(state.isolatedComponentId)) state.isolatedComponentId = null;
  state.hiddenComponentIds = new Set(
    [...state.hiddenComponentIds].filter((componentId) => componentIds.has(componentId)),
  );
  elements.componentTree.hidden = !workpiece;
  elements.componentCount.textContent = String(components.length);
  elements.componentList.replaceChildren();
  if (!components.length) {
    elements.componentList.append(createElement("div", "empty-list", "未识别到独立组件"));
    return;
  }
  components.forEach((component, index) => {
    const row = createElement("div", "component-row");
    row.classList.toggle("is-selected", component.component_id === state.selectedComponentId);
    row.classList.toggle("is-hidden", state.hiddenComponentIds.has(component.component_id));
    row.classList.toggle("is-isolated", component.component_id === state.isolatedComponentId);
    const select = createElement("button", "component-select");
    select.type = "button";
    select.setAttribute(
      "aria-pressed",
      String(component.component_id === state.selectedComponentId),
    );
    const swatch = createElement("i", `component-swatch component-color-${index % 6}`);
    const copy = createElement("span");
    copy.append(
      createElement("strong", "", component.name),
      createElement("small", "", `${formatInteger(component.triangle_count)} 个三角面${component.watertight ? " · 封闭" : " · 开放"}`),
    );
    select.append(swatch, copy);
    select.addEventListener("click", () => selectComponent(component.component_id, true));

    const actions = createElement("span", "component-actions");
    const rename = createElement("button", "component-tool", "✎");
    rename.type = "button";
    rename.title = `重命名${component.name}`;
    rename.setAttribute("aria-label", rename.title);
    rename.addEventListener("click", () => openComponentDialog(component.component_id));
    const visibility = createElement(
      "button",
      "component-tool",
      state.hiddenComponentIds.has(component.component_id) ? "○" : "●",
    );
    visibility.type = "button";
    visibility.title = state.hiddenComponentIds.has(component.component_id)
      ? `显示${component.name}`
      : `隐藏${component.name}`;
    visibility.setAttribute("aria-label", visibility.title);
    visibility.addEventListener("click", () => toggleComponentVisibility(component.component_id));
    const isolate = createElement(
      "button",
      "component-tool",
      component.component_id === state.isolatedComponentId ? "■" : "□",
    );
    isolate.type = "button";
    isolate.title = component.component_id === state.isolatedComponentId
      ? "退出隔离"
      : `隔离${component.name}`;
    isolate.setAttribute("aria-label", isolate.title);
    isolate.addEventListener("click", () => toggleComponentIsolation(component.component_id));
    const remove = createElement("button", "component-tool component-tool-danger", "删除");
    remove.type = "button";
    remove.title = components.length <= 1
      ? "至少保留一个组件"
      : `从 STL 中删除${component.name}`;
    remove.setAttribute("aria-label", remove.title);
    remove.disabled = components.length <= 1 || Boolean(state.busy);
    remove.addEventListener("click", () => deleteComponent(component));
    actions.append(rename, visibility, isolate, remove);
    row.append(select, actions);
    elements.componentList.append(row);
  });
}

function selectComponent(componentId, openMaterials = false) {
  state.selectedComponentId = componentId;
  renderComponents();
  drawWorkpiece();
  if (openMaterials && selectedStudy()?.plan) {
    switchTab("materials");
    const materialRow = elements.componentMaterialList.querySelector(
      `[data-component-id="${CSS.escape(componentId)}"]`,
    );
    materialRow?.scrollIntoView({ block: "nearest" });
    materialRow?.querySelector("select, input")?.focus({ preventScroll: true });
  }
}

function toggleComponentVisibility(componentId) {
  if (state.hiddenComponentIds.has(componentId)) {
    state.hiddenComponentIds.delete(componentId);
  } else {
    state.hiddenComponentIds.add(componentId);
    if (state.isolatedComponentId === componentId) state.isolatedComponentId = null;
  }
  renderComponents();
  drawWorkpiece();
}

function toggleComponentIsolation(componentId) {
  state.isolatedComponentId = state.isolatedComponentId === componentId ? null : componentId;
  if (state.isolatedComponentId) state.hiddenComponentIds.delete(componentId);
  state.selectedComponentId = componentId;
  renderComponents();
  drawWorkpiece();
}

function resetComponentView() {
  state.selectedComponentId = null;
  state.selectedRegionId = null;
  state.selectedSurfaceFaces.clear();
  state.selectionMode = "component";
  state.hiddenComponentIds = new Set();
  state.isolatedComponentId = null;
  state.componentHitTriangles = [];
  state.boundaryMarkers = [];
}

function openComponentDialog(componentId) {
  const component = selectedWorkpiece()?.components?.find(
    (item) => item.component_id === componentId,
  );
  if (!component) return;
  state.editingComponentId = componentId;
  elements.componentNameInput.value = component.name;
  elements.componentDialog.showModal();
  elements.componentNameInput.focus();
  elements.componentNameInput.select();
}

function closeComponentDialog() {
  state.editingComponentId = null;
  elements.componentDialog.close();
}

async function renameSelectedComponent(event) {
  event.preventDefault();
  const workpiece = selectedWorkpiece();
  const componentId = state.editingComponentId;
  if (!workpiece || !componentId || !elements.componentRenameForm.reportValidity()) return;
  setBusy(true);
  try {
    await request(
      `/v1/workpieces/${workpiece.workpiece_id}/components/${encodeURIComponent(componentId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: elements.componentNameInput.value }),
      },
    );
    closeComponentDialog();
    await loadWorkspace();
    showToast("组件名称已更新，稳定组件 ID 与历史研究保持不变");
  } catch (error) {
    showToast(`组件重命名失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

async function selectWorkpiece(workpieceId) {
  if (!await flushDraftBeforeNavigation()) return;
  state.selectedWorkpieceId = workpieceId;
  state.selectedProjectId = selectedWorkpiece()?.project_id || null;
  state.activeTab = selectedWorkpiece()?.unit_confirmed ? "scenario" : "geometry";
  state.draftSyncedFor = null;
  discardSourceDraft();
  resetComponentView();
  resetStlView(false);
  selectLatestStudy(null, workpieceId);
  persistWorkspaceState();
  const selectedData = Promise.all([loadSelectedMesh(), loadSelectedResult(), loadSelectedAgentRun(), loadSelectedValidation()]);
  render();
  await selectedData;
  render();
  startHeatAnimation();
}

async function selectProject(projectId) {
  if (!await flushDraftBeforeNavigation()) return;
  state.selectedProjectId = projectId;
  const project = selectedProject();
  const workpieces = selectedProjectWorkpieces();
  state.selectedWorkpieceId = workpieces.find(
    (item) => item.workpiece_id === project?.active_workpiece_id,
  )?.workpiece_id || workpieces[0]?.workpiece_id || null;
  state.activeTab = selectedWorkpiece()?.unit_confirmed ? "scenario" : "geometry";
  state.draftSyncedFor = null;
  discardSourceDraft();
  resetComponentView();
  resetStlView(false);
  selectLatestStudy();
  persistWorkspaceState();
  const selectedData = Promise.all([loadSelectedMesh(), loadSelectedResult(), loadSelectedAgentRun(), loadSelectedValidation()]);
  render();
  await selectedData;
  render();
  startHeatAnimation();
}

function renderWorkspace() {
  const project = selectedProject();
  const workpiece = selectedWorkpiece();
  const study = selectedStudy();
  const steps = document.querySelectorAll(".workflow li");
  steps.forEach((item) => item.classList.remove("is-complete", "is-active"));

  if (!workpiece) {
    elements.workspaceKind.textContent = "工作区";
    elements.workspaceTitle.textContent = "尚未选择工件";
    applyStatusBadge(null);
    elements.emptyStage.hidden = false;
    elements.thermalLegend.hidden = true;
    elements.visualizationModes.hidden = true;
    elements.sliceControls.hidden = true;
    elements.probeReadout.hidden = true;
    elements.resetViewButton.hidden = true;
    elements.sourceEditButton.hidden = true;
    closeSourceEditor();
    elements.workpieceCanvas.classList.remove("is-interactive");
    elements.geometryStep.textContent = "未就绪";
    elements.planStep.textContent = "未生成";
    elements.meshStep.textContent = "未生成";
    elements.solveStep.textContent = "未运行";
    elements.geometryEngine.textContent = "—";
    setPropertyValues(["—", "—", "—", "—"]);
    return;
  }

  elements.workspaceKind.textContent = project?.name || "工程项目";
  elements.workspaceTitle.textContent = workpiece.name;
  applyStatusBadge(study?.status || "geometry");
  elements.emptyStage.hidden = true;
  const hasPreview = Boolean(workpiece.geometry?.summary?.preview?.triangles?.length || workpiece.kind === "box");
  const hasSource = Boolean(study?.plan && ThermoFlowSources.sourcesFromPlan(study.plan).length && heatSourceEnabled(study.plan));
  const hasMesh = Boolean(state.mesh?.cell_samples_mm?.length);
  const hasThermalResult = Boolean(state.result && hasPreview);
  const hasTransientResult = Boolean(hasThermalResult && state.result.time_steps?.length);
  const hasHeatFluxResult = Boolean(state.result?.heat_flux_field_preview?.samples?.length);
  const comparing = Boolean(state.comparison);
  if (state.visualizationMode === "mesh" && !hasMesh) {
    state.visualizationMode = hasThermalResult ? "thermal" : "model";
  }
  if (state.visualizationMode === "flux" && !hasHeatFluxResult) {
    state.visualizationMode = "thermal";
  }
  if (state.visualizationMode === "diffusion" && !hasTransientResult) {
    state.visualizationMode = hasThermalResult ? "thermal" : "model";
  }
  elements.resetViewButton.hidden = !hasPreview;
  elements.sourceEditButton.hidden = !study?.plan || !hasPreview || comparing;
  elements.sourceEditButton.title = hasSource ? "编辑热源" : "添加热源";
  elements.sourceEditButton.setAttribute("aria-label", elements.sourceEditButton.title);
  elements.visualizationModes.hidden = comparing || (!hasMesh && !hasThermalResult);
  elements.meshViewButton.hidden = !hasMesh;
  elements.diffusionViewButton.hidden = !hasTransientResult;
  elements.thermalViewButton.hidden = !hasThermalResult;
  elements.heatFluxViewButton.hidden = !hasHeatFluxResult;
  elements.contourViewButton.hidden = !hasThermalResult;
  elements.sliceViewButton.hidden = !hasThermalResult;
  elements.sliceControls.hidden = comparing || !hasThermalResult || state.visualizationMode !== "slice";
  elements.probeReadout.hidden = comparing || !hasThermalResult || !state.probe || !isTemperatureVisualization();
  syncVisualizationModeButtons();
  if (!hasPreview || comparing) closeSourceEditor();
  elements.workpieceCanvas.classList.toggle("is-interactive", hasPreview);
  const watertight = workpiece.geometry?.summary?.quality?.watertight;
  elements.geometryStep.textContent = workpiece.geometry.available
    ? !workpiece.unit_confirmed
      ? "等待确认尺度"
      : watertight
      ? "尺度已确认 · 封闭体"
      : "尺度已确认 · 近似重建"
    : "几何检查未通过";
  elements.planStep.textContent = study?.confirmation?.status === "confirmed"
    ? "用户已确认"
    : study?.plan
    ? "草案待确认"
    : "未生成";
  const meshLabels = {
    not_generated: "未生成",
    generating: "正在生成",
    needs_review: "等待确认质量提示",
    ready: "真实网格已就绪",
    blocked: "质量检查未通过",
    failed: "生成失败",
  };
  elements.meshStep.textContent = study?.plan
    ? meshLabels[study.mesh_status] || "未生成"
    : "未生成";
  elements.solveStep.textContent = study?.status === "succeeded" ? "结果已就绪" : statusSolveLabel(study);
  elements.geometryEngine.textContent = engineLabel(workpiece.geometry.engine);
  steps[0].classList.add(workpiece.geometry.available && workpiece.unit_confirmed ? "is-complete" : "is-active");
  if (study?.plan) steps[1].classList.add(study.confirmation?.status === "confirmed" ? "is-complete" : "is-active");
  if (study?.mesh_status === "ready") steps[2].classList.add("is-complete");
  else if (study?.confirmation?.status === "confirmed") steps[2].classList.add("is-active");
  if (["planned", "ready", "running"].includes(study?.status) && study?.mesh_status === "ready") {
    steps[3].classList.add("is-active");
  }
  if (study?.status === "succeeded") steps[3].classList.add("is-complete");

  const summary = workpiece.geometry.summary || {};
  const dimensions = resolvedDimensions(workpiece);
  setPropertyValues([
    workpiece.kind === "box" ? "长方体" : workpiece.cad_format?.toUpperCase() || "CAD",
    workpiece.unit_confirmed && workpiece.dimensions_mm
      ? `${formatNumber(dimensions.x, 1)} × ${formatNumber(dimensions.y, 1)} × ${formatNumber(dimensions.z, 1)} mm`
      : `${formatNumber(workpiece.source_dimensions?.x)} × ${formatNumber(workpiece.source_dimensions?.y)} × ${formatNumber(workpiece.source_dimensions?.z)}（文件坐标）`,
    workpiece.unit_confirmed ? formatEngineering(summary.volume, "mm³") : "确认单位后计算",
    workpiece.unit_confirmed ? formatEngineering(summary.area, "mm²") : "确认单位后计算",
  ]);
}

function statusSolveLabel(study) {
  if (!study) return "未运行";
  if (study.status === "running") return "正在求解";
  if (study.status === "failed") return "运行失败";
  if (study.status === "rejected") return "方案未通过";
  if (study.status === "needs_input") return "等待确认输入";
  if (study.status === "ready") return "已准备求解";
  return "等待运行";
}

function engineLabel(engine) {
  if (engine === "cadflow") return "CadFlow";
  if (engine === "trimesh-stl") return "STL 网格检查";
  if (engine === "analytic-box-metadata") return "解析几何";
  return engine || "—";
}

function setPropertyValues(values) {
  const cells = elements.propertyGrid.querySelectorAll("dd");
  values.forEach((value, index) => {
    cells[index].textContent = value;
  });
}

function applyStatusBadge(status) {
  elements.workspaceState.className = "state-badge";
  const states = {
    geometry: ["几何就绪", "state-success"],
    planned: ["方案就绪", "state-planned"],
    needs_input: ["待确认输入", "state-planned"],
    ready: ["已准备求解", "state-success"],
    running: ["求解中", "state-running"],
    succeeded: ["已完成", "state-success"],
    failed: ["运行失败", "state-failed"],
    rejected: ["方案驳回", "state-failed"],
  };
  const [label, className] = states[status] || ["待选择", "state-neutral"];
  elements.workspaceState.textContent = label;
  elements.workspaceState.classList.add(className);
}

function renderStudies() {
  const studies = selectedProjectStudies();
  elements.studyList.replaceChildren();
  elements.selectedStudyId.textContent = studies.length ? `${studies.length} 个版本` : "";
  if (!studies.length) {
    elements.studyList.append(createElement("div", "empty-list", "暂无研究"));
    return;
  }
  for (const study of studies) {
    const button = createElement("button", "study-row");
    button.type = "button";
    button.classList.toggle("is-selected", study.study_id === state.selectedStudyId);
    button.setAttribute("aria-pressed", String(study.study_id === state.selectedStudyId));
    const marker = createElement("i");
    marker.dataset.status = study.status;
    const copy = createElement("div");
    const geometry = state.workpieces.find((item) => item.workpiece_id === study.workpiece_id);
    copy.append(
      createElement("strong", "", study.plan?.study_name || "仿真草案"),
      createElement("small", "", `${geometry?.name || "几何版本"} · ${formatDate(study.updated_at)}`),
    );
    button.append(marker, copy, createElement("span", "", STATUS_LABELS[study.status] || study.status));
    button.addEventListener("click", () => selectStudy(study.study_id));
    elements.studyList.append(button);
  }
}

async function selectStudy(studyId, options = {}) {
  if (!await flushDraftBeforeNavigation()) return;
  state.selectedStudyId = studyId;
  const study = selectedStudy();
  if (study) {
    state.selectedProjectId = study.project_id;
    state.selectedWorkpieceId = study.workpiece_id;
  }
  if (options.activeTab) state.activeTab = options.activeTab;
  if (options.visualizationMode) state.visualizationMode = options.visualizationMode;
  persistWorkspaceState();
  discardSourceDraft();
  const selectedData = Promise.all([loadSelectedMesh(), loadSelectedResult(), loadSelectedAgentRun(), loadSelectedValidation()]);
  render();
  await selectedData;
  render();
  startHeatAnimation();
  if (options.announce) {
    showToast(state.result ? "已在画布显示采用研究的温度场" : "已选中采用研究，暂无可视化结果", !state.result);
  }
}

function renderInspector() {
  renderComputationStatus();
  const study = selectedStudy();
  const plan = study?.plan;
  renderGeometryPanel();
  renderMaterialsPanel();
  renderStructuredDraft();
  renderMeshPanel();
  renderEvaluation();
  elements.planEmpty.hidden = Boolean(plan);
  elements.planContent.hidden = !plan;
  elements.confirmationCheck.hidden = !plan;

  if (plan) {
    elements.planName.textContent = plan.study_name;
    elements.planConfidence.textContent = study.planner?.provider === "manual-template" ? "手动参数" : study.overrides
      ? `用户参数 · ${Math.round(plan.confidence * 100)}%`
      : `置信度 ${Math.round(plan.confidence * 100)}%`;
    elements.planMaterial.textContent = plan.material.name;
    elements.planConductivity.textContent = `${formatNumber(
      plan.material.thermal_conductivity_w_m_k,
    )} W/(m·K)`;
    const boundaryDescriptions = plan.boundaries
      .map((item) => `${boundaryName(item)} ${formatNumber(item.temperature_k)} K`);
    if (plan.surface_conditions?.length) boundaryDescriptions.push(`${plan.surface_conditions.length} 个区域热边界`);
    if (plan.contacts?.length) boundaryDescriptions.push(`${plan.contacts.length} 个组件热接触`);
    elements.planBoundaries.textContent = boundaryDescriptions.join(" / ") || "无";
    const planSources = ThermoFlowSources.sourcesFromPlan(plan);
    if (planSources.length && plan.heat_source_enabled) {
      const shapeNames = { point: "点", line: "线", surface: "面", volume: "体" };
      const shapeSummary = [...new Set(planSources.map(source => shapeNames[source.shape] || "点"))].join("/");
      const totalPower = planSources.reduce((sum, source) => sum + Number(source.total_power_w || 0), 0);
      elements.planSource.textContent = `${planSources.length} 个热源 · ${shapeSummary} · 总功率 ${formatNumber(totalPower)} W`;
    } else {
      elements.planSource.textContent = "无内部热源";
    }
    elements.planConvection.textContent = plan.convection && plan.global_convection_enabled
      ? `${formatNumber(plan.convection.ambient_temperature_k)} K · ${formatNumber(plan.convection.heat_transfer_coefficient_w_m2_k)} W/(m²·K)`
      : "未设置";
    elements.planMesh.textContent = `${formatNumber(plan.mesh.target_element_size_mm)} mm`;
    elements.planTolerance.textContent = formatScientific(plan.solver.relative_tolerance);
    elements.planSummary.textContent = plan.decision_summary;
    elements.planAssumptions.replaceChildren(
      ...plan.assumptions.map((assumption) => createElement("li", "", assumption)),
    );
    elements.confirmInputs.checked = study.confirmation?.status === "confirmed";
    elements.confirmInputs.disabled = study.confirmation?.status === "confirmed";
    elements.confirmationCheck.classList.toggle(
      "is-confirmed",
      study.confirmation?.status === "confirmed",
    );
  }

  const resultAvailable = Boolean(state.result);
  elements.resultEmpty.hidden = resultAvailable;
  elements.resultContent.hidden = !resultAvailable;
  if (state.result) renderResult(state.result);
  renderComparisonTool();
  renderAgent();
  renderModeling();
  switchTab(state.activeTab);
}

function renderGeometryPanel() {
  const workpiece = selectedWorkpiece();
  if (!workpiece) {
    elements.geometryQuality.textContent = "待导入";
    elements.unitNotice.hidden = true;
    elements.unitForm.hidden = true;
    elements.geometryChecks.replaceChildren();
    elements.geometryRegions.hidden = true;
    elements.geometryRegionList.replaceChildren();
    return;
  }
  const quality = workpiece.geometry?.summary?.quality || {};
  const topology = workpiece.geometry?.summary?.topology || {};
  elements.geometryQuality.textContent = workpiece.geometry.available ? "检查完成" : "检查失败";
  elements.unitNotice.hidden = workpiece.unit_confirmed;
  elements.unitForm.hidden = workpiece.unit_confirmed;
  updateUnitDimensions();
  const checks = [
    ["文件格式", `STL · ${(workpiece.geometry.summary?.stl_encoding || "未知").toUpperCase()}`],
    ["三角面", formatInteger(topology.triangles || 0)],
    ["断开壳体", `${topology.bodies || workpiece.components?.length || 0} 个`],
    ["封闭性", quality.watertight ? "封闭" : "存在开放边界"],
    ["法线一致性", quality.winding_consistent ? "一致" : "需要关注"],
    ["非流形边", `${quality.non_manifold_edges || 0} 条`],
    ["退化三角形", `${quality.degenerate_triangles || 0} 个`],
    ["已忽略退化片段", `${topology.discarded_degenerate_components || 0} 个`],
  ];
  elements.geometryChecks.replaceChildren(
    ...checks.map(([label, value]) => {
      const row = createElement("div");
      row.append(createElement("dt", "", label), createElement("dd", "", value));
      return row;
    }),
  );
  renderGeometryRegions(workpiece);
  const diagnostics = workpiece.geometry.diagnostics || [];
  elements.geometryLimitations.hidden = !diagnostics.length;
  elements.geometryLimitations.textContent = diagnostics.join(" ");
}

function renderGeometryRegions(workpiece) {
  const regions = (workpiece.regions || []).filter(
    (region) => ["bounding_plane", "surface_patch", "component_surface"].includes(region.kind),
  );
  const plan = selectedStudy()?.plan;
  const temperatures = new Map(
    (plan?.boundaries || []).map((boundary) => [boundary.region_id || boundary.selector, boundary.temperature_k]),
  );
  const contactRegions = new Set((plan?.contacts || []).flatMap(contact =>
    [contact.source_region_id, contact.target_region_id]));
  if (!regions.some((region) => region.region_id === state.selectedRegionId)) {
    state.selectedRegionId = null;
  }
  elements.geometryRegions.hidden = !regions.length;
  elements.geometryRegionCount.textContent = `${regions.length} 个`;
  elements.geometryRegionList.replaceChildren(
    ...regions.map((region) => {
      const button = createElement("button", "geometry-region-row");
      button.type = "button";
      button.classList.toggle("is-selected", region.region_id === state.selectedRegionId);
      button.setAttribute(
        "aria-pressed",
        String(region.region_id === state.selectedRegionId),
      );
      const copy = createElement("span");
      copy.append(
        createElement("strong", "", region.name),
        createElement("small", "", regionConditionLabel(region, temperatures, plan)),
      );
      const marker = createElement(
        "i",
        temperatures.has(region.region_id) || temperatures.has(region.selector)
          ? "region-state is-fixed" : contactRegions.has(region.region_id) ? "region-state is-contact" : "region-state",
      );
      button.append(marker, copy);
      button.addEventListener("click", () => selectGeometryRegion(region.region_id));
      return button;
    }),
  );
}

function regionConditionLabel(region, temperatures, plan) {
  const temperature = temperatures.get(region.region_id) ?? temperatures.get(region.selector);
  if (temperature != null) {
    return `固定温度 ${formatNumber(temperature)} K`;
  }
  const terms = plan?.surface_conditions?.filter(condition => condition.region_id === region.region_id) || [];
  if (terms.length) return terms.map(condition => ({ heat_flux: "表面热流", convection: "区域对流", radiation: "环境辐射" })[condition.kind]).join(" · ");
  if (plan?.contacts?.some(contact =>
    contact.source_region_id === region.region_id || contact.target_region_id === region.region_id)) return "组件热接触";
  if (plan?.convection && plan.global_convection_enabled) {
    return `环境对流 ${formatNumber(plan.convection.heat_transfer_coefficient_w_m2_k)} W/(m²·K)`;
  }
  return region.supported_condition_kinds?.includes("fixed_temperature")
    ? "可绑定固定温度"
    : "仅用于空间定位";
}

function selectGeometryRegion(regionId) {
  state.selectedRegionId = state.selectedRegionId === regionId ? null : regionId;
  const workpiece = selectedWorkpiece();
  if (workpiece) renderGeometryRegions(workpiece);
  setVisualizationMode("model");
}

function boundaryName(boundary) {
  return boundary.region_id
    ? selectedWorkpiece()?.regions?.find(region => region.region_id === boundary.region_id)?.name || "区域不可用"
    : faceLabel(boundary.selector);
}

function syncSurfaceSelection() {
  const available = selectedWorkpiece()?.cad_format === "stl" && selectedWorkpiece()?.unit_confirmed && !state.comparison;
  elements.geometrySelectionMode.disabled = !available;
  elements.geometrySelectionMode.value = state.selectionMode;
  elements.surfaceSelectionTools.hidden = !available || state.selectionMode !== "faces";
  elements.surfaceSelectionCount.textContent = `${state.selectedSurfaceFaces.size} 个面`;
  elements.saveSurfaceSelection.disabled = state.savingRegion || !state.selectedSurfaceFaces.size;
  elements.clearSurfaceSelection.disabled = state.savingRegion || !state.selectedSurfaceFaces.size;
}

function surfaceFaceSelected(index) {
  if (state.savingRegion || state.selectionMode !== "faces") return;
  if (state.selectedSurfaceFaces.has(index)) state.selectedSurfaceFaces.delete(index);
  else if (state.selectedSurfaceFaces.size < 200000) state.selectedSurfaceFaces.add(index);
  else { showToast("选面数量已达当前上限", true); return; }
  state.selectedRegionId = null;
  syncSurfaceSelection();
  drawWorkpiece();
}

elements.geometrySelectionMode.addEventListener("change", () => {
  state.selectionMode = elements.geometrySelectionMode.value;
  syncSurfaceSelection();
  if (state.selectionMode === "faces") setVisualizationMode("model");
  else drawWorkpiece();
});
elements.clearSurfaceSelection.addEventListener("click", () => {
  state.selectedSurfaceFaces.clear();
  syncSurfaceSelection();
  drawWorkpiece();
});
elements.saveSurfaceSelection.addEventListener("click", () => {
  if (!state.selectedSurfaceFaces.size) return;
  elements.surfaceRegionName.value = "";
  elements.surfaceRegionDialog.showModal();
  elements.surfaceRegionName.focus();
});
elements.cancelSurfaceRegion.addEventListener("click", () => elements.surfaceRegionDialog.close());
elements.surfaceRegionForm.addEventListener("submit", async event => {
  event.preventDefault();
  const workpiece = selectedWorkpiece();
  if (!workpiece || !state.selectedSurfaceFaces.size || state.savingRegion || !elements.surfaceRegionForm.reportValidity()) return;
  state.savingRegion = true;
  elements.submitSurfaceRegion.disabled = true;
  syncSurfaceSelection();
  try {
    const region = await request(`/v1/workpieces/${workpiece.workpiece_id}/regions`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: elements.surfaceRegionName.value.trim(), triangle_ids: [...state.selectedSurfaceFaces] }),
    });
    const current = state.workpieces.find(item => item.workpiece_id === workpiece.workpiece_id);
    if (current && !current.regions.some(item => item.region_id === region.region_id)) current.regions.push(region);
    if (selectedWorkpiece()?.workpiece_id === workpiece.workpiece_id) {
      state.selectedSurfaceFaces.clear();
      state.selectedRegionId = region.region_id;
      renderGeometryRegions(current);
      // Add choices in place so saving geometry cannot discard unconfirmed form edits.
      elements.fixedBoundaryRows.querySelectorAll("select").forEach(select => populateBoundaryRegions(select));
      elements.surfaceConditionRows.querySelectorAll(".region-target").forEach(select => populateBoundaryRegions(select));
      elements.thermalContactRows.querySelectorAll(".contact-region").forEach(select => {
        const componentId = select.closest(".contact-side").querySelector(".contact-component").value;
        populateContactRegions(select, componentId, select.value);
      });
      drawWorkpiece();
    }
    elements.surfaceRegionDialog.close();
    showToast(`已保存区域：${region.name}`);
  } catch (error) { showToast(`区域保存失败：${error.message}`, true); }
  finally {
    state.savingRegion = false;
    elements.submitSurfaceRegion.disabled = false;
    syncSurfaceSelection();
  }
});

function populateBoundaryRegions(select) {
  const value = select.value;
  const regions = selectedWorkpiece()?.regions?.filter(region => region.supported_condition_kinds.includes("fixed_temperature")) || [];
  select.replaceChildren(...regions.map(region => {
    const option = createElement("option", "", region.name);
    option.value = region.kind === "bounding_plane" && !select.classList.contains("region-target") ? region.selector : region.region_id;
    return option;
  }));
  if (value) {
    if (![...select.options].some(option => option.value === value)) {
      const missing = createElement("option", "", "区域不可用");
      missing.value = value;
      select.append(missing);
    }
    select.value = value;
  }
}

function fixedBoundaryValues() {
  return [...elements.fixedBoundaryRows.children].map(row => {
    const target = row.querySelector("select").value;
    return { ...(target.startsWith("region-") ? { region_id: target } : { selector: target }),
      temperature_k: Number(row.querySelector("input").value) };
  });
}

function syncBoundaryControls() {
  const confirmed = selectedStudy()?.confirmation?.status === "confirmed";
  elements.addFixedBoundary.disabled = confirmed || elements.fixedBoundaryRows.children.length >= 24;
  elements.fixedBoundaryRows.querySelectorAll("button").forEach(button => {
    button.disabled = confirmed;
  });
  elements.addSurfaceCondition.disabled = confirmed || elements.surfaceConditionRows.children.length >= 48;
  elements.surfaceConditionRows.querySelectorAll("button").forEach(button => { button.disabled = confirmed; });
  const components = selectedWorkpiece()?.components || [];
  elements.addThermalContact.disabled = confirmed || components.length < 2
    || elements.thermalContactRows.children.length >= 100;
  elements.thermalContactRows.querySelectorAll("button").forEach(button => { button.disabled = confirmed; });
}

function appendFixedBoundary(boundary = null) {
  const row = createElement("div", "fixed-boundary-row");
  const regionLabel = createElement("label", "", "区域");
  const select = createElement("select");
  select.required = true;
  populateBoundaryRegions(select);
  if (boundary) select.value = boundary.region_id || boundary.selector;
  const temperatureLabel = createElement("label", "", "温度 · K");
  const input = createElement("input");
  Object.assign(input, { type: "number", min: "1", max: "5000", step: "any", required: true });
  input.value = boundary?.temperature_k ?? "";
  const remove = createElement("button", "icon-button", "×");
  remove.type = "button";
  remove.title = "移除定温边界";
  remove.setAttribute("aria-label", remove.title);
  remove.addEventListener("click", () => { row.remove(); syncBoundaryControls(); drawWorkpiece(); });
  select.addEventListener("change", () => {
    state.selectedRegionId = selectedWorkpiece()?.regions?.find(region => (region.kind === "bounding_plane" ? region.selector : region.region_id) === select.value)?.region_id || null;
    setVisualizationMode("model");
  });
  input.addEventListener("input", () => drawWorkpiece());
  regionLabel.append(select);
  temperatureLabel.append(input);
  row.append(regionLabel, temperatureLabel, remove);
  elements.fixedBoundaryRows.append(row);
}
elements.addFixedBoundary.addEventListener("click", () => {
  if (selectedStudy()?.confirmation?.status === "confirmed" || elements.fixedBoundaryRows.children.length >= 24) return;
  appendFixedBoundary();
  syncBoundaryControls();
});

const SURFACE_FIELDS = {
  heat_flux: [["heat_flux_w_m2", "流入热流 · W/m²", -1e9, 1e9]],
  convection: [["ambient_temperature_k", "流体温度 · K", 1, 5000],
    ["heat_transfer_coefficient_w_m2_k", "对流系数 · W/(m²·K)", 0.000001, 1000000]],
  radiation: [["radiation_temperature_k", "环境辐射温度 · K", 1, 5000],
    ["emissivity", "发射率", 0.000001, 1], ["emissivity_source", "发射率来源"]],
};
function surfaceConditionValues() {
  return [...elements.surfaceConditionRows.children].map(row => ({
    kind: row.querySelector(".condition-kind").value, region_id: row.querySelector(".region-target").value,
    ...Object.fromEntries([...row.querySelectorAll("input")].map(input => [input.dataset.property,
      input.type === "number" ? Number(input.value) : input.value.trim()])),
  }));
}
function appendSurfaceCondition(condition = { kind: "heat_flux" }) {
  const row = createElement("div", "surface-boundary-row");
  const header = createElement("header");
  const kind = createElement("select", "condition-kind");
  kind.setAttribute("aria-label", "区域热边界类型");
  for (const [value, label] of [["heat_flux", "表面热流"], ["convection", "区域对流"], ["radiation", "环境辐射"]]) {
    const option = createElement("option", "", label); option.value = value; kind.append(option);
  }
  kind.value = condition.kind;
  const remove = createElement("button", "icon-button", "×");
  remove.type = "button"; remove.title = "移除区域热边界"; remove.setAttribute("aria-label", remove.title);
  remove.addEventListener("click", () => { row.remove(); syncBoundaryControls(); drawWorkpiece(); });
  header.append(kind, remove);
  const regionLabel = createElement("label", "", "区域");
  const target = createElement("select", "region-target");
  populateBoundaryRegions(target); target.required = true;
  if (condition.region_id) target.value = condition.region_id;
  regionLabel.append(target);
  const fields = createElement("div", "form-grid");
  const renderFields = () => {
    fields.replaceChildren(...SURFACE_FIELDS[kind.value].map(([property, title, minimum, maximum]) => {
      const label = createElement("label", property === "emissivity_source" ? "wide-field" : "", title);
      const input = createElement("input");
      input.type = minimum == null ? "text" : "number"; input.required = true;
      if (minimum != null) { input.min = minimum; input.max = maximum; input.step = "any"; }
      else input.maxLength = 300;
      input.dataset.property = property; input.value = condition[property] ?? "";
      label.append(input); return label;
    }));
  };
  kind.addEventListener("change", () => { renderFields(); drawWorkpiece(); });
  target.addEventListener("change", () => { state.selectedRegionId = target.value; setVisualizationMode("model"); });
  row.addEventListener("input", () => drawWorkpiece());
  renderFields();
  row.append(header, regionLabel, fields); elements.surfaceConditionRows.append(row);
}
elements.addSurfaceCondition.addEventListener("click", () => {
  if (selectedStudy()?.confirmation?.status === "confirmed" || elements.surfaceConditionRows.children.length >= 48) return;
  appendSurfaceCondition(); syncBoundaryControls();
});

function populateContactComponents(select, selectedId = "") {
  const components = selectedWorkpiece()?.components || [];
  select.replaceChildren(...components.map(component => {
    const option = createElement("option", "", component.name);
    option.value = component.component_id;
    return option;
  }));
  if (selectedId && !components.some(component => component.component_id === selectedId)) {
    const missing = createElement("option", "", "组件不可用");
    missing.value = selectedId;
    select.append(missing);
  }
  select.value = selectedId || components[0]?.component_id || "";
}

function populateContactRegions(select, componentId, selectedId = "") {
  const regions = (selectedWorkpiece()?.regions || []).filter(region =>
    ["component_surface", "surface_patch"].includes(region.kind) && region.component_ids?.includes(componentId));
  select.replaceChildren(...regions.map(region => {
    const option = createElement("option", "", region.name);
    option.value = region.region_id;
    return option;
  }));
  if (selectedId && !regions.some(region => region.region_id === selectedId)) {
    const missing = createElement("option", "", "区域不可用");
    missing.value = selectedId;
    select.append(missing);
  }
  select.value = selectedId || regions[0]?.region_id || "";
}

function thermalContactValues() {
  return [...elements.thermalContactRows.children].map(row => {
    const values = {
      kind: "thermal_contact",
      source_component_id: row.querySelector(".source-component").value,
      target_component_id: row.querySelector(".target-component").value,
      source_region_id: row.querySelector(".source-region").value,
      target_region_id: row.querySelector(".target-region").value,
      contact_resistance_m2_k_w: Number(row.querySelector(".contact-resistance").value),
    };
    const area = row.querySelector(".contact-area").value;
    const gap = row.querySelector(".contact-gap").value;
    if (area !== "") values.contact_area_m2 = Number(area);
    if (gap !== "") values.max_gap_mm = Number(gap);
    return values;
  });
}

function appendThermalContact(condition = null) {
  const components = selectedWorkpiece()?.components || [];
  if (components.length < 2 && !condition) return;
  const sourceId = condition?.source_component_id || components[0]?.component_id || "";
  const targetId = condition?.target_component_id
    || components.find(component => component.component_id !== sourceId)?.component_id || "";
  const row = createElement("div", "thermal-contact-row");
  const header = createElement("header");
  const remove = createElement("button", "icon-button", "×");
  remove.type = "button";
  remove.title = "移除组件热接触";
  remove.setAttribute("aria-label", remove.title);
  remove.addEventListener("click", () => { row.remove(); syncBoundaryControls(); drawWorkpiece(); });
  header.append(createElement("strong", "", "表面对表面传热"), remove);

  const createSide = (side, componentId, regionId) => {
    const wrapper = createElement("div", "contact-side");
    const componentLabel = createElement("label", "", side === "source" ? "源组件" : "目标组件");
    const component = createElement("select", `contact-component ${side}-component`);
    component.required = true;
    component.setAttribute("aria-label", side === "source" ? "热接触源组件" : "热接触目标组件");
    populateContactComponents(component, componentId);
    const regionLabel = createElement("label", "", side === "source" ? "源表面区域" : "目标表面区域");
    const region = createElement("select", `contact-region ${side}-region`);
    region.required = true;
    region.setAttribute("aria-label", side === "source" ? "热接触源表面区域" : "热接触目标表面区域");
    populateContactRegions(region, component.value, regionId);
    component.addEventListener("change", () => {
      populateContactRegions(region, component.value);
      state.selectedComponentId = component.value;
      state.selectedRegionId = region.value || null;
      setVisualizationMode("model");
    });
    region.addEventListener("change", () => {
      state.selectedComponentId = component.value;
      state.selectedRegionId = region.value || null;
      setVisualizationMode("model");
    });
    componentLabel.append(component);
    regionLabel.append(region);
    wrapper.append(componentLabel, regionLabel);
    return wrapper;
  };

  const fields = createElement("div", "form-grid");
  const numericField = (className, title, value, maximum, required = false) => {
    const label = createElement("label", "", title);
    const input = createElement("input", className);
    Object.assign(input, { type: "number", min: "0.000000000001", max: String(maximum), step: "any", required });
    input.value = value ?? "";
    label.append(input);
    return label;
  };
  fields.append(
    numericField("contact-resistance", "单位面积热阻 · m²·K/W", condition?.contact_resistance_m2_k_w, 100000000, true),
    numericField("contact-area", "有效面积 · m²（可选）", condition?.contact_area_m2, 1000000),
    numericField("contact-gap", "最大间隙 · mm（可选）", condition?.max_gap_mm, 1000000),
  );
  row.addEventListener("input", () => drawWorkpiece());
  row.append(
    header,
    createSide("source", sourceId, condition?.source_region_id),
    createSide("target", targetId, condition?.target_region_id),
    fields,
  );
  elements.thermalContactRows.append(row);
}

elements.addThermalContact.addEventListener("click", () => {
  if (selectedStudy()?.confirmation?.status === "confirmed"
      || elements.thermalContactRows.children.length >= 100) return;
  appendThermalContact();
  syncBoundaryControls();
  drawWorkpiece();
});

function syncEnabledThermalFields() {
  const confirmed = selectedStudy()?.confirmation?.status === "confirmed";
  for (const [attribute, enabled] of [["data-source-field", elements.draftEnableHeatSource.checked],
    ["data-convection-field", elements.draftEnableGlobalConvection.checked]]) {
    document.querySelectorAll(`[${attribute}]`).forEach(label => {
      label.hidden = !enabled; label.querySelector("input").disabled = confirmed || !enabled;
    });
  }
  const workpiece = selectedWorkpiece();
  const hasPreview = Boolean(workpiece?.geometry?.summary?.preview?.triangles?.length
    || workpiece?.kind === "box");
  elements.sourceEditButton.hidden = !selectedStudy()?.plan || !hasPreview || Boolean(state.comparison);
  drawWorkpiece();
}
elements.draftEnableHeatSource.addEventListener("change", syncEnabledThermalFields);
elements.draftEnableGlobalConvection.addEventListener("change", syncEnabledThermalFields);
elements.materialsCreateDraftButton.addEventListener("click", async () => {
  const workpiece = selectedWorkpiece();
  if (!workpiece) return;
  if (!workpiece.unit_confirmed) {
    switchTab("geometry");
    showToast("请先确认 STL 尺度", true);
    return;
  }
  const study = await createStudy(
    workpiece.workpiece_id,
    "请生成可编辑的热传导仿真草案，随后由用户手动确认材料、热源和边界条件",
    null, "manual",
  );
  if (study?.plan) switchTab("materials");
});
elements.materialsGoScenario.addEventListener("click", () => switchTab("scenario"));
elements.editMaterialsButton.addEventListener("click", () => copySelectedStudy("materials"));
elements.editParametersButton.addEventListener("click", () => copySelectedStudy("scenario"));
elements.manualDraftButton.addEventListener("click", async () => {
  if (!selectedWorkpiece()?.unit_confirmed) return;
  const study = await createStudy(selectedWorkpiece().workpiece_id, "手动设置瞬态导热", null, "manual");
  if (study?.plan) switchTab("materials");
});
elements.openSourceEditorFromScenario.addEventListener("click", () => {
  if (!selectedStudy()?.plan) return;
  openSourceEditor();
  elements.sourceEditorSlot.scrollIntoView({ block: "nearest", behavior: "smooth" });
});

function updateUnitDimensions() {
  const workpiece = selectedWorkpiece();
  const source = workpiece?.source_dimensions;
  if (!source) {
    elements.unitDimensions.textContent = "—";
    return;
  }
  const scale = { um: 0.001, mm: 1, m: 1000 }[elements.lengthUnit.value] || 1;
  elements.unitDimensions.textContent = `${formatNumber(source.x * scale)} × ${formatNumber(source.y * scale)} × ${formatNumber(source.z * scale)} mm`;
}

function renderMaterialsPanel() {
  const workpiece = selectedWorkpiece();
  const study = selectedStudy();
  const plan = study?.plan;
  const renderKey = `${study?.study_id}:${study?.confirmation?.status}`;
  const confirmed = study?.confirmation?.status === "confirmed";
  elements.editMaterialsButton.hidden = !confirmed;
  elements.editMaterialsButton.disabled = state.busy || Boolean(activeStudyTask());
  elements.editParametersButton.hidden = !confirmed;
  elements.editParametersButton.disabled = state.busy || Boolean(activeStudyTask());
  elements.manualDraftButton.hidden = Boolean(plan);
  const materialsConfirmed = Boolean(plan) && (
    confirmed || state.materialsConfirmedFor === study?.study_id
  );
  elements.materialConfirmationNotice.hidden = !plan || confirmed;
  elements.materialConfirmationCheck.hidden = !plan;
  elements.confirmMaterials.checked = materialsConfirmed;
  elements.confirmMaterials.disabled = confirmed;
  elements.materialConfirmationCheck.classList.toggle("is-confirmed", materialsConfirmed);
  elements.materialsCreateDraftButton.hidden = Boolean(plan);
  elements.materialsCreateDraftButton.disabled = state.busy || !workpiece?.unit_confirmed;
  elements.materialsGoScenario.hidden = Boolean(plan);
  if (plan && state.materialsSyncedFor === renderKey) return;
  state.materialsSyncedFor = plan ? renderKey : null;
  elements.materialsEmpty.hidden = Boolean(plan);
  elements.componentMaterialList.replaceChildren();
  elements.materialDetail.hidden = !plan;
  if (!workpiece || !plan) return;
  const components = workpiece.components?.length
    ? workpiece.components
    : [{ component_id: "component-whole", name: "整个工件", triangle_count: 0 }];
  const assignments = new Map(
    (plan.component_materials || []).map((item) => [item.component_id, item]),
  );
  components.forEach((component, index) => {
    const assignment = assignments.get(component.component_id);
    const assignedMaterial = assignment?.material || plan.material;
    const row = createElement("div", "component-material-row");
    row.dataset.componentId = component.component_id;
    const heading = createElement("span");
    heading.append(
      createElement("i", `component-swatch component-color-${index % 6}`),
      createElement("strong", "", component.name),
    );
    const select = createElement("select", "component-material-select");
    select.setAttribute("aria-label", `${component.name}材料`);
    select.disabled = study.confirmation?.status === "confirmed";
    state.materials.forEach((entry) => {
      const option = createElement("option", "", entry.material.name);
      option.value = entry.material_id;
      select.append(option);
    });
    const customOption = createElement("option", "", "自定义物性（下方填写）");
    customOption.value = "custom";
    select.append(customOption);
    const selectedCatalog = state.materials.find(
      (entry) => entry.material_id === assignment?.material_id,
    );
    if (selectedCatalog) {
      select.value = selectedCatalog.material_id;
      row.materialId = selectedCatalog.material_id;
      row.baseMaterial = selectedCatalog.material;
    } else {
      const option = createElement("option", "", `${assignedMaterial.name} · 当前值`);
      option.value = "";
      option.selected = true;
      select.prepend(option);
      row.materialId = null;
      row.baseMaterial = assignedMaterial;
    }
    const fields = createElement("div", "component-material-fields");
    const fieldDefinitions = [
      ["导热系数", "W/(m·K)", "thermal_conductivity_w_m_k", 0.011, 5000],
      ["密度", "kg/m³", "density_kg_m3", 1.01, 30000],
      ["比热容", "J/(kg·K)", "specific_heat_j_kg_k", 1.01, 20000],
    ];
    for (const [labelText, unit, property, minimum, maximum] of fieldDefinitions) {
      const label = createElement("label");
      const title = createElement("span", "", labelText);
      title.append(createElement("i", "", unit));
      const input = document.createElement("input");
      input.type = "number";
      input.step = "any";
      input.min = String(minimum);
      input.max = String(maximum);
      input.required = true;
      input.dataset.materialProperty = property;
      input.disabled = study.confirmation?.status === "confirmed";
      label.append(title, input);
      fields.append(label);
    }
    const source = createElement("small", "material-row-source");
    const syncRow = (material, materialId) => {
      row.baseMaterial = material;
      row.materialId = materialId;
      fields.querySelectorAll("input").forEach((input) => {
        input.value = material[input.dataset.materialProperty];
      });
      source.textContent = materialSourceSummary(material, materialId);
      renderMaterialDetail(material, materialId);
    };
    select.addEventListener("change", () => {
      const entry = state.materials.find((item) => item.material_id === select.value);
      if (entry) syncRow(entry.material, entry.material_id);
      else if (select.value === "custom") syncRow({
        ...materialAssignmentFromRow(row).material,
        name: "自定义材料", source_type: "user", source_basis: "用户手动输入的热物性。",
        source_reference: null, source_version: null, source_citation: null,
        valid_temperature_min_k: null, valid_temperature_max_k: null,
      }, null);
    });
    fields.addEventListener("input", () => {
      const current = materialAssignmentFromRow(row);
      source.textContent = materialSourceSummary(current.material, current.material_id);
      renderMaterialDetail(current.material, current.material_id);
    });
    row.addEventListener("focusin", () => {
      const current = materialAssignmentFromRow(row);
      renderMaterialDetail(current.material, current.material_id);
    });
    row.append(heading, select, fields, source);
    syncRow(row.baseMaterial, row.materialId);
    elements.componentMaterialList.append(row);
  });
  const first = elements.componentMaterialList.querySelector(".component-material-row");
  if (first) {
    const current = materialAssignmentFromRow(first);
    renderMaterialDetail(current.material, current.material_id);
  }
}

function resetMaterialConfirmation() {
  state.materialsConfirmedFor = null;
  elements.confirmMaterials.checked = false;
  elements.materialConfirmationCheck.classList.remove("is-confirmed");
}

function materialAssignmentFromRow(row) {
  const base = row.baseMaterial;
  const values = Object.fromEntries(
    [...row.querySelectorAll("input[data-material-property]")].map((input) => [
      input.dataset.materialProperty,
      Number(input.value),
    ]),
  );
  const edited = Object.entries(values).some(
    ([property, value]) => !Number.isFinite(value) || value !== Number(base[property]),
  );
  if (!edited) {
    return {
      component_id: row.dataset.componentId,
      material_id: row.materialId,
      material: base,
    };
  }
  const baseName = base.name.replace(/（用户覆盖）$/, "");
  return {
    component_id: row.dataset.componentId,
    material_id: null,
    material: {
      ...base,
      ...values,
      name: `${baseName}（用户覆盖）`,
      source_basis: "用户在结构化材料面板中确认或覆盖的热物性值。",
      source_type: "user",
      source_reference: null,
      source_version: null,
      source_citation: null,
      valid_temperature_min_k: null,
      valid_temperature_max_k: null,
    },
  };
}

function materialSourceSummary(material, materialId) {
  const sourceLabels = { database: "材料目录", user: "用户输入", suggestion: "待确认建议" };
  const identity = materialId ? " · 已锁定目录记录" : "";
  const version = material.source_version ? ` · ${material.source_version}` : "";
  return `${sourceLabels[material.source_type] || "来源未知"}${identity}${version}`;
}

function renderMaterialDetail(material, materialId = null) {
  if (!material) return;
  elements.materialConductivity.textContent = `${formatNumber(material.thermal_conductivity_w_m_k)} W/(m·K)`;
  elements.materialDensitySummary.textContent = `${formatNumber(material.density_kg_m3)} kg/m³`;
  elements.materialHeatCapacity.textContent = `${formatNumber(material.specific_heat_j_kg_k)} J/(kg·K)`;
  elements.materialEmissivity.textContent = material.emissivity === null || material.emissivity === undefined
    ? "未提供"
    : formatNumber(material.emissivity, 3);
  const range = material.valid_temperature_min_k && material.valid_temperature_max_k
    ? ` · ${formatNumber(material.valid_temperature_min_k)}–${formatNumber(material.valid_temperature_max_k)} K`
    : "";
  const citation = material.source_citation ? ` · ${material.source_citation}` : "";
  elements.materialSource.textContent = `${materialSourceSummary(material, materialId)}${range}${citation}`;
}

function renderStructuredDraft() {
  const study = selectedStudy();
  const plan = study?.plan;
  elements.structuredInputs.hidden = !plan;
  elements.generateDraftButton.hidden = Boolean(plan);
  elements.sourceQuickActions.hidden = !plan;
  elements.draftState.textContent = !plan
    ? "待描述"
    : study.confirmation?.status === "confirmed"
    ? "已确认"
    : "草案待确认";
  if (!plan) {
    elements.studyPurpose.disabled = false;
    elements.missingInformation.hidden = true;
    elements.unsupportedPhysics.hidden = true;
    return;
  }
  if (state.draftSyncedFor !== study.study_id) {
    state.draftBaseRevision = study.draft_revision || 0;
    state.draftDirty = false;
    state.draftSaveError = false;
    elements.studyPurpose.value = study.purpose || plan.purpose || "";
    elements.draftAnalysisType.value = plan.analysis_type;
    elements.draftInitialTemperature.value = plan.initial_temperature_k ?? "";
    elements.draftDuration.value = plan.duration_s ?? "";
    elements.draftTimeStep.value = plan.time_step_s ?? "";
    const primarySource = ThermoFlowSources.sourcesFromPlan(plan)[0];
    elements.draftSourcePower.value = primarySource?.total_power_w ?? "";
    elements.draftSourceRadius.value = primarySource?.radius_mm ?? "";
    elements.draftAmbient.value = plan.convection?.ambient_temperature_k ?? "";
    elements.draftConvection.value = plan.convection?.heat_transfer_coefficient_w_m2_k ?? "";
    elements.draftEnableHeatSource.checked = Boolean(primarySource && plan.heat_source_enabled);
    elements.draftEnableGlobalConvection.checked = Boolean(plan.convection && plan.global_convection_enabled);
    elements.surfaceConditionRows.replaceChildren();
    (plan.surface_conditions || []).forEach(condition => appendSurfaceCondition(condition));
    elements.thermalContactRows.replaceChildren();
    (plan.contacts || []).forEach(condition => appendThermalContact(condition));
    const heatAxis = plan.boundaries?.[0]?.selector?.split(".")?.[1]?.[0] || "x";
    elements.draftHeatAxis.value = heatAxis;
    const temperatures = Object.fromEntries(
      (plan.boundaries || []).map((item) => [item.selector, item.temperature_k]),
    );
    elements.draftMinTemperature.value = temperatures[`face.${heatAxis}min`] ?? "";
    elements.draftMaxTemperature.value = temperatures[`face.${heatAxis}max`] ?? "";
    elements.fixedBoundaryRows.replaceChildren();
    (plan.boundaries || []).forEach(boundary => appendFixedBoundary(boundary));
    const criterion = (plan.criteria || []).find((item) => item.metric === "max_temperature");
    elements.draftCriterionMax.value = criterion?.target?.value ?? "";
    elements.draftMeshSize.value = plan.mesh?.target_element_size_mm ?? "";
    state.draftSyncedFor = study.study_id;
  }
  elements.structuredInputs.querySelectorAll("input, select").forEach((input) => {
    input.disabled = study.confirmation?.status === "confirmed";
  });
  elements.openSourceEditorFromScenario.disabled = study.confirmation?.status === "confirmed"
    ? false : !elements.draftEnableHeatSource.checked;
  const regional = selectedWorkpiece()?.cad_format === "stl";
  elements.fixedBoundaryEditor.hidden = !regional;
  elements.surfaceThermalEditor.hidden = !regional;
  elements.thermalContactEditor.hidden = !regional;
  document.querySelectorAll("[data-legacy-boundary]").forEach(label => {
    label.hidden = regional;
    label.querySelector("input, select").disabled = regional || study.confirmation?.status === "confirmed";
  });
  elements.fixedBoundaryRows.querySelectorAll("input, select").forEach(input => {
    input.disabled = !regional || study.confirmation?.status === "confirmed";
  });
  elements.thermalContactRows.querySelectorAll("input, select").forEach(input => {
    input.disabled = !regional || study.confirmation?.status === "confirmed";
  });
  syncBoundaryControls();
  syncEnabledThermalFields();
  elements.studyPurpose.disabled = study.confirmation?.status === "confirmed";
  syncTransientFields();
  renderReviewList(elements.missingInformation, [...(plan.missing_information || []), ...(study.policy?.errors || [])]);
  renderReviewList(elements.unsupportedPhysics, plan.unsupported_physics || []);
}

function syncTransientFields() {
  const transient = elements.draftAnalysisType.value === "transient_conduction";
  document.querySelectorAll("[data-transient-field]").forEach((label) => {
    label.hidden = !transient;
    label.querySelector("input").required = transient;
  });
  if (transient) {
    const defaults = ThermoFlowSources.longTransientWindow(selectedStudy()?.plan || {});
    if (elements.draftInitialTemperature.value === "") {
      elements.draftInitialTemperature.value = String(defaults.initialTemperature);
    }
    if (elements.draftDuration.value === "") elements.draftDuration.value = String(defaults.duration);
    if (elements.draftTimeStep.value === "") elements.draftTimeStep.value = String(defaults.timeStep);
  }
}

function renderReviewList(section, items) {
  section.hidden = !items.length;
  section.querySelector("ul").replaceChildren(
    ...items.map((item) => createElement("li", "", item)),
  );
}

function renderMeshPanel() {
  const study = selectedStudy();
  const mesh = state.mesh;
  const derived = study?.policy?.derived || {};
  const warnings = mesh
    ? [...(mesh.warnings || []), ...(mesh.notices || [])]
    : study?.policy?.warnings || [];
  elements.draftMeshSize.disabled = !study?.plan || study.confirmation?.status === "confirmed";
  elements.effectiveMeshSize.textContent = formatNumber(
    mesh?.pitch_mm ?? derived.effective_pitch_mm,
  );
  elements.meshCellCountLabel.textContent = mesh ? "实际活动单元" : "预计包围盒单元";
  elements.meshPointCountLabel.textContent = mesh ? "实际网格点" : "预计网格点";
  elements.estimatedCells.textContent = mesh?.active_cells
    ? formatInteger(mesh.active_cells)
    : derived.cells
      ? formatInteger(derived.cells)
      : "—";
  elements.estimatedPoints.textContent = mesh?.grid?.points
    ? formatInteger(mesh.grid.points)
    : derived.grid_points
      ? formatInteger(derived.grid_points)
      : "—";
  elements.surfaceCells.textContent = mesh?.surface_cells ? formatInteger(mesh.surface_cells) : "—";
  const layers = mesh?.quality?.occupied_layers;
  elements.occupiedLayers.textContent = layers
    ? `${formatInteger(layers.x)} × ${formatInteger(layers.y)} × ${formatInteger(layers.z)}`
    : "—";
  elements.meshConnectivity.textContent = mesh?.quality
    ? `${formatInteger(mesh.quality.connected_regions)} / ${formatInteger(mesh.quality.expected_components)}`
    : "—";
  const volumeDeviation = mesh?.quality?.volume_deviation_percent;
  elements.meshVolumeDeviation.textContent = Number.isFinite(Number(volumeDeviation))
    ? `${formatNumber(volumeDeviation, 1)}%`
    : mesh
      ? "开放网格不适用"
      : "—";
  const qualityLabels = { passed: "通过", warning: "需关注", blocked: "阻止求解" };
  elements.meshQuality.textContent = mesh
    ? `${qualityLabels[mesh.quality_status] || "未知"} · 长宽比 ${formatNumber(mesh.quality.maximum_aspect_ratio, 2)}`
    : study?.plan
      ? "尚未生成真实网格"
      : "待生成";
  elements.meshRisk.textContent = !study?.plan
    ? "待生成"
    : mesh?.quality_status === "blocked"
      ? "已阻止"
      : mesh
        ? warnings.length ? `${warnings.length} 项提示` : "可求解"
        : "待生成";
  elements.meshWarnings.textContent = warnings.length
    ? warnings.join(" ")
    : mesh
      ? "实际网格通过当前质量阈值，可以进入求解。"
      : study?.plan
        ? "当前仅为规模估算；确认输入后生成真实网格并检查质量。"
        : "生成草案后可检查网格规模与风险。";
  elements.boundaryMapping.hidden = !mesh?.boundary_mapping?.length;
  elements.boundaryMapping.replaceChildren(...(mesh?.boundary_mapping || []).map(boundary => {
    const row = createElement("div");
    row.append(createElement("dt", "", boundaryName(boundary)),
      createElement("dd", "", `${formatInteger(boundary.mapped_cells)} 个单元 · ` + (boundary.kind
        ? `${{ heat_flux: "热流", convection: "对流", radiation: "辐射" }[boundary.kind]} · ${formatScientific(boundary.mapped_area.value)} m²`
        : `${formatNumber(boundary.temperature_k)} K`)));
    return row;
  }));
  const needsReview = study?.mesh_status === "needs_review" && mesh?.review_status === "pending";
  if (elements.meshReviewCheck.dataset.studyId !== study?.study_id) {
    elements.acceptMeshWarnings.checked = false;
    elements.meshReviewCheck.dataset.studyId = study?.study_id || "";
  }
  elements.meshReviewCheck.hidden = !needsReview;
  elements.acceptMeshWarnings.disabled = state.busy || !needsReview;
  const artifact = mesh?.artifacts?.[0];
  elements.meshArtifact.hidden = !artifact || !study;
  if (artifact && study) {
    elements.meshArtifact.href = `/v1/studies/${encodeURIComponent(study.study_id)}/artifacts/${encodeURIComponent(artifact.name)}`;
    elements.meshArtifact.download = artifact.name;
    elements.meshArtifact.textContent = `下载网格 VTK · ${formatBytes(artifact.size_bytes)}`;
  }
}

function renderEvaluation() {
  const study = selectedStudy();
  const result = state.result;
  const statuses = {
    meets_criteria: ["满足设定目标", "满足", "state-success"],
    violates_criteria: ["不满足设定目标", "超限", "state-failed"],
    indeterminate: ["无法判定", "判据不足", "state-planned"],
    not_evaluated: ["尚未评估", "待结果", "state-neutral"],
  };
  const status = result?.evaluation_status || study?.evaluation_status || "not_evaluated";
  const [title, badge, className] = statuses[status] || statuses.not_evaluated;
  elements.evaluationTitle.textContent = title;
  elements.evaluationBadge.textContent = badge;
  elements.evaluationBadge.className = `state-badge ${className}`;
  const summaries = result?.evaluation_summary || study?.evaluation_summary || [];
  elements.evaluationCallout.textContent = summaries[0]
    || "完成求解后将按用户设置的判据评估结果。";
  elements.evaluationList.replaceChildren(
    ...summaries.slice(1).map((item) => createElement("li", "", item)),
  );
  const assumptions = (result?.assumptions || study?.plan?.assumptions || []).filter(
    (item) => !item.includes("稀疏线性系统实际使用"),
  );
  const unsupported = (study?.plan?.unsupported_physics || []).map(
    (item) => `未建模：${item}`,
  );
  elements.evaluationAssumptions.replaceChildren(
    ...[...unsupported, ...assumptions].map((item) => createElement("li", "", item)),
  );
}

function displayedTimeResult(result) {
  if (!result || !state.timeFrame || state.comparison) return result;
  const frame = state.timeFrame;
  if (frame.study_id !== result.study_id) return result;
  return {
    ...result,
    temperature_min_k: frame.step.temperature_min_k,
    temperature_max_k: frame.step.temperature_max_k,
    time_s: frame.step.time_s,
    boundary_power_balance: frame.step.boundary_power_balance || {},
    energy_balance_reference_power: frame.step.energy_balance_reference_power ?? null,
    energy_balance_relative_error: frame.step.energy_balance_relative_error ?? 0,
    temperature_field_preview: frame.temperature_field_preview,
    heat_flux_field_preview: frame.heat_flux_field_preview,
    heat_flux_w_m2: frame.heat_flux_field_preview?.maximum_magnitude_w_m2
      ?? frame.surface?.heat_flux_max_w_m2 ?? null,
  };
}

function renderTimeControls() {
  const steps = state.result?.time_steps || [];
  const playbackReady = !steps.length || state.playbackStudyId === state.result?.study_id;
  elements.timeControls.hidden = !steps.length || Boolean(state.comparison);
  elements.timeControls.closest(".workspace").classList.toggle("has-time-results", !elements.timeControls.hidden);
  elements.timePosition.max = String(Math.max(0, steps.length - 1));
  elements.timePosition.value = String(state.timeIndex);
  elements.timeLabel.textContent = `${formatNumber(steps[state.timeIndex]?.time_s || 0)} s`;
  elements.timeControls.setAttribute("aria-busy", String(state.timeLoading));
  elements.timeControls.dataset.playbackReady = String(playbackReady);
  elements.timeLoading.hidden = !state.timeLoading;
  elements.timeLoading.textContent = playbackReady ? "" : `一次加载 ${steps.length} 个真实温度帧…`;
  elements.timePosition.disabled = !playbackReady;
  elements.timePlay.disabled = !playbackReady;
  elements.timeLockScale.checked = true;
  elements.timeLockScale.disabled = true;
  elements.timePlay.textContent = state.timePlaying ? "Ⅱ" : "▶";
  elements.timePlay.title = state.timePlaying ? "暂停播放" : "播放时间步";
  elements.timePlay.setAttribute("aria-label", elements.timePlay.title);
}

function stopTimePlayback() {
  state.timePlaying = false;
  clearTimeout(state.timeTimer);
  state.timeTimer = null;
}

function cancelScheduledTimeStep() {
  clearTimeout(state.timeScrubTimer);
  state.timeScrubTimer = null;
}

function scheduleTimeStep(index) {
  stopTimePlayback();
  cancelScheduledTimeStep();
  const maximum = Math.max(0, (state.result?.time_steps?.length || 1) - 1);
  state.timeIndex = Math.max(0, Math.min(Number(index), maximum));
  if (state.timeFrames.has(`${state.result?.study_id}:${state.timeIndex}`)) {
    selectTimeStep(state.timeIndex);
    return;
  }
  state.timeLoading = true;
  renderTimeControls();
  state.timeScrubTimer = setTimeout(() => {
    state.timeScrubTimer = null;
    selectTimeStep(state.timeIndex);
  }, 90);
}

async function selectTimeStep(index) {
  const studyId = state.result?.study_id;
  if (!studyId || !state.result.time_steps?.[index]) return false;
  cancelScheduledTimeStep();
  state.timeIndex = index;
  const cacheKey = `${studyId}:${index}`;
  const cached = state.timeFrames.get(cacheKey);
  state.timeLoading = !cached;
  renderTimeControls();
  if (cached) {
    state.timeFrame = cached;
    state.timeLoading = false;
    state.probe = null;
    renderTimeControls();
    renderResult(state.result);
    return drawWorkpiece();
  }
  const ticket = ++state.timeRequest;
  try {
    const frame = await request(`/v1/studies/${studyId}/frames/${index}`);
    if (ticket !== state.timeRequest || state.result?.study_id !== studyId) return false;
    state.timeFrames.set(cacheKey, frame);
    state.timeFrame = frame;
    state.timeIndex = index;
    state.timeLoading = false;
    state.probe = null;
    renderTimeControls();
    renderResult(state.result);
    if (await drawWorkpiece() === false) {
      stopTimePlayback();
      renderTimeControls();
      return false;
    }
    return true;
  } catch (error) {
    if (ticket === state.timeRequest) {
      stopTimePlayback();
      state.timeLoading = false;
      state.timeIndex = state.timeFrame?.step?.index
        ?? Math.max(0, (state.result?.time_steps?.length || 1) - 1);
      renderTimeControls();
      showToast(`该时刻结果读取失败：${error.message}`, true);
    }
    return false;
  }
}

async function playNextTimeStep() {
  if (!state.timePlaying) return;
  const next = state.timeIndex + 1;
  if (next >= (state.result?.time_steps?.length || 0)) {
    stopTimePlayback();
    renderTimeControls();
    return;
  }
  if (await selectTimeStep(next) && state.timePlaying) {
    state.timeTimer = setTimeout(playNextTimeStep, 100);
  }
}

function renderResult(result) {
  result = displayedTimeResult(result);
  elements.resultAnalysisLabel.textContent = result.time_steps?.length ? "瞬态导热" : "稳态导热";
  renderTimeControls();
  elements.resultTimePeakRow.hidden = !result.time_steps?.length;
  elements.resultTimePeak.textContent = `${formatNumber(result.temperature_max_over_time_k - 273.15)} ℃`;
  elements.resultResistanceLabel.textContent = result.time_steps?.length ? "温升 / 功率" : "热阻";
  const frameTemperatures = ThermoFlowTransient.legendValues(result.temperature_min_k, result.temperature_max_k);
  elements.resultMin.textContent = formatNumber(frameTemperatures.values[0]);
  elements.resultMax.textContent = formatNumber(frameTemperatures.values[2]);
  elements.resultHeatRate.textContent = formatNumber(result.heat_rate_w);
  elements.resultResistance.textContent = formatNumber(result.thermal_resistance_k_w, 4);
  const currentFlux = result.heat_flux_field_preview?.maximum_magnitude_w_m2 ?? result.heat_flux_w_m2;
  elements.resultFlux.textContent = currentFlux == null
    ? "切换热流视图读取当前帧" : `${formatNumber(currentFlux)} W/m²`;
  const plan = selectedStudy()?.plan;
  const componentNames = new Map(
    (selectedWorkpiece()?.components || []).map(component => [component.component_id, component.name]),
  );
  const materialWarnings = ThermoFlowTransient.materialRangeWarnings(plan, result);
  elements.materialRangeWarning.hidden = !materialWarnings.length;
  elements.materialRangeWarning.textContent = materialWarnings.map(warning => {
    const scope = warning.componentIds.length
      ? warning.componentIds.map(id => componentNames.get(id) || id).join("、")
      : "整个工件";
    return `${scope} · ${warning.message}`;
  }).join("\n");
  const referenceTemperatures = (plan?.boundaries || []).map(boundary => boundary.temperature_k);
  if (plan?.convection && plan.global_convection_enabled) referenceTemperatures.push(plan.convection.ambient_temperature_k);
  (plan?.surface_conditions || []).forEach(condition => {
    if (condition.kind === "convection") referenceTemperatures.push(condition.ambient_temperature_k);
    if (condition.kind === "radiation") referenceTemperatures.push(condition.radiation_temperature_k);
  });
  const ambientTemperature = referenceTemperatures.length ? Math.min(...referenceTemperatures) : null;
  if (result.time_steps?.length && result.heat_rate_w > 0 && ambientTemperature != null) {
    elements.resultResistance.textContent = formatNumber(Math.max(0, result.temperature_max_k - ambientTemperature) / result.heat_rate_w, 4);
  }
  elements.resultRise.textContent = ambientTemperature == null ? "不适用" : `${formatNumber(result.temperature_max_k - ambientTemperature)} K`;
  const powerNames = { volumetric_source: "局部功率流入", surface_heat_flux: "表面热流净输入",
    fixed_temperature: "定温边界净输入", convection: "对流净输入", radiation: "辐射净输入",
    thermal_contact_transfer: "组件接触传热（内部绝对值）", storage: "蓄热速率", residual: "能量闭合残差" };
  elements.boundaryPowerBalance.hidden = !Object.keys(result.boundary_power_balance || {}).length;
  elements.boundaryPowerBalance.replaceChildren(...Object.entries(result.boundary_power_balance || {}).map(([key, quantity]) => {
    const row = createElement("div");
    row.append(createElement("dt", "", powerNames[key] || "热功率"),
      createElement("dd", "", `${formatScientific(key === "storage" ? -quantity.value : quantity.value)} W`));
    return row;
  }));
  elements.resultSpan.textContent = `${formatNumber(result.temperature_max_k - result.temperature_min_k)} K`;
  const step = result.time_steps?.[state.timeIndex];
  const hottestSample = step ? [...step.maximum_position_mm, step.temperature_max_k] : null;
  elements.resultHotspot.textContent = hottestSample
    ? `(${hottestSample.slice(0, 3).map((value) => formatNumber(value)).join(", ")}) mm · ${formatNumber(hottestSample[3])} K`
    : "读取完整场位置";
  elements.resultPoints.textContent = formatInteger(result.grid.points);
  elements.resultCells.textContent = formatInteger(result.grid.cells);
  elements.resultCompute.textContent = result.compute_device
    ? `${result.compute_backend} · ${result.compute_device}`
    : result.compute_backend || "—";
  elements.resultError.textContent = formatScientific(result.energy_balance_relative_error);
  elements.artifactList.replaceChildren();
  for (const artifact of result.artifacts) {
    if (artifact.name.endsWith(".json") || artifact.name.endsWith(".npz")) continue;
    if (artifact.name.startsWith("thermal-") && artifact.name !== step?.vtk_artifact) continue;
    const link = createElement("a", "artifact-link");
    link.href = `/v1/studies/${result.study_id}/artifacts/${encodeURIComponent(artifact.name)}`;
    link.download = artifact.name;
    const copy = createElement("span");
    copy.append(
      createElement("strong", "", artifact.name.startsWith("thermal-") ? `当前时刻场数据 · ${formatNumber(step.time_s)} s` : result.time_steps?.length ? "末态温度与热流场数据" : "温度与热流场数据"),
      createElement("small", "", "VTK"),
    );
    link.append(
      createElement("span", "", "↓"),
      copy,
      createElement("span", "", formatBytes(artifact.size_bytes)),
    );
    elements.artifactList.append(link);
  }
}

function clearComparison() {
  state.comparison = null;
  state.comparisonCandidateResult = null;
  state.comparisonMode = "baseline";
  elements.thermalLegend.classList.remove("is-difference");
}

function comparisonCandidates() {
  const selected = selectedStudy();
  return selectedProjectStudies().filter(
    (study) => study.status === "succeeded" && study.study_id !== selected?.study_id,
  );
}

function renderComparisonTool() {
  const selected = selectedStudy();
  elements.copyStudyButton.disabled = !selected?.plan || state.busy;
  const candidates = state.result ? comparisonCandidates() : [];
  const previous = elements.comparisonCandidate.value;
  elements.comparisonCandidate.replaceChildren(
    ...candidates.map((study) => {
      const option = document.createElement("option");
      option.value = study.study_id;
      option.textContent = `${study.plan?.study_name || "仿真研究"} · ${formatDate(study.updated_at)}`;
      return option;
    }),
  );
  if (candidates.some((study) => study.study_id === previous)) {
    elements.comparisonCandidate.value = previous;
  }
  elements.comparisonAvailability.textContent = candidates.length
    ? `${candidates.length} 项结果可比较`
    : "需要另一项结果";
  elements.comparisonCandidate.disabled = !candidates.length || state.busy;
  elements.compareStudiesButton.disabled = !candidates.length || state.busy;

  const comparison = state.comparison;
  const isCurrent = comparison?.baseline_study_id === selected?.study_id;
  elements.exitComparisonButton.hidden = !comparison || !isCurrent;
  elements.comparisonContent.hidden = !comparison || !isCurrent;
  if (!comparison || !isCurrent) return;
  const baseline = comparison.studies.find(
    (study) => study.study_id === comparison.baseline_study_id,
  );
  const difference = comparison.differences[0];
  const candidate = comparison.studies.find(
    (study) => study.study_id === difference?.candidate_study_id,
  );
  if (!baseline || !candidate || !difference) return;
  elements.comparisonScale.textContent = `统一温标 ${formatNumber(comparison.common_scale.minimum.value)}–${formatNumber(comparison.common_scale.maximum.value)} K`;
  elements.comparisonBaselineMax.textContent = `${formatNumber(baseline.temperature_max.value)} K`;
  elements.comparisonBaselineName.textContent = baseline.study_name;
  elements.comparisonCandidateMax.textContent = `${formatNumber(candidate.temperature_max.value)} K`;
  elements.comparisonCandidateName.textContent = candidate.study_name;
  elements.comparisonMaxDelta.textContent = formatSignedQuantity(
    difference.temperature_max_delta.value,
    "K",
  );
  elements.comparisonHeatDelta.textContent = formatSignedQuantity(
    difference.heat_rate_delta.value,
    "W",
  );
  elements.comparisonResistanceDelta.textContent = formatSignedQuantity(
    difference.thermal_resistance_delta?.value,
    "K/W",
  );
  elements.comparisonDifferenceMode.disabled = !difference.field;
  elements.comparisonNotice.textContent = difference.field
    ? `差值场为“${candidate.study_name}”减去“${baseline.study_name}”，来自相同网格坐标的真实求解采样。`
    : difference.field_unavailable_reason;
  for (const [mode, button] of Object.entries({
    baseline: elements.comparisonBaselineMode,
    candidate: elements.comparisonCandidateMode,
    difference: elements.comparisonDifferenceMode,
  })) {
    const active = mode === state.comparisonMode;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

function formatSignedQuantity(value, unit) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value)} ${unit}`;
}

async function copySelectedStudy(targetTab = "scenario") {
  const study = selectedStudy();
  if (!study?.plan) return;
  setBusy(true);
  try {
    const copied = await request(`/v1/studies/${study.study_id}/copy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    state.selectedProjectId = copied.project_id;
    state.selectedWorkpieceId = copied.workpiece_id;
    state.selectedStudyId = copied.study_id;
    state.activeTab = typeof targetTab === "string" ? targetTab : "scenario";
    state.draftSyncedFor = null;
    await loadWorkspace();
    showToast("研究副本已创建，请检查修改并重新确认");
  } catch (error) {
    showToast(`研究复制失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

async function compareSelectedStudies() {
  stopTimePlayback();
  cancelScheduledTimeStep();
  state.timeRequest += 1;
  state.timeFrame = null;
  state.timeLoading = false;
  state.timeFrames.clear();
  state.timeIndex = Math.max(0, (state.result?.time_steps?.length || 1) - 1);
  const baseline = selectedStudy();
  const candidateId = elements.comparisonCandidate.value;
  if (!baseline || !candidateId) return;
  setBusy(true);
  try {
    const [comparison, candidateResult] = await Promise.all([
      request("/v1/study-comparisons", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          study_ids: [baseline.study_id, candidateId],
          baseline_study_id: baseline.study_id,
          quantity: "temperature",
        }),
      }),
      request(`/v1/studies/${candidateId}/result`),
    ]);
    state.comparison = comparison;
    state.comparisonCandidateResult = candidateResult;
    state.comparisonMode = "baseline";
    state.visualizationMode = "thermal";
    state.probe = null;
    render();
    showToast("研究比较已建立，画布使用统一温标");
  } catch (error) {
    clearComparison();
    showToast(`研究比较失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

function setComparisonMode(mode) {
  if (!state.comparison) return;
  const difference = state.comparison.differences[0];
  if (mode === "difference" && !difference?.field) return;
  state.comparisonMode = mode;
  state.visualizationMode = "thermal";
  state.probe = null;
  closeSourceEditor();
  renderComparisonTool();
  renderWorkspace();
  drawWorkpiece();
}

function renderAgent() {
  const study = selectedStudy();
  const eligible = study?.status === "succeeded" && Boolean(state.result);
  elements.agentEmpty.hidden = eligible;
  elements.agentContent.hidden = !eligible;
  elements.runAgentButton.disabled = state.busy || !eligible;
  elements.runAgentButton.textContent = state.busy ? "正在检查与优化…" : "检查目标并自动优化";
  elements.agentMode.textContent = "可选工具";
  elements.agentMode.title = "目标驱动的受控方案优化";
  if (eligible) applyAgentFormDefaults(study);

  const run = state.agentRun;
  elements.agentRun.hidden = !run;
  if (!run) return;
  const statuses = {
    running: "正在检查与优化",
    goal_met: "当前结果满足设定目标",
    budget_exhausted: "当前结果未满足设定目标",
    failed: "目标优化未完成",
  };
  elements.agentRunStatus.textContent = statuses[run.status] || run.status;
  const rounds = Number(run.rounds_completed) || 0;
  if (run.status === "running") {
    elements.agentRoundCount.textContent = rounds ? `已自动重算 ${rounds} 轮` : "正在检查当前结果";
  } else if (rounds === 0) {
    elements.agentRoundCount.textContent = "未触发自动重算";
  } else {
    elements.agentRoundCount.textContent = `已自动重算 ${rounds} 轮`;
  }

  const valueOrNull = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };
  const steps = Array.isArray(run.steps) ? run.steps : [];
  const resultStep = [...steps].reverse().find(
    (step) => valueOrNull(step.metrics?.temperature_max_k) !== null,
  );
  const metrics = resultStep?.metrics || {};
  const goal = run.resolved_goal || {};
  const maximum = valueOrNull(metrics.temperature_max_k);
  const targetMinimum = valueOrNull(goal.target_min_temperature_k);
  const targetMaximum = valueOrNull(goal.target_max_temperature_k);
  const tolerance = valueOrNull(goal.temperature_tolerance_k) || 0;
  const energyError = valueOrNull(metrics.energy_balance_relative_error);
  const energyLimit = valueOrNull(goal.max_energy_balance_relative_error);
  const temperatureText = maximum === null
    ? "最高温度暂不可用"
    : `${formatNumber(maximum)} K（${formatNumber(maximum - 273.15)} °C）`;
  const targetText = targetMinimum !== null && targetMaximum !== null
    ? `${formatNumber(targetMinimum)}–${formatNumber(targetMaximum)} K（${formatNumber(targetMinimum - 273.15)}–${formatNumber(targetMaximum - 273.15)} °C）`
    : null;
  const temperaturePassed = maximum !== null
    && (targetMinimum === null || maximum >= targetMinimum - tolerance)
    && (targetMaximum === null || maximum <= targetMaximum + tolerance);
  const temperatureWithinTarget = maximum !== null
    && (targetMinimum === null || maximum >= targetMinimum)
    && (targetMaximum === null || maximum <= targetMaximum);
  const energyPassed = energyError !== null
    && (energyLimit === null || energyError <= energyLimit);

  if (run.failure) {
    elements.agentRunSummary.textContent = "本次目标优化没有完成。基准仿真结果仍然保留，请检查目标、授权变量和计算预算后重试。";
  } else if (run.status === "goal_met") {
    const targetConclusion = !targetText
      ? ""
      : (temperatureWithinTarget ? "，位于目标范围内" : "，处于允许容差内");
    elements.agentRunSummary.textContent = rounds === 0
      ? `仿真已完成。Agent 仅检查了你设置的目标：最高温度为 ${temperatureText}${targetConclusion}；没有修改参数，也没有重新求解。`
      : `Agent 已完成 ${rounds} 轮自动调参和重新求解。最终最高温度为 ${temperatureText}${targetConclusion}。`;
  } else if (run.status === "running") {
    elements.agentRunSummary.textContent = "正在用当前仿真结果检查目标；只有不满足目标时，才会在授权范围内调参并重新求解。";
  } else {
    elements.agentRunSummary.textContent = `仿真已经完成，但当前结果未满足你设置的优化目标。最高温度为 ${temperatureText}${targetText ? `，目标范围为 ${targetText}` : ""}。`;
  }
  elements.agentSelectedStudy.dataset.studyId = run.selected_study_id;
  elements.agentSelectedStudy.hidden = !run.selected_study_id;
  elements.agentSelectedStudy.textContent = "在画布查看温度场";
  elements.agentSelectedStudy.title = "打开本轮采用的研究结果";
  elements.agentTrace.replaceChildren();
  const appendTraceItem = (phase, marker, title, summary) => {
    const item = createElement("li");
    item.dataset.phase = phase;
    const index = createElement("span", "agent-trace-index", marker);
    const copy = createElement("div", "agent-trace-copy");
    copy.append(createElement("strong", "", title), createElement("span", "", summary));
    item.append(index, copy);
    elements.agentTrace.append(item);
  };
  const baselineResultStep = steps.find((step) => step.tool === "inspect_result");
  let previousMaximum = valueOrNull(baselineResultStep?.metrics?.temperature_max_k);
  const adjustmentSteps = steps.filter(
    (step) => step.phase === "act" && step.tool === "adjust_parameters",
  );
  const solvedRoundSteps = steps.filter((step) => step.tool === "run_solver");
  solvedRoundSteps.forEach((step, index) => {
    const roundMaximum = valueOrNull(step.metrics?.temperature_max_k);
    const roundError = valueOrNull(step.metrics?.energy_balance_relative_error);
    const delta = roundMaximum !== null && previousMaximum !== null
      ? roundMaximum - previousMaximum
      : null;
    const metricParts = [];
    if (roundMaximum !== null) metricParts.push(`最高温度 ${formatNumber(roundMaximum)} K`);
    if (delta !== null) {
      metricParts.push(
        Math.abs(delta) < 0.005
          ? "与上一研究基本一致"
          : `较上一研究${delta < 0 ? "降低" : "升高"} ${formatNumber(Math.abs(delta))} K`,
      );
    }
    if (roundError !== null) metricParts.push(`能量相对误差 ${formatScientific(roundError)}`);
    appendTraceItem(
      "act",
      String(index + 1),
      `第 ${index + 1} 轮 · ${agentChangeLabel(adjustmentSteps[index]?.changes || {})}`,
      metricParts.join("；") || "候选研究已完成。",
    );
    previousMaximum = roundMaximum ?? previousMaximum;
  });
  const checks = [];
  if (maximum !== null) {
    let temperatureSummary = `最高温度 ${temperatureText}`;
    if (targetText) temperatureSummary += `；目标 ${targetText}`;
    checks.push({
      passed: temperaturePassed,
      title: temperaturePassed ? "温度符合要求" : "温度未达到要求",
      summary: temperatureSummary,
    });
  }
  if (energyError !== null) {
    checks.push({
      passed: energyPassed,
      title: energyPassed ? "能量收支正常" : "能量误差超限",
      summary: energyLimit === null
        ? `相对误差 ${formatScientific(energyError)}`
        : `相对误差 ${formatScientific(energyError)}；允许上限 ${formatScientific(energyLimit)}`,
    });
  }
  checks.push({
    passed: run.status === "goal_met",
    title: run.status === "goal_met"
      ? (rounds === 0 ? "Agent 未修改仿真" : "自动优化已完成")
      : (run.status === "running" ? "正在检查目标" : "自动优化未达到目标"),
    summary: run.status === "goal_met"
      ? (rounds === 0 ? "只进行了目标检查，没有生成新的仿真研究。" : `经过 ${rounds} 轮自动调参和重新求解后满足目标。`)
      : (run.failure
        ? "优化任务未完成；基准结果未受影响。"
        : run.summary || "当前结果尚未满足设定目标。"),
  });
  for (const check of checks) {
    appendTraceItem(
      check.passed ? "finish" : "evaluate",
      check.passed ? "✓" : "!",
      check.title,
      check.summary,
    );
  }
}

function agentChangeLabel(changes) {
  const labels = [];
  if (Number.isFinite(Number(changes.heat_source_power_w))) {
    labels.push(`热源功率调至 ${formatNumber(changes.heat_source_power_w)} W`);
  }
  if (Number.isFinite(Number(changes.target_element_size_mm))) {
    labels.push(`网格尺寸调至 ${formatNumber(changes.target_element_size_mm)} mm`);
  }
  if (Number.isFinite(Number(changes.max_axis_intervals))) {
    labels.push(`单轴区间上限调至 ${formatInteger(changes.max_axis_intervals)}`);
  }
  return labels.join("；") || "应用已授权参数调整";
}

function applyAgentFormDefaults(study) {
  if (elements.agentForm.dataset.defaultsFor === study.study_id) return;
  const prescribedTemperatures = (study.plan?.boundaries || [])
    .map((boundary) => boundary.temperature_k);
  if (study.plan?.convection) {
    prescribedTemperatures.push(study.plan.convection.ambient_temperature_k);
  }
  const referenceTemperature = prescribedTemperatures.length
    ? Math.max(...prescribedTemperatures)
    : state.result.temperature_min_k;
  const targetMinimum = referenceTemperature + 2;
  const targetMaximum = referenceTemperature + 6;
  const inputNumber = (value) => value.toFixed(2).replace(/\.00$/, "");

  elements.agentInstruction.value = `将峰值温度控制在 ${inputNumber(targetMinimum)}–${inputNumber(targetMaximum)} K（相对边界基准温升 2–6 K），并校核能量平衡与热源映射。`;
  elements.agentTargetMin.value = inputNumber(targetMinimum);
  elements.agentTargetMax.value = inputNumber(targetMaximum);
  elements.agentTemperatureTolerance.value = "1";
  elements.agentEnergyTolerance.value = "0.00001";
  elements.agentMaxRounds.value = "3";
  elements.agentAllowPower.checked = true;
  elements.agentAllowMesh.checked = true;
  elements.agentForm.dataset.defaultsFor = study.study_id;
}

async function runAgent(event) {
  event.preventDefault();
  const study = selectedStudy();
  if (study?.status !== "succeeded" || !state.result) {
    showToast("请先完成一次仿真求解", true);
    return;
  }
  if (!elements.agentForm.reportValidity()) return;
  const optionalNumber = (element) => element.value === "" ? null : Number(element.value);
  const payload = {
    instruction: elements.agentInstruction.value.trim(),
    target_max_temperature_k: optionalNumber(elements.agentTargetMax),
    target_min_temperature_k: optionalNumber(elements.agentTargetMin),
    temperature_tolerance_k: Number(elements.agentTemperatureTolerance.value),
    max_energy_balance_relative_error: Number(elements.agentEnergyTolerance.value),
    max_rounds: Number(elements.agentMaxRounds.value),
    allow_power_adjustment: elements.agentAllowPower.checked,
    allow_mesh_refinement: elements.agentAllowMesh.checked,
  };
  setBusy(true);
  try {
    const run = await request(`/v1/studies/${study.study_id}/agent-runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.agentRun = run;
    state.selectedStudyId = run.selected_study_id;
    state.activeTab = "agent";
    await loadWorkspace();
    const succeeded = run.status === "goal_met";
    const successMessage = run.rounds_completed
      ? `Agent 优化完成，已自动重算 ${run.rounds_completed} 轮`
      : "目标检查完成，未触发自动重算";
    showToast(
      succeeded ? successMessage : "目标优化未完成，请检查目标、授权变量和计算预算",
      !succeeded,
    );
  } catch (error) {
    showToast(`Agent 运行失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

function switchTab(tabName) {
  state.activeTab = tabName;
  persistWorkspaceState();
  const planViews = {
    geometry: elements.geometryPanel,
    materials: elements.materialsPanel,
    scenario: elements.scenarioPanel,
    mesh: elements.meshPanel,
    solve: elements.solvePanel,
  };
  const navigation = {
    geometry: elements.geometryNav,
    materials: elements.materialsNav,
    scenario: elements.scenarioNav,
    mesh: elements.meshNav,
    solve: elements.solveNav,
    result: elements.resultNav,
    evaluation: elements.evaluationNav,
    agent: elements.agentNav,
  };
  elements.planPane.hidden = !Object.hasOwn(planViews, tabName);
  elements.resultPane.hidden = tabName !== "result";
  elements.evaluationPane.hidden = tabName !== "evaluation";
  elements.agentPane.hidden = tabName !== "agent";
  Object.entries(planViews).forEach(([name, pane]) => { pane.hidden = name !== tabName; });
  Object.entries(navigation).forEach(([name, button]) => {
    const active = name === tabName;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  renderPrimaryAction();
}

function renderPrimaryAction() {
  const workpiece = selectedWorkpiece();
  const study = selectedStudy();
  elements.primaryAction.disabled = state.busy;
  if (state.busy) {
    elements.primaryAction.textContent = "处理中…";
    return;
  }
  if (activeStudyTask()) {
    elements.primaryAction.textContent = activeStudyTask().operation === "mesh" ? "网格任务进行中" : "求解任务进行中";
    elements.primaryAction.disabled = true;
    return;
  }
  if (!workpiece) {
    elements.primaryAction.textContent = "导入 STL";
    return;
  }
  if (!workpiece.unit_confirmed) {
    elements.primaryAction.textContent = "确认 STL 尺度";
    return;
  }
  if (!study || study.status === "rejected" || study.status === "failed") {
    elements.primaryAction.textContent = study ? "重新生成草案" : "描述工况并生成草案";
    return;
  }
  if (study.status === "needs_input") {
    if (state.activeTab === "materials") {
      elements.primaryAction.textContent = "下一步：热源与时长";
      elements.primaryAction.disabled = state.busy || !elements.confirmMaterials.checked;
      return;
    }
    if (!elements.confirmMaterials.checked) {
      elements.primaryAction.textContent = "先选择并确认材料";
      return;
    }
    elements.primaryAction.textContent = "确认并开始仿真";
    elements.primaryAction.disabled = state.busy || state.modelingBusy || state.draftSaveError
      || Boolean(study.modeling?.proposal) || !elements.confirmMaterials.checked
      || !elements.confirmInputs.checked;
    return;
  }
  if (["planned", "ready"].includes(study.status)) {
    if (workpiece.cad_format === "stl" && study.mesh_status !== "ready") {
      if (study.mesh_status === "generating") {
        elements.primaryAction.textContent = "正在生成网格";
        elements.primaryAction.disabled = true;
      } else if (study.mesh_status === "needs_review") {
        elements.primaryAction.textContent = "确认网格风险";
        elements.primaryAction.disabled = state.busy || !elements.acceptMeshWarnings.checked;
      } else if (study.mesh_status === "blocked") {
        elements.primaryAction.textContent = "检查网格风险";
      } else {
        elements.primaryAction.textContent = study.mesh_status === "failed" ? "重新生成网格" : "生成网格";
      }
      return;
    }
    elements.primaryAction.textContent = "开始求解";
    return;
  }
  if (study.status === "running") {
    elements.primaryAction.textContent = "正在求解";
    elements.primaryAction.disabled = true;
    return;
  }
  if (study.status === "succeeded" && state.activeTab === "result") {
    elements.primaryAction.textContent = "结果已显示";
    elements.primaryAction.disabled = true;
    return;
  }
  elements.primaryAction.textContent = "查看结果";
}

async function handlePrimaryAction() {
  const workpiece = selectedWorkpiece();
  const study = selectedStudy();
  if (!workpiece) {
    openWorkpieceDialog();
    return;
  }
  if (!workpiece.unit_confirmed) {
    switchTab("geometry");
    return;
  }
  if (!study || study.status === "rejected" || study.status === "failed") {
    switchTab("scenario");
    return;
  }
  if (study.status === "needs_input") {
    if (state.activeTab === "materials") {
      switchTab("scenario");
      return;
    }
    if (!elements.confirmMaterials.checked) {
      switchTab("materials");
      return;
    }
    await confirmStudy(study.study_id);
    return;
  }
  if (["planned", "ready"].includes(study.status)) {
    if (workpiece.cad_format === "stl" && study.mesh_status === "needs_review") {
      if (!elements.acceptMeshWarnings.checked) {
        switchTab("mesh");
        return;
      }
      await confirmMeshReview(study.study_id);
      return;
    }
    if (workpiece.cad_format === "stl" && study.mesh_status === "blocked") {
      switchTab("mesh");
      showToast("当前确认研究不可直接编辑；请复制为新修订，检查区域映射，并同步调整目标单元尺寸和最大单轴区间数", true);
      return;
    }
    if (workpiece.cad_format === "stl" && study.mesh_status !== "ready") {
      await generateMesh(study.study_id);
      return;
    }
    await runStudy(study.study_id);
    return;
  }
  if (study.status === "succeeded") switchTab("result");
}

async function createStudy(workpieceId, purpose = "", overrides = null, planningMode = "ai") {
  const alreadyBusy = state.busy;
  if (!alreadyBusy) setBusy(true);
  try {
    const study = await request("/v1/studies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workpiece_id: workpieceId,
        purpose,
        overrides,
        require_confirmation: true,
        planning_mode: planningMode,
      }),
    });
    state.selectedStudyId = study.study_id;
    state.activeTab = "scenario";
    state.draftSyncedFor = null;
    await loadWorkspace();
    showToast(
      study.status === "needs_input" ? "仿真草案已生成，请检查并确认参数" : study.failure || "方案未通过校验",
      study.status !== "needs_input",
    );
    return study;
  } catch (error) {
    showToast(`方案生成失败：${error.message}`, true);
    return null;
  } finally {
    if (!alreadyBusy) setBusy(false);
  }
}

async function confirmUnit(event) {
  event.preventDefault();
  const workpiece = selectedWorkpiece();
  if (!workpiece) return;
  setBusy(true);
  try {
    await request(`/v1/workpieces/${workpiece.workpiece_id}/unit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unit: elements.lengthUnit.value }),
    });
    const pendingUpload = state.pendingUploadOverrides;
    state.pendingUploadOverrides = null;
    state.activeTab = "scenario";
    resetStlView(false);
    await loadWorkspace();
    if (state.assistantSession && state.assistantSession.workpiece_id === workpiece.workpiece_id) {
      state.assistantSession = await request(`/v1/assistant-sessions/${state.assistantSession.session_id}/workpiece`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: state.assistantSession.revision, workpiece_id: workpiece.workpiece_id }),
      });
      state.assistantSessionId = state.assistantSession.session_id;
      state.selectedStudyId = state.assistantSession.study_id || null;
      persistWorkspaceState();
      await loadWorkspace();
      showToast("尺度已确认，Agent 将继续补齐仿真参数。");
      return;
    }
    {
      const study = await createStudy(
        workpiece.workpiece_id,
        "手动设置 STL 瞬态导热",
        pendingUpload?.workpieceId === workpiece.workpiece_id ? pendingUpload.overrides : null,
        "manual",
      );
      if (study?.status === "needs_input") {
        switchTab("materials");
        showToast("尺度已确认：请选择材料，下一步设置热源与时长。手动设置不依赖 AI。");
      }
    }
  } catch (error) {
    showToast(`尺度确认失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

function draftFieldsValid() {
  const nullable = new Set(["draftInitialTemperature", "draftDuration", "draftTimeStep"]);
  return [...elements.structuredInputs.querySelectorAll("input, select")].every(input =>
    input.disabled || (nullable.has(input.id) && input.value === "" && !input.validity.badInput) || input.checkValidity())
    && elements.draftMeshSize.checkValidity()
    && (elements.sourceEditor.hidden || [...elements.sourceEditor.querySelectorAll("input, select")]
      .every(input => input.closest("label")?.hidden || input.checkValidity()))
    && [...elements.componentMaterialList.querySelectorAll("input")].every(input => input.checkValidity());
}

function rememberModelingStudy(study, { syncFields = false, appendHistory = false } = {}) {
  const index = state.studies.findIndex(item => item.study_id === study.study_id);
  if (index >= 0 && (state.studies[index].draft_revision || 0) > (study.draft_revision || 0)) return;
  if (index >= 0) state.studies[index] = study;
  if (study.modeling?.history_retained === false && study.modeling.messages.length) {
    const history = appendHistory ? state.modelingSessionHistory.get(study.study_id) || [] : [];
    state.modelingSessionHistory.set(study.study_id, [...history, ...study.modeling.messages].slice(-40));
  }
  if (state.selectedStudyId !== study.study_id) return;
  state.draftBaseRevision = study.draft_revision || 0;
  state.validationFailure = null;
  if (syncFields) {
    state.draftDirty = false;
    state.draftSaveError = false;
    state.draftSyncedFor = null;
    state.materialsSyncedFor = null;
    discardSourceDraft();
    renderInspector();
    drawWorkpiece();
  } else renderModeling();
}

function queueDraftSave(event) {
  const study = selectedStudy();
  if (!study?.plan || study.confirmation?.status === "confirmed") return;
  state.validationFailure = null;
  if (event?.target && elements.componentMaterialList.contains(event.target)) {
    resetMaterialConfirmation();
  }
  const source = activeSourceDraft();
  const sourceCollection = activeSourceCollection();
  if (source && event?.target) {
    for (const [id, field] of [["draftSourcePower", "power"], ["draftSourceRadius", "radius"]]) {
      if (event.target.id === id && event.target.value !== "" && event.target.checkValidity()) {
        source[field] = Number(event.target.value);
        syncSourceEditor();
      }
    }
    for (const [id, field] of [["draftAmbient", "ambientTemperature"], ["draftConvection", "convectionCoefficient"]]) {
      if (event.target.id === id && event.target.value !== "" && event.target.checkValidity()) {
        sourceCollection[field] = Number(event.target.value);
        syncSourceEditor();
      }
    }
  }
  state.draftDirty = true;
  state.draftEditSerial++;
  elements.confirmInputs.checked = false;
  clearTimeout(state.draftSaveTimer);
  state.draftSaveTimer = setTimeout(() => saveStructuredDraft().catch(() => {}), 700);
  renderModeling();
  renderPrimaryAction();
  drawWorkpiece();
}

async function saveStructuredDraft() {
  clearTimeout(state.draftSaveTimer);
  if (state.draftSavePromise) {
    await state.draftSavePromise;
    return saveStructuredDraft();
  }
  const study = selectedStudy();
  if (!study?.plan || !state.draftDirty || study.confirmation?.status === "confirmed") return;
  if (state.draftSaveError) throw new Error("草案保存存在冲突，请先检查服务器状态");
  if (!draftFieldsValid()) {
    elements.draftSaveStatus.textContent = "未保存：请补齐有效参数";
    state.validationFailure = {studyId: study.study_id, message: '草案尚未保存：请补齐有效参数。点击确认时会定位未通过输入检查的字段。'};
    renderValidationFeedback();
    throw new Error("请补齐有效参数后保存草案");
  }
  const editSerial = state.draftEditSerial;
  const body = { expected_revision: state.draftBaseRevision, purpose: elements.studyPurpose.value.trim(), overrides: confirmedOverrides() };
  elements.draftSaveStatus.textContent = "正在保存草案";
  state.draftSavePromise = (async () => {
    try {
      const updated = await request(`/v1/studies/${study.study_id}/draft`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (state.selectedStudyId === study.study_id && state.draftEditSerial === editSerial) state.draftDirty = false;
      rememberModelingStudy(updated);
    } catch (error) {
      state.draftSaveError = true;
      state.validationFailure = {studyId: study.study_id, message: `草案未保存：${error.message}`};
      renderModeling();
      showToast(`草案未保存：${error.message}`, true);
      throw error;
    } finally {
      state.draftSavePromise = null;
    }
  })();
  await state.draftSavePromise;
  if (state.draftDirty) await saveStructuredDraft();
}

async function flushDraftBeforeNavigation() {
  if (state.modelingDecisionBusy) return false;
  try { await saveStructuredDraft(); return true; }
  catch (error) { showToast(error.message, true); return false; }
}

function renderModeling() {
  renderValidationFeedback();
  const study = selectedStudy();
  elements.modelingDrawer.hidden = !study?.plan;
  if (!study?.plan) return;
  const session = study.modeling || {};
  const proposal = session.proposal;
  const confirmed = study.confirmation?.status === "confirmed";
  const history = session.history_retained === false
    ? state.modelingSessionHistory.get(study.study_id) || session.messages || [] : session.messages || [];
  elements.modelingMessages.replaceChildren(...history.map(message => {
    const paragraph = createElement("p", "", message.content);
    paragraph.dataset.role = message.role;
    return paragraph;
  }));
  elements.modelingPrivacy.hidden = session.history_retained !== false;
  elements.draftSaveStatus.textContent = state.draftSaveError ? "保存失败，请检查" : state.modelingBusy ? "正在生成建议"
    : confirmed ? "输入已确认" : state.draftDirty ? "有未保存修改" : proposal ? "有待应用建议" : "草案已保存";
  elements.modelingProposal.hidden = !proposal;
  elements.modelingChanges.replaceChildren(...(proposal?.changes || []).map(change => {
    const item = createElement("dl", "modeling-change");
    item.append(createElement("dt", "", change.label), createElement("dd", "", `当前：${change.before}`),
      createElement("dd", "", `建议：${change.after}`));
    return item;
  }));
  elements.modelingValidation.replaceChildren(...(proposal?.validation_errors || []).map(error => createElement("li", "", error)));
  elements.modelingForm.hidden = confirmed;
  elements.sendModeling.disabled = state.busy || state.modelingBusy || Boolean(proposal) || state.draftSaveError;
  elements.applyModeling.disabled = state.busy || state.modelingBusy || state.draftDirty || state.draftSaveError;
  elements.dismissModeling.disabled = state.busy || state.modelingBusy;
  elements.undoModeling.hidden = !session.undo_plan || confirmed;
  elements.undoModeling.disabled = state.busy || state.modelingBusy || state.draftDirty;
  elements.reloadDraft.hidden = !state.draftSaveError;
  highlightModelingFields((proposal?.changes || []).map(change => change.field), false);
  if (!proposal && !confirmed) highlightModelingFields(session.suggested_fields || [], true);
}

function highlightModelingFields(fields, applied) {
  if (!applied) {
    document.querySelectorAll(".has-agent-change").forEach(node => node.classList.remove("has-agent-change"));
    document.querySelectorAll(".agent-change-label").forEach(node => node.remove());
  }
  const targets = {
    purpose: ["studyPurpose"], analysis_type: ["draftAnalysisType"], initial_temperature_k: ["draftInitialTemperature"],
    duration_s: ["draftDuration"], time_step_s: ["draftTimeStep"], heat_source_enabled: ["draftEnableHeatSource"],
    global_convection_enabled: ["draftEnableGlobalConvection"], heat_source: ["draftSourcePower", "draftSourceRadius"],
    convection: ["draftAmbient", "draftConvection"], boundaries: ["fixedBoundaryEditor"], surface_conditions: ["surfaceThermalEditor"],
    contacts: ["thermalContactEditor"],
    component_materials: ["componentMaterialList"], material: ["componentMaterialList"], mesh: ["draftMeshSize"], criteria: ["draftCriterionMax"],
  };
  for (const id of new Set(fields.flatMap(field => targets[field] || []))) {
    const node = elements[id];
    if (!node || node.hidden) continue;
    node.classList.add("has-agent-change");
    const label = createElement("small", "agent-change-label", applied ? "Agent 建议，待确认" : "有待应用的 Agent 建议");
    node.insertAdjacentElement("afterend", label);
  }
}

async function sendModelingMessage(event) {
  event.preventDefault();
  if (!elements.modelingForm.reportValidity() || !await flushDraftBeforeNavigation()) return;
  const study = selectedStudy();
  const message = elements.modelingInput.value.trim();
  if (!message || study?.modeling?.proposal || state.modelingBusy) return;
  state.modelingBusy = true;
  renderModeling();
  try {
    const updated = await request(`/v1/studies/${study.study_id}/modeling/messages`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: state.draftBaseRevision, message,
        session_history: state.modelingSessionHistory.get(study.study_id) || [] }),
    });
    rememberModelingStudy(updated);
    if (state.selectedStudyId === study.study_id) {
      elements.modelingInput.value = "";
      elements.modelingDrawer.open = true;
    }
  } catch (error) { showToast(`未生成建议：${error.message}`, true); }
  finally { state.modelingBusy = false; renderModeling(); }
}

async function decideModeling(action) {
  if (!await flushDraftBeforeNavigation() || state.modelingBusy) return;
  const study = selectedStudy();
  state.modelingBusy = true;
  state.modelingDecisionBusy = true;
  const frozen = [...document.querySelectorAll("#structuredInputs input, #structuredInputs select, #structuredInputs button, #componentMaterialList input, #componentMaterialList select, #sourceEditor input, #sourceEditor select, #sourceEditor button, #studyPurpose, #draftMeshSize")]
    .map(node => [node, node.disabled]);
  frozen.forEach(([node]) => { node.disabled = true; });
  renderModeling();
  try {
    const updated = await request(`/v1/studies/${study.study_id}/modeling/decision`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: state.draftBaseRevision, action }),
    });
    rememberModelingStudy(updated, { syncFields: action !== "dismiss", appendHistory: true });
    if (action !== "dismiss") resetMaterialConfirmation();
    elements.confirmInputs.checked = false;
    renderPrimaryAction();
  } catch (error) { showToast(`建议未处理：${error.message}`, true); }
  finally {
    state.modelingBusy = false;
    state.modelingDecisionBusy = false;
    frozen.forEach(([node, disabled]) => { if (node.isConnected) node.disabled = disabled; });
    renderStructuredDraft();
    renderModeling();
  }
}

async function reloadModelingDraft() {
  if (state.draftDirty && !window.confirm("重新载入会放弃当前页面未保存的修改，是否继续？")) return;
  try {
    const updated = await request(`/v1/studies/${state.selectedStudyId}`);
    rememberModelingStudy(updated, { syncFields: true });
  } catch (error) { showToast(`读取草案失败：${error.message}`, true); }
}

async function generateStudyDraft(event) {
  event.preventDefault();
  const workpiece = selectedWorkpiece();
  if (!workpiece?.unit_confirmed) {
    switchTab("geometry");
    showToast("请先确认 STL 尺度", true);
    return;
  }
  if (!elements.studyDraftForm.reportValidity()) return;
  await createStudy(workpiece.workpiece_id, elements.studyPurpose.value.trim());
}

function confirmedOverrides() {
  const study = selectedStudy();
  const plan = study?.plan;
  if (!plan) return null;
  const selectedAssignments = [
    ...elements.componentMaterialList.querySelectorAll(".component-material-row"),
  ].map(materialAssignmentFromRow);
  const axis = elements.draftHeatAxis.value;
  const criterionValue = elements.draftCriterionMax.value;
  const previousCriterion = (plan.criteria || []).find(item => item.metric === "max_temperature");
  const criteria = previousCriterion?.target?.value === Number(criterionValue) && criterionValue !== ""
    ? plan.criteria : (plan.criteria || []).filter(item => item !== previousCriterion);
  if (criterionValue !== "" && criteria !== plan.criteria) criteria.push({
    metric: "max_temperature", operator: "less_or_equal", target: { value: Number(criterionValue), unit: "K" },
  });
  const sourceCollection = activeSourceCollection();
  const sourceOverrides = sourceCollection
    ? { heat_sources: sourceCollection.sources.map(ThermoFlowSources.toPlanSource) }
    : {};
  return {
    ...sourceOverrides,
    analysis_type: elements.draftAnalysisType.value,
    ...(elements.draftAnalysisType.value === "transient_conduction" ? {
      initial_temperature_k: elements.draftInitialTemperature.value === "" ? null : Number(elements.draftInitialTemperature.value),
      duration_s: elements.draftDuration.value === "" ? null : Number(elements.draftDuration.value),
      time_step_s: elements.draftTimeStep.value === "" ? null : Number(elements.draftTimeStep.value),
    } : {}),
    component_materials: selectedAssignments.length ? selectedAssignments : plan.component_materials,
    ...(selectedWorkpiece()?.cad_format === "stl" ? { fixed_boundaries: fixedBoundaryValues() } : {
      heat_axis: axis,
      min_face_temperature_k: Number(elements.draftMinTemperature.value),
      max_face_temperature_k: Number(elements.draftMaxTemperature.value),
    }),
    enable_heat_source: elements.draftEnableHeatSource.checked,
    enable_global_convection: elements.draftEnableGlobalConvection.checked,
    surface_conditions: surfaceConditionValues(),
    contacts: thermalContactValues(),
    ...(elements.draftEnableGlobalConvection.checked ? {
      ambient_temperature_k: Number(elements.draftAmbient.value), convection_coefficient_w_m2_k: Number(elements.draftConvection.value),
    } : {}),
    target_element_size_mm: Number(elements.draftMeshSize.value),
    criteria,
  };
}

async function confirmStudy(studyId) {
  const inputConflict = renderValidationFeedback().find(issue => issue.severity === 'error');
  if (state.draftDirty && inputConflict?.fields?.length) {
    locateValidationFields(inputConflict.fields);
    return;
  }
  if (!await flushDraftBeforeNavigation()) return;
  if (selectedStudy()?.study_id !== studyId) return;
  const revision = selectedStudy().draft_revision;
  if (!await loadSelectedValidation()) return;
  if (selectedStudy()?.study_id !== studyId || selectedStudy()?.draft_revision !== revision || state.draftDirty) return;
  const issues = renderValidationFeedback();
  const conflict = issues.find(issue => issue.severity === 'error');
  if (conflict) {
    locateValidationFields(conflict.fields || []);
    showToast('请先修改“参数检查”列出的冲突，再确认仿真。', true);
    return;
  }
  if (state.modelingBusy || selectedStudy()?.modeling?.proposal) {
    elements.modelingDrawer.open = true;
    showToast("请先处理建模建议，再确认输入", true);
    return;
  }
  if (!elements.structuredInputs.reportValidity()) {
    switchTab("scenario");
    return;
  }
  if (!elements.confirmMaterials.checked) {
    switchTab("materials");
    elements.confirmMaterials.focus();
    showToast("请先逐组件核对并确认材料及热物性", true);
    return;
  }
  const invalidMaterialInput = [
    ...elements.componentMaterialList.querySelectorAll("input[data-material-property]"),
  ].find((input) => !input.reportValidity());
  if (invalidMaterialInput) {
    switchTab("materials");
    invalidMaterialInput.focus();
    return;
  }
  setBusy(true);
  try {
    await request(`/v1/studies/${studyId}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: state.draftBaseRevision,
        purpose: elements.studyPurpose.value.trim(),
        overrides: confirmedOverrides(),
        materials_confirmed: true,
      }),
    });
    state.activeTab = "mesh";
    await loadWorkspace();
    await submitComputation(studyId, "apply_and_solve");
  } catch (error) {
    state.validationFailure = {studyId, message: `参数确认失败：${error.message}`};
    renderValidationFeedback();
    showToast(`参数确认失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

async function generateMesh(studyId) {
  return submitComputation(studyId, "mesh");
}

async function confirmMeshReview(studyId) {
  setBusy(true);
  try {
    await request(`/v1/studies/${studyId}/mesh/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept_warnings: true }),
    });
    state.activeTab = "mesh";
    state.visualizationMode = "mesh";
    await loadWorkspace();
    await runStudy(studyId);
  } catch (error) {
    await loadWorkspace();
    showToast(`网格风险确认失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

async function runStudy(studyId) {
  return submitComputation(studyId, "solve");
}

const TASK_TERMINAL_STATES = new Set(["succeeded", "needs_review", "cancelled", "failed", "timed_out", "interrupted"]);

function activeStudyTask() {
  return state.tasks.find(task => task.study_id === state.selectedStudyId && !TASK_TERMINAL_STATES.has(task.status));
}

function renderComputationStatus() {
  const task = state.tasks.find(item => item.study_id === state.selectedStudyId);
  elements.computationStatus.hidden = !task;
  if (!task) return;
  const labels = { queued: "排队中", running: "计算中", cancelling: "正在取消", succeeded: "已完成",
    needs_review: "待检查网格", cancelled: "已取消", failed: "未完成", timed_out: "已超时", interrupted: "计算中断" };
  elements.computationTitle.textContent = task.operation === "mesh" ? "网格生成" : "热场求解";
  elements.computationState.textContent = labels[task.status];
  elements.computationMessage.textContent = state.taskConnectionLost && !TASK_TERMINAL_STATES.has(task.status)
    ? "暂时无法获取进度，计算状态正在重新连接" : task.message;
  if (task.progress == null) elements.computationProgress.removeAttribute("value");
  else elements.computationProgress.value = task.progress;
  elements.computationProgress.hidden = TASK_TERMINAL_STATES.has(task.status);
  const start = new Date(task.started_at || task.created_at).getTime();
  const end = task.finished_at ? new Date(task.finished_at).getTime() : Date.now();
  const elapsed = Math.max(0, Math.floor((end - start) / 1000));
  elements.computationElapsed.textContent = `${task.started_at ? "计算耗时" : "已等待"} ${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`;
  elements.cancelComputation.hidden = TASK_TERMINAL_STATES.has(task.status);
  elements.cancelComputation.disabled = !task.cancellable;
  elements.cancelComputation.dataset.taskId = task.task_id;
}

function scheduleTaskPoll() {
  clearTimeout(state.taskPollTimer);
  const assistantTrackingTask = state.assistantSession?.task_id
    && !["completed", "needs_mesh_review", "failed"].includes(state.assistantSession.status);
  if (state.tasks.some(task => !TASK_TERMINAL_STATES.has(task.status)) || assistantTrackingTask) {
    state.taskPollTimer = setTimeout(pollComputationTasks, 900);
  }
}

async function pollComputationTasks() {
  if (state.taskPollRunning) return;
  state.taskPollRunning = true;
  try {
    const previous = state.tasks;
    state.tasks = await request("/v1/tasks");
    state.taskConnectionLost = false;
    const completed = state.tasks.filter(task => TASK_TERMINAL_STATES.has(task.status)
      && previous.some(old => old.task_id === task.task_id && !TASK_TERMINAL_STATES.has(old.status)));
    await refreshAssistantTaskStatus();
    const selectedCompletion = completed.find(task => task.study_id === state.selectedStudyId);
    if (selectedCompletion?.status === "succeeded") {
      state.activeTab = selectedCompletion.operation === "mesh" ? "mesh" : "result";
      state.visualizationMode = selectedCompletion.operation === "mesh" ? "mesh" : "thermal";
    } else if (selectedCompletion?.status === "needs_review") {
      state.activeTab = "mesh";
      state.visualizationMode = "mesh";
    }
    if (selectedCompletion) {
      await loadWorkspace();
      showToast(selectedCompletion.message, selectedCompletion.status !== "succeeded");
    } else if (completed.length) {
      state.studies = await request("/v1/studies");
      renderStudies();
      renderComputationStatus();
      renderPrimaryAction();
    } else {
      renderComputationStatus();
      renderPrimaryAction();
    }
  } catch {
    state.taskConnectionLost = true;
    renderComputationStatus();
  } finally {
    state.taskPollRunning = false;
    scheduleTaskPoll();
  }
}

async function submitComputation(studyId, operation) {
  setBusy(true);
  try {
    const task = await request(`/v1/studies/${studyId}/tasks`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ operation }),
    });
    state.tasks = [task, ...state.tasks.filter(item => item.task_id !== task.task_id)];
    state.activeTab = operation === "mesh" ? "mesh" : "solve";
    await loadWorkspace();
    showToast(operation === "mesh" ? "网格任务已提交" : "求解任务已提交");
    return task;
  } catch (error) {
    await loadWorkspace();
    showToast(`任务提交失败：${error.message}`, true);
    return null;
  } finally {
    setBusy(false);
    scheduleTaskPoll();
  }
}

async function cancelComputation() {
  const taskId = elements.cancelComputation.dataset.taskId;
  if (!taskId) return;
  elements.cancelComputation.disabled = true;
  try {
    const task = await request(`/v1/tasks/${taskId}/cancel`, { method: "POST" });
    state.tasks = state.tasks.map(item => item.task_id === taskId ? task : item);
    renderComputationStatus();
    scheduleTaskPoll();
  } catch (error) {
    showToast(`取消失败：${error.message}`, true);
    renderComputationStatus();
  }
}

function setBusy(busy) {
  state.busy = busy;
  elements.submitWorkpieceButton.disabled = busy;
  elements.submitWorkpieceButton.textContent = busy ? "正在检查…" : "导入并检查";
  elements.materialsCreateDraftButton.disabled = busy || !selectedWorkpiece()?.unit_confirmed;
  elements.generateDraftButton.disabled = busy;
  elements.generateDraftButton.textContent = busy ? "正在生成…" : "生成仿真草案";
  elements.applySourceButton.disabled = busy;
  elements.applySourceButton.textContent = busy
    ? "正在应用…"
    : selectedStudy()?.confirmation?.status === "confirmed" ? "应用并求解" : "应用到草案";
  elements.runAgentButton.disabled = busy || selectedStudy()?.status !== "succeeded";
  elements.runAgentButton.textContent = busy ? "正在检查与优化…" : "检查目标并自动优化";
  renderComparisonTool();
  renderPrimaryAction();
}

function syncVisualizationModeButtons() {
  const buttons = {
    model: elements.modelViewButton,
    mesh: elements.meshViewButton,
    diffusion: elements.diffusionViewButton,
    thermal: elements.thermalViewButton,
    flux: elements.heatFluxViewButton,
    contour: elements.contourViewButton,
    slice: elements.sliceViewButton,
  };
  Object.entries(buttons).forEach(([mode, button]) => {
    button.setAttribute("aria-pressed", String(state.visualizationMode === mode));
  });
}

function setVisualizationMode(mode) {
  if (state.comparison && mode !== "thermal") return;
  if (mode === "diffusion" && !state.result?.time_steps?.length) return;
  state.visualizationMode = mode;
  if (mode !== "model") state.selectionMode = "component";
  persistWorkspaceState();
  syncVisualizationModeButtons();
  elements.sliceControls.hidden = mode !== "slice";
  elements.probeReadout.hidden = !isTemperatureVisualization() || !state.probe;
  if (["model", "mesh"].includes(mode)) {
    if (state.heatAnimationFrame) cancelAnimationFrame(state.heatAnimationFrame);
    state.heatAnimationFrame = null;
    drawWorkpiece();
    return;
  }
  startHeatAnimation();
}

function startHeatAnimation() {
  if (state.heatAnimationFrame) cancelAnimationFrame(state.heatAnimationFrame);
  state.heatAnimationFrame = null;
  drawWorkpiece();
}

function isTemperatureVisualization() {
  return ["diffusion", "thermal", "contour", "slice"].includes(state.visualizationMode);
}

function sourceDraftFromPlan(study) {
  if (!study?.plan) return null;
  const bbox = selectedWorkpiece()?.geometry?.summary?.bbox
    || [0, 0, 0, ...Object.values(selectedWorkpiece()?.dimensions_mm || { x: 1, y: 1, z: 1 })];
  const collection = ThermoFlowSources.createCollection(study.plan, bbox);
  collection.studyId = study.study_id;
  const mappings = state.result?.study_id === study.study_id
    ? state.result.heat_source_mappings?.length
      ? state.result.heat_source_mappings
      : state.result.heat_source_mapping ? [state.result.heat_source_mapping] : []
    : [];
  collection.sources.forEach((source, index) => {
    const mapping = mappings.find(item => item.source_id && item.source_id === source.sourceId)
      || mappings[index];
    if (mapping?.resolved_center_mm) {
      source.center = Object.fromEntries(["x", "y", "z"].map((axis, axisIndex) => [
        axis, mapping.resolved_center_mm[axisIndex],
      ]));
    }
    if (mapping?.resolved_end_mm) {
      source.end = Object.fromEntries(["x", "y", "z"].map((axis, axisIndex) => [
        axis, mapping.resolved_end_mm[axisIndex],
      ]));
    }
  });
  return collection;
}

function activeSourceCollection(create = false) {
  const study = selectedStudy();
  if (!study?.plan) return null;
  const configured = ThermoFlowSources.sourcesFromPlan(study.plan).length && heatSourceEnabled(study.plan);
  const existing = state.sourceDraft?.studyId === study.study_id;
  if (!configured && !existing && !create) return null;
  if (!state.sourceDraft || state.sourceDraft.studyId !== study.study_id) {
    state.sourceDraft = sourceDraftFromPlan(study);
  }
  return state.sourceDraft;
}

function activeSourceDraft(create = false) {
  const collection = activeSourceCollection(create);
  if (!collection?.sources.length) return null;
  collection.activeIndex = Math.max(0, Math.min(collection.activeIndex, collection.sources.length - 1));
  return collection.sources[collection.activeIndex];
}

function heatSourceEnabled(plan) {
  if (!ThermoFlowSources.sourcesFromPlan(plan).length) return false;
  if (plan === selectedStudy()?.plan && state.draftSyncedFor === selectedStudy()?.study_id
    && selectedStudy()?.confirmation?.status !== "confirmed") return elements.draftEnableHeatSource.checked;
  return plan.heat_source_enabled !== false;
}

function displayedHeatSources(plan) {
  const collection = activeSourceCollection();
  if (collection) return collection.sources.map(ThermoFlowSources.toPlanSource);
  return heatSourceEnabled(plan) ? ThermoFlowSources.sourcesFromPlan(plan) : [];
}

function displayedHeatSource(plan) {
  const sources = displayedHeatSources(plan);
  const activeIndex = activeSourceCollection()?.activeIndex || 0;
  return sources[activeIndex] || sources[0] || null;
}

function solvedHeatSources(plan, result) {
  if (!plan || plan.heat_source_enabled === false) return [];
  const sources = ThermoFlowSources.sourcesFromPlan(plan);
  const mappings = result?.heat_source_mappings?.length
    ? result.heat_source_mappings
    : result?.heat_source_mapping ? [result.heat_source_mapping] : [];
  return sources.map((source, sourceIndex) => {
    const mapping = mappings.find(item => item.source_id && item.source_id === source.source_id)
      || mappings[sourceIndex];
    return {
      ...source,
      center_mm: mapping?.resolved_center_mm
        ? Object.fromEntries(["x", "y", "z"].map((axis, index) => [axis, mapping.resolved_center_mm[index]]))
        : { ...source.center_mm },
      end_mm: mapping?.resolved_end_mm
        ? Object.fromEntries(["x", "y", "z"].map((axis, index) => [axis, mapping.resolved_end_mm[index]]))
        : source.end_mm ? { ...source.end_mm } : null,
    };
  });
}

function solvedHeatSource(plan, result) {
  const sources = solvedHeatSources(plan, result);
  const activeIndex = activeSourceCollection()?.activeIndex || 0;
  return sources[activeIndex] || sources[0] || null;
}

function syncSourceShapeFields() {
  const draft = activeSourceDraft();
  if (!draft) return;
  const line = draft.shape === "line";
  const surface = draft.shape === "surface";
  const volume = draft.shape === "volume";
  elements.canvasSourceDepthField.hidden = draft.placement !== "embedded";
  elements.canvasSourceRadiusField.hidden = surface || volume;
  elements.canvasSourceRadiusLabel.innerHTML = line ? "线半径 <i>mm</i>" : "点半径 <i>mm</i>";
  [elements.canvasSourceEndXField, elements.canvasSourceEndYField, elements.canvasSourceEndZField]
    .forEach((field) => { field.hidden = !line; });
  [
    elements.canvasSourceSurfaceAxisField,
    elements.canvasSourceWidthField,
    elements.canvasSourceHeightField,
    elements.canvasSourceThicknessField,
  ].forEach((field) => { field.hidden = !surface; });
  [elements.canvasSourceEndX, elements.canvasSourceEndY, elements.canvasSourceEndZ]
    .forEach((input) => { input.required = line; });
  [elements.canvasSourceWidth, elements.canvasSourceHeight, elements.canvasSourceThickness]
    .forEach((input) => { input.required = surface; });
  [
    elements.canvasSourceVolumeWidthField,
    elements.canvasSourceVolumeHeightField,
    elements.canvasSourceVolumeDepthField,
  ].forEach((field) => { field.hidden = !volume; });
  [elements.canvasSourceVolumeWidth, elements.canvasSourceVolumeHeight, elements.canvasSourceVolumeDepth]
    .forEach((input) => { input.required = volume; });
}

function syncSourceList() {
  const collection = activeSourceCollection();
  if (!collection) return;
  const shapeNames = { point: "点", line: "线", surface: "面", volume: "体" };
  elements.sourceList.replaceChildren(...collection.sources.map((source, index) => {
    const row = document.createElement("div");
    row.className = "source-list-row";
    const selectButton = document.createElement("button");
    selectButton.type = "button";
    selectButton.className = `source-list-item${index === collection.activeIndex ? " is-active" : ""}`;
    selectButton.dataset.sourceIndex = String(index);
    selectButton.setAttribute("role", "option");
    selectButton.setAttribute("aria-selected", String(index === collection.activeIndex));
    selectButton.setAttribute("aria-label", `选择热源：${source.name}`);
    selectButton.textContent = source.name;
    const kind = document.createElement("small");
    kind.textContent = `${shapeNames[source.shape] || "点"} · ${formatNumber(source.power)} W`;
    selectButton.append(kind);
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "source-list-delete";
    deleteButton.dataset.sourceDelete = String(index);
    deleteButton.disabled = collection.sources.length <= 1 || state.modelingDecisionBusy;
    deleteButton.title = collection.sources.length <= 1 ? "至少保留一个热源" : `删除热源：${source.name}`;
    deleteButton.setAttribute("aria-label", deleteButton.title);
    deleteButton.textContent = "×";
    row.append(selectButton, deleteButton);
    return row;
  }));
  elements.deleteHeatSource.disabled = collection.sources.length <= 1 || state.modelingDecisionBusy;
  elements.deleteHeatSource.textContent = collection.sources.length <= 1
    ? "至少保留一个" : `删除当前：${collection.sources[collection.activeIndex].name}`;
  elements.addHeatSource.disabled = collection.sources.length >= 16 || state.modelingDecisionBusy;
}

function syncSourceEditor() {
  const draft = activeSourceDraft();
  const collection = activeSourceCollection();
  if (!draft || !collection) return;
  elements.applySourceButton.textContent = selectedStudy()?.confirmation?.status === "confirmed"
    ? "应用并求解" : "应用到草案";
  syncSourceList();
  elements.canvasSourceName.value = draft.name;
  elements.canvasSourceShape.value = draft.shape;
  elements.canvasSourcePlacement.value = draft.placement;
  elements.canvasSourceDepth.value = String(draft.embeddingDepth);
  elements.canvasSourcePower.value = String(draft.power);
  elements.canvasSourceRadius.value = String(draft.radius);
  elements.canvasSourceX.value = String(Number(draft.center.x.toFixed(6)));
  elements.canvasSourceY.value = String(Number(draft.center.y.toFixed(6)));
  elements.canvasSourceZ.value = String(Number(draft.center.z.toFixed(6)));
  elements.canvasSourceEndX.value = String(Number(draft.end.x.toFixed(6)));
  elements.canvasSourceEndY.value = String(Number(draft.end.y.toFixed(6)));
  elements.canvasSourceEndZ.value = String(Number(draft.end.z.toFixed(6)));
  elements.canvasSourceSurfaceAxis.value = draft.surfaceAxis;
  elements.canvasSourceWidth.value = String(draft.surfaceWidth);
  elements.canvasSourceHeight.value = String(draft.surfaceHeight);
  elements.canvasSourceThickness.value = String(draft.surfaceThickness);
  elements.canvasSourceVolumeWidth.value = String(draft.volumeWidth);
  elements.canvasSourceVolumeHeight.value = String(draft.volumeHeight);
  elements.canvasSourceVolumeDepth.value = String(draft.volumeDepth);
  elements.canvasAmbientTemperature.value = String(collection.ambientTemperature);
  elements.canvasConvectionCoefficient.value = String(collection.convectionCoefficient);
  const transient = selectedStudy()?.plan?.analysis_type === "transient_conduction";
  elements.canvasDurationField.hidden = !transient;
  elements.canvasTimeStepField.hidden = !transient;
  elements.canvasDuration.disabled = !transient;
  elements.canvasTimeStep.disabled = !transient;
  elements.canvasDuration.required = transient;
  elements.canvasTimeStep.required = transient;
  elements.canvasDuration.value = String(collection.timeWindow.duration);
  elements.canvasTimeStep.value = String(collection.timeWindow.timeStep);
  syncSourceShapeFields();
  updateSourcePositionReadout();
}

function updateSourcePositionReadout() {
  const draft = activeSourceDraft();
  if (!draft) return;
  const shapeLabel = { point: "点", line: "线", surface: "面", volume: "体" }[draft.shape];
  const placementLabel = draft.placement === "embedded" ? "嵌入" : "表面";
  const count = activeSourceCollection()?.sources.length || 1;
  elements.sourcePositionReadout.textContent = `${count} 个 · ${shapeLabel} · ${placementLabel} · (${formatNumber(draft.center.x)}, ${formatNumber(draft.center.y)}, ${formatNumber(draft.center.z)}) mm`;
}

function positionSourceEditor() {
  // The editor is docked in the workpiece sidebar and no longer tracks the source marker.
}

function openSourceEditor() {
  if (!activeSourceDraft(true)) return;
  if (selectedStudy()?.confirmation?.status !== "confirmed") {
    elements.draftEnableHeatSource.checked = true;
  }
  syncSourceEditor();
  elements.sourceEditorSlot.hidden = false;
  elements.sourceEditor.hidden = false;
  elements.sourceEditButton.classList.add("is-active");
  positionSourceEditor();
  if (window.matchMedia("(max-width: 680px)").matches) {
    elements.sourceEditorSlot.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function closeSourceEditor() {
  elements.sourceEditor.hidden = true;
  elements.sourceEditorSlot.hidden = true;
  elements.sourceEditButton.classList.remove("is-active");
}

function discardSourceDraft() {
  state.sourceDraft = null;
  state.sourcePreviewDirty = false;
  state.sourceDrag = null;
  state.sourceMarker = null;
  closeSourceEditor();
}

function resetSourceEditor() {
  state.sourceDraft = sourceDraftFromPlan(selectedStudy());
  state.sourcePreviewDirty = false;
  syncSourceEditor();
  startHeatAnimation();
}

function selectHeatSource(index, { readInputs = true, openEditor = false } = {}) {
  const collection = activeSourceCollection();
  if (!collection || index < 0 || index >= collection.sources.length) return;
  if (readInputs && !elements.sourceEditor.hidden) {
    updateSourceDraftFromInputs(undefined, { markDirty: false });
  }
  if (!ThermoFlowSources.selectSource(collection, index)) return;
  if (openEditor) openSourceEditor();
  syncSourceEditor();
  drawWorkpiece();
}

function addHeatSource() {
  const collection = activeSourceCollection(true);
  const workpiece = selectedWorkpiece();
  if (!collection || !workpiece) return;
  updateSourceDraftFromInputs();
  const bbox = workpiece.geometry?.summary?.bbox
    || [0, 0, 0, ...Object.values(workpiece.dimensions_mm || { x: 1, y: 1, z: 1 })];
  if (!ThermoFlowSources.addSource(collection, bbox, selectedStudy()?.plan)) {
    showToast("一个研究最多支持 16 个热源", true);
    return;
  }
  if (selectedStudy()?.confirmation?.status !== "confirmed") {
    elements.draftEnableHeatSource.checked = true;
    queueDraftSave();
  }
  state.sourcePreviewDirty = true;
  syncSourceEditor();
  drawWorkpiece();
}

function deleteHeatSource(index = null) {
  const collection = activeSourceCollection();
  if (!collection) return;
  if (!elements.sourceEditor.hidden) updateSourceDraftFromInputs();
  const targetIndex = Number.isInteger(index) ? index : collection.activeIndex;
  const sourceName = collection.sources[targetIndex]?.name || "当前热源";
  if (!ThermoFlowSources.removeSource(collection, targetIndex)) {
    showToast("至少保留一个热源；如不需要热源，请关闭“启用局部热源”", true);
    return;
  }
  if (selectedStudy()?.confirmation?.status !== "confirmed") queueDraftSave();
  state.sourcePreviewDirty = true;
  syncSourceEditor();
  drawWorkpiece();
  showToast(`已删除热源：${sourceName}`);
}

function updateSourceDraftFromInputs(event, { markDirty = true } = {}) {
  const draft = activeSourceDraft();
  const collection = activeSourceCollection();
  if (!draft || !collection) return;
  const numericValue = (input, fallback) => {
    const value = Number(input.value);
    return input.value !== "" && Number.isFinite(value) ? value : fallback;
  };
  draft.power = numericValue(elements.canvasSourcePower, draft.power);
  draft.radius = numericValue(elements.canvasSourceRadius, draft.radius);
  draft.name = elements.canvasSourceName.value.trim() || draft.name;
  draft.shape = elements.canvasSourceShape.value;
  draft.placement = elements.canvasSourcePlacement.value;
  draft.embeddingDepth = numericValue(elements.canvasSourceDepth, draft.embeddingDepth);
  draft.center.x = numericValue(elements.canvasSourceX, draft.center.x);
  draft.center.y = numericValue(elements.canvasSourceY, draft.center.y);
  draft.center.z = numericValue(elements.canvasSourceZ, draft.center.z);
  draft.end.x = numericValue(elements.canvasSourceEndX, draft.end.x);
  draft.end.y = numericValue(elements.canvasSourceEndY, draft.end.y);
  draft.end.z = numericValue(elements.canvasSourceEndZ, draft.end.z);
  draft.surfaceAxis = elements.canvasSourceSurfaceAxis.value;
  draft.surfaceWidth = numericValue(elements.canvasSourceWidth, draft.surfaceWidth);
  draft.surfaceHeight = numericValue(elements.canvasSourceHeight, draft.surfaceHeight);
  draft.surfaceThickness = numericValue(elements.canvasSourceThickness, draft.surfaceThickness);
  draft.volumeWidth = numericValue(elements.canvasSourceVolumeWidth, draft.volumeWidth);
  draft.volumeHeight = numericValue(elements.canvasSourceVolumeHeight, draft.volumeHeight);
  draft.volumeDepth = numericValue(elements.canvasSourceVolumeDepth, draft.volumeDepth);
  collection.ambientTemperature = numericValue(
    elements.canvasAmbientTemperature,
    collection.ambientTemperature,
  );
  collection.convectionCoefficient = numericValue(
    elements.canvasConvectionCoefficient,
    collection.convectionCoefficient,
  );
  collection.timeWindow.duration = numericValue(elements.canvasDuration, collection.timeWindow.duration);
  collection.timeWindow.timeStep = numericValue(elements.canvasTimeStep, collection.timeWindow.timeStep);
  if (selectedStudy()?.confirmation?.status !== "confirmed") {
    elements.draftSourcePower.value = elements.canvasSourcePower.value;
    elements.draftSourceRadius.value = elements.canvasSourceRadius.value;
    elements.draftAmbient.value = elements.canvasAmbientTemperature.value;
    elements.draftConvection.value = elements.canvasConvectionCoefficient.value;
    if (selectedStudy()?.plan?.analysis_type === "transient_conduction") {
      elements.draftDuration.value = elements.canvasDuration.value;
      elements.draftTimeStep.value = elements.canvasTimeStep.value;
    }
    queueDraftSave();
  }
  if (markDirty
    && ![elements.canvasAmbientTemperature, elements.canvasConvectionCoefficient].includes(event?.target)) {
    state.sourcePreviewDirty = true;
  }
  syncSourceShapeFields();
  syncSourceList();
  updateSourcePositionReadout();
  startHeatAnimation();
}

function simulationOverridesFromDraft(plan, draft) {
  const collection = activeSourceCollection() || {
    sources: [draft],
    ambientTemperature: draft.ambientTemperature,
    convectionCoefficient: draft.convectionCoefficient,
    timeWindow: ThermoFlowSources.longTransientWindow(plan),
  };
  const timeOverrides = plan.analysis_type === "transient_conduction"
    ? ThermoFlowSources.toOverrides(collection, {
      initialTemperature: plan.initial_temperature_k,
      duration: collection.timeWindow.duration,
      timeStep: collection.timeWindow.timeStep,
    })
    : ThermoFlowSources.toOverrides(collection);
  return {
    material_name: plan.material.name,
    thermal_conductivity_w_m_k: plan.material.thermal_conductivity_w_m_k,
    density_kg_m3: plan.material.density_kg_m3,
    specific_heat_j_kg_k: plan.material.specific_heat_j_kg_k,
    fixed_boundaries: plan.boundaries,
    surface_conditions: plan.surface_conditions || [],
    contacts: plan.contacts || [],
    enable_heat_source: true,
    enable_global_convection: plan.global_convection_enabled,
    ...timeOverrides,
    ambient_temperature_k: collection.ambientTemperature,
    convection_coefficient_w_m2_k: collection.convectionCoefficient,
    target_element_size_mm: plan.mesh.target_element_size_mm,
    max_axis_intervals: plan.mesh.max_axis_intervals,
    relative_tolerance: plan.solver.relative_tolerance,
    max_iterations: plan.solver.max_iterations,
    component_materials: plan.component_materials || [],
    criteria: plan.criteria || [],
  };
}

async function applySourceChanges(event) {
  event.preventDefault();
  if (!elements.sourceEditor.reportValidity()) return;
  updateSourceDraftFromInputs();
  if (selectedStudy()?.confirmation?.status !== "confirmed") {
    if (!await flushDraftBeforeNavigation()) return;
    closeSourceEditor();
    switchTab("scenario");
    showToast("热源已更新到草案，请检查并确认全部输入后求解");
    return;
  }
  const study = selectedStudy();
  const draft = activeSourceDraft();
  if (!study?.plan || !draft) return;
  setBusy(true);
  try {
    const nextStudy = await request(`/v1/studies/${study.study_id}/copy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        overrides: simulationOverridesFromDraft(study.plan, draft),
        purpose: study.purpose || study.plan.purpose || "",
      }),
    });
    if (nextStudy.status !== "needs_input") {
      throw new Error(nextStudy.failure || "热源参数未通过校验");
    }
    await request(`/v1/studies/${nextStudy.study_id}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        overrides: simulationOverridesFromDraft(study.plan, draft),
        purpose: study.purpose || study.plan.purpose || "",
        materials_confirmed: true,
      }),
    });
    state.selectedStudyId = nextStudy.study_id;
    state.activeTab = "mesh";
    discardSourceDraft();
    const task = await submitComputation(nextStudy.study_id, "apply_and_solve");
    if (task) showToast("参数已应用到新研究，后台计算已提交");
  } catch (error) {
    showToast(`热源求解失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

function openWorkpieceDialog() {
  elements.workpieceForm.reset();
  renderUploadMaterialOptions();
  elements.fileLabel.textContent = "选择 STL 文件";
  const project = selectedProject();
  elements.currentProjectOption.disabled = !project;
  elements.currentProjectOption.textContent = project
    ? `加入当前项目：${project.name}`
    : "加入当前项目";
  elements.importProjectMode.value = "new";
  updateUploadSourceFields();
  setParameterMode("auto");
  elements.workpieceDialog.showModal();
  elements.cadFile.focus();
}

function updateUploadSourceFields() {
  const shape = elements.heatSourceShape.value;
  document.querySelectorAll("[data-upload-hide-for]").forEach((field) => {
    field.hidden = field.dataset.uploadHideFor.split(/\s+/).includes(shape);
  });
  document.querySelectorAll("[data-upload-source]").forEach((field) => {
    const visible = field.dataset.uploadSource === shape;
    field.hidden = !visible;
    field.querySelectorAll("input, select").forEach((input) => { input.required = visible; });
  });
  document.querySelectorAll("[data-upload-placement]").forEach((field) => {
    field.hidden = field.dataset.uploadPlacement !== elements.heatSourcePlacement.value;
  });
}

function closeWorkpieceDialog() {
  elements.workpieceDialog.close();
}

function setParameterMode(mode) {
  state.parameterMode = mode;
  const automatic = mode === "auto";
  elements.autoModeButton.classList.toggle("is-active", automatic);
  elements.customModeButton.classList.toggle("is-active", !automatic);
  elements.autoModeButton.setAttribute("aria-selected", String(automatic));
  elements.customModeButton.setAttribute("aria-selected", String(!automatic));
  elements.customParameterFields.hidden = automatic;
}

function collectUploadOverrides() {
  if (state.parameterMode !== "custom") return null;
  const overrides = {};
  const numberFields = {
    thermal_conductivity_w_m_k: "thermalConductivity",
    density_kg_m3: "materialDensity",
    specific_heat_j_kg_k: "specificHeat",
    heat_source_embedding_depth_mm: "heatSourceDepth",
    heat_source_power_w: "heatSourcePower",
    heat_source_radius_mm: "heatSourceRadius",
    heat_source_x_mm: "heatSourceX",
    heat_source_y_mm: "heatSourceY",
    heat_source_z_mm: "heatSourceZ",
    heat_source_end_x_mm: "heatSourceEndX",
    heat_source_end_y_mm: "heatSourceEndY",
    heat_source_end_z_mm: "heatSourceEndZ",
    heat_source_surface_width_mm: "heatSourceWidth",
    heat_source_surface_height_mm: "heatSourceHeight",
    heat_source_surface_thickness_mm: "heatSourceThickness",
    heat_source_volume_width_mm: "heatSourceVolumeWidth",
    heat_source_volume_height_mm: "heatSourceVolumeHeight",
    heat_source_volume_depth_mm: "heatSourceVolumeDepth",
    min_face_temperature_k: "minFaceTemperature",
    max_face_temperature_k: "maxFaceTemperature",
    ambient_temperature_k: "ambientTemperature",
    convection_coefficient_w_m2_k: "convectionCoefficient",
    target_element_size_mm: "targetElementSize",
    max_axis_intervals: "maxAxisIntervals",
    relative_tolerance: "relativeTolerance",
    max_iterations: "maxIterations",
  };
  const valueOf = (id) => document.getElementById(id)?.value?.trim() || "";
  const numberValue = (id) => {
    const raw = valueOf(id);
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  };
  const materialName = valueOf("materialName");
  if (materialName) overrides.material_name = materialName;
  Object.entries(numberFields).forEach(([key, id]) => {
    const value = numberValue(id);
    if (value !== null) overrides[key] = value;
  });
  const selectFields = {
    heat_axis: "heatAxis",
    heat_source_shape: "heatSourceShape",
    heat_source_placement: "heatSourcePlacement",
    heat_source_surface_axis: "heatSourceSurfaceAxis",
  };
  Object.entries(selectFields).forEach(([key, id]) => {
    const value = valueOf(id);
    if (value && (key !== "heat_source_surface_axis" || valueOf("heatSourceShape") === "surface")) {
      overrides[key] = value;
    }
  });
  const hasSourceValues = [
    "heat_source_shape", "heat_source_placement", "heat_source_power_w", "heat_source_radius_mm",
    "heat_source_x_mm", "heat_source_y_mm", "heat_source_z_mm", "heat_source_end_x_mm",
    "heat_source_end_y_mm", "heat_source_end_z_mm", "heat_source_surface_width_mm",
    "heat_source_surface_height_mm", "heat_source_surface_thickness_mm", "heat_source_volume_width_mm",
    "heat_source_volume_height_mm", "heat_source_volume_depth_mm",
  ].some((key) => Object.hasOwn(overrides, key));
  if (hasSourceValues) overrides.enable_heat_source = true;
  return Object.keys(overrides).length ? overrides : null;
}

async function submitWorkpiece(event) {
  event.preventDefault();
  setBusy(true);
  try {
    const file = elements.cadFile.files[0];
    if (!file) {
      showToast("请选择 STL 文件", true);
      return;
    }
    if (!file.name.toLocaleLowerCase().endsWith(".stl")) {
      showToast("只接受 .stl 文件", true);
      return;
    }
    if (!elements.workpieceForm.reportValidity()) return;
    const uploadOverrides = collectUploadOverrides();
    const payload = new FormData();
    payload.append("file", file);
    if (elements.importProjectMode.value === "current" && state.selectedProjectId) {
      payload.append("project_id", state.selectedProjectId);
    }
    const workpiece = await request("/v1/workpieces/files", { method: "POST", body: payload });
    state.pendingUploadOverrides = uploadOverrides
      ? { workpieceId: workpiece.workpiece_id, overrides: uploadOverrides }
      : null;
    state.selectedProjectId = workpiece.project_id;
    state.selectedWorkpieceId = workpiece.workpiece_id;
    state.selectedStudyId = null;
    resetStlView(false);
    state.activeTab = "geometry";
    state.draftSyncedFor = null;
    closeWorkpieceDialog();
    await loadWorkspace();
    if (state.assistantSession && !state.assistantSession.workpiece_id) {
      try {
        state.assistantSession = await request(`/v1/assistant-sessions/${state.assistantSession.session_id}/workpiece`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expected_revision: state.assistantSession.revision, workpiece_id: workpiece.workpiece_id }),
        });
        persistWorkspaceState();
        renderAssistant();
      } catch (error) { showToast(`Agent 绑定工件失败：${error.message}`, true); }
    }
    showToast("STL 已导入，请确认单位与实际尺寸");
  } catch (error) {
    showToast(`STL 导入失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

function canvasStudyContext() {
  const baselineStudy = selectedStudy();
  const baselineWorkpiece = selectedWorkpiece();
  const baselineResult = displayedTimeResult(state.result);
  const comparison = state.comparison;
  if (!comparison || state.comparisonMode === "baseline") {
    return {
      study: baselineStudy,
      workpiece: baselineWorkpiece,
      result: baselineResult,
      difference: false,
    };
  }
  const difference = comparison.differences[0];
  const candidateStudy = state.studies.find(
    (study) => study.study_id === difference?.candidate_study_id,
  );
  const candidateWorkpiece = state.workpieces.find(
    (workpiece) => workpiece.workpiece_id === candidateStudy?.workpiece_id,
  );
  if (state.comparisonMode === "candidate") {
    return {
      study: candidateStudy,
      workpiece: candidateWorkpiece,
      result: state.comparisonCandidateResult,
      difference: false,
    };
  }
  const field = difference?.field;
  if (!field || !baselineResult) {
    return {
      study: baselineStudy,
      workpiece: baselineWorkpiece,
      result: baselineResult,
      difference: false,
    };
  }
  return {
    study: baselineStudy,
    workpiece: baselineWorkpiece,
    result: {
      ...baselineResult,
      temperature_min_k: field.minimum_delta.value,
      temperature_max_k: field.maximum_delta.value,
      temperature_field_preview: {
        sampling: "surface_and_volume_voxel_centers",
        pitch_mm: field.pitch.value,
        total_surface_cells: field.surface_samples.length,
        samples: field.surface_samples,
        total_volume_cells: field.volume_samples.length || null,
        volume_samples: field.volume_samples,
      },
      heat_flux_field_preview: null,
    },
    difference: true,
  };
}

function drawWorkpiece() {
  syncSurfaceSelection();
  if (!engineeringViewport) return;
  const display = canvasStudyContext();
  const result = display.result;
  const bbox = display.workpiece?.geometry?.summary?.bbox || [0, 0, 0, 1, 1, 1];
  const axis = "xyz".indexOf(state.sliceAxis);
  const section = bbox[axis] + (bbox[axis + 3] - bbox[axis]) * state.sliceFraction;
  elements.slicePositionLabel.textContent = `${state.sliceAxis.toUpperCase()} = ${formatNumber(section)} mm`;
  let low = result?.temperature_min_k;
  let high = result?.temperature_max_k;
  const diffusion = Boolean(
    state.visualizationMode === "diffusion"
    && result?.time_steps?.length
    && state.diffusionScale
    && !state.comparison
    && !display.difference
  );
  if (diffusion) {
    low = state.diffusionScale.low;
    high = state.diffusionScale.high;
  } else if (state.comparison && !display.difference) {
    low = state.comparison.common_scale.minimum.value;
    high = state.comparison.common_scale.maximum.value;
  } else if (result?.time_steps?.length) {
    low = result.temperature_min_over_time_k;
    high = result.temperature_max_over_time_k;
  }
  const frameIndex = result?.time_steps?.find(step => step.time_s === result.time_s)?.index ?? null;
  const playbackSurface = frameIndex == null ? null
    : state.timeFrames.get(`${result?.study_id}:${frameIndex}`)?.surface || null;
  return engineeringViewport.update({
    ...display, mode: state.visualizationMode, mesh: state.mesh, frameIndex, playbackSurface,
    low, high, diffusion, diffusionUniform: Boolean(diffusion && state.diffusionScale.uniform),
    diffusionReferenceK: diffusion ? playbackSurface?.temperature_min_k : null,
    lockTemperatureScale: Boolean(result?.time_steps?.length
      && !state.comparison && !display.difference), sliceAxis: state.sliceAxis, slicePosition: section,
    differenceStudyId: display.difference ? state.comparison?.differences[0]?.candidate_study_id : null,
    hiddenComponents: state.hiddenComponentIds, isolatedComponent: state.isolatedComponentId,
    selectedComponent: state.selectedComponentId, selectedRegion: state.selectedRegionId,
    selectedFaces: state.selectedSurfaceFaces, selectionMode: state.selectionMode,
    boundaries: display.workpiece?.cad_format === "stl" && !state.comparison
      && state.draftSyncedFor === display.study?.study_id && display.study?.confirmation?.status !== "confirmed"
      ? fixedBoundaryValues().filter(boundary => boundary.temperature_k > 0) : display.study?.plan?.boundaries || [],
    surfaceConditions: !state.comparison && state.draftSyncedFor === display.study?.study_id
      && display.study?.confirmation?.status !== "confirmed" ? surfaceConditionValues() : display.study?.plan?.surface_conditions || [],
    contacts: !state.comparison && state.draftSyncedFor === display.study?.study_id
      && display.study?.confirmation?.status !== "confirmed" ? thermalContactValues() : display.study?.plan?.contacts || [],
    opacity: Number(document.getElementById("geometryOpacity").value),
    source: state.comparison || state.selectionMode === "faces" ? null : displayedHeatSource(display.study?.plan),
    sources: state.comparison || state.selectionMode === "faces" ? [] : displayedHeatSources(display.study?.plan),
    activeSourceIndex: activeSourceCollection()?.activeIndex || 0,
    previewSource: state.sourcePreviewDirty && !state.comparison
      ? displayedHeatSource(display.study?.plan) : null,
    previewSources: state.sourcePreviewDirty && !state.comparison
      ? displayedHeatSources(display.study?.plan) : null,
    solvedSource: solvedHeatSource(display.study?.plan, result),
    solvedSources: solvedHeatSources(display.study?.plan, result),
    sourcePlacement: !elements.sourceEditor.hidden,
    probe: state.probe,
  });
}

function drawLegacyWorkpiece() {
  const canvas = elements.workpieceCanvas;
  state.sourceMarker = null;
  state.temperaturePoints = [];
  state.componentHitTriangles = [];
  state.boundaryMarkers = [];
  const rectangle = canvas.getBoundingClientRect();
  if (!rectangle.width || !rectangle.height) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(rectangle.width * ratio);
  canvas.height = Math.round(rectangle.height * ratio);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  context.clearRect(0, 0, rectangle.width, rectangle.height);

  const display = canvasStudyContext();
  const workpiece = display.workpiece;
  if (!workpiece) return;
  const dimensions = resolvedDimensions(workpiece);
  const study = display.study;
  const plan = study?.plan || null;
  const preview = workpiece.geometry?.summary?.preview;
  if (previewVertices(preview).length && preview?.triangles?.length) {
    drawEngineeringStlPreview(
      context,
      rectangle,
      workpiece,
      plan,
      dimensions,
      preview,
      display.result,
      display.difference,
    );
    return;
  }
  const heatAxis = plan?.boundaries?.[0]?.selector?.split(".")[1]?.[0] || longestAxis(dimensions);
  const axes = [heatAxis, ...["x", "y", "z"].filter((axis) => axis !== heatAxis)];
  const horizontal = axes[0];
  const vertical = axes[1];
  const maxWidth = rectangle.width * 0.64;
  const maxHeight = rectangle.height * 0.52;
  const scale = Math.min(
    maxWidth / Math.max(dimensions[horizontal], 1),
    maxHeight / Math.max(dimensions[vertical], 1),
  );
  const width = Math.max(120, dimensions[horizontal] * scale);
  const height = Math.max(70, dimensions[vertical] * scale);
  const x = (rectangle.width - width) / 2;
  const y = (rectangle.height - height) / 2;

  context.save();
  if (plan) {
    const sideTemperatures = Object.fromEntries(
      plan.boundaries.map((boundary) => [boundary.selector, boundary.temperature_k]),
    );
    const minSelector = `face.${horizontal}min`;
    const maxSelector = `face.${horizontal}max`;
    const minTemperature = sideTemperatures[minSelector];
    const maxTemperature = sideTemperatures[maxSelector];
    const low = Math.min(...plan.boundaries.map((item) => item.temperature_k));
    const high = Math.max(...plan.boundaries.map((item) => item.temperature_k));
    const gradient = context.createLinearGradient(x, y, x + width, y);
    gradient.addColorStop(0, thermalColor(minTemperature, low, high, 0.24));
    gradient.addColorStop(1, thermalColor(maxTemperature, low, high, 0.24));
    context.fillStyle = gradient;
    elements.thermalLegend.hidden = false;
    elements.thermalLegendTitle.textContent = "边界温度 · K";
    elements.coldTemperature.textContent = `${formatNumber(low)} K`;
    elements.midTemperature.textContent = `${formatNumber((low + high) / 2)} K`;
    elements.hotTemperature.textContent = `${formatNumber(high)} K`;
  } else {
    context.fillStyle = "#e5e8e7";
    elements.thermalLegend.hidden = true;
  }
  context.fillRect(x, y, width, height);
  context.strokeStyle = "#495158";
  context.lineWidth = 1.5;
  context.strokeRect(x, y, width, height);

  const depth = Math.min(28, Math.max(14, dimensions[axes[2]] * scale * 0.25));
  context.beginPath();
  context.moveTo(x, y);
  context.lineTo(x + depth, y - depth * 0.55);
  context.lineTo(x + width + depth, y - depth * 0.55);
  context.lineTo(x + width, y);
  context.closePath();
  context.fillStyle = "rgba(255, 255, 255, 0.72)";
  context.fill();
  context.stroke();
  context.beginPath();
  context.moveTo(x + width, y);
  context.lineTo(x + width + depth, y - depth * 0.55);
  context.lineTo(x + width + depth, y + height - depth * 0.55);
  context.lineTo(x + width, y + height);
  context.closePath();
  context.fillStyle = "rgba(73, 81, 88, 0.08)";
  context.fill();
  context.stroke();

  drawDimension(context, x, y + height + 28, x + width, y + height + 28, horizontal, dimensions[horizontal]);
  drawDimension(context, x - 28, y + height, x - 28, y, vertical, dimensions[vertical]);
  context.fillStyle = "#687078";
  context.font = "600 10px Segoe UI, Microsoft YaHei, sans-serif";
  context.textAlign = "right";
  context.fillText(`${axes[2].toUpperCase()} ${formatNumber(dimensions[axes[2]])} mm`, x + width, y - depth - 12);

  if (plan) drawHeatDirection(context, x, y, width, height, plan, horizontal);
  context.restore();
}

function drawEngineeringStlPreview(
  context,
  rectangle,
  workpiece,
  plan,
  dimensions,
  preview,
  result,
  isDifference,
) {
  const bbox = workpiece.geometry.summary.bbox;
  const center = [
    (bbox[0] + bbox[3]) / 2,
    (bbox[1] + bbox[4]) / 2,
    (bbox[2] + bbox[5]) / 2,
  ];
  const { yaw, pitch } = state.view;
  const yawCosine = Math.cos(yaw);
  const yawSine = Math.sin(yaw);
  const pitchCosine = Math.cos(pitch);
  const pitchSine = Math.sin(pitch);
  const transformPoint = (point) => {
    const localX = point[0] - center[0];
    const localY = point[1] - center[1];
    const localZ = point[2] - center[2];
    const rotatedX = localX * yawCosine - localY * yawSine;
    const rotatedY = localX * yawSine + localY * yawCosine;
    return [rotatedX, rotatedY * pitchSine - localZ * pitchCosine, rotatedY * pitchCosine + localZ * pitchSine, point];
  };
  const transformed = previewVertices(preview).map(transformPoint);
  const xValues = transformed.map((point) => point[0]);
  const yValues = transformed.map((point) => point[1]);
  const projectedWidth = Math.max(...xValues) - Math.min(...xValues) || 1;
  const projectedHeight = Math.max(...yValues) - Math.min(...yValues) || 1;
  const scale = Math.min(
    (rectangle.width * 0.72) / projectedWidth,
    (rectangle.height * 0.62) / projectedHeight,
  );
  const offsetX = rectangle.width / 2 - ((Math.min(...xValues) + Math.max(...xValues)) / 2) * scale;
  const offsetY = rectangle.height / 2 - ((Math.min(...yValues) + Math.max(...yValues)) / 2) * scale - 8;
  const projectPoint = (point) => {
    const transformedPoint = transformPoint([point.x, point.y, point.z]);
    return {
      x: transformedPoint[0] * scale + offsetX,
      y: transformedPoint[1] * scale + offsetY,
      depth: transformedPoint[2],
    };
  };
  const showMesh = Boolean(state.mesh && state.visualizationMode === "mesh");
  const showResult = Boolean(
    result && !["model", "mesh"].includes(state.visualizationMode),
  );
  const showFlux = showResult && state.visualizationMode === "flux";
  const showTemperature = showResult && !showFlux;
  const showSlice = showTemperature && state.visualizationMode === "slice";
  const prescribedTemperatures = [
    ...(plan?.boundaries || []).map((item) => item.temperature_k),
    ...(plan?.convection ? [plan.convection.ambient_temperature_k] : []),
  ];
  let low = result?.temperature_min_k
    ?? (prescribedTemperatures.length ? Math.min(...prescribedTemperatures) : 0);
  let high = result?.temperature_max_k
    ?? (prescribedTemperatures.length ? Math.max(...prescribedTemperatures) : 1);
  if (result?.time_steps?.length) {
    low = result.temperature_min_over_time_k;
    high = result.temperature_max_over_time_k;
  }
  if (state.comparison && !isDifference) {
    low = state.comparison.common_scale.minimum.value;
    high = state.comparison.common_scale.maximum.value;
  } else if (isDifference) {
    const span = Math.max(Math.abs(low), Math.abs(high), 1e-12);
    low = -span;
    high = span;
  }
  const fieldPreview = result?.temperature_field_preview;
  const triangleComponentIds = Array.isArray(preview.component_ids)
    ? preview.component_ids
    : [];
  const triangles = preview.triangles
    .map((triangle, index) => ({
      triangle,
      componentId: triangleComponentIds[index] || null,
      depth: triangle.reduce((sum, index) => sum + transformed[index][2], 0) / 3,
    }))
    .filter((item) => componentIsVisible(item.componentId))
    .sort((a, b) => a.depth - b.depth);

  context.save();
  const screenTriangles = [];
  for (const item of triangles) {
    const points = item.triangle.map((index) => transformed[index]);
    const screenPoints = points.map((point) => ({
      x: point[0] * scale + offsetX,
      y: point[1] * scale + offsetY,
    }));
    screenTriangles.push(screenPoints);
    if (item.componentId) {
      state.componentHitTriangles.push({
        componentId: item.componentId,
        depth: item.depth,
        points: screenPoints,
      });
    }
    context.beginPath();
    context.moveTo(screenPoints[0].x, screenPoints[0].y);
    for (let index = 1; index < screenPoints.length; index += 1) {
      context.lineTo(screenPoints[index].x, screenPoints[index].y);
    }
    context.closePath();
    const selectedComponent = item.componentId === state.selectedComponentId;
    context.fillStyle = showSlice
      ? "rgba(93, 108, 118, 0.10)"
      : showMesh
        ? "rgba(129, 149, 166, 0.10)"
      : showResult
        ? "rgba(116, 132, 142, 0.22)"
        : componentPreviewColor(item.componentId, selectedComponent ? 0.68 : 0.42);
    context.strokeStyle = selectedComponent
      ? "rgba(0, 93, 88, 0.95)"
      : showResult || showMesh
      ? "rgba(255, 255, 255, 0.08)"
      : "rgba(55, 63, 68, 0.34)";
    context.lineWidth = selectedComponent ? 1.3 : showResult ? 0.35 : 0.65;
    context.fill();
    context.stroke();
  }

  if (showMesh && state.mesh?.cell_samples_mm?.length) {
    drawEngineeringMeshPreview(
      context,
      state.mesh,
      projectPoint,
      screenTriangles,
    );
  } else if (showSlice && fieldPreview?.volume_samples?.length) {
    state.temperaturePoints = drawEngineeringSlice(
      context,
      fieldPreview,
      bbox,
      projectPoint,
      low,
      high,
      scale,
    );
  } else if (showTemperature && fieldPreview?.samples?.length) {
    state.temperaturePoints = drawEngineeringTemperatureSamples(
      context,
      fieldPreview,
      projectPoint,
      low,
      high,
      scale,
      screenTriangles,
    );
    if (showSlice) elements.slicePositionLabel.textContent = "旧结果 · 表面采样";
  }
  if (showFlux && result.heat_flux_field_preview?.samples?.length) {
    drawEngineeringHeatFlux(
      context,
      result.heat_flux_field_preview,
      projectPoint,
      screenTriangles,
    );
  }

  const displayedSource = displayedHeatSource(plan);
  if (!isDifference) {
    drawEngineeringBoundaryConditions(
      context,
      bbox,
      plan || { boundaries: [], convection: null },
      projectPoint,
      low,
      high,
    );
  }
  if (displayedSource && !state.comparison) {
    const sliceAxisIndex = { x: 0, y: 1, z: 2 }[state.sliceAxis];
    const sliceCoordinate = bbox[sliceAxisIndex]
      + (bbox[sliceAxisIndex + 3] - bbox[sliceAxisIndex]) * state.sliceFraction;
    const sourceCoordinate = displayedSource.center_mm[state.sliceAxis];
    const sourceOffSlice = showSlice
      && Math.abs(sourceCoordinate - sliceCoordinate) > (fieldPreview?.pitch_mm || 0) * 0.6;
    drawEngineeringHeatSource(
      context,
      displayedSource,
      projectPoint,
      scale,
      showResult,
      sourceOffSlice,
    );
  }
  if (!state.comparison) drawEngineeringProbe(context, projectPoint);

  elements.thermalLegend.hidden = !showResult;
  if (showFlux) {
    const fluxPreview = result.heat_flux_field_preview;
    const fluxLow = fluxPreview.minimum_magnitude_w_m2;
    const fluxHigh = fluxPreview.maximum_magnitude_w_m2;
    elements.thermalLegendTitle.textContent = "热流密度 · W/m²";
    elements.coldTemperature.textContent = formatScientific(fluxLow);
    elements.midTemperature.textContent = formatScientific((fluxLow + fluxHigh) / 2);
    elements.hotTemperature.textContent = formatScientific(fluxHigh);
  } else if (showTemperature) {
    elements.thermalLegend.classList.toggle("is-difference", isDifference);
    elements.thermalLegendTitle.textContent = isDifference ? "相对基准温差 · K" : "温度 · K";
    elements.coldTemperature.textContent = `${formatNumber(low)} K`;
    elements.midTemperature.textContent = `${formatNumber((low + high) / 2)} K`;
    elements.hotTemperature.textContent = `${formatNumber(high)} K`;
  }
  if (!isDifference) elements.thermalLegend.classList.remove("is-difference");
  if (showResult && result.time_s != null) {
    elements.thermalLegendTitle.textContent += ` · ${formatNumber(result.time_s)} s`;
  }
  updateProbeReadout();
  context.fillStyle = "#687078";
  context.font = "600 10px Segoe UI, Microsoft YaHei, sans-serif";
  context.textAlign = "center";
  context.fillText(
    `X ${formatNumber(dimensions.x)}  ·  Y ${formatNumber(dimensions.y)}  ·  Z ${formatNumber(dimensions.z)} mm`,
    rectangle.width / 2,
    rectangle.height - 17,
  );
  context.restore();
}

function componentIsVisible(componentId) {
  if (state.isolatedComponentId) return componentId === state.isolatedComponentId;
  return !componentId || !state.hiddenComponentIds.has(componentId);
}

function componentPreviewColor(componentId, alpha) {
  const workpiece = selectedWorkpiece();
  const index = workpiece?.components?.findIndex(
    (component) => component.component_id === componentId,
  ) ?? -1;
  const colors = [
    [36, 123, 114],
    [209, 122, 50],
    [64, 123, 178],
    [155, 94, 119],
    [111, 126, 61],
    [113, 102, 161],
  ];
  const color = colors[index >= 0 ? index % colors.length : 0];
  return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${alpha})`;
}

function drawEngineeringMeshPreview(context, mesh, projectPoint, screenTriangles) {
  const projected = mesh.cell_samples_mm
    .map((sample) => ({ sample, depth: projectPoint({ x: sample[0], y: sample[1], z: sample[2] }).depth }))
    .sort((first, second) => first.depth - second.depth);
  const front = projected.slice(Math.floor(projected.length * 0.28));
  const stride = Math.max(1, Math.ceil(front.length / 900));
  const visible = front.filter((_, index) => index % stride === 0);
  const half = mesh.pitch_mm / 2;
  const edges = [
    [0, 1], [0, 2], [0, 4], [1, 3], [1, 5], [2, 3],
    [2, 6], [3, 7], [4, 5], [4, 6], [5, 7], [6, 7],
  ];
  const offsets = [
    [-half, -half, -half], [half, -half, -half],
    [-half, half, -half], [half, half, -half],
    [-half, -half, half], [half, -half, half],
    [-half, half, half], [half, half, half],
  ];

  context.save();
  context.beginPath();
  for (const triangle of screenTriangles) {
    const signedArea = (triangle[1].x - triangle[0].x) * (triangle[2].y - triangle[0].y)
      - (triangle[1].y - triangle[0].y) * (triangle[2].x - triangle[0].x);
    const ordered = signedArea < 0
      ? [triangle[0], triangle[2], triangle[1]]
      : triangle;
    context.moveTo(ordered[0].x, ordered[0].y);
    context.lineTo(ordered[1].x, ordered[1].y);
    context.lineTo(ordered[2].x, ordered[2].y);
    context.closePath();
  }
  context.clip();
  context.strokeStyle = mesh.quality_status === "blocked"
    ? "rgba(178, 55, 48, 0.86)"
    : mesh.quality_status === "warning"
      ? "rgba(173, 101, 20, 0.82)"
      : "rgba(0, 112, 108, 0.82)";
  context.lineWidth = 0.55;
  for (const { sample } of visible) {
    const corners = offsets.map(([dx, dy, dz]) => projectPoint({
      x: sample[0] + dx,
      y: sample[1] + dy,
      z: sample[2] + dz,
    }));
    context.beginPath();
    for (const [start, end] of edges) {
      context.moveTo(corners[start].x, corners[start].y);
      context.lineTo(corners[end].x, corners[end].y);
    }
    context.stroke();
  }
  context.restore();
}

function drawEngineeringTemperatureSamples(
  context,
  fieldPreview,
  projectPoint,
  low,
  high,
  scale,
  screenTriangles,
) {
  const projected = fieldPreview.samples.map((sample) => ({
    ...projectPoint({ x: sample[0], y: sample[1], z: sample[2] }),
    temperature: sample[3],
    world: { x: sample[0], y: sample[1], z: sample[2] },
  }));
  const sortedDepths = projected.map((sample) => sample.depth).sort((a, b) => a - b);
  const visibleDepth = sortedDepths[Math.floor(sortedDepths.length * 0.42)];
  const visible = projected.filter((sample) => sample.depth >= visibleDepth).sort((a, b) => a.depth - b.depth);
  const radius = Math.max(5, Math.min(28, fieldPreview.pitch_mm * scale * 1.14));
  context.save();
  context.beginPath();
  for (const triangle of screenTriangles) {
    const signedArea = (triangle[1].x - triangle[0].x) * (triangle[2].y - triangle[0].y)
      - (triangle[1].y - triangle[0].y) * (triangle[2].x - triangle[0].x);
    const ordered = signedArea < 0
      ? [triangle[0], triangle[2], triangle[1]]
      : triangle;
    context.moveTo(ordered[0].x, ordered[0].y);
    context.lineTo(ordered[1].x, ordered[1].y);
    context.lineTo(ordered[2].x, ordered[2].y);
    context.closePath();
  }
  context.clip();
  for (const sample of visible) {
    let temperature = sample.temperature;
    if (state.visualizationMode === "contour") {
      const normalized = (temperature - low) / Math.max(high - low, 1e-12);
      const band = Math.round(Math.max(0, Math.min(1, normalized)) * 9) / 9;
      temperature = low + (high - low) * band;
    }
    const gradient = context.createRadialGradient(
      sample.x,
      sample.y,
      0,
      sample.x,
      sample.y,
      radius,
    );
    gradient.addColorStop(0, resultFieldColor(temperature, low, high, 0.9));
    gradient.addColorStop(0.58, resultFieldColor(temperature, low, high, 0.7));
    gradient.addColorStop(1, resultFieldColor(temperature, low, high, 0.04));
    context.fillStyle = gradient;
    context.fillRect(sample.x - radius, sample.y - radius, radius * 2, radius * 2);
  }
  context.restore();
  return visible;
}

function drawEngineeringHeatFlux(context, fieldPreview, projectPoint, screenTriangles) {
  const projected = fieldPreview.samples
    .filter((sample) => sample[6] > 0)
    .map((sample) => {
      const magnitude = sample[6];
      const origin = projectPoint({ x: sample[0], y: sample[1], z: sample[2] });
      const directionPoint = projectPoint({
        x: sample[0] + sample[3] / magnitude,
        y: sample[1] + sample[4] / magnitude,
        z: sample[2] + sample[5] / magnitude,
      });
      return {
        ...origin,
        dx: directionPoint.x - origin.x,
        dy: directionPoint.y - origin.y,
        magnitude,
      };
    });
  if (!projected.length) return;
  const sortedDepths = projected.map((sample) => sample.depth).sort((a, b) => a - b);
  const visibleDepth = sortedDepths[Math.floor(sortedDepths.length * 0.42)];
  const visible = projected.filter((sample) => sample.depth >= visibleDepth);
  const stride = Math.max(1, Math.ceil(visible.length / 160));
  const minimum = fieldPreview.minimum_magnitude_w_m2;
  const maximum = Math.max(fieldPreview.maximum_magnitude_w_m2, 1e-12);

  context.save();
  context.beginPath();
  for (const triangle of screenTriangles) {
    context.moveTo(triangle[0].x, triangle[0].y);
    context.lineTo(triangle[1].x, triangle[1].y);
    context.lineTo(triangle[2].x, triangle[2].y);
    context.closePath();
  }
  context.clip();
  visible.filter((_, index) => index % stride === 0).forEach((sample) => {
    const projectedLength = Math.hypot(sample.dx, sample.dy);
    if (projectedLength < 1e-9) return;
    const ux = sample.dx / projectedLength;
    const uy = sample.dy / projectedLength;
    const normalized = Math.sqrt(Math.max(0, Math.min(1, sample.magnitude / maximum)));
    const length = 9 + normalized * 19;
    const start = { x: sample.x - ux * length * 0.35, y: sample.y - uy * length * 0.35 };
    const end = { x: sample.x + ux * length * 0.65, y: sample.y + uy * length * 0.65 };
    drawFieldArrow(
      context,
      start,
      end,
      thermalColor(sample.magnitude, minimum, maximum, 0.94),
    );
  });
  context.restore();
}

function drawFieldArrow(context, start, end, color) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.hypot(dx, dy);
  if (length < 4) return;
  const ux = dx / length;
  const uy = dy / length;
  context.save();
  context.strokeStyle = color;
  context.fillStyle = color;
  context.lineWidth = 1.25;
  context.beginPath();
  context.moveTo(start.x, start.y);
  context.lineTo(end.x, end.y);
  context.stroke();
  context.beginPath();
  context.moveTo(end.x, end.y);
  context.lineTo(end.x - ux * 5 - uy * 2.8, end.y - uy * 5 + ux * 2.8);
  context.lineTo(end.x - ux * 5 + uy * 2.8, end.y - uy * 5 - ux * 2.8);
  context.closePath();
  context.fill();
  context.restore();
}

function drawEngineeringSlice(context, fieldPreview, bbox, projectPoint, low, high, scale) {
  const axis = state.sliceAxis;
  const axisIndex = { x: 0, y: 1, z: 2 }[axis];
  const minimum = bbox[axisIndex];
  const maximum = bbox[axisIndex + 3];
  const position = minimum + (maximum - minimum) * state.sliceFraction;
  const point = (x, y, z) => projectPoint({ x, y, z });
  const planeCorners = {
    x: [point(position, bbox[1], bbox[2]), point(position, bbox[4], bbox[2]), point(position, bbox[4], bbox[5]), point(position, bbox[1], bbox[5])],
    y: [point(bbox[0], position, bbox[2]), point(bbox[3], position, bbox[2]), point(bbox[3], position, bbox[5]), point(bbox[0], position, bbox[5])],
    z: [point(bbox[0], bbox[1], position), point(bbox[3], bbox[1], position), point(bbox[3], bbox[4], position), point(bbox[0], bbox[4], position)],
  }[axis];
  context.save();
  context.beginPath();
  context.moveTo(planeCorners[0].x, planeCorners[0].y);
  planeCorners.slice(1).forEach((corner) => context.lineTo(corner.x, corner.y));
  context.closePath();
  context.fillStyle = "rgba(255, 255, 255, 0.38)";
  context.strokeStyle = "rgba(23, 26, 29, 0.62)";
  context.setLineDash([5, 4]);
  context.lineWidth = 1;
  context.fill();
  context.stroke();
  context.setLineDash([]);

  const minimumDistance = fieldPreview.volume_samples.reduce(
    (distance, sample) => Math.min(distance, Math.abs(sample[axisIndex] - position)),
    Number.POSITIVE_INFINITY,
  );
  const tolerance = Math.max(fieldPreview.pitch_mm * 0.52, minimumDistance + 1e-9);
  const projected = fieldPreview.volume_samples
    .filter((sample) => Math.abs(sample[axisIndex] - position) <= tolerance)
    .map((sample) => ({
      ...projectPoint({ x: sample[0], y: sample[1], z: sample[2] }),
      temperature: sample[3],
      world: { x: sample[0], y: sample[1], z: sample[2] },
    }))
    .sort((a, b) => a.depth - b.depth);
  const radius = Math.max(2.4, Math.min(12, fieldPreview.pitch_mm * scale * 0.78));
  for (const sample of projected) {
    context.beginPath();
    context.arc(sample.x, sample.y, radius, 0, Math.PI * 2);
    context.fillStyle = thermalColor(sample.temperature, low, high, 0.96);
    context.fill();
  }
  context.restore();
  elements.slicePositionLabel.textContent = `${axis.toUpperCase()} = ${formatNumber(position)} mm`;
  return projected;
}

function drawEngineeringBoundaryConditions(context, bbox, plan, projectPoint, low, high) {
  const center = {
    x: (bbox[0] + bbox[3]) / 2,
    y: (bbox[1] + bbox[4]) / 2,
    z: (bbox[2] + bbox[5]) / 2,
  };
  const faceCenters = {
    "face.xmin": { ...center, x: bbox[0] },
    "face.xmax": { ...center, x: bbox[3] },
    "face.ymin": { ...center, y: bbox[1] },
    "face.ymax": { ...center, y: bbox[4] },
    "face.zmin": { ...center, z: bbox[2] },
    "face.zmax": { ...center, z: bbox[5] },
  };
  const projectedCenter = projectPoint(center);
  const regions = selectedWorkpiece()?.regions || [];
  const regionBySelector = new Map(regions.map((region) => [region.selector, region]));
  const fixedSelectors = new Set((plan.boundaries || []).map((boundary) => boundary.selector));
  state.boundaryMarkers = Object.entries(faceCenters).map(([selector, face]) => {
    const projected = projectPoint(face);
    return {
      selector,
      regionId: regionBySelector.get(selector)?.region_id || null,
      x: projected.x,
      y: projected.y,
      depth: projected.depth,
    };
  });
  for (const marker of state.boundaryMarkers) {
    if (fixedSelectors.has(marker.selector)) continue;
    const selected = marker.regionId === state.selectedRegionId;
    context.save();
    context.beginPath();
    context.arc(marker.x, marker.y, selected ? 6 : 3, 0, Math.PI * 2);
    context.fillStyle = selected ? "rgba(0, 112, 108, 0.14)" : "rgba(255, 255, 255, 0.76)";
    context.fill();
    context.strokeStyle = selected ? "rgba(0, 93, 88, 0.96)" : "rgba(73, 81, 88, 0.64)";
    context.lineWidth = selected ? 1.6 : 0.8;
    context.stroke();
    context.restore();
  }
  for (const boundary of plan.boundaries || []) {
    const face = faceCenters[boundary.selector];
    if (!face) continue;
    const anchor = projectPoint(face);
    const directionLength = Math.hypot(anchor.x - projectedCenter.x, anchor.y - projectedCenter.y) || 1;
    const labelX = anchor.x + ((anchor.x - projectedCenter.x) / directionLength) * 28;
    const labelY = anchor.y + ((anchor.y - projectedCenter.y) / directionLength) * 22;
    const color = thermalColor(boundary.temperature_k, low, high, 1);
    context.save();
    const region = regionBySelector.get(boundary.selector);
    const selected = region?.region_id === state.selectedRegionId;
    context.beginPath();
    context.arc(anchor.x, anchor.y, selected ? 7 : 4, 0, Math.PI * 2);
    context.fillStyle = color;
    context.fill();
    context.strokeStyle = "rgba(255, 255, 255, 0.94)";
    context.lineWidth = 1.5;
    context.stroke();
    context.beginPath();
    context.moveTo(anchor.x, anchor.y);
    context.lineTo(labelX, labelY);
    context.strokeStyle = color;
    context.lineWidth = 1;
    context.stroke();
    drawEngineeringLabel(context, `${faceLabel(boundary.selector)}  ${formatNumber(boundary.temperature_k)} K`, labelX, labelY, color);
    context.restore();
  }

  if (plan.convection) {
    const allFaces = Object.entries(faceCenters);
    const convectionFaces = allFaces.filter(([selector]) => !fixedSelectors.has(selector));
    const projectedFaces = (convectionFaces.length ? convectionFaces : allFaces)
      .map(([, face]) => ({
        world: face,
        projected: projectPoint(face),
      }));
    const topFace = projectedFaces.sort((a, b) => a.projected.y - b.projected.y)[0];
    const dx = topFace.projected.x - projectedCenter.x;
    const dy = topFace.projected.y - projectedCenter.y;
    const length = Math.hypot(dx, dy) || 1;
    const start = {
      x: topFace.projected.x + (dx / length) * 4,
      y: topFace.projected.y + (dy / length) * 4,
    };
    const end = {
      x: topFace.projected.x + (dx / length) * 28,
      y: topFace.projected.y + (dy / length) * 28,
    };
    drawEngineeringArrow(
      context,
      start,
      end,
      `对流 h=${formatNumber(plan.convection.heat_transfer_coefficient_w_m2_k)}`,
      "rgba(13, 107, 93, 0.84)",
    );
  }
}

function drawEngineeringArrow(context, start, end, label, color) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.hypot(dx, dy);
  if (length < 12) return;
  const ux = dx / length;
  const uy = dy / length;
  context.save();
  context.strokeStyle = color;
  context.fillStyle = color;
  context.lineWidth = 1.4;
  context.beginPath();
  context.moveTo(start.x + ux * 10, start.y + uy * 10);
  context.lineTo(end.x - ux * 9, end.y - uy * 9);
  context.stroke();
  context.beginPath();
  context.moveTo(end.x, end.y);
  context.lineTo(end.x - ux * 10 - uy * 4, end.y - uy * 10 + ux * 4);
  context.lineTo(end.x - ux * 10 + uy * 4, end.y - uy * 10 - ux * 4);
  context.closePath();
  context.fill();
  context.font = "700 8px Segoe UI, Microsoft YaHei, sans-serif";
  context.textAlign = "center";
  context.fillText(label, (start.x + end.x) / 2 - uy * 9, (start.y + end.y) / 2 + ux * 9);
  context.restore();
}

function drawEngineeringLabel(context, text, x, y, color) {
  context.save();
  context.font = "700 8px Segoe UI, Microsoft YaHei, sans-serif";
  const width = context.measureText(text).width + 10;
  context.fillStyle = "rgba(255, 255, 255, 0.92)";
  context.strokeStyle = color;
  context.lineWidth = 1;
  context.fillRect(x - width / 2, y - 9, width, 18);
  context.strokeRect(x - width / 2, y - 9, width, 18);
  context.fillStyle = "#20262a";
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(text, x, y);
  context.restore();
}

function drawEngineeringHeatSource(
  context,
  source,
  projectPoint,
  scale,
  showThermal,
  projectedOnSlice,
) {
  const marker = projectPoint(source.center_mm);
  let endMarker = null;
  context.save();
  if (projectedOnSlice) context.globalAlpha = 0.5;
  if (source.shape === "line" && source.end_mm) {
    endMarker = projectPoint(source.end_mm);
    context.beginPath();
    context.moveTo(marker.x, marker.y);
    context.lineTo(endMarker.x, endMarker.y);
    context.strokeStyle = "rgba(214, 85, 63, 0.92)";
    context.lineWidth = Math.max(3, Math.min(12, source.radius_mm * scale * 1.5));
    context.lineCap = "round";
    context.stroke();
    drawSourceHandle(context, endMarker.x, endMarker.y, 5);
  } else if (source.shape === "surface") {
    const normalIndex = { x: 0, y: 1, z: 2 }[source.surface_normal_axis || "z"];
    const tangentAxes = [0, 1, 2].filter((axis) => axis !== normalIndex);
    const centerValues = [source.center_mm.x, source.center_mm.y, source.center_mm.z];
    const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([u, v]) => {
      const values = [...centerValues];
      values[tangentAxes[0]] += u * source.surface_width_mm / 2;
      values[tangentAxes[1]] += v * source.surface_height_mm / 2;
      return projectPoint({ x: values[0], y: values[1], z: values[2] });
    });
    context.beginPath();
    context.moveTo(corners[0].x, corners[0].y);
    corners.slice(1).forEach((corner) => context.lineTo(corner.x, corner.y));
    context.closePath();
    context.fillStyle = "rgba(214, 85, 63, 0.25)";
    context.strokeStyle = "rgba(180, 52, 38, 0.98)";
    context.lineWidth = 2;
    context.fill();
    context.stroke();
  } else {
    context.beginPath();
    context.arc(marker.x, marker.y, Math.max(8, Math.min(28, source.radius_mm * scale)), 0, Math.PI * 2);
    context.fillStyle = "rgba(214, 85, 63, 0.18)";
    context.fill();
  }
  if (source.placement === "embedded") {
    context.beginPath();
    context.arc(marker.x, marker.y, 12, 0, Math.PI * 2);
    context.setLineDash([3, 3]);
    context.strokeStyle = "rgba(164, 49, 36, 0.92)";
    context.lineWidth = 1.5;
    context.stroke();
    context.setLineDash([]);
  }
  state.sourceMarker = { x: marker.x, y: marker.y, end: endMarker, scale };
  positionSourceEditor();
  drawSourceHandle(context, marker.x, marker.y, 7);
  const shape = { point: "点", line: "线", surface: "面", volume: "体" }[source.shape] || "";
  context.fillStyle = "#20262a";
  context.font = "700 9px Segoe UI, Microsoft YaHei, sans-serif";
  context.textAlign = "left";
  const sourceLabel = projectedOnSlice ? `${shape}热源投影` : `${shape}热源`;
  context.fillText(`${sourceLabel}  ${formatNumber(source.total_power_w)} W`, marker.x + 12, marker.y - 1);
  if (source.placement === "embedded") {
    context.fillStyle = "#a43124";
    context.font = "650 8px Segoe UI, Microsoft YaHei, sans-serif";
    context.fillText(`嵌入 ${formatNumber(source.embedding_depth_mm)} mm`, marker.x + 12, marker.y + 11);
  }
  context.restore();
}

function drawEngineeringProbe(context, projectPoint) {
  if (!state.probe || !isTemperatureVisualization()) return;
  const marker = projectPoint(state.probe.world);
  context.save();
  context.strokeStyle = "rgba(23, 26, 29, 0.92)";
  context.fillStyle = thermalColor(
    state.probe.temperature,
    displayedTimeResult(state.result).temperature_min_k,
    displayedTimeResult(state.result).temperature_max_k,
    1,
  );
  context.lineWidth = 1.5;
  context.beginPath();
  context.arc(marker.x, marker.y, 8, 0, Math.PI * 2);
  context.fill();
  context.stroke();
  context.beginPath();
  context.moveTo(marker.x - 12, marker.y);
  context.lineTo(marker.x + 12, marker.y);
  context.moveTo(marker.x, marker.y - 12);
  context.lineTo(marker.x, marker.y + 12);
  context.stroke();
  context.restore();
}

function updateProbeReadout() {
  if (!state.probe || !isTemperatureVisualization()) {
    elements.probeReadout.hidden = true;
    return;
  }
  const { world, temperature } = state.probe;
  elements.probeReadout.textContent = `探针  ${formatNumber(temperature)} K / ${formatNumber(temperature - 273.15)} °C  ·  (${formatNumber(world.x)}, ${formatNumber(world.y)}, ${formatNumber(world.z)}) mm`;
  elements.probeReadout.hidden = false;
}

function drawStlPreview(context, rectangle, workpiece, plan, dimensions, preview) {
  const bbox = workpiece.geometry.summary.bbox;
  const center = [
    (bbox[0] + bbox[3]) / 2,
    (bbox[1] + bbox[4]) / 2,
    (bbox[2] + bbox[5]) / 2,
  ];
  const { yaw, pitch } = state.view;
  const yawCosine = Math.cos(yaw);
  const yawSine = Math.sin(yaw);
  const pitchCosine = Math.cos(pitch);
  const pitchSine = Math.sin(pitch);
  const transformed = previewVertices(preview).map((vertex) => {
    const x = vertex[0] - center[0];
    const y = vertex[1] - center[1];
    const z = vertex[2] - center[2];
    const rotatedX = x * yawCosine - y * yawSine;
    const rotatedY = x * yawSine + y * yawCosine;
    const screenY = rotatedY * pitchSine - z * pitchCosine;
    const depth = rotatedY * pitchCosine + z * pitchSine;
    return [rotatedX, screenY, depth, vertex];
  });
  const xValues = transformed.map((point) => point[0]);
  const yValues = transformed.map((point) => point[1]);
  const projectedWidth = Math.max(...xValues) - Math.min(...xValues) || 1;
  const projectedHeight = Math.max(...yValues) - Math.min(...yValues) || 1;
  const scale = Math.min(
    (rectangle.width * 0.72) / projectedWidth,
    (rectangle.height * 0.62) / projectedHeight,
  );
  const offsetX = rectangle.width / 2 - ((Math.min(...xValues) + Math.max(...xValues)) / 2) * scale;
  const offsetY = rectangle.height / 2 - ((Math.min(...yValues) + Math.max(...yValues)) / 2) * scale - 8;
  const projectPoint = (point) => {
    const localX = point.x - center[0];
    const localY = point.y - center[1];
    const localZ = point.z - center[2];
    const rotatedX = localX * yawCosine - localY * yawSine;
    const rotatedY = localX * yawSine + localY * yawCosine;
    return {
      x: rotatedX * scale + offsetX,
      y: (rotatedY * pitchSine - localZ * pitchCosine) * scale + offsetY,
      depth: rotatedY * pitchCosine + localZ * pitchSine,
    };
  };
  const heatAxis = plan?.boundaries?.[0]?.selector?.split(".")[1]?.[0] || longestAxis(dimensions);
  const displayedSource = displayedHeatSource(plan);
  const showThermal = Boolean(state.result && state.visualizationMode !== "model");
  const axisIndex = { x: 0, y: 1, z: 2 }[heatAxis];
  const temperatures = Object.fromEntries(
    (plan?.boundaries || []).map((boundary) => [boundary.selector, boundary.temperature_k]),
  );
  const tMin = temperatures[`face.${heatAxis}min`];
  const tMax = temperatures[`face.${heatAxis}max`];
  const low = state.result?.temperature_min_k ?? Math.min(tMin, tMax);
  const high = state.result?.temperature_max_k ?? Math.max(tMin, tMax);
  const axisLow = bbox[axisIndex];
  const axisHigh = bbox[axisIndex + 3];
  const triangles = preview.triangles
    .map((triangle) => ({
      triangle,
      depth: triangle.reduce((sum, index) => sum + transformed[index][2], 0) / 3,
    }))
    .sort((a, b) => a.depth - b.depth);

  context.save();
  for (const item of triangles) {
    const points = item.triangle.map((index) => transformed[index]);
    const screenPoints = points.map((point) => ({
      x: point[0] * scale + offsetX,
      y: point[1] * scale + offsetY,
    }));
    context.beginPath();
    context.moveTo(screenPoints[0].x, screenPoints[0].y);
    for (let index = 1; index < points.length; index += 1) {
      context.lineTo(screenPoints[index].x, screenPoints[index].y);
    }
    context.closePath();
    if (showThermal) {
      let temperature = low;
      if (displayedSource) {
        const centroid = [0, 1, 2].map(
          (index) => points.reduce((sum, point) => sum + point[3][index], 0) / 3,
        );
        const distance = distanceToHeatSource(centroid, displayedSource);
        const decayLength = Math.max(
          sourceCharacteristicSize(displayedSource) * 2.5,
          Math.min(dimensions.x, dimensions.y, dimensions.z) / 3,
        );
        const fraction = Math.exp(-distance / Math.max(decayLength, 1e-12));
        temperature = low + (high - low) * fraction;
      } else {
        const coordinate = points.reduce((sum, point) => sum + point[3][axisIndex], 0) / 3;
        const fraction = (coordinate - axisLow) / Math.max(axisHigh - axisLow, 1e-12);
        temperature = tMin + (tMax - tMin) * fraction;
      }
      if (state.visualizationMode === "contour") {
        const normalized = (temperature - low) / Math.max(high - low, 1e-12);
        const band = Math.round(Math.max(0, Math.min(1, normalized)) * 7) / 7;
        temperature = low + (high - low) * band;
      }
      context.fillStyle = thermalColor(temperature, low, high, 0.88);
    } else {
      context.fillStyle = "rgba(129, 149, 166, 0.38)";
    }
    context.strokeStyle = showThermal
      ? "rgba(255, 255, 255, 0.18)"
      : "rgba(55, 63, 68, 0.34)";
    context.lineWidth = showThermal ? 0.45 : 0.65;
    context.fill();
    context.stroke();
  }
  const fieldPreview = state.result?.temperature_field_preview;
  if (showThermal && fieldPreview?.samples?.length) {
    drawTemperatureSamples(context, fieldPreview, projectPoint, low, high, scale);
  }
  if (displayedSource) {
    const source = displayedSource.center_mm;
    const marker = projectPoint(source);
    if (showThermal) {
      drawHeatOverlay(context, marker, displayedSource, dimensions, scale);
    }
    let endMarker = null;
    if (displayedSource.shape === "line" && displayedSource.end_mm) {
      endMarker = projectPoint(displayedSource.end_mm);
      context.beginPath();
      context.moveTo(marker.x, marker.y);
      context.lineTo(endMarker.x, endMarker.y);
      context.strokeStyle = "rgba(214, 85, 63, 0.84)";
      context.lineWidth = Math.max(3, Math.min(12, displayedSource.radius_mm * scale * 1.5));
      context.lineCap = "round";
      context.stroke();
      drawSourceHandle(context, endMarker.x, endMarker.y, 5);
    } else if (displayedSource.shape === "surface") {
      const normalIndex = { x: 0, y: 1, z: 2 }[displayedSource.surface_normal_axis || "z"];
      const tangentAxes = [0, 1, 2].filter((axis) => axis !== normalIndex);
      const centerValues = [source.x, source.y, source.z];
      const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([u, v]) => {
        const values = [...centerValues];
        values[tangentAxes[0]] += u * displayedSource.surface_width_mm / 2;
        values[tangentAxes[1]] += v * displayedSource.surface_height_mm / 2;
        return projectPoint({ x: values[0], y: values[1], z: values[2] });
      });
      context.beginPath();
      context.moveTo(corners[0].x, corners[0].y);
      corners.slice(1).forEach((corner) => context.lineTo(corner.x, corner.y));
      context.closePath();
      context.fillStyle = "rgba(214, 85, 63, 0.22)";
      context.strokeStyle = "rgba(214, 85, 63, 0.92)";
      context.lineWidth = 2;
      context.fill();
      context.stroke();
    } else {
      context.beginPath();
      context.arc(
        marker.x,
        marker.y,
        Math.max(8, Math.min(30, displayedSource.radius_mm * scale)),
        0,
        Math.PI * 2,
      );
      context.fillStyle = "rgba(214, 85, 63, 0.14)";
      context.fill();
    }
    state.sourceMarker = {
      x: marker.x,
      y: marker.y,
      end: endMarker,
      scale,
    };
    positionSourceEditor();
    drawSourceHandle(context, marker.x, marker.y, 7);
    context.fillStyle = "#20262a";
    context.font = "700 9px Segoe UI, Microsoft YaHei, sans-serif";
    context.textAlign = "left";
    context.fillText(`${formatNumber(displayedSource.total_power_w)} W`, marker.x + 12, marker.y - 1);
    if (showThermal) {
      context.fillStyle = "#a43124";
      context.font = "650 9px Segoe UI, Microsoft YaHei, sans-serif";
      context.fillText(`峰值 ${formatNumber(high)} K`, marker.x + 12, marker.y + 11);
    }
  }
  elements.thermalLegend.hidden = !showThermal;
  if (showThermal) {
    elements.thermalLegendTitle.textContent = "温度 · K";
    elements.coldTemperature.textContent = `${formatNumber(low)} K`;
    elements.midTemperature.textContent = `${formatNumber((low + high) / 2)} K`;
    elements.hotTemperature.textContent = `${formatNumber(high)} K`;
  }
  context.fillStyle = "#687078";
  context.font = "600 10px Segoe UI, Microsoft YaHei, sans-serif";
  context.textAlign = "center";
  context.fillText(
    `X ${formatNumber(dimensions.x)}  ·  Y ${formatNumber(dimensions.y)}  ·  Z ${formatNumber(dimensions.z)} mm`,
    rectangle.width / 2,
    rectangle.height - 17,
  );
  context.restore();
}

function drawSourceHandle(context, x, y, radius) {
  context.beginPath();
  context.arc(x, y, radius, 0, Math.PI * 2);
  context.fillStyle = "#d6553f";
  context.fill();
  context.strokeStyle = "rgba(255, 255, 255, 0.92)";
  context.lineWidth = 2;
  context.stroke();
}

function drawTemperatureSamples(context, fieldPreview, projectPoint, low, high, scale) {
  const projected = fieldPreview.samples.map((sample) => ({
    ...projectPoint({ x: sample[0], y: sample[1], z: sample[2] }),
    temperature: sample[3],
  }));
  const sortedDepths = projected.map((sample) => sample.depth).sort((a, b) => a - b);
  const visibleDepth = sortedDepths[Math.floor(sortedDepths.length * 0.46)];
  const radius = Math.max(2, Math.min(10, fieldPreview.pitch_mm * scale * 0.72));
  context.save();
  for (const sample of projected) {
    if (sample.depth < visibleDepth) continue;
    let temperature = sample.temperature;
    if (state.visualizationMode === "contour") {
      const normalized = (temperature - low) / Math.max(high - low, 1e-12);
      const band = Math.round(Math.max(0, Math.min(1, normalized)) * 7) / 7;
      temperature = low + (high - low) * band;
    }
    context.beginPath();
    context.arc(sample.x, sample.y, radius, 0, Math.PI * 2);
    context.fillStyle = thermalColor(temperature, low, high, 0.62);
    context.fill();
  }
  context.restore();
}

function sourceCharacteristicSize(source) {
  if (source.shape === "surface") {
    return Math.max(
      source.surface_thickness_mm || 0,
      Math.sqrt((source.surface_width_mm || 0) * (source.surface_height_mm || 0)) / 3,
    );
  }
  if (source.shape === "line" && source.end_mm) {
    const length = Math.hypot(
      source.end_mm.x - source.center_mm.x,
      source.end_mm.y - source.center_mm.y,
      source.end_mm.z - source.center_mm.z,
    );
    return Math.max(source.radius_mm, length / 5);
  }
  return source.radius_mm;
}

function drawHeatOverlay(context, marker, source, dimensions, scale) {
  const sourceRadius = Math.max(10, sourceCharacteristicSize(source) * scale);
  const maximumRadius = Math.max(dimensions.x, dimensions.y, dimensions.z) * scale * 0.62;
  context.save();

  const glow = context.createRadialGradient(
    marker.x,
    marker.y,
    0,
    marker.x,
    marker.y,
    Math.max(sourceRadius * 2.2, 24),
  );
  glow.addColorStop(0, "rgba(202, 67, 48, 0.34)");
  glow.addColorStop(0.45, "rgba(238, 190, 73, 0.16)");
  glow.addColorStop(1, "rgba(42, 157, 143, 0)");
  context.fillStyle = glow;
  context.fillRect(
    marker.x - sourceRadius * 2.3,
    marker.y - sourceRadius * 2.3,
    sourceRadius * 4.6,
    sourceRadius * 4.6,
  );

  if (state.visualizationMode === "contour") {
    for (let index = 1; index <= 6; index += 1) {
      const progress = index / 7;
      const radius = sourceRadius + (maximumRadius - sourceRadius) * progress;
      context.beginPath();
      context.ellipse(marker.x, marker.y, radius, radius * 0.52, 0, 0, Math.PI * 2);
      context.strokeStyle = `rgba(255, 255, 255, ${0.62 - progress * 0.32})`;
      context.lineWidth = 1;
      context.stroke();
    }
  } else {
    const elapsed = performance.now() - state.heatAnimationStartedAt;
    const phase = Math.max(0, Math.min(1, elapsed / 1600));
    for (const offset of [0, 0.34, 0.68]) {
      const progress = (phase + offset) % 1;
      const radius = sourceRadius + (maximumRadius - sourceRadius) * progress;
      context.beginPath();
      context.ellipse(marker.x, marker.y, radius, radius * 0.52, 0, 0, Math.PI * 2);
      context.strokeStyle = `rgba(255, 246, 221, ${(1 - progress) * 0.42})`;
      context.lineWidth = 1.5;
      context.stroke();
    }
  }
  context.restore();
}

function distanceToHeatSource(point, source) {
  const center = [source.center_mm.x, source.center_mm.y, source.center_mm.z];
  if (source.shape === "line" && source.end_mm) {
    const end = [source.end_mm.x, source.end_mm.y, source.end_mm.z];
    const direction = end.map((value, index) => value - center[index]);
    const squaredLength = direction.reduce((sum, value) => sum + value * value, 0);
    const fraction = squaredLength
      ? Math.max(0, Math.min(1, point.reduce((sum, value, index) => sum + (value - center[index]) * direction[index], 0) / squaredLength))
      : 0;
    return Math.hypot(...point.map((value, index) => value - center[index] - fraction * direction[index]));
  }
  if (source.shape === "surface") {
    const normalIndex = { x: 0, y: 1, z: 2 }[source.surface_normal_axis || "z"];
    const tangentAxes = [0, 1, 2].filter((axis) => axis !== normalIndex);
    const delta = point.map((value, index) => Math.abs(value - center[index]));
    const outside = [0, 0, 0];
    outside[normalIndex] = Math.max(0, delta[normalIndex] - (source.surface_thickness_mm || 0) / 2);
    outside[tangentAxes[0]] = Math.max(0, delta[tangentAxes[0]] - (source.surface_width_mm || 0) / 2);
    outside[tangentAxes[1]] = Math.max(0, delta[tangentAxes[1]] - (source.surface_height_mm || 0) / 2);
    return Math.hypot(...outside);
  }
  return Math.hypot(...point.map((value, index) => value - center[index]));
}

function resetStlView(redraw = true) {
  engineeringViewport?.fit();
  state.view.yaw = -Math.PI / 5;
  state.view.pitch = 0.42;
  state.viewDrag = null;
  elements.workpieceCanvas.classList.remove("is-dragging");
  if (redraw) drawWorkpiece();
}

function hasRotatablePreview() {
  return Boolean(selectedWorkpiece()?.geometry?.summary?.preview?.triangles?.length);
}

function sourceHandleAt(pointerX, pointerY) {
  const marker = state.sourceMarker;
  if (!marker) return null;
  if (marker.end && Math.hypot(pointerX - marker.end.x, pointerY - marker.end.y) <= 18) {
    return "end";
  }
  return Math.hypot(pointerX - marker.x, pointerY - marker.y) <= 18 ? "center" : null;
}

function beginViewDrag(event) {
  if (!hasRotatablePreview() || (event.pointerType === "mouse" && event.button !== 0)) return;
  const rectangle = elements.workpieceCanvas.getBoundingClientRect();
  const pointerX = event.clientX - rectangle.left;
  const pointerY = event.clientY - rectangle.top;
  const marker = state.sourceMarker;
  const sourceHandle = sourceHandleAt(pointerX, pointerY);
  if (marker && sourceHandle) {
    const draft = activeSourceDraft();
    if (!draft) return;
    state.sourceDrag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startCenter: { ...draft.center },
      startEnd: { ...draft.end },
      handle: sourceHandle,
      scale: marker.scale,
    };
    elements.workpieceCanvas.setPointerCapture(event.pointerId);
    elements.workpieceCanvas.classList.add("is-dragging", "is-over-source");
    openSourceEditor();
    return;
  }
  state.viewDrag = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    startYaw: state.view.yaw,
    startPitch: state.view.pitch,
    moved: false,
  };
  elements.workpieceCanvas.setPointerCapture(event.pointerId);
  elements.workpieceCanvas.classList.add("is-dragging");
}

function updateViewDrag(event) {
  const sourceDrag = state.sourceDrag;
  if (sourceDrag && sourceDrag.pointerId === event.pointerId) {
    const draft = activeSourceDraft();
    if (!draft) return;
    const projectedX = (event.clientX - sourceDrag.startX) / sourceDrag.scale;
    const projectedY = (event.clientY - sourceDrag.startY) / sourceDrag.scale;
    const rotatedY = projectedY * Math.sin(state.view.pitch);
    const delta = {
      x: projectedX * Math.cos(state.view.yaw) + rotatedY * Math.sin(state.view.yaw),
      y: -projectedX * Math.sin(state.view.yaw) + rotatedY * Math.cos(state.view.yaw),
      z: -projectedY * Math.cos(state.view.pitch),
    };
    ["x", "y", "z"].forEach((axis) => {
      if (sourceDrag.handle === "end") {
        draft.end[axis] = sourceDrag.startEnd[axis] + delta[axis];
      } else {
        draft.center[axis] = sourceDrag.startCenter[axis] + delta[axis];
        if (draft.shape === "line") draft.end[axis] = sourceDrag.startEnd[axis] + delta[axis];
      }
    });
    syncSourceEditor();
    drawWorkpiece();
    return;
  }
  const drag = state.viewDrag;
  if (!drag || drag.pointerId !== event.pointerId) {
    const rectangle = elements.workpieceCanvas.getBoundingClientRect();
    const overSource = Boolean(sourceHandleAt(
      event.clientX - rectangle.left,
      event.clientY - rectangle.top,
    ));
    elements.workpieceCanvas.classList.toggle("is-over-source", overSource);
    return;
  }
  const deltaX = event.clientX - drag.startX;
  const deltaY = event.clientY - drag.startY;
  if (Math.hypot(deltaX, deltaY) > 3) drag.moved = true;
  state.view.yaw = drag.startYaw + deltaX * 0.01;
  state.view.pitch = Math.max(
    -1.25,
    Math.min(1.25, drag.startPitch + deltaY * 0.01),
  );
  drawWorkpiece();
}

function endViewDrag(event) {
  if (state.sourceDrag?.pointerId === event.pointerId) {
    if (elements.workpieceCanvas.hasPointerCapture(event.pointerId)) {
      elements.workpieceCanvas.releasePointerCapture(event.pointerId);
    }
    state.sourceDrag = null;
    state.suppressProbeClick = true;
    elements.workpieceCanvas.classList.remove("is-dragging");
    syncSourceEditor();
    startHeatAnimation();
    return;
  }
  if (!state.viewDrag || state.viewDrag.pointerId !== event.pointerId) return;
  if (elements.workpieceCanvas.hasPointerCapture(event.pointerId)) {
    elements.workpieceCanvas.releasePointerCapture(event.pointerId);
  }
  state.suppressProbeClick = state.viewDrag.moved;
  state.viewDrag = null;
  elements.workpieceCanvas.classList.remove("is-dragging");
}

function pickTemperatureProbe(event) {
  if (state.suppressProbeClick) {
    state.suppressProbeClick = false;
    return;
  }
  const rectangle = elements.workpieceCanvas.getBoundingClientRect();
  const pointer = { x: event.clientX - rectangle.left, y: event.clientY - rectangle.top };
  const boundaryMarker = [...state.boundaryMarkers]
    .sort((first, second) => second.depth - first.depth)
    .find((marker) => Math.hypot(marker.x - pointer.x, marker.y - pointer.y) <= 12);
  if (boundaryMarker?.regionId && !state.comparison) {
    selectGeometryRegion(boundaryMarker.regionId);
    return;
  }
  if (!state.comparison && (!state.result || state.visualizationMode === "model")) {
    pickComponentAt(pointer);
    return;
  }
  if (state.comparison) return;
  if (!isTemperatureVisualization() || !state.temperaturePoints.length) return;
  const nearest = state.temperaturePoints.reduce((current, sample) => {
    const distance = Math.hypot(sample.x - pointer.x, sample.y - pointer.y);
    return !current || distance < current.distance ? { sample, distance } : current;
  }, null);
  state.probe = nearest && nearest.distance <= 28
    ? { world: { ...nearest.sample.world }, temperature: nearest.sample.temperature }
    : null;
  drawWorkpiece();
}

function pickComponentAt(pointer) {
  const hit = [...state.componentHitTriangles]
    .sort((first, second) => second.depth - first.depth)
    .find((triangle) => pointInTriangle(pointer, triangle.points));
  if (!hit) return;
  selectComponent(hit.componentId, Boolean(selectedStudy()?.plan));
}

function pointInTriangle(point, triangle) {
  const [first, second, third] = triangle;
  const sign = (a, b, c) => (a.x - c.x) * (b.y - c.y) - (b.x - c.x) * (a.y - c.y);
  const d1 = sign(point, first, second);
  const d2 = sign(point, second, third);
  const d3 = sign(point, third, first);
  const hasNegative = d1 < 0 || d2 < 0 || d3 < 0;
  const hasPositive = d1 > 0 || d2 > 0 || d3 > 0;
  return !(hasNegative && hasPositive);
}

function rotateViewWithKeyboard(event) {
  if (!hasRotatablePreview()) return;
  const step = Math.PI / 36;
  if (event.key === "ArrowLeft") state.view.yaw -= step;
  else if (event.key === "ArrowRight") state.view.yaw += step;
  else if (event.key === "ArrowUp") state.view.pitch = Math.max(-1.25, state.view.pitch - step);
  else if (event.key === "ArrowDown") state.view.pitch = Math.min(1.25, state.view.pitch + step);
  else return;
  event.preventDefault();
  drawWorkpiece();
}

function drawDimension(context, x1, y1, x2, y2, axis, value) {
  context.save();
  context.strokeStyle = "#8d959d";
  context.fillStyle = "#687078";
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(x1, y1);
  context.lineTo(x2, y2);
  context.stroke();
  const horizontal = Math.abs(y2 - y1) < 1;
  for (const [x, y] of [
    [x1, y1],
    [x2, y2],
  ]) {
    context.beginPath();
    if (horizontal) {
      context.moveTo(x, y - 4);
      context.lineTo(x, y + 4);
    } else {
      context.moveTo(x - 4, y);
      context.lineTo(x + 4, y);
    }
    context.stroke();
  }
  context.font = "600 10px Segoe UI, Microsoft YaHei, sans-serif";
  context.textAlign = "center";
  context.textBaseline = "middle";
  if (horizontal) {
    context.fillText(`${axis.toUpperCase()} ${formatNumber(value)} mm`, (x1 + x2) / 2, y1 + 13);
  } else {
    context.save();
    context.translate(x1 - 12, (y1 + y2) / 2);
    context.rotate(-Math.PI / 2);
    context.fillText(`${axis.toUpperCase()} ${formatNumber(value)} mm`, 0, 0);
    context.restore();
  }
  context.restore();
}

function drawHeatDirection(context, x, y, width, height, plan, axis) {
  const temperatures = Object.fromEntries(
    plan.boundaries.map((boundary) => [boundary.selector, boundary.temperature_k]),
  );
  const minSide = temperatures[`face.${axis}min`];
  const maxSide = temperatures[`face.${axis}max`];
  if (minSide === maxSide) return;
  const leftToRight = minSide > maxSide;
  const startX = leftToRight ? x + 18 : x + width - 18;
  const endX = leftToRight ? x + width - 18 : x + 18;
  const centerY = y + height / 2;
  context.save();
  context.strokeStyle = "rgba(23, 26, 29, 0.72)";
  context.fillStyle = "rgba(23, 26, 29, 0.72)";
  context.lineWidth = 1.5;
  context.beginPath();
  context.moveTo(startX, centerY);
  context.lineTo(endX, centerY);
  context.stroke();
  context.beginPath();
  context.moveTo(endX, centerY);
  context.lineTo(endX + (leftToRight ? -8 : 8), centerY - 5);
  context.lineTo(endX + (leftToRight ? -8 : 8), centerY + 5);
  context.closePath();
  context.fill();
  context.font = "700 9px Segoe UI, Microsoft YaHei, sans-serif";
  context.textAlign = "center";
  context.fillText("热流方向", (startX + endX) / 2, centerY - 10);
  context.restore();
}

function thermalColor(value, low, high, alpha) {
  if (!Number.isFinite(value) || high === low) return `rgba(103, 112, 120, ${alpha})`;
  const fraction = Math.max(0, Math.min(1, (value - low) / (high - low)));
  const stops = [
    [45, 94, 162],
    [42, 157, 143],
    [238, 190, 73],
    [202, 67, 48],
  ];
  const position = fraction * (stops.length - 1);
  const lowerIndex = Math.min(stops.length - 2, Math.floor(position));
  const localFraction = position - lowerIndex;
  const rgb = stops[lowerIndex].map((channel, index) => Math.round(
    channel + (stops[lowerIndex + 1][index] - channel) * localFraction,
  ));
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}

function resultFieldColor(value, low, high, alpha) {
  if (state.comparisonMode !== "difference") {
    return thermalColor(value, low, high, alpha);
  }
  const span = Math.max(Math.abs(low), Math.abs(high), 1e-12);
  const normalized = Math.max(-1, Math.min(1, value / span));
  const endpoint = normalized < 0 ? [45, 94, 162] : [190, 56, 48];
  const fraction = Math.abs(normalized);
  const neutral = [245, 246, 244];
  const channels = neutral.map(
    (channel, index) => Math.round(channel + (endpoint[index] - channel) * fraction),
  );
  return `rgba(${channels[0]}, ${channels[1]}, ${channels[2]}, ${alpha})`;
}

function resolvedDimensions(workpiece) {
  if (workpiece.dimensions_mm) return workpiece.dimensions_mm;
  const bbox = workpiece.geometry?.summary?.bbox;
  if (Array.isArray(bbox) && bbox.length >= 6) {
    return {
      x: Math.max(Math.abs(bbox[3] - bbox[0]), 1),
      y: Math.max(Math.abs(bbox[4] - bbox[1]), 1),
      z: Math.max(Math.abs(bbox[5] - bbox[2]), 1),
    };
  }
  return { x: 100, y: 60, z: 30 };
}

function previewVertices(preview) {
  if (!preview) return [];
  return preview.vertices_mm || preview.vertices_source || [];
}

function longestAxis(dimensions) {
  return Object.entries(dimensions).sort((a, b) => b[1] - a[1])[0][0];
}

function faceLabel(selector) {
  const labels = {
    "face.xmin": "X− 面",
    "face.xmax": "X+ 面",
    "face.ymin": "Y− 面",
    "face.ymax": "Y+ 面",
    "face.zmin": "Z− 面",
    "face.zmax": "Z+ 面",
  };
  return labels[selector] || selector;
}

function createElement(tagName, className = "", text = null) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  if (text !== null) element.textContent = text;
  return element;
}

function formatNumber(value, maximumFractionDigits = 2) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits }).format(Number(value));
}

function formatInteger(value) {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value));
}

function formatScientific(value) {
  if (!Number.isFinite(Number(value))) return "—";
  if (Number(value) === 0) return "0";
  return Number(value).toExponential(2);
}

function formatEngineering(value, unit) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `${formatNumber(value)} ${unit}`;
}

function formatBytes(value) {
  if (!Number.isFinite(Number(value))) return "—";
  const bytes = Number(value);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${formatNumber(bytes / 1024, 1)} KB`;
  return `${formatNumber(bytes / 1024 ** 2, 1)} MB`;
}

function formatDate(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function showToast(message, isError = false) {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-error", isError);
  elements.toast.classList.add("is-visible");
  toastTimer = window.setTimeout(() => elements.toast.classList.remove("is-visible"), 3200);
}

elements.refreshButton.addEventListener("click", () => loadWorkspace());
elements.newWorkpieceButton.addEventListener("click", openWorkpieceDialog);
elements.workpieceSearch.addEventListener("input", renderWorkpieces);
elements.primaryAction.addEventListener("click", handlePrimaryAction);
elements.geometryNav.addEventListener("click", () => switchTab("geometry"));
elements.materialsNav.addEventListener("click", () => switchTab("materials"));
elements.scenarioNav.addEventListener("click", () => switchTab("scenario"));
elements.meshNav.addEventListener("click", () => switchTab("mesh"));
elements.solveNav.addEventListener("click", () => switchTab("solve"));
elements.resultNav.addEventListener("click", () => switchTab("result"));
elements.evaluationNav.addEventListener("click", () => switchTab("evaluation"));
elements.agentNav.addEventListener("click", () => switchTab("agent"));
elements.planTab.addEventListener("click", () => switchTab("solve"));
elements.resultTab.addEventListener("click", () => switchTab("result"));
elements.agentTab.addEventListener("click", () => switchTab("agent"));
elements.modelingForm.addEventListener("submit", sendModelingMessage);
elements.applyModeling.addEventListener("click", () => decideModeling("apply"));
elements.dismissModeling.addEventListener("click", () => decideModeling("dismiss"));
elements.undoModeling.addEventListener("click", () => decideModeling("undo"));
elements.reloadDraft.addEventListener("click", reloadModelingDraft);
[elements.structuredInputs, elements.componentMaterialList, elements.studyPurpose, elements.draftMeshSize].forEach(node => {
  node.addEventListener("input", queueDraftSave);
  node.addEventListener("change", queueDraftSave);
});
[elements.addFixedBoundary, elements.addSurfaceCondition, elements.addThermalContact]
  .forEach(node => node.addEventListener("click", queueDraftSave));
[elements.fixedBoundaryRows, elements.surfaceConditionRows, elements.thermalContactRows].forEach(node => node.addEventListener("click", event => {
  if (event.target.closest("button")) queueDraftSave();
}));
window.addEventListener("beforeunload", event => {
  if (state.draftDirty || state.draftSavePromise) { event.preventDefault(); event.returnValue = ""; }
});
elements.unitForm.addEventListener("submit", confirmUnit);
elements.lengthUnit.addEventListener("change", updateUnitDimensions);
elements.studyDraftForm.addEventListener("submit", generateStudyDraft);
elements.draftAnalysisType.addEventListener("change", syncTransientFields);
elements.timePosition.addEventListener("input", () => {
  scheduleTimeStep(Number(elements.timePosition.value));
});
elements.timeLockScale.addEventListener("change", drawWorkpiece);
elements.timePlay.addEventListener("click", async () => {
  cancelScheduledTimeStep();
  if (state.timePlaying) {
    stopTimePlayback();
  } else {
    if (state.timeIndex >= (state.result?.time_steps?.length || 0) - 1) {
      if (!await selectTimeStep(0)) return;
    }
    state.timePlaying = true;
    state.timeTimer = setTimeout(playNextTimeStep, 100);
  }
  renderTimeControls();
});
elements.confirmInputs.addEventListener("change", renderPrimaryAction);
elements.confirmMaterials.addEventListener("change", () => {
  state.materialsConfirmedFor = elements.confirmMaterials.checked
    ? selectedStudy()?.study_id || null
    : null;
  elements.materialConfirmationCheck.classList.toggle(
    "is-confirmed",
    elements.confirmMaterials.checked,
  );
  renderPrimaryAction();
});
elements.cancelComputation.addEventListener("click", cancelComputation);
elements.acceptMeshWarnings.addEventListener("change", renderPrimaryAction);
elements.diagnosticsButton.addEventListener("click", () => elements.diagnosticsDialog.showModal());
elements.closeDiagnosticsButton.addEventListener("click", () => elements.diagnosticsDialog.close());
elements.agentForm.addEventListener("submit", runAgent);
elements.projectSidebarTab.addEventListener("click", () => setSidebarView("project"));
elements.assistantSidebarTab.addEventListener("click", () => setSidebarView("assistant"));
elements.assistantForm.addEventListener("submit", submitAssistantTurn);
elements.assistantQuestionForm.addEventListener("submit", submitAssistantAnswers);
elements.assistantRetry.addEventListener("click", (event) => submitAssistantInput(event, {retry: true}));
elements.assistantQuestionForm.addEventListener("change", (event) => {
  const session = state.assistantSession;
  const questions = session?.questions || [];
  if (
    state.assistantBusy
    || event.target.type !== "radio"
    || questions.length !== 1
    || questions[0].type !== "single_choice"
  ) return;
  requestAnimationFrame(() => elements.assistantQuestionForm.requestSubmit());
});
elements.assistantInput.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  if (!elements.assistantSend.disabled) elements.assistantForm.requestSubmit();
});
elements.assistantLaunch.addEventListener("click", launchAssistant);
elements.assistantOpenStudyForm.addEventListener("click", openAssistantStudyForm);
elements.assistantSummaryConfirmed.addEventListener("change", renderAssistant);
elements.assistantMaterialsConfirmed.addEventListener("change", renderAssistant);
elements.assistantNew.addEventListener("click", () => {
  state.assistantRequestGeneration += 1;
  state.assistantBusy = false;
  state.assistantPendingMessage = "";
  state.assistantStreamAnswer = "";
  state.assistantStreamStatus = "";
  state.assistantSession = null;
  state.assistantSessionId = null;
  elements.assistantInput.value = "";
  persistWorkspaceState();
  renderAssistant();
});
elements.assistantRail?.addEventListener("toggle", () => {
  state.assistantCollapsed = !elements.assistantRail.open;
  persistWorkspaceState();
});
elements.assistantHistory.addEventListener("change", async () => {
  const sessionId = elements.assistantHistory.value;
  if (!sessionId) {
    state.assistantSession = null;
    state.assistantSessionId = null;
    persistWorkspaceState();
    renderAssistant();
    return;
  }
  state.assistantSessionId = sessionId;
  await loadWorkspace();
});
elements.agentSelectedStudy.addEventListener("click", () => {
  const studyId = elements.agentSelectedStudy.dataset.studyId;
  if (studyId) selectStudy(studyId, {
    activeTab: "result",
    visualizationMode: "thermal",
    announce: true,
  });
});
elements.closeDialogButton.addEventListener("click", closeWorkpieceDialog);
elements.cancelDialogButton.addEventListener("click", closeWorkpieceDialog);
elements.autoModeButton.addEventListener("click", () => setParameterMode("auto"));
elements.customModeButton.addEventListener("click", () => setParameterMode("custom"));
elements.workpieceForm.addEventListener("submit", submitWorkpiece);
elements.componentRenameForm.addEventListener("submit", renameSelectedComponent);
elements.closeComponentDialogButton.addEventListener("click", closeComponentDialog);
elements.cancelComponentRenameButton.addEventListener("click", closeComponentDialog);
elements.copyStudyButton.addEventListener("click", copySelectedStudy);
elements.compareStudiesButton.addEventListener("click", compareSelectedStudies);
elements.exitComparisonButton.addEventListener("click", () => {
  clearComparison();
  renderComparisonTool();
  renderWorkspace();
  drawWorkpiece();
});
elements.comparisonCandidate.addEventListener("change", () => {
  clearComparison();
  renderComparisonTool();
  drawWorkpiece();
});
elements.comparisonBaselineMode.addEventListener("click", () => setComparisonMode("baseline"));
elements.comparisonCandidateMode.addEventListener("click", () => setComparisonMode("candidate"));
elements.comparisonDifferenceMode.addEventListener("click", () => setComparisonMode("difference"));
elements.cadFile.addEventListener("change", () => {
  elements.fileLabel.textContent = elements.cadFile.files[0]?.name || "选择 STL 文件";
});
elements.workpieceDialog.addEventListener("click", (event) => {
  if (event.target === elements.workpieceDialog) closeWorkpieceDialog();
});
elements.resetViewButton.addEventListener("click", () => resetStlView());
elements.modelViewButton.addEventListener("click", () => setVisualizationMode("model"));
elements.meshViewButton.addEventListener("click", () => setVisualizationMode("mesh"));
elements.diffusionViewButton.addEventListener("click", () => setVisualizationMode("diffusion"));
elements.thermalViewButton.addEventListener("click", () => setVisualizationMode("thermal"));
elements.heatFluxViewButton.addEventListener("click", () => setVisualizationMode("flux"));
elements.contourViewButton.addEventListener("click", () => setVisualizationMode("contour"));
elements.sliceViewButton.addEventListener("click", () => setVisualizationMode("slice"));
elements.sliceAxis.addEventListener("change", () => {
  state.sliceAxis = elements.sliceAxis.value;
  drawWorkpiece();
});
elements.slicePosition.addEventListener("input", () => {
  state.sliceFraction = Number(elements.slicePosition.value) / 100;
  drawWorkpiece();
});
elements.sourceEditButton.addEventListener("click", () => {
  if (elements.sourceEditor.hidden) openSourceEditor();
  else closeSourceEditor();
});
elements.closeSourceEditorButton.addEventListener("click", closeSourceEditor);
elements.resetSourceButton.addEventListener("click", resetSourceEditor);
elements.addHeatSource.addEventListener("click", addHeatSource);
elements.deleteHeatSource.addEventListener("click", deleteHeatSource);
elements.sourceList.addEventListener("click", event => {
  const deleteButton = event.target.closest("[data-source-delete]");
  if (deleteButton) {
    deleteHeatSource(Number(deleteButton.dataset.sourceDelete));
    return;
  }
  const button = event.target.closest("[data-source-index]");
  if (button) selectHeatSource(Number(button.dataset.sourceIndex));
});
elements.sourceEditor.addEventListener("submit", applySourceChanges);
[
  elements.canvasSourceName,
  elements.canvasSourceShape,
  elements.canvasSourcePlacement,
  elements.canvasSourceDepth,
  elements.canvasSourcePower,
  elements.canvasSourceRadius,
  elements.canvasSourceX,
  elements.canvasSourceY,
  elements.canvasSourceZ,
  elements.canvasSourceEndX,
  elements.canvasSourceEndY,
  elements.canvasSourceEndZ,
  elements.canvasSourceSurfaceAxis,
  elements.canvasSourceWidth,
  elements.canvasSourceHeight,
  elements.canvasSourceThickness,
  elements.canvasSourceVolumeWidth,
  elements.canvasSourceVolumeHeight,
  elements.canvasSourceVolumeDepth,
  elements.canvasAmbientTemperature,
  elements.canvasConvectionCoefficient,
  elements.canvasDuration,
  elements.canvasTimeStep,
].forEach((input) => input.addEventListener("input", updateSourceDraftFromInputs));
elements.heatSourceShape.addEventListener("change", updateUploadSourceFields);
elements.heatSourcePlacement.addEventListener("change", updateUploadSourceFields);
document.getElementById("standardView").addEventListener("change", event => engineeringViewport?.fit(event.target.value));
document.getElementById("geometryOpacity").addEventListener("input", drawWorkpiece);
async function focusMaximumResult() {
  if (!state.result || !engineeringViewport) return;
  state.visualizationMode = "thermal";
  await drawWorkpiece();
  const point = engineeringViewport?.surface?.maximum_position_mm;
  const bbox = canvasStudyContext().workpiece?.geometry?.summary?.bbox;
  if (!point || !bbox) return;
  state.sliceAxis = "x";
  state.sliceFraction = Math.max(0, Math.min(1, (point[0] - bbox[0]) / (bbox[3] - bbox[0])));
  elements.sliceAxis.value = "x";
  elements.slicePosition.value = String(state.sliceFraction * 100);
  state.visualizationMode = "slice";
  elements.sliceControls.hidden = false;
  syncVisualizationModeButtons();
  await drawWorkpiece();
  engineeringViewport.focus(point);
}
document.getElementById("focusMaximum").addEventListener("click", focusMaximumResult);
elements.resultHotspot.addEventListener("click", focusMaximumResult);
const viewportStatus = document.getElementById("viewportStatus");
const showViewportStatus = (message, error = false) => {
  viewportStatus.textContent = message;
  viewportStatus.hidden = !message;
  viewportStatus.classList.toggle("is-error", error);
};
import("/assets/viewport.mjs?v=20260909-flow7").then(({ EngineeringViewport }) => {
  engineeringViewport = new EngineeringViewport(elements.workpieceCanvas, {
    status: showViewportStatus,
    component: id => selectComponent(id, true),
    region: selectGeometryRegion,
    face: surfaceFaceSelected,
    loaded: surface => {
      document.getElementById("focusMaximum").disabled = !surface.maximum_position_mm;
      if (surface.maximum_position_mm && state.result) {
        elements.resultHotspot.textContent = `(${surface.maximum_position_mm.map(value => formatNumber(value)).join(", ")}) mm`;
      }
    },
    legend: ({ hasField, low, high, flux, time, difference, bands, preview, locked,
      diffusion, referenceTemperatureK, uniform }) => {
      elements.thermalLegend.hidden = !hasField;
      elements.thermalLegend.classList.toggle("is-difference", difference);
      elements.diffusionReference.hidden = !diffusion;
      elements.diffusionReference.textContent = diffusion
        ? `仅显示温差，不是绝对温度。当前最低温度 ${formatNumber(referenceTemperatureK - 273.15)} ℃`
        : "";
      elements.thermalLegendTitle.textContent = (diffusion
        ? `空间温差 · Δ℃ · 全时段固定${uniform ? " · 近似等温" : ""}`
        : difference ? "相对基准温差 · Δ℃" : flux ? "热流密度 · W/m²"
        : locked ? (bands ? "全时段固定温度等值带 · ℃" : "真实温度 · ℃ · 全时段固定")
        : bands ? "温度等值带 · ℃" : "真实温度 · ℃")
        + (preview ? " · 近似预览" : "") + (time == null ? "" : ` · ${formatNumber(time)} s`);
      const temperatureLegend = ThermoFlowTransient.legendValues(low, high, difference || diffusion);
      [elements.coldTemperature, elements.midTemperature, elements.hotTemperature].forEach((element, index) => {
        const value = low + (high - low) * index / 2;
        element.textContent = flux ? formatScientific(value)
          : `${formatNumber(temperatureLegend.values[index])} ${temperatureLegend.unit}`;
      });
    },
    sourceSelect: sourceIndex => {
      if (state.modelingDecisionBusy) return;
      selectHeatSource(sourceIndex, { readInputs: false, openEditor: true });
    },
    source: (handle, position, sourceIndex = null) => {
      if (state.modelingDecisionBusy) return;
      const collection = activeSourceCollection();
      if (collection && Number.isInteger(sourceIndex)) {
        collection.activeIndex = Math.max(0, Math.min(sourceIndex, collection.sources.length - 1));
      }
      const draft = activeSourceDraft();
      if (!draft) return;
      const target = handle === "end" ? draft.end : draft.center;
      ["x", "y", "z"].forEach((axis, index) => {
        if (handle === "center" && draft.shape === "line") draft.end[axis] += position[index] - target[axis];
        target[axis] = position[index];
      });
      state.sourcePreviewDirty = true;
      queueDraftSave();
      openSourceEditor();
      syncSourceEditor();
      drawWorkpiece();
    },
    probe: async (cellId, fingerprint, options) => {
      const ticket = ++probeRequestTicket;
      const studyId = options.study.study_id;
      const query = options.frameIndex == null ? "" : `?frame_index=${options.frameIndex}`;
      try {
        const probe = await request(`/v1/studies/${studyId}/cells/${cellId}/probe${query}`);
        if (ticket !== probeRequestTicket || selectedStudy()?.study_id !== studyId
          || engineeringViewport.surface?.source_sha256 !== fingerprint) return;
        if (probe.source_sha256 !== fingerprint) throw new Error("探针与当前场数据不一致，请重新打开结果");
        state.probe = { world: Object.fromEntries(["x", "y", "z"].map((axis, i) => [axis, probe.position_mm[i]])), temperature: probe.temperature.value };
        if (probe.component_id) selectComponent(probe.component_id);
        elements.probeReadout.hidden = false;
        elements.probeReadout.textContent = `单元中心 ${formatNumber(probe.temperature.value)} K`
          + (probe.heat_flux_w_m2 ? ` · 热流 ${formatScientific(Math.hypot(...probe.heat_flux_w_m2))} W/m²` : "")
          + ` · (${probe.position_mm.map(value => formatNumber(value)).join(", ")}) mm`
          + (probe.time_s == null ? "" : ` · ${formatNumber(probe.time_s)} s`);
        drawWorkpiece();
      } catch (error) { if (ticket === probeRequestTicket) showToast(error.message, true); }
    },
  });
  drawWorkpiece();
}).catch(() => showViewportStatus("无法启动 WebGL 三维画布，请检查浏览器图形加速支持", true));
setSidebarView(state.sidebarView, {persist: false});
loadWorkspace();
