from __future__ import annotations

import json
from types import SimpleNamespace

import openai
import pytest

from thermoflow.models import (
    Point3DMM,
    SimulationOverrides,
    SimulationPlan,
    VolumetricHeatSource,
)
from thermoflow.planner import (
    DeterministicPlanner,
    OpenAIPlanner,
    PlannerUnavailableError,
    apply_user_overrides,
)

from .helpers import box_workpiece


def test_openai_planner_uses_structured_responses_contract(monkeypatch) -> None:
    workpiece = box_workpiece()
    expected = DeterministicPlanner().plan(workpiece).plan
    captured: dict[str, object] = {}

    class FakeResponses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="resp-test", output_parsed=expected)

    class FakeOpenAI:
        def __init__(self, *, api_key: str, timeout: float, max_retries: int) -> None:
            captured["api_key"] = api_key
            self.responses = FakeResponses()

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    decision = OpenAIPlanner("gpt-test", api_key="secret-test").plan(workpiece)

    assert captured["model"] == "gpt-test"
    assert captured["text_format"] is SimulationPlan
    assert captured["store"] is False
    assert json.loads(str(captured["input"]))["material_catalog"]
    assert json.loads(str(captured["input"]))["workpiece"]["workpiece_id"] == "wp-test"
    assert decision.plan == expected
    assert decision.provenance.provider == "openai"
    assert decision.provenance.response_id == "resp-test"


def test_openai_planner_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(PlannerUnavailableError, match="OPENAI_API_KEY"):
        OpenAIPlanner("gpt-test").plan(box_workpiece())


def test_heat_source_overrides_create_a_missing_source_and_select_voxel_solver() -> None:
    plan = DeterministicPlanner().plan(box_workpiece()).plan
    assert plan.heat_source is None

    updated = apply_user_overrides(
        plan,
        SimulationOverrides(
            enable_heat_source=True,
            heat_source_shape="surface",
            heat_source_placement="surface",
            heat_source_x_mm=50,
            heat_source_y_mm=10,
            heat_source_z_mm=10,
            heat_source_power_w=20,
            heat_source_radius_mm=1,
            heat_source_surface_axis="z",
            heat_source_surface_width_mm=8,
            heat_source_surface_height_mm=4,
            heat_source_surface_thickness_mm=1,
        ),
    )

    assert updated.heat_source_enabled is True
    assert updated.heat_source is not None
    assert updated.heat_source.shape == "surface"
    assert updated.heat_source.center_mm.as_tuple() == (50, 10, 10)
    assert updated.heat_source.total_power_w == 20
    assert updated.solver.backend == "voxel_stl_v1"


def test_multiple_heat_source_override_preserves_every_independent_source() -> None:
    plan = DeterministicPlanner().plan(box_workpiece()).plan
    sources = [
        VolumetricHeatSource(
            source_id="source-1",
            name="左侧点热源",
            shape="point",
            center_mm=Point3DMM(x=25, y=10, z=5),
            total_power_w=10,
            radius_mm=2,
        ),
        VolumetricHeatSource(
            source_id="source-2",
            name="中部体热源",
            shape="volume",
            center_mm=Point3DMM(x=60, y=10, z=5),
            total_power_w=30,
            radius_mm=1,
            volume_width_mm=12,
            volume_height_mm=8,
            volume_depth_mm=6,
        ),
    ]

    updated = apply_user_overrides(
        plan,
        SimulationOverrides(enable_heat_source=True, heat_sources=sources),
    )

    assert updated.heat_sources == sources
    assert updated.heat_source == sources[0]
    assert updated.solver.backend == "voxel_stl_v1"


def test_switching_to_transient_supplies_a_sixty_second_default_window() -> None:
    plan = DeterministicPlanner().plan(box_workpiece()).plan

    updated = apply_user_overrides(
        plan,
        SimulationOverrides(analysis_type="transient_conduction"),
    )

    assert updated.initial_temperature_k == pytest.approx(293.15)
    assert updated.duration_s == pytest.approx(60.0)
    assert updated.time_step_s == pytest.approx(1.0)
    assert updated.analyses == ["transient_thermal"]
