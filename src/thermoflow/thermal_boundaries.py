"""Face-resolved heat transfer terms shared by mesh review and finite-volume assembly."""

from __future__ import annotations

import numpy as np

STEFAN_BOLTZMANN = 5.670374419e-8


def _fixed_boundary_faces(face_map, plan):
    """Return actual exterior faces that carry a fixed-temperature condition."""
    selected = np.zeros(len(face_map.cells), dtype=bool)
    for boundary in plan.boundaries:
        if boundary.region_id:
            selected |= face_map.select(boundary.region_id)
            continue
        selector = boundary.selector.value
        axis = "xyz".index(selector[5])
        sign = 1 if selector.endswith("max") else -1
        layer = (
            face_map.cells[:, axis].max()
            if sign > 0
            else face_map.cells[:, axis].min()
        )
        selected |= (
            (face_map.cells[:, axis] == layer)
            & (face_map.directions[:, axis] * sign > 0.99)
        )
    return selected


class ThermalSurfaceTerms:
    def __init__(self, face_map, fixed, plan, pitch_mm, excluded_faces=None):
        self.cells = face_map.cells
        count = len(self.cells)
        self.flux = np.zeros(count)
        self.convection = np.zeros(count)
        self.ambient_rhs = np.zeros(count)
        self.radiation = np.zeros(count)
        self.radiation_rhs = np.zeros(count)
        self.mapping = []
        self.selections = []
        area = (pitch_mm / 1000)**2
        fixed_faces = _fixed_boundary_faces(face_map, plan)
        free = ~fixed[tuple(self.cells.T)]
        if excluded_faces is not None:
            free &= ~excluded_faces
        if plan.convection is not None and plan.global_convection_enabled:
            self.convection[free] = plan.convection.heat_transfer_coefficient_w_m2_k * area
            self.ambient_rhs = self.convection * plan.convection.ambient_temperature_k
        used = {kind: np.zeros(count, dtype=bool) for kind in ("heat_flux", "convection", "radiation")}
        for condition in plan.surface_conditions:
            selected = face_map.select(condition.region_id)
            if not selected.any():
                raise ValueError("区域热边界未映射到任何外露单元面，请加密网格或扩大区域")
            if np.any(selected & fixed_faces):
                raise ValueError("区域换热或热流作用于定温单元的同一外露面，请调整区域或边界条件")
            if excluded_faces is not None and np.any(selected & excluded_faces):
                raise ValueError("区域换热或热流作用于热接触面，请调整区域或边界条件")
            # A side face may belong to a cell that is fixed through a different
            # exterior face. That edge intersection is not a boundary-condition
            # conflict; omit the fixed cell's side-face contribution consistently.
            selected &= free
            if not selected.any():
                raise ValueError("区域热边界仅映射到定温单元，请调整区域或加密网格")
            if np.any(selected & used[condition.kind]):
                raise ValueError("相同类型的区域热边界存在重叠，请合并条件或调整选区")
            used[condition.kind] |= selected
            if condition.kind == "heat_flux":
                self.flux[selected] = condition.heat_flux_w_m2 * area
            elif condition.kind == "convection":
                self.convection[selected] = condition.heat_transfer_coefficient_w_m2_k * area
                self.ambient_rhs[selected] = self.convection[selected] * condition.ambient_temperature_k
            else:
                self.radiation[selected] = condition.emissivity * STEFAN_BOLTZMANN * area
                self.radiation_rhs[selected] = self.radiation[selected] * condition.radiation_temperature_k**4
            self.mapping.append({
                "region_id": condition.region_id, "kind": condition.kind,
                "mapped_faces": int(selected.sum()), "mapped_cells": len(np.unique(self.cells[selected], axis=0)),
                "mapped_area": {"value": float(selected.sum() * area), "unit": "m^2"},
            })
            self.selections.append((condition.kind, selected))

    def aggregate(self, shape):
        result = []
        for values in (self.convection, self.flux + self.ambient_rhs + self.radiation_rhs, self.radiation):
            field = np.zeros(shape)
            np.add.at(field, tuple(self.cells.T), values)
            result.append(field)
        return result

    def validate_anchors(self, active, fixed, transient, contact_links=()):
        if transient:
            return
        from scipy.ndimage import label
        labels, count = label(active)
        parent = list(range(count + 1))

        def find(value):
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(first, second):
            first, second = find(first), find(second)
            if first != second:
                parent[second] = first

        for link in contact_links:
            source_label = int(labels[link.source])
            target_label = int(labels[link.target])
            if source_label and target_label:
                union(source_label, target_label)
        anchored = {find(int(value)) for value in labels[fixed] if value}
        anchored.update(find(int(value)) for value in labels[tuple(self.cells[(self.convection > 0) | (self.radiation > 0)].T)] if value)
        if any(find(component) not in anchored for component in range(1, count + 1)):
            raise ValueError("至少一个连通组件没有定温或换热温度基准，稳态温度无法唯一确定；请设置换热边界或改为瞬态分析")

    def powers(self, temperature):
        values = temperature[tuple(self.cells.T)]
        return {"surface_heat_flux": float(self.flux.sum()),
                "convection": float((self.ambient_rhs - self.convection * values).sum()),
                "radiation": float((self.radiation_rhs - self.radiation * values**4).sum())}
