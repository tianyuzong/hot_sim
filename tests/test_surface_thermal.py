from __future__ import annotations

import numpy as np
import pytest
import trimesh
from scipy.optimize import brentq

from tests.test_study_comparison import _service, _solve_stl
from thermoflow.models import (
    FixedTemperatureBoundary,
    LengthUnit,
    SimulationOverrides,
    StudyConfirmationRequest,
    StudyCopyRequest,
    SurfaceConvectionBoundary,
    SurfaceHeatFluxBoundary,
    SurfaceRadiationBoundary,
)
from thermoflow.solvers.radiation import solve_radiating_system
from thermoflow.thermal_boundaries import STEFAN_BOLTZMANN
from thermoflow.visualization import ViewRequest, study_surface


def _setup(tmp_path, size=10):
    service, repository = _service(tmp_path)
    part = service.register_stl("boundary-test.stl", trimesh.creation.box(extents=[size]*3).export(file_type="stl"))
    part = service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    region = next(r for r in part.regions if r.kind == "component_surface")
    return service, repository, part, region


def _study(service, part, conditions, **kwargs):
    return service.create_study(part.workpiece_id, overrides=SimulationOverrides(
        fixed_boundaries=[], surface_conditions=conditions, enable_heat_source=False,
        enable_global_convection=False, target_element_size_mm=2, relative_tolerance=1e-10, **kwargs,
    ))


@pytest.mark.parametrize("kind", ["convection", "radiation"])
def test_uniform_surface_heating_matches_exact_equilibrium(tmp_path, kind):
    service, repository, part, region = _setup(tmp_path)
    flux = SurfaceHeatFluxBoundary(region_id=region.region_id, heat_flux_w_m2=10000)
    if kind == "convection":
        exchange = SurfaceConvectionBoundary(region_id=region.region_id, ambient_temperature_k=300,
                                              heat_transfer_coefficient_w_m2_k=100)
        expected = 400
    else:
        exchange = SurfaceRadiationBoundary(region_id=region.region_id, radiation_temperature_k=300,
                                             emissivity=0.8, emissivity_source="Test reference")
        expected = (300**4 + 10000 / (0.8 * STEFAN_BOLTZMANN))**0.25
    study = _study(service, part, [flux, exchange])
    assert study.policy.accepted, study.policy.errors
    assert study.simulation_spec.schema_version == "1.3"
    _solve_stl(service, study.study_id)
    result = repository.get_result(study.study_id)
    assert result.temperature_min_k == pytest.approx(expected, rel=1e-7)
    assert result.temperature_max_k == pytest.approx(expected, rel=1e-7)
    assert result.energy_balance_relative_error < 1e-7
    area = result.boundary_mapping[0]["mapped_area"]["value"]
    assert result.boundary_power_balance["surface_heat_flux"].value == pytest.approx(10000 * area)
    assert result.boundary_power_balance[kind].value == pytest.approx(-10000 * area, rel=1e-7)
    assert result.heat_source_mapping is None and result.heat_source_cells is None
    assert result.heat_rate_w == pytest.approx(10000 * area)
    if kind == "radiation":
        assert result.nonlinear_convergence[-1]["residual_relative"] <= 1e-10
        assert len(result.nonlinear_convergence) > 1
    view = study_surface(repository, study.study_id, ViewRequest())
    assert np.allclose(view.temperature_k, expected, rtol=1e-7)


def test_single_cell_radiative_transient_matches_independent_scalar_root(tmp_path):
    service, repository, part, region = _setup(tmp_path, size=1)
    condition = SurfaceRadiationBoundary(region_id=region.region_id, radiation_temperature_k=300,
                                          emissivity=0.8, emissivity_source="Reference")
    study = _study(service, part, [condition], analysis_type="transient_conduction",
                   initial_temperature_k=900, duration_s=1, time_step_s=0.25)
    _solve_stl(service, study.study_id)
    result = repository.get_result(study.study_id)
    assert result.grid["cells"] == 1
    area = result.boundary_mapping[0]["mapped_area"]["value"]
    material = study.plan.component_materials[0].material
    capacity = material.density_kg_m3 * material.specific_heat_j_kg_k * 1e-9
    previous = 900
    previous_time = 0.0
    for index, step in enumerate(result.time_steps[1:], start=1):
        dt = step.time_s - previous_time
        expected = brentq(
            lambda value, dt=dt, previous=previous: capacity / dt * (value - previous)
            + 0.8 * STEFAN_BOLTZMANN * area * (value**4 - 300**4),
            300,
            previous,
        )
        assert step.temperature_max_k == pytest.approx(expected, rel=1e-8)
        assert step.energy_balance_relative_error < 1e-7
        assert step.boundary_power_balance["radiation"].value < 0
        previous = expected
        previous_time = step.time_s
    assert result.thermal_resistance_k_w is None
    assert result.boundary_power_balance["radiation"].value < 0
    assert result.boundary_power_balance["storage"].value > 0
    assert result.energy_balance_relative_error < 1e-7


