from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from thermoflow.modeling import ModelingService
from thermoflow.models import (
    BoxWorkpieceInput, DimensionsMM, DraftUpdateRequest, ModelingDecisionRequest,
    ModelingMessage, ModelingMessageRequest, SimulationOverrides, StudyConfirmationRequest,
    LengthUnit, ComponentUpdateRequest,
)
from thermoflow.planner import DeterministicPlanner, OpenAIPlanner, PlannerUnavailableError
from tests.test_confirmed_service import _service
from tests.test_confirmed_workflow import _client


def setup_draft(tmp_path, *, retain_history=True):
    service, repository = _service(tmp_path)
    part = service.register_box(BoxWorkpieceInput(name="Modeling reference", dimensions_mm=DimensionsMM(x=10, y=6, z=4)))
    study = service.create_study(part.workpiece_id, require_confirmation=True, purpose="温度验证")
    return service, repository, ModelingService(service, retain_history=retain_history), study


def message(modeling, study, text="最高温度不超过 400 K"):
    return modeling.message(study.study_id, ModelingMessageRequest(expected_revision=study.draft_revision, message=text))


def decide(modeling, study, action):
    return modeling.decide(study.study_id, ModelingDecisionRequest(expected_revision=study.draft_revision, action=action))


def test_proposal_apply_undo_and_separate_confirmation(tmp_path):
    service, repo, modeling, base = setup_draft(tmp_path)
    proposed = message(modeling, base)
    assert proposed.plan == base.plan
    assert proposed.input_snapshot_sha256 is None
    assert proposed.modeling.proposal.plan.criteria[0].target.value == 400
    change = next(c for c in proposed.modeling.proposal.changes if c.field == "criteria")
    assert "400 K" in change.after and "max_temperature" not in change.after
    assert len(repo.get_study(base.study_id).modeling.messages) == 2
    with pytest.raises(ValueError, match="应用或放弃"):
        service.confirm_study(
            base.study_id, StudyConfirmationRequest(materials_confirmed=True)
        )
    with pytest.raises(ValueError):
        service.generate_mesh(base.study_id)
    applied = decide(modeling, proposed, "apply")
    assert applied.plan.criteria[0].target.value == 400
    assert applied.confirmation.status == "needs_input"
    assert applied.input_snapshot_sha256 is None and applied.modeling.proposal is None
    assert "criteria" in repo.get_study(base.study_id).modeling.suggested_fields
    undone = decide(modeling, applied, "undo")
    assert undone.plan == base.plan
    assert undone.modeling.suggested_fields == []
    again = decide(modeling, message(modeling, undone), "apply")
    confirmed = service.confirm_study(
        again.study_id,
        StudyConfirmationRequest(
            expected_revision=again.draft_revision,
            materials_confirmed=True,
        ),
    )
    assert confirmed.input_snapshot_sha256
    assert confirmed.modeling.undo_plan is None
    with pytest.raises(ValueError, match="未确认"):
        message(modeling, confirmed)


def test_form_saves_context_and_revision_conflicts_without_confirmation(tmp_path):
    service, repo, modeling, base = setup_draft(tmp_path)
    request = DraftUpdateRequest(expected_revision=0, purpose="用户修改的用途",
        overrides=SimulationOverrides(min_face_temperature_k=310))
    saved = modeling.update_draft(base.study_id, request)
    assert saved.plan.boundaries[0].temperature_k == 310
    assert saved.simulation_spec.confirmation.status == "needs_input"
    noop = modeling.update_draft(base.study_id, request.model_copy(update={"expected_revision": saved.draft_revision}))
    assert noop.draft_revision == saved.draft_revision
    with pytest.raises(ValueError, match="其他操作"):
        modeling.update_draft(base.study_id, request)
    proposed = message(modeling, saved)
    assert proposed.modeling.proposal.plan.boundaries[0].temperature_k == 310
    assert proposed.modeling.proposal.plan.purpose == "用户修改的用途"
    edited = modeling.update_draft(base.study_id, DraftUpdateRequest(expected_revision=proposed.draft_revision,
        overrides=SimulationOverrides(max_face_temperature_k=420)))
    assert edited.modeling.proposal is None
    assert repo.get_study(base.study_id).plan.boundaries[-1].temperature_k == 420
    with pytest.raises(ValueError):
        decide(modeling, proposed, "apply")
    with pytest.raises(ValueError, match="已更新"):
        service.confirm_study(
            base.study_id,
            StudyConfirmationRequest(expected_revision=0, materials_confirmed=True),
        )


def test_late_model_response_cannot_overwrite_newer_form_edits(tmp_path):
    service, repo, modeling, base = setup_draft(tmp_path)
    class RacingPlanner(DeterministicPlanner):
        def plan(self, *args, **kwargs):
            modeling.update_draft(base.study_id, DraftUpdateRequest(expected_revision=0, purpose="并发编辑"))
            return super().plan(*args, **kwargs)
    service.planner = RacingPlanner()
    with pytest.raises(ValueError, match="其他操作"):
        message(modeling, base)
    current = repo.get_study(base.study_id)
    assert current.purpose == "并发编辑" and current.modeling.proposal is None


