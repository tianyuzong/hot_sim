from __future__ import annotations

import pytest

from thermoflow.models import (
    CadFormat,
    DimensionsMM,
    EngineeringCriterion,
    FaceSelector,
    FixedTemperatureBoundary,
    GeometryComponent,
    GeometryInspection,
    GeometryRegion,
    LengthUnit,
    Point3DMM,
    PolicyIssue,
    PolicyReport,
    Quantity,
    SurfaceConvectionBoundary,
    ThermalContactCondition,
    VolumetricHeatSource,
    WorkpieceKind,
    WorkpieceRecord,
)
from thermoflow.planner import DeterministicPlanner
from thermoflow.policy import validate_plan

from .helpers import box_workpiece


def _stl_workpiece() -> WorkpieceRecord:
    component = GeometryComponent(
        component_id="component-123456789abc",
        name="壳体",
        triangle_count=12,
        vertex_count=8,
        bbox_source=[0, 0, 0, 10, 8, 4],
        area_source2=304,
        watertight=True,
    )
    return WorkpieceRecord(
        workpiece_id="wp-diagnostics",
        kind=WorkpieceKind.CAD_FILE,
        name="诊断工件",
        content_sha256="a" * 64,
        dimensions_mm=DimensionsMM(x=10, y=8, z=4),
        cad_format=CadFormat.STL,
        geometry=GeometryInspection(
            available=True,
            engine="test",
            summary={"bbox": [0, 0, 0, 10, 8, 4]},
        ),
        source_dimensions=DimensionsMM(x=10, y=8, z=4),
        length_unit=LengthUnit.MILLIMETER,
        unit_confirmed=True,
        components=[component],
        regions=[
            GeometryRegion(
                region_id="region-123456789abc",
                name="右侧散热面",
                kind="bounding_plane",
                selector="face.xmax",
                component_ids=[component.component_id],
                supported_condition_kinds=["fixed_temperature", "convection"],
            )
        ],
    )


def _contact_workpiece() -> WorkpieceRecord:
    workpiece = _stl_workpiece()
    first = workpiece.components[0]
    second = GeometryComponent(
        component_id="component-fedcba987654",
        name="散热器",
        triangle_count=12,
        vertex_count=8,
        bbox_source=[10, 0, 0, 20, 8, 4],
        area_source2=304,
        watertight=True,
    )
    regions = [
        *workpiece.regions,
        GeometryRegion(
            region_id="region-aaaaaaaaaaaa",
            name="壳体接触面",
            kind="component_surface",
            selector="component.component-123456789abc.surface",
            component_ids=[first.component_id],
            supported_condition_kinds=["thermal_contact"],
        ),
        GeometryRegion(
            region_id="region-bbbbbbbbbbbb",
            name="散热器接触面",
            kind="surface_patch",
            selector="patch.heatsink",
            component_ids=[second.component_id],
            supported_condition_kinds=["thermal_contact"],
        ),
        GeometryRegion(
            region_id="region-cccccccccccc",
            name="壳体包围盒面",
            kind="bounding_plane",
            selector="face.xmin",
            component_ids=[first.component_id],
            supported_condition_kinds=["thermal_contact"],
        ),
    ]
    return workpiece.model_copy(update={"components": [first, second], "regions": regions})


def _contact(**updates: str) -> ThermalContactCondition:
    values = {
        "source_component_id": "component-123456789abc",
        "target_component_id": "component-fedcba987654",
        "source_region_id": "region-aaaaaaaaaaaa",
        "target_region_id": "region-bbbbbbbbbbbb",
        "contact_resistance_m2_k_w": 0.001,
    }
    values.update(updates)
    return ThermalContactCondition(**values)


def test_policy_report_accepts_old_payload_without_structured_issues() -> None:
    report = PolicyReport.model_validate({"accepted": False, "errors": ["旧错误"]})

    assert report.issues == []
    assert PolicyIssue(
        code="example",
        severity="warning",
        message="可定位警告",
    ).fields == []
    with pytest.raises(ValueError):
        PolicyIssue(code="example", severity="info", message="不支持的级别")


