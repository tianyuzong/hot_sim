import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("src/thermoflow/web/assets/app.js", "utf8");
const definition = (name, next) => {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf(`function ${next}(`, start);
  assert(start >= 0 && end > start, `Could not extract ${name}`);
  return source.slice(start, end);
};
const asyncDefinition = (name, next) => {
  const start = source.indexOf(`async function ${name}(`);
  const end = source.indexOf(`async function ${next}(`, start);
  assert(start >= 0 && end > start, `Could not extract ${name}`);
  return source.slice(start, end);
};

const snapshots = [];
const submitted = [{question_id: "geometry_unit", option_ids: ["mm"]}];
let finishStream;
const completedSession = {
  session_id: "assistant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  revision: 2,
  study_id: "study-a",
};
const state = {
  assistantBusy: false,
  assistantPendingMessage: "",
  assistantPendingAnswers: [],
  assistantStreamAnswer: "",
  assistantStreamStatus: "",
  assistantRequestGeneration: 0,
  assistantSession: {
    session_id: completedSession.session_id,
    revision: 1,
    workpiece_id: "wp-a",
    questions: [{question_id: "geometry_unit"}],
  },
  assistantSessionId: completedSession.session_id,
  assistantSessions: [],
  selectedStudyId: "study-a",
  draftSyncedFor: "study-a",
  draftSaveTimer: 42,
  draftDirty: true,
  draftSaveError: true,
  draftEditSerial: 7,
  materialsSyncedFor: "study-a:needs_input",
  materialsConfirmedFor: "study-a",
};
const elements = {assistantInput: {value: ""}};
let invalidatedStudyId = null;
const context = vm.createContext({
  state,
  elements,
  clearTimeout: () => {},
  assistantQuestionAnswers: () => submitted,
  showToast: message => { throw new Error(message); },
  renderAssistant: () => snapshots.push({
    busy: state.assistantBusy,
    answers: state.assistantPendingAnswers,
  }),
  streamAssistant: async () => new Promise(resolve => { finishStream = resolve; }),
  request: async () => { throw new Error("Unexpected request"); },
  persistWorkspaceState() {},
  loadWorkspace: async () => {},
  invalidateAgentStudyForm: studyId => { invalidatedStudyId = studyId; },
  discardSourceDraft() {},
});

vm.runInContext(definition("pendingAssistantAnswer", "invalidateAgentStudyForm"), context);
vm.runInContext(asyncDefinition("submitAssistantInput", "launchAssistant"), context);

const turn = context.submitAssistantInput({preventDefault() {}}, {answersOnly: true});
await new Promise(resolve => setImmediate(resolve));
assert.equal(
  context.pendingAssistantAnswer("geometry_unit")?.option_ids?.[0],
  "mm",
  "A submitted choice must remain available while the Agent response streams",
);
assert.deepEqual(
  snapshots[0],
  {busy: true, answers: submitted},
  "The first rendering pass keeps the chosen option selected and disables duplicate input",
);
finishStream(completedSession);
await turn;
assert.equal(state.assistantPendingAnswers.length, 0, "The transient choice clears after the next turn completes");
assert.equal(invalidatedStudyId, "study-a", "An Agent-updated study invalidates its stale form cache");

const beforeBusyAttempt = snapshots.length;
state.assistantBusy = true;
await context.submitAssistantInput({preventDefault() {}}, {answersOnly: true});
assert.equal(snapshots.length, beforeBusyAttempt, "A busy Agent cannot submit the same choice twice");

assert.match(
  source,
  /input\.checked = Boolean\(pendingAnswer\?\.option_ids\?\.includes\(option\.option_id\)\)/,
  "The question renderer must rehydrate the submitted radio or checkbox state",
);
console.log("Assistant question selection survives streaming rerenders and duplicate submission is blocked");
