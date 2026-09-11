"""Exercise the real GLM HTTP adapter with provider responses isolated by MockTransport."""
import json

import httpx
import openai
import pytest
import trimesh
from fastapi.testclient import TestClient

from tests.test_confirmed_service import _service
from thermoflow.api import create_app
from thermoflow.glm_modeling import GLMModelingPlanner
from thermoflow.materials import list_materials
from thermoflow.modeling import ModelingService
from thermoflow.models import (
    DraftUpdateRequest,
    LengthUnit,
    ModelingDecisionRequest,
    ModelingMessageRequest,
    Point3DMM,
    SimulationOverrides,
    StudyConfirmationRequest,
    VolumetricHeatSource,
)
from thermoflow.planner import DeterministicPlanner, PlannerUnavailableError
from thermoflow.settings import Settings


def settings(tmp_path):
    return Settings(project_root=tmp_path, data_dir=tmp_path / "data",
                    cadflow_repo=tmp_path / "no-cadflow", planner_mode="deterministic",
                    compute_backend="cpu", modeling_provider="glm")


def setup(tmp_path):
    service, repo = _service(tmp_path)
    workpiece = service.register_stl("agent-test.stl",
        trimesh.creation.box(extents=[10, 6, 4]).export(file_type="stl"))
    workpiece = service.confirm_workpiece_unit(workpiece.workpiece_id, LengthUnit.MILLIMETER)
    study = service.create_study(workpiece.workpiece_id, require_confirmation=True, planning_mode="manual")
    planner = GLMModelingPlanner(settings(tmp_path), api_key="test-only")
    return service, repo, workpiece, study, planner, ModelingService(service, planner=planner)


def mock_provider(monkeypatch, turn, *, status=200, finish="stop"):
    observed = {}
    factory = openai.OpenAI

    def respond(request):
        observed["url"] = str(request.url)
        observed["request"] = json.loads(request.content)
        content = turn if isinstance(turn, str) else json.dumps(turn, ensure_ascii=False)
        body = {"id": "glm-test-response", "object": "chat.completion", "created": 1,
                "model": "glm-5.2", "choices": [{"index": 0, "finish_reason": finish,
                    "message": {"role": "assistant", "content": content}}]}
        if status != 200:
            body = {"error": {"message": "secret-token /private/path", "code": "test"}}
        return httpx.Response(status, json=body)

    def client(**kwargs):
        observed["options"] = {key: value for key, value in kwargs.items() if key != "api_key"}
        return factory(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond)))

    monkeypatch.setattr(openai, "OpenAI", client)
    return observed


def send(modeling, study, message="初始温度 25 ℃，持续 120 秒，时间步 1 秒"):
    return modeling.message(study.study_id, ModelingMessageRequest(
        expected_revision=study.draft_revision, message=message))


def decide(modeling, study, action):
    return modeling.decide(study.study_id, ModelingDecisionRequest(
        expected_revision=study.draft_revision, action=action))


