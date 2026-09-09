"""Explicit, editable room-temperature STL template; no model-service dependency."""
import math

from .materials import list_materials
from .models import (
    CadFormat,
    ConvectionBoundary,
    MeshPlan,
    PlannerProvenance,
    Point3DMM,
    SimulationPlan,
    SolverPlan,
    VolumetricHeatSource,
    WorkpieceRecord,
)
from .planner import PlannerDecision
from .policy import MAX_TOTAL_CELLS


def manual_stl_template(workpiece: WorkpieceRecord) -> PlannerDecision:
    if workpiece.cad_format != CadFormat.STL or workpiece.dimensions_mm is None:
        raise ValueError("手动瞬态模板需要已确认尺度的 STL 工件")
    dimensions = workpiece.dimensions_mm.as_tuple()
    summary = workpiece.geometry.summary
    bbox = summary.get("bbox", [0, 0, 0, *dimensions])
    center = [(bbox[i] + bbox[i + 3]) / 2 for i in range(3)]
    # Characteristic thickness is only a starting estimate, not a mesh-quality claim.
    thickness = 2 * abs(float(summary.get("volume", 0))) / max(float(summary.get("area", 1)), 1e-12)
    pitch = max(max(dimensions) / 100, min(min(dimensions) / 6, (thickness or min(dimensions)) / 6))
    pitch = math.ceil(pitch * 10) / 10
    while math.prod(math.ceil(length / pitch) + 1 for length in dimensions) > MAX_TOTAL_CELLS:
        pitch = math.ceil(pitch * 1.05 * 1000) / 1000
    catalog = list_materials()[0]
    material = catalog.material.model_copy(update={"source_type": "suggestion"})
    source = VolumetricHeatSource(
        shape="point", placement="embedded", center_mm=Point3DMM(x=center[0], y=center[1], z=center[2]),
        total_power_w=1.0, radius_mm=pitch, embedding_depth_mm=0,
    )
    plan = SimulationPlan(
        study_name=f"{workpiece.name[:85]} · 瞬态导热",
        analysis_type="transient_conduction", analyses=["transient_thermal"],
        initial_temperature_k=293.15, duration_s=60, time_step_s=0.3,
        material=material, boundaries=[], heat_source=source, heat_sources=[source],
        convection=ConvectionBoundary(ambient_temperature_k=293.15, heat_transfer_coefficient_w_m2_k=10),
        mesh=MeshPlan(target_element_size_mm=pitch, max_axis_intervals=100, refinement_passes=0),
        solver=SolverPlan(backend="voxel_stl_v1", relative_tolerance=1e-8, max_iterations=5000),
        assumptions=["初始全工件为 20 ℃，不存在预设冷热端。", "各向同性、常物性固体导热；未建模相变和流体。",
                     "默认外表面对 20 ℃ 环境对流；热源功率在整个仿真时段恒定。"],
        decision_summary="手动模板（未调用 AI）：默认 20 ℃、60 秒、1 W 局部热源。请选择实际材料并调整热源位置和功率；网格建议必须经过质量检查。",
        confidence=0.5,
        missing_information=["请核对材料、热源位置与功率、初始温度和环境参数，然后确认开始仿真。"],
        component_materials=[{"component_id": c.component_id, "material": material} for c in workpiece.components],
    )
    return PlannerDecision(plan=plan, provenance=PlannerProvenance(
        provider="manual-template", model="room-temperature-stl-v1", prompt_version="manual-v1", attempts=1,
    ))
