import numpy as np
import pytest
import trimesh

from tests.test_study_comparison import _service, _solve_stl
from thermoflow.models import (
    ComponentMaterialAssignment,
    LengthUnit,
    SimulationOverrides,
    SurfaceHeatFluxBoundary,
)
from thermoflow.visualization import ViewRequest, probe_cell, study_surface


@pytest.mark.parametrize("sizes, densities", [((4, 4), (1000, 8000)), ((4, 8), (1000, 1000))])
@pytest.mark.parametrize("unit", [LengthUnit.MILLIMETER, LengthUnit.MICROMETER, LengthUnit.METER])
def test_each_component_heats_using_its_own_material_and_physical_volume(tmp_path, sizes, densities, unit):
    service, repo = _service(tmp_path)
    shells = []
    for size, position in zip(sizes, [(0.1, 10, 0), (0.2, -10, 0)], strict=True):
        shell = trimesh.creation.box(extents=[size] * 3)
        shell.apply_translation(position)
        shell.apply_scale(1 / unit.scale_to_mm)
        shells.append(shell)
    part = service.register_stl("separate-bodies.stl", trimesh.util.concatenate(shells).export(file_type="stl"))
    part = service.confirm_workpiece_unit(part.workpiece_id, unit)
    assert len(part.components) == 2
    assignments, conditions, expectations = [], [], []
    from thermoflow.materials import list_materials
    for component in part.components:
        upper = component.bbox_source[1] > 0
        index = 0 if upper else 1
        material = list_materials()[0].material.model_copy(update={
            "density_kg_m3": densities[index], "specific_heat_j_kg_k": 1000,
            "source_type": "user", "source_basis": "Synthetic heat-capacity reference",
        })
        assignments.append(ComponentMaterialAssignment(component_id=component.component_id, material=material))
        region = next(r for r in part.regions if r.kind == "component_surface" and r.component_ids == [component.component_id])
        conditions.append(SurfaceHeatFluxBoundary(region_id=region.region_id, heat_flux_w_m2=1000))
        expectations.append((upper, region.region_id, densities[index] * 1000 * sizes[index]**3 * 1e-9))
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(
        component_materials=assignments, fixed_boundaries=[], surface_conditions=conditions,
        enable_heat_source=False, enable_global_convection=False,
        analysis_type="transient_conduction", initial_temperature_k=300,
        duration_s=1, time_step_s=0.25, target_element_size_mm=1, relative_tolerance=1e-10,
    ))
    assert study.policy.accepted, study.policy.errors
    _solve_stl(service, study.study_id)
    result = repo.get_result(study.study_id)
    final_index = result.time_steps[-1].index
    field = np.load(repo.artifact_path(study.study_id, f"thermal-{final_index:04d}.npz"))
    for upper, region_id, capacity in expectations:
        cells = (field["centers_mm"][:, 1] > 0) == upper
        area = next(m["mapped_area"]["value"] for m in result.boundary_mapping if m.get("region_id") == region_id)
        # No inter-body connection or losses: ΔT_mean = q'' A t / (rho cp V).
        expected = 300 + 1000 * area / capacity
        assert field["temperature_k"][cells].mean() == pytest.approx(expected, rel=1e-7)
    assert result.maximum_energy_balance_error_over_time < 1e-8
    # Display and probing must retain the very same original component IDs.
    view = study_surface(repo, study.study_id, ViewRequest(frame_index=final_index))
    triangle_centers = np.asarray(view.vertices)[view.triangles].mean(axis=1)
    for component in part.components:
        upper = component.bbox_source[1] > 0
        selected = np.flatnonzero((triangle_centers[:, 1] > 0) == upper)
        assert selected.size
        assert {view.component_ids[i] for i in selected} == {component.component_id}
        probe = probe_cell(repo, study.study_id, view.cell_ids[selected[0]], final_index)
        assert probe.component_id == component.component_id


