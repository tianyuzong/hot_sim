"""Verified analytical steady-conduction reference solver for a homogeneous box."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from thermoflow.execution import ProgressCallback
from typing import TextIO, cast

from thermoflow.models import (
    ArtifactRef,
    PolicyReport,
    SimulationPlan,
    SimulationResult,
    WorkpieceRecord,
)


class AnalyticBoxSolver:
    solver_id = "analytic_box_v1"

    def solve(
        self,
        *,
        study_id: str,
        workpiece: WorkpieceRecord,
        plan: SimulationPlan,
        policy: PolicyReport,
        artifact_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> SimulationResult:
        if not policy.accepted:
            raise ValueError("不能执行未通过策略校验的仿真方案")
        if workpiece.dimensions_mm is None:
            raise ValueError("analytic_box_v1 求解器需要长方体尺寸")
        if plan.solver.backend != self.solver_id:
            raise ValueError(f"不支持的求解器后端：{plan.solver.backend}")
        if progress:
            progress("solving", 30)

        boundary_by_side = {item.selector.value: item.temperature_k for item in plan.boundaries}
        axis = plan.boundaries[0].selector.axis
        t_at_min = boundary_by_side[f"face.{axis}min"]
        t_at_max = boundary_by_side[f"face.{axis}max"]
        dimensions = workpiece.dimensions_mm
        lengths_mm = {"x": dimensions.x, "y": dimensions.y, "z": dimensions.z}
        length_m = lengths_mm[axis] / 1_000.0
        other_axes = [name for name in ("x", "y", "z") if name != axis]
        area_m2 = math.prod(lengths_mm[name] for name in other_axes) * 1e-6
        conductivity = plan.material.thermal_conductivity_w_m_k
        thermal_resistance = length_m / (conductivity * area_m2)
        signed_heat_flux = -conductivity * (t_at_max - t_at_min) / length_m
        heat_rate = abs(signed_heat_flux) * area_m2

        intervals = cast(dict[str, int], policy.derived["intervals"])
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if progress:
            progress("fields", 80)
        vtk_path = artifact_dir / "temperature.vtk"
        _write_legacy_vtk(
            vtk_path,
            dimensions=lengths_mm,
            intervals=intervals,
            heat_axis=axis,
            t_at_min=t_at_min,
            t_at_max=t_at_max,
        )
        vtk_bytes = vtk_path.read_bytes()
        artifact = ArtifactRef(
            name=vtk_path.name,
            media_type="application/vnd.vtk",
            sha256=hashlib.sha256(vtk_bytes).hexdigest(),
            size_bytes=len(vtk_bytes),
        )
        return SimulationResult(
            study_id=study_id,
            workpiece_id=workpiece.workpiece_id,
            solver_backend=self.solver_id,
            compute_backend="cpu-analytic",
            compute_device="CPU",
            temperature_min_k=min(t_at_min, t_at_max),
            temperature_max_k=max(t_at_min, t_at_max),
            heat_rate_w=heat_rate,
            heat_flux_w_m2=signed_heat_flux,
            thermal_resistance_k_w=thermal_resistance,
            energy_balance_relative_error=0.0,
            grid={
                "x_intervals": intervals["x"],
                "y_intervals": intervals["y"],
                "z_intervals": intervals["z"],
                "points": int(policy.derived["grid_points"]),
                "cells": int(policy.derived["cells"]),
            },
            artifacts=[artifact],
            assumptions=[
                *plan.assumptions,
                "解析参考解假定未选中的四个表面均为绝热边界。",
            ],
        )


def _write_legacy_vtk(
    path: Path,
    *,
    dimensions: dict[str, float],
    intervals: dict[str, int],
    heat_axis: str,
    t_at_min: float,
    t_at_max: float,
) -> None:
    nx, ny, nz = intervals["x"], intervals["y"], intervals["z"]
    point_count = (nx + 1) * (ny + 1) * (nz + 1)
    cell_count = nx * ny * nz
    with path.open("w", encoding="ascii", newline="\n") as stream:
        stream.write("# vtk DataFile Version 3.0\n")
        stream.write("ThermoFlow steady-state temperature field\n")
        stream.write("ASCII\n")
        stream.write("DATASET UNSTRUCTURED_GRID\n")
        stream.write(f"POINTS {point_count} double\n")
        for k in range(nz + 1):
            z = dimensions["z"] * k / nz
            for j in range(ny + 1):
                y = dimensions["y"] * j / ny
                for i in range(nx + 1):
                    x = dimensions["x"] * i / nx
                    stream.write(f"{x:.12g} {y:.12g} {z:.12g}\n")

        stream.write(f"CELLS {cell_count} {cell_count * 9}\n")
        for k in range(nz):
            for j in range(ny):
                for i in range(nx):
                    nodes = (
                        _point_id(i, j, k, nx, ny),
                        _point_id(i + 1, j, k, nx, ny),
                        _point_id(i + 1, j + 1, k, nx, ny),
                        _point_id(i, j + 1, k, nx, ny),
                        _point_id(i, j, k + 1, nx, ny),
                        _point_id(i + 1, j, k + 1, nx, ny),
                        _point_id(i + 1, j + 1, k + 1, nx, ny),
                        _point_id(i, j + 1, k + 1, nx, ny),
                    )
                    stream.write("8 " + " ".join(str(node) for node in nodes) + "\n")
        stream.write(f"CELL_TYPES {cell_count}\n")
        _write_repeated(stream, "12", cell_count)

        stream.write(f"POINT_DATA {point_count}\n")
        stream.write("SCALARS temperature_k double 1\n")
        stream.write("LOOKUP_TABLE default\n")
        axis_index = {"x": 0, "y": 1, "z": 2}[heat_axis]
        axis_intervals = intervals[heat_axis]
        for k in range(nz + 1):
            for j in range(ny + 1):
                for i in range(nx + 1):
                    step = (i, j, k)[axis_index]
                    fraction = step / axis_intervals
                    temperature = t_at_min + (t_at_max - t_at_min) * fraction
                    stream.write(f"{temperature:.12g}\n")


def _point_id(i: int, j: int, k: int, nx: int, ny: int) -> int:
    return (k * (ny + 1) + j) * (nx + 1) + i


def _write_repeated(stream: TextIO, value: str, count: int, per_line: int = 20) -> None:
    stream.writelines(
        " ".join([value] * min(per_line, count - offset)) + "\n"
        for offset in range(0, count, per_line)
    )
