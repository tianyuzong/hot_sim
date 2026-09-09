"""STL validation and geometry metadata independent of the CadFlow runtime."""

from __future__ import annotations

import math
import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import DimensionsMM, GeometryComponent, GeometryInspection

MAX_PREVIEW_TRIANGLES = 1_200


@dataclass(frozen=True, slots=True)
class StlInspection:
    geometry: GeometryInspection
    dimensions_mm: DimensionsMM | None
    components: list[GeometryComponent]


def inspect_stl(path: Path) -> StlInspection:
    """Inspect any usable STL and record quality issues for voxel reconstruction."""
    try:
        mesh = load_stl_mesh(path)
        bounds = mesh.bounds
        extents = mesh.extents
        if any(not math.isfinite(float(value)) or float(value) <= 0 for value in extents):
            raise ValueError("STL 包围盒必须在 X、Y、Z 三个方向都有正尺寸")
        dimensions = DimensionsMM(x=float(extents[0]), y=float(extents[1]), z=float(extents[2]))
        component_groups = _component_face_groups(mesh)
        valid_component_groups = [
            item for item in component_groups if _is_component_usable(item[0])
        ]
        invalid_component_count = len(component_groups) - len(valid_component_groups)
        if not valid_component_groups:
            raise ValueError("STL 不包含具有有效面积的壳体")
        valid_component_groups.sort(
            key=lambda item: tuple(float(value) for value in item[0].centroid)
        )
        components = [
            _component_record(item[0], index)
            for index, item in enumerate(valid_component_groups)
        ]
        face_component_ids: list[str | None] = [None] * len(mesh.faces)
        for component, (_, face_indices) in zip(
            components,
            valid_component_groups,
            strict=True,
        ):
            for face_index in face_indices:
                face_component_ids[int(face_index)] = component.component_id
        bodies = len(components)
        diagnostics: list[str] = []
        if not mesh.is_watertight:
            diagnostics.append("检测到开放网格；求解时将从三角面重建近似体素域。")
        if not mesh.is_winding_consistent:
            diagnostics.append("三角面朝向不一致；体素重建不会依赖原始法向方向。")
        if bodies != 1:
            diagnostics.append(f"STL 包含 {bodies} 个不连通壳体；将统一纳入体素求解域。")
        if invalid_component_count:
            diagnostics.append(
                f"检测并忽略 {invalid_component_count} 个仅含退化三角形的断开片段；"
                "这些片段不参与组件分配和求解。"
            )
        volume = abs(float(mesh.volume))
        if not math.isfinite(volume) or volume <= 0:
            diagnostics.append("原始网格没有可靠正体积；结果体积以重建体素域为准。")

        summary = {
            "kind": "triangle_mesh",
            "stl_encoding": _stl_encoding(path),
            "coordinate_system": {
                "length_unit": None,
                "up_axis": "+Z",
                "unit_basis": "STL 文件不包含长度单位，等待用户确认",
            },
            "bbox": [*bounds[0].astype(float).tolist(), *bounds[1].astype(float).tolist()],
            "volume": volume if mesh.is_watertight else None,
            "area": float(mesh.area),
            "center_of_mass_source": (mesh.center_mass if mesh.is_watertight else mesh.centroid)
            .astype(float)
            .tolist(),
            "topology": {
                "bodies": bodies,
                "discarded_degenerate_components": invalid_component_count,
                "vertices": len(mesh.vertices),
                "triangles": len(mesh.faces),
            },
            "quality": {
                "watertight": bool(mesh.is_watertight),
                "winding_consistent": bool(mesh.is_winding_consistent),
                "non_manifold_edges": _non_manifold_edge_count(mesh),
                "degenerate_triangles": _degenerate_triangle_count(mesh),
                "simulation_domain": (
                    "enclosed_volume" if mesh.is_watertight else "reconstructed_voxel_domain"
                ),
            },
            "preview": _preview_mesh(mesh, face_component_ids),
            "components": [component.model_dump(mode="json") for component in components],
        }
        return StlInspection(
            geometry=GeometryInspection(
                available=True,
                engine="trimesh-stl",
                engine_version=_trimesh_version(),
                summary=summary,
                faces=_bounding_faces(bounds, extents),
                diagnostics=diagnostics,
            ),
            dimensions_mm=dimensions,
            components=components,
        )
    except Exception as exc:  # noqa: BLE001 - malformed files stay inside the import boundary
        return StlInspection(
            geometry=GeometryInspection(
                available=False,
                engine="trimesh-stl",
                engine_version=_trimesh_version(),
                diagnostics=[f"STL 几何检查失败：{type(exc).__name__}: {exc}"],
            ),
            dimensions_mm=None,
            components=[],
        )


def load_stl_mesh(path: Path) -> Any:
    import trimesh

    loaded = trimesh.load_mesh(path, file_type="stl", process=True)
    if not isinstance(loaded, trimesh.Trimesh):
        raise TypeError("STL 未解析为单一三角网格")
    if loaded.is_empty or len(loaded.vertices) < 3 or len(loaded.faces) < 1:
        raise ValueError("STL 不包含可用的三角面")
    if not _all_finite(loaded.vertices):
        raise ValueError("STL 包含非有限坐标")
    return loaded


