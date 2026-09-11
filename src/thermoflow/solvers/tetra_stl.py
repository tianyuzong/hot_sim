"""Linear tetrahedral FEM with lumped capacity and implicit transient integration."""

from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix, diags

from thermoflow.models import (
    HeatFluxFieldPreview,
    Quantity,
    SimulationResult,
    TemperatureFieldPreview,
    ThermalTimeFrame,
    ThermalTimeStep,
)
from thermoflow.tetrahedral import artifact, exterior_faces, read_tetra_mesh, write_vtk

from .compute import compute_runtime
from .voxel_stl import _distance_to_segment


def assemble(points, cells, conductivity, density, heat_capacity):
    """Assemble SI-unit P1 conduction and positive lumped nodal heat capacity."""
    corners = points[cells] / 1000
    edges = corners[:, 1:] - corners[:, 0, None]
    volumes = np.linalg.det(edges) / 6
    if not np.isfinite(volumes).all() or np.any(volumes <= 0):
        raise ValueError("四面体单元必须具有有限正体积")
    gradients = np.empty((len(cells), 4, 3))
    gradients[:, 1:] = np.linalg.inv(edges).transpose(0, 2, 1)
    gradients[:, 0] = -gradients[:, 1:].sum(axis=1)
    local = (
        conductivity[:, None, None]
        * volumes[:, None, None]
        * (gradients @ gradients.transpose(0, 2, 1))
    )
    rows = np.repeat(cells, 4, axis=1).ravel()
    cols = np.tile(cells, (1, 4)).ravel()
    matrix = coo_matrix((local.ravel(), (rows, cols)), shape=(len(points), len(points))).tocsr()
    # Enforce the exact constant-temperature nullspace to roundoff precision.
    matrix -= diags(np.asarray(matrix.sum(axis=1)).ravel())
    capacity = np.zeros(len(points))
    np.add.at(capacity, cells.ravel(), np.repeat(density * heat_capacity * volumes / 4, 4))
    faces, owners = exterior_faces(cells)
    tri = points[faces] / 1000
    areas = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) / 2
    surface_area = np.zeros(len(points))
    np.add.at(surface_area, faces.ravel(), np.repeat(areas / 3, 3))
    return matrix, capacity, gradients, volumes, surface_area, np.unique(owners)


def map_source(centers, volumes, surface_cells, source):
    """Keep the established nearest-solid source placement; integrate actual volumes."""
    requested = np.asarray(source.center_mm.as_tuple())
    choices = surface_cells if source.placement == "surface" else np.arange(len(centers))
    anchor = int(choices[np.linalg.norm(centers[choices] - requested, axis=1).argmin()])
    resolved = centers[anchor]
    delta = centers - resolved
    resolved_end = None
    if source.shape == "line":
        resolved_end = np.asarray(source.end_mm.as_tuple()) + resolved - requested
        selected = _distance_to_segment(centers, resolved, resolved_end) <= source.radius_mm
    elif source.shape == "surface":
        axis = "xyz".index(source.surface_normal_axis)
        tangent = [i for i in range(3) if i != axis]
        selected = np.abs(delta[:, axis]) <= source.surface_thickness_mm / 2
        selected &= np.abs(delta[:, tangent[0]]) <= source.surface_width_mm / 2
        selected &= np.abs(delta[:, tangent[1]]) <= source.surface_height_mm / 2
    elif source.shape == "volume":
        half = (
            np.array([source.volume_width_mm, source.volume_height_mm, source.volume_depth_mm]) / 2
        )
        selected = np.all(np.abs(delta) <= half, axis=1)
    else:
        selected = np.linalg.norm(delta, axis=1) <= source.radius_mm
    if not selected.any():
        selected[anchor] = True
    power = np.zeros(len(centers))
    power[selected] = source.total_power_w * volumes[selected] / volumes[selected].sum()
    mapping = {
        "source_id": source.source_id,
        "name": source.name,
        "shape": source.shape,
        "placement": source.placement,
        "total_power_w": source.total_power_w,
        "requested_center_mm": requested.tolist(),
        "resolved_center_mm": resolved.tolist(),
        "projection_distance_mm": float(np.linalg.norm(resolved - requested)),
        "requested_embedding_depth_mm": source.embedding_depth_mm,
        "mapped_cell_count": int(selected.sum()),
        "mapped_volume_mm3": float(volumes[selected].sum() * 1e9),
        "distribution": "uniform_power_density_over_selected_tetrahedra",
    }
    if resolved_end is not None:
        mapping.update(
            requested_end_mm=list(source.end_mm.as_tuple()), resolved_end_mm=resolved_end.tolist()
        )
    return power, mapping


