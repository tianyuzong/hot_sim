"""Build and fingerprint the versioned simulation contract persisted per study."""

from __future__ import annotations

import hashlib
import json

from .models import (
    LengthUnit,
    MaterialSourceSnapshot,
    ProjectRecord,
    Quantity,
    QuantityRange,
    QuantityVector3,
    SimulationComponentSnapshot,
    SimulationConditionsSpec,
    SimulationConvectionCondition,
    SimulationEnvironmentSpec,
    SimulationFixedTemperatureCondition,
    SimulationGeometrySnapshot,
    SimulationHeatSourceCondition,
    SimulationInitialTemperatureCondition,
    SimulationMaterialAssignment,
    SimulationMaterialSnapshot,
    SimulationMaterialsSpec,
    SimulationMeshSpec,
    SimulationPhysicsSpec,
    SimulationPlan,
    SimulationPoint3D,
    SimulationProjectSnapshot,
    SimulationRadiationCondition,
    SimulationRegionSnapshot,
    SimulationScenarioSpec,
    SimulationSolverSpec,
    SimulationSpec,
    SimulationSurfaceFluxCondition,
    SimulationThermalContactCondition,
    ThermalMaterial,
    WorkpieceRecord,
)


def build_simulation_spec(
    *,
    project: ProjectRecord,
    workpiece: WorkpieceRecord,
    plan: SimulationPlan,
    geometry_snapshot_sha256: str,
) -> SimulationSpec:
    """Project the executable thermal plan into the public, unit-bearing contract."""

    if workpiece.dimensions_mm is None:
        raise ValueError("生成仿真规格前必须确认几何实际尺寸")
    region_snapshots = [
        SimulationRegionSnapshot(
            id=region.region_id,
            name=region.name,
            kind=region.kind,
            selector=region.selector,
            component_refs=region.component_ids,
            supported_condition_kinds=region.supported_condition_kinds,
            triangle_ids=region.triangle_ids,
        )
        for region in workpiece.regions
    ]
    component_snapshots = [
        SimulationComponentSnapshot(
            id=component.component_id,
            name=component.name,
            region_refs=[
                region.id
                for region in region_snapshots
                if component.component_id in region.component_refs
            ],
        )
        for component in workpiece.components
    ]
    if not component_snapshots:
        component_snapshots = [
            SimulationComponentSnapshot(
                id="geometry.whole",
                name=workpiece.name,
                region_refs=[region.id for region in region_snapshots],
            )
        ]
    component_ids = [component.id for component in component_snapshots]

    material_assignments = [
        SimulationMaterialAssignment(
            component_id=assignment.component_id,
            material_id=assignment.material_id,
            material=_material_snapshot(assignment.material),
        )
        for assignment in plan.component_materials
    ]
    if not material_assignments:
        material_assignments = [
            SimulationMaterialAssignment(
                component_id=component_ids[0],
                material_id=None,
                material=_material_snapshot(plan.material),
            )
        ]

    target_ref_by_selector = {
        region.selector: region.id for region in region_snapshots
    }
    thermal_conditions = [
        SimulationFixedTemperatureCondition(
            id=f"thermal.fixed-temperature.{index}",
            target_refs=[
                boundary.region_id or target_ref_by_selector.get(boundary.selector.value, boundary.selector.value)
            ],
            temperature=_quantity(boundary.temperature_k, "K"),
        )
        for index, boundary in enumerate(plan.boundaries, start=1)
    ]
    heat_sources = plan.heat_sources or ([plan.heat_source] if plan.heat_source is not None else [])
    if heat_sources and plan.heat_source_enabled:
        for index, source in enumerate(heat_sources, start=1):
            thermal_conditions.append(
                SimulationHeatSourceCondition(
                id=source.source_id or f"thermal.heat-source.{index}",
                target_refs=component_ids,
                shape=source.shape,
                placement=source.placement,
                center=_point(source.center_mm.x, source.center_mm.y, source.center_mm.z),
                total_power=_quantity(source.total_power_w, "W"),
                radius=_quantity(source.radius_mm, "mm"),
                embedding_depth=_quantity(source.embedding_depth_mm, "mm"),
                end=(
                    _point(source.end_mm.x, source.end_mm.y, source.end_mm.z)
                    if source.end_mm is not None
                    else None
                ),
                surface_normal_axis=source.surface_normal_axis,
                surface_width=_optional_quantity(source.surface_width_mm, "mm"),
                surface_height=_optional_quantity(source.surface_height_mm, "mm"),
                surface_thickness=_optional_quantity(source.surface_thickness_mm, "mm"),
                volume_width=_optional_quantity(source.volume_width_mm, "mm"),
                volume_height=_optional_quantity(source.volume_height_mm, "mm"),
                volume_depth=_optional_quantity(source.volume_depth_mm, "mm"),
            )
        )
    if plan.convection is not None and plan.global_convection_enabled:
        thermal_conditions.append(
            SimulationConvectionCondition(
                id="thermal.convection.1",
                target_refs=["surface.exposed-unprescribed"],
                ambient_temperature=_quantity(plan.convection.ambient_temperature_k, "K"),
                heat_transfer_coefficient=_quantity(
                    plan.convection.heat_transfer_coefficient_w_m2_k,
                    "W/(m^2*K)",
                ),
            )
        )

    for index, condition in enumerate(plan.surface_conditions):
        common = {"id": f"thermal.surface.{index + 1}", "target_refs": [condition.region_id]}
        if condition.kind == "heat_flux":
            thermal_conditions.append(SimulationSurfaceFluxCondition(
                **common, inward_heat_flux=_quantity(condition.heat_flux_w_m2, "W/m^2"),
            ))
        elif condition.kind == "convection":
            thermal_conditions.append(SimulationConvectionCondition(
                **common, ambient_temperature=_quantity(condition.ambient_temperature_k, "K"),
                heat_transfer_coefficient=_quantity(condition.heat_transfer_coefficient_w_m2_k, "W/(m^2*K)"),
            ))
        else:
            thermal_conditions.append(SimulationRadiationCondition(
                **common, radiation_temperature=_quantity(condition.radiation_temperature_k, "K"),
                emissivity=_quantity(condition.emissivity, "1"), emissivity_source=condition.emissivity_source,
            ))
    contact_conditions = [
        SimulationThermalContactCondition(
            id=f"thermal.contact.{index + 1}",
            source_component_ref=condition.source_component_id,
            target_component_ref=condition.target_component_id,
            source_region_ref=condition.source_region_id,
            target_region_ref=condition.target_region_id,
            contact_resistance=_quantity(condition.contact_resistance_m2_k_w, "m^2*K/W"),
            contact_area=_optional_quantity(condition.contact_area_m2, "m^2"),
            max_gap=_optional_quantity(condition.max_gap_mm, "mm"),
        )
        for index, condition in enumerate(plan.contacts)
    ]
    dimensions = workpiece.dimensions_mm
    return SimulationSpec(
        schema_version="1.4" if plan.contacts else "1.3" if plan.surface_conditions or not plan.boundaries or not plan.heat_source_enabled or not plan.global_convection_enabled
        else "1.2" if any(region.triangle_ids for region in region_snapshots) or any(b.region_id for b in plan.boundaries)
        else "1.1" if plan.analysis_type == "transient_conduction" else "1.0",
        project=SimulationProjectSnapshot(project_id=project.project_id, name=project.name),
        geometry=SimulationGeometrySnapshot(
            asset_id=workpiece.workpiece_id,
            content_sha256=workpiece.content_sha256,
            unit=workpiece.length_unit or LengthUnit.MILLIMETER,
            dimensions=QuantityVector3(
                x=_quantity(dimensions.x, "mm"),
                y=_quantity(dimensions.y, "mm"),
                z=_quantity(dimensions.z, "mm"),
            ),
            components=component_snapshots,
            regions=region_snapshots,
        ),
        materials=SimulationMaterialsSpec(assignments=material_assignments),
        physics=SimulationPhysicsSpec(
            analyses=plan.analyses,
            model=plan.analysis_type,
            unsupported=plan.unsupported_physics,
        ),
        scenario=SimulationScenarioSpec(
            purpose=plan.purpose,
            duration=_optional_quantity(plan.duration_s, "s"),
            environment=SimulationEnvironmentSpec(
                ambient_temperature=(
                    _quantity(plan.convection.ambient_temperature_k, "K")
                    if plan.convection is not None and plan.global_convection_enabled
                    else None
                ),
                medium=None,
            ),
        ),
        conditions=SimulationConditionsSpec(
            thermal=thermal_conditions,
            initial=[SimulationInitialTemperatureCondition(
                target_refs=component_ids,
                temperature=_quantity(plan.initial_temperature_k, "K"),
            )] if plan.initial_temperature_k is not None else [],
            contacts=contact_conditions,
        ),
        mesh=SimulationMeshSpec(
            global_size=_quantity(plan.mesh.target_element_size_mm, "mm"),
            max_axis_intervals=plan.mesh.max_axis_intervals,
            refinement_passes=plan.mesh.refinement_passes,
        ),
        solver=SimulationSolverSpec(
            type=plan.solver.backend,
            relative_tolerance=_quantity(plan.solver.relative_tolerance, "1"),
            max_iterations=plan.solver.max_iterations,
            time_step=_optional_quantity(plan.time_step_s, "s"),
            time_integration="backward_euler" if plan.analysis_type == "transient_conduction" else None,
        ),
        criteria=plan.criteria,
        assumptions=plan.assumptions,
        missing_information=plan.missing_information,
        confirmation=plan.confirmation,
        source_plan_sha256=simulation_plan_sha256(plan),
        geometry_snapshot_sha256=geometry_snapshot_sha256,
    )


