"""Immutable STL surface selections and deterministic fixed-temperature mapping."""

from __future__ import annotations

import hashlib
import json

import numpy as np
from pydantic import Field, StrictInt, field_validator

from .models import GeometryRegion, StrictModel
from .stl_geometry import load_stl_mesh
from .visualization import geometry_surface

SURFACE_CONDITION_KINDS = [
    "fixed_temperature", "convection", "heat_flux", "radiation", "thermal_contact",
]


class SurfaceRegionRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    triangle_ids: list[StrictInt] = Field(min_length=1, max_length=200_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        if not value.strip():
            raise ValueError("区域名称不能为空")
        return value.strip()


def create_surface_region(repository, workpiece_id, request):
    with repository.workpiece_lock(workpiece_id):
        workpiece = repository.get_workpiece(workpiece_id)
        if not workpiece.unit_confirmed or workpiece.cad_format is None or workpiece.cad_format.value != "stl":
            raise ValueError("请先导入 STL 并确认尺度")
        surface = geometry_surface(repository, workpiece_id)
        indices = sorted(set(request.triangle_ids))
        if len(indices) != len(request.triangle_ids) or indices[0] < 0 or indices[-1] >= len(surface.triangles):
            raise ValueError("所选三角面为空、重复或不属于该几何版本")
        payload = json.dumps([workpiece.content_sha256, indices], separators=(",", ":")).encode()
        region_id = "region-" + hashlib.sha256(payload).hexdigest()[:12]
        existing = next((r for r in workpiece.regions if r.region_id == region_id), None)
        if existing:
            return existing
        if len(workpiece.regions) >= 300:
            raise ValueError("当前几何区域数量已达上限")
        if any(r.name == request.name for r in workpiece.regions):
            raise ValueError("区域名称已存在，请使用不同名称")
        region = GeometryRegion(
            region_id=region_id, name=request.name, kind="surface_patch", selector="surface." + region_id[7:],
            triangle_ids=indices,
            component_ids=sorted({surface.component_ids[i] for i in indices if surface.component_ids[i]}),
            supported_condition_kinds=SURFACE_CONDITION_KINDS,
        )
        repository.save_workpiece(workpiece.model_copy(update={"regions": [*workpiece.regions, region]}))
        return region


def assert_confirmed_regions(study, workpiece):
    if study.simulation_spec is None:
        if (any(b.region_id for b in study.plan.boundaries) or study.plan.surface_conditions
                or study.plan.contacts):
            raise ValueError("区域边界缺少已确认的区域定义快照")
        return
    snapshots = {r.id: r for r in study.simulation_spec.geometry.regions}
    current = {r.region_id: r for r in workpiece.regions}
    region_ids = [
        *(boundary.region_id for boundary in [*study.plan.boundaries, *study.plan.surface_conditions]),
        *(region_id for contact in study.plan.contacts
          for region_id in (contact.source_region_id, contact.target_region_id)),
    ]
    for region_id in region_ids:
        if region_id is None:
            continue
        old, now = snapshots.get(region_id), current.get(region_id)
        if (old is None or now is None or old.kind != now.kind or old.selector != now.selector
                or old.triangle_ids != now.triangle_ids or old.component_refs != now.component_ids):
            raise ValueError("区域定义与已确认快照不一致，请建立新研究并重新确认")


def _nearest_faces(mesh, positions, progress=None, directions=None):
    from scipy.spatial import cKDTree
    from trimesh.triangles import closest_point

    triangles = mesh.triangles
    centers = triangles.mean(axis=1)
    radius = np.linalg.norm(triangles - centers[:, None], axis=2).max()
    tree = cKDTree(centers)
    distances, _ = tree.query(positions)
    result = np.empty(len(positions), dtype=int)
    # The nearest centroid gives an upper bound on surface distance. Bounding spheres
    # prune only triangles that provably cannot be closer; no nearest-centroid shortcut.
    for index, (position, distance) in enumerate(zip(positions, distances, strict=True)):
        if progress and index % 200 == 0:
            progress("quality", 65 + 10 * index / len(positions))
        candidates = np.asarray(tree.query_ball_point(position, distance + radius + 1e-10), dtype=int)
        candidates.sort()
        closest = closest_point(triangles[candidates], np.broadcast_to(position, (len(candidates), 3)))
        best = np.linalg.norm(closest - position, axis=1)
        closest_indices = np.flatnonzero(np.isclose(best, best.min(), rtol=0, atol=radius * 1e-10))
        if directions is not None:
            alignment = mesh.face_normals[candidates[closest_indices]] @ directions[index]
            closest_indices = closest_indices[np.isclose(alignment, alignment.max(), rtol=0, atol=1e-10)]
        result[index] = candidates[closest_indices[0]]
    return result


def _exposed_face_samples(active, transform):
    coordinates, positions, directions = [], [], []
    padded = np.pad(active, 1)
    for axis in range(3):
        for sign in (-1, 1):
            neighbor = [slice(1, length + 1) for length in active.shape]
            neighbor[axis] = slice(1 + sign, active.shape[axis] + 1 + sign)
            cells = np.argwhere(active & ~padded[tuple(neighbor)])
            direction = transform[:3, axis] * sign
            coordinates.append(cells)
            positions.append(cells @ transform[:3, :3].T + transform[:3, 3] + direction / 2)
            directions.append(np.broadcast_to(direction / np.linalg.norm(direction), (len(cells), 3)))
    return np.concatenate(coordinates), np.concatenate(positions), np.concatenate(directions)


class RegionFaceMap:
    def __init__(self, active, transform, workpiece, source_path, progress=None):
        self.active, self.transform, self.workpiece = active, transform, workpiece
        self.source_path, self.progress = source_path, progress
        self.cells, self.positions, self.directions = _exposed_face_samples(active, transform)
        self.nearest = None
        self.components = None

    def select(self, region_id):
        region = next((r for r in self.workpiece.regions if r.region_id == region_id), None)
        if region is None:
            raise ValueError("热边界引用了不存在的区域")
        if region.kind == "bounding_plane":
            axis = "xyz".index(region.selector[5])
            sign = 1 if region.selector.endswith("max") else -1
            layer = (self.cells[:, axis].max() if sign > 0 else self.cells[:, axis].min())
            return (self.cells[:, axis] == layer) & (self.directions[:, axis] * sign > 0.99)
        self._ensure_surface_mapping()
        if region.kind == "surface_patch":
            if not region.triangle_ids or min(region.triangle_ids) < 0 or max(region.triangle_ids) >= len(self.original.faces):
                raise ValueError("所选区域缺少有效三角面")
            return np.isin(self.nearest, region.triangle_ids)
        if region.kind == "component_surface":
            return np.isin(self.face_component_ids(), region.component_ids)
        raise ValueError("该区域类型尚不支持体素热边界映射")

    def _ensure_surface_mapping(self):
        if self.nearest is None:
            original_path = self.source_path.parent / "source.stl"
            if hashlib.sha256(original_path.read_bytes()).hexdigest() != self.workpiece.content_sha256:
                raise ValueError("区域引用的原始 STL 指纹不匹配，请重新导入")
            self.original = load_stl_mesh(original_path)
            mesh = self.original.copy()
            mesh.apply_scale(self.workpiece.length_unit.scale_to_mm)
            self.nearest = _nearest_faces(mesh, self.positions, self.progress, self.directions)

    def face_component_ids(self):
        self._ensure_surface_mapping()
        if self.components is None:
            from .stl_geometry import (
                _component_face_groups,
                _component_record,
                _is_component_usable,
            )
            self.components = np.full(len(self.original.faces), None, dtype=object)
            for group, faces in _component_face_groups(self.original):
                if _is_component_usable(group):
                    self.components[faces] = _component_record(group, 0).component_id
        return self.components[self.nearest]


class ComponentMappingError(ValueError):
    """Valid geometry cannot be uniquely associated with this voxel resolution."""


def component_cell_masks(active, workpiece, face_map):
    """Bind connected voxel bodies to stable STL IDs using their actual surfaces."""
    from scipy.ndimage import label

    labels, count = label(active)
    components = {component.component_id for component in workpiece.components}
    if count != max(1, len(components)):
        raise ComponentMappingError("体素连通区域与 STL 组件数量不同，材料映射已停止，请调整网格尺寸")
    if len(components) <= 1:
        return {component_id: active for component_id in components}
    face_owners = face_map.face_component_ids()
    face_labels = labels[tuple(face_map.cells.T)]
    masks = {}
    for label_id in range(1, count + 1):
        owners = set(face_owners[face_labels == label_id])
        if len(owners) != 1 or not owners <= components:
            raise ComponentMappingError("体素区域无法唯一映射到原始组件，材料映射已停止，请加密网格")
        owner = owners.pop()
        if owner in masks:
            raise ComponentMappingError("多个体素区域映射到同一组件，材料映射已停止，请调整网格")
        masks[owner] = labels == label_id
    return masks


def map_fixed_boundaries(*, active, transform, workpiece, plan, source_path, progress=None, face_map=None):
    from scipy.ndimage import binary_erosion

    fixed = np.zeros(active.shape, dtype=bool)
    values = np.zeros(active.shape, dtype=float)
    region_by_id = {r.region_id: r for r in workpiece.regions}
    surface_coordinates = np.argwhere(active & ~binary_erosion(active, border_value=0))
    mappings = []
    for boundary in plan.boundaries:
        region = region_by_id.get(boundary.region_id) if boundary.region_id else None
        if boundary.region_id and region is None:
            raise ValueError("定温边界引用了不存在的区域")
        selector = region.selector if region is not None else boundary.selector.value
        if region is None or region.kind == "bounding_plane":
            axis = "xyz".index(selector[5])
            coordinates = np.argwhere(active)
            layer = coordinates[:, axis].min() if selector.endswith("min") else coordinates[:, axis].max()
            selected = surface_coordinates[surface_coordinates[:, axis] == layer]
        else:
            if face_map is None:
                face_map = RegionFaceMap(active, transform, workpiece, source_path, progress)
            selected = np.unique(face_map.cells[face_map.select(region.region_id)], axis=0)
        if not len(selected):
            raise ValueError("区域未映射到任何表面单元，请加密网格或扩大选面区域")
        indices = tuple(selected.T)
        conflict = fixed[indices] & ~np.isclose(values[indices], boundary.temperature_k, rtol=0, atol=1e-9)
        if conflict.any():
            raise ValueError("多个定温区域映射到相同单元且温度冲突，请调整选面或网格")
        fixed[indices] = True
        values[indices] = boundary.temperature_k
        mappings.append({"region_id": boundary.region_id, "selector": selector,
                         "mapped_cells": len(selected), "temperature_k": boundary.temperature_k})
    return fixed, values, mappings
