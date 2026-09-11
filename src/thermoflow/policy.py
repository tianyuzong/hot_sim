"""Deterministic acceptance policy for GPT-produced simulation plans."""

from __future__ import annotations

import math

from .materials import list_materials
from .models import (
    CadFormat,
    FaceSelector,
    PolicyIssue,
    PolicyReport,
    SimulationPlan,
    WorkpieceKind,
    WorkpieceRecord,
)

MAX_GRID_POINTS = 250_000
MAX_TOTAL_CELLS = 200_000
MAX_TIME_STEPS = 200
# Support the same bounded STL grid in transient and steady analyses. Output
# frames stay capped at 200; the task timeout still bounds actual integration.
MAX_TRANSIENT_CELL_STEPS = MAX_TOTAL_CELLS * MAX_TIME_STEPS


def _number(value: float) -> str:
    return f"{value:g}"


def _temperature(value_k: float) -> str:
    value_c = f"{value_k - 273.15:.2f}".rstrip("0").rstrip(".")
    return f"{_number(value_k)} K（{value_c} ℃）"


def _record_issue(
    errors: list[str],
    warnings: list[str],
    issues: list[PolicyIssue],
    *,
    code: str,
    severity: str,
    message: str,
    suggestion: str = "",
    fields: list[str] | None = None,
) -> None:
    issue = PolicyIssue(
        code=code,
        severity=severity,
        message=message,
        suggestion=suggestion,
        fields=fields or [],
    )
    issues.append(issue)
    (errors if severity == "error" else warnings).append(message)


_FACE_NAMES = {
    "face.xmin": "X 最小面",
    "face.xmax": "X 最大面",
    "face.ymin": "Y 最小面",
    "face.ymax": "Y 最大面",
    "face.zmin": "Z 最小面",
    "face.zmax": "Z 最大面",
}