def test_duplicate_fixed_temperature_diagnostic_has_rows_values_and_fields() -> None:
    workpiece = box_workpiece()
    plan = DeterministicPlanner().plan(workpiece).plan.model_copy(
        update={
            "boundaries": [
                FixedTemperatureBoundary(selector=FaceSelector.X_MIN, temperature_k=7),
                FixedTemperatureBoundary(selector=FaceSelector.X_MIN, temperature_k=4),
            ]
        }
    )

    report = validate_plan(workpiece, plan)

    issue = next(item for item in report.issues if item.code == "duplicate_fixed_temperature_region")
    assert issue.model_dump() == {
        "code": "duplicate_fixed_temperature_region",
        "severity": "error",
        "message": (
            "固定温度第 1 行和第 2 行重复指向 X 最小面（face.xmin）："
            "第 1 行为 7 K（-266.15 ℃），第 2 行为 4 K（-269.15 ℃）。"
        ),
        "suggestion": "删除重复行；如果原本要约束另一个区域，请修改其中一行的区域。",
        "fields": [
            "boundaries.0.selector",
            "boundaries.0.temperature_k",
            "boundaries.1.selector",
            "boundaries.1.temperature_k",
        ],
    }
    assert issue.message in report.errors
    assert "同一个定温区域不能重复施加条件。" not in report.errors


def test_duplicate_diagnostic_resolves_bounding_plane_region_alias() -> None:
    region = GeometryRegion(
        region_id="region-abcdef123456",
        name="冷端左侧面",
        kind="bounding_plane",
        selector="face.xmin",
        supported_condition_kinds=["fixed_temperature"],
    )
    workpiece = box_workpiece().model_copy(update={"regions": [region]})
    plan = DeterministicPlanner().plan(workpiece).plan.model_copy(
        update={
            "boundaries": [
                FixedTemperatureBoundary(region_id=region.region_id, temperature_k=280),
                FixedTemperatureBoundary(selector=FaceSelector.X_MIN, temperature_k=290),
            ]
        }
    )

    issue = next(
        item
        for item in validate_plan(workpiece, plan).issues
        if item.code == "duplicate_fixed_temperature_region"
    )

    assert "区域“冷端左侧面”（face.xmin）" in issue.message
    assert issue.fields[:2] == ["boundaries.0.selector", "boundaries.0.temperature_k"]


@pytest.mark.parametrize(
    ("duration_s", "time_step_s", "code", "message_fragment", "suggestion_fragment"),
    [
        (10.0, 11.0, "time_step_exceeds_duration", "持续时间 10 s，时间步长 11 s", "不超过 10 s"),
        (10.0, 0.01, "too_many_time_steps", "需要 1000 个积分区间", "至少 0.05 s"),
    ],
)
def test_time_step_conflicts_report_actual_values_and_both_fields(
    duration_s: float,
    time_step_s: float,
    code: str,
    message_fragment: str,
    suggestion_fragment: str,
) -> None:
    workpiece = _stl_workpiece()
    plan = DeterministicPlanner().plan(workpiece).plan.model_copy(
        update={
            "analysis_type": "transient_conduction",
            "analyses": ["transient_thermal"],
            "initial_temperature_k": 300,
            "duration_s": duration_s,
            "time_step_s": time_step_s,
        }
    )

    report = validate_plan(workpiece, plan)

    issue = next(item for item in report.issues if item.code == code)
    assert message_fragment in issue.message
    assert suggestion_fragment in issue.suggestion
    assert issue.fields == ["duration_s", "time_step_s"]
    assert issue.message in report.errors


def test_missing_line_endpoint_identifies_heat_source_row_and_endpoint_field() -> None:
    workpiece = _stl_workpiece()
    source = VolumetricHeatSource(
        source_id="laser-line",
        name="激光线",
        shape="line",
        center_mm=Point3DMM(x=2, y=2, z=2),
        total_power_w=10,
        radius_mm=0.5,
    )
    plan = DeterministicPlanner().plan(workpiece).plan.model_copy(
        update={"heat_source": None, "heat_sources": [source], "heat_source_enabled": True}
    )

    report = validate_plan(workpiece, plan)

    issue = next(item for item in report.issues if item.code == "line_heat_source_missing_endpoint")
    assert issue.message == "第 1 行热源“激光线”是线热源，但没有终点。"
    assert issue.suggestion == "填写线热源终点，并确保它与起点不同。"
    assert issue.fields == ["heat_sources.0.end_mm"]
    assert issue.message in report.errors