def remove_stl_component(path: Path, component_index: int) -> bytes:
    """Return an STL with one disconnected shell removed."""
    import numpy as np

    mesh = load_stl_mesh(path)
    groups = [item for item in _component_face_groups(mesh) if _is_component_usable(item[0])]
    groups.sort(key=lambda item: tuple(float(value) for value in item[0].centroid))
    if component_index < 0 or component_index >= len(groups):
        raise ValueError("工件中不存在该组件")
    if len(groups) <= 1:
        raise ValueError("至少保留一个组件")
    _, face_indices = groups[component_index]
    keep = np.ones(len(mesh.faces), dtype=bool)
    keep[np.asarray(face_indices, dtype=int)] = False
    mesh.update_faces(keep)
    mesh.remove_unreferenced_vertices()
    if mesh.is_empty or len(mesh.faces) == 0:
        raise ValueError("删除组件后 STL 不再包含有效三角面")
    exported = mesh.export(file_type="stl")
    return exported.encode("utf-8") if isinstance(exported, str) else bytes(exported)


def _all_finite(values: Any) -> bool:
    import numpy as np

    return bool(np.isfinite(values).all())


def _trimesh_version() -> str | None:
    try:
        import trimesh

        return str(trimesh.__version__)
    except Exception:  # noqa: BLE001 - diagnostics should survive dependency import failures
        return None


def _bounding_faces(bounds: Any, extents: Any) -> list[dict[str, Any]]:
    axes = ("x", "y", "z")
    faces: list[dict[str, Any]] = []
    for axis_index, axis in enumerate(axes):
        other = [index for index in range(3) if index != axis_index]
        bounding_area = float(extents[other[0]] * extents[other[1]])
        for side, normal_sign, bound_index in (("min", -1.0, 0), ("max", 1.0, 1)):
            normal = [0.0, 0.0, 0.0]
            normal[axis_index] = normal_sign
            faces.append(
                {
                    "selector": f"face.{axis}{side}",
                    "selection": "global_bounding_plane_voxel_layer",
                    "coordinate_source": float(bounds[bound_index, axis_index]),
                    "bounding_area_source2": bounding_area,
                    "normal": normal,
                }
            )
    return faces


def _preview_mesh(
    mesh: Any,
    face_component_ids: list[str | None],
) -> dict[str, Any]:
    import numpy as np

    faces = mesh.faces
    if len(faces) > MAX_PREVIEW_TRIANGLES:
        positions = _preview_face_positions(face_component_ids, MAX_PREVIEW_TRIANGLES)
        faces = faces[positions]
        preview_component_ids = [face_component_ids[int(index)] for index in positions]
    else:
        preview_component_ids = face_component_ids
    unique_vertices, inverse = np.unique(faces.reshape(-1), return_inverse=True)
    vertices = np.round(mesh.vertices[unique_vertices], decimals=6)
    return {
        "vertices_source": vertices.astype(float).tolist(),
        "triangles": inverse.reshape((-1, 3)).astype(int).tolist(),
        "component_ids": preview_component_ids,
        "sampled": len(mesh.faces) > len(faces),
    }


def _preview_face_positions(
    component_ids: list[str | None],
    limit: int,
) -> Any:
    import numpy as np

    grouped: dict[str | None, list[int]] = {}
    for index, component_id in enumerate(component_ids):
        grouped.setdefault(component_id, []).append(index)

    selected: set[int] = set()
    minimum_per_component = max(1, min(12, limit // max(len(grouped), 1)))
    for indices in grouped.values():
        count = min(len(indices), minimum_per_component)
        for position in np.linspace(0, len(indices) - 1, count, dtype=int):
            selected.add(indices[int(position)])

    remaining = limit - len(selected)
    if remaining > 0:
        candidates = [index for index in range(len(component_ids)) if index not in selected]
        count = min(remaining, len(candidates))
        for position in np.linspace(0, len(candidates) - 1, count, dtype=int):
            selected.add(candidates[int(position)])
    return np.asarray(sorted(selected), dtype=int)


def _component_face_groups(mesh: Any) -> list[tuple[Any, Any]]:
    import numpy as np
    import trimesh

    face_groups = trimesh.graph.connected_components(
        mesh.face_adjacency,
        nodes=np.arange(len(mesh.faces)),
        min_len=1,
    )
    return [
        (mesh.submesh([face_indices], append=True, repair=False), face_indices)
        for face_indices in face_groups
    ]


def _component_record(mesh: Any, index: int) -> GeometryComponent:
    import numpy as np

    fingerprint = hashlib.sha256()
    fingerprint.update(np.round(mesh.vertices, decimals=9).astype("<f8").tobytes())
    fingerprint.update(np.asarray(mesh.faces, dtype="<i8").tobytes())
    component_id = f"component-{fingerprint.hexdigest()[:12]}"
    return GeometryComponent(
        component_id=component_id,
        name=f"组件 {index + 1}",
        triangle_count=len(mesh.faces),
        vertex_count=len(mesh.vertices),
        bbox_source=[*mesh.bounds[0].astype(float).tolist(), *mesh.bounds[1].astype(float).tolist()],
        area_source2=float(mesh.area),
        watertight=bool(mesh.is_watertight),
    )


def _is_component_usable(mesh: Any) -> bool:
    area = float(mesh.area)
    return (
        len(mesh.vertices) >= 3
        and len(mesh.faces) >= 1
        and math.isfinite(area)
        and area > 0
    )


def _stl_encoding(path: Path) -> str:
    payload = path.read_bytes()
    if len(payload) >= 84:
        triangle_count = struct.unpack("<I", payload[80:84])[0]
        if 84 + triangle_count * 50 == len(payload):
            return "binary"
    return "ascii"


def _non_manifold_edge_count(mesh: Any) -> int:
    import numpy as np

    if not len(mesh.edges_unique_inverse):
        return 0
    counts = np.bincount(mesh.edges_unique_inverse)
    return int(np.count_nonzero(counts > 2))


def _degenerate_triangle_count(mesh: Any) -> int:
    import numpy as np

    areas = np.asarray(mesh.area_faces, dtype=float)
    scale = max(float(np.max(mesh.extents)), 1.0)
    return int(np.count_nonzero(areas <= scale * scale * 1e-14))