def prepare_linear_solver(matrix, preference, tolerance, max_iterations):
    runtime = compute_runtime(preference)
    if not runtime["ready"]:
        raise RuntimeError(f"计算后端不可用：{runtime['detail']}")
    if runtime["selected_backend"] == "cuda-cupy":
        import cupy as cp
        from cupyx.scipy.sparse import csr_matrix
        from cupyx.scipy.sparse import diags as gpu_diags
        from cupyx.scipy.sparse.linalg import cg

        gpu = csr_matrix(matrix)
        preconditioner = gpu_diags(1 / gpu.diagonal())

        def solve(rhs, previous):
            result, info = cg(
                gpu,
                cp.asarray(rhs),
                x0=cp.asarray(previous),
                M=preconditioner,
                rtol=tolerance,
                atol=0,
                maxiter=max_iterations,
            )
            if int(info) != 0 or not bool(cp.isfinite(result).all()):
                raise RuntimeError("四面体 CUDA 温度系统未收敛，请细化网格或增加迭代上限")
            return cp.asnumpy(result)

        return solve, "cuda-cupy-cg-jacobi", runtime["device"]
    from scipy.sparse.linalg import splu

    factor = splu(matrix.tocsc())
    return lambda rhs, previous: factor.solve(rhs), "cpu-scipy-splu", "CPU"