def test_material_temperature_warning_links_limit_and_every_offending_temperature() -> None:
    workpiece = _stl_workpiece()
    plan = DeterministicPlanner().plan(workpiece).plan
    material = plan.component_materials[0].material.model_copy(
        update={"name": "测试涂层", "valid_temperature_max_k": 400}
    )
    assignment = plan.component_materials[0].model_copy(update={"material": material})
    surface_condition = SurfaceConvectionBoundary(
        region_id="region-123456789abc",
        ambient_temperature_k=500,
        heat_transfer_coefficient_w_m2_k=12,
    )
    plan = plan.model_copy(
        update={
            "boundaries": [
                FixedTemperatureBoundary(selector=FaceSelector.X_MIN, temperature_k=450)
            ],
            "surface_conditions": [surface_condition],
            "component_materials": [assignment],
        }
    )

    report = validate_plan(workpiece, plan)

    issue = next(
        item
        for item in report.issues
        if item.code == "material_temperature_above_range" and "测试涂层" in item.message
    )
    assert "测试涂层" in issue.message
    assert "400 K（126.85 ℃）" in issue.message
    assert "500 K（226.85 ℃）" in issue.message
    assert issue.fields == [
        "component_materials.0.material.valid_temperature_max_k",
        "boundaries.0.temperature_k",
        "surface_conditions.0.ambient_temperature_k",
    ]
    assert issue.message in report.warnings


def test_mesh_interval_limit_warning_links_mesh_controls() -> None:
    workpiece = box_workpiece()
    plan = DeterministicPlanner().plan(workpiece).plan
    plan = plan.model_copy(
        update={
            "mesh": plan.mesh.model_copy(
                update={"target_element_size_mm": 0.1, "max_axis_intervals": 10}
            )
        }
    )

    report = validate_plan(workpiece, plan)

    issue = next(item for item in report.issues if item.code == "mesh_axis_interval_limited")
    assert issue.severity == "warning"
    assert issue.fields == ["mesh.target_element_size_mm", "mesh.max_axis_intervals"]
    assert issue.message in report.warnings


@pytest.mark.parametrize(
    ("boundary_update", "code", "field"),
    [
        (
            {
                "boundaries": [
                    FixedTemperatureBoundary(
                        region_id="region-000000000000", temperature_k=310
                    )
                ]
            },
            "boundary_region_not_found",
            "boundaries.0.region_id",
        ),
        (
            {
                "surface_conditions": [
                    SurfaceConvectionBoundary(
                        region_id="region-aaaaaaaaaaaa",
                        ambient_temperature_k=300,
                        heat_transfer_coefficient_w_m2_k=10,
                    )
                ]
            },
            "boundary_condition_not_supported",
            "surface_conditions.0.region_id",
        ),
    ],
)
def test_invalid_boundary_region_reports_row_region_and_field(
    boundary_update: dict[str, object], code: str, field: str
) -> None:
    workpiece = _contact_workpiece()
    plan = DeterministicPlanner().plan(workpiece).plan.model_copy(update=boundary_update)

    report = validate_plan(workpiece, plan)

    issue = next(item for item in report.issues if item.code == code)
    assert "第 1 行" in issue.message
    assert issue.fields == [field]
    assert issue.suggestion
    assert issue.message in report.errors