def test_insulated_transient_preserves_temperature_and_steady_requires_anchor(tmp_path):
    service, repository, part, _ = _setup(tmp_path)
    steady = _study(service, part, [])
    mesh = service.generate_mesh(steady.study_id)
    assert mesh.quality_status == "blocked"
    assert any("温度基准" in message for message in mesh.warnings)
    transient = _study(service, part, [], analysis_type="transient_conduction",
                       initial_temperature_k=350, duration_s=1, time_step_s=0.5)
    _solve_stl(service, transient.study_id)
    result = repository.get_result(transient.study_id)
    assert result.temperature_min_k == pytest.approx(350)
    assert result.temperature_max_k == pytest.approx(350)
    assert result.thermal_resistance_k_w is None


def test_overlapping_exchange_and_prescribed_cells_are_blocked(tmp_path):
    service, _, part, region = _setup(tmp_path)
    condition = SurfaceConvectionBoundary(region_id=region.region_id, ambient_temperature_k=300,
                                           heat_transfer_coefficient_w_m2_k=100)
    overlap = _study(service, part, [condition, condition])
    mesh = service.generate_mesh(overlap.study_id)
    assert mesh.quality_status == "blocked"
    assert any("重叠" in message for message in mesh.warnings)
    conflicting = service.create_study(part.workpiece_id, overrides=SimulationOverrides(surface_conditions=[condition]))
    mesh = service.generate_mesh(conflicting.study_id)
    assert mesh.quality_status == "blocked"
    assert any("定温单元" in message for message in mesh.warnings)


def test_perpendicular_exchange_face_can_touch_fixed_boundary_cells(tmp_path):
    service, _, part, _ = _setup(tmp_path)
    fixed = next(region for region in part.regions if region.selector == "face.xmax")
    side = next(region for region in part.regions if region.selector == "face.ymin")
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(
        fixed_boundaries=[FixedTemperatureBoundary(region_id=fixed.region_id, temperature_k=300)],
        surface_conditions=[SurfaceConvectionBoundary(
            region_id=side.region_id,
            ambient_temperature_k=300,
            heat_transfer_coefficient_w_m2_k=100,
        )],
        enable_heat_source=False,
        enable_global_convection=False,
        target_element_size_mm=1,
    ))
    mesh = service.generate_mesh(study.study_id)

    assert mesh.quality_status != "blocked"
    assert any(item["region_id"] == side.region_id for item in mesh.boundary_mapping)
    assert not any("定温单元的同一外露面" in message for message in mesh.warnings)


def test_surface_overrides_and_copy_preserve_confirmation_contract(tmp_path):
    service, repository, part, region = _setup(tmp_path)
    condition = SurfaceConvectionBoundary(region_id=region.region_id, ambient_temperature_k=300,
                                           heat_transfer_coefficient_w_m2_k=100)
    study = _study(service, part, [condition])
    copied = service.copy_study(study.study_id, StudyCopyRequest())
    assert copied.plan.surface_conditions == [condition]
    assert not copied.plan.heat_source_enabled and not copied.plan.global_convection_enabled
    assert copied.plan.boundaries == []
    with pytest.raises(ValueError, match="确认"):
        service.generate_mesh(copied.study_id)
    service.confirm_study(copied.study_id, StudyConfirmationRequest(materials_confirmed=True))
    _solve_stl(service, copied.study_id)
    result = repository.get_result(copied.study_id)
    assert result.temperature_max_k == pytest.approx(300)
    assert result.thermal_resistance_k_w is None