def validate_plan(workpiece: WorkpieceRecord, plan: SimulationPlan) -> PolicyReport:
    errors: list[str] = []
    warnings: list[str] = []
    issues: list[PolicyIssue] = []
    derived: dict[str, object] = {}

    if workpiece.dimensions_mm is None:
        errors.append("工件缺少可用于离散化的包围盒尺寸。")
        return PolicyReport(accepted=False, errors=errors, issues=issues)

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
    is_tetra = plan.solver.backend == "tetra_stl_v1"
    if is_tetra and is_stl:
        expected_backend = "tetra_stl_v1"
        if not all(c.watertight for c in workpiece.components):
            errors.append("贴合表面的四面体网格需要封闭 STL 组件。")
        if plan.analysis_type != "transient_conduction" or plan.boundaries or plan.surface_conditions or plan.contacts:
            errors.append("四面体求解器当前支持瞬态导热、内部热源和全局对流；局部边界与热接触请使用体素网格。")
        if source_active and any(s.embedding_depth_mm > 0 for s in heat_sources):
            errors.append("四面体热源暂不支持指定嵌入深度，请设为 0 并通过坐标定位。")
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
                _record_issue(
                    errors,
                    warnings,
                    issues,
                    code=f"missing_transient_{field}",
                    severity="error",
                    message=f"瞬态分析缺少{label}。",
                    suggestion=f"填写{label}后重新校验。",
                    fields=[field],
                )
        if plan.duration_s is not None and plan.time_step_s is not None:
            # Exact 200-interval settings such as 57 / 0.285 can round up by one ULP.
            maximum_step_intervals = math.ceil(
                math.nextafter(plan.duration_s / plan.time_step_s, -math.inf)
            )
            if plan.time_step_s > plan.duration_s:
                _record_issue(
                    errors,
                    warnings,
                    issues,
                    code="time_step_exceeds_duration",
                    severity="error",
                    message=(
                        f"工作持续时间 {_number(plan.duration_s)} s，时间步长 "
                        f"{_number(plan.time_step_s)} s；时间步长大于整个仿真时段。"
                    ),
                    suggestion=f"将时间步长设为不超过 {_number(plan.duration_s)} s。",
                    fields=["duration_s", "time_step_s"],
                )
            if maximum_step_intervals > MAX_TIME_STEPS:
                minimum_step_s = plan.duration_s / MAX_TIME_STEPS
                _record_issue(
                    errors,
                    warnings,
                    issues,
                    code="too_many_time_steps",
                    severity="error",
                    message=(
                        f"持续时间 {_number(plan.duration_s)} s、时间步长 "
                        f"{_number(plan.time_step_s)} s 需要 {maximum_step_intervals} 个积分区间，"
                        f"超过上限 {MAX_TIME_STEPS}。"
                    ),
                    suggestion=(
                        f"在持续时间不变时，将时间步长设为至少 {_number(minimum_step_s)} s；"
                        "或缩短持续时间。"
                    ),
                    fields=["duration_s", "time_step_s"],
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
                ("四面体瞬态采用一阶隐式积分，保存 201 个等间隔真实时刻；" if is_tetra else "瞬态采用一阶隐式时间积分和材料扩散率自适应真实输出帧；") +
                "确认的时间步长是内部积分上限，仍需减小该上限比较时间离散误差。"
            )

    first = plan.boundaries[0] if plan.boundaries else None
    second = plan.boundaries[-1] if plan.boundaries else None
    regions = {r.region_id: r for r in workpiece.regions}
    targets: list[str] = []
    for boundary in plan.boundaries:
        region = regions.get(boundary.region_id)
        targets.append(region.selector if region and region.kind == "bounding_plane"
                       else boundary.region_id or boundary.selector.value)
    target_rows: dict[str, list[int]] = {}
    for index, target in enumerate(targets):
        target_rows.setdefault(target, []).append(index)
    for target, rows in target_rows.items():
        if len(rows) < 2:
            continue
        aliases = [regions.get(plan.boundaries[index].region_id) for index in rows]
        named_region = next(
            (region for region in aliases if region is not None and region.kind == "bounding_plane"),
            None,
        )
        target_label = (
            f"区域“{named_region.name}”（{target}）"
            if named_region is not None
            else f"{_FACE_NAMES.get(target, '区域')}（{target}）"
        )
        if len(rows) == 2:
            row_label = f"第 {rows[0] + 1} 行和第 {rows[1] + 1} 行"
        else:
            row_label = "第 " + "、".join(str(index + 1) for index in rows) + " 行"
        values = "，".join(
            f"第 {index + 1} 行为 {_temperature(plan.boundaries[index].temperature_k)}"
            for index in rows
        )
        fields = [
            field
            for index in rows
            for field in (
                f"boundaries.{index}.selector",
                f"boundaries.{index}.temperature_k",
            )
        ]
        _record_issue(
            errors,
            warnings,
            issues,
            code="duplicate_fixed_temperature_region",
            severity="error",
            message=f"固定温度{row_label}重复指向 {target_label}：{values}。",
            suggestion="删除重复行；如果原本要约束另一个区域，请修改其中一行的区域。",
            fields=fields,
        )
    if is_box and (len(plan.boundaries) != 2 or any(b.region_id for b in plan.boundaries)
                   or first.selector.axis != second.selector.axis or first.selector.side == second.selector.side):
        errors.append("解析长方体需要在同一坐标轴的两个相对表面设置固定温度。")
    condition_names = {
        "fixed_temperature": "固定温度",
        "convection": "对流",
        "heat_flux": "热流",
        "radiation": "辐射",
    }
    for collection_name, collection in (
        ("boundaries", plan.boundaries),
        ("surface_conditions", plan.surface_conditions),
    ):
        for index, boundary in enumerate(collection):
            if not boundary.region_id:
                continue
            region = regions.get(boundary.region_id)
            condition_name = condition_names[boundary.kind]
            field = f"{collection_name}.{index}.region_id"
            if region is None:
                _record_issue(
                    errors,
                    warnings,
                    issues,
                    code="boundary_region_not_found",
                    severity="error",
                    message=(
                        f"{condition_name}第 {index + 1} 行引用的区域 "
                        f"{boundary.region_id!r} 不存在。"
                    ),
                    suggestion="重新选择当前工件中支持该边界条件的区域。",
                    fields=[field],
                )
            elif boundary.kind not in region.supported_condition_kinds:
                supported = "、".join(
                    condition_names.get(kind, kind) for kind in region.supported_condition_kinds
                ) or "无"
                _record_issue(
                    errors,
                    warnings,
                    issues,
                    code="boundary_condition_not_supported",
                    severity="error",
                    message=(
                        f"{condition_name}第 {index + 1} 行选择了区域“{region.name}”"
                        f"（{region.region_id}），该区域不支持{condition_name}；"
                        f"当前支持：{supported}。"
                    ),
                    suggestion="选择支持该条件的区域，或删除这行不适用的边界条件。",
                    fields=[field],
                )
    if is_box and plan.surface_conditions:
        errors.append("解析长方体仅支持两端定温验证；区域热流、对流和辐射需要 STL 数值网格。")
    if plan.contacts:
        if not is_stl:
            errors.append("组件热接触需要 STL 数值网格。")
        if is_stl:
            component_ids = {component.component_id for component in workpiece.components}
            region_by_id = {region.region_id: region for region in workpiece.regions}
            component_by_id = {
                component.component_id: component for component in workpiece.components
            }
            pairs: dict[tuple[str, str], int] = {}
            for index, contact in enumerate(plan.contacts):
                missing_component_fields = [
                    ("source_component_id", "源", contact.source_component_id),
                    ("target_component_id", "目标", contact.target_component_id),
                ]
                missing_component_fields = [
                    item for item in missing_component_fields if item[2] not in component_ids
                ]
                if missing_component_fields:
                    missing_values = "、".join(
                        f"{side}组件 {component_id!r}"
                        for _, side, component_id in missing_component_fields
                    )
                    _record_issue(
                        errors,
                        warnings,
                        issues,
                        code="contact_component_not_found",
                        severity="error",
                        message=f"热接触第 {index + 1} 行引用了不存在的{missing_values}。",
                        suggestion="重新选择当前工件中的源组件和目标组件。",
                        fields=[
                            f"contacts.{index}.{field}"
                            for field, _, _ in missing_component_fields
                        ],
                    )
                if contact.source_component_id == contact.target_component_id:
                    component = component_by_id.get(contact.source_component_id)
                    component_label = (
                        f"组件“{component.name}”（{contact.source_component_id}）"
                        if component is not None
                        else f"组件 {contact.source_component_id!r}"
                    )
                    _record_issue(
                        errors,
                        warnings,
                        issues,
                        code="contact_same_component",
                        severity="error",
                        message=(
                            f"热接触第 {index + 1} 行的源组件和目标组件都是{component_label}；"
                            "热接触的两个组件必须不同。"
                        ),
                        suggestion="将源组件或目标组件改为实际接触的另一个组件。",
                        fields=[
                            f"contacts.{index}.source_component_id",
                            f"contacts.{index}.target_component_id",
                        ],
                    )
                pair = tuple(sorted((contact.source_component_id, contact.target_component_id)))
                if pair in pairs:
                    first_index = pairs[pair]
                    first_component = component_by_id.get(pair[0])
                    second_component = component_by_id.get(pair[1])
                    first_label = first_component.name if first_component is not None else pair[0]
                    second_label = second_component.name if second_component is not None else pair[1]
                    _record_issue(
                        errors,
                        warnings,
                        issues,
                        code="duplicate_contact_pair",
                        severity="error",
                        message=(
                            f"热接触第 {first_index + 1} 行和第 {index + 1} 行重复定义了"
                            f"同一对组件“{first_label}”与“{second_label}”。"
                        ),
                        suggestion="删除重复行；若两行表示不同接触，请选择正确的组件对。",
                        fields=[
                            f"contacts.{first_index}.source_component_id",
                            f"contacts.{first_index}.target_component_id",
                            f"contacts.{index}.source_component_id",
                            f"contacts.{index}.target_component_id",
                        ],
                    )
                else:
                    pairs[pair] = index
                for side, side_name, component_id, region_id in (
                    (
                        "source",
                        "源",
                        contact.source_component_id,
                        contact.source_region_id,
                    ),
                    (
                        "target",
                        "目标",
                        contact.target_component_id,
                        contact.target_region_id,
                    ),
                ):
                    region = region_by_id.get(region_id)
                    if region is None:
                        _record_issue(
                            errors,
                            warnings,
                            issues,
                            code="contact_region_not_found",
                            severity="error",
                            message=(
                                f"热接触第 {index + 1} 行引用的{side_name}区域 "
                                f"{region_id!r} 不存在。"
                            ),
                            suggestion=f"重新选择属于{side_name}组件的接触表面区域。",
                            fields=[f"contacts.{index}.{side}_region_id"],
                        )
                    elif component_id not in region.component_ids:
                        component = component_by_id.get(component_id)
                        component_label = (
                            f"组件“{component.name}”（{component_id}）"
                            if component is not None
                            else f"组件 {component_id!r}"
                        )
                        _record_issue(
                            errors,
                            warnings,
                            issues,
                            code="contact_region_wrong_component",
                            severity="error",
                            message=(
                                f"热接触第 {index + 1} 行的{side_name}区域“{region.name}”"
                                f"（{region_id}）不属于对应的{component_label}。"
                            ),
                            suggestion=f"为{side_name}组件选择其自身的接触表面区域。",
                            fields=[
                                f"contacts.{index}.{side}_component_id",
                                f"contacts.{index}.{side}_region_id",
                            ],
                        )
                    elif region.kind not in {"component_surface", "surface_patch"}:
                        _record_issue(
                            errors,
                            warnings,
                            issues,
                            code="contact_region_wrong_kind",
                            severity="error",
                            message=(
                                f"热接触第 {index + 1} 行的{side_name}区域“{region.name}”"
                                f"（{region_id}）类型为 {region.kind!r}；热接触区域必须是"
                                "组件表面或手工表面选区。"
                            ),
                            suggestion=f"为{side_name}组件选择组件表面或手工表面选区。",
                            fields=[f"contacts.{index}.{side}_region_id"],
                        )
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
        _record_issue(
            errors,
            warnings,
            issues,
            code="equal_fixed_temperatures",
            severity="error",
            message=(
                "固定温度第 1 行和第 2 行均为 "
                f"{_temperature(first.temperature_k)}；解析长方体的两个固定温度必须不同。"
            ),
            suggestion="修改其中一个温度，以设置明确的两端温差。",
            fields=["boundaries.0.temperature_k", "boundaries.1.temperature_k"],
        )

    if (is_voxel or is_tetra) and source_active:
        bbox = workpiece.geometry.summary.get("bbox")
        if is_box:
            bbox = [0, 0, 0, *workpiece.dimensions_mm.as_tuple()]
        source_prefixes = (
            [f"heat_sources.{index}" for index in range(len(heat_sources))]
            if plan.heat_sources
            else ["heat_source"]
        )
        if not isinstance(bbox, list) or len(bbox) != 6:
            _record_issue(
                errors,
                warnings,
                issues,
                code="heat_source_bbox_missing",
                severity="error",
                message="工件缺少三维包围盒，无法映射热源位置。",
                suggestion="返回几何步骤重新检查工件，确认几何解析成功并包含三维包围盒。",
                fields=[f"{prefix}.center_mm" for prefix in source_prefixes],
            )
        source_ids = [source.source_id for source in heat_sources if source.source_id]
        if len(source_ids) != len(set(source_ids)):
            errors.append("多个热源的 ID 不能重复。")
        for index, source in enumerate(heat_sources, start=1):
            label = source.name or f"热源 {index}"
            prefix = source_prefixes[index - 1]
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
                    _record_issue(
                        errors,
                        warnings,
                        issues,
                        code="heat_source_center_outside_bbox",
                        severity="warning",
                        message=(
                            f"第 {index} 行热源“{label}”的中心超出工件包围盒的 "
                            f"{', '.join(outside)} 轴；求解时会映射到最近的可用体素。"
                        ),
                        suggestion="检查热源中心坐标和工件长度单位；需要精确放置时请将中心移入工件。",
                        fields=[f"{prefix}.center_mm"],
                    )
            if source.shape == "line":
                if source.end_mm is None:
                    _record_issue(
                        errors,
                        warnings,
                        issues,
                        code="line_heat_source_missing_endpoint",
                        severity="error",
                        message=f"第 {index} 行热源“{label}”是线热源，但没有终点。",
                        suggestion="填写线热源终点，并确保它与起点不同。",
                        fields=[f"{prefix}.end_mm"],
                    )
                elif all(
                    math.isclose(first, second, rel_tol=0.0, abs_tol=1e-9)
                    for first, second in zip(
                        source.center_mm.as_tuple(), source.end_mm.as_tuple(), strict=True
                    )
                ):
                    _record_issue(
                        errors,
                        warnings,
                        issues,
                        code="line_heat_source_zero_length",
                        severity="error",
                        message=(
                            f"第 {index} 行热源“{label}”的起点和终点均为 "
                            f"({_number(source.center_mm.x)}, {_number(source.center_mm.y)}, "
                            f"{_number(source.center_mm.z)}) mm，线段长度为零。"
                        ),
                        suggestion="修改终点或起点，使两点不重合。",
                        fields=[f"{prefix}.center_mm", f"{prefix}.end_mm"],
                    )
            if source.shape == "surface":
                missing_surface_fields: list[tuple[str, str]] = []
                if source.surface_normal_axis is None:
                    missing_surface_fields.append(("surface_normal_axis", "法向轴"))
                if source.surface_width_mm is None:
                    missing_surface_fields.append(("surface_width_mm", "宽度"))
                if source.surface_height_mm is None:
                    missing_surface_fields.append(("surface_height_mm", "高度"))
                if source.surface_thickness_mm is None:
                    missing_surface_fields.append(("surface_thickness_mm", "映射厚度"))
                if missing_surface_fields:
                    labels = "、".join(label for _, label in missing_surface_fields)
                    _record_issue(
                        errors,
                        warnings,
                        issues,
                        code="surface_heat_source_missing_geometry",
                        severity="error",
                        message=f"第 {index} 行热源“{label}”是面热源，但缺少{labels}。",
                        suggestion="补全矩形面热源的方向和几何尺寸。",
                        fields=[f"{prefix}.{field}" for field, _ in missing_surface_fields],
                    )
            if source.shape == "volume":
                volume_fields = (
                    ("volume_width_mm", source.volume_width_mm, "X 向尺寸"),
                    ("volume_height_mm", source.volume_height_mm, "Y 向尺寸"),
                    ("volume_depth_mm", source.volume_depth_mm, "Z 向尺寸"),
                )
                missing_volume_fields = [item for item in volume_fields if item[1] is None]
                if missing_volume_fields:
                    labels = "、".join(label for _, _, label in missing_volume_fields)
                    _record_issue(
                        errors,
                        warnings,
                        issues,
                        code="volume_heat_source_missing_geometry",
                        severity="error",
                        message=f"第 {index} 行热源“{label}”是体热源，但缺少{labels}。",
                        suggestion="补全体热源的 X、Y、Z 三个方向尺寸。",
                        fields=[f"{prefix}.{field}" for field, _, _ in missing_volume_fields],
                    )

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
        if requested > plan.mesh.max_axis_intervals and not is_tetra:
            _record_issue(
                errors,
                warnings,
                issues,
                code="mesh_axis_interval_limited",
                severity="warning",
                message=(
                    f"{axis.upper()} 轴按目标单元尺寸需要 {requested} 个区间，"
                    f"已限制为 {plan.mesh.max_axis_intervals} 个。"
                ),
                suggestion="需要保留目标分辨率时提高单轴区间上限；否则检查实际有效网格尺寸。",
                fields=["mesh.target_element_size_mm", "mesh.max_axis_intervals"],
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
    elif is_tetra:
        # Estimate only; the mesher enforces limits against the actual output.
        triangles = sum(c.triangle_count for c in workpiece.components)
        cells = max(3 * triangles, math.ceil(abs(float(workpiece.geometry.summary.get("volume", 0))) * 6 / effective_pitch**3))
        points = max(4, sum(c.vertex_count for c in workpiece.components))
        derived.update({"effective_pitch_mm": effective_pitch, "grid_points": points, "cells": cells,
                        "mesh_estimate_only": True})
    else:
        points = math.prod(value + 1 for value in intervals.values())
        cells = math.prod(intervals.values())
        derived.update({"intervals": intervals, "grid_points": points, "cells": cells})
    if points > MAX_GRID_POINTS:
        _record_issue(
            errors,
            warnings,
            issues,
            code="mesh_point_limit_exceeded",
            severity="error",
            message=f"请求的网格包含 {points} 个点，策略上限为 {MAX_GRID_POINTS}。",
            suggestion="增大目标单元尺寸或降低单轴区间上限。",
            fields=["mesh.target_element_size_mm", "mesh.max_axis_intervals"],
        )
    if cells > MAX_TOTAL_CELLS:
        _record_issue(
            errors,
            warnings,
            issues,
            code="mesh_cell_limit_exceeded",
            severity="error",
            message=f"请求的网格包含 {cells} 个单元，策略上限为 {MAX_TOTAL_CELLS}。",
            suggestion="增大目标单元尺寸或降低单轴区间上限。",
            fields=["mesh.target_element_size_mm", "mesh.max_axis_intervals"],
        )
    if time_steps * cells > MAX_TRANSIENT_CELL_STEPS:
        _record_issue(
            errors,
            warnings,
            issues,
            code="transient_resource_limit_exceeded",
            severity="error",
            message=(
                f"瞬态计算需要约 {time_steps * cells} 个单元步，"
                f"超过资源上限 {MAX_TRANSIENT_CELL_STEPS}。"
            ),
            suggestion="增大网格尺寸，或调整持续时间和时间步长以减少积分区间。",
            fields=[
                "mesh.target_element_size_mm",
                "mesh.max_axis_intervals",
                "duration_s",
                "time_step_s",
            ],
        )

    if plan.confidence < 0.35:
        warnings.append("GPT 置信度较低，建模假设需要工程人员复核。")

    temperature_inputs: list[tuple[float, str]] = [
        (item.temperature_k, f"boundaries.{index}.temperature_k")
        for index, item in enumerate(plan.boundaries)
    ]
    temperature_inputs.extend(
        (condition.ambient_temperature_k, f"surface_conditions.{index}.ambient_temperature_k")
        for index, condition in enumerate(plan.surface_conditions)
        if condition.kind == "convection"
    )
    temperature_inputs.extend(
        (condition.radiation_temperature_k, f"surface_conditions.{index}.radiation_temperature_k")
        for index, condition in enumerate(plan.surface_conditions)
        if condition.kind == "radiation"
    )
    if transient and plan.initial_temperature_k is not None:
        temperature_inputs.append((plan.initial_temperature_k, "initial_temperature_k"))
    if plan.convection is not None and plan.global_convection_enabled:
        temperature_inputs.append(
            (plan.convection.ambient_temperature_k, "convection.ambient_temperature_k")
        )
    material_inputs = [
        (plan.material, "material"),
        *(
            (assignment.material, f"component_materials.{index}.material")
            for index, assignment in enumerate(plan.component_materials)
        ),
    ]
    grouped_materials: dict[
        tuple[str, str | None, float | None, float | None],
        tuple[object, list[str]],
    ] = {}
    for material, field_prefix in material_inputs:
        identity = (
            material.name,
            material.source_version,
            material.valid_temperature_min_k,
            material.valid_temperature_max_k,
        )
        if identity not in grouped_materials:
            grouped_materials[identity] = (material, [])
        grouped_materials[identity][1].append(field_prefix)
    for material, field_prefixes in grouped_materials.values():
        if material.valid_temperature_max_k is not None:
            offenders = [
                (value, path)
                for value, path in temperature_inputs
                if value > material.valid_temperature_max_k
            ]
            if offenders:
                peak_temperature = max(value for value, _ in offenders)
                _record_issue(
                    errors,
                    warnings,
                    issues,
                    code="material_temperature_above_range",
                    severity="warning",
                    message=(
                        f"材料“{material.name}”的已知适用温度上限为 "
                        f"{_temperature(material.valid_temperature_max_k)}，"
                        f"但当前条件最高达到 {_temperature(peak_temperature)}。"
                    ),
                    suggestion="核对材料温域数据；若数据无误，请降低相关温度或选择适用温域更高的材料。",
                    fields=[
                        *(f"{prefix}.valid_temperature_max_k" for prefix in field_prefixes),
                        *(path for _, path in offenders),
                    ],
                )
        if material.valid_temperature_min_k is not None:
            offenders = [
                (value, path)
                for value, path in temperature_inputs
                if value < material.valid_temperature_min_k
            ]
            if offenders:
                minimum_temperature = min(value for value, _ in offenders)
                _record_issue(
                    errors,
                    warnings,
                    issues,
                    code="material_temperature_below_range",
                    severity="warning",
                    message=(
                        f"材料“{material.name}”的已知适用温度下限为 "
                        f"{_temperature(material.valid_temperature_min_k)}，"
                        f"但当前条件最低达到 {_temperature(minimum_temperature)}。"
                    ),
                    suggestion="核对材料温域数据；若数据无误，请提高相关温度或选择适用温域更低的材料。",
                    fields=[
                        *(f"{prefix}.valid_temperature_min_k" for prefix in field_prefixes),
                        *(path for _, path in offenders),
                    ],
                )

    criterion_units = {
        "max_temperature": ("最高温度", "K"),
        "min_temperature": ("最低温度", "K"),
        "energy_balance_error": ("能量相对误差", "1"),
    }
    for index, criterion in enumerate(plan.criteria):
        metric_label, expected_unit = criterion_units[criterion.metric]
        if criterion.target.unit != expected_unit:
            unit_description = "无量纲单位 1" if expected_unit == "1" else expected_unit
            suggestion = (
                "先清空最高温度判据并保存，等待界面显示“草案已保存”；"
                "将目标值换算为 K 后重新填写最高温度判据并再次保存。"
                "系统不会静默换算或修改目标值。"
                if criterion.metric == "max_temperature"
                else f"使用单位 {expected_unit!r} 重新创建该判据，并核对目标值。"
            )
            _record_issue(
                errors,
                warnings,
                issues,
                code="invalid_criterion_unit",
                severity="error",
                message=(
                    f"第 {index + 1} 行{metric_label}判据使用了单位 "
                    f"{criterion.target.unit!r}，必须使用{unit_description}。"
                ),
                suggestion=suggestion,
                fields=(
                    [f"criteria.{index}.target.unit"]
                    if criterion.metric == "max_temperature"
                    else []
                ),
            )

    if any(not _valid_box_selector(boundary.selector) for boundary in plan.boundaries):
        errors.append("边界选择器不是有效的长方体表面。")

    return PolicyReport(
        accepted=not errors,
        errors=errors,
        warnings=warnings,
        issues=issues,
        derived=derived,
    )


def _valid_box_selector(selector: FaceSelector) -> bool:
    return selector in set(FaceSelector)