def test_unavailable_model_preserves_input_and_does_not_leak_exception(tmp_path):
    service, repo, modeling, base = setup_draft(tmp_path)
    class BrokenPlanner:
        def plan(self, *args, **kwargs):
            raise RuntimeError("secret /private/path sk-example")
    service.planner = BrokenPlanner()
    with pytest.raises(PlannerUnavailableError) as error:
        message(modeling, base)
    assert "secret" not in str(error.value) and "/private" not in str(error.value)
    assert repo.get_study(base.study_id) == base


def test_model_material_claims_require_existing_data(tmp_path):
    service, repo, modeling, base = setup_draft(tmp_path)
    class InventingPlanner(DeterministicPlanner):
        def plan(self, *args, **kwargs):
            result = super().plan(*args, **kwargs)
            result.plan.material.thermal_conductivity_w_m_k = 999
            result.plan.material.source_type = "database"
            return result
    service.planner = InventingPlanner()
    with pytest.raises(ValueError, match="无法核验"):
        message(modeling, base)
    assert repo.get_study(base.study_id).plan.material == base.plan.material


def test_history_retention_can_be_disabled_while_current_session_is_supplied(tmp_path):
    service, repo, modeling, base = setup_draft(tmp_path, retain_history=False)
    proposed = message(modeling, base)
    assert len(proposed.modeling.messages) == 2 and not proposed.modeling.history_retained
    assert repo.get_study(base.study_id).modeling.messages == []
    dismissed = decide(modeling, proposed, "dismiss")
    seen = []
    class Recorder(DeterministicPlanner):
        def plan(self, *args, **kwargs):
            seen.extend(kwargs["conversation"])
            return super().plan(*args, **kwargs)
    service.planner = Recorder()
    modeling.message(base.study_id, ModelingMessageRequest(expected_revision=dismissed.draft_revision,
        message="最高温度不超过 380 K", session_history=proposed.modeling.messages))
    assert len(seen) == 3 and seen[0].content == "最高温度不超过 400 K"
    assert repo.get_study(base.study_id).modeling.messages == []


def test_openai_context_uses_structured_current_plan_and_bounded_conversation(tmp_path, monkeypatch):
    service, _, _, base = setup_draft(tmp_path)
    observed = {}
    def parse(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(output_parsed=base.plan, id="response-test")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **kwargs: SimpleNamespace(
        responses=SimpleNamespace(parse=parse))))
    planner = OpenAIPlanner("configured-model", api_key="test-only")
    history = [ModelingMessage(role="user", content=f"补充条件 {i}") for i in range(40)]
    planner.plan(service.get_workpiece(base.workpiece_id), current_plan=base.plan, conversation=history,
        user_description="环境温度 300 K")
    payload = json.loads(observed["input"])
    assert payload["current_draft"] == base.plan.model_dump(mode="json")
    assert len(payload["conversation"]) == 20
    assert "triangle_ids" not in json.dumps(payload["workpiece"])
    assert observed["store"] is False and observed["model"] == "configured-model"


def test_modeling_api_separates_save_suggestion_application_and_solve(tmp_path):
    client = _client(tmp_path)
    part = client.post("/v1/workpieces/boxes", json={"name": "Reference", "dimensions_mm": {"x": 10, "y": 6, "z": 4}}).json()
    study = client.post("/v1/studies", json={"workpiece_id": part["workpiece_id"], "purpose": "温度验证"}).json()
    prefix = f"/v1/studies/{study['study_id']}"
    saved = client.put(prefix + "/draft", json={"expected_revision": 0, "purpose": "热边界验证"})
    assert saved.status_code == 200, saved.text
    proposed = client.post(prefix + "/modeling/messages", json={"expected_revision": saved.json()["draft_revision"],
        "message": "最高温度不超过 400 K"})
    assert proposed.status_code == 200, proposed.text
    assert client.post(prefix + "/confirm", json={}).status_code == 409
    applied = client.post(prefix + "/modeling/decision", json={"expected_revision": proposed.json()["draft_revision"], "action": "apply"})
    assert applied.status_code == 200 and applied.json()["confirmation"]["status"] == "needs_input"
    assert client.post(prefix + "/solve").status_code != 200
    assert client.post(
        prefix + "/confirm",
        json={
            "expected_revision": applied.json()["draft_revision"],
            "materials_confirmed": True,
        },
    ).status_code == 200


