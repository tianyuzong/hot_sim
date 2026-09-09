from __future__ import annotations

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient
from pydantic import ValidationError

from thermoflow.api import create_app
from thermoflow.agent import _overrides_from_plan
from thermoflow.meshing import reconstruct_voxel_domain
from thermoflow.models import (
    ComponentUpdateRequest, FaceSelector, FixedTemperatureBoundary, LengthUnit,
    SimulationOverrides, StudyConfirmationRequest, StudyCopyRequest,
)
from thermoflow.planner import DeterministicPlanner, apply_user_overrides
from thermoflow.policy import validate_plan
from thermoflow.regions import (
    SurfaceRegionRequest, _nearest_faces, assert_confirmed_regions,
    create_surface_region, map_fixed_boundaries,
)
from thermoflow.settings import Settings
from thermoflow.stl_geometry import load_stl_mesh
from thermoflow.visualization import ViewRequest, geometry_surface, study_surface
from tests.test_study_comparison import _service, _solve_stl


def _part(service, unit=LengthUnit.MILLIMETER, scale=1):
    part = service.register_stl("block.stl", trimesh.creation.box(
        extents=np.array([10, 6, 4]) * scale,
    ).export(file_type="stl"))
    return service.confirm_workpiece_unit(part.workpiece_id, unit)


def _patch(repository, part, axis, side, name):
    view = geometry_surface(repository, part.workpiece_id)
    vertices = np.asarray(view.vertices)
    coordinate = (vertices[:, axis].min() if side == "min" else vertices[:, axis].max())
    faces = np.asarray(view.triangles)
    indices = np.flatnonzero(np.isclose(vertices[faces, axis], coordinate).all(axis=1)).tolist()
    return create_surface_region(repository, part.workpiece_id, SurfaceRegionRequest(name=name, triangle_ids=indices))


def _mapping(repository, part, plan, pitch=1):
    source = repository.workpiece_dir(part.workpiece_id) / part.stored_filename
    grid, active, _ = reconstruct_voxel_domain(load_stl_mesh(source), pitch)
    return map_fixed_boundaries(active=active, transform=grid.transform, workpiece=part,
                                plan=plan, source_path=source)


def test_region_selection_is_stable_validated_and_persistent(tmp_path):
    service, repository = _service(tmp_path)
    part = _part(service)
    region = _patch(repository, part, 0, "min", "Cold wall")
    assert region.kind == "surface_patch" and len(region.triangle_ids) == 2
    assert region.component_ids == [part.components[0].component_id]
    repeated = create_surface_region(repository, part.workpiece_id, SurfaceRegionRequest(
        name="Another label", triangle_ids=list(reversed(region.triangle_ids)),
    ))
    assert repeated == region
    for indices in ([region.triangle_ids[0]] * 2, [-1], [99999]):
        with pytest.raises(ValueError, match="三角面"):
            create_surface_region(repository, part.workpiece_id, SurfaceRegionRequest(name="Bad", triangle_ids=indices))
    for indices in ([], [True], [1.2], ["1"]):
        with pytest.raises(ValidationError):
            SurfaceRegionRequest(name="Bad", triangle_ids=indices)
    with pytest.raises(ValidationError):
        SurfaceRegionRequest(name="  ", triangle_ids=[0])
    with pytest.raises(ValueError, match="名称已存在"):
        _patch(repository, part, 0, "max", region.name)
    service.update_component(part.workpiece_id, part.components[0].component_id, ComponentUpdateRequest(name="Renamed"))
    reloaded = repository.get_workpiece(part.workpiece_id)
    assert next(r for r in reloaded.regions if r.region_id == region.region_id) == region
    unconfirmed = service.register_stl("unconfirmed.stl", trimesh.creation.box().export(file_type="stl"))
    with pytest.raises(ValueError, match="确认尺度"):
        create_surface_region(repository, unconfirmed.workpiece_id, SurfaceRegionRequest(name="Wall", triangle_ids=[0]))


