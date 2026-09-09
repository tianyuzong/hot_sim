from __future__ import annotations

import pytest

from thermoflow.models import Point3DMM, SimulationOverrides, VolumetricHeatSource
from thermoflow.planner import DeterministicPlanner, apply_user_overrides
from thermoflow.policy import validate_plan
from thermoflow.solvers import AnalyticBoxSolver
from thermoflow.solvers.voxel_stl import VoxelStlSolver

from .helpers import box_workpiece


def test_analytic_box_solver_matches_fourier_law(tmp_path) -> None:
    workpiece = box_workpiece()
    plan = DeterministicPlanner().plan(workpiece).plan
    policy = validate_plan(workpiece, plan)

    result = AnalyticBoxSolver().solve(
        study_id="study-test",
        workpiece=workpiece,
        plan=plan,
        policy=policy,
        artifact_dir=tmp_path,
    )

    assert result.temperature_min_k == pytest.approx(293.15)
    assert result.temperature_max_k == pytest.approx(373.15)
    assert result.heat_flux_w_m2 == pytest.approx(-133_600.0)
    assert result.heat_rate_w == pytest.approx(26.72)
    assert result.thermal_resistance_k_w == pytest.approx(0.1 / (167.0 * 0.0002))
    assert result.energy_balance_relative_error == 0.0
    assert result.grid["points"] == 156

    vtk_path = tmp_path / "temperature.vtk"
    assert vtk_path.is_file()
    payload = vtk_path.read_text(encoding="ascii")
    assert "DATASET UNSTRUCTURED_GRID" in payload
    assert "SCALARS temperature_k double 1" in payload
    assert result.artifacts[0].size_bytes == vtk_path.stat().st_size


def test_box_with_local_heat_source_uses_real_voxel_solution(tmp_path) -> None:
    workpiece = box_workpiece()
    plan = apply_user_overrides(
        DeterministicPlanner().plan(workpiece).plan,
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
    policy = validate_plan(workpiece, plan)
    assert policy.accepted, policy.errors

    result = VoxelStlSolver(compute_backend="cpu").solve(
        study_id="study-box-source",
        workpiece=workpiece,
        plan=plan,
        policy=policy,
        artifact_dir=tmp_path / "studies" / "study-box-source" / "artifacts",
    )

    assert result.solver_backend == "voxel_stl_v1"
    assert result.heat_source_power_w == pytest.approx(20)
    assert result.heat_source_cells and result.heat_source_cells > 0
    assert result.heat_source_mapping["shape"] == "surface"
    assert result.boundary_power_balance["volumetric_source"].value == pytest.approx(20)
    assert result.energy_balance_relative_error < 1e-6


def test_voxel_solver_superposes_point_line_surface_and_volume_sources(tmp_path) -> None:
    workpiece = box_workpiece()
    base = DeterministicPlanner().plan(workpiece).plan
    sources = [
        VolumetricHeatSource(
            source_id="point-1",
            name="点热源 1",
            shape="point",
            center_mm=Point3DMM(x=20, y=10, z=5),
            total_power_w=10,
            radius_mm=3,
        ),
        VolumetricHeatSource(
            source_id="line-1",
            name="线热源 1",
            shape="line",
            center_mm=Point3DMM(x=35, y=6, z=5),
            end_mm=Point3DMM(x=45, y=14, z=5),
            total_power_w=20,
            radius_mm=3,
        ),
        VolumetricHeatSource(
            source_id="surface-1",
            name="面热源 1",
            shape="surface",
            center_mm=Point3DMM(x=60, y=10, z=5),
            total_power_w=30,
            radius_mm=1,
            surface_normal_axis="z",
            surface_width_mm=12,
            surface_height_mm=8,
            surface_thickness_mm=5,
        ),
        VolumetricHeatSource(
            source_id="volume-1",
            name="体热源 1",
            shape="volume",
            center_mm=Point3DMM(x=80, y=10, z=5),
            total_power_w=40,
            radius_mm=1,
            volume_width_mm=12,
            volume_height_mm=8,
            volume_depth_mm=6,
        ),
    ]
    plan = apply_user_overrides(
        base,
        SimulationOverrides(enable_heat_source=True, heat_sources=sources),
    )
    policy = validate_plan(workpiece, plan)
    assert policy.accepted, policy.errors

    result = VoxelStlSolver(compute_backend="cpu").solve(
        study_id="study-multiple-sources",
        workpiece=workpiece,
        plan=plan,
        policy=policy,
        artifact_dir=tmp_path / "studies" / "study-multiple-sources" / "artifacts",
    )

    assert result.heat_source_power_w == pytest.approx(100)
    assert result.boundary_power_balance["volumetric_source"].value == pytest.approx(100)
    assert [mapping["shape"] for mapping in result.heat_source_mappings] == [
        "point", "line", "surface", "volume",
    ]
    assert [mapping["source_id"] for mapping in result.heat_source_mappings] == [
        "point-1", "line-1", "surface-1", "volume-1",
    ]
    assert all(mapping["mapped_cell_count"] > 0 for mapping in result.heat_source_mappings)
    assert result.heat_source_mapping == result.heat_source_mappings[0]
    assert result.energy_balance_relative_error < 1e-6