def test_radiation_rejects_nonphysical_cooling_solution():
    from scipy.sparse import csr_matrix
    with pytest.raises(RuntimeError, match="非物理"):
        solve_radiating_system(csr_matrix([[0.0]]), np.array([-1.0]), np.array([1.0]), np.array([300.0]),
                               relative_tolerance=1e-10, max_iterations=100, preference="cpu")


def test_flux_to_regional_convection_matches_linear_reference_on_aligned_grids(tmp_path):
    service, repository, part, _ = _setup(tmp_path, size=10)
    left = next(r for r in part.regions if r.selector == "face.xmin")
    right = next(r for r in part.regions if r.selector == "face.xmax")
    conditions = [SurfaceHeatFluxBoundary(region_id=left.region_id, heat_flux_w_m2=10000),
                  SurfaceConvectionBoundary(region_id=right.region_id, ambient_temperature_k=300,
                                             heat_transfer_coefficient_w_m2_k=100)]
    # Linear temperature is exactly represented at these aligned cell centers.
    # Refinement must retain the exact solution, not introduce a volume-dependent conductivity.
    for pitch in (2.5, 1.25, 0.625):
        study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(
            fixed_boundaries=[], surface_conditions=conditions, enable_heat_source=False,
            enable_global_convection=False, target_element_size_mm=pitch, relative_tolerance=1e-10,
        ))
        _solve_stl(service, study.study_id)
        result = repository.get_result(study.study_id)
        expected_peak = 300 + 10000 / 100 + 10000 * 0.01 / study.plan.material.thermal_conductivity_w_m_k
        assert result.temperature_min_k == pytest.approx(400, rel=1e-7)
        assert result.temperature_max_k == pytest.approx(expected_peak, abs=1e-6)
        assert result.energy_balance_relative_error < 1e-7


def test_insulated_power_heating_matches_total_heat_capacity(tmp_path):
    service, repository, part, _ = _setup(tmp_path)
    study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(
        fixed_boundaries=[], enable_global_convection=False, heat_source_power_w=10,
        analysis_type="transient_conduction", initial_temperature_k=350, duration_s=1,
        time_step_s=0.25, target_element_size_mm=2, relative_tolerance=1e-10,
    ))
    _solve_stl(service, study.study_id)
    result = repository.get_result(study.study_id)
    final_index = result.time_steps[-1].index
    field = np.load(repository.artifact_path(study.study_id, f"thermal-{final_index:04d}.npz"))
    material = study.plan.material
    expected_mean = 350 + 10 / (material.density_kg_m3 * material.specific_heat_j_kg_k * 1e-6)
    assert field["temperature_k"].mean() == pytest.approx(expected_mean, rel=1e-8)
    assert result.boundary_power_balance["storage"].value == pytest.approx(-10, rel=1e-7)


def test_each_disconnected_component_requires_its_own_steady_anchor(tmp_path):
    service, _ = _service(tmp_path)
    first = trimesh.creation.box(extents=[6, 6, 6])
    second = first.copy()
    second.apply_translation([12, 0, 0])
    part = service.register_stl("assembly.stl", (first + second).export(file_type="stl"))
    part = service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    region = next(r for r in part.regions if r.kind == "component_surface")
    study = _study(service, part, [SurfaceConvectionBoundary(region_id=region.region_id,
                    ambient_temperature_k=300, heat_transfer_coefficient_w_m2_k=100)])
    mesh = service.generate_mesh(study.study_id)
    assert mesh.quality_status == "blocked"
    assert any("连通组件" in message for message in mesh.warnings)


def test_regional_convection_replaces_global_term_and_null_resistance_comparison(tmp_path):
    from thermoflow.models import StudyComparisonRequest
    service, repository, part, region = _setup(tmp_path)
    ids = []
    for ambient in (330, 350):
        study = service.create_study(part.workpiece_id, overrides=SimulationOverrides(
            fixed_boundaries=[], enable_heat_source=False, ambient_temperature_k=300,
            convection_coefficient_w_m2_k=100, target_element_size_mm=2,
            surface_conditions=[SurfaceConvectionBoundary(region_id=region.region_id,
                ambient_temperature_k=ambient, heat_transfer_coefficient_w_m2_k=200)],
        ))
        _solve_stl(service, study.study_id)
        result = repository.get_result(study.study_id)
        assert result.temperature_max_k == pytest.approx(ambient)
        assert result.energy_balance_relative_error < 1e-7
        assert result.energy_balance_reference_power.unit == "W"
        assert result.energy_balance_reference_power.value > 0
        assert result.energy_balance_relative_error == pytest.approx(
            abs(result.boundary_power_balance["residual"].value) / result.energy_balance_reference_power.value,
        )
        ids.append(study.study_id)
    comparison = service.compare_studies(StudyComparisonRequest(study_ids=ids))
    assert comparison.differences[0].thermal_resistance_delta is None
    assert all(study.thermal_resistance is None for study in comparison.studies)


