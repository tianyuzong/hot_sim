from __future__ import annotations

from types import SimpleNamespace

import openai
import pytest
import trimesh

from thermoflow.agent import (
    DeterministicAgentPolicy,
    OpenAIAgentPolicy,
    SimulationAgent,
    resolve_agent_goal,
)
from thermoflow.cadflow_adapter import CadFlowGeometryInspector
from thermoflow.models import (
    AgentGoalRequest,
    LengthUnit,
    MeshReviewRequest,
    SimulationOverrides,
)
from thermoflow.planner import DeterministicPlanner
from thermoflow.service import StudyService
from thermoflow.storage import FileRepository


def _service(tmp_path) -> tuple[StudyService, FileRepository]:
    repository = FileRepository(tmp_path / "data")
    service = StudyService(
        repository=repository,
        planner=DeterministicPlanner(),
        geometry=CadFlowGeometryInspector(tmp_path / "missing-cadflow"),
        compute_backend="cpu",
    )
    return service, repository


def test_agent_resolves_chinese_celsius_constraint() -> None:
    resolved = resolve_agent_goal(
        AgentGoalRequest(instruction="把峰值温度控制在不超过 80 ℃，并检查能量平衡")
    )

    assert resolved.target_max_temperature_k == pytest.approx(353.15)
    assert resolved.parsed_from_instruction


def test_openai_agent_policy_uses_structured_response(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                id="resp-agent-test",
                output_parsed=SimpleNamespace(
                    action="apply_adjustment",
                    summary="执行受控调整。",
                ),
            )

    class FakeOpenAI:
        def __init__(self, *, api_key: str, timeout: float, max_retries: int) -> None:
            captured["api_key"] = api_key
            self.responses = FakeResponses()

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    choice = OpenAIAgentPolicy("gpt-test", api_key="secret-test").decide(
        {"evaluation": {"goal_met": False}},
        {"heat_source_power_w": 12.0},
    )

    assert captured["model"] == "gpt-test"
    assert captured["store"] is False
    assert choice.decision.action == "apply_adjustment"
    assert choice.response_id == "resp-agent-test"


def test_agent_runs_bounded_solver_feedback_loop(tmp_path) -> None:
    service, repository = _service(tmp_path)
    stl = trimesh.creation.box(extents=[60, 30, 15]).export(file_type="stl")
    workpiece = service.register_stl("agent-part.stl", stl)
    workpiece = service.confirm_workpiece_unit(workpiece.workpiece_id, LengthUnit.MILLIMETER)
    baseline = service.create_study(
        workpiece.workpiece_id,
        overrides=SimulationOverrides(heat_source_power_w=120),
    )
    baseline_mesh = service.generate_mesh(baseline.study_id)
    if baseline_mesh.review_status == "pending":
        service.confirm_mesh(
            baseline.study_id,
            MeshReviewRequest(accept_warnings=True, confirmed_by="测试授权"),
        )
    baseline = service.run_study(baseline.study_id)
    baseline_result = repository.get_result(baseline.study_id)
    target = 293.15 + (baseline_result.temperature_max_k - 293.15) * 0.55

    run = SimulationAgent(
        repository=repository,
        service=service,
        policy=DeterministicAgentPolicy(),
    ).run(
        baseline.study_id,
        AgentGoalRequest(
            instruction="降低热点，同时保持当前材料、边界和热源位置",
            target_max_temperature_k=target,
            temperature_tolerance_k=0.5,
            max_rounds=2,
            allow_power_adjustment=True,
            allow_mesh_refinement=True,
        ),
    )

    assert run.status == "goal_met"
    assert run.rounds_completed >= 1
    assert run.selected_study_id != run.base_study_id
    assert run.candidate_study_ids == [run.selected_study_id]
    assert {step.tool for step in run.steps} >= {
        "inspect_geometry",
        "inspect_result",
        "evaluate_constraints",
        "adjust_parameters",
        "run_solver",
        "finish",
    }

    selected_study = repository.get_study(run.selected_study_id)
    selected_result = repository.get_result(run.selected_study_id)
    selected_mesh = repository.get_mesh(run.selected_study_id)
    assert run.project_id == baseline.project_id
    assert selected_study.plan is not None
    assert selected_study.simulation_spec is not None
    assert selected_study.project_id == baseline.project_id
    assert selected_study.confirmation.confirmed_by == "Agent 授权范围"
    assert selected_study.simulation_spec.confirmation.confirmed_by == "Agent 授权范围"
    assert selected_study.plan.heat_source is not None
    assert selected_study.plan.heat_source.total_power_w < 120
    assert selected_study.mesh_status == "ready"
    assert selected_mesh.review_status in {"not_required", "accepted"}
    assert selected_result.temperature_max_k <= target + 0.5
    assert selected_result.energy_balance_relative_error <= 1e-5

    listed = repository.list_agent_runs(workpiece_id=workpiece.workpiece_id)
    assert [item.run_id for item in listed] == [run.run_id]