def simulation_plan_sha256(plan: SimulationPlan) -> str:
    values = plan.model_dump(mode="json")
    if not values["surface_conditions"]:
        values.pop("surface_conditions")
    if not values["contacts"]:
        values.pop("contacts")
    for key in ("heat_source_enabled", "global_convection_enabled"):
        if values[key]:
            values.pop(key)
    for boundary in values["boundaries"]:
        if boundary["region_id"] is None:
            boundary.pop("region_id")
    # Additive optional fields must not invalidate previously confirmed steady studies.
    for key in ("initial_temperature_k", "duration_s", "time_step_s"):
        if values[key] is None:
            values.pop(key)
    return _model_sha256(values)


def simulation_spec_sha256(spec: SimulationSpec) -> str:
    values = spec.model_dump(mode="json")
    if not values["conditions"]["contacts"]:
        values["conditions"].pop("contacts")
    if spec.schema_version in {"1.0", "1.1"}:
        for region in values["geometry"]["regions"]:
            if not region["triangle_ids"]:
                region.pop("triangle_ids")
    if spec.schema_version == "1.0":
        if values["scenario"]["duration"] is None:
            values["scenario"].pop("duration")
        for key in ("time_step", "time_integration"):
            if values["solver"][key] is None:
                values["solver"].pop(key)
    return _model_sha256(values)


