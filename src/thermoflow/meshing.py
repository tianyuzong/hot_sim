"""Deterministic voxel meshing and persisted pre-solve mesh previews."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, TextIO

from .execution import ProgressCallback
from .models import (
    ArtifactRef,
    MeshQualityMetrics,
    MeshRecord,
    SimulationPlan,
    WorkpieceKind,
    WorkpieceRecord,
)
from .stl_geometry import load_stl_mesh

MAX_MESH_PREVIEW_CELLS = 4_000


def load_solver_mesh(workpiece: WorkpieceRecord, source_path: Path) -> Any:
    if workpiece.kind is WorkpieceKind.BOX:
        import numpy as np
        import trimesh

        dimensions = np.asarray(workpiece.dimensions_mm.as_tuple(), dtype=float)
        mesh = trimesh.creation.box(extents=dimensions)
        mesh.apply_translation(dimensions / 2)
        return mesh
    return load_stl_mesh(source_path)


def reconstruct_voxel_domain(mesh: Any, pitch_mm: float) -> tuple[Any, Any, str]:
    import numpy as np
    from scipy.ndimage import binary_fill_holes

    voxel_grid = mesh.voxelized(pitch_mm)
    surface = np.asarray(voxel_grid.matrix, dtype=bool)
    if mesh.is_watertight:
        return voxel_grid, np.asarray(voxel_grid.fill().matrix, dtype=bool), "enclosed_volume"

    candidates = [binary_fill_holes(surface)]
    for axis in range(3):
        filled = surface.copy()
        for index in range(surface.shape[axis]):
            slices = [slice(None)] * 3
            slices[axis] = index
            key = tuple(slices)
            filled[key] = binary_fill_holes(surface[key])
        candidates.append(filled)
    directional_votes = sum(candidate.astype(np.uint8) for candidate in candidates[1:])
    reconstructed = surface | candidates[0] | (directional_votes >= 2)
    return voxel_grid, reconstructed, "reconstructed_open_surface"


def generate_voxel_mesh_record(
    *,
    study_id: str,
    workpiece: WorkpieceRecord,
    plan: SimulationPlan,
    plan_snapshot_sha256: str,
    geometry_snapshot_sha256: str,
    pitch_mm: float,
    source_path: Path,
    artifact_dir: Path,
    progress: ProgressCallback | None = None,
) -> MeshRecord:
    import numpy as np
    from scipy.ndimage import binary_erosion, label

    if progress:
        progress("geometry", 5)
    mesh = load_solver_mesh(workpiece, source_path)
    if progress:
        progress("meshing", None)
    voxel_grid, active, domain_mode = reconstruct_voxel_domain(mesh, pitch_mm)
    coordinates = np.argwhere(active)
    if progress:
        progress("quality", 65)
    if not len(coordinates):
        raise ValueError("工件体素化后没有活动单元")

    surface = active & ~binary_erosion(active, border_value=0)
    surface_coordinates = np.argwhere(surface)
    if not len(surface_coordinates):
        raise ValueError("生成的体素网格没有可预览的表面单元")

    occupied_layers = {
        axis: int(coordinates[:, index].max() - coordinates[:, index].min() + 1)
        for index, axis in enumerate(("x", "y", "z"))
    }
    boundary_axis = plan.boundaries[0].selector.axis if plan.boundaries else "x"
    warnings: list[str] = []
    notices: list[str] = []
    blocked = bool(plan.boundaries) and occupied_layers[boundary_axis] < 2 and not any(b.region_id for b in plan.boundaries)
    if blocked:
        warnings.append(
            f"{boundary_axis.upper()} 方向只有一个活动体素层，无法施加相对面温度边界。"
        )
    if any(b.region_id for b in plan.boundaries) or plan.surface_conditions:
        warnings.append("区域热边界通过外露体素面映射到单元中心，边缘存在体素近似；请检查映射数量并进行网格收敛比较。")
        if not mesh.is_winding_consistent or mesh.volume < 0:
            warnings.append("原始面法向不一致或指向内部，等距边缘的区域映射可能不可靠；建议修复法向后重新导入。")
    if min(occupied_layers.values()) < 4:
        warnings.append("至少一个方向少于 4 个活动体素层，薄壁或局部特征分辨率较低。")
    from .regions import RegionFaceMap, component_cell_masks, map_fixed_boundaries
    from .thermal_boundaries import ThermalSurfaceTerms
    from .thermal_contacts import build_contact_links
    boundary_mapping = []
    try:
        face_map = RegionFaceMap(active, np.asarray(voxel_grid.transform), workpiece, source_path, progress)
        component_cell_masks(active, workpiece, face_map)
        fixed, _, boundary_mapping = map_fixed_boundaries(
            active=active, transform=np.asarray(voxel_grid.transform), workpiece=workpiece,
            plan=plan, source_path=source_path, progress=progress, face_map=face_map,
        )
        contact_links, contact_mapping, contact_faces = build_contact_links(
            active=active,
            face_map=face_map,
            plan=plan,
            workpiece=workpiece,
            pitch_mm=pitch_mm,
        )
        surface_terms = ThermalSurfaceTerms(
            face_map, fixed, plan, pitch_mm, excluded_faces=contact_faces,
        )
        surface_terms.validate_anchors(
            active, fixed, plan.analysis_type == "transient_conduction", contact_links,
        )
        boundary_mapping.extend(surface_terms.mapping)
        boundary_mapping.extend(contact_mapping)
        if fixed[active].all():
            blocked = True
            warnings.append("定温区域覆盖了所有单元，局部体积热源没有可用自由单元。")
        if any(m.get("region_id") and m["mapped_cells"] < 4 for m in boundary_mapping):
            warnings.append("至少一个定温区域少于 4 个映射单元，建议加密后检查边界分辨率。")
    except ValueError as exc:
        blocked = True
        warnings.append(str(exc))
    if not mesh.is_watertight:
        warnings.append("原始 STL 存在开放边界，本网格由开放表面近似重建。")
    _, connected_regions = label(active)
    expected_components = max(1, len(workpiece.components))
    if connected_regions != expected_components:
        blocked = True
        warnings.append(
            f"网格包含 {connected_regions} 个连通区域，但 STL 识别出 {expected_components} 个组件；"
            "组件材料无法可靠绑定，请调整网格尺寸。"
        )

    voxel_volume_mm3 = len(coordinates) * pitch_mm**3
    volume_deviation_percent = None
    if mesh.is_watertight and abs(float(mesh.volume)) > 0:
        volume_deviation_percent = (
            abs(voxel_volume_mm3 - abs(float(mesh.volume))) / abs(float(mesh.volume)) * 100.0
        )
        if volume_deviation_percent > 25.0:
            warnings.append(
                f"体素网格体积与 STL 体积相差 {volume_deviation_percent:.1f}%，建议减小单元尺寸。"
            )

    sample_coordinates = _sample_rows(surface_coordinates, MAX_MESH_PREVIEW_CELLS)
    if len(surface_coordinates) > len(sample_coordinates):
        notices.append(
            f"画布从 {len(surface_coordinates)} 个表面单元中抽样显示 {len(sample_coordinates)} 个；"
            "求解仍使用完整网格。"
        )
    transform = np.asarray(voxel_grid.transform, dtype=float)
    centers = sample_coordinates @ transform[:3, :3].T + transform[:3, 3]

    artifact_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress("fields", 85)
    vtk_path = artifact_dir / "mesh.vtk"
    _write_mesh_vtk(
        vtk_path,
        active=active,
        surface=surface,
        pitch_mm=pitch_mm,
        origin_mm=transform[:3, 3] - pitch_mm / 2.0,
    )
    vtk_bytes = vtk_path.read_bytes()
    shape = tuple(int(value) for value in active.shape)
    quality_status = "blocked" if blocked else "warning" if warnings else "passed"
    return MeshRecord(
        boundary_mapping=boundary_mapping,
        study_id=study_id,
        workpiece_id=workpiece.workpiece_id,
        plan_snapshot_sha256=plan_snapshot_sha256,
        geometry_snapshot_sha256=geometry_snapshot_sha256,
        pitch_mm=pitch_mm,
        domain_mode=domain_mode,
        grid={
            "x_intervals": shape[0],
            "y_intervals": shape[1],
            "z_intervals": shape[2],
            "points": int(math.prod(value + 1 for value in shape)),
            "cells": int(len(coordinates)),
        },
        active_cells=int(len(coordinates)),
        surface_cells=int(len(surface_coordinates)),
        quality_status=quality_status,
        review_status="pending" if quality_status == "warning" else "not_required",
        quality=MeshQualityMetrics(
            cell_volume_mm3=pitch_mm**3,
            maximum_aspect_ratio=1.0,
            maximum_skewness=0.0,
            minimum_orthogonality=1.0,
            occupied_layers=occupied_layers,
            connected_regions=int(connected_regions),
            expected_components=expected_components,
            volume_deviation_percent=volume_deviation_percent,
        ),
        warnings=warnings,
        notices=notices,
        cell_samples_mm=[tuple(float(value) for value in center) for center in centers],
        artifacts=[
            ArtifactRef(
                name=vtk_path.name,
                media_type="application/vnd.vtk",
                sha256=hashlib.sha256(vtk_bytes).hexdigest(),
                size_bytes=len(vtk_bytes),
            )
        ],
    )


def _sample_rows(rows: Any, limit: int) -> Any:
    import numpy as np

    if len(rows) <= limit:
        return rows
    positions = np.linspace(0, len(rows) - 1, limit, dtype=int)
    return rows[positions]


def _write_mesh_vtk(
    path: Path,
    *,
    active: Any,
    surface: Any,
    pitch_mm: float,
    origin_mm: Any,
) -> None:
    import numpy as np

    nx, ny, nz = (int(value) for value in active.shape)
    point_count = (nx + 1) * (ny + 1) * (nz + 1)
    coordinates = np.argwhere(active)
    with path.open("w", encoding="ascii", newline="\n") as stream:
        stream.write("# vtk DataFile Version 3.0\n")
        stream.write("ThermoFlow pre-solve voxel mesh\n")
        stream.write("ASCII\n")
        stream.write("DATASET UNSTRUCTURED_GRID\n")
        stream.write(f"POINTS {point_count} double\n")
        for k in range(nz + 1):
            for j in range(ny + 1):
                for i in range(nx + 1):
                    point = origin_mm + np.array((i, j, k), dtype=float) * pitch_mm
                    stream.write(f"{point[0]:.12g} {point[1]:.12g} {point[2]:.12g}\n")

        stream.write(f"CELLS {len(coordinates)} {len(coordinates) * 9}\n")
        for i, j, k in coordinates:
            nodes = (
                _point_id(i, j, k, nx, ny),
                _point_id(i + 1, j, k, nx, ny),
                _point_id(i, j + 1, k, nx, ny),
                _point_id(i + 1, j + 1, k, nx, ny),
                _point_id(i, j, k + 1, nx, ny),
                _point_id(i + 1, j, k + 1, nx, ny),
                _point_id(i, j + 1, k + 1, nx, ny),
                _point_id(i + 1, j + 1, k + 1, nx, ny),
            )
            stream.write("8 " + " ".join(str(node) for node in nodes) + "\n")
        stream.write(f"CELL_TYPES {len(coordinates)}\n")
        _write_repeated(stream, "11", len(coordinates))
        stream.write(f"CELL_DATA {len(coordinates)}\n")
        stream.write("SCALARS surface_cell int 1\n")
        stream.write("LOOKUP_TABLE default\n")
        for coordinate in coordinates:
            stream.write("1\n" if surface[tuple(coordinate)] else "0\n")


def _point_id(i: int, j: int, k: int, nx: int, ny: int) -> int:
    return (int(k) * (ny + 1) + int(j)) * (nx + 1) + int(i)


def _write_repeated(stream: TextIO, value: str, count: int, per_line: int = 20) -> None:
    stream.writelines(
        " ".join([value] * min(per_line, count - offset)) + "\n"
        for offset in range(0, count, per_line)
    )
