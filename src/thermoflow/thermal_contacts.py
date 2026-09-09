"""Deterministic finite-volume links for explicitly confirmed component contacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ContactLink:
    source: tuple[int, int, int]
    target: tuple[int, int, int]
    conductance_w_k: float
    direction: tuple[float, float, float]


def build_contact_links(*, active: Any, face_map: Any, plan: Any, workpiece: Any,
                        pitch_mm: float) -> tuple[list[ContactLink], list[dict[str, Any]], Any]:
    """Pair confirmed component surface faces without silently inventing a contact.

    The voxel solver has no conformal interface mesh. A contact is therefore accepted
    only when both saved surface regions produce opposing, nearby exterior faces. The
    resistance is distributed over the paired face cells, making each pair a symmetric
    matrix edge and preserving energy exactly.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    if not plan.contacts:
        return [], [], np.zeros(len(face_map.cells), dtype=bool)
    component_ids = {component.component_id for component in workpiece.components}
    region_by_id = {region.region_id: region for region in workpiece.regions}
    links: list[ContactLink] = []
    mappings: list[dict[str, Any]] = []
    seen_pairs: set[tuple[tuple[int, int, int], tuple[int, int, int]]] = set()
    claimed_faces: set[int] = set()
    face_area_m2 = (float(pitch_mm) / 1000.0) ** 2
    contact_faces = np.zeros(len(face_map.cells), dtype=bool)

    for contact in plan.contacts:
        if contact.source_component_id not in component_ids or contact.target_component_id not in component_ids:
            raise ValueError("热接触引用了不存在的组件")
        if contact.source_component_id == contact.target_component_id:
            raise ValueError("热接触的两个组件必须不同")
        source_region = region_by_id.get(contact.source_region_id)
        target_region = region_by_id.get(contact.target_region_id)
        if source_region is None or target_region is None:
            raise ValueError("热接触引用了不存在的表面区域")
        if contact.source_component_id not in source_region.component_ids:
            raise ValueError("热接触源区域不属于源组件")
        if contact.target_component_id not in target_region.component_ids:
            raise ValueError("热接触目标区域不属于目标组件")

        source_mask = face_map.select(contact.source_region_id)
        target_mask = face_map.select(contact.target_region_id)
        source_indices = np.flatnonzero(source_mask)
        target_indices = np.flatnonzero(target_mask)
        if not len(source_indices) or not len(target_indices):
            raise ValueError("热接触区域没有映射到外露体素面，请加密网格或扩大区域")
        source_positions = face_map.positions[source_indices]
        target_positions = face_map.positions[target_indices]
        target_tree = cKDTree(target_positions)
        distances, target_nearest = target_tree.query(source_positions)
        source_tree = cKDTree(source_positions)
        _, source_nearest = source_tree.query(target_positions)
        source_directions = face_map.directions[source_indices]
        target_directions = face_map.directions[target_indices[target_nearest]]
        opposing = np.einsum("ij,ij->i", source_directions, target_directions) < -0.5
        vectors = target_positions[target_nearest] - source_positions
        unit_vectors = vectors / np.maximum(distances[:, None], 1e-12)
        facing = (
            np.einsum("ij,ij->i", source_directions, unit_vectors) > 0.5
        ) & (
            np.einsum("ij,ij->i", target_directions, -unit_vectors) > 0.5
        )
        mutual = source_nearest[target_nearest] == np.arange(len(source_positions))
        allowed_gap = contact.max_gap_mm if contact.max_gap_mm is not None else pitch_mm
        nearby = distances <= float(allowed_gap) + 1e-9
        candidates = np.flatnonzero(opposing & facing & mutual & nearby & (distances > 1e-12))
        if not len(candidates):
            raise ValueError("热接触两侧没有相对且足够接近的面，未建立隐式远距离热阻")

        pair_cells: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = []
        accepted_source_faces: list[int] = []
        accepted_target_faces: list[int] = []
        accepted_distances: list[float] = []
        for candidate in candidates:
            source_face = int(source_indices[candidate])
            target_face = int(target_indices[target_nearest[candidate]])
            if source_face in claimed_faces or target_face in claimed_faces:
                continue
            source_cell = tuple(int(value) for value in face_map.cells[source_face])
            target_cell = tuple(int(value) for value in face_map.cells[target_face])
            if source_cell == target_cell or not active[source_cell] or not active[target_cell]:
                continue
            ordered = (source_cell, target_cell)
            if ordered[1] < ordered[0]:
                ordered = (ordered[1], ordered[0])
            if ordered in seen_pairs:
                continue
            seen_pairs.add(ordered)
            claimed_faces.update((source_face, target_face))
            accepted_source_faces.append(source_face)
            accepted_target_faces.append(target_face)
            accepted_distances.append(float(distances[candidate]))
            pair_cells.append((source_cell, target_cell))
        if not pair_cells:
            raise ValueError("热接触面映射到了相同或无效体素单元")

        mapped_area = len(pair_cells) * face_area_m2
        if contact.contact_area_m2 is not None and contact.contact_area_m2 > mapped_area * (1 + 1e-9):
            raise ValueError("确认的有效接触面积大于当前网格映射面积，请加密网格或修正面积")
        area = contact.contact_area_m2 or mapped_area
        conductance = float(area) / float(contact.contact_resistance_m2_k_w)
        pair_conductance = conductance / len(pair_cells)
        for (source_cell, target_cell), source_face in zip(pair_cells, accepted_source_faces, strict=True):
            # Contact heat crosses the paired surface normal, not a spurious lateral
            # direction introduced by nearest-neighbor offsets of voxel centers.
            vector = face_map.directions[source_face]
            links.append(ContactLink(
                source=source_cell,
                target=target_cell,
                conductance_w_k=pair_conductance,
                direction=tuple(float(value) for value in vector),
            ))
        contact_faces[accepted_source_faces] = True
        contact_faces[accepted_target_faces] = True
        mappings.append({
            "kind": "thermal_contact",
            "source_component_id": contact.source_component_id,
            "target_component_id": contact.target_component_id,
            "source_region_id": contact.source_region_id,
            "target_region_id": contact.target_region_id,
            "mapped_pairs": len(pair_cells),
            "mapped_area": {"value": float(mapped_area), "unit": "m^2"},
            "effective_contact_area": {"value": float(area), "unit": "m^2"},
            "contact_resistance": {"value": contact.contact_resistance_m2_k_w, "unit": "m^2*K/W"},
            "conductance": {"value": conductance, "unit": "W/K"},
            "maximum_face_gap": {"value": float(max(accepted_distances)), "unit": "mm"},
        })
    return links, mappings, contact_faces


