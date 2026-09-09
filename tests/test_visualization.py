from __future__ import annotations

import hashlib

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from tests.test_study_comparison import _service, _solve_stl
from thermoflow.api import create_app
from thermoflow.models import (
    BoxWorkpieceInput,
    DimensionsMM,
    LengthUnit,
    Quantity,
    SimulationOverrides,
    StudyConfirmationRequest,
    StudyCopyRequest,
)
from thermoflow.planner import DeterministicPlanner
from thermoflow.settings import Settings
from thermoflow.storage import RecordNotFoundError
from thermoflow.visualization import ViewRequest, geometry_surface, probe_cell, study_surface
from thermoflow.vtk_io import read_solver_vtk


def test_full_geometry_has_every_triangle_stable_components_and_confirmed_scale(tmp_path):
    service, repository = _service(tmp_path)
    mesh = trimesh.creation.icosphere(subdivisions=4, radius=3)
    part = service.register_stl("sphere.stl", mesh.export(file_type="stl"))
    assert part.geometry.summary["preview"]["sampled"]
    original = geometry_surface(repository, part.workpiece_id)
    assert original.coordinate_unit == "source"
    assert len(original.triangles) == len(mesh.faces) > 1200
    assert set(original.component_ids) == {part.components[0].component_id}
    service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MICROMETER)
    normalized = geometry_surface(repository, part.workpiece_id)
    assert normalized.coordinate_unit == "mm"
    assert np.allclose(normalized.vertices, np.asarray(original.vertices) * 0.001)
    assert normalized.component_ids == original.component_ids


def test_nodal_vtk_surface_and_exact_section_match_fourier_reference(tmp_path):
    service, repository = _service(tmp_path)
    part = service.register_box(BoxWorkpieceInput(name="Reference", dimensions_mm=DimensionsMM(x=10, y=6, z=4)))
    study = service.create_study(part.workpiece_id)
    service.run_study(study.study_id)
    view = study_surface(repository, study.study_id, ViewRequest())
    assert view.triangles and not view.sampled
    assert len(view.temperature_k) == len(view.vertices)
    axis = "xyz".index(study.plan.boundaries[0].selector.axis)
    low = study.plan.boundaries[0].temperature_k
    high = study.plan.boundaries[1].temperature_k
    length = part.dimensions_mm.as_tuple()[axis]
    expected = low + (high - low) * np.asarray(view.vertices)[:, axis] / length
    assert np.allclose(view.temperature_k, expected)
    section = study_surface(repository, study.study_id, ViewRequest(
        section_axis="xyz"[axis], section_position=Quantity(value=length * 0.37 / 1000, unit="m"),
    ))
    assert section.triangles
    assert np.allclose(np.asarray(section.vertices)[:, axis], length * 0.37)
    assert np.allclose(section.temperature_k, low + (high - low) * 0.37)
    probe = probe_cell(repository, study.study_id, section.cell_ids[0], None)
    assert probe.temperature.value == pytest.approx(low + (high - low) * probe.position_mm[axis] / length)
    assert probe.location == "cell_center"
    outside = study_surface(repository, study.study_id, ViewRequest(
        section_axis="x", section_position=Quantity(value=1000, unit="mm"),
    ))
    assert outside.triangles == []


def test_voxel_surface_and_probe_use_all_real_cells_and_flux(tmp_path):
    service, repository = _service(tmp_path)
    mesh = trimesh.creation.box(extents=[12, 8, 4])
    part = service.register_stl("block.stl", mesh.export(file_type="stl"))
    service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    study = service.create_study(part.workpiece_id)
    _solve_stl(service, study.study_id)
    view = study_surface(repository, study.study_id, ViewRequest())
    volume = read_solver_vtk(repository.artifact_path(study.study_id, "temperature.vtk"))
    mesh_view = study_surface(repository, study.study_id, ViewRequest(kind="mesh"))
    assert mesh_view.temperature_k is None
    assert mesh_view.triangles == view.triangles
    assert mesh_view.cell_ids == view.cell_ids
    assert np.allclose(mesh_view.vertices, view.vertices)
    expected_t = np.concatenate(volume.cell_data["temperature_k"]).reshape(-1)
    expected_q = np.concatenate(volume.cell_data["heat_flux_w_m2"])
    assert view.total_cells == len(expected_t)
    assert view.temperature_min_k == pytest.approx(expected_t.min())
    assert view.temperature_max_k == pytest.approx(expected_t.max())
    values_by_position: dict[tuple[float, float, float], list[float]] = {}
    for position, value in zip(view.vertices, view.temperature_k, strict=True):
        values_by_position.setdefault(tuple(position), []).append(value)
    assert all(np.ptp(values) == pytest.approx(0) for values in values_by_position.values())
    assert any(np.ptp(np.asarray(view.temperature_k)[list(triangle)]) > 0 for triangle in view.triangles)
    hidden_cell = next(cell for cell in range(len(expected_t)) if cell not in set(view.cell_ids))
    probe = probe_cell(repository, study.study_id, hidden_cell, None)
    assert probe.temperature.value == pytest.approx(expected_t[hidden_cell])
    assert probe.heat_flux_w_m2 == pytest.approx(expected_q[hidden_cell])
    assert probe.component_id == part.components[0].component_id
    assert probe.source_sha256 == view.source_sha256
    with pytest.raises(RecordNotFoundError):
        probe_cell(repository, study.study_id, -1, None)
    with pytest.raises(ValueError, match="校验失败"):
        repository.artifact_path(study.study_id, "temperature.vtk").write_text("corrupt")
        study_surface(repository, study.study_id, ViewRequest())