def test_unconnected_extra_body_cannot_change_existing_steady_temperature(tmp_path):
    service, repo = _service(tmp_path)
    small = trimesh.creation.box(extents=[4] * 3)
    small.apply_translation([0.1, 10, 0])
    large = trimesh.creation.box(extents=[8] * 3)
    large.apply_translation([0.2, -10, 0])
    peaks = []
    for geometry in (small, trimesh.util.concatenate([small, large])):
        part = service.register_stl("independent.stl", geometry.export(file_type="stl"))
        part = service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
        study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(
            fixed_boundaries=[], ambient_temperature_k=300, convection_coefficient_w_m2_k=100,
            heat_source_power_w=0.1, heat_source_x_mm=0.1, heat_source_y_mm=10, heat_source_z_mm=0,
            heat_source_radius_mm=1, heat_source_embedding_depth_mm=0,
            target_element_size_mm=1, relative_tolerance=1e-10,
        ))
        _solve_stl(service, study.study_id)
        peaks.append(repo.get_result(study.study_id).temperature_max_k)
    assert peaks[0] > 300
    assert peaks[1] == pytest.approx(peaks[0], rel=1e-7)


def test_contact_does_not_create_a_spurious_heat_flux_peak(tmp_path):
    from tests.test_contact_thermal import _contact, _two_shell_service
    from thermoflow.models import FaceSelector, FixedTemperatureBoundary
    service, repo, part, regions = _two_shell_service(tmp_path)
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(
        fixed_boundaries=[FixedTemperatureBoundary(selector=FaceSelector.X_MIN, temperature_k=300),
                          FixedTemperatureBoundary(selector=FaceSelector.X_MAX, temperature_k=400)],
        enable_heat_source=False, enable_global_convection=False, contacts=[_contact(part, regions)],
        target_element_size_mm=1, relative_tolerance=1e-10,
    ))
    _solve_stl(service, study.study_id)
    result = repo.get_result(study.study_id)
    samples = np.asarray(result.heat_flux_field_preview.samples)
    # The insulated equal-section bars form a one-dimensional series circuit.
    # Internal conduction and contact transfer carry the same power per voxel face.
    contact = next(m for m in result.boundary_mapping if m.get("kind") == "thermal_contact")
    expected = -result.boundary_power_balance["thermal_contact_transfer"].value / contact["mapped_area"]["value"]
    assert samples[:, 3] == pytest.approx(np.full(len(samples), expected), rel=1e-7)
    # Scale the zero transverse-flow tolerance to the ~10^6 W/m² axial flow.
    assert np.abs(samples[:, 4:6]).max() < abs(expected) * 1e-8


def test_ambiguous_components_block_solve_but_keep_mesh_inspectable(tmp_path):
    service, repo = _service(tmp_path)
    shells = []
    for extents, angles, translation in [
        ([1.13677, 1.22904, 1.46726], [0.70928, 0.30141, 1.08334], [1.11535, 1.19183, -1.35524]),
        ([0.56047, 1.06474, 1.10658], [1.45429, 0.56644, 3.06499], [0.7487, -1.4381, 0.38684]),
    ]:
        shell = trimesh.creation.box(extents=extents)
        shell.apply_transform(trimesh.transformations.euler_matrix(*angles))
        shell.apply_translation(translation)
        shells.append(shell)
    part = service.register_stl("underresolved.stl", trimesh.util.concatenate(shells).export(file_type="stl"))
    part = service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(
        fixed_boundaries=[], enable_heat_source=False, enable_global_convection=False,
        analysis_type="transient_conduction", initial_temperature_k=300,
        duration_s=1, time_step_s=0.25, target_element_size_mm=1,
    ))
    mesh = service.generate_mesh(study.study_id)
    assert mesh.quality_status == "blocked"
    assert mesh.quality.connected_regions == len(part.components) == 2
    view = study_surface(repo, study.study_id, ViewRequest(kind="mesh"))
    assert view.triangles
    assert set(view.component_ids) == {None}
    assert repo.get_study(study.study_id).mesh_status.value == "blocked"
    # Fallback applies to ambiguous ownership, never to corrupted geometry.
    original = repo.workpiece_dir(part.workpiece_id) / "source.stl"
    original.write_bytes(original.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="指纹不匹配"):
        study_surface(repo, study.study_id, ViewRequest(kind="mesh"))
