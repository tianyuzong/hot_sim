from __future__ import annotations

import pytest
import trimesh

from thermoflow.cadflow_adapter import CadFlowGeometryInspector
from thermoflow.models import (
    BoxWorkpieceInput,
    DimensionsMM,
    LengthUnit,
    MeshReviewRequest,
    ProjectCreateRequest,
    SimulationOverrides,
    StudyComparisonRequest,
    StudyConfirmationRequest,
    StudyCopyRequest,
    StudyStatus,
)
from thermoflow.planner import DeterministicPlanner
from thermoflow.service import StudyService
from thermoflow.storage import FileRepository, RecordNotFoundError


def _service(tmp_path) -> tuple[StudyService, FileRepository]:
    repository = FileRepository(tmp_path / "data")
    service = StudyService(
        repository=repository,
        planner=DeterministicPlanner(),
        geometry=CadFlowGeometryInspector(tmp_path / "missing-cadflow"),
        compute_backend="cpu",
    )
    return service, repository


def _solve_stl(service: StudyService, study_id: str) -> None:
    mesh = service.generate_mesh(study_id)
    if mesh.review_status == "pending":
        service.confirm_mesh(
            study_id,
            MeshReviewRequest(accept_warnings=True, confirmed_by="比较测试"),
        )
    assert service.run_study(study_id).status is StudyStatus.SUCCEEDED


def test_copy_requires_confirmation_and_comparison_uses_real_matching_field(tmp_path) -> None:
    service, repository = _service(tmp_path)
    stl = trimesh.creation.box(extents=[40, 24, 12]).export(file_type="stl")
    workpiece = service.register_stl("comparison-part.stl", stl)
    workpiece = service.confirm_workpiece_unit(workpiece.workpiece_id, LengthUnit.MILLIMETER)
    baseline = service.create_study(
        workpiece.workpiece_id,
        overrides=SimulationOverrides(heat_source_power_w=20),
    )
    _solve_stl(service, baseline.study_id)

    copied = service.copy_study(
        baseline.study_id,
        StudyCopyRequest(
            name="40 W 热源研究",
            overrides=SimulationOverrides(heat_source_power_w=40),
        ),
    )

    assert copied.source_study_id == baseline.study_id
    assert copied.status is StudyStatus.NEEDS_INPUT
    assert copied.confirmation.status == "needs_input"
    assert copied.input_snapshot_sha256 is None
    assert copied.mesh_status == "not_generated"
    assert copied.plan is not None and copied.plan.study_name == "40 W 热源研究"
    assert copied.plan.heat_source is not None
    assert copied.plan.heat_source.total_power_w == pytest.approx(40)
    with pytest.raises(RecordNotFoundError):
        repository.get_result(copied.study_id)
    with pytest.raises(RecordNotFoundError):
        repository.get_mesh(copied.study_id)

    candidate = service.confirm_study(
        copied.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )
    _solve_stl(service, candidate.study_id)
    comparison = service.compare_studies(
        StudyComparisonRequest(
            study_ids=[baseline.study_id, candidate.study_id],
            baseline_study_id=baseline.study_id,
        )
    )

    baseline_result = repository.get_result(baseline.study_id)
    candidate_result = repository.get_result(candidate.study_id)
    difference = comparison.differences[0]
    assert comparison.project_id == baseline.project_id
    assert comparison.common_scale.minimum.value == pytest.approx(
        min(baseline_result.temperature_min_k, candidate_result.temperature_min_k)
    )
    assert comparison.common_scale.maximum.value == pytest.approx(
        max(baseline_result.temperature_max_k, candidate_result.temperature_max_k)
    )
    assert difference.temperature_max_delta.value > 0
    assert difference.field_unavailable_reason is None
    assert difference.field is not None
    assert difference.field.surface_samples
    assert baseline_result.temperature_field_preview is not None
    assert candidate_result.temperature_field_preview is not None
    base_sample = baseline_result.temperature_field_preview.samples[0]
    candidate_sample = candidate_result.temperature_field_preview.samples[0]
    delta_sample = difference.field.surface_samples[0]
    assert delta_sample[:3] == pytest.approx(base_sample[:3])
    assert delta_sample[3] == pytest.approx(candidate_sample[3] - base_sample[3])


def test_comparison_keeps_metrics_but_rejects_field_for_different_geometry(tmp_path) -> None:
    service, repository = _service(tmp_path)
    project = service.create_project(ProjectCreateRequest(name="跨几何方案"))
    first = service.register_box(
        BoxWorkpieceInput(
            name="方案 A",
            dimensions_mm=DimensionsMM(x=100, y=20, z=10),
            project_id=project.project_id,
        )
    )
    second = service.register_box(
        BoxWorkpieceInput(
            name="方案 B",
            dimensions_mm=DimensionsMM(x=80, y=20, z=10),
            project_id=project.project_id,
        )
    )
    first_study = service.create_study(first.workpiece_id)
    second_study = service.create_study(second.workpiece_id)
    service.run_study(first_study.study_id)
    service.run_study(second_study.study_id)

    comparison = service.compare_studies(
        StudyComparisonRequest(study_ids=[first_study.study_id, second_study.study_id])
    )

    assert len(comparison.studies) == 2
    assert comparison.differences[0].field is None
    assert "不同几何版本" in (comparison.differences[0].field_unavailable_reason or "")
    assert repository.get_result(first_study.study_id).temperature_max_k == pytest.approx(373.15)


def test_comparison_rejects_studies_from_different_projects(tmp_path) -> None:
    service, _ = _service(tmp_path)
    first = service.register_box(
        BoxWorkpieceInput(name="项目一", dimensions_mm=DimensionsMM(x=100, y=20, z=10))
    )
    second = service.register_box(
        BoxWorkpieceInput(name="项目二", dimensions_mm=DimensionsMM(x=100, y=20, z=10))
    )
    first_study = service.create_study(first.workpiece_id)
    second_study = service.create_study(second.workpiece_id)
    service.run_study(first_study.study_id)
    service.run_study(second_study.study_id)

    with pytest.raises(ValueError, match="同一项目"):
        service.compare_studies(
            StudyComparisonRequest(study_ids=[first_study.study_id, second_study.study_id])
        )