def test_legacy_capability_migration_and_geometry_locks_preserve_saved_regions(tmp_path):
    service, repository = _service(tmp_path)
    part = _part(service)
    patch = _patch(repository, part, 0, "min", "Saved")
    current = repository.get_workpiece(part.workpiece_id)
    repository.save_workpiece(current.model_copy(update={"regions": [
        region.model_copy(update={"supported_condition_kinds": []}) if region.kind == "component_surface" else region
        for region in current.regions
    ]}))
    migrated = service.get_workpiece(part.workpiece_id)
    assert patch in migrated.regions
    assert all("fixed_temperature" in region.supported_condition_kinds for region in migrated.regions if region.kind == "component_surface")
    with repository.workpiece_lock(part.workpiece_id):
        with pytest.raises(ValueError, match="正在执行"):
            _patch(repository, part, 0, "max", "Blocked")
        with pytest.raises(ValueError, match="正在执行"):
            service.update_component(part.workpiece_id, part.components[0].component_id, ComponentUpdateRequest(name="Blocked"))
        with pytest.raises(ValueError, match="正在执行"):
            service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    assert repository.get_workpiece(part.workpiece_id) == migrated


@pytest.mark.parametrize("unit,scale", [(LengthUnit.MILLIMETER, 1), (LengthUnit.METER, 0.001), (LengthUnit.MICROMETER, 1000)])
def test_selected_opposite_faces_match_legacy_plane_mapping_at_all_scales(tmp_path, unit, scale):
    service, repository = _service(tmp_path)
    part = _part(service, unit, scale)
    left = _patch(repository, part, 0, "min", "Left")
    right = _patch(repository, part, 0, "max", "Right")
    part = repository.get_workpiece(part.workpiece_id)
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(
        fixed_boundaries=[FixedTemperatureBoundary(selector=FaceSelector.X_MIN, temperature_k=320),
                          FixedTemperatureBoundary(selector=FaceSelector.X_MAX, temperature_k=300)],
    ))
    reference = _mapping(repository, part, study.plan)
    regional = apply_user_overrides(study.plan, SimulationOverrides(fixed_boundaries=[
        FixedTemperatureBoundary(region_id=left.region_id, temperature_k=320),
        FixedTemperatureBoundary(region_id=right.region_id, temperature_k=300),
    ]))
    mapped = _mapping(repository, part, regional)
    assert np.array_equal(mapped[0], reference[0])
    assert np.array_equal(mapped[1], reference[1])
    assert [item["mapped_cells"] for item in mapped[2]] == [35, 35]


def test_region_snapshots_allow_additions_but_reject_referenced_changes(tmp_path):
    service, repository = _service(tmp_path)
    part = _part(service)
    left = _patch(repository, part, 0, "min", "Left")
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(fixed_boundaries=[
        FixedTemperatureBoundary(region_id=left.region_id, temperature_k=300),
    ]))
    assert study.policy.accepted, study.policy.errors
    assert study.simulation_spec.schema_version == "1.2"
    condition = study.simulation_spec.conditions.thermal[0]
    assert condition.target_refs == [left.region_id]
    assert condition.temperature.value == 300 and condition.temperature.unit == "K"
    _patch(repository, part, 0, "max", "New unrelated region")
    current = repository.get_workpiece(part.workpiece_id)
    assert_confirmed_regions(study, current)
    _solve_stl(service, study.study_id)
    assert repository.get_study(study.study_id).input_snapshot_sha256 == study.input_snapshot_sha256
    copy = service.copy_study(study.study_id, StudyCopyRequest())
    service.confirm_study(copy.study_id, StudyConfirmationRequest(materials_confirmed=True))
    tampered = current.model_copy(update={"regions": [
        region.model_copy(update={"triangle_ids": [0]}) if region.region_id == left.region_id else region
        for region in current.regions
    ]})
    with pytest.raises(ValueError, match="快照不一致"):
        assert_confirmed_regions(study, tampered)
    repository.save_workpiece(tampered)
    with pytest.raises(ValueError, match="快照不一致"):
        service.generate_mesh(copy.study_id)


