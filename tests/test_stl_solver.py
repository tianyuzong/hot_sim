from __future__ import annotations

import math

import pytest
import trimesh

from thermoflow.models import CadFormat, LengthUnit, Point3DMM, WorkpieceKind, WorkpieceRecord
from thermoflow.planner import DeterministicPlanner
from thermoflow.policy import validate_plan
from thermoflow.solvers import VoxelStlSolver
from thermoflow.stl_geometry import inspect_stl


def test_closed_stl_is_inspected_and_solved(tmp_path) -> None:
    stl_path = tmp_path / "cadflow-output.stl"
    stl_path.write_bytes(trimesh.creation.box(extents=[100, 20, 10]).export(file_type="stl"))
    inspected = inspect_stl(stl_path)
    assert inspected.geometry.available
    assert inspected.geometry.engine == "trimesh-stl"
    assert inspected.dimensions_mm is not None
    assert inspected.dimensions_mm.as_tuple() == pytest.approx((100, 20, 10))

    data_dir = tmp_path / "data"
    workpiece_id = "wp-stl-test"
    source_dir = data_dir / "workpieces" / workpiece_id
    source_dir.mkdir(parents=True)
    source_path = source_dir / "source.stl"
    source_path.write_bytes(stl_path.read_bytes())
    workpiece = WorkpieceRecord(
        workpiece_id=workpiece_id,
        kind=WorkpieceKind.CAD_FILE,
        name="cadflow-output",
        content_sha256="0" * 64,
        dimensions_mm=inspected.dimensions_mm,
        cad_format=CadFormat.STL,
        stored_filename="source.stl",
        geometry=inspected.geometry,
        source_dimensions=inspected.dimensions_mm,
        length_unit=LengthUnit.MILLIMETER,
        unit_confirmed=True,
    )
    plan = DeterministicPlanner().plan(workpiece).plan
    assert plan.heat_source is not None
    plan = plan.model_copy(
        update={
            "heat_source": plan.heat_source.model_copy(
                update={
                    "shape": "surface",
                    "placement": "surface",
                    "surface_normal_axis": "z",
                    "surface_width_mm": 30.0,
                    "surface_height_mm": 8.0,
                    "surface_thickness_mm": 2.0,
                }
            )
        }
    )
    policy = validate_plan(workpiece, plan)
    assert policy.accepted
    assert plan.solver.backend == "voxel_stl_v1"

    result = VoxelStlSolver().solve(
        study_id="study-stl-test",
        workpiece=workpiece,
        plan=plan,
        policy=policy,
        artifact_dir=data_dir / "studies" / "study-stl-test" / "artifacts",
    )

    assert result.temperature_min_k == pytest.approx(293.15)
    assert result.temperature_max_k > result.temperature_min_k
    assert result.heat_rate_w == pytest.approx(25.0)
    assert result.heat_source_power_w == pytest.approx(25.0)
    assert result.heat_source_cells is not None and result.heat_source_cells > 0
    assert result.heat_source_mapping is not None
    assert result.heat_source_mapping["shape"] == "surface"
    assert result.heat_source_mapping["placement"] == "surface"
    assert result.temperature_field_preview is not None
    assert result.temperature_field_preview.total_surface_cells > 0
    assert len(result.temperature_field_preview.samples) <= 4_000
    assert all(len(sample) == 4 for sample in result.temperature_field_preview.samples)
    assert result.temperature_field_preview.total_volume_cells == result.grid["cells"]
    assert len(result.temperature_field_preview.volume_samples) <= 8_000
    assert all(len(sample) == 4 for sample in result.temperature_field_preview.volume_samples)
    assert result.heat_flux_field_preview is not None
    assert result.heat_flux_field_preview.total_surface_cells > 0
    assert result.heat_flux_field_preview.maximum_magnitude_w_m2 > 0
    assert len(result.heat_flux_field_preview.samples) <= 1_200
    assert all(len(sample) == 7 for sample in result.heat_flux_field_preview.samples)
    assert all(
        sample[6] == pytest.approx(math.hypot(sample[3], sample[4], sample[5]))
        for sample in result.heat_flux_field_preview.samples
    )
    assert result.energy_balance_relative_error < 1e-6
    assert result.grid["cells"] > 0
    vtk_path = data_dir / "studies" / "study-stl-test" / "artifacts" / "temperature.vtk"
    assert vtk_path.is_file()
    vtk = vtk_path.read_text(encoding="ascii")
    assert "VECTORS heat_flux_w_m2 double" in vtk
    assert "SCALARS heat_flux_magnitude_w_m2 double 1" in vtk