def validate_spec_plan_alignment(spec: SimulationSpec, plan: SimulationPlan) -> None:
    if spec.source_plan_sha256 != simulation_plan_sha256(plan):
        raise ValueError("版本化仿真规格与当前执行方案不一致，请重新确认输入")


def _material_snapshot(material: ThermalMaterial) -> SimulationMaterialSnapshot:
    return SimulationMaterialSnapshot(
        name=material.name,
        thermal_conductivity=_quantity(material.thermal_conductivity_w_m_k, "W/(m*K)"),
        density=_quantity(material.density_kg_m3, "kg/m^3"),
        specific_heat_capacity=_quantity(material.specific_heat_j_kg_k, "J/(kg*K)"),
        emissivity=material.emissivity,
        valid_temperature_range=QuantityRange(
            minimum=_optional_quantity(material.valid_temperature_min_k, "K"),
            maximum=_optional_quantity(material.valid_temperature_max_k, "K"),
        ),
        source=MaterialSourceSnapshot(
            type=material.source_type,
            basis=material.source_basis,
            reference=material.source_reference,
            version=material.source_version,
            citation=material.source_citation,
        ),
    )


def _point(x: float, y: float, z: float) -> SimulationPoint3D:
    return SimulationPoint3D(
        x=_quantity(x, "mm"),
        y=_quantity(y, "mm"),
        z=_quantity(z, "mm"),
    )


def _quantity(value: float, unit: str) -> Quantity:
    return Quantity(value=float(value), unit=unit)


def _optional_quantity(value: float | None, unit: str) -> Quantity | None:
    return None if value is None else _quantity(value, unit)


def _model_sha256(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "build_simulation_spec",
    "simulation_plan_sha256",
    "simulation_spec_sha256",
    "validate_spec_plan_alignment",
]