def test_surface_schema_units_confirmation_and_radiation_scope(tmp_path):
    from fastapi.testclient import TestClient

    from thermoflow.api import create_app
    from thermoflow.planner import DeterministicPlanner
    from thermoflow.settings import Settings
    app = create_app(Settings(project_root=tmp_path, data_dir=tmp_path / "data",
        cadflow_repo=tmp_path / "missing", planner_mode="deterministic", compute_backend="cpu"), DeterministicPlanner())
    client = TestClient(app)
    part = app.state.service.register_stl("block.stl", trimesh.creation.box(extents=[10]*3).export(file_type="stl"))
    part = app.state.service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    region = next(r for r in part.regions if r.kind == "component_surface")
    radiation = {"kind": "radiation", "region_id": region.region_id,
                 "radiation_temperature_k": 300, "emissivity": 0.8, "emissivity_source": "User reference"}
    payload = {"workpiece_id": part.workpiece_id, "purpose": "环境辐射冷却",
               "overrides": {"fixed_boundaries": [], "enable_heat_source": False,
                             "enable_global_convection": False, "surface_conditions": [radiation]}}
    created = client.post("/v1/studies", json=payload)
    assert created.status_code == 201, created.text
    study = created.json()
    prefix = f"/v1/studies/{study['study_id']}"
    assert client.post(prefix + "/mesh").status_code == 409
    assert client.post(
        prefix + "/confirm", json={"materials_confirmed": True}
    ).status_code == 200
    spec = client.get(prefix + "/spec").json()
    assert spec["schema_version"] == "1.3"
    assert spec["physics"]["unsupported"] == []
    assert len(spec["conditions"]["thermal"]) == 1
    condition = spec["conditions"]["thermal"][0]
    assert condition["radiation_temperature"] == {"value": 300, "unit": "K"}
    assert condition["emissivity"] == {"value": 0.8, "unit": "1"}
    assert condition["target_refs"] == [region.region_id]
    radiation["emissivity_source"] = " "
    assert client.post("/v1/studies", json=payload).status_code == 422
    radiation["emissivity_source"] = "User reference"
    payload["purpose"] = "表面间辐射需要视角因子"
    complex_study = client.post("/v1/studies", json=payload).json()
    assert any("视角因子" in item for item in complex_study["plan"]["unsupported_physics"])


def test_planner_region_context_is_compact_and_variants_keep_materials(tmp_path):
    import json

    from thermoflow.agent import _overrides_from_plan
    from thermoflow.planner import _planner_context, apply_user_overrides
    from thermoflow.regions import SurfaceRegionRequest, create_surface_region
    service, repository, part, region = _setup(tmp_path)
    patch = create_surface_region(repository, part.workpiece_id, SurfaceRegionRequest(name="Patch", triangle_ids=[0, 1]))
    part = service.get_workpiece(part.workpiece_id)
    context = _planner_context(part)
    entry = next(item for item in context["regions"] if item["region_id"] == patch.region_id)
    assert entry["triangle_count"] == 2
    assert "radiation" in entry["supported_condition_kinds"]
    assert "triangle_ids" not in json.dumps(context)
    assert "preview" not in context["geometry_summary"]
    study = _study(service, part, [SurfaceConvectionBoundary(region_id=region.region_id,
        ambient_temperature_k=300, heat_transfer_coefficient_w_m2_k=100)])
    updated = apply_user_overrides(study.plan, _overrides_from_plan(study.plan, {"target_element_size_mm": 1}))
    assert updated.component_materials == study.plan.component_materials
    assert updated.surface_conditions == study.plan.surface_conditions
    assert not updated.heat_source_enabled and not updated.global_convection_enabled
