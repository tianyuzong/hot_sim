"""Version-checked draft editing and user-reviewed modeling conversations."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .materials import list_materials
from .models import (
    ConfirmationRecord,
    DraftUpdateRequest,
    ModelingChange,
    ModelingDecisionRequest,
    ModelingMessage,
    ModelingMessageRequest,
    ModelingProposal,
    SimulationPlan,
    StudyRecord,
    StudyStatus,
)
from .planner import PlannerUnavailableError, apply_user_overrides
from .policy import validate_plan
from .service import StudyService, _geometry_snapshot_sha256
from .specification import build_simulation_spec


class ModelingService:
    def __init__(self, service: StudyService, *, retain_history: bool = True):
        self.service = service
        self.repository = service.repository
        self.retain_history = retain_history

    def _editable(self, study_id: str, revision: int) -> StudyRecord:
        study = self.repository.get_study(study_id)
        if study.plan is None or study.confirmation.status == "confirmed" or study.status not in {
            StudyStatus.NEEDS_INPUT, StudyStatus.PLANNED, StudyStatus.REJECTED,
        }:
            raise ValueError("只有未确认的草案可以修改；请复制已确认研究后继续建模")
        self.service._assert_task_owner(study_id, None)
        if study.draft_revision != revision:
            raise ValueError("草案已在其他操作中更新，请重新载入后检查，当前修改未覆盖服务器数据")
        return study

    def _save(self, study: StudyRecord) -> StudyRecord:
        session = study.modeling.model_copy(update={"history_retained": self.retain_history})
        updated = study.model_copy(update={"modeling": session, "updated_at": datetime.now(timezone.utc)})
        stored = updated if self.retain_history else updated.model_copy(update={
            "modeling": session.model_copy(update={"messages": []}),
        })
        self.repository.save_study(stored)
        return updated

    def _replace_plan(self, study: StudyRecord, plan: SimulationPlan) -> StudyRecord:
        workpiece = self.service.get_workpiece(study.workpiece_id)
        confirmation = ConfirmationRecord(status="needs_input")
        plan = SimulationPlan.model_validate(plan.model_dump()).model_copy(update={"confirmation": confirmation})
        policy = validate_plan(workpiece, plan)
        spec = build_simulation_spec(
            project=self.service._project_for_workpiece(workpiece), workpiece=workpiece,
            plan=plan, geometry_snapshot_sha256=_geometry_snapshot_sha256(workpiece),
        )
        return study.model_copy(update={
            "plan": plan, "purpose": plan.purpose, "simulation_spec": spec, "policy": policy,
            "confirmation": confirmation, "status": StudyStatus.NEEDS_INPUT,
            "input_snapshot_sha256": None, "failure": None,
            "draft_revision": study.draft_revision + 1,
        })

    def update_draft(self, study_id: str, request: DraftUpdateRequest) -> StudyRecord:
        with self.repository.study_lock(study_id):
            study = self._editable(study_id, request.expected_revision)
            plan = apply_user_overrides(study.plan, request.overrides)
            if request.purpose is not None:
                plan = plan.model_copy(update={"purpose": request.purpose.strip()})
            # Repeated form synchronization is a no-op, preserving a pending proposal.
            if plan.model_dump(exclude={"decision_summary"}) == study.plan.model_dump(exclude={"decision_summary"}):
                return study
            updated = self._replace_plan(study, plan)
            edited = {key for key in SimulationPlan.model_fields if getattr(study.plan, key) != getattr(plan, key)}
            updated.modeling = study.modeling.model_copy(update={
                "proposal": None, "undo_plan": None, "undo_suggested_fields": [],
                "suggested_fields": [key for key in study.modeling.suggested_fields if key not in edited],
            })
            updated.overrides = request.overrides or study.overrides
            return self._save(updated)

    def message(self, study_id: str, request: ModelingMessageRequest) -> StudyRecord:
        # A separate lock bounds concurrent model calls without blocking form saves.
        with self.repository.modeling_lock(study_id):
            with self.repository.study_lock(study_id):
                study = self._editable(study_id, request.expected_revision)
                if study.modeling.proposal is not None:
                    raise ValueError("请先应用或放弃上一条建议，再继续对话")
                workpiece = self.service.get_workpiece(study.workpiece_id)
                geometry_hash = _modeling_geometry_sha256(workpiece)
            history = study.modeling.messages if self.retain_history else request.session_history
            history = [*history[-38:], ModelingMessage(role="user", content=request.message)]
            try:
                decision = self.service.planner.plan(
                    workpiece, user_description=request.message,
                    current_plan=study.plan.model_copy(deep=True), conversation=history,
                )
                plan = SimulationPlan.model_validate(decision.plan.model_dump())
            except PlannerUnavailableError:
                # Planner adapters already turn provider failures into fixed public messages.
                raise
            except Exception as exc:
                raise PlannerUnavailableError("建模助手暂时不可用；已保存的草案不受影响，请稍后重试") from exc
            plan = plan.model_copy(update={"confirmation": ConfirmationRecord(status="needs_input")})
            _validate_suggested_materials(study.plan, plan)
            policy = validate_plan(workpiece, plan)
            changes = describe_changes(study.plan, plan, workpiece)
            questions = list(dict.fromkeys([*policy.errors, *plan.missing_information]))[:3]
            content = "\n".join([plan.decision_summary, *questions])[:4_000]
            history.append(ModelingMessage(role="assistant", content=content))
            with self.repository.study_lock(study_id):
                current = self._editable(study_id, request.expected_revision)
                if _modeling_geometry_sha256(self.service.get_workpiece(study.workpiece_id)) != geometry_hash:
                    raise ValueError("几何信息已更新，请基于当前几何重新提出建模请求")
                revision = current.draft_revision + 1
                session = current.modeling.model_copy(update={
                    "messages": history, "undo_plan": None, "undo_suggested_fields": [],
                    "proposal": ModelingProposal(base_revision=revision, geometry_sha256=geometry_hash,
                        plan=plan, changes=changes, validation_errors=policy.errors),
                })
                return self._save(current.model_copy(update={
                    "modeling": session, "draft_revision": revision, "planner": decision.provenance,
                }))

    def decide(self, study_id: str, request: ModelingDecisionRequest) -> StudyRecord:
        with self.repository.study_lock(study_id):
            study = self._editable(study_id, request.expected_revision)
            session = study.modeling
            if request.action == "undo":
                if session.undo_plan is None or session.proposal is not None:
                    raise ValueError("当前没有可撤销的建议；后续编辑不会被撤销覆盖")
                updated = self._replace_plan(study, session.undo_plan)
                session = session.model_copy(update={"undo_plan": None,
                    "suggested_fields": session.undo_suggested_fields, "undo_suggested_fields": []})
            else:
                proposal = session.proposal
                if proposal is None or proposal.base_revision != study.draft_revision:
                    raise ValueError("该建议已失效，请重新检查当前草案")
                if request.action == "apply":
                    workpiece = self.service.get_workpiece(study.workpiece_id)
                    if _modeling_geometry_sha256(workpiece) != proposal.geometry_sha256:
                        raise ValueError("建议引用的几何已改变，请放弃该建议并重新建模")
                    updated = self._replace_plan(study, proposal.plan)
                    session = session.model_copy(update={"proposal": None, "undo_plan": study.plan,
                        "undo_suggested_fields": session.suggested_fields,
                        "suggested_fields": list(dict.fromkeys([*session.suggested_fields, *(c.field for c in proposal.changes)])),
                    })
                else:
                    updated = study.model_copy(update={"draft_revision": study.draft_revision + 1})
                    session = session.model_copy(update={"proposal": None, "undo_plan": None, "undo_suggested_fields": []})
            notice = {"apply": "用户已将建议应用到待确认草案，尚未确认或求解。",
                      "dismiss": "用户已放弃建议，保留原草案。", "undo": "用户已撤销上一次应用的建议。"}[request.action]
            session = session.model_copy(update={"messages": [*session.messages[-39:], ModelingMessage(role="assistant", content=notice)]})
            return self._save(updated.model_copy(update={"modeling": session}))


def _modeling_geometry_sha256(workpiece) -> str:
    payload = {"geometry": _geometry_snapshot_sha256(workpiece),
               "components": [c.model_dump(mode="json") for c in workpiece.components],
               "regions": [r.model_dump(mode="json") for r in workpiece.regions]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _validate_suggested_materials(current: SimulationPlan, proposed: SimulationPlan) -> None:
    trusted = [current.material, *(a.material for a in current.component_materials),
               *(entry.material for entry in list_materials())]
    fingerprints = [m.model_dump(exclude={"source_type"}) for m in trusted]
    for material in [proposed.material, *(a.material for a in proposed.component_materials)]:
        fingerprint = material.model_dump(exclude={"source_type"})
        if fingerprint not in fingerprints:
            raise ValueError("建议包含无法核验来源的材料属性；请选择材料目录记录或在材料面板输入物性后重试")
        canonical = trusted[fingerprints.index(fingerprint)]
        material.source_type = canonical.source_type


_LABELS = {
    "study_name": "研究名称", "purpose": "用途与工程问题", "analysis_type": "分析类型",
    "analyses": "物理场", "initial_temperature_k": "初始温度", "duration_s": "工作持续时间",
    "time_step_s": "时间步长", "material": "材料", "component_materials": "组件材料",
    "boundaries": "定温边界", "surface_conditions": "区域热边界", "contacts": "组件热接触", "heat_source_enabled": "启用局部热源",
    "global_convection_enabled": "启用全局对流", "heat_source": "局部热源", "convection": "全局对流",
    "mesh": "网格", "solver": "求解设置", "criteria": "工程判据", "assumptions": "建模假设",
    "missing_information": "待补充信息", "unsupported_physics": "未支持效应", "kind": "类型",
    "name": "名称", "component_id": "组件", "material_id": "材料记录", "region_id": "区域",
    "selector": "边界位置", "temperature_k": "温度", "heat_flux_w_m2": "流入热流密度",
    "ambient_temperature_k": "流体温度", "radiation_temperature_k": "辐射环境温度",
    "heat_transfer_coefficient_w_m2_k": "换热系数", "emissivity": "发射率", "emissivity_source": "发射率来源",
    "thermal_conductivity_w_m_k": "导热系数", "density_kg_m3": "密度", "specific_heat_j_kg_k": "比热容",
    "source_basis": "数据依据", "source_reference": "来源", "source_version": "数据版本", "source_citation": "引用",
    "source_type": "数据来源类型", "valid_temperature_min_k": "适用最低温度", "valid_temperature_max_k": "适用最高温度",
    "allowable_temperature_k": "允许温度", "total_power_w": "功率", "radius_mm": "作用半径",
    "center_mm": "位置 (mm)", "end_mm": "终点 (mm)", "shape": "形状", "placement": "放置方式",
    "embedding_depth_mm": "嵌入深度", "surface_normal_axis": "表面法向", "surface_width_mm": "表面宽度",
    "surface_height_mm": "表面高度", "surface_thickness_mm": "映射厚度", "target_element_size_mm": "目标网格尺寸",
    "max_axis_intervals": "每轴最大区间数", "refinement_passes": "加密次数", "relative_tolerance": "相对容差",
    "max_iterations": "最大迭代数", "backend": "求解模型", "metric": "指标", "operator": "关系",
    "target": "限值", "value": "数值", "unit": "单位", "label": "名称", "x": "X", "y": "Y", "z": "Z",
}
_ENUMS = {
    "steady_state_conduction": "稳态导热", "transient_conduction": "瞬态导热",
    "steady_thermal": "稳态热", "transient_thermal": "瞬态热", "fixed_temperature": "定温",
    "heat_flux": "热流", "convection": "对流", "radiation": "环境辐射", "point": "点", "line": "线",
    "surface": "表面", "embedded": "嵌入", "database": "材料目录", "user": "用户输入", "suggestion": "待确认建议",
    "voxel_stl_v1": "体素导热", "analytic_box_v1": "解析长方体", "max_temperature": "最高温度",
    "less_than": "小于", "less_or_equal": "不超过", "greater_than": "大于", "greater_or_equal": "不低于",
}


def describe_changes(before: SimulationPlan, after: SimulationPlan, workpiece) -> list[ModelingChange]:
    names = {c.component_id: c.name for c in workpiece.components}
    names.update({r.region_id: r.name for r in workpiece.regions})
    names.update({entry.material_id: entry.material.name for entry in list_materials()})
    names.update({f"face.{axis}{side}": f"{axis.upper()} 轴{'最小' if side == 'min' else '最大'}侧"
                  for axis in "xyz" for side in ("min", "max")})

    def display(value, field=""):
        if value is None:
            return "未设置"
        if isinstance(value, bool):
            return "开启" if value else "关闭"
        if isinstance(value, dict):
            if set(value) == {"value", "unit"}:
                return f"{value['value']:g} {value['unit']}"
            return "；".join(f"{_LABELS.get(key, '属性')}：{display(item, key)}" for key, item in value.items() if item is not None)
        if isinstance(value, list):
            return "\n".join(display(item, field) for item in value) or "无"
        if isinstance(value, (int, float)):
            unit = next((unit for suffix, unit in (("_w_m2_k", "W/(m²·K)"), ("_w_m_k", "W/(m·K)"),
                ("_j_kg_k", "J/(kg·K)"), ("_kg_m3", "kg/m³"), ("_w_m2", "W/m²"),
                ("_mm", "mm"), ("_k", "K"), ("_w", "W"), ("_s", "s")) if field.endswith(suffix)), "")
            return f"{value:g} {unit}".strip()
        if field.endswith("_id"):
            return names.get(value, "无效引用")
        return names.get(value, _ENUMS.get(value, str(value)))

    left, right = before.model_dump(mode="json"), after.model_dump(mode="json")
    return [ModelingChange(field=key, label=_LABELS[key], before=display(left[key], key), after=display(right[key], key))
            for key in left if key in _LABELS and left[key] != right[key]]
