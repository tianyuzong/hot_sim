"""Boundary-conforming STL tetrahedra with explicit component ownership."""

import hashlib

import numpy as np

from .models import ArtifactRef, MeshQualityMetrics, MeshRecord
from .stl_geometry import _component_face_groups, _component_record, load_stl_mesh

TET_FACES = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]])


def artifact(path, media_type="application/octet-stream"):
    payload = path.read_bytes()
    return ArtifactRef(
        name=path.name,
        media_type=media_type,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def exterior_faces(cells):
    faces = cells[:, TET_FACES].reshape(-1, 3)
    _, inverse, counts = np.unique(
        np.sort(faces, axis=1), axis=0, return_inverse=True, return_counts=True
    )
    exterior = counts[inverse] == 1
    if np.any(counts > 2):
        raise ValueError("四面体网格包含非流形面")
    return faces[exterior], np.repeat(np.arange(len(cells)), 4)[exterior]


def write_vtk(path, points, cells, component_indices, temperature=None, nodal=None, flux=None):
    import meshio

    data = {"component_index": [component_indices.astype(np.int32)]}
    if temperature is not None:
        data["temperature_k"] = [temperature]
    if flux is not None:
        data["heat_flux_w_m2"] = [flux]
    meshio.write(
        path,
        meshio.Mesh(
            points,
            [("tetra", cells)],
            cell_data=data,
            point_data={"temperature_k": nodal} if nodal is not None else {},
        ),
        file_format="vtk42",
        binary=True,
    )


def generate_tetra_mesh_record(
    *,
    study_id,
    workpiece,
    plan,
    plan_snapshot_sha256,
    geometry_snapshot_sha256,
    pitch_mm,
    source_path,
    artifact_dir,
    progress=None,
):
    import tetgen

    raw_path = source_path.parent / "source.stl"
    if hashlib.sha256(raw_path.read_bytes()).hexdigest() != workpiece.content_sha256:
        raise ValueError("原始 STL 指纹不匹配，请重新导入")
    mesh = load_stl_mesh(raw_path)
    keys = [c.component_id for c in workpiece.components]
    points = []
    cells = []
    owners = []
    offset = 0
    source_volume = 0.0
    component_errors = []
    if progress:
        progress("meshing", 10)
    for group, _ in _component_face_groups(mesh):
        key = _component_record(group, 0).component_id
        if key not in keys or not group.is_watertight or group.volume <= 0:
            raise ValueError("四面体网格需要各组件为法向一致的封闭正体积实体")
        vertices = np.asarray(group.vertices) * workpiece.length_unit.scale_to_mm
        tgen = tetgen.TetGen(vertices, np.asarray(group.faces, dtype=np.int32))
        # Preserve input surface facets and resolve the actual thin solid interior.
        p, t, *_ = tgen.tetrahedralize(switches=f"pq2YQa{pitch_mm**3 / 6:.12g}")
        p = np.asarray(p)
        t = np.asarray(t, dtype=np.int64)
        determinants = np.linalg.det(p[t[:, 1:]] - p[t[:, 0, None]])
        reversed_cells = determinants < 0
        t[reversed_cells] = t[reversed_cells][:, [0, 2, 1, 3]]
        volume = np.abs(determinants) / 6
        if not len(t) or not np.isfinite(volume).all() or volume.min() <= 0:
            raise ValueError("四面体网格包含零体积或无效单元")
        expected = group.volume * workpiece.length_unit.scale_to_mm**3
        error = abs(volume.sum() - expected) / expected * 100
        if error > 1:
            raise ValueError(f"组件四面体体积偏差 {error:.3g}%，请检查原始 STL")
        component_errors.append(
            {"component_id": key, "cells": len(t), "volume_deviation_percent": float(error)}
        )
        source_volume += expected
        points.append(p)
        cells.append(t + offset)
        owners.extend([keys.index(key)] * len(t))
        offset += len(p)
        if offset > 250_000 or len(owners) > 200_000:
            raise ValueError("四面体网格超过 20 万单元或 25 万节点上限，请增大目标尺寸")
        if progress:
            progress("meshing", 10 + 50 * len(points) / len(keys))
    if len(points) != len(keys):
        raise ValueError("四面体组件与原始 STL 数量不匹配")
    points = np.concatenate(points)
    cells = np.concatenate(cells)
    owners = np.asarray(owners, dtype=np.int32)
    corner = points[cells]
    volume = np.linalg.det(corner[:, 1:] - corner[:, 0, None]) / 6
    _faces, face_owners = exterior_faces(cells)
    edges = corner[:, :, None, :] - corner[:, None, :, :]
    max_edge = np.linalg.norm(edges, axis=-1).max(axis=(1, 2))
    tri = corner[:, TET_FACES]
    face_area = (
        np.linalg.norm(np.cross(tri[:, :, 1] - tri[:, :, 0], tri[:, :, 2] - tri[:, :, 0]), axis=-1)
        / 2
    )
    aspect = max_edge * face_area.max(axis=1) / (3 * volume) * np.sqrt(2 / 3)
    max_aspect = float(max(1, aspect.max()))
    normals = np.cross(tri[:, :, 1] - tri[:, :, 0], tri[:, :, 2] - tri[:, :, 0]) / (
        2 * face_area[:, :, None]
    )
    pairs = np.array([[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]])
    angles = np.pi - np.arccos(
        np.clip(np.sum(normals[:, pairs[:, 0]] * normals[:, pairs[:, 1]], axis=-1), -1, 1)
    )
    ideal = np.arccos(1 / 3)
    skewness = np.maximum(
        (angles.max(axis=1) - ideal) / (np.pi - ideal), (ideal - angles.min(axis=1)) / ideal
    )
    face_vectors = tri.mean(axis=2) - corner.mean(axis=1)[:, None]
    orthogonality = np.abs(np.sum(normals * face_vectors, axis=-1)) / np.linalg.norm(
        face_vectors, axis=-1
    )
    # Verify actual nodal connectivity independently within every declared component.
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    rows = np.repeat(cells[:, 0], 3)
    cols = cells[:, 1:].ravel()
    graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(points), len(points))).tocsr()
    count, labels = connected_components(graph, directed=False)
    if count != len(keys) or any(
        len(np.unique(labels[cells[owners == i].ravel()])) != 1 for i in range(len(keys))
    ):
        raise ValueError("四面体节点连通性与组件归属不一致")
    error = float(abs(volume.sum() - source_volume) / source_volume * 100)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    write_vtk(artifact_dir / "mesh.vtk", points, cells, owners)
    np.savez_compressed(
        artifact_dir / "tetra-mesh.npz",
        points=points,
        cells=cells,
        component_indices=owners,
        component_keys=np.asarray(keys),
        volumes_mm3=volume,
        source_sha256=workpiece.content_sha256,
    )
    centers = points[cells].mean(axis=1)
    surface_cells = np.unique(face_owners)
    samples = surface_cells[
        np.linspace(0, len(surface_cells) - 1, min(4000, len(surface_cells)), dtype=int)
    ]
    warnings = []
    if max_aspect > 100:
        warnings.append(
            f"部分薄壁四面体较细长（最大长宽比 {max_aspect:.1f}）；请进行网格收敛比较。"
        )
    return MeshRecord(
        study_id=study_id,
        workpiece_id=workpiece.workpiece_id,
        generator="tetra_mesher_v1",
        plan_snapshot_sha256=plan_snapshot_sha256,
        geometry_snapshot_sha256=geometry_snapshot_sha256,
        pitch_mm=pitch_mm,
        domain_mode="conforming_tetrahedra",
        grid={"points": len(points), "cells": len(cells)},
        active_cells=len(cells),
        surface_cells=len(surface_cells),
        quality_status="warning" if warnings else "passed",
        review_status="pending" if warnings else "not_required",
        quality=MeshQualityMetrics(
            element_type="tetrahedron",
            cell_volume_mm3=float(volume.mean()),
            maximum_aspect_ratio=max_aspect,
            maximum_skewness=float(np.clip(skewness.max(), 0, 1)),
            minimum_orthogonality=float(np.clip(orthogonality.min(), 0, 1)),
            occupied_layers={},
            connected_regions=count,
            expected_components=len(keys),
            volume_deviation_percent=error,
        ),
        boundary_mapping=component_errors,
        warnings=warnings,
        notices=["四面体贴合原始 STL 表面；组件拥有独立节点，不会因网格粗化合并。"],
        cell_samples_mm=centers[samples].tolist(),
        artifacts=[
            artifact(artifact_dir / "mesh.vtk", "application/vnd.vtk"),
            artifact(artifact_dir / "tetra-mesh.npz"),
        ],
    )


