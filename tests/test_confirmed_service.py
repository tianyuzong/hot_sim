from __future__ import annotations

import pytest
import trimesh
from pydantic import ValidationError

from thermoflow.cadflow_adapter import CadFlowGeometryInspector
from thermoflow.models import (
    BoxWorkpieceInput,
    DimensionsMM,
    EngineeringCriterion,
    LengthUnit,
    MeshReviewRequest,
    Quantity,
    SimulationOverrides,
    StudyConfirmationRequest,
    StudyCreateRequest,
    StudyStatus,
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


def test_confirmed_stl_service_workflow_preserves_capability_limits(tmp_path) -> None:
    service, repository = _service(tmp_path)
    payload = trimesh.creation.box(extents=[12, 8, 4]).export(file_type="stl")

    workpiece = service.register_stl("thermal-bracket.stl", payload)

    assert workpiece.unit_confirmed is False
    assert workpiece.dimensions_mm is None
    assert workpiece.geometry.summary["coordinate_system"]["length_unit"] is None
    with pytest.raises(ValueError, match="确认 STL"):
        service.create_study(workpiece.workpiece_id, require_confirmation=True)

    workpiece = service.confirm_workpiece_unit(workpiece.workpiece_id, LengthUnit.MILLIMETER)
    draft = service.create_study(
        workpiece.workpiece_id,
        purpose=(
            "6061 铝合金支架，环境 25 ℃，热源功率 20 W，最高温度不超过 100 ℃，"
            "同时考虑辐射、接触热阻、压力载荷和热应力。"
        ),
        require_confirmation=True,
    )

    assert draft.status is StudyStatus.NEEDS_INPUT
    assert draft.confirmation.status == "needs_input"
    assert draft.input_snapshot_sha256 is None
    assert draft.plan is not None
    assert {item.material_id for item in draft.plan.component_materials} == {"al-6061-t6"}
    assert len(draft.plan.unsupported_physics) == 4
    with pytest.raises(ValueError, match="已准备求解"):
        service.run_study(draft.study_id)

    ready = service.confirm_study(
        draft.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )
    assert ready.status is StudyStatus.READY
    assert ready.confirmation.status == "confirmed"
    assert ready.input_snapshot_sha256
    assert ready.mesh_status == "not_generated"
    with pytest.raises(ValueError, match="生成并检查真实网格"):
        service.run_study(ready.study_id)

    mesh = service.generate_mesh(ready.study_id)
    meshed_study = repository.get_study(ready.study_id)
    assert meshed_study.mesh_status == "ready"
    assert meshed_study.mesh_snapshot_sha256 == ready.input_snapshot_sha256
    assert mesh.plan_snapshot_sha256 == ready.input_snapshot_sha256
    assert mesh.active_cells > 0
    assert mesh.surface_cells > 0
    assert mesh.quality.maximum_aspect_ratio == pytest.approx(1.0)
    assert mesh.cell_samples_mm
    mesh_artifact = repository.artifact_path(ready.study_id, "mesh.vtk")
    assert "SCALARS surface_cell int 1" in mesh_artifact.read_text(encoding="ascii")

    completed = service.run_study(ready.study_id)
    result = repository.get_result(ready.study_id)

    assert completed.status is StudyStatus.SUCCEEDED
    assert result.evaluation_status in {"indeterminate", "violates_criteria"}
    assert any("不能据此判定完整工程目标" in item for item in result.evaluation_summary)
    assert result.heat_flux_field_preview is not None
    assert result.heat_flux_field_preview.maximum_magnitude_w_m2 > 0
    assert result.grid == mesh.grid


def test_public_study_request_cannot_disable_confirmation() -> None:
    with pytest.raises(ValidationError):
        StudyCreateRequest(workpiece_id="wp-test", require_confirmation=False)


def test_study_confirmation_requires_explicit_material_review(tmp_path) -> None:
    service, _ = _service(tmp_path)
    workpiece = service.register_box(
        BoxWorkpieceInput(
            name="material-review-coupon",
            dimensions_mm=DimensionsMM(x=20, y=10, z=5),
        )
    )
    draft = service.create_study(workpiece.workpiece_id, require_confirmation=True)

    with pytest.raises(ValueError, match="明确确认.*材料"):
        service.confirm_study(draft.study_id, StudyConfirmationRequest())

    confirmed = service.confirm_study(
        draft.study_id,
        StudyConfirmationRequest(materials_confirmed=True),
    )
    assert confirmed.status is StudyStatus.READY


def test_mesh_quality_warning_requires_explicit_review(tmp_path) -> None:
    service, repository = _service(tmp_path)
    payload = trimesh.creation.box(extents=[12, 8, 4]).export(file_type="stl")
    workpiece = service.register_stl("coarse-part.stl", payload)
    workpiece = service.confirm_workpiece_unit(workpiece.workpiece_id, LengthUnit.MILLIMETER)
    draft = service.create_study(
        workpiece.workpiece_id,
        overrides=SimulationOverrides(
            target_element_size_mm=5,
            max_axis_intervals=10,
        ),
        require_confirmation=True,
    )
    ready = service.confirm_study(
        draft.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )

    mesh = service.generate_mesh(ready.study_id)

    assert mesh.quality_status == "warning"
    assert mesh.review_status == "pending"
    assert repository.get_study(ready.study_id).mesh_status == "needs_review"
    with pytest.raises(ValueError, match="真实网格"):
        service.run_study(ready.study_id)

    reviewed = service.confirm_mesh(
        ready.study_id,
        MeshReviewRequest(accept_warnings=True, confirmed_by="测试工程师"),
    )

    assert reviewed.review_status == "accepted"
    assert reviewed.reviewed_by == "测试工程师"
    assert reviewed.reviewed_at is not None
    assert repository.get_study(ready.study_id).mesh_status == "ready"
    assert service.run_study(ready.study_id).status is StudyStatus.SUCCEEDED


def test_mesh_blocks_component_material_mapping_when_coarse_cells_merge_shells(tmp_path) -> None:
    service, repository = _service(tmp_path)
    gap_mm = 0.2
    left = trimesh.creation.box(extents=[4, 4, 4])
    left.apply_translation([-2 - gap_mm / 2, 0, 0])
    right = trimesh.creation.box(extents=[4, 4, 4])
    right.apply_translation([2 + gap_mm / 2, 0, 0])
    payload = trimesh.util.concatenate([left, right]).export(file_type="stl")
    workpiece = service.register_stl("two-close-shells.stl", payload)
    workpiece = service.confirm_workpiece_unit(workpiece.workpiece_id, LengthUnit.MILLIMETER)
    assert len(workpiece.components) == 2
    draft = service.create_study(
        workpiece.workpiece_id,
        overrides=SimulationOverrides(
            target_element_size_mm=1,
            max_axis_intervals=20,
        ),
        require_confirmation=True,
    )
    ready = service.confirm_study(
        draft.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )

    mesh = service.generate_mesh(ready.study_id)

    assert mesh.quality_status == "blocked"
    assert mesh.quality.connected_regions == 1
    assert mesh.quality.expected_components == 2
    assert any("组件材料无法可靠绑定" in warning for warning in mesh.warnings)
    assert repository.get_study(ready.study_id).mesh_status == "blocked"
    with pytest.raises(ValueError, match="真实网格"):
        service.run_study(ready.study_id)


def test_component_material_user_override_clears_catalog_identity(tmp_path) -> None:
    service, repository = _service(tmp_path)
    payload = trimesh.creation.box(extents=[12, 8, 4]).export(file_type="stl")
    workpiece = service.register_stl("material-override.stl", payload)
    workpiece = service.confirm_workpiece_unit(workpiece.workpiece_id, LengthUnit.MILLIMETER)
    draft = service.create_study(
        workpiece.workpiece_id,
        purpose="6061 铝合金零件，环境 25 ℃，热源功率 20 W。",
        require_confirmation=True,
    )
    assert draft.plan is not None
    catalog_assignment = draft.plan.component_materials[0]
    user_material = catalog_assignment.material.model_copy(
        update={
            "name": "铝合金 6061-T6（用户覆盖）",
            "thermal_conductivity_w_m_k": 155.0,
            "source_basis": "用户在结构化材料面板中确认或覆盖的热物性值。",
            "source_type": "user",
            "source_reference": None,
            "source_version": None,
            "source_citation": None,
            "valid_temperature_min_k": None,
            "valid_temperature_max_k": None,
        }
    )
    user_assignment = catalog_assignment.model_copy(
        update={"material_id": None, "material": user_material}
    )

    confirmed = service.confirm_study(
        draft.study_id,
        StudyConfirmationRequest(
            overrides=SimulationOverrides(component_materials=[user_assignment]),
            materials_confirmed=True,
            confirmed_by="材料工程师",
        ),
    )

    assert confirmed.plan is not None
    saved = confirmed.plan.component_materials[0]
    assert saved.material_id is None
    assert saved.material.thermal_conductivity_w_m_k == pytest.approx(155.0)
    assert saved.material.source_type == "user"
    assert saved.material.source_reference is None
    assert saved.material.valid_temperature_max_k is None
    assert confirmed.confirmation.confirmed_by == "材料工程师"
    assert repository.get_study(confirmed.study_id).input_snapshot_sha256


def test_result_outside_material_property_range_is_indeterminate(tmp_path) -> None:
    service, repository = _service(tmp_path)
    workpiece = service.register_box(
        BoxWorkpieceInput(
            name="high-temperature-coupon",
            dimensions_mm=DimensionsMM(x=100, y=20, z=10),
        )
    )
    draft = service.create_study(
        workpiece.workpiece_id,
        overrides=SimulationOverrides(
            material_name="铝合金 6061-T6",
            thermal_conductivity_w_m_k=167,
            density_kg_m3=2700,
            specific_heat_j_kg_k=896,
            min_face_temperature_k=293.15,
            max_face_temperature_k=600,
            criteria=[
                EngineeringCriterion(
                    metric="max_temperature",
                    operator="less_or_equal",
                    target=Quantity(value=700, unit="K"),
                )
            ],
        ),
        require_confirmation=True,
    )
    confirmed = service.confirm_study(
        draft.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )

    completed = service.run_study(confirmed.study_id)
    result = repository.get_result(completed.study_id)

    assert result.temperature_max_k == pytest.approx(600)
    assert result.evaluation_status == "indeterminate"
    assert completed.evaluation_status == "indeterminate"
    assert any("物性数据范围上限" in item for item in result.evaluation_summary)
