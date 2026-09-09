from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from thermoflow.api import create_app
from thermoflow.cadflow_adapter import CadFlowGeometryInspector
from thermoflow.models import (
    BoxWorkpieceInput,
    ComponentUpdateRequest,
    DimensionsMM,
    LengthUnit,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    StudyConfirmationRequest,
    StudyRecord,
    StudyStatus,
)
from thermoflow.planner import DeterministicPlanner
from thermoflow.service import StudyService
from thermoflow.settings import Settings
from thermoflow.specification import simulation_spec_sha256
from thermoflow.storage import FileRepository, RecordNotFoundError

from .helpers import box_workpiece


def _service(tmp_path: Path) -> tuple[StudyService, FileRepository]:
    repository = FileRepository(tmp_path / "data")
    service = StudyService(
        repository=repository,
        planner=DeterministicPlanner(),
        geometry=CadFlowGeometryInspector(tmp_path / "missing-cadflow"),
        compute_backend="cpu",
    )
    return service, repository


def test_project_owns_geometry_versions_studies_and_versioned_spec(tmp_path) -> None:
    service, repository = _service(tmp_path)
    project = service.create_project(ProjectCreateRequest(name="安装支架热评估"))
    payload = trimesh.creation.box(extents=[80, 50, 20]).export(file_type="stl")
    workpiece = service.register_stl("mounting-bracket.stl", payload, project.project_id)
    workpiece = service.confirm_workpiece_unit(workpiece.workpiece_id, LengthUnit.MILLIMETER)
    service.register_box(
        BoxWorkpieceInput(
            name="解析校核体",
            dimensions_mm=DimensionsMM(x=100, y=20, z=10),
            project_id=project.project_id,
        )
    )

    draft = service.create_study(
        workpiece.workpiece_id,
        purpose="6061 铝合金支架，环境 25 ℃，局部热源 20 W，最高温度低于 100 ℃。",
        require_confirmation=True,
    )

    assert draft.project_id == project.project_id
    assert draft.simulation_spec is not None
    spec = draft.simulation_spec
    assert spec.schema_version == "1.0"
    assert spec.project.project_id == project.project_id
    assert spec.geometry.asset_id == workpiece.workpiece_id
    assert spec.geometry.dimensions.x.unit == "mm"
    assert len(workpiece.regions) == 7
    assert len(spec.geometry.regions) == 7
    region_ids = {region.id for region in spec.geometry.regions}
    assert len(region_ids) == len(spec.geometry.regions)
    assert set(spec.geometry.components[0].region_refs).issubset(region_ids)
    fixed_conditions = [
        condition
        for condition in spec.conditions.thermal
        if condition.kind == "fixed_temperature"
    ]
    assert all(condition.target_refs[0] in region_ids for condition in fixed_conditions)
    assert spec.materials.assignments[0].material.thermal_conductivity.unit == "W/(m*K)"
    assert spec.materials.assignments[0].material.density.unit == "kg/m^3"
    assert spec.conditions.thermal[0].temperature.unit == "K"
    assert spec.mesh.global_size.unit == "mm"
    assert spec.confirmation.status == "needs_input"
    assert draft.input_snapshot_sha256 is None

    confirmed = service.confirm_study(
        draft.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )

    assert confirmed.simulation_spec is not None
    assert confirmed.simulation_spec.confirmation.status == "confirmed"
    assert confirmed.input_snapshot_sha256 == simulation_spec_sha256(
        confirmed.simulation_spec
    )
    workspace = service.get_project_workspace(project.project_id)
    assert {item.workpiece_id for item in workspace.workpieces} == set(
        workspace.project.workpiece_ids
    )
    assert [item.study_id for item in workspace.studies] == [confirmed.study_id]
    assert repository.get_study(confirmed.study_id).project_id == project.project_id

    renamed = service.update_project(
        project.project_id,
        ProjectUpdateRequest(name="安装支架热评估 R2"),
    )
    assert renamed.name == "安装支架热评估 R2"
    assert service.get_simulation_spec(confirmed.study_id).project.name == "安装支架热评估"


def test_simulation_spec_mismatch_blocks_mesh_generation(tmp_path) -> None:
    service, repository = _service(tmp_path)
    payload = trimesh.creation.box(extents=[20, 10, 5]).export(file_type="stl")
    workpiece = service.register_stl("tamper-check.stl", payload)
    workpiece = service.confirm_workpiece_unit(workpiece.workpiece_id, LengthUnit.MILLIMETER)
    draft = service.create_study(
        workpiece.workpiece_id,
        purpose="6061 铝合金零件，热源 20 W。",
        require_confirmation=True,
    )
    confirmed = service.confirm_study(
        draft.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )
    assert confirmed.plan is not None
    changed_plan = confirmed.plan.model_copy(
        update={
            "mesh": confirmed.plan.mesh.model_copy(
                update={"target_element_size_mm": confirmed.plan.mesh.target_element_size_mm / 2}
            )
        }
    )
    repository.save_study(confirmed.model_copy(update={"plan": changed_plan}))

    with pytest.raises(ValueError, match="仿真规格与当前执行方案不一致"):
        service.generate_mesh(confirmed.study_id)


