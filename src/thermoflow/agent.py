"""Bounded agent loop for inspecting, solving, and improving thermal studies."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import Field

from .codex_harness import CodexHarness, CodexHarnessError
from .models import (
    AgentGoalRequest,
    AgentRunRecord,
    AgentRunStatus,
    AgentStep,
    MeshReviewRequest,
    ResolvedAgentGoal,
    SimulationOverrides,
    SimulationPlan,
    SimulationResult,
    StrictModel,
)
from .planner import PlannerUnavailableError, _openai_failure
from .service import StudyService
from .settings import Settings
from .storage import FileRepository

AGENT_PROMPT_VERSION = "thermal-agent-v1"
AGENT_INSTRUCTIONS = """
你是热仿真平台中的受控工程 Agent。你会收到几何观察、求解结果、确定性约束评估，
以及平台计算出的候选参数调整。你只能选择 finish 或 apply_adjustment，不能生成代码、
命令、文件路径或新的工具。数值计算和是否达标以平台工具输出为准。存在未满足约束且
候选调整能够改善结果时选择 apply_adjustment；目标已满足、没有可用调整或继续迭代
没有工程意义时选择 finish。summary 必须使用简体中文，只说明可审计的工程依据，
不得输出隐藏思维过程。
""".strip()


class AgentPolicyDecision(StrictModel):
    action: Literal["finish", "apply_adjustment"]
    summary: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True, slots=True)
class PolicyChoice:
    decision: AgentPolicyDecision
    response_id: str | None = None


class AgentPolicy(Protocol):
    provider: str
    model: str

    def decide(
        self,
        context: dict[str, Any],
        suggested_changes: dict[str, Any],
    ) -> PolicyChoice: ...


class DeterministicAgentPolicy:
    """Offline policy used by tests and local demonstrations."""

    provider = "deterministic-physics-agent"
    model = "bounded-control-v1"

    def decide(
        self,
        context: dict[str, Any],
        suggested_changes: dict[str, Any],
    ) -> PolicyChoice:
        if suggested_changes:
            summary = "存在未满足的物理约束，执行平台计算出的受控参数调整。"
            action: Literal["finish", "apply_adjustment"] = "apply_adjustment"
        else:
            summary = "没有既获授权又能改善当前结果的参数调整，结束本次闭环。"
            action = "finish"
        return PolicyChoice(decision=AgentPolicyDecision(action=action, summary=summary))


class OpenAIAgentPolicy:
    """Model policy that chooses between server-computed, allowlisted actions."""

    provider = "openai"

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

    def decide(
        self,
        context: dict[str, Any],
        suggested_changes: dict[str, Any],
    ) -> PolicyChoice:
        if not self.api_key:
            raise PlannerUnavailableError("运行 OpenAI 仿真 Agent 必须配置 OPENAI_API_KEY")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise PlannerUnavailableError("尚未安装 openai Python 软件包") from exc

        try:
            client = OpenAI(api_key=self.api_key, timeout=45.0, max_retries=2)
            response = client.responses.parse(
                model=self.model,
                instructions=AGENT_INSTRUCTIONS,
                input=json.dumps(
                    {"state": context, "allowed_adjustment": suggested_changes},
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                text_format=AgentPolicyDecision,
                store=False,
            )
        except Exception as exc:  # noqa: BLE001 - sanitize the external provider boundary
            raise _openai_failure(exc) from None
        parsed = response.output_parsed
        if parsed is None:
            raise PlannerUnavailableError("模型未返回结构化优化决策，未执行任何候选调整，请重试")
        if parsed.action == "apply_adjustment" and not suggested_changes:
            parsed = parsed.model_copy(update={"action": "finish"})
        return PolicyChoice(decision=parsed, response_id=getattr(response, "id", None))


class CodexAgentPolicy:
    """Codex chooses only between the same server-authorized optimization actions."""

    provider = "codex-cli"
    model = "codex-configured"

    def __init__(self, harness: CodexHarness):
        self.harness = harness

    def decide(self, context: dict[str, Any], suggested_changes: dict[str, Any]) -> PolicyChoice:
        try:
            parsed, self.model = self.harness.generate(
                AgentPolicyDecision, instructions=AGENT_INSTRUCTIONS,
                payload={"state": context, "allowed_adjustment": suggested_changes},
            )
        except CodexHarnessError as exc:
            raise PlannerUnavailableError(str(exc)) from None
        if parsed.action == "apply_adjustment" and not suggested_changes:
            parsed = parsed.model_copy(update={"action": "finish"})
        return PolicyChoice(decision=parsed)


def build_agent_policy(settings: Settings) -> AgentPolicy:
    if settings.planner_mode == "deterministic":
        return DeterministicAgentPolicy()
    if settings.planner_mode == "codex":
        return CodexAgentPolicy(CodexHarness(executable=settings.codex_executable, codex_home=settings.codex_home,
                                            timeout_seconds=settings.codex_timeout_seconds))
    return OpenAIAgentPolicy(settings.openai_model)


class SimulationAgent:
    """Runs a ReAct-style loop with deterministic physics feedback and hard budgets."""

    def __init__(
        self,
        repository: FileRepository,
        service: StudyService,
        policy: AgentPolicy,
    ) -> None:
        self.repository = repository
        self.service = service
        self.policy = policy

    def run(self, base_study_id: str, goal_request: AgentGoalRequest) -> AgentRunRecord:
        base_study = self.repository.get_study(base_study_id)
        if base_study.status.value != "succeeded" or base_study.plan is None:
            raise ValueError("仿真 Agent 需要一个已成功求解的基准研究")
        workpiece = self.repository.get_workpiece(base_study.workpiece_id)
        self.repository.get_result(base_study_id)
        goal = resolve_agent_goal(goal_request)
        record = AgentRunRecord(
            run_id=f"agent-{uuid.uuid4().hex}",
            base_study_id=base_study_id,
            selected_study_id=base_study_id,
            workpiece_id=base_study.workpiece_id,
            project_id=base_study.project_id,
            status=AgentRunStatus.RUNNING,
            goal=goal_request,
            resolved_goal=goal,
            provider=self.policy.provider,
            model=self.policy.model,
            prompt_version=AGENT_PROMPT_VERSION,
        )
        record = self._append_step(
            record,
            phase="observe",
            tool="inspect_geometry",
            title="检查 STL 与重建域",
            summary=_geometry_summary(workpiece.geometry.summary),
            metrics=_geometry_metrics(workpiece.geometry.summary),
        )
        self.repository.save_agent_run(record)

        current_study = base_study
        try:
            while True:
                assert current_study.plan is not None
                result = self.repository.get_result(current_study.study_id)
                record = self._append_step(
                    record,
                    phase="observe",
                    tool="inspect_result",
                    title="读取求解结果",
                    summary=(
                        f"峰值温度 {result.temperature_max_k:.2f} K，"
                        f"能量相对误差 {result.energy_balance_relative_error:.3g}。"
                    ),
                    study_id=current_study.study_id,
                    metrics=_result_metrics(result),
                )
                evaluation = _evaluate(current_study.plan, result, goal)
                record = self._append_step(
                    record,
                    phase="evaluate",
                    tool="evaluate_constraints",
                    title="校核工程目标",
                    summary=evaluation.summary,
                    study_id=current_study.study_id,
                    metrics={"goal_met": evaluation.goal_met, **evaluation.metrics},
                )
                record = record.model_copy(update={"selected_study_id": current_study.study_id})
                self.repository.save_agent_run(record)

                if evaluation.goal_met:
                    return self._finish(
                        record,
                        AgentRunStatus.GOAL_MET,
                        f"目标已满足；采用研究 {current_study.study_id}。",
                    )
                if record.rounds_completed >= goal.max_rounds:
                    return self._finish(
                        record,
                        AgentRunStatus.BUDGET_EXHAUSTED,
                        f"已完成 {record.rounds_completed} 轮重算，达到用户设定的迭代上限。",
                    )

                changes, change_summary = _suggest_adjustments(
                    current_study.plan,
                    result,
                    goal,
                    evaluation,
                )
                context = {
                    "instruction": goal_request.instruction,
                    "round": record.rounds_completed + 1,
                    "max_rounds": goal.max_rounds,
                    "evaluation": evaluation.model_dump(mode="json"),
                    "current_result": _result_metrics(result),
                }
                choice = self.policy.decide(context, changes)
                response_ids = list(record.response_ids)
                if choice.response_id:
                    response_ids.append(choice.response_id)
                record = record.model_copy(update={"response_ids": response_ids, "model": self.policy.model})
                record = self._append_step(
                    record,
                    phase="decide",
                    tool="finish" if choice.decision.action == "finish" else "adjust_parameters",
                    title="选择下一步",
                    summary=choice.decision.summary,
                    study_id=current_study.study_id,
                    changes=changes if choice.decision.action == "apply_adjustment" else {},
                )
                if choice.decision.action == "finish" or not changes:
                    return self._finish(
                        record,
                        AgentRunStatus.BUDGET_EXHAUSTED,
                        "当前目标尚未全部满足，但没有继续执行获授权调整。",
                    )

                record = self._append_step(
                    record,
                    phase="act",
                    tool="adjust_parameters",
                    title="生成候选方案",
                    summary=change_summary,
                    study_id=current_study.study_id,
                    changes=changes,
                )
                overrides = _overrides_from_plan(current_study.plan, changes)
                candidate = self.service.create_variant_study(current_study.study_id, overrides)
                if candidate.status.value != "planned":
                    error_text = "; ".join(candidate.policy.errors) if candidate.policy else "方案被拒绝"
                    raise ValueError(f"Agent 候选方案未通过策略校验：{error_text}")
                candidate_mesh = self.service.generate_mesh(candidate.study_id)
                if candidate_mesh.review_status == "pending":
                    self.service.confirm_mesh(
                        candidate.study_id,
                        MeshReviewRequest(
                            accept_warnings=True,
                            confirmed_by="Agent 授权范围",
                        ),
                    )
                completed = self.service.run_study(candidate.study_id)
                candidate_ids = [*record.candidate_study_ids, completed.study_id]
                record = record.model_copy(
                    update={
                        "rounds_completed": record.rounds_completed + 1,
                        "candidate_study_ids": candidate_ids,
                        "selected_study_id": completed.study_id,
                    }
                )
                candidate_result = self.repository.get_result(completed.study_id)
                record = self._append_step(
                    record,
                    phase="act",
                    tool="run_solver",
                    title=f"完成第 {record.rounds_completed} 轮求解",
                    summary=(
                        f"候选研究峰值温度 {candidate_result.temperature_max_k:.2f} K，"
                        f"能量相对误差 {candidate_result.energy_balance_relative_error:.3g}。"
                    ),
                    study_id=completed.study_id,
                    metrics=_result_metrics(candidate_result),
                )
                self.repository.save_agent_run(record)
                current_study = completed
        except Exception as exc:  # noqa: BLE001 - persist every tool failure in the audit record
            logging.getLogger(__name__).warning(
                "Agent run failed: run_id=%s error_type=%s", record.run_id, type(exc).__name__,
            )
            failed = record.model_copy(
                update={
                    "status": AgentRunStatus.FAILED,
                    "completed_at": datetime.now(timezone.utc),
                    "summary": "仿真 Agent 在受控工具调用期间终止。",
                    "failure": str(exc) if isinstance(exc, PlannerUnavailableError) else (
                        "受控计算步骤未完成，请检查输入或联系部署管理员；已完成研究仍可查看"
                    ),
                }
            )
            self.repository.save_agent_run(failed)
            return failed

    def _append_step(
        self,
        record: AgentRunRecord,
        *,
        phase: Literal["observe", "evaluate", "decide", "act", "finish"],
        tool: Literal[
            "inspect_geometry",
            "inspect_result",
            "evaluate_constraints",
            "adjust_parameters",
            "run_solver",
            "finish",
        ],
        title: str,
        summary: str,
        study_id: str | None = None,
        metrics: dict[str, Any] | None = None,
        changes: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        step = AgentStep(
            sequence=len(record.steps) + 1,
            phase=phase,
            tool=tool,
            title=title,
            summary=summary,
            study_id=study_id,
            metrics=metrics or {},
            changes=changes or {},
        )
        return record.model_copy(update={"steps": [*record.steps, step]})

    def _finish(
        self,
        record: AgentRunRecord,
        status: AgentRunStatus,
        summary: str,
    ) -> AgentRunRecord:
        completed = self._append_step(
            record,
            phase="finish",
            tool="finish",
            title="结束闭环",
            summary=summary,
            study_id=record.selected_study_id,
        ).model_copy(
            update={
                "status": status,
                "completed_at": datetime.now(timezone.utc),
                "summary": summary,
            }
        )
        self.repository.save_agent_run(completed)
        return completed


class AgentEvaluation(StrictModel):
    goal_met: bool
    summary: str
    issues: list[str]
    metrics: dict[str, Any]


def resolve_agent_goal(request: AgentGoalRequest) -> ResolvedAgentGoal:
    target_max = request.target_max_temperature_k
    target_min = request.target_min_temperature_k
    parsed: list[str] = []
    if target_max is None:
        value = _parse_temperature_constraint(request.instruction, "max")
        if value is not None:
            target_max = value
            parsed.append(f"从目标文本识别最高温度 {value:.2f} K")
    if target_min is None:
        value = _parse_temperature_constraint(request.instruction, "min")
        if value is not None:
            target_min = value
            parsed.append(f"从目标文本识别最低温度 {value:.2f} K")
    if target_min is not None and target_max is not None and target_min > target_max:
        raise ValueError("解析后的最低目标温度高于最高目标温度")
    return ResolvedAgentGoal(
        target_max_temperature_k=target_max,
        target_min_temperature_k=target_min,
        temperature_tolerance_k=request.temperature_tolerance_k,
        max_energy_balance_relative_error=request.max_energy_balance_relative_error,
        max_rounds=request.max_rounds,
        allow_power_adjustment=request.allow_power_adjustment,
        allow_mesh_refinement=request.allow_mesh_refinement,
        parsed_from_instruction=parsed,
    )


def _parse_temperature_constraint(instruction: str, bound: Literal["max", "min"]) -> float | None:
    clauses = re.split(r"[，,；;。\n]", instruction)
    markers = (
        ("最高", "峰值", "不超过", "低于", "小于", "上限", "至多", "≤")
        if bound == "max"
        else ("最低", "至少", "高于", "大于", "下限", "不低于", "≥")
    )
    pattern = re.compile(r"(-?\d+(?:\.\d+)?)\s*(K|开尔文|℃|°\s*C|摄氏度)", re.IGNORECASE)
    for clause in clauses:
        if not any(marker in clause for marker in markers):
            continue
        match = pattern.search(clause)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower().replace(" ", "")
            return value + 273.15 if unit in {"℃", "°c", "摄氏度"} else value
    return None


def _evaluate(
    plan: SimulationPlan,
    result: SimulationResult,
    goal: ResolvedAgentGoal,
) -> AgentEvaluation:
    issues: list[str] = []
    tolerance = goal.temperature_tolerance_k
    if (
        goal.target_max_temperature_k is not None
        and result.temperature_max_k > goal.target_max_temperature_k + tolerance
    ):
        issues.append(
            f"峰值温度高于上限 {goal.target_max_temperature_k:.2f} K"
        )
    if (
        goal.target_min_temperature_k is not None
        and result.temperature_max_k < goal.target_min_temperature_k - tolerance
    ):
        issues.append(
            f"峰值温度低于下限 {goal.target_min_temperature_k:.2f} K"
        )
    if result.energy_balance_relative_error > goal.max_energy_balance_relative_error:
        issues.append(
            "能量相对误差高于 "
            f"{goal.max_energy_balance_relative_error:.3g}"
        )
    mapping = result.heat_source_mapping or {}
    if (
        plan.heat_source is not None
        and plan.heat_source_enabled
        and plan.heat_source.placement == "embedded"
        and mapping.get("depth_satisfied") is False
    ):
        issues.append("重建域无法达到请求的热源嵌入深度")
    summary = "所有可判定目标均已满足。" if not issues else "；".join(issues) + "。"
    return AgentEvaluation(
        goal_met=not issues,
        summary=summary,
        issues=issues,
        metrics={
            "temperature_min_k": result.temperature_min_k,
            "temperature_max_k": result.temperature_max_k,
            "energy_balance_relative_error": result.energy_balance_relative_error,
            "source_depth_satisfied": mapping.get("depth_satisfied"),
        },
    )


def _suggest_adjustments(
    plan: SimulationPlan,
    result: SimulationResult,
    goal: ResolvedAgentGoal,
    evaluation: AgentEvaluation,
) -> tuple[dict[str, Any], str]:
    changes: dict[str, Any] = {}
    reasons: list[str] = []
    source = plan.heat_source
    mapping = result.heat_source_mapping or {}
    if source is not None and mapping.get("depth_satisfied") is False:
        achieved = mapping.get("achieved_embedding_depth_mm")
        if isinstance(achieved, (int, float)) and achieved >= 0:
            changes["heat_source_embedding_depth_mm"] = float(achieved)
            reasons.append("把请求深度对齐到重建域实际可达深度")

    temperature_target: float | None = None
    if (
        goal.target_max_temperature_k is not None
        and result.temperature_max_k > goal.target_max_temperature_k + goal.temperature_tolerance_k
    ):
        temperature_target = goal.target_max_temperature_k
    elif (
        goal.target_min_temperature_k is not None
        and result.temperature_max_k < goal.target_min_temperature_k - goal.temperature_tolerance_k
    ):
        temperature_target = goal.target_min_temperature_k

    if temperature_target is not None and source is not None and plan.heat_source_enabled and goal.allow_power_adjustment:
        boundary_ceiling = max((item.temperature_k for item in plan.boundaries), default=0)
        if temperature_target + goal.temperature_tolerance_k >= boundary_ceiling:
            reference = min(
                [item.temperature_k for item in plan.boundaries]
                + ([plan.convection.ambient_temperature_k] if plan.convection and plan.global_convection_enabled else [])
                + [c.ambient_temperature_k for c in plan.surface_conditions if c.kind == "convection"]
                + [c.radiation_temperature_k for c in plan.surface_conditions if c.kind == "radiation"],
                default=0,
            )
            resistance = result.thermal_resistance_k_w
            if resistance is not None and math.isfinite(resistance) and resistance > 1e-12:
                proposed = (temperature_target - reference) / resistance
                current = source.total_power_w
                proposed = max(current * 0.2, min(current * 5.0, proposed))
                proposed = max(1e-6, min(10_000_000.0, proposed))
                if not math.isclose(proposed, current, rel_tol=1e-6, abs_tol=1e-9):
                    changes["heat_source_power_w"] = proposed
                    reasons.append(
                        f"按当前热阻把热源功率从 {current:.3g} W 调整为 {proposed:.3g} W"
                    )

    if (
        result.energy_balance_relative_error > goal.max_energy_balance_relative_error
        and goal.allow_mesh_refinement
    ):
        refined_size = max(plan.mesh.target_element_size_mm * 0.75, 1e-6)
        changes["target_element_size_mm"] = refined_size
        changes["max_axis_intervals"] = min(100, max(plan.mesh.max_axis_intervals, 80))
        changes["relative_tolerance"] = min(
            plan.solver.relative_tolerance,
            max(1e-12, goal.max_energy_balance_relative_error * 0.1),
        )
        reasons.append("细化体素并收紧线性求解容差")

    if not changes and evaluation.issues:
        reasons.append("未找到符合当前权限边界的调整")
    return changes, "；".join(reasons) + "。"


def _overrides_from_plan(
    plan: SimulationPlan,
    changes: dict[str, Any],
) -> SimulationOverrides:
    source = plan.heat_source
    values: dict[str, Any] = {
        "material_name": plan.material.name,
        "thermal_conductivity_w_m_k": plan.material.thermal_conductivity_w_m_k,
        "density_kg_m3": plan.material.density_kg_m3,
        "specific_heat_j_kg_k": plan.material.specific_heat_j_kg_k,
        "fixed_boundaries": plan.boundaries,
        "surface_conditions": plan.surface_conditions,
        "contacts": plan.contacts,
        "enable_heat_source": plan.heat_source_enabled,
        "enable_global_convection": plan.global_convection_enabled,
        "component_materials": plan.component_materials,
        "criteria": plan.criteria,
        "target_element_size_mm": plan.mesh.target_element_size_mm,
        "max_axis_intervals": plan.mesh.max_axis_intervals,
        "relative_tolerance": plan.solver.relative_tolerance,
        "max_iterations": plan.solver.max_iterations,
    }
    if source is not None:
        values.update(
            {
                "heat_source_x_mm": source.center_mm.x,
                "heat_source_y_mm": source.center_mm.y,
                "heat_source_z_mm": source.center_mm.z,
                "heat_source_shape": source.shape,
                "heat_source_placement": source.placement,
                "heat_source_embedding_depth_mm": source.embedding_depth_mm,
                "heat_source_power_w": source.total_power_w,
                "heat_source_radius_mm": source.radius_mm,
                "heat_source_surface_axis": source.surface_normal_axis,
                "heat_source_surface_width_mm": source.surface_width_mm,
                "heat_source_surface_height_mm": source.surface_height_mm,
                "heat_source_surface_thickness_mm": source.surface_thickness_mm,
            }
        )
        if source.end_mm is not None:
            values.update(
                {
                    "heat_source_end_x_mm": source.end_mm.x,
                    "heat_source_end_y_mm": source.end_mm.y,
                    "heat_source_end_z_mm": source.end_mm.z,
                }
            )
    if plan.convection is not None:
        values.update(
            {
                "ambient_temperature_k": plan.convection.ambient_temperature_k,
                "convection_coefficient_w_m2_k": (
                    plan.convection.heat_transfer_coefficient_w_m2_k
                ),
            }
        )
    values.update(changes)
    return SimulationOverrides.model_validate(values)


def _geometry_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    quality = summary.get("quality") or {}
    return {
        "watertight": quality.get("watertight"),
        "winding_consistent": quality.get("winding_consistent"),
        "body_count": quality.get("body_count"),
        "triangle_count": summary.get("triangle_count"),
        "simulation_domain": summary.get("simulation_domain"),
    }


def _geometry_summary(summary: dict[str, Any]) -> str:
    metrics = _geometry_metrics(summary)
    state = "封闭 STL" if metrics["watertight"] else "开放或不完整 STL"
    triangles = metrics["triangle_count"]
    triangle_text = f"，{triangles} 个三角面" if isinstance(triangles, int) else ""
    return f"识别为{state}{triangle_text}；求解继续使用平台重建域。"


def _result_metrics(result: SimulationResult) -> dict[str, Any]:
    mapping = result.heat_source_mapping or {}
    return {
        "temperature_min_k": result.temperature_min_k,
        "temperature_max_k": result.temperature_max_k,
        "heat_source_power_w": result.heat_source_power_w,
        "thermal_resistance_k_w": result.thermal_resistance_k_w,
        "energy_balance_relative_error": result.energy_balance_relative_error,
        "grid_cells": result.grid.get("cells"),
        "source_depth_satisfied": mapping.get("depth_satisfied"),
    }


__all__ = [
    "DeterministicAgentPolicy",
    "OpenAIAgentPolicy",
    "SimulationAgent",
    "build_agent_policy",
    "resolve_agent_goal",
]