class TetraStlSolver:
    solver_id = "tetra_stl_v1"

    def __init__(self, compute_backend="auto"):
        self.compute_backend = compute_backend

    def solve(self, *, study_id, workpiece, plan, policy, artifact_dir: Path, progress=None):
        if not policy.accepted or plan.solver.backend != self.solver_id:
            raise ValueError("四面体方案未通过校验")
        sources = (
            (plan.heat_sources or ([plan.heat_source] if plan.heat_source else []))
            if plan.heat_source_enabled
            else []
        )
        if (
            plan.analysis_type != "transient_conduction"
            or plan.boundaries
            or plan.surface_conditions
            or plan.contacts
            or any(s.embedding_depth_mm > 0 for s in sources)
        ):
            raise ValueError(
                "四面体当前支持瞬态、坐标定位的内部热源和全局对流，不能忽略其他边界条件"
            )
        data, mesh = read_tetra_mesh(artifact_dir)
        points, cells, indices = data["points"], data["cells"], data["component_indices"]
        materials = {a.component_id: a.material for a in plan.component_materials}
        keys = data["component_keys"]
        if len(keys) > 1 and any(key not in materials for key in keys):
            raise ValueError("组件缺少确认材料")

        def property_values(name):
            return np.array(
                [getattr(materials.get(str(key), plan.material), name) for key in keys]
            )[indices]

        conductivity = property_values("thermal_conductivity_w_m_k")
        if progress:
            progress("assembly", 15)
        stiffness, capacity, gradients, volumes, area, surface_cells = assemble(
            points,
            cells,
            conductivity,
            property_values("density_kg_m3"),
            property_values("specific_heat_j_kg_k"),
        )
        centers = points[cells].mean(axis=1)
        source_power = np.zeros(len(cells))
        mappings = []
        for source in sources:
            power, mapping = map_source(centers, volumes, surface_cells, source)
            source_power += power
            mappings.append(mapping)
        nodal_power = np.zeros(len(points))
        np.add.at(nodal_power, cells.ravel(), np.repeat(source_power / 4, 4))
        total_power = float(sum(s.total_power_w for s in sources))
        convection = plan.convection if plan.global_convection_enabled else None
        ambient = convection.ambient_temperature_k if convection else plan.initial_temperature_k
        h = convection.heat_transfer_coefficient_w_m2_k if convection else 0
        loss = h * area
        matrix = stiffness + diags(loss)
        initial = float(plan.initial_temperature_k)
        rhs = nodal_power + loss * (ambient - initial)
        duration = float(plan.duration_s)
        dt = duration / 200
        if dt > float(plan.time_step_s) * (1 + 1e-12) or not (0 < duration <= 3600):
            raise ValueError("仿真时长或积分步长超过限制")
        storage = capacity / dt
        implicit = matrix + diags(storage)
        if progress:
            progress("solving", 25)
        solve, backend, device = prepare_linear_solver(
            implicit,
            self.compute_backend,
            plan.solver.relative_tolerance,
            plan.solver.max_iterations,
        )
        theta = np.zeros(len(points))
        steps = []
        artifacts = []
        surface_sample = surface_cells[
            np.linspace(0, len(surface_cells) - 1, min(4000, len(surface_cells)), dtype=int)
        ]
        volume_sample = np.linspace(0, len(cells) - 1, min(8000, len(cells)), dtype=int)
        flux_sample = surface_cells[
            np.linspace(0, len(surface_cells) - 1, min(1200, len(surface_cells)), dtype=int)
        ]

        def save_frame(index, time, energy=0.0, residual=0.0):
            nodal = initial + theta
            cell_temperature = nodal[cells].mean(axis=1)
            # Differences ensure a uniform temperature has exactly zero heat flux.
            gradient = np.einsum(
                "eij,ei->ej", gradients[:, 1:], theta[cells[:, 1:]] - theta[cells[:, 0, None]]
            )
            flux = -conductivity[:, None] * gradient
            magnitude = np.linalg.norm(flux, axis=1)
            conv = float(np.dot(loss, ambient - nodal))
            storage_power = energy / dt if index else 0.0
            balance = total_power + conv - storage_power if index else 0.0
            reference = max(abs(total_power) + abs(conv) + abs(storage_power), 1e-20)
            error = abs(balance) / reference
            if index and error > max(1e-5, 100 * plan.solver.relative_tolerance):
                raise RuntimeError(f"四面体在 {time:g} s 的物理功率守恒误差 {error:.3g} 超限")
            powers = {
                name: Quantity(value=value, unit="W")
                for name, value in {
                    "volumetric_source": total_power,
                    "global_convection": conv,
                    "storage": -storage_power,
                    "residual": balance,
                }.items()
            }
            preview = TemperatureFieldPreview(
                sampling="surface_and_volume_cell_centers",
                pitch_mm=mesh.pitch_mm,
                total_surface_cells=len(surface_cells),
                total_volume_cells=len(cells),
                samples=np.column_stack(
                    [centers[surface_sample], cell_temperature[surface_sample]]
                ).tolist(),
                volume_samples=np.column_stack(
                    [centers[volume_sample], cell_temperature[volume_sample]]
                ).tolist(),
            )
            flux_preview = HeatFluxFieldPreview(
                sampling="surface_cell_centers",
                pitch_mm=mesh.pitch_mm,
                total_surface_cells=len(surface_cells),
                minimum_magnitude_w_m2=float(magnitude.min()),
                maximum_magnitude_w_m2=float(magnitude.max()),
                samples=np.column_stack(
                    [centers[flux_sample], flux[flux_sample], magnitude[flux_sample]]
                ).tolist(),
            )
            prefix = f"thermal-{index:04d}"
            step = ThermalTimeStep(
                index=index,
                time_s=time,
                temperature_min_k=float(nodal.min()),
                temperature_max_k=float(nodal.max()),
                minimum_position_mm=tuple(points[nodal.argmin()]),
                maximum_position_mm=tuple(points[nodal.argmax()]),
                energy_balance_relative_error=error,
                stored_energy_change_j=energy,
                linear_residual_relative=residual,
                boundary_power_balance=powers,
                energy_balance_reference_power=Quantity(value=reference, unit="W"),
                field_artifact=prefix + ".json",
                vtk_artifact=prefix + ".vtk",
            )
            write_vtk(
                artifact_dir / step.vtk_artifact,
                points,
                cells,
                indices,
                cell_temperature,
                nodal,
                flux,
            )
            np.savez_compressed(
                artifact_dir / (prefix + ".npz"),
                temperature_k=cell_temperature,
                nodal_temperature_k=nodal,
                centers_mm=centers,
                heat_flux_w_m2=flux,
                time_s=time,
            )
            (artifact_dir / step.field_artifact).write_text(
                ThermalTimeFrame(
                    study_id=study_id,
                    step=step,
                    temperature_field_preview=preview,
                    heat_flux_field_preview=flux_preview,
                ).model_dump_json(),
                encoding="utf-8",
            )
            steps.append(step)
            artifacts.extend(
                [
                    artifact(artifact_dir / step.vtk_artifact, "application/vnd.vtk"),
                    artifact(artifact_dir / step.field_artifact, "application/json"),
                    artifact(artifact_dir / (prefix + ".npz")),
                ]
            )
            return preview, flux_preview, cell_temperature, nodal, flux

        save_frame(0, 0.0)
        for index in range(1, 201):
            step_rhs = rhs + storage * theta
            current = solve(step_rhs, theta)
            residual = float(np.linalg.norm(implicit @ current - step_rhs)) / max(
                float(np.linalg.norm(step_rhs)), 1e-20
            )
            if (
                not np.isfinite(current).all()
                or (initial + current).min() < 1
                or residual > max(1e-10, 10 * plan.solver.relative_tolerance)
            ):
                raise RuntimeError("四面体瞬态温度残差或绝对温度校验失败")
            energy = float(np.dot(capacity, current - theta))
            theta = current
            preview, flux_preview, cell_temperature, nodal, flux = save_frame(
                index, duration * index / 200, energy, residual
            )
            if progress:
                progress("solving", 25 + 70 * index / 200)
        final_path = artifact_dir / "temperature.vtk"
        write_vtk(final_path, points, cells, indices, cell_temperature, nodal, flux)
        artifacts.append(artifact(final_path, "application/vnd.vtk"))
        final = steps[-1]
        undershoot = max(0.0, min(initial, ambient) - min(s.temperature_min_k for s in steps))
        numerical_notices = []
        if undershoot > 1e-6:
            numerical_notices.append(
                f"当前非正交网格出现最大 {undershoot:.4g} K 的离散下冲；"
                "这是网格离散误差，不代表实际冷却，需加密比较。真实节点温度未作截断。"
            )
        return SimulationResult(
            study_id=study_id,
            workpiece_id=workpiece.workpiece_id,
            solver_backend=self.solver_id,
            solver_version="1.0",
            compute_backend=backend,
            compute_device=device,
            analysis_type=plan.analysis_type,
            time_steps=steps,
            time_s=duration,
            temperature_min_k=final.temperature_min_k,
            temperature_max_k=final.temperature_max_k,
            temperature_min_over_time_k=min(s.temperature_min_k for s in steps),
            temperature_max_over_time_k=max(s.temperature_max_k for s in steps),
            maximum_energy_balance_error_over_time=max(
                s.energy_balance_relative_error for s in steps
            ),
            heat_rate_w=total_power,
            heat_source_power_w=total_power,
            heat_source_cells=int(np.count_nonzero(source_power)) or None,
            heat_source_mapping=mappings[0] if mappings else None,
            heat_source_mappings=mappings,
            temperature_field_preview=preview,
            heat_flux_field_preview=flux_preview,
            heat_flux_w_m2=flux_preview.maximum_magnitude_w_m2,
            thermal_resistance_k_w=(final.temperature_max_k - ambient) / total_power
            if total_power > 0
            else None,
            boundary_power_balance=final.boundary_power_balance,
            energy_balance_reference_power=final.energy_balance_reference_power,
            energy_balance_relative_error=final.energy_balance_relative_error,
            grid=mesh.grid,
            artifacts=artifacts,
            assumptions=[
                "原始封闭 STL 贴合四面体；线性有限元、集中热容和一阶隐式时间积分。",
                "各组件使用各自确认的材料和独立节点；未设置热接触时不跨组件传热。",
                "热源按最近实体单元定位，所选四面体内按实际体积均匀分配总功率；投影位置保存在热源映射中。",
                "全局对流按原始三角形表面的实际面积积分；温度回放保留求解节点值。",
                f"真实积分步长 {dt:g} s，共 200 步；网格和时间离散误差需通过加密比较评估。",
            ] + numerical_notices,
        )
