from __future__ import annotations

from thermoflow.materials import list_materials
from thermoflow.models import (
    CadFormat,
    ComponentMaterialAssignment,
    DimensionsMM,
    FaceSelector,
    GeometryComponent,
    GeometryInspection,
    LengthUnit,
    WorkpieceKind,
    WorkpieceRecord,
)
from thermoflow.planner import DeterministicPlanner
from thermoflow.policy import validate_plan

from .helpers import box_workpiece


def test_deterministic_plan_passes_policy() -> None:
    workpiece = box_workpiece()
    plan = DeterministicPlanner().plan(workpiece).plan

    report = validate_plan(workpiece, plan)

    assert report.accepted
    assert report.errors == []
    assert report.derived == {
        "intervals": {"x": 12, "y": 3, "z": 2},
        "grid_points": 156,
        "cells": 72,
    }


def test_policy_rejects_boundaries_that_are_not_opposite() -> None:
    workpiece = box_workpiece()
    plan = DeterministicPlanner().plan(workpiece).plan
    bad_boundaries = [
        plan.boundaries[0],
        plan.boundaries[1].model_copy(update={"selector": FaceSelector.Y_MAX}),
    ]

    report = validate_plan(workpiece, plan.model_copy(update={"boundaries": bad_boundaries}))

    assert not report.accepted
    assert "相对表面" in " ".join(report.errors)


def test_catalog_material_id_cannot_mask_modified_properties() -> None:
    component = GeometryComponent(
        component_id="component-123456789abc",
        name="壳体",
        triangle_count=12,
        vertex_count=8,
        bbox_source=[0, 0, 0, 10, 8, 4],
        area_source2=304,
        watertight=True,
    )
    workpiece = WorkpieceRecord(
        workpiece_id="wp-stl",
        kind=WorkpieceKind.CAD_FILE,
        name="catalog-check",
        content_sha256="1" * 64,
        dimensions_mm=DimensionsMM(x=10, y=8, z=4),
        cad_format=CadFormat.STL,
        original_filename="catalog-check.stl",
        stored_filename="source_mm.stl",
        geometry=GeometryInspection(
            available=True,
            engine="trimesh-stl",
            summary={"bbox": [0, 0, 0, 10, 8, 4]},
        ),
        source_dimensions=DimensionsMM(x=10, y=8, z=4),
        length_unit=LengthUnit.MILLIMETER,
        unit_confirmed=True,
        components=[component],
    )
    plan = DeterministicPlanner().plan(workpiece).plan
    catalog_entry = list_materials()[0]
    assignment = ComponentMaterialAssignment(
        component_id=component.component_id,
        material_id=catalog_entry.material_id,
        material=catalog_entry.material,
    )
    valid_plan = plan.model_copy(update={"component_materials": [assignment]})
    assert validate_plan(workpiece, valid_plan).accepted

    tampered = assignment.model_copy(
        update={
            "material": assignment.material.model_copy(
                update={
                    "thermal_conductivity_w_m_k": (
                        assignment.material.thermal_conductivity_w_m_k + 1
                    )
                }
            )
        }
    )
    report = validate_plan(
        workpiece,
        plan.model_copy(update={"component_materials": [tampered]}),
    )

    assert not report.accepted
    assert "用户覆盖值必须移除数据库 ID" in " ".join(report.errors)