def test_incomplete_transient_draft_can_be_saved_and_questioned_but_not_solved(tmp_path):
    import trimesh
    service, _ = _service(tmp_path)
    part = service.register_stl("transient.stl", trimesh.creation.box(extents=[10, 6, 4]).export(file_type="stl"))
    part = service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    base = service.create_study(part.workpiece_id, require_confirmation=True)
    modeling = ModelingService(service)
    changed = modeling.update_draft(base.study_id, DraftUpdateRequest(expected_revision=0,
        overrides=SimulationOverrides(analysis_type="transient_conduction", duration_s=1, initial_temperature_k=350,
                                      time_step_s=None)))
    assert changed.plan.time_step_s is None and not changed.policy.accepted
    proposed = message(modeling, changed, "很热")
    assert proposed.modeling.proposal.plan.initial_temperature_k == 350
    assert any("时间步长" in error for error in proposed.modeling.proposal.validation_errors)
    applied = decide(modeling, proposed, "apply")
    with pytest.raises(ValueError, match="时间步长"):
        service.confirm_study(
            base.study_id,
            StudyConfirmationRequest(
                expected_revision=applied.draft_revision,
                materials_confirmed=True,
            ),
        )
    filled = modeling.update_draft(base.study_id, DraftUpdateRequest(expected_revision=applied.draft_revision,
        overrides=SimulationOverrides(time_step_s=0.1)))
    assert filled.policy.accepted
    cleared = modeling.update_draft(base.study_id, DraftUpdateRequest(expected_revision=filled.draft_revision,
        overrides=SimulationOverrides(time_step_s=None)))
    assert cleared.plan.time_step_s is None


def test_proposal_geometry_and_duplicate_request_guards(tmp_path):
    import trimesh
    service, _ = _service(tmp_path)
    part = service.register_stl("component.stl", trimesh.creation.box(extents=[10, 6, 4]).export(file_type="stl"))
    part = service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    base = service.create_study(part.workpiece_id, require_confirmation=True)
    modeling = ModelingService(service)
    with service.repository.modeling_lock(base.study_id):
        with pytest.raises(ValueError):
            message(modeling, base)
    proposed = message(modeling, base)
    with pytest.raises(ValueError, match="上一条"):
        message(modeling, proposed)
    service.update_component(part.workpiece_id, part.components[0].component_id, ComponentUpdateRequest(name="Renamed component"))
    with pytest.raises(ValueError, match="几何已改变"):
        decide(modeling, proposed, "apply")
    dismissed = decide(modeling, proposed, "dismiss")
    assert dismissed.modeling.proposal is None


def test_material_selection_keeps_catalog_evidence_and_rejects_unknown_regions(tmp_path):
    import trimesh
    service, _ = _service(tmp_path)
    part = service.register_stl("component.stl", trimesh.creation.box(extents=[10, 6, 4]).export(file_type="stl"))
    part = service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    base = service.create_study(part.workpiece_id, require_confirmation=True)
    modeling = ModelingService(service)
    proposed = message(modeling, base, "使用紫铜")
    assert proposed.modeling.proposal.plan.material.source_type == "database"
    assert proposed.modeling.proposal.plan.material.source_citation
    applied = decide(modeling, proposed, "apply")
    class InvalidRegionPlanner(DeterministicPlanner):
        def plan(self, *args, **kwargs):
            result = super().plan(*args, **kwargs)
            result.plan.boundaries[0].region_id = "region-000000000000"
            return result
    service.planner = InvalidRegionPlanner()
    invalid = message(modeling, applied)
    assert any("不存在" in error for error in invalid.modeling.proposal.validation_errors)
    invalid_applied = decide(modeling, invalid, "apply")
    with pytest.raises(ValueError, match="不存在"):
        service.confirm_study(
            base.study_id,
            StudyConfirmationRequest(
                expected_revision=invalid_applied.draft_revision,
                materials_confirmed=True,
            ),
        )


def test_editing_clears_only_affected_suggestion_provenance(tmp_path):
    _, repo, modeling, base = setup_draft(tmp_path)
    applied = decide(modeling, message(modeling, base), "apply")
    edited = modeling.update_draft(base.study_id, DraftUpdateRequest(expected_revision=applied.draft_revision, purpose="另一个用途"))
    assert "criteria" in edited.modeling.suggested_fields
    assert edited.modeling.undo_plan is None
    removed = modeling.update_draft(base.study_id, DraftUpdateRequest(expected_revision=edited.draft_revision,
        overrides=SimulationOverrides(criteria=[])))
    assert "criteria" not in repo.get_study(base.study_id).modeling.suggested_fields
    assert removed.plan.criteria == []


def test_retention_setting_is_explicit_and_existing_confirmed_records_are_immutable(tmp_path, monkeypatch):
    from thermoflow.settings import Settings
    monkeypatch.setenv("THERMOFLOW_RETAIN_MODELING_HISTORY", "false")
    assert not Settings.from_env(tmp_path).retain_modeling_history
    monkeypatch.setenv("THERMOFLOW_RETAIN_MODELING_HISTORY", "invalid")
    with pytest.raises(ValueError, match="true 或 false"):
        Settings.from_env(tmp_path)
    service, repo, modeling, base = setup_draft(tmp_path)
    legacy = service.create_study(base.workpiece_id)
    assert legacy.confirmation.status == "confirmed"
    with pytest.raises(ValueError, match="已确认研究不能"):
        service.confirm_study(
            legacy.study_id,
            StudyConfirmationRequest(purpose="不可覆盖", materials_confirmed=True),
        )
    with pytest.raises(ValueError, match="未确认"):
        modeling.update_draft(legacy.study_id, DraftUpdateRequest(expected_revision=0, purpose="不可覆盖"))
    assert repo.get_study(legacy.study_id) == legacy