def test_project_and_simulation_spec_are_exposed_in_openapi(tmp_path) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        cadflow_repo=tmp_path / "missing-cadflow",
        planner_mode="deterministic",
    )
    schema = create_app(settings, DeterministicPlanner()).openapi()

    assert schema["tags"][1]["name"] == "项目"
    assert "/v1/projects" in schema["paths"]
    assert "/v1/projects/{project_id}" in schema["paths"]
    assert "/v1/projects/{project_id}/workspace" in schema["paths"]
    assert "/v1/studies/{study_id}/spec" in schema["paths"]
    assert "/v1/workpieces/{workpiece_id}/components/{component_id}" in schema["paths"]
    assert "delete" in schema["paths"]["/v1/projects/{project_id}"]
    assert "delete" in schema["paths"]["/v1/workpieces/{workpiece_id}/components/{component_id}"]
    assert "/v1/studies/{study_id}/copy" in schema["paths"]
    assert "/v1/study-comparisons" in schema["paths"]
    specification = schema["components"]["schemas"]["SimulationSpec"]
    assert specification["properties"]["schema_version"]["enum"] == ["1.0", "1.1", "1.2", "1.3", "1.4"]


def test_legacy_workpiece_and_study_are_migrated_into_a_project(tmp_path) -> None:
    service, repository = _service(tmp_path)
    legacy = box_workpiece(workpiece_id="wp-legacy", name="历史散热件")
    repository.save_workpiece(legacy)
    repository.save_study(
        StudyRecord(
            study_id="study-legacy",
            workpiece_id=legacy.workpiece_id,
            status=StudyStatus.REJECTED,
        )
    )

    projects = service.list_projects()

    assert len(projects) == 1
    assert projects[0].name == legacy.name
    assert projects[0].workpiece_ids == [legacy.workpiece_id]
    assert repository.get_workpiece(legacy.workpiece_id).project_id == projects[0].project_id
    assert repository.get_workpiece(legacy.workpiece_id).regions
    assert repository.get_study("study-legacy").project_id == projects[0].project_id


def test_component_rename_preserves_id_and_confirmed_spec_snapshot(tmp_path) -> None:
    service, repository = _service(tmp_path)
    payload = trimesh.creation.box(extents=[20, 10, 5]).export(file_type="stl")
    workpiece = service.register_stl("rename-part.stl", payload)
    workpiece = service.confirm_workpiece_unit(
        workpiece.workpiece_id,
        LengthUnit.MILLIMETER,
    )
    component_id = workpiece.components[0].component_id
    draft = service.create_study(
        workpiece.workpiece_id,
        purpose="安装面支架，环境 25 ℃，局部热源 20 W。",
        require_confirmation=True,
    )

    renamed = service.update_component(
        workpiece.workpiece_id,
        component_id,
        ComponentUpdateRequest(name="主承力壳体"),
    )

    assert renamed.components[0].component_id == component_id
    assert renamed.components[0].name == "主承力壳体"
    assert next(
        region for region in renamed.regions if region.kind == "component_surface"
    ).name == "主承力壳体外表面"
    updated_draft = repository.get_study(draft.study_id)
    assert updated_draft.simulation_spec is not None
    assert updated_draft.simulation_spec.geometry.components[0].name == "主承力壳体"

    confirmed = service.confirm_study(
        draft.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )
    service.update_component(
        workpiece.workpiece_id,
        component_id,
        ComponentUpdateRequest(name="支架主体"),
    )

    assert service.get_workpiece(workpiece.workpiece_id).components[0].name == "支架主体"
    assert service.get_simulation_spec(confirmed.study_id).geometry.components[0].name == "主承力壳体"


def test_component_and_project_deletion_rebuilds_geometry_and_removes_records(tmp_path) -> None:
    service, repository = _service(tmp_path)
    project = service.create_project(ProjectCreateRequest(name="可删除项目"))
    left = trimesh.creation.box(extents=[10, 10, 10])
    right = trimesh.creation.box(extents=[8, 8, 8])
    left.apply_translation([-12, 0, 0])
    right.apply_translation([12, 0, 0])
    payload = trimesh.util.concatenate([left, right]).export(file_type="stl")
    workpiece = service.register_stl("two-shells.stl", payload, project.project_id)

    assert len(workpiece.components) == 2
    updated = service.delete_component(
        workpiece.workpiece_id,
        workpiece.components[0].component_id,
    )
    assert len(updated.components) == 1
    assert repository.get_workpiece(updated.workpiece_id).geometry.available

    service.delete_project(project.project_id)
    with pytest.raises(RecordNotFoundError):
        repository.get_project(project.project_id)
    with pytest.raises(RecordNotFoundError):
        repository.get_workpiece(updated.workpiece_id)