@pytest.mark.parametrize(
    ("contacts", "code", "fields", "message_fragment"),
    [
        (
            [_contact(target_component_id="component-000000000000")],
            "contact_component_not_found",
            ["contacts.0.target_component_id"],
            "component-000000000000",
        ),
        (
            [
                _contact(
                    target_component_id="component-123456789abc",
                    target_region_id="region-aaaaaaaaaaaa",
                )
            ],
            "contact_same_component",
            ["contacts.0.source_component_id", "contacts.0.target_component_id"],
            "component-123456789abc",
        ),
        (
            [_contact(), _contact()],
            "duplicate_contact_pair",
            [
                "contacts.0.source_component_id",
                "contacts.0.target_component_id",
                "contacts.1.source_component_id",
                "contacts.1.target_component_id",
            ],
            "第 1 行和第 2 行",
        ),
        (
            [_contact(source_region_id="region-000000000000")],
            "contact_region_not_found",
            ["contacts.0.source_region_id"],
            "region-000000000000",
        ),
        (
            [_contact(source_region_id="region-bbbbbbbbbbbb")],
            "contact_region_wrong_component",
            ["contacts.0.source_component_id", "contacts.0.source_region_id"],
            "壳体",
        ),
        (
            [_contact(source_region_id="region-cccccccccccc")],
            "contact_region_wrong_kind",
            ["contacts.0.source_region_id"],
            "壳体包围盒面",
        ),
    ],
)
def test_contact_conflicts_report_row_values_suggestion_and_fields(
    contacts: list[ThermalContactCondition],
    code: str,
    fields: list[str],
    message_fragment: str,
) -> None:
    workpiece = _contact_workpiece()
    plan = DeterministicPlanner().plan(workpiece).plan.model_copy(update={"contacts": contacts})

    report = validate_plan(workpiece, plan)

    issue = next(item for item in report.issues if item.code == code)
    assert "第 " in issue.message
    assert message_fragment in issue.message
    assert issue.fields == fields
    assert issue.suggestion
    assert issue.message in report.errors


def test_equal_analytic_box_temperatures_report_both_rows_and_temperature_fields() -> None:
    workpiece = box_workpiece()
    plan = DeterministicPlanner().plan(workpiece).plan.model_copy(
        update={
            "boundaries": [
                FixedTemperatureBoundary(selector=FaceSelector.X_MIN, temperature_k=300),
                FixedTemperatureBoundary(selector=FaceSelector.X_MAX, temperature_k=300),
            ]
        }
    )

    report = validate_plan(workpiece, plan)

    issue = next(item for item in report.issues if item.code == "equal_fixed_temperatures")
    assert "第 1 行和第 2 行" in issue.message
    assert "300 K（26.85 ℃）" in issue.message
    assert "必须不同" in issue.message
    assert issue.fields == ["boundaries.0.temperature_k", "boundaries.1.temperature_k"]
    assert issue.suggestion
    assert issue.message in report.errors


@pytest.mark.parametrize(
    ("criterion", "expected_unit", "metric_label", "expected_fields"),
    [
        (
            EngineeringCriterion(
                metric="max_temperature",
                operator="less_or_equal",
                target=Quantity(value=100, unit="C"),
            ),
            "K",
            "最高温度",
            ["criteria.0.target.unit"],
        ),
        (
            EngineeringCriterion(
                metric="energy_balance_error",
                operator="less_or_equal",
                target=Quantity(value=0.01, unit="%"),
            ),
            "1",
            "能量相对误差",
            [],
        ),
    ],
)
def test_invalid_criterion_unit_reports_row_actual_unit_and_target_field(
    criterion: EngineeringCriterion,
    expected_unit: str,
    metric_label: str,
    expected_fields: list[str],
) -> None:
    workpiece = box_workpiece()
    plan = DeterministicPlanner().plan(workpiece).plan.model_copy(update={"criteria": [criterion]})

    report = validate_plan(workpiece, plan)

    issue = next(item for item in report.issues if item.code == "invalid_criterion_unit")
    assert f"第 1 行{metric_label}判据" in issue.message
    assert f"{criterion.target.unit!r}" in issue.message
    assert expected_unit in issue.message
    assert issue.fields == expected_fields
    assert issue.suggestion
    if criterion.metric == "max_temperature":
        assert "清空" in issue.suggestion and "保存" in issue.suggestion
    assert issue.message in report.errors