def test_regional_steady_and_transient_fields_conserve_energy_and_preserve_copy(tmp_path):
    service, repository = _service(tmp_path)
    part = _part(service)
    patch = _patch(repository, part, 2, "min", "Cooling wall")
    boundary = FixedTemperatureBoundary(region_id=patch.region_id, temperature_k=310)
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(
        fixed_boundaries=[boundary], target_element_size_mm=1, relative_tolerance=1e-10,
    ), require_confirmation=True)
    with pytest.raises(ValueError, match="确认"):
        service.generate_mesh(study.study_id)
    study = service.confirm_study(
        study.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )
    _solve_stl(service, study.study_id)
    result = repository.get_result(study.study_id)
    assert result.energy_balance_relative_error < 1e-5
    assert result.boundary_mapping == repository.get_mesh(study.study_id).boundary_mapping
    view = study_surface(repository, study.study_id, ViewRequest())
    assert view.temperature_k and view.heat_flux_w_m2
    assert np.isclose(view.temperature_k, 310).any()
    copy = service.copy_study(study.study_id, StudyCopyRequest(overrides=SimulationOverrides(
        analysis_type="transient_conduction", initial_temperature_k=400,
        duration_s=0.3, time_step_s=0.1, heat_source_power_w=5,
    )))
    assert copy.plan.boundaries == [boundary]
    assert copy.confirmation.status != "confirmed"
    service.confirm_study(copy.study_id, StudyConfirmationRequest(materials_confirmed=True))
    _solve_stl(service, copy.study_id)
    result = repository.get_result(copy.study_id)
    assert len(result.time_steps) == 201
    assert result.time_steps[0].time_s == 0
    assert result.time_steps[-1].time_s == pytest.approx(0.3)
    assert result.maximum_energy_balance_error_over_time < 1e-5
    assert result.energy_balance_relative_error < 1e-5
    assert result.boundary_mapping[0]["region_id"] == patch.region_id
    optimized = apply_user_overrides(copy.plan, _overrides_from_plan(copy.plan, {"heat_source_power_w": 2}))
    assert optimized.boundaries == [boundary]
    assert optimized.analysis_type == "transient_conduction"
    assert optimized.heat_source.total_power_w == 2
    with pytest.raises(ValueError, match="区域边界列表"):
        apply_user_overrides(copy.plan, SimulationOverrides(heat_axis="y"))


def test_component_surface_mapping_and_region_validation(tmp_path):
    service, repository = _service(tmp_path)
    part = _part(service)
    region = next(region for region in part.regions if region.kind == "component_surface")
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(fixed_boundaries=[
        FixedTemperatureBoundary(region_id=region.region_id, temperature_k=310),
    ]))
    assert study.policy.accepted
    fixed, _, mappings = _mapping(repository, part, study.plan)
    assert fixed.sum() == 11 * 7 * 5 - 9 * 5 * 3
    assert mappings[0]["region_id"] == region.region_id
    plane = next(region for region in part.regions if region.selector == "face.xmin")
    alias = study.plan.model_copy(update={"boundaries": [
        FixedTemperatureBoundary(region_id=plane.region_id, temperature_k=310),
        FixedTemperatureBoundary(selector=FaceSelector.X_MIN, temperature_k=310),
    ]})
    assert any("重复" in error for error in validate_plan(part, alias).errors)
    invalid = study.plan.model_copy(update={"boundaries": [
        FixedTemperatureBoundary(region_id="region-000000000000", temperature_k=310),
    ]})
    assert not validate_plan(part, invalid).accepted
    source = repository.workpiece_dir(part.workpiece_id) / "source.stl"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="指纹不匹配"):
        _mapping(repository, part, study.plan)


