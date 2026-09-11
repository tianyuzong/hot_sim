import numpy as np
import pytest
import trimesh
from scipy.sparse.linalg import spsolve

from tests.test_study_comparison import _service, _solve_stl
from thermoflow.models import (
    LengthUnit,
    Point3DMM,
    Quantity,
    SimulationOverrides,
    StudyConfirmationRequest,
    VolumetricHeatSource,
)
from thermoflow.solvers.tetra_stl import assemble, map_source
from thermoflow.tetrahedral import read_tetra_mesh
from thermoflow.visualization import (
    ViewRequest,
    _tetra_section,
    probe_cell,
    study_playback,
    study_surface,
)


def test_linear_tetra_patch_preserves_capacity_gradient_and_constant_nullspace():
    points = np.array([[0, 0, 0], [2, 0, 0], [0, 3, 0], [0, 0, 4]], dtype=float)
    cells = np.array([[0, 1, 2, 3]])
    k, capacity, gradient, volume, area, _ = assemble(
        points, cells, np.array([17]), np.array([2000]), np.array([500])
    )
    assert volume.sum() == pytest.approx(4e-9)
    assert capacity.sum() == pytest.approx(0.004)
    assert area.sum() == pytest.approx((13 + np.sqrt(61)) * 1e-6)
    assert np.allclose(k @ np.ones(4), 0, atol=1e-14)
    expected = np.array([1.0, -2.0, 3.0])
    nodal = 300 + points @ expected / 1000
    assert np.einsum("eij,ei->ej", gradient, nodal[cells])[0] == pytest.approx(expected)
    from scipy.sparse import diags

    # Uniform volumetric heating has an exact lumped-capacity solution.
    rate = 10
    solution = spsolve(k + diags(capacity / 0.1), capacity / 0.1 * 300 + capacity * rate)
    assert solution == pytest.approx(np.full(4, 301))
    request = ViewRequest(section_axis="z", section_position=Quantity(value=1, unit="mm"))
    vertices, owners, values = _tetra_section(points, cells, nodal, request)
    assert len(owners) == 1
    assert np.allclose(values, 300 + vertices @ expected / 1000)


def test_variable_tetra_volumes_receive_uniform_source_density():
    source = VolumetricHeatSource(
        shape="point",
        placement="embedded",
        center_mm=Point3DMM(x=0, y=0, z=0),
        radius_mm=10,
        total_power_w=12,
    )
    power, mapping = map_source(
        np.array([[0.0, 0, 0], [1, 0, 0]]), np.array([1e-9, 3e-9]), np.array([0, 1]), source
    )
    assert power == pytest.approx([3, 9])
    assert mapping["total_power_w"] == pytest.approx(sum(power))


@pytest.mark.parametrize("unit,scale", [("mm", 1), ("um", 1000), ("m", 0.001)])
def test_thin_disconnected_components_keep_geometry_units_and_ownership(tmp_path, unit, scale):
    service, repo = _service(tmp_path)
    first = trimesh.creation.box(extents=[20, 15, 1])
    second = first.copy()
    second.apply_translation([0, 0, 1.05])
    shape = trimesh.util.concatenate([first, second])
    shape.apply_scale(scale)
    part = service.register_stl("thin-pair.stl", shape.export(file_type="stl"))
    service.confirm_workpiece_unit(part.workpiece_id, LengthUnit(unit))
    study = service.create_study(
        part.workpiece_id,
        SimulationOverrides(solver_backend="tetra_stl_v1", target_element_size_mm=8),
        planning_mode="manual",
        require_confirmation=True,
    )
    assert study.policy.accepted, study.policy.errors
    study = service.confirm_study(
        study.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )
    mesh = service.generate_mesh(study.study_id)
    assert mesh.quality.connected_regions == mesh.quality.expected_components == 2
    assert mesh.quality.volume_deviation_percent < 1e-6
    arrays, _ = read_tetra_mesh(repo.study_dir(study.study_id) / "artifacts")
    assert arrays["volumes_mm3"].sum() == pytest.approx(600, rel=1e-6)
    surface = study_surface(repo, study.study_id, ViewRequest(kind="mesh"))
    assert set(surface.component_ids) == {c.component_id for c in part.components}
    # An ownership artifact cannot be substituted behind a reviewed mesh.
    path = repo.artifact_path(study.study_id, "tetra-mesh.npz")
    path.write_bytes(path.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="校验失败"):
        read_tetra_mesh(path.parent)