def test_transient_selection_and_full_mesh_difference(tmp_path):
    service, repository = _service(tmp_path)
    part = service.register_stl("block.stl", trimesh.creation.box(extents=[12, 8, 4]).export(file_type="stl"))
    service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    study = service.create_study(part.workpiece_id, purpose="6061，瞬态导热，初始温度 420 K，持续 1 秒，步长 0.5 秒")
    _solve_stl(service, study.study_id)
    final_index = repository.get_result(study.study_id).time_steps[-1].index
    first = study_surface(repository, study.study_id, ViewRequest(frame_index=0))
    last = study_surface(repository, study.study_id, ViewRequest(frame_index=final_index))
    assert first.time_s == 0 and last.time_s == 1
    assert first.source_sha256 != last.source_sha256
    assert not np.allclose(first.temperature_k, last.temperature_k)
    probe = probe_cell(repository, study.study_id, first.cell_ids[0], 0)
    assert probe.time_s == 0 and probe.source_sha256 == first.source_sha256
    copy = service.copy_study(study.study_id, StudyCopyRequest(overrides=SimulationOverrides(heat_source_power_w=40)))
    service.confirm_study(copy.study_id, StudyConfirmationRequest(materials_confirmed=True))
    _solve_stl(service, copy.study_id)
    other = study_surface(repository, copy.study_id, ViewRequest(frame_index=final_index))
    difference = study_surface(repository, study.study_id, ViewRequest(
        frame_index=final_index, difference_study_id=copy.study_id,
    ))
    assert np.allclose(difference.temperature_k, np.asarray(other.temperature_k) - last.temperature_k)
    assert difference.triangles == last.triangles
    with pytest.raises(RecordNotFoundError):
        study_surface(repository, study.study_id, ViewRequest(frame_index=1000))


def test_disconnected_results_preserve_component_mapping(tmp_path):
    service, repository = _service(tmp_path)
    first = trimesh.creation.box(extents=[6, 6, 6])
    second = first.copy()
    second.apply_translation([10, 0, 0])
    part = service.register_stl("assembly.stl", (first + second).export(file_type="stl"))
    service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(target_element_size_mm=1))
    _solve_stl(service, study.study_id)
    view = study_surface(repository, study.study_id, ViewRequest())
    assert set(view.component_ids) == {c.component_id for c in part.components}
    for component, triangle in zip(view.component_ids, view.triangles, strict=True):
        mean_x = np.asarray(view.vertices)[list(triangle), 0].mean()
        assert component == part.components[0 if mean_x < 5 else 1].component_id


def test_view_api_rejects_unpublished_fields_and_bad_units(tmp_path):
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / "data", cadflow_repo=tmp_path / "missing",
                        planner_mode="deterministic", compute_backend="cpu")
    app = create_app(settings, DeterministicPlanner())
    client = TestClient(app)
    part = app.state.service.register_box(BoxWorkpieceInput(name="Box", dimensions_mm=DimensionsMM(x=10, y=6, z=4)))
    study = app.state.service.create_study(part.workpiece_id)
    assert client.get(f"/v1/workpieces/{part.workpiece_id}/view").status_code == 200
    assert client.post(f"/v1/studies/{study.study_id}/view", json={}).status_code == 404
    assert client.post(f"/v1/studies/{study.study_id}/view", json={"section_axis": "x",
        "section_position": {"value": 1, "unit": "K"}}).status_code == 422
    app.state.service.run_study(study.study_id)
    response = client.post(f"/v1/studies/{study.study_id}/view", json={})
    assert response.status_code == 200, response.text
    assert client.get(f"/v1/studies/{study.study_id}/cells/0/probe").status_code == 200
    assert client.get("/assets/viewport.mjs").status_code == 200
    assert client.get("/assets/vendor/three/three.module.min.js").status_code == 200
    path = app.state.repository.artifact_path(study.study_id, "temperature.vtk")
    assert response.json()["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