def test_conflicting_region_cells_are_blocked_before_solve(tmp_path):
    service, repository = _service(tmp_path)
    part = _part(service)
    left = _patch(repository, part, 0, "min", "Left")
    bottom = _patch(repository, part, 2, "min", "Bottom")
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(fixed_boundaries=[
        FixedTemperatureBoundary(region_id=left.region_id, temperature_k=310),
        FixedTemperatureBoundary(region_id=bottom.region_id, temperature_k=320),
    ], target_element_size_mm=1))
    mesh = service.generate_mesh(study.study_id)
    assert mesh.quality_status == "blocked"
    assert any("温度冲突" in warning for warning in mesh.warnings)
    with pytest.raises(ValueError):
        service.run_study(study.study_id)


def test_nearest_triangle_is_not_nearest_centroid_and_unmapped_region_is_rejected(tmp_path):
    mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [100, 0, 0], [0, 100, 0],
                                    [0, 0, 2], [1, 0, 2], [0, 1, 2]],
                           faces=[[0, 1, 2], [3, 4, 5]], process=False)
    assert _nearest_faces(mesh, np.array([[0.1, 0.1, 0.1]])).tolist() == [0]
    service, repository = _service(tmp_path)
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=5)
    part = service.register_stl("sphere.stl", mesh.export(file_type="stl"))
    part = service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    surface = geometry_surface(repository, part.workpiece_id)
    # A coarse voxel surface cannot map every one of the fine original triangles.
    from thermoflow.regions import _exposed_face_samples
    source = repository.workpiece_dir(part.workpiece_id) / part.stored_filename
    grid, active, _ = reconstruct_voxel_domain(load_stl_mesh(source), 2)
    _, positions, directions = _exposed_face_samples(active, grid.transform)
    original = load_stl_mesh(repository.workpiece_dir(part.workpiece_id) / "source.stl")
    assigned = set(_nearest_faces(original, positions, directions=directions))
    missing = next(index for index in range(len(surface.triangles)) if index not in assigned)
    patch = create_surface_region(repository, part.workpiece_id, SurfaceRegionRequest(name="Small patch", triangle_ids=[missing]))
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(
        fixed_boundaries=[FixedTemperatureBoundary(region_id=patch.region_id, temperature_k=310)],
        target_element_size_mm=2,
    ))
    mesh_record = service.generate_mesh(study.study_id)
    assert mesh_record.quality_status == "blocked"
    assert any("未映射" in warning for warning in mesh_record.warnings)


def test_unrelated_overrides_preserve_arbitrary_nonregional_boundary_list(tmp_path):
    service, _ = _service(tmp_path)
    part = _part(service)
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(fixed_boundaries=[
        FixedTemperatureBoundary(selector=FaceSelector.X_MIN, temperature_k=310),
        FixedTemperatureBoundary(selector=FaceSelector.Y_MAX, temperature_k=310),
        FixedTemperatureBoundary(selector=FaceSelector.Z_MIN, temperature_k=310),
    ]))
    assert apply_user_overrides(study.plan, SimulationOverrides(heat_source_power_w=40)).boundaries == study.plan.boundaries


def test_region_api_schema_validation_and_ui_contract(tmp_path):
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / "data", cadflow_repo=tmp_path / "missing",
                        planner_mode="deterministic", compute_backend="cpu")
    app = create_app(settings, DeterministicPlanner())
    client = TestClient(app)
    part = _part(app.state.service)
    url = f"/v1/workpieces/{part.workpiece_id}/regions"
    result = client.post(url, json={"name": "Wall", "triangle_ids": [0, 1]})
    assert result.status_code == 200, result.text
    assert result.json()["kind"] == "surface_patch"
    assert client.post(url, json={"name": "Wall", "triangle_ids": []}).status_code == 422
    assert client.post(url, json={"name": "Wall", "triangle_ids": [99999]}).status_code == 409
    spec = client.get("/api/openapi.json").json()
    assert "1.2" in spec["components"]["schemas"]["SimulationSpec"]["properties"]["schema_version"]["enum"]
    html = client.get("/").text
    for element in ("geometrySelectionMode", "saveSurfaceSelection", "fixedBoundaryRows", "boundaryMapping"):
        assert f'id="{element}"' in html
