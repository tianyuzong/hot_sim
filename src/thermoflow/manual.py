"""Explicit, editable room-temperature STL template; no model-service dependency."""
import math

from .materials import list_materials
from .models import (
    CadFormat,
    ConvectionBoundary,
    MeshPlan,
    PlannerProvenance,
    SimulationPlan,
    SolverPlan,
    WorkpieceRecord,
)
from .planner import PlannerDecision
from .policy import MAX_TOTAL_CELLS


def manual_stl_template(workpiece: WorkpieceRecord) -> PlannerDecision:
    if workpiece.cad_format != CadFormat.STL or workpiece.dimensions_mm is None:
        raise ValueError("手动瞬态模板需要已确认尺度的 STL 工件")
    dimensions = workpiece.dimensions_mm.as_tuple()
    summary = workpiece.geometry.summary
    # Characteristic thickness is only a starting estimate, not a mesh-quality claim.
    thickness = 2 * abs(float(summary.get("volume", 0))) / max(float(summary.get("area", 1)), 1e-12)
    pitch = max(max(dimensions) / 100, min(min(dimensions) / 6, (thickness or min(dimensions)) / 6))
    pitch = math.ceil(pitch * 10) / 10
    while math.prod(math.ceil(length / pitch) + 1 for length in dimensions) > MAX_TOTAL_CELLS:
        pitch = math.ceil(pitch * 1.05 * 1000) / 1000
    backend = "tetra_stl_v1" if (thickness > 0 and pitch > 2 * thickness
        and workpiece.components and all(c.watertight for c in workpiece.components)) else "voxel_stl_v1"
    catalog = list_materials()[0]
    material = catalog.material.model_copy(update={"source_type": "suggestion"})
    plan = SimulationPlan(
        study_name=f"{workpiece.name[:85]} · 瞬态导热",
        analysis_type="transient_conduction", analyses=["transient_thermal"],
        initial_temperature_k=293.15, duration_s=60, time_step_s=0.3,
        material=material, boundaries=[], heat_source=None, heat_sources=[], heat_source_enabled=False,
        convection=ConvectionBoundary(ambient_temperature_k=293.15, heat_transfer_coefficient_w_m2_k=10),
        mesh=MeshPlan(target_element_size_mm=pitch, max_axis_intervals=100, refinement_passes=0),
        solver=SolverPlan(backend=backend, relative_tolerance=1e-8, max_iterations=5000),
        assumptions=["初始全工件为 20 ℃，不存在预设冷热端。", "各向同性、常物性固体导热；未建模相变和流体。",
                     "环境对流的待确认初值为 20 ℃、10 W/(m²·K)；未添加内部热源。"],
        decision_summary="手动草案（未调用 AI）：未添加热源。20 ℃ 初温、60 秒时长和环境对流为待确认初值；请选择实际材料和工况。初温与环境相同且无热源时，温度将保持不变。网格建议必须经过质量检查。",
        confidence=0.5,
        missing_information=["请设置实际材料、初始温度、环境和仿真时长（最长 3600 秒）；需要加热时再启用并设置热源。"],
        component_materials=[{"component_id": c.component_id, "material": material} for c in workpiece.components],
    )
    return PlannerDecision(plan=plan, provenance=PlannerProvenance(
        provider="manual-template", model="source-free-stl-v2", prompt_version="manual-v2", attempts=1,
    ))
