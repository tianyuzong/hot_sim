"""Steady and transient finite-volume conduction for STL meshes."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, TextIO

from thermoflow.execution import ProgressCallback
from thermoflow.meshing import load_solver_mesh, reconstruct_voxel_domain
from thermoflow.models import (
    ArtifactRef,
    CadFormat,
    HeatFluxFieldPreview,
    PolicyReport,
    Quantity,
    SimulationPlan,
    SimulationResult,
    TemperatureFieldPreview,
    ThermalTimeFrame,
    ThermalTimeStep,
    WorkpieceKind,
    WorkpieceRecord,
)
from thermoflow.thermal_contacts import (
    ContactLink,
    add_contact_heat_flux,
    build_contact_links,
    contact_fixed_boundary_power,
    contact_transfer_power,
)

from .compute import compute_runtime, solve_sparse_system
from .time_grid import TransientTimeGrid, build_transient_time_grid
from .transient import integrate_thermal_system


class VoxelStlSolver:
    solver_id = "voxel_stl_v1"

    def __init__(self, compute_backend: str = "auto") -> None:
        if compute_backend not in {"auto", "cpu", "cuda"}:
            raise ValueError("计算后端必须为 auto、cpu 或 cuda")
        self.compute_backend = compute_backend

    def compute_runtime(self) -> dict[str, object]:
        return compute_runtime(self.compute_backend)

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
        is_stl = workpiece.cad_format is CadFormat.STL and bool(workpiece.stored_filename)
        if workpiece.kind is not WorkpieceKind.BOX and not is_stl:
            raise ValueError("voxel_stl_v1 求解器需要长方体或已登记的 STL 文件")
        if plan.solver.backend != self.solver_id:
            raise ValueError(f"不支持的求解器后端：{plan.solver.backend}")

        import numpy as np
        from scipy.sparse import coo_matrix

        source_path = self._source_path(workpiece, artifact_dir)
        if progress:
            progress("geometry", 5)
        mesh = load_solver_mesh(workpiece, source_path)

        pitch_mm = float(policy.derived["effective_pitch_mm"])
        if progress:
            progress("meshing", None)
        voxel_grid, active, domain_mode = reconstruct_voxel_domain(mesh, pitch_mm)
        coordinates = np.argwhere(active)
        if not len(coordinates):
            raise ValueError("STL 体素化后没有活动单元")

        from thermoflow.regions import (
            RegionFaceMap,
            component_cell_masks,
            map_fixed_boundaries,
        )
        from thermoflow.thermal_boundaries import ThermalSurfaceTerms
        face_map = RegionFaceMap(active, np.asarray(voxel_grid.transform), workpiece, source_path, progress)
        component_cells = component_cell_masks(active, workpiece, face_map)
        fixed, fixed_temperature, boundary_mapping = map_fixed_boundaries(
            active=active, transform=np.asarray(voxel_grid.transform), workpiece=workpiece,
            plan=plan, source_path=source_path, progress=progress, face_map=face_map,
        )

        unknown_coordinates = np.argwhere(active & ~fixed)
        temperature = fixed_temperature.copy()
        pitch_m = pitch_mm / 1_000.0
        capacity_volume_fraction = _capacity_volume_fractions(
            active, mesh, component_cells, face_map, pitch_mm,
        )
        conductivity, component_material_count = _conductivity_field(
            active=active,
            plan=plan,
            workpiece=workpiece,
            component_cells=component_cells,
        )
        heat_sources = (
            plan.heat_sources or ([plan.heat_source] if plan.heat_source is not None else [])
        ) if plan.heat_source_enabled else []
        source_mask = np.zeros(active.shape, dtype=bool)
        source_cell_power = np.zeros(active.shape, dtype=float)
        source_mappings: list[dict[str, Any]] = []
        for source_index, heat_source in enumerate(heat_sources):
            selected_mask, source_mapping = _select_source_cells(
                active=active, fixed=fixed,
                voxel_transform=np.asarray(voxel_grid.transform, dtype=float),
                source=heat_source, pitch_mm=pitch_mm,
            )
            selected_count = int(selected_mask.sum())
            source_cell_power[selected_mask] += heat_source.total_power_w / selected_count
            source_mask |= selected_mask
            source_mapping.update({
                "source_index": source_index,
                "source_id": heat_source.source_id,
                "name": heat_source.name,
                "total_power_w": heat_source.total_power_w,
            })
            source_mappings.append(source_mapping)
        source_cell_count = int(source_mask.sum())
        source_power = sum(source.total_power_w for source in heat_sources)
        source_mapping = source_mappings[0] if source_mappings else None
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
        surface_diagonal, surface_rhs, radiation = surface_terms.aggregate(active.shape)
        boundary_mapping.extend(surface_terms.mapping)
        boundary_mapping.extend(contact_mapping)
        contact_neighbors: dict[tuple[int, int, int], list[tuple[tuple[int, int, int], float]]] = {}
        for link in contact_links:
            contact_neighbors.setdefault(link.source, []).append((link.target, link.conductance_w_k))
            contact_neighbors.setdefault(link.target, []).append((link.source, link.conductance_w_k))
        nonlinear_history = []
        energy_reference_powers = []
        balance_reference_power = 1e-30
        def boundary_powers(storage=None):
            powers = {"volumetric_source": source_power, **surface_terms.powers(temperature),
                      "fixed_temperature": _fixed_boundary_heat_flow(active, fixed, temperature, conductivity, pitch_m)
                      + contact_fixed_boundary_power(contact_links, fixed, temperature),
                      "thermal_contact_transfer": contact_transfer_power(contact_links, temperature)}
            if storage is not None:
                powers["storage"] = -storage
                powers["residual"] = sum(value for key, value in powers.items()
                                          if key != "thermal_contact_transfer")
            return powers
        used_compute_backend = "cpu-numpy"
        compute_device = "CPU"
        compute_fallback_reason = None
        transient_steps: list[ThermalTimeStep] = []
        transient_artifacts: list[ArtifactRef] = []
        time_grid: TransientTimeGrid | None = None
        storage_rate_w = 0.0
        transient = plan.analysis_type == "transient_conduction"
        if transient and any(value is None for value in (
            plan.initial_temperature_k, plan.duration_s, plan.time_step_s,
        )):
            raise ValueError("瞬态分析缺少已确认的初始温度、持续时间或时间步长")

        if len(unknown_coordinates):
            unknown_ids = np.full(active.shape, -1, dtype=np.int64)
            unknown_ids[tuple(unknown_coordinates.T)] = np.arange(len(unknown_coordinates))
            rows: list[int] = []
            columns: list[int] = []
            values: list[float] = []
            rhs = np.zeros(len(unknown_coordinates), dtype=float)
            for row, coordinate in enumerate(unknown_coordinates):
                if progress and row % 500 == 0:
                    progress("assembly", 20 + 20 * row / len(unknown_coordinates))
                cell_tuple = tuple(coordinate)
                diagonal = surface_diagonal[cell_tuple]
                rhs[row] = surface_rhs[cell_tuple]
                for neighbor, inside in _six_neighbors(coordinate, active.shape):
                    neighbor_tuple = tuple(neighbor)
                    if not inside or not active[neighbor_tuple]:
                        continue
                    neighbor_conductance = _interface_conductance(
                        conductivity[cell_tuple],
                        conductivity[neighbor_tuple],
                        pitch_m,
                    )
                    diagonal += neighbor_conductance
                    if fixed[neighbor_tuple]:
                        rhs[row] += neighbor_conductance * fixed_temperature[neighbor_tuple]
                    else:
                        rows.append(row)
                        columns.append(int(unknown_ids[neighbor_tuple]))
                        values.append(-neighbor_conductance)
                for neighbor_tuple, conductance in contact_neighbors.get(cell_tuple, ()):
                    diagonal += conductance
                    if fixed[neighbor_tuple]:
                        rhs[row] += conductance * fixed_temperature[neighbor_tuple]
                    else:
                        rows.append(row)
                        columns.append(int(unknown_ids[neighbor_tuple]))
                        values.append(-conductance)
                rhs[row] += source_cell_power[cell_tuple]
                if diagonal <= 0 and not transient and radiation[cell_tuple] == 0:
                    raise ValueError("体素域包含与边界不连通的孤立单元")
                rows.append(row)
                columns.append(row)
                values.append(diagonal)

            matrix = coo_matrix(
                (values, (rows, columns)), shape=(len(unknown_coordinates),) * 2
            ).tocsr()
            if transient:
                if progress:
                    progress("solving", 40)
                temperature[tuple(unknown_coordinates.T)] = plan.initial_temperature_k
                frame_options = {
                    "study_id": study_id, "active": active, "temperature": temperature,
                    "conductivity": conductivity, "source_mask": source_mask,
                    "source_cell_power": source_cell_power, "pitch_mm": pitch_mm,
                    "voxel_transform": np.asarray(voxel_grid.transform, dtype=float),
                    "artifact_dir": artifact_dir, "contact_links": contact_links,
                }
                step, artifacts = _write_time_frame(index=0, time_s=0.0,
                    boundary_power_balance=boundary_powers(), **frame_options)
                transient_steps.append(step)
                transient_artifacts.extend(artifacts)
                density, _ = _conductivity_field(
                    active=active, plan=plan, workpiece=workpiece, property_name="density_kg_m3",
                    component_cells=component_cells,
                )
                heat_capacity, _ = _conductivity_field(
                    active=active, plan=plan, workpiece=workpiece, property_name="specific_heat_j_kg_k",
                    component_cells=component_cells,
                )
                diffusivity = conductivity[active] / (density[active] * heat_capacity[active])
                time_grid = build_transient_time_grid(
                    duration_s=plan.duration_s,
                    maximum_step_s=plan.time_step_s,
                    pitch_m=pitch_m,
                    diffusivities_m2_s=diffusivity.tolist(),
                )
                cell_capacity = (density * heat_capacity * capacity_volume_fraction)[tuple(unknown_coordinates.T)] * pitch_m**3
                for index, (time, solution, solve_outcome, energy, error, residual) in enumerate(
                    integrate_thermal_system(
                        matrix, rhs, cell_capacity, temperature[tuple(unknown_coordinates.T)],
                        duration_s=plan.duration_s, time_step_s=plan.time_step_s,
                        output_times_s=time_grid.output_times_s[1:],
                        relative_tolerance=plan.solver.relative_tolerance,
                        max_iterations=plan.solver.max_iterations, preference=self.compute_backend,
                        radiation=radiation[tuple(unknown_coordinates.T)], nonlinear_history=nonlinear_history,
                        energy_reference_powers=energy_reference_powers,
                    ), start=1,
                ):
                    if progress:
                        progress("solving", 40 + 45 * time / plan.duration_s)
                    temperature[tuple(unknown_coordinates.T)] = solution
                    storage_rate_w = energy / (time - transient_steps[-1].time_s)
                    step, artifacts = _write_time_frame(
                        index=index, time_s=time, stored_energy_change_j=energy,
                        energy_balance_relative_error=error, linear_residual_relative=residual,
                        boundary_power_balance=boundary_powers(storage_rate_w),
                        energy_balance_reference_power=energy_reference_powers[-1],
                        **frame_options,
                    )
                    transient_steps.append(step)
                    transient_artifacts.extend(artifacts)
            else:
                if progress:
                    progress("solving", None)
                if np.any(radiation):
                    from .radiation import solve_radiating_system
                    initial = np.full(len(unknown_coordinates), max(
                        [b.temperature_k for b in plan.boundaries] + [300.0]
                        + [c.radiation_temperature_k for c in plan.surface_conditions if c.kind == "radiation"],
                    ))
                    solve_outcome = solve_radiating_system(matrix, rhs, radiation[tuple(unknown_coordinates.T)], initial,
                        relative_tolerance=plan.solver.relative_tolerance, max_iterations=plan.solver.max_iterations,
                        preference=self.compute_backend, history=nonlinear_history)
                else:
                    solve_outcome = solve_sparse_system(
                        matrix, rhs, relative_tolerance=plan.solver.relative_tolerance,
                        max_iterations=plan.solver.max_iterations, preference=self.compute_backend,
                    )
            used_compute_backend = solve_outcome.backend
            compute_device = solve_outcome.device
            compute_fallback_reason = solve_outcome.fallback_reason
            temperature[tuple(unknown_coordinates.T)] = solve_outcome.solution
            # Absolute equation terms provide a defined reference even at zero net power.
            balance_reference_power = energy_reference_powers[-1] if transient else max(
                float(np.abs(rhs).sum()) + float(np.abs(matrix @ solve_outcome.solution).sum())
                + float(np.sum(radiation[tuple(unknown_coordinates.T)] * solve_outcome.solution**4)), 1e-30,
            )
        if not np.isfinite(temperature[active]).all() or np.any(temperature[active] < 1):
            raise RuntimeError("求解结果包含非物理绝对温度，请检查流出热流、温度基准和工作持续时间")

        if progress:
            progress("fields", 88)
        powers = boundary_powers(storage_rate_w)
        energy_error = abs(powers["residual"]) / balance_reference_power
        reference_values = [b.temperature_k for b in plan.boundaries]
        if plan.convection is not None and plan.global_convection_enabled:
            reference_values.append(plan.convection.ambient_temperature_k)
        reference_values.extend(c.ambient_temperature_k for c in plan.surface_conditions if c.kind == "convection")
        reference_values.extend(c.radiation_temperature_k for c in plan.surface_conditions if c.kind == "radiation")
        reference_temperature = min(reference_values, default=0)
        imposed_power = source_power + float(surface_terms.flux[surface_terms.flux > 0].sum())
        peak_temperature = float(temperature[active].max())
        heat_flux_vectors, heat_flux_magnitude = _compute_heat_flux_field(
            active=active,
            temperature=temperature,
            conductivity=conductivity,
            pitch_m=pitch_m,
        )
        heat_flux_vectors = add_contact_heat_flux(contact_links, temperature, heat_flux_vectors, pitch_mm, active=active)
        heat_flux_magnitude = np.linalg.norm(heat_flux_vectors, axis=-1)

        artifact_dir.mkdir(parents=True, exist_ok=True)
        vtk_path = artifact_dir / "temperature.vtk"
        _write_voxel_vtk(
            vtk_path,
            active=active,
            temperature=temperature,
            heat_flux_vectors=heat_flux_vectors,
            heat_flux_magnitude=heat_flux_magnitude,
            source_mask=source_mask,
            source_cell_power=source_cell_power,
            pitch_mm=pitch_mm,
            origin_mm=np.asarray(voxel_grid.transform[:3, 3], dtype=float) - pitch_mm / 2.0,
        )
        vtk_bytes = vtk_path.read_bytes()
        artifact = ArtifactRef(
            name=vtk_path.name,
            media_type="application/vnd.vtk",
            sha256=hashlib.sha256(vtk_bytes).hexdigest(),
            size_bytes=len(vtk_bytes),
        )
        shape = active.shape
        temperature_field_preview = _build_temperature_field_preview(
            active=active,
            temperature=temperature,
            voxel_transform=np.asarray(voxel_grid.transform, dtype=float),
            pitch_mm=pitch_mm,
        )
        heat_flux_field_preview = _build_heat_flux_field_preview(
            active=active,
            heat_flux_vectors=heat_flux_vectors,
            heat_flux_magnitude=heat_flux_magnitude,
            voxel_transform=np.asarray(voxel_grid.transform, dtype=float),
            pitch_mm=pitch_mm,
        )
        return SimulationResult(
            solver_version="1.5",
            boundary_power_balance={key: Quantity(value=value, unit="W") for key, value in powers.items()},
            nonlinear_convergence=nonlinear_history,
            energy_balance_reference_power=Quantity(value=balance_reference_power, unit="W"),
            analysis_type=plan.analysis_type,
            time_steps=transient_steps,
            time_s=plan.duration_s if transient else None,
            temperature_min_over_time_k=min(s.temperature_min_k for s in transient_steps) if transient_steps else None,
            temperature_max_over_time_k=max(s.temperature_max_k for s in transient_steps) if transient_steps else None,
            maximum_energy_balance_error_over_time=max(s.energy_balance_relative_error or 0 for s in transient_steps) if transient_steps else None,
            study_id=study_id,
            workpiece_id=workpiece.workpiece_id,
            solver_backend=self.solver_id,
            compute_backend=used_compute_backend,
            compute_device=compute_device,
            compute_fallback_reason=compute_fallback_reason,
            temperature_min_k=float(temperature[active].min()),
            temperature_max_k=peak_temperature,
            heat_rate_w=imposed_power,
            heat_source_power_w=source_power if heat_sources else None,
            heat_source_cells=source_cell_count or None,
            heat_source_mapping=source_mapping,
            heat_source_mappings=source_mappings,
            boundary_mapping=boundary_mapping,
            temperature_field_preview=temperature_field_preview,
            heat_flux_field_preview=heat_flux_field_preview,
            heat_flux_w_m2=float(heat_flux_magnitude[active].max()),
            thermal_resistance_k_w=max(0.0, peak_temperature - reference_temperature) / imposed_power
            if imposed_power > 0 and reference_values else None,
            energy_balance_relative_error=float(energy_error),
            grid={
                "x_intervals": int(shape[0]),
                "y_intervals": int(shape[1]),
                "z_intervals": int(shape[2]),
                "points": int(math.prod(value + 1 for value in shape)),
                "cells": len(coordinates),
            },
            artifacts=[artifact, *transient_artifacts],
            assumptions=[
                *plan.assumptions,
                (
                    ("长方体按毫米生成体素域；" if workpiece.kind is WorkpieceKind.BOX else "STL 坐标按毫米解释；")
                    + "导热域模式为 "
                    f"{'封闭网格体积填充' if domain_mode == 'enclosed_volume' else '开放网格近似体素重建'}。"
                ),
                "采用六邻域体素有限体积离散；定温和表面换热使用单元中心温度，未指定换热的表面按绝热处理。",
                *(["辐射为灰体表面对大型等温环境的净辐射，未计算表面间视角因子、自遮挡或参与介质。"] if np.any(radiation) else []),
                *(["瞬态采用后向欧拉积分；初始温度作用于自由单元，定温层在初始时刻即施加边界温度。",
                   "热源和边界在计算时段内保持恒定，能量平衡包含自由单元蓄热。"] if transient else []),
                *([
                    (
                        f"瞬态保存 {len(time_grid.output_times_s)} 个真实求解帧；"
                        f"首个正时刻为 {time_grid.output_times_s[1]:.9g} s，"
                        f"用户确认的最大内部积分步长为 {plan.time_step_s:.9g} s。"
                    ),
                    *(
                        [time_grid.fallback_reason]
                        if time_grid.fallback_reason is not None
                        else [
                            (
                                f"输出时间表指数为 {time_grid.exponent:.6g}；"
                                "未使用前端插值或复制帧。"
                            )
                        ]
                    ),
                ] if transient and time_grid is not None else []),
                f"{len(heat_sources)} 个热源共 {source_power:.6g} W，分配到 {source_cell_count} 个活动体素。",
                *[
                    f"{mapping['name']}：{mapping['placement_label']}从请求坐标映射到 "
                    f"{mapping['resolved_center_mm']} mm。"
                    for mapping in source_mappings
                ],
                "导热与换热按实际体素面面积离散；封闭组件的热容按各自 STL 体积独立修正，开放组件使用体素体积。",
                f"按 {component_material_count} 个连通体素区域应用组件材料。",
                *([f"建立 {len(contact_links)} 条组件热接触有限体积连接；接触热阻按确认的有效面积分配。"] if contact_links else []),
                f"稀疏线性系统实际使用 {used_compute_backend}（{compute_device}）。",
            ],
        )

    @staticmethod
    def _source_path(workpiece: WorkpieceRecord, artifact_dir: Path) -> Path:
        study_directory = artifact_dir.parent
        data_directory = study_directory.parent.parent
        return (
            data_directory
            / "workpieces"
            / workpiece.workpiece_id
            / (workpiece.stored_filename or "generated-box.stl")
        )


def _write_time_frame(
    *, study_id: str, index: int, time_s: float, active: Any, temperature: Any,
    conductivity: Any, source_mask: Any, source_cell_power: Any, pitch_mm: float,
    voxel_transform: Any, artifact_dir: Path, contact_links: list[ContactLink] | None = None,
    stored_energy_change_j: float | None = None,
    energy_balance_relative_error: float | None = None,
    linear_residual_relative: float | None = None,
    boundary_power_balance: dict[str, float] | None = None,
    energy_balance_reference_power: float | None = None,
) -> tuple[ThermalTimeStep, list[ArtifactRef]]:
    import numpy as np

    artifact_dir.mkdir(parents=True, exist_ok=True)
    vectors, magnitude = _compute_heat_flux_field(
        active=active, temperature=temperature, conductivity=conductivity, pitch_m=pitch_mm / 1000,
    )
    if contact_links:
        vectors = add_contact_heat_flux(contact_links, temperature, vectors, pitch_mm, active=active)
        magnitude = np.linalg.norm(vectors, axis=-1)
    coordinates = np.argwhere(active)
    centers = coordinates @ voxel_transform[:3, :3].T + voxel_transform[:3, 3]
    temperatures = temperature[active]
    vtk_path = artifact_dir / f"thermal-{index:04d}.vtk"
    field_path = artifact_dir / f"thermal-{index:04d}.json"
    data_path = artifact_dir / f"thermal-{index:04d}.npz"
    _write_voxel_vtk(
        vtk_path, active=active, temperature=temperature, heat_flux_vectors=vectors,
        heat_flux_magnitude=magnitude, source_mask=source_mask,
        source_cell_power=source_cell_power, pitch_mm=pitch_mm,
        origin_mm=voxel_transform[:3, 3] - pitch_mm / 2,
    )
    # Full cell fields support precise probes independently of display sampling.
    np.savez_compressed(data_path, centers_mm=centers, temperature_k=temperatures,
                        heat_flux_w_m2=vectors[active], time_s=time_s)
    step = ThermalTimeStep(
        energy_balance_reference_power=Quantity(value=energy_balance_reference_power, unit="W") if energy_balance_reference_power is not None else None,
        boundary_power_balance={key: Quantity(value=value, unit="W") for key, value in (boundary_power_balance or {}).items()},
        index=index, time_s=time_s,
        temperature_min_k=float(temperatures.min()), temperature_max_k=float(temperatures.max()),
        minimum_position_mm=tuple(centers[int(temperatures.argmin())]),
        maximum_position_mm=tuple(centers[int(temperatures.argmax())]),
        stored_energy_change_j=stored_energy_change_j,
        energy_balance_relative_error=energy_balance_relative_error,
        linear_residual_relative=linear_residual_relative,
        field_artifact=field_path.name, vtk_artifact=vtk_path.name,
    )
    frame = ThermalTimeFrame(
        study_id=study_id, step=step,
        temperature_field_preview=_build_temperature_field_preview(
            active=active, temperature=temperature, voxel_transform=voxel_transform, pitch_mm=pitch_mm,
        ),
        heat_flux_field_preview=_build_heat_flux_field_preview(
            active=active, heat_flux_vectors=vectors, heat_flux_magnitude=magnitude,
            voxel_transform=voxel_transform, pitch_mm=pitch_mm,
        ),
    )
    field_path.write_text(frame.model_dump_json(), encoding="utf-8")
    artifacts = []
    for path, media_type in ((vtk_path, "application/vnd.vtk"),
                             (field_path, "application/json"),
                             (data_path, "application/octet-stream")):
        content = path.read_bytes()
        artifacts.append(ArtifactRef(name=path.name, media_type=media_type,
                                    size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest()))
    return step, artifacts


def _fixed_boundary_heat_flow(active, fixed, temperature, conductivity, pitch_m):
    import numpy as np

    power = 0.0
    for cell in np.argwhere(active & fixed):
        for neighbor in _neighbors(cell, active.shape):
            target = tuple(neighbor)
            if active[target] and not fixed[target]:
                power += _interface_conductance(conductivity[tuple(cell)], conductivity[target], pitch_m) * (
                    temperature[tuple(cell)] - temperature[target]
                )
    return float(power)


def _axis_layer(shape: tuple[int, ...], axis: int, index: int) -> Any:
    import numpy as np

    layer = np.zeros(shape, dtype=bool)
    slices = [slice(None)] * 3
    slices[axis] = index
    layer[tuple(slices)] = True
    return layer


def _neighbors(coordinate: Any, shape: tuple[int, ...]):
    for axis in range(3):
        for delta in (-1, 1):
            neighbor = coordinate.copy()
            neighbor[axis] += delta
            if 0 <= neighbor[axis] < shape[axis]:
                yield neighbor


def _six_neighbors(coordinate: Any, shape: tuple[int, ...]):
    for axis in range(3):
        for delta in (-1, 1):
            neighbor = coordinate.copy()
            neighbor[axis] += delta
            inside = 0 <= neighbor[axis] < shape[axis]
            yield neighbor, inside


def _select_source_cells(
    *,
    active: Any,
    fixed: Any,
    voxel_transform: Any,
    source: Any,
    pitch_mm: float,
) -> tuple[Any, dict[str, Any]]:
    import numpy as np
    from scipy.ndimage import binary_erosion, distance_transform_edt

    candidates = np.argwhere(active & ~fixed)
    if not len(candidates):
        raise ValueError("定温边界占据了全部活动体素，无法放置热源")
    centers = candidates @ voxel_transform[:3, :3].T + voxel_transform[:3, 3]

    requested_center = np.asarray(source.center_mm.as_tuple(), dtype=float)
    padded = np.pad(active, 1, mode="constant", constant_values=False)
    depth_field = distance_transform_edt(padded)[1:-1, 1:-1, 1:-1] * pitch_mm
    candidate_depths = depth_field[tuple(candidates.T)]
    if source.placement == "surface":
        boundary = active & ~binary_erosion(active, border_value=0)
        anchor_choices = boundary[tuple(candidates.T)]
        placement_label = "贴附表面模式"
    else:
        requested_depth = float(source.embedding_depth_mm)
        anchor_choices = candidate_depths >= requested_depth
        if not anchor_choices.any():
            maximum_depth = float(candidate_depths.max())
            anchor_choices = candidate_depths >= maximum_depth - 1e-12
        placement_label = "嵌入模式"
    eligible_indices = np.flatnonzero(anchor_choices)
    anchor_distances = np.linalg.norm(centers[eligible_indices] - requested_center, axis=1)
    anchor_candidate_index = int(eligible_indices[int(np.argmin(anchor_distances))])
    resolved_center = centers[anchor_candidate_index]
    translation = resolved_center - requested_center

    if source.shape == "line":
        if source.end_mm is None:
            raise ValueError("线热源缺少终点")
        requested_end = np.asarray(source.end_mm.as_tuple(), dtype=float)
        resolved_end = requested_end + translation
        distances = _distance_to_segment(centers, resolved_center, resolved_end)
        selected = distances <= float(source.radius_mm)
    elif source.shape == "surface":
        if (
            source.surface_normal_axis is None
            or source.surface_width_mm is None
            or source.surface_height_mm is None
            or source.surface_thickness_mm is None
        ):
            raise ValueError("面热源缺少法向轴、宽度、高度或厚度")
        normal_axis = {"x": 0, "y": 1, "z": 2}[source.surface_normal_axis]
        tangent_axes = [axis for axis in range(3) if axis != normal_axis]
        delta = np.abs(centers - resolved_center)
        selected = (
            (delta[:, normal_axis] <= max(float(source.surface_thickness_mm) / 2.0, pitch_mm / 2.0))
            & (delta[:, tangent_axes[0]] <= float(source.surface_width_mm) / 2.0)
            & (delta[:, tangent_axes[1]] <= float(source.surface_height_mm) / 2.0)
        )
        resolved_end = None
    elif source.shape == "volume":
        if any(value is None for value in (
            source.volume_width_mm, source.volume_height_mm, source.volume_depth_mm,
        )):
            raise ValueError("体热源缺少 X、Y、Z 三个方向的尺寸")
        half_sizes = np.asarray([
            source.volume_width_mm, source.volume_height_mm, source.volume_depth_mm,
        ], dtype=float) / 2.0
        selected = np.all(
            np.abs(centers - resolved_center) <= np.maximum(half_sizes, pitch_mm / 2.0),
            axis=1,
        )
        resolved_end = None
    else:
        distances = np.linalg.norm(centers - resolved_center, axis=1)
        selected = distances <= float(source.radius_mm)
        resolved_end = None
    if not selected.any():
        selected[anchor_candidate_index] = True
    mask = np.zeros(active.shape, dtype=bool)
    mask[tuple(candidates[selected].T)] = True
    mapping: dict[str, Any] = {
        "shape": source.shape,
        "placement": source.placement,
        "placement_label": placement_label,
        "requested_center_mm": [float(value) for value in requested_center],
        "resolved_center_mm": [float(value) for value in resolved_center],
        "requested_embedding_depth_mm": float(source.embedding_depth_mm),
        "achieved_embedding_depth_mm": float(candidate_depths[anchor_candidate_index]),
        "depth_satisfied": (
            source.placement != "embedded"
            or float(candidate_depths[anchor_candidate_index]) + 1e-12
            >= float(source.embedding_depth_mm)
        ),
        "mapped_cell_count": int(selected.sum()),
    }
    if source.shape == "line":
        mapping["requested_end_mm"] = [float(value) for value in requested_end]
        mapping["resolved_end_mm"] = [float(value) for value in resolved_end]
    return mask, mapping


def _build_temperature_field_preview(
    *,
    active: Any,
    temperature: Any,
    voxel_transform: Any,
    pitch_mm: float,
) -> TemperatureFieldPreview:
    import numpy as np
    from scipy.ndimage import binary_erosion

    surface = active & ~binary_erosion(active, border_value=0)
    surface_coordinates = np.argwhere(surface)
    total_surface_cells = len(surface_coordinates)
    if total_surface_cells > 4_000:
        sample_indices = np.linspace(0, total_surface_cells - 1, 4_000, dtype=np.int64)
        surface_coordinates = surface_coordinates[sample_indices]
    centers = surface_coordinates @ voxel_transform[:3, :3].T + voxel_transform[:3, 3]
    temperatures = temperature[tuple(surface_coordinates.T)]
    samples = [
        (float(center[0]), float(center[1]), float(center[2]), float(sample_temperature))
        for center, sample_temperature in zip(centers, temperatures, strict=True)
    ]

    volume_coordinates = np.argwhere(active)
    total_volume_cells = len(volume_coordinates)
    if total_volume_cells > 8_000:
        sample_indices = np.linspace(0, total_volume_cells - 1, 8_000, dtype=np.int64)
        volume_coordinates = volume_coordinates[sample_indices]
    volume_centers = volume_coordinates @ voxel_transform[:3, :3].T + voxel_transform[:3, 3]
    volume_temperatures = temperature[tuple(volume_coordinates.T)]
    volume_samples = [
        (float(center[0]), float(center[1]), float(center[2]), float(sample_temperature))
        for center, sample_temperature in zip(volume_centers, volume_temperatures, strict=True)
    ]
    return TemperatureFieldPreview(
        pitch_mm=pitch_mm,
        total_surface_cells=total_surface_cells,
        samples=samples,
        total_volume_cells=total_volume_cells,
        volume_samples=volume_samples,
    )


def _compute_heat_flux_field(
    *,
    active: Any,
    temperature: Any,
    conductivity: Any,
    pitch_m: float,
) -> tuple[Any, Any]:
    import numpy as np

    gradient = np.zeros((*active.shape, 3), dtype=float)
    for axis in range(3):
        forward_valid = np.zeros(active.shape, dtype=bool)
        backward_valid = np.zeros(active.shape, dtype=bool)
        forward_temperature = np.zeros(active.shape, dtype=float)
        backward_temperature = np.zeros(active.shape, dtype=float)

        current_slice = [slice(None)] * 3
        neighbor_slice = [slice(None)] * 3
        current_slice[axis] = slice(0, -1)
        neighbor_slice[axis] = slice(1, None)
        current_key = tuple(current_slice)
        neighbor_key = tuple(neighbor_slice)
        forward_valid[current_key] = active[current_key] & active[neighbor_key]
        forward_temperature[current_key] = temperature[neighbor_key]

        current_slice[axis] = slice(1, None)
        neighbor_slice[axis] = slice(0, -1)
        current_key = tuple(current_slice)
        neighbor_key = tuple(neighbor_slice)
        backward_valid[current_key] = active[current_key] & active[neighbor_key]
        backward_temperature[current_key] = temperature[neighbor_key]

        both = forward_valid & backward_valid
        forward_only = forward_valid & ~backward_valid
        backward_only = backward_valid & ~forward_valid
        gradient[..., axis][both] = (
            forward_temperature[both] - backward_temperature[both]
        ) / (2.0 * pitch_m)
        gradient[..., axis][forward_only] = (
            forward_temperature[forward_only] - temperature[forward_only]
        ) / pitch_m
        gradient[..., axis][backward_only] = (
            temperature[backward_only] - backward_temperature[backward_only]
        ) / pitch_m

    vectors = -conductivity[..., np.newaxis] * gradient
    vectors[~active] = 0.0
    magnitude = np.linalg.norm(vectors, axis=-1)
    return vectors, magnitude


def _build_heat_flux_field_preview(
    *,
    active: Any,
    heat_flux_vectors: Any,
    heat_flux_magnitude: Any,
    voxel_transform: Any,
    pitch_mm: float,
) -> HeatFluxFieldPreview:
    import numpy as np
    from scipy.ndimage import binary_erosion

    surface = active & ~binary_erosion(active, border_value=0)
    coordinates = np.argwhere(surface)
    total_surface_cells = len(coordinates)
    if total_surface_cells > 1_200:
        sample_indices = np.linspace(0, total_surface_cells - 1, 1_200, dtype=np.int64)
        coordinates = coordinates[sample_indices]
    centers = coordinates @ voxel_transform[:3, :3].T + voxel_transform[:3, 3]
    vectors = heat_flux_vectors[tuple(coordinates.T)]
    magnitudes = heat_flux_magnitude[tuple(coordinates.T)]
    samples = [
        (
            float(center[0]),
            float(center[1]),
            float(center[2]),
            float(vector[0]),
            float(vector[1]),
            float(vector[2]),
            float(magnitude),
        )
        for center, vector, magnitude in zip(centers, vectors, magnitudes, strict=True)
    ]
    active_magnitudes = heat_flux_magnitude[active]
    return HeatFluxFieldPreview(
        pitch_mm=pitch_mm,
        total_surface_cells=total_surface_cells,
        minimum_magnitude_w_m2=float(active_magnitudes.min()),
        maximum_magnitude_w_m2=float(active_magnitudes.max()),
        samples=samples,
    )


def _distance_to_segment(points: Any, start: Any, end: Any) -> Any:
    import numpy as np

    direction = end - start
    squared_length = float(np.dot(direction, direction))
    if squared_length <= 1e-24:
        return np.linalg.norm(points - start, axis=1)
    fraction = np.clip(((points - start) @ direction) / squared_length, 0.0, 1.0)
    projections = start + fraction[:, np.newaxis] * direction
    return np.linalg.norm(points - projections, axis=1)


def _boundary_heat_flow(
    active: Any,
    temperature: Any,
    conductivity: Any,
    pitch_m: float,
    axis: int,
    layer_index: int,
    boundary_temperature: float,
) -> float:
    import numpy as np

    total = 0.0
    for coordinate in np.argwhere(active & _axis_layer(active.shape, axis, layer_index)):
        for neighbor in _neighbors(coordinate, active.shape):
            neighbor_tuple = tuple(neighbor)
            if active[neighbor_tuple] and neighbor[axis] != layer_index:
                conductance = _interface_conductance(
                    conductivity[tuple(coordinate)],
                    conductivity[neighbor_tuple],
                    pitch_m,
                )
                total += conductance * (boundary_temperature - temperature[neighbor_tuple])
    return float(total)


def _interface_conductance(
    first_conductivity: float,
    second_conductivity: float,
    pitch_m: float,
) -> float:
    harmonic = 2.0 * first_conductivity * second_conductivity / (
        first_conductivity + second_conductivity
    )
    # G = k_harmonic A / d, with A = pitch² and d = pitch.
    return float(harmonic * pitch_m)


def _capacity_volume_fractions(active, mesh, component_cells, face_map, pitch_mm):
    import numpy as np

    fractions = np.ones(active.shape)
    if len(component_cells) <= 1:
        bodies = [(active, mesh)]
    else:
        from thermoflow.stl_geometry import _component_face_groups, _component_record
        # The original geometry defines stable IDs; volumes must use confirmed units.
        groups = {_component_record(group, 0).component_id: group
                  for group, _ in _component_face_groups(face_map.original)}
        bodies = []
        for component_id, cells in component_cells.items():
            body = groups[component_id].copy()
            body.apply_scale(face_map.workpiece.length_unit.scale_to_mm)
            bodies.append((cells, body))
    for cells, body in bodies:
        if body.is_watertight:
            volume = abs(float(body.volume))
            if not np.isfinite(volume) or volume <= 0:
                raise ValueError("封闭组件体积无效，无法确定其热容")
            fractions[cells] = volume / (np.count_nonzero(cells) * pitch_mm**3)
    return fractions


def _conductivity_field(
    *,
    active: Any,
    plan: SimulationPlan,
    workpiece: WorkpieceRecord,
    component_cells: dict[str, Any],
    property_name: str = "thermal_conductivity_w_m_k",
) -> tuple[Any, int]:
    import numpy as np

    field = np.full(active.shape, getattr(plan.material, property_name), dtype=float)
    if not plan.component_materials:
        return field, 1

    assignments = {item.component_id: item.material for item in plan.component_materials}
    for component_id, cells in component_cells.items():
        material = assignments.get(component_id)
        if material is None:
            raise ValueError("组件缺少已确认的材料，材料映射已停止")
        field[cells] = getattr(material, property_name)
    return field, max(1, len(component_cells))


def _convection_heat_flow(
    *,
    active: Any,
    fixed: Any,
    temperature: Any,
    ambient_temperature: float,
    face_conductance: float,
) -> float:
    import numpy as np

    total = 0.0
    for coordinate in np.argwhere(active & ~fixed):
        exposed_faces = 0
        for neighbor, inside in _six_neighbors(coordinate, active.shape):
            if not inside or not active[tuple(neighbor)]:
                exposed_faces += 1
        total += (
            exposed_faces
            * face_conductance
            * (ambient_temperature - temperature[tuple(coordinate)])
        )
    return float(total)


def _write_voxel_vtk(
    path: Path,
    *,
    active: Any,
    temperature: Any,
    heat_flux_vectors: Any,
    heat_flux_magnitude: Any,
    source_mask: Any,
    source_cell_power: Any,
    pitch_mm: float,
    origin_mm: Any,
) -> None:
    import numpy as np

    nx, ny, nz = (int(value) for value in active.shape)
    point_count = (nx + 1) * (ny + 1) * (nz + 1)
    coordinates = np.argwhere(active)
    with path.open("w", encoding="ascii", newline="\n") as stream:
        stream.write("# vtk DataFile Version 3.0\n")
        stream.write("ThermoFlow STL steady-state temperature field\n")
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
        stream.write("SCALARS temperature_k double 1\n")
        stream.write("LOOKUP_TABLE default\n")
        for coordinate in coordinates:
            stream.write(f"{temperature[tuple(coordinate)]:.12g}\n")
        stream.write("VECTORS heat_flux_w_m2 double\n")
        for coordinate in coordinates:
            vector = heat_flux_vectors[tuple(coordinate)]
            stream.write(f"{vector[0]:.12g} {vector[1]:.12g} {vector[2]:.12g}\n")
        stream.write("SCALARS heat_flux_magnitude_w_m2 double 1\n")
        stream.write("LOOKUP_TABLE default\n")
        for coordinate in coordinates:
            stream.write(f"{heat_flux_magnitude[tuple(coordinate)]:.12g}\n")
        stream.write("SCALARS heat_source_power_w double 1\n")
        stream.write("LOOKUP_TABLE default\n")
        for coordinate in coordinates:
            value = source_cell_power[tuple(coordinate)] if source_mask[tuple(coordinate)] else 0.0
            stream.write(f"{value:.12g}\n")


def _point_id(i: int, j: int, k: int, nx: int, ny: int) -> int:
    return (int(k) * (ny + 1) + int(j)) * (nx + 1) + int(i)


def _write_repeated(stream: TextIO, value: str, count: int, per_line: int = 20) -> None:
    stream.writelines(
        " ".join([value] * min(per_line, count - offset)) + "\n"
        for offset in range(0, count, per_line)
    )
