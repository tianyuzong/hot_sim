"""Deterministic acceptance policy for GPT-produced simulation plans."""

from __future__ import annotations

import math

from .materials import list_materials
from .models import (
    CadFormat,
    FaceSelector,
    PolicyReport,
    SimulationPlan,
    WorkpieceKind,
    WorkpieceRecord,
)

MAX_GRID_POINTS = 250_000
MAX_TOTAL_CELLS = 200_000
MAX_TIME_STEPS = 200
MAX_TRANSIENT_CELL_STEPS = 5_000_000


def validate_plan(workpiece: WorkpieceRecord, plan: SimulationPlan) -> PolicyReport:
    errors: list[str] = []
    warnings: list[str] = []
    derived: dict[str, object] = {}

    if workpiece.dimensions_mm is None:
        errors.append("工件缺少可用于离散化的包围盒尺寸。")
        return PolicyReport(accepted=False, errors=errors)

    is_box = workpiece.kind is WorkpieceKind.BOX
    is_stl = workpiece.kind is WorkpieceKind.CAD_FILE and workpiece.cad_format is CadFormat.STL
    if not is_box and not is_stl:
        errors.append("当前执行器只支持解析长方体或 STL 文件。")
    if is_stl and not workpiece.geometry.available:
        errors.append("STL 必须能够解析并具有三维包围盒。")
    if is_stl:
        if not workpiece.unit_confirmed or workpiece.length_unit is None:
            errors.append("STL 长度单位和实际尺寸尚未由用户确认。")
        warnings.extend(workpiece.geometry.diagnostics)
        expected_components = {component.component_id for component in workpiece.components}
        assigned_components = {
            assignment.component_id for assignment in plan.component_materials
        }
        unknown_components = assigned_components - expected_components
        missing_components = expected_components - assigned_components
        if unknown_components:
            errors.append("材料分配引用了不存在的组件。")
        if missing_components:
            errors.append("每个 STL 组件都必须分配材料。")
        if len(assigned_components) != len(plan.component_materials):
            errors.append("同一个组件不能重复分配材料。")
        catalog = {entry.material_id: entry.material for entry in list_materials()}
        for assignment in plan.component_materials:
            if assignment.material_id is None:
                continue
            catalog_material = catalog.get(assignment.material_id)
            if catalog_material is None:
                errors.append(f"材料目录中不存在 {assignment.material_id!r}。")
            elif assignment.material != catalog_material:
                errors.append(
                    f"组件 {assignment.component_id!r} 的材料属性与目录 ID "
                    f"{assignment.material_id!r} 不一致；用户覆盖值必须移除数据库 ID。"
                )
    heat_sources = plan.heat_sources or ([plan.heat_source] if plan.heat_source is not None else [])
    source_active = bool(heat_sources) and plan.heat_source_enabled
    expected_backend = "voxel_stl_v1" if is_stl or (is_box and source_active) else "analytic_box_v1"
    is_voxel = plan.solver.backend == "voxel_stl_v1"
    if plan.solver.backend != expected_backend:
        errors.append(f"该工件必须使用已注册求解器 {expected_backend}。")
    transient = plan.analysis_type == "transient_conduction"
    expected_analyses = ["transient_thermal"] if transient else ["steady_thermal"]
    if plan.analyses != expected_analyses:
        errors.append("物理场列表与分析类型不一致。")
    time_steps = 0
    if transient:
        if not is_stl:
            errors.append("瞬态导热需要 STL 数值网格；解析长方体仅支持稳态验证。")
        for field, label in (("initial_temperature_k", "初始温度"),
                             ("duration_s", "工作持续时间"), ("time_step_s", "时间步长")):
            if getattr(plan, field) is None:
                errors.append(f"瞬态分析必须明确{label}。")
        if plan.duration_s is not None and plan.time_step_s is not None:
            maximum_step_intervals = math.ceil(plan.duration_s / plan.time_step_s)
            if plan.time_step_s > plan.duration_s:
                errors.append("时间步长不能大于工作持续时间。")
            if maximum_step_intervals > MAX_TIME_STEPS:
                errors.append(
                    f"最大积分步长需要至少 {maximum_step_intervals} 个区间，"
                    f"当前上限为 {MAX_TIME_STEPS}。"
                )
            time_steps = max(maximum_step_intervals, MAX_TIME_STEPS)
            derived.update(
                {
                    "time_steps": MAX_TIME_STEPS,
                    "saved_time_steps": MAX_TIME_STEPS,
                    "maximum_step_intervals": maximum_step_intervals,
                    "maximum_internal_step_s": plan.time_step_s,
                }
            )
            warnings.append(
                "瞬态采用一阶隐式时间积分和材料扩散率自适应真实输出帧；"
                "确认的时间步长是内部积分上限，仍需减小该上限比较时间离散误差。"
            )

    first = plan.boundaries[0] if plan.boundaries else None
    second = plan.boundaries[-1] if plan.boundaries else None
    regions = {r.region_id: r for r in workpiece.regions}
    targets = []
    for boundary in plan.boundaries:
        region = regions.get(boundary.region_id)
        targets.append(region.selector if region and region.kind == "bounding_plane"
                       else boundary.region_id or boundary.selector.value)
    if len(targets) != len(set(targets)):
        errors.append("同一个定温区域不能重复施加条件。")
    if is_box and (len(plan.boundaries) != 2 or any(b.region_id for b in plan.boundaries)
                   or first.selector.axis != second.selector.axis or first.selector.side == second.selector.side):
        errors.append("解析长方体需要在同一坐标轴的两个相对表面设置固定温度。")
    for boundary in [*plan.boundaries, *plan.surface_conditions]:
        if boundary.region_id:
            region = regions.get(boundary.region_id)
            if region is None or boundary.kind not in region.supported_condition_kinds:
                errors.append("热边界引用了不存在或不支持该条件的区域。")
    if is_box and plan.surface_conditions:
        errors.append("解析长方体仅支持两端定温验证；区域热流、对流和辐射需要 STL 数值网格。")
    if plan.contacts:
        if not is_stl:
            errors.append("组件热接触需要 STL 数值网格。")
        if is_stl:
            component_ids = {component.component_id for component in workpiece.components}
            region_by_id = {region.region_id: region for region in workpiece.regions}
            pairs: set[tuple[str, str]] = set()
            for contact in plan.contacts:
                if contact.source_component_id not in component_ids or contact.target_component_id not in component_ids:
                    errors.append("热接触引用了不存在的组件。")
                if contact.source_component_id == contact.target_component_id:
                    errors.append("热接触的两个组件必须不同。")
                pair = tuple(sorted((contact.source_component_id, contact.target_component_id)))
                if pair in pairs:
                    errors.append("同一对组件不能重复定义热接触。")
                pairs.add(pair)
                for component_id, region_id in ((contact.source_component_id, contact.source_region_id),
                                                (contact.target_component_id, contact.target_region_id)):
                    region = region_by_id.get(region_id)
                    if region is None:
                        errors.append("热接触引用了不存在的表面区域。")
                    elif component_id not in region.component_ids:
                        errors.append("热接触区域不属于对应组件。")
                    elif region.kind not in {"component_surface", "surface_patch"}:
                        errors.append("热接触区域必须是组件表面或手工表面选区。")
            if plan.contacts:
                warnings.append("热接触使用外露体素面的近邻配对；无法可靠映射的间隙或粗网格会阻止求解。")
    if plan.surface_conditions:
        warnings.append("区域对流替换该区域的全局对流；热流和辐射可叠加，未指定换热的表面按绝热处理。")
    if any(condition.kind == "radiation" for condition in plan.surface_conditions):
        warnings.append("辐射模型为灰体表面对大型等温环境的净辐射，不包含表面间视角因子、自遮挡、光谱效应或参与介质。")
    if any(b.region_id for b in plan.boundaries):
        warnings.append("选面区域通过外露体素面映射到定温单元中心；边缘存在体素近似，需检查区域映射和网格收敛。")
    if (
        is_box and first is not None and second is not None
        and math.isclose(first.temperature_k, second.temperature_k, rel_tol=0.0, abs_tol=1e-9)
        and not source_active
    ):
        errors.append("两个固定温度必须不同。")

    if is_voxel and source_active:
        bbox = workpiece.geometry.summary.get("bbox")
        if is_box:
            bbox = [0, 0, 0, *workpiece.dimensions_mm.as_tuple()]
        if not isinstance(bbox, list) or len(bbox) != 6:
            errors.append("工件缺少可用于映射热源位置的包围盒。")
        source_ids = [source.source_id for source in heat_sources if source.source_id]
        if len(source_ids) != len(set(source_ids)):
            errors.append("多个热源的 ID 不能重复。")
        for index, source in enumerate(heat_sources, start=1):
            label = source.name or f"热源 {index}"
            if isinstance(bbox, list) and len(bbox) == 6:
                point = source.center_mm.as_tuple()
                outside = [
                    axis
                    for axis, value, lower, upper in zip(
                        ("X", "Y", "Z"), point, bbox[:3], bbox[3:], strict=True
                    )
                    if value < float(lower) or value > float(upper)
                ]
                if outside:
                    warnings.append(
                        f"{label}请求位置超出工件包围盒的 {', '.join(outside)} 轴；"
                        "求解时会映射到最近的可用体素。"
                    )
            if source.shape == "line":
                if source.end_mm is None:
                    errors.append(f"{label}为线热源，必须指定终点。")
                elif all(
                    math.isclose(first, second, rel_tol=0.0, abs_tol=1e-9)
                    for first, second in zip(
                        source.center_mm.as_tuple(), source.end_mm.as_tuple(), strict=True
                    )
                ):
                    errors.append(f"{label}的线热源起点和终点不能重合。")
            if source.shape == "surface":
                if source.surface_normal_axis is None:
                    errors.append(f"{label}为面热源，必须指定法向轴。")
                if source.surface_width_mm is None or source.surface_height_mm is None:
                    errors.append(f"{label}为面热源，必须指定宽度和高度。")
                if source.surface_thickness_mm is None:
                    errors.append(f"{label}为面热源，必须指定映射厚度。")
            if source.shape == "volume" and any(value is None for value in (
                source.volume_width_mm, source.volume_height_mm, source.volume_depth_mm,
            )):
                errors.append(f"{label}为体热源，必须指定 X、Y、Z 三个方向的尺寸。")

    dimensions = workpiece.dimensions_mm
    intervals: dict[str, int] = {}
    effective_pitch = plan.mesh.target_element_size_mm
    if is_voxel:
        effective_pitch = max(
            effective_pitch,
            max(workpiece.dimensions_mm.as_tuple()) / plan.mesh.max_axis_intervals,
        )
    for axis, length in zip(("x", "y", "z"), dimensions.as_tuple(), strict=True):
        requested = max(2, math.ceil(length / effective_pitch))
        intervals[axis] = min(requested, plan.mesh.max_axis_intervals)
        if requested > plan.mesh.max_axis_intervals:
            warnings.append(
                f"{axis.upper()} 轴区间数从 {requested} 限制为 {plan.mesh.max_axis_intervals}。"
            )

    if is_voxel:
        voxel_shape = {axis: value + 1 for axis, value in intervals.items()}
        points = math.prod(value + 1 for value in voxel_shape.values())
        cells = math.prod(voxel_shape.values())
        derived.update(
            {
                "intervals": intervals,
                "effective_pitch_mm": effective_pitch,
                "estimated_voxel_shape": voxel_shape,
                "grid_points": points,
                "cells": cells,
            }
        )
        if min(intervals.values()) < 4:
            warnings.append("至少一个包围盒方向少于 4 个区间，局部几何分辨率较低。")
    else:
        points = math.prod(value + 1 for value in intervals.values())
        cells = math.prod(intervals.values())
        derived.update({"intervals": intervals, "grid_points": points, "cells": cells})
    if points > MAX_GRID_POINTS:
        errors.append(f"请求的网格包含 {points} 个点，策略上限为 {MAX_GRID_POINTS}。")
    if cells > MAX_TOTAL_CELLS:
        errors.append(f"请求的网格包含 {cells} 个单元，策略上限为 {MAX_TOTAL_CELLS}。")
    if time_steps * cells > MAX_TRANSIENT_CELL_STEPS:
        errors.append("瞬态网格规模与时间步数的乘积超出当前资源上限，请调整网格或时间步长。")

    if plan.confidence < 0.35:
        warnings.append("GPT 置信度较低，建模假设需要工程人员复核。")

    temperatures = [item.temperature_k for item in plan.boundaries]
    temperatures.extend(condition.ambient_temperature_k for condition in plan.surface_conditions if condition.kind == "convection")
    temperatures.extend(condition.radiation_temperature_k for condition in plan.surface_conditions if condition.kind == "radiation")
    peak_boundary_temperature = max(temperatures, default=float("-inf"))
    minimum_boundary_temperature = min(temperatures, default=float("inf"))
    if transient and plan.initial_temperature_k is not None:
        peak_boundary_temperature = max(peak_boundary_temperature, plan.initial_temperature_k)
        minimum_boundary_temperature = min(minimum_boundary_temperature, plan.initial_temperature_k)
    if plan.convection is not None and plan.global_convection_enabled:
        peak_boundary_temperature = max(
            peak_boundary_temperature,
            plan.convection.ambient_temperature_k,
        )
        minimum_boundary_temperature = min(
            minimum_boundary_temperature,
            plan.convection.ambient_temperature_k,
        )
    materials = [plan.material, *(item.material for item in plan.component_materials)]
    checked_materials: set[tuple[str, str | None, float | None, float | None]] = set()
    for material in materials:
        identity = (
            material.name,
            material.source_version,
            material.valid_temperature_min_k,
            material.valid_temperature_max_k,
        )
        if identity in checked_materials:
            continue
        checked_materials.add(identity)
        if (
            material.valid_temperature_max_k is not None
            and peak_boundary_temperature > material.valid_temperature_max_k
        ):
            warnings.append(
                f"{material.name} 的已知适用温度上限为 {material.valid_temperature_max_k:g} K，"
                "当前边界条件已超出该范围。"
            )
        if (
            material.valid_temperature_min_k is not None
            and minimum_boundary_temperature < material.valid_temperature_min_k
        ):
            warnings.append(
                f"{material.name} 的已知适用温度下限为 {material.valid_temperature_min_k:g} K，"
                "当前边界条件已低于该范围。"
            )

    for criterion in plan.criteria:
        if criterion.metric in {"max_temperature", "min_temperature"} and criterion.target.unit != "K":
            errors.append("温度判据必须使用 K。")
        if criterion.metric == "energy_balance_error" and criterion.target.unit != "1":
            errors.append("能量相对误差判据必须使用无量纲单位 1。")

    if any(not _valid_box_selector(boundary.selector) for boundary in plan.boundaries):
        errors.append("边界选择器不是有效的长方体表面。")

    return PolicyReport(
        accepted=not errors,
        errors=errors,
        warnings=warnings,
        derived=derived,
    )


def _valid_box_selector(selector: FaceSelector) -> bool:
    return selector in set(FaceSelector)