def contact_transfer_power(links: list[ContactLink], temperature: Any) -> float:
    """Return absolute one-way contact transfer for audit, not external balance."""
    absolute = 0.0
    for link in links:
        flow = link.conductance_w_k * (float(temperature[link.source]) - float(temperature[link.target]))
        absolute += abs(flow)
    return float(absolute)


def contact_fixed_boundary_power(links: list[ContactLink], fixed: Any, temperature: Any) -> float:
    """Heat supplied by fixed-temperature cells through contact links."""
    total = 0.0
    for link in links:
        source_fixed = bool(fixed[link.source])
        target_fixed = bool(fixed[link.target])
        if source_fixed and not target_fixed:
            total += link.conductance_w_k * (temperature[link.source] - temperature[link.target])
        elif target_fixed and not source_fixed:
            total += link.conductance_w_k * (temperature[link.target] - temperature[link.source])
    return float(total)


def add_contact_heat_flux(links: list[ContactLink], temperature: Any, vectors: Any,
                          pitch_mm: float, *, active: Any) -> Any:
    """Average internal and contact face fluxes into one cell vector."""
    import numpy as np

    if not links:
        return vectors.copy()
    weights = np.zeros(vectors.shape)
    for axis in range(3):
        left, right = [slice(None)] * 3, [slice(None)] * 3
        left[axis], right[axis] = slice(0, -1), slice(1, None)
        left, right = tuple(left), tuple(right)
        connected = active[left] & active[right]
        weights[..., axis][left] += connected
        weights[..., axis][right] += connected
    result = vectors * weights
    area = (float(pitch_mm) / 1000.0) ** 2
    for link in links:
        flow = link.conductance_w_k * (float(temperature[link.source]) - float(temperature[link.target]))
        vector = np.asarray(link.direction, dtype=float) * (flow / area)
        result[link.source] += vector
        result[link.target] += vector
        normal_weight = np.abs(np.asarray(link.direction))
        weights[link.source] += normal_weight
        weights[link.target] += normal_weight
    return np.divide(result, weights, out=np.zeros_like(result), where=weights > 0)