def test_open_stl_is_reconstructed_and_solved(tmp_path) -> None:
    mesh = trimesh.creation.box()
    mesh.update_faces([*range(len(mesh.faces) - 1)])
    stl_path = tmp_path / "open.stl"
    stl_path.write_bytes(mesh.export(file_type="stl"))

    inspected = inspect_stl(stl_path)

    assert inspected.geometry.available
    assert not inspected.geometry.summary["quality"]["watertight"]
    assert "开放网格" in " ".join(inspected.geometry.diagnostics)

    data_dir = tmp_path / "data"
    workpiece_id = "wp-open-stl-test"
    source_dir = data_dir / "workpieces" / workpiece_id
    source_dir.mkdir(parents=True)
    (source_dir / "source.stl").write_bytes(stl_path.read_bytes())
    workpiece = WorkpieceRecord(
        workpiece_id=workpiece_id,
        kind=WorkpieceKind.CAD_FILE,
        name="open-part",
        content_sha256="0" * 64,
        dimensions_mm=inspected.dimensions_mm,
        cad_format=CadFormat.STL,
        stored_filename="source.stl",
        geometry=inspected.geometry,
        source_dimensions=inspected.dimensions_mm,
        length_unit=LengthUnit.MILLIMETER,
        unit_confirmed=True,
    )
    plan = DeterministicPlanner().plan(workpiece).plan
    assert plan.heat_source is not None
    plan = plan.model_copy(
        update={
            "heat_source": plan.heat_source.model_copy(
                update={
                    "shape": "line",
                    "placement": "embedded",
                    "center_mm": Point3DMM(x=10.0, y=10.0, z=10.0),
                    "end_mm": Point3DMM(x=10.0, y=10.5, z=10.0),
                    "radius_mm": 0.12,
                    "embedding_depth_mm": 0.2,
                }
            )
        }
    )
    policy = validate_plan(workpiece, plan)

    assert policy.accepted
    assert "开放网格" in " ".join(policy.warnings)
    assert "最近的可用体素" in " ".join(policy.warnings)
    result = VoxelStlSolver().solve(
        study_id="study-open-stl-test",
        workpiece=workpiece,
        plan=plan,
        policy=policy,
        artifact_dir=data_dir / "studies" / "study-open-stl-test" / "artifacts",
    )
    assert result.heat_source_power_w == pytest.approx(25.0)
    assert result.heat_source_mapping is not None
    assert result.heat_source_mapping["shape"] == "line"
    assert result.heat_source_mapping["requested_center_mm"] == pytest.approx([10, 10, 10])
    assert result.heat_source_mapping["resolved_center_mm"] != pytest.approx([10, 10, 10])
    assert result.heat_source_mapping["achieved_embedding_depth_mm"] > 0
    assert result.heat_source_mapping["depth_satisfied"] is False
    assert result.energy_balance_relative_error < 1e-5
    assert "开放网格近似体素重建" in " ".join(result.assumptions)


def test_stl_inspection_ignores_disconnected_degenerate_fragments(tmp_path) -> None:
    box = trimesh.creation.box(extents=[10, 6, 4])
    degenerate = trimesh.Trimesh(
        vertices=[[20, 0, 0], [21, 0, 0], [22, 0, 0]],
        faces=[[0, 1, 2]],
        process=False,
    )
    stl_path = tmp_path / "part-with-degenerate-fragment.stl"
    stl_path.write_bytes(
        trimesh.util.concatenate([box, degenerate]).export(file_type="stl")
    )

    inspected = inspect_stl(stl_path)

    assert inspected.geometry.available
    assert len(inspected.components) == 1
    assert inspected.geometry.summary["topology"]["discarded_degenerate_components"] == 1
    preview = inspected.geometry.summary["preview"]
    assert len(preview["component_ids"]) == len(preview["triangles"])
    assert set(preview["component_ids"]) == {
        inspected.components[0].component_id,
        None,
    }
    assert "忽略 1 个" in " ".join(inspected.geometry.diagnostics)