def test_real_transient_frames_preserve_component_materials_and_nodal_field(tmp_path):
    service, repo = _service(tmp_path)
    first = trimesh.creation.box(extents=[10, 10, 1])
    second = first.copy()
    second.apply_translation([30, 0, 0])
    part = service.register_stl(
        "independent.stl", trimesh.util.concatenate([first, second]).export(file_type="stl")
    )
    service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    source = VolumetricHeatSource(
        shape="point",
        placement="embedded",
        center_mm=Point3DMM(x=0, y=0, z=0),
        radius_mm=15,
        total_power_w=1,
    )
    study = service.create_study(
        part.workpiece_id,
        SimulationOverrides(
            solver_backend="tetra_stl_v1",
            target_element_size_mm=5,
            duration_s=0.2,
            time_step_s=0.001,
            enable_global_convection=False,
            enable_heat_source=True,
            heat_sources=[source],
        ),
        planning_mode="manual",
        require_confirmation=True,
    )
    study = service.confirm_study(
        study.study_id, StudyConfirmationRequest(materials_confirmed=True)
    )
    _solve_stl(service, study.study_id)
    result = repo.get_result(study.study_id)
    material = study.plan.component_materials[0].material
    rise = 0.2 / (material.density_kg_m3 * material.specific_heat_j_kg_k * 1e-7)
    assert result.temperature_max_k == pytest.approx(293.15 + rise, abs=1e-8)
    assert result.temperature_min_k == pytest.approx(293.15, abs=1e-8)
    assert sum(s.stored_energy_change_j for s in result.time_steps) == pytest.approx(0.2)
    assert result.maximum_energy_balance_error_over_time < 1e-8
    playback = study_playback(repo, study.study_id)
    assert len(playback.frames) == 201
    assert playback.frames[-1].time_s == 0.2
    assert set(playback.component_ids) == {c.component_id for c in part.components}
    data = np.load(repo.artifact_path(study.study_id, "thermal-0200.npz"))
    surface = study_surface(repo, study.study_id, ViewRequest(frame_index=200))
    assert surface.temperature_max_k == pytest.approx(data["nodal_temperature_k"].max())
    assert playback.frames[-1].temperature_max_k == pytest.approx(surface.temperature_max_k)
    probe = probe_cell(repo, study.study_id, surface.cell_ids[0], 200)
    assert probe.component_id == surface.component_ids[0]
    assert probe.temperature.value == pytest.approx(data["temperature_k"][surface.cell_ids[0]])
    # All displayed values come from the exact nodal solution, including compact frames.
    arrays, _ = read_tetra_mesh(repo.study_dir(study.study_id) / "artifacts")
    values = {tuple(p): t for p, t in zip(arrays["points"], data["nodal_temperature_k"])}
    assert np.allclose(
        playback.frames[-1].temperature_k, [values[tuple(p)] for p in playback.vertices]
    )


def test_tetra_policy_rejects_unsupported_physics_instead_of_ignoring_it(tmp_path):
    service, _ = _service(tmp_path)
    part = service.register_stl(
        "solid.stl", trimesh.creation.box(extents=[10, 10, 2]).export(file_type="stl")
    )
    service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    study = service.create_study(
        part.workpiece_id,
        SimulationOverrides(solver_backend="tetra_stl_v1", analysis_type="steady_state_conduction"),
        planning_mode="manual",
    )
    assert not study.policy.accepted
    assert any("四面体求解器" in e for e in study.policy.errors)