def test_glm_proposes_applies_and_undoes_without_solving(tmp_path, monkeypatch):
    service, repo, _, study, _, modeling = setup(tmp_path)
    observed = mock_provider(monkeypatch, {"reply": "已整理温度与时间，请检查热源位置。",
        "overrides": {"initial_temperature_k": 298.15, "duration_s": 120, "time_step_s": 1},
        "questions": ["热源具体放在哪个位置？"], "missing_information": ["热源位置待确认"]})
    proposed = send(modeling, study)
    assert proposed.plan == study.plan
    assert proposed.modeling.proposal.plan.initial_temperature_k == 298.15
    assert proposed.modeling.proposal.plan.heat_sources == study.plan.heat_sources
    assert proposed.modeling.questions == ["热源具体放在哪个位置？"]
    assert proposed.planner.provider == "glm" and proposed.planner.response_id == "glm-test-response"
    assert observed["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert observed["request"]["response_format"] == {"type": "json_object"}
    assert observed["request"]["thinking"] == {"type": "disabled"}
    assert observed["options"]["timeout"] == 60 and observed["options"]["max_retries"] == 0
    assert "tools" not in observed["request"]
    with pytest.raises(ValueError, match="应用或放弃"):
        service.confirm_study(study.study_id, StudyConfirmationRequest(materials_confirmed=True))
    applied = decide(modeling, proposed, "apply")
    assert applied.plan.duration_s == 120 and applied.plan.time_step_s == 1
    assert applied.confirmation.status == "needs_input" and applied.input_snapshot_sha256 is None
    assert applied.mesh_status == "not_generated" and applied.mesh_snapshot_sha256 is None
    assert applied.active_task_id is None and applied.evaluation_status == "not_evaluated"
    undone = decide(modeling, applied, "undo")
    assert undone.plan == study.plan
    assert repo.get_study(study.study_id).plan == study.plan


def test_question_only_turn_allows_follow_up_and_preserves_draft(tmp_path, monkeypatch):
    _, repo, _, study, _, modeling = setup(tmp_path)
    observed = mock_provider(monkeypatch, {"reply": "需要先明确环境温度。",
        "questions": ["环境温度是多少 ℃？"], "missing_information": ["环境温度"]})
    first = send(modeling, study, "室温下散热")
    assert first.modeling.proposal is None
    assert first.plan == study.plan and repo.get_study(study.study_id).plan == study.plan
    second = send(modeling, first, "环境温度为 25 ℃")
    payload = json.loads(observed["request"]["messages"][1]["content"])
    assert len(payload["conversation"]) == 3
    assert payload["conversation"][-1]["content"] == "环境温度为 25 ℃"
    assert second.modeling.proposal is None


def test_sparse_updates_preserve_other_sources_and_regions(tmp_path, monkeypatch):
    _, _, _, study, _, modeling = setup(tmp_path)
    # Manual drafts deliberately start without an invented heat source.
    first = VolumetricHeatSource(center_mm=Point3DMM(x=0, y=0, z=0),
                                 total_power_w=10, radius_mm=1)
    second = first.model_copy(update={"total_power_w": 7})
    study = modeling.update_draft(study.study_id, DraftUpdateRequest(expected_revision=0,
        overrides=SimulationOverrides(heat_sources=[first, second])))
    mock_provider(monkeypatch, {"reply": "仅修改第一个热源为 20 W。",
        "overrides": {"heat_source_power_w": 20}})
    proposed = send(modeling, study, "仅把第一个热源功率改成 20 W")
    after = proposed.modeling.proposal.plan
    assert after.heat_sources[0].total_power_w == 20
    assert after.heat_sources[1] == second
    assert after.surface_conditions == study.plan.surface_conditions
    assert after.mesh == study.plan.mesh and after.material == study.plan.material


def test_changing_to_transient_never_invents_time_values(tmp_path, monkeypatch):
    _, _, _, study, _, modeling = setup(tmp_path)
    study = modeling.update_draft(study.study_id, DraftUpdateRequest(expected_revision=0,
        overrides=SimulationOverrides(analysis_type="steady_state_conduction")))
    mock_provider(monkeypatch, {"reply": "切换瞬态，请补充时间与初温。",
        "overrides": {"analysis_type": "transient_conduction"},
        "questions": ["初始温度、持续时间和时间步长分别是多少？"]})
    proposed = send(modeling, study, "改成瞬态")
    plan = proposed.modeling.proposal.plan
    assert plan.initial_temperature_k is None and plan.duration_s is None and plan.time_step_s is None
    assert proposed.modeling.proposal.validation_errors


@pytest.mark.parametrize("turn", [
    {"reply": "执行", "overrides": {"shell_command": "bad"}},
    {"reply": "确认", "confirmation": {"status": "confirmed"}},
    {"reply": "不合法", "overrides": {"duration_s": -2}},
    {"reply": "伪造材料", "overrides": {"thermal_conductivity_w_m_k": 100}},
    {"reply": "伪造目录", "catalog_material_id": "unknown"},
    {"reply": "伪造组件", "material_assignments": [{"component_id": "unknown", "catalog_material_id": "al-6061-t6"}]},
    "not json",
])
def test_invalid_output_does_not_touch_saved_draft(tmp_path, monkeypatch, turn):
    _, repo, _, study, _, modeling = setup(tmp_path)
    mock_provider(monkeypatch, turn)
    with pytest.raises(PlannerUnavailableError):
        send(modeling, study)
    assert repo.get_study(study.study_id) == study


@pytest.mark.parametrize("status, expected", [(401, "认证"), (429, "限流"), (503, "服务")])
def test_provider_errors_are_sanitized_and_preserve_draft(tmp_path, monkeypatch, status, expected):
    _, repo, _, study, _, modeling = setup(tmp_path)
    mock_provider(monkeypatch, {}, status=status)
    with pytest.raises(PlannerUnavailableError, match=expected) as error:
        send(modeling, study)
    assert "secret-token" not in str(error.value) and "/private" not in str(error.value)
    assert repo.get_study(study.study_id) == study


def test_truncated_response_is_not_applied(tmp_path, monkeypatch):
    _, repo, _, study, _, modeling = setup(tmp_path)
    mock_provider(monkeypatch, {"reply": "修改", "overrides": {"duration_s": 10}}, finish="length")
    with pytest.raises(PlannerUnavailableError, match="完整"):
        send(modeling, study)
    assert repo.get_study(study.study_id) == study


def test_catalog_materials_resolve_server_side(tmp_path, monkeypatch):
    _, _, _, study, _, modeling = setup(tmp_path)
    entry = list_materials()[1]
    mock_provider(monkeypatch, {"reply": "使用指定的目录材料。", "catalog_material_id": entry.material_id})
    proposed = send(modeling, study, "使用紫铜")
    plan = proposed.modeling.proposal.plan
    assert plan.material == entry.material
    assert all(item.material_id == entry.material_id for item in plan.component_materials)
    assert plan.confirmation.status == "needs_input"


def test_concurrent_form_edit_is_not_overwritten(tmp_path, monkeypatch):
    _, repo, _, study, planner, modeling = setup(tmp_path)
    mock_provider(monkeypatch, {"reply": "温度 25 ℃", "overrides": {"initial_temperature_k": 298.15}})
    original = planner.plan
    def racing(*args, **kwargs):
        modeling.update_draft(study.study_id, DraftUpdateRequest(expected_revision=0, purpose="用户并发修改"))
        return original(*args, **kwargs)
    monkeypatch.setattr(planner, "plan", racing)
    with pytest.raises(ValueError, match="其他操作"):
        send(modeling, study)
    assert repo.get_study(study.study_id).purpose == "用户并发修改"
    assert repo.get_study(study.study_id).modeling.proposal is None


def test_missing_and_encrypted_credentials_are_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    planner = GLMModelingPlanner(settings(tmp_path))
    assert not planner.status()["configured"]
    monkeypatch.setenv("ZHIPU_API_KEY", "enc:v1:encrypted-test-only")
    assert not planner.status()["configured"]
    monkeypatch.delenv("ZHIPU_API_KEY")
    (tmp_path / ".env").write_text("ZHIPU_API_KEY=test-only\n", encoding="utf-8")
    assert planner.status()["configured"]
    assert "test-only" not in json.dumps(planner.status())


def test_api_uses_independent_agent_and_keeps_manual_workflow(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    client = TestClient(create_app(settings(tmp_path), DeterministicPlanner()))
    health = client.get("/health").json()
    assert health["planner_mode"] == "deterministic"
    assert health["modeling_agent"]["provider"] == "glm"
    assert health["modeling_agent"]["configured"] is False
    part = client.post("/v1/workpieces/boxes", json={"name": "API test", "dimensions_mm": {"x": 10, "y": 6, "z": 4}}).json()
    created = client.post("/v1/studies", json={"workpiece_id": part["workpiece_id"], "require_confirmation": True})
    assert created.status_code == 201
    study = created.json()
    response = client.post(f"/v1/studies/{study['study_id']}/modeling/messages", json={"expected_revision": 0, "message": "温度 25 ℃"})
    assert response.status_code == 503 and "API Key" in response.json()["detail"]
    assert client.get(f"/v1/studies/{study['study_id']}").json() == study


def test_config_accepts_glm_and_rejects_invalid_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("THERMOFLOW_MODELING_PROVIDER", "glm")
    monkeypatch.setenv("THERMOFLOW_GLM_MODEL", "glm-5.2")
    assert Settings.from_env(tmp_path).modeling_provider == "glm"
    monkeypatch.setenv("THERMOFLOW_GLM_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError, match="超时"):
        Settings.from_env(tmp_path)