def read_tetra_mesh(artifact_dir):
    """Read only geometry whose artifact digest was persisted with the reviewed mesh."""
    import json

    record = MeshRecord.model_validate(json.loads((artifact_dir.parent / "mesh.json").read_text()))
    path = artifact_dir / "tetra-mesh.npz"
    expected = next((a.sha256 for a in record.artifacts if a.name == path.name), None)
    if expected is None or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("四面体组件映射文件校验失败，请重新生成网格")
    with np.load(path, allow_pickle=False) as data:
        arrays = {key: data[key].copy() for key in data.files}
    source = artifact_dir.parent.parent.parent / "workpieces" / record.workpiece_id / "source.stl"
    if hashlib.sha256(source.read_bytes()).hexdigest() != str(arrays["source_sha256"]):
        raise ValueError("四面体原始几何指纹不匹配，请重新生成网格")
    p, c, owners = arrays["points"], arrays["cells"], arrays["component_indices"]
    if (
        p.shape != (record.grid["points"], 3)
        or c.shape != (record.active_cells, 4)
        or owners.shape != (len(c),)
        or not np.isfinite(p).all()
        or c.min() < 0
        or c.max() >= len(p)
        or owners.min() < 0
        or owners.max() >= len(arrays["component_keys"])
    ):
        raise ValueError("四面体网格拓扑或组件映射无效")
    return arrays, record
