"""Application service coordinating storage, CadFlow, GPT, policy, and solvers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .cadflow_adapter import CadFlowGeometryInspector, box_content_hash
from .execution import ExecutionMonitor, UnmonitoredExecution
from .materials import list_materials
from .meshing import generate_voxel_mesh_record
from .models import (
    BoxWorkpieceInput,
    CadFormat,
    ComparisonScale,
    ComparisonStudySummary,
    ComponentMaterialAssignment,
    ComponentUpdateRequest,
    ConfirmationRecord,
    DimensionsMM,
    FaceSelector,
    GeometryComponent,
    GeometryInspection,
    GeometryRegion,
    LengthUnit,
    MeshRecord,
    MeshReviewRequest,
    MeshStatus,
    ProjectCreateRequest,
    ProjectRecord,
    ProjectUpdateRequest,
    ProjectWorkspace,
    Quantity,
    SimulationOverrides,
    SimulationPlan,
    SimulationResult,
    SimulationSpec,
    SimulationPoint3D,
    StudyComparisonRequest,
    StudyComparisonResponse,
    StudyCopyRequest,
    StudyDifferenceSummary,
    StudyConfirmationRequest,
    StudyRecord,
    StudyStatus,
    TemperatureDifferenceField,
    WorkpieceKind,
    WorkpieceRecord,
)
from .planner import PlannerUnavailableError, SimulationPlanner, apply_user_overrides
from .policy import validate_plan
from .regions import SURFACE_CONDITION_KINDS
from .specification import (
    build_simulation_spec,
    simulation_plan_sha256,
    simulation_spec_sha256,
    validate_spec_plan_alignment,
)
from .solvers import AnalyticBoxSolver, VoxelStlSolver
from .stl_geometry import inspect_stl, load_stl_mesh, remove_stl_component
from .storage import FileRepository


_TERMINAL_TASK_STATES = {
    "succeeded", "needs_review", "cancelled", "failed", "timed_out", "interrupted",
}


class StudyService:
    def __init__(
        self,
        repository: FileRepository,
        planner: SimulationPlanner,
        geometry: CadFlowGeometryInspector,
        compute_backend: str = "auto",
    ) -> None:
        self.repository = repository
        self.planner = planner
        self.geometry = geometry
        self.solvers = {
            AnalyticBoxSolver.solver_id: AnalyticBoxSolver(),
            VoxelStlSolver.solver_id: VoxelStlSolver(compute_backend=compute_backend),
        }

    def compute_runtime(self) -> dict[str, object]:
        solver = self.solvers[VoxelStlSolver.solver_id]
        return solver.compute_runtime()

    def create_project(self, request: ProjectCreateRequest) -> ProjectRecord:
        now = datetime.now(timezone.utc)
        project = ProjectRecord(
            project_id=_new_id("project"),
            name=request.name,
            created_at=now,
            updated_at=now,
        )
        self.repository.save_project(project)
        return project

    def update_project(
        self,
        project_id: str,
        request: ProjectUpdateRequest,
    ) -> ProjectRecord:
        project = self.repository.get_project(project_id)
        updated = project.model_copy(
            update={"name": request.name, "updated_at": datetime.now(timezone.utc)}
        )
        self.repository.save_project(updated)
        return updated

    def delete_project(self, project_id: str) -> dict[str, object]:
        """Delete a project and every persisted record owned by it."""
        project = self.repository.get_project(project_id)
        workpiece_ids = set(project.workpiece_ids)
        workpiece_ids.update(
            item.workpiece_id
            for item in self.repository.list_workpieces(project_id=project_id)
        )
        studies_by_id = {
            item.study_id: item
            for item in self.repository.list_studies(project_id=project_id)
        }
        for workpiece_id in workpiece_ids:
            for study in self.repository.list_studies(workpiece_id=workpiece_id):
                studies_by_id[study.study_id] = study
        study_ids = set(studies_by_id)
        tasks_by_id = {
            item.task_id: item
            for item in self.repository.list_tasks(project_id=project_id)
        }
        for study_id in study_ids:
            for task in self.repository.list_tasks(study_id=study_id):
                tasks_by_id[task.task_id] = task
        active_tasks = [
            task for task in tasks_by_id.values()
            if task.status not in _TERMINAL_TASK_STATES
        ]
        if active_tasks:
            raise ValueError("项目存在正在运行或排队的计算任务，请先取消任务后再删除")

        agent_run_ids = {
            item.run_id
            for item in self.repository.list_agent_runs(project_id=project_id)
        }
        for workpiece_id in workpiece_ids:
            agent_run_ids.update(
                item.run_id
                for item in self.repository.list_agent_runs(workpiece_id=workpiece_id)
            )
        for run_id in agent_run_ids:
            self.repository.delete_agent_run_dir(run_id)
        for task_id in tasks_by_id:
            self.repository.delete_task_dir(task_id)
        for study_id in study_ids:
            self.repository.delete_study_dir(study_id)
        for workpiece_id in workpiece_ids:
            self.repository.delete_workpiece_dir(workpiece_id)
        self.repository.delete_project_dir(project_id)
        return {"project_id": project_id, "deleted": True}

    def list_projects(self) -> list[ProjectRecord]:
        for workpiece in self.list_workpieces():
            if workpiece.project_id is None:
                self._project_for_workpiece(workpiece)
        return self.repository.list_projects()

    def list_workpieces(self, project_id: str | None = None) -> list[WorkpieceRecord]:
        return [
            self._ensure_workpiece_regions(workpiece)
            for workpiece in self.repository.list_workpieces(project_id=project_id)
        ]

    def get_workpiece(self, workpiece_id: str) -> WorkpieceRecord:
        return self._ensure_workpiece_regions(self.repository.get_workpiece(workpiece_id))

    def update_component(
        self,
        workpiece_id: str,
        component_id: str,
        request: ComponentUpdateRequest,
    ) -> WorkpieceRecord:
        self.get_workpiece(workpiece_id)
        with self.repository.workpiece_lock(workpiece_id):
            return self._update_component(workpiece_id, component_id, request)

    def delete_component(self, workpiece_id: str, component_id: str) -> WorkpieceRecord:
        """Remove one disconnected STL shell and rebuild the workpiece metadata."""
        workpiece = self.get_workpiece(workpiece_id)
        if workpiece.cad_format is not CadFormat.STL:
            raise ValueError("只有 STL 工件支持删除组件；STEP/BREP 请重新导入修改后的文件")
        if len(workpiece.components) <= 1:
            raise ValueError("至少保留一个组件；如需替换整个几何，请删除项目后重新导入 STL")
        if self.repository.list_studies(workpiece_id=workpiece_id):
            raise ValueError("该工件已有仿真研究，不能破坏几何历史；请重新导入 STL 建立新工件")
        component_index = next(
            (index for index, item in enumerate(workpiece.components)
             if item.component_id == component_id),
            None,
        )
        if component_index is None:
            raise ValueError("工件中不存在该组件")

        with self.repository.workpiece_lock(workpiece_id):
            current = self.repository.get_workpiece(workpiece_id)
            source_path = self.repository.workpiece_dir(workpiece_id) / "source.stl"
            normalized_path = self.repository.workpiece_dir(workpiece_id) / "source_mm.stl"
            raw_temp = source_path.with_name("source.stl.delete.tmp")
            normalized_temp = normalized_path.with_name("source_mm.stl.delete.tmp")
            try:
                raw_payload = remove_stl_component(source_path, component_index)
                raw_temp.write_bytes(raw_payload)
                if current.unit_confirmed:
                    normalized_mesh = load_stl_mesh(raw_temp)
                    normalized_mesh.apply_scale(current.length_unit.scale_to_mm)
                    normalized_temp.write_bytes(normalized_mesh.export(file_type="stl"))
                os.replace(raw_temp, source_path)
                if current.unit_confirmed:
                    os.replace(normalized_temp, normalized_path)
            finally:
                raw_temp.unlink(missing_ok=True)
                normalized_temp.unlink(missing_ok=True)

            inspection = inspect_stl(source_path)
            if not inspection.geometry.available or inspection.dimensions_mm is None:
                raise ValueError("删除组件后 STL 几何检查失败，请重新导入完整文件")
            old_names = {item.component_id: item.name for item in current.components}
            components = [
                item.model_copy(update={"name": old_names.get(item.component_id, item.name)})
                for item in inspection.components
            ]
            summary = dict(inspection.geometry.summary)
            summary["components"] = [item.model_dump(mode="json") for item in components]
            geometry = inspection.geometry.model_copy(update={"summary": summary})
            dimensions = inspection.dimensions_mm
            dimensions_mm = None
            if current.unit_confirmed:
                scale = current.length_unit.scale_to_mm
                geometry = geometry.model_copy(update={
                    "summary": _scale_geometry_summary(geometry.summary, scale, current.length_unit),
                    "faces": _scale_geometry_faces(geometry.faces, scale),
                })
                dimensions_mm = dimensions.model_copy(
                    update={"x": dimensions.x * scale, "y": dimensions.y * scale, "z": dimensions.z * scale}
                )
            content_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
            geometry, regions = _attach_geometry_regions(geometry, content_sha256, components)
            updated = current.model_copy(update={
                "content_sha256": content_sha256,
                "size_bytes": len(source_path.read_bytes()),
                "source_dimensions": dimensions,
                "dimensions_mm": dimensions_mm,
                "geometry": geometry,
                "components": components,
                "regions": regions,
            })
            self.repository.save_workpiece(updated)
            if updated.project_id is not None:
                self._touch_project(updated.project_id, updated.workpiece_id)
            return updated

    def _update_component(self, workpiece_id, component_id, request) -> WorkpieceRecord:
        workpiece = self.repository.get_workpiece(workpiece_id)
        component = next(
            (
                item
                for item in workpiece.components
                if item.component_id == component_id
            ),
            None,
        )
        if component is None:
            raise ValueError("工件中不存在该组件")
        if any(
            item.component_id != component_id
            and item.name.casefold() == request.name.casefold()
            for item in workpiece.components
        ):
            raise ValueError("同一工件中的组件名称不能重复")
        components = [
            item.model_copy(update={"name": request.name})
            if item.component_id == component_id
            else item
            for item in workpiece.components
        ]
        regions = [
            region.model_copy(update={"name": f"{request.name}外表面"})
            if region.kind == "component_surface"
            and component_id in region.component_ids
            else region
            for region in workpiece.regions
        ]
        summary = dict(workpiece.geometry.summary)
        if isinstance(summary.get("components"), list):
            summary["components"] = [
                {**item, "name": request.name}
                if isinstance(item, dict) and item.get("component_id") == component_id
                else item
                for item in summary["components"]
            ]
        updated = workpiece.model_copy(
            update={
                "components": components,
                "regions": regions,
                "geometry": workpiece.geometry.model_copy(update={"summary": summary}),
            }
        )
        self.repository.save_workpiece(updated)
        project = self._project_for_workpiece(updated)
        for study in self.repository.list_studies(workpiece_id=workpiece_id):
            if (
                study.status is StudyStatus.NEEDS_INPUT
                and study.plan is not None
                and study.input_snapshot_sha256 is None
            ):
                spec = build_simulation_spec(
                    project=project,
                    workpiece=updated,
                    plan=study.plan,
                    geometry_snapshot_sha256=_geometry_snapshot_sha256(updated),
                )
                self.repository.save_study(
                    study.model_copy(
                        update={
                            "updated_at": datetime.now(timezone.utc),
                            "simulation_spec": spec,
                        }
                    )
                )
        self._touch_project(project.project_id, workpiece_id)
        return updated

    def get_project_workspace(self, project_id: str) -> ProjectWorkspace:
        project = self.repository.get_project(project_id)
        return ProjectWorkspace(
            project=project,
            workpieces=self.list_workpieces(project_id=project_id),
            studies=self.repository.list_studies(project_id=project_id),
        )

    def register_box(self, request: BoxWorkpieceInput) -> WorkpieceRecord:
        project = self._resolve_project(request.project_id, request.name)
        workpiece_id = _new_id("wp")
        directory = self.repository.workpiece_dir(workpiece_id)
        content_sha256 = box_content_hash(request.name, request.dimensions_mm)
        inspection = self.geometry.inspect_box(
            request.dimensions_mm,
            preview_path=directory / "preview.glb",
        )
        inspection, regions = _attach_geometry_regions(
            inspection,
            content_sha256,
            [],
        )
        record = WorkpieceRecord(
            workpiece_id=workpiece_id,
            project_id=project.project_id,
            kind=WorkpieceKind.BOX,
            name=request.name,
            content_sha256=content_sha256,
            dimensions_mm=request.dimensions_mm,
            source_dimensions=request.dimensions_mm,
            length_unit=LengthUnit.MILLIMETER,
            unit_confirmed=True,
            geometry=inspection,
            regions=regions,
        )
        self.repository.save_workpiece(record)
        self._attach_workpiece(project, record.workpiece_id)
        return record

    def register_file(
        self,
        filename: str,
        payload: bytes,
        project_id: str | None = None,
    ) -> WorkpieceRecord:
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("必须提供有效的 CAD 文件名")
        project = (
            self.repository.get_project(project_id)
            if project_id is not None
            else None
        )
        cad_format = _cad_format(safe_name)
        workpiece_id = _new_id("wp")
        directory = self.repository.workpiece_dir(workpiece_id)
        directory.mkdir(parents=True, exist_ok=True)
        stored_name = f"source.{cad_format.value}"
        stored_path = directory / stored_name
        temporary = stored_path.with_suffix(stored_path.suffix + ".tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, stored_path)
        content_sha256 = hashlib.sha256(payload).hexdigest()
        if cad_format is CadFormat.STL:
            stl_inspection = inspect_stl(stored_path)
            inspection = stl_inspection.geometry
            source_dimensions = stl_inspection.dimensions_mm
            dimensions = None
            components = stl_inspection.components
        else:
            inspection = self.geometry.inspect_file(
                stored_path,
                cad_format,
                preview_path=directory / "preview.glb",
            )
            dimensions = None
            source_dimensions = None
            components = []
        inspection, regions = _attach_geometry_regions(
            inspection,
            content_sha256,
            components,
        )
        if project is None:
            project = self.create_project(ProjectCreateRequest(name=Path(safe_name).stem))
        record = WorkpieceRecord(
            workpiece_id=workpiece_id,
            project_id=project.project_id,
            kind=WorkpieceKind.CAD_FILE,
            name=Path(safe_name).stem,
            content_sha256=content_sha256,
            dimensions_mm=dimensions,
            cad_format=cad_format,
            original_filename=safe_name,
            stored_filename=stored_name,
            size_bytes=len(payload),
            geometry=inspection,
            source_dimensions=source_dimensions,
            unit_confirmed=cad_format is not CadFormat.STL,
            components=components,
            regions=regions,
        )
        self.repository.save_workpiece(record)
        self._attach_workpiece(project, record.workpiece_id)
        return record

    def register_stl(
        self,
        filename: str,
        payload: bytes,
        project_id: str | None = None,
    ) -> WorkpieceRecord:
        if Path(filename).suffix.lower() != ".stl":
            raise ValueError("该接口只接受扩展名为 .stl 的文件")
        return self.register_file(filename, payload, project_id)

    def confirm_workpiece_unit(self, workpiece_id: str, unit: LengthUnit) -> WorkpieceRecord:
        self.get_workpiece(workpiece_id)
        with self.repository.workpiece_lock(workpiece_id):
            return self._confirm_workpiece_unit(workpiece_id, unit)

    def _confirm_workpiece_unit(self, workpiece_id, unit) -> WorkpieceRecord:
        workpiece = self.repository.get_workpiece(workpiece_id)
        if workpiece.cad_format is not CadFormat.STL or workpiece.source_dimensions is None:
            raise ValueError("只有已解析的 STL 工件需要确认长度单位")
        existing_studies = self.repository.list_studies(workpiece_id=workpiece_id)
        if existing_studies:
            raise ValueError("该工件已经存在研究，不能再修改几何尺度；请重新导入 STL")

        scale = unit.scale_to_mm
        source_path = self.repository.workpiece_dir(workpiece_id) / "source.stl"
        normalized_path = self.repository.workpiece_dir(workpiece_id) / "source_mm.stl"
        mesh = load_stl_mesh(source_path)
        mesh.apply_scale(scale)
        normalized_path.write_bytes(mesh.export(file_type="stl"))

        summary = _scale_geometry_summary(workpiece.geometry.summary, scale, unit)
        faces = [
            {
                **face,
                **(
                    {
                        "coordinate_mm": float(
                            face.get("coordinate_source", face.get("coordinate_mm"))
                        )
                        * scale
                    }
                    if "coordinate_source" in face or "coordinate_mm" in face
                    else {}
                ),
                **(
                    {
                        "bounding_area_mm2": float(
                            face.get("bounding_area_source2", face.get("bounding_area_mm2"))
                        )
                        * scale**2
                    }
                    if "bounding_area_source2" in face or "bounding_area_mm2" in face
                    else {}
                ),
            }
            for face in workpiece.geometry.faces
        ]
        now = datetime.now(timezone.utc)
        dimensions = workpiece.source_dimensions
        confirmed = workpiece.model_copy(
            update={
                "dimensions_mm": dimensions.model_copy(
                    update={
                        "x": dimensions.x * scale,
                        "y": dimensions.y * scale,
                        "z": dimensions.z * scale,
                    }
                ),
                "stored_filename": "source_mm.stl",
                "length_unit": unit,
                "unit_confirmed": True,
                "unit_confirmed_at": now,
                "geometry": workpiece.geometry.model_copy(update={"summary": summary, "faces": faces}),
            }
        )
        self.repository.save_workpiece(confirmed)
        if confirmed.project_id is not None:
            self._touch_project(confirmed.project_id, confirmed.workpiece_id)
        return confirmed

    def create_study(
        self,
        workpiece_id: str,
        overrides: SimulationOverrides | None = None,
        purpose: str = "",
        require_confirmation: bool = False,
    ) -> StudyRecord:
        workpiece = self.get_workpiece(workpiece_id)
        project = self._project_for_workpiece(workpiece)
        study_id = _new_id("study")
        if workpiece.cad_format is CadFormat.STL and not workpiece.unit_confirmed:
            raise ValueError("请先确认 STL 的长度单位和实际尺寸")
        if workpiece.kind is WorkpieceKind.CAD_FILE and not workpiece.geometry.available:
            record = StudyRecord(
                study_id=study_id,
                workpiece_id=workpiece_id,
                project_id=project.project_id,
                status=StudyStatus.REJECTED,
                overrides=overrides,
                failure=("上传几何必须先通过检查，GPT 才能生成可执行的仿真设置。"),
            )
            self.repository.save_study(record)
            return record

        overrides, unsupported_physics = _merge_description_overrides(purpose, overrides)
        feedback: list[str] = []
        decision = None
        policy = None
        for attempt in range(1, 3):
            decision = self.planner.plan(workpiece, feedback, attempt, purpose)
            plan = apply_user_overrides(decision.plan, overrides)
            assignments = plan.component_materials or [
                ComponentMaterialAssignment(
                    component_id=component.component_id,
                    material_id=_catalog_material_id(plan.material),
                    material=plan.material,
                )
                for component in workpiece.components
            ]
            assignments = [
                assignment.model_copy(
                    update={"material_id": _catalog_material_id(assignment.material)}
                )
                if assignment.material_id is None
                else assignment
                for assignment in assignments
            ]
            missing_information = _draft_missing_information(overrides) if require_confirmation else []
            confirmation = ConfirmationRecord(
                status="needs_input" if require_confirmation else "confirmed",
                confirmed_by=None if require_confirmation else "API 调用方",
                confirmed_at=None if require_confirmation else datetime.now(timezone.utc),
            )
            plan = plan.model_copy(
                update={
                    "purpose": purpose.strip(),
                    "component_materials": assignments,
                    "missing_information": list(dict.fromkeys([*plan.missing_information, *missing_information])),
                    "unsupported_physics": list(dict.fromkeys([*plan.unsupported_physics, *unsupported_physics])),
                    "confirmation": confirmation,
                }
            )
            decision = decision.__class__(plan=plan, provenance=decision.provenance)
            policy = validate_plan(workpiece, plan)
            if policy.accepted:
                break
            feedback = policy.errors
        assert decision is not None and policy is not None
        simulation_spec = build_simulation_spec(
            project=project,
            workpiece=workpiece,
            plan=decision.plan,
            geometry_snapshot_sha256=_geometry_snapshot_sha256(workpiece),
        )
        now = datetime.now(timezone.utc)
        record = StudyRecord(
            study_id=study_id,
            workpiece_id=workpiece_id,
            project_id=project.project_id,
            created_at=now,
            updated_at=now,
            status=(
                StudyStatus.NEEDS_INPUT
                if require_confirmation
                else StudyStatus.PLANNED
                if policy.accepted
                else StudyStatus.REJECTED
            ),
            overrides=overrides,
            plan=decision.plan,
            simulation_spec=simulation_spec,
            planner=decision.provenance,
            policy=policy,
            failure=None if policy.accepted else "自动补齐方案未通过确定性策略校验。",
            purpose=purpose.strip(),
            confirmation=confirmation,
            input_snapshot_sha256=(
                simulation_spec_sha256(simulation_spec)
                if policy.accepted and not require_confirmation
                else None
            ),
        )
        self.repository.save_study(record)
        self._touch_project(project.project_id, workpiece.workpiece_id)
        return record

    def confirm_study(
        self,
        study_id: str,
        request: StudyConfirmationRequest,
    ) -> StudyRecord:
        with self.repository.study_lock(study_id):
            self._assert_task_owner(study_id, None)
            return self._confirm_study(study_id, request)

    def _confirm_study(self, study_id: str, request: StudyConfirmationRequest) -> StudyRecord:
        study = self.repository.get_study(study_id)
        if request.expected_revision is not None and request.expected_revision != study.draft_revision:
            raise ValueError("草案已更新，请重新检查当前输入后确认")
        if study.confirmation.status == "confirmed":
            raise ValueError("已确认研究不能直接改写，请复制后修改并重新确认")
        if study.modeling.proposal is not None:
            raise ValueError("请先应用或放弃待处理的建模建议，再确认输入")
        if study.status not in {StudyStatus.NEEDS_INPUT, StudyStatus.PLANNED} or study.plan is None:
            raise ValueError("只有尚未求解的草案可以确认")
        if not request.materials_confirmed:
            raise ValueError("请明确确认每个组件的材料及热物性后再确认仿真输入")
        workpiece = self.get_workpiece(study.workpiece_id)
        plan = apply_user_overrides(study.plan, request.overrides)
        purpose = request.purpose.strip() if request.purpose is not None else study.purpose
        confirmation = ConfirmationRecord(
            status="confirmed",
            confirmed_by=request.confirmed_by.strip(),
            confirmed_at=datetime.now(timezone.utc),
        )
        plan = plan.model_copy(
            update={
                "purpose": purpose,
                "missing_information": [],
                "confirmation": confirmation,
            }
        )
        policy = validate_plan(workpiece, plan)
        if not policy.accepted:
            raise ValueError("确认后的仿真方案未通过校验：" + "；".join(policy.errors))
        project = self._project_for_workpiece(workpiece)
        simulation_spec = build_simulation_spec(
            project=project,
            workpiece=workpiece,
            plan=plan,
            geometry_snapshot_sha256=_geometry_snapshot_sha256(workpiece),
        )
        confirmed = study.model_copy(
            update={
                "updated_at": datetime.now(timezone.utc),
                "status": StudyStatus.READY,
                "draft_revision": study.draft_revision + 1,
                "modeling": study.modeling.model_copy(update={"undo_plan": None, "undo_suggested_fields": []}),
                "overrides": request.overrides or study.overrides,
                "plan": plan,
                "project_id": project.project_id,
                "simulation_spec": simulation_spec,
                "policy": policy,
                "purpose": purpose,
                "confirmation": confirmation,
                "input_snapshot_sha256": simulation_spec_sha256(simulation_spec),
                "mesh_status": MeshStatus.NOT_GENERATED,
                "mesh_snapshot_sha256": None,
                "mesh_failure": None,
                "failure": None,
            }
        )
        self.repository.save_study(confirmed)
        self._touch_project(project.project_id, workpiece.workpiece_id)
        return confirmed

    def generate_mesh(self, study_id: str) -> MeshRecord:
        return self.execute_mesh(study_id)

    def execute_mesh(self, study_id: str, *, task_id: str | None = None,
                     monitor: ExecutionMonitor | None = None) -> MeshRecord:
        with self.repository.study_lock(study_id):
            self._assert_task_owner(study_id, task_id)
            return self._generate_mesh(study_id, monitor or UnmonitoredExecution())

    def _assert_task_owner(self, study_id: str, task_id: str | None) -> None:
        if self.repository.get_study(study_id).active_task_id != task_id:
            raise ValueError("该研究已被计算任务占用，请等待完成或取消任务")

    def _generate_mesh(self, study_id: str, monitor: ExecutionMonitor) -> MeshRecord:
        monitor.report("validating", 0)
        study = self.repository.get_study(study_id)
        if study.status not in {StudyStatus.PLANNED, StudyStatus.READY}:
            raise ValueError("只有尚未求解且输入已确认的研究可以生成网格")
        if study.confirmation.status != "confirmed" or study.plan is None or study.policy is None:
            raise ValueError("请先确认仿真输入，再生成网格")
        if not study.policy.accepted:
            raise ValueError("仿真方案未通过确定性校验，不能生成网格")
        workpiece = self.get_workpiece(study.workpiece_id)
        from .regions import assert_confirmed_regions
        assert_confirmed_regions(study, workpiece)
        if study.plan.solver.backend != "voxel_stl_v1":
            raise ValueError("当前研究使用解析求解器，不需要生成体素网格")

        plan_snapshot = _study_input_snapshot_sha256(study)
        if study.input_snapshot_sha256 != plan_snapshot:
            raise ValueError("当前仿真方案与用户确认的输入快照不一致，请重新确认")
        generating = study.model_copy(
            update={
                "updated_at": datetime.now(timezone.utc),
                "mesh_status": MeshStatus.GENERATING,
                "mesh_snapshot_sha256": None,
                "mesh_failure": None,
            }
        )
        self.repository.save_study(generating)
        try:
            mesh = generate_voxel_mesh_record(
                study_id=study.study_id,
                workpiece=workpiece,
                plan=study.plan,
                plan_snapshot_sha256=plan_snapshot,
                geometry_snapshot_sha256=_geometry_snapshot_sha256(workpiece),
                pitch_mm=float(study.policy.derived["effective_pitch_mm"]),
                source_path=self.repository.workpiece_dir(workpiece.workpiece_id)
                / (workpiece.stored_filename or "generated-box.stl"),
                artifact_dir=self.repository.study_dir(study.study_id) / "artifacts",
                progress=monitor.report,
            )
            mesh_status = (
                MeshStatus.BLOCKED
                if mesh.quality_status == "blocked"
                else MeshStatus.NEEDS_REVIEW
                if mesh.quality_status == "warning"
                else MeshStatus.READY
            )
            updated = generating.model_copy(
                update={
                    "updated_at": datetime.now(timezone.utc),
                    "mesh_status": mesh_status,
                    "mesh_snapshot_sha256": mesh.plan_snapshot_sha256,
                }
            )
            with monitor.publishing():
                self.repository.save_mesh(mesh)
                self.repository.save_study(updated)
            return mesh
        except Exception:
            failed = generating.model_copy(
                update={
                    "updated_at": datetime.now(timezone.utc),
                    "mesh_status": MeshStatus.FAILED,
                    "mesh_failure": "网格生成未完成，请检查几何和单元尺寸；确认输入已保留。",
                }
            )
            self.repository.save_study(failed)
            raise

    def confirm_mesh(self, study_id: str, request: MeshReviewRequest) -> MeshRecord:
        with self.repository.study_lock(study_id):
            self._assert_task_owner(study_id, None)
            return self._confirm_mesh(study_id, request)

    def _confirm_mesh(self, study_id: str, request: MeshReviewRequest) -> MeshRecord:
        study = self.repository.get_study(study_id)
        if study.mesh_status is not MeshStatus.NEEDS_REVIEW:
            raise ValueError("只有包含质量提示且尚未确认的网格可以确认风险")
        if study.plan is None:
            raise ValueError("研究缺少已确认的仿真方案")
        mesh = self.repository.get_mesh(study_id)
        plan_snapshot = _study_input_snapshot_sha256(study)
        if (
            mesh.quality_status != "warning"
            or mesh.review_status != "pending"
            or mesh.plan_snapshot_sha256 != plan_snapshot
            or study.input_snapshot_sha256 != plan_snapshot
        ):
            raise ValueError("网格风险记录与当前确认输入不一致，请重新生成")
        reviewed_at = datetime.now(timezone.utc)
        reviewed = mesh.model_copy(
            update={
                "review_status": "accepted",
                "reviewed_by": request.confirmed_by.strip(),
                "reviewed_at": reviewed_at,
            }
        )
        self.repository.save_mesh(reviewed)
        ready = study.model_copy(
            update={
                "updated_at": reviewed_at,
                "mesh_status": MeshStatus.READY,
                "mesh_failure": None,
            }
        )
        self.repository.save_study(ready)
        return reviewed

    def run_study(self, study_id: str) -> StudyRecord:
        return self.execute_study(study_id)

    def execute_study(self, study_id: str, *, task_id: str | None = None,
                      monitor: ExecutionMonitor | None = None) -> StudyRecord:
        with self.repository.study_lock(study_id):
            self._assert_task_owner(study_id, task_id)
            return self._run_study(study_id, monitor or UnmonitoredExecution())

    def _run_study(self, study_id: str, monitor: ExecutionMonitor) -> StudyRecord:
        monitor.report("validating", 0)
        study = self.repository.get_study(study_id)
        if study.status not in {StudyStatus.PLANNED, StudyStatus.READY}:
            raise ValueError(f"研究必须已准备求解；当前状态为 {study.status.value}")
        if study.status is StudyStatus.READY and study.confirmation.status != "confirmed":
            raise ValueError("仿真输入尚未由用户确认")
        if study.plan is None or study.policy is None:
            raise ValueError("研究没有可执行的仿真方案")
        workpiece = self.get_workpiece(study.workpiece_id)
        from .regions import assert_confirmed_regions
        assert_confirmed_regions(study, workpiece)
        mesh = None
        if study.plan.solver.backend == "voxel_stl_v1":
            if study.mesh_status is not MeshStatus.READY:
                raise ValueError("请先生成并检查真实网格，再开始求解")
            mesh = self.repository.get_mesh(study.study_id)
            plan_snapshot = _study_input_snapshot_sha256(study)
            if (
                mesh.quality_status == "blocked"
                or mesh.review_status not in {"not_required", "accepted"}
                or mesh.plan_snapshot_sha256 != plan_snapshot
                or study.mesh_snapshot_sha256 != plan_snapshot
                or study.input_snapshot_sha256 != plan_snapshot
            ):
                raise ValueError("网格与当前确认输入不一致或质量检查未通过，请重新生成")
            if mesh.geometry_snapshot_sha256 != _geometry_snapshot_sha256(workpiece):
                raise ValueError("网格对应的几何版本已变化，请重新生成")
            mesh_artifact = self.repository.artifact_path(
                study.study_id, mesh.artifacts[0].name
            )
            if hashlib.sha256(mesh_artifact.read_bytes()).hexdigest() != mesh.artifacts[0].sha256:
                raise ValueError("网格文件校验失败，请重新生成")
        running = study.model_copy(
            update={"status": StudyStatus.RUNNING, "updated_at": datetime.now(timezone.utc)}
        )
        self.repository.save_study(running)
        try:
            solver = self.solvers[study.plan.solver.backend]
            result = solver.solve(
                study_id=study.study_id,
                workpiece=workpiece,
                plan=study.plan,
                policy=study.policy,
                artifact_dir=self.repository.study_dir(study.study_id) / "artifacts",
                progress=monitor.report,
            )
            if mesh is not None and result.grid != mesh.grid:
                raise ValueError("求解器使用的网格与求解前预览不一致")
            result, evaluation_status, evaluation_summary = _evaluate_result(study.plan, result)
            completed = running.model_copy(
                update={
                    "status": StudyStatus.SUCCEEDED,
                    "updated_at": datetime.now(timezone.utc),
                    "evaluation_status": evaluation_status,
                    "evaluation_summary": evaluation_summary,
                }
            )
            with monitor.publishing():
                self.repository.save_result(result)
                self.repository.save_study(completed)
                if completed.project_id is not None:
                    self._touch_project(completed.project_id, completed.workpiece_id)
            return completed
        except Exception:
            failed = running.model_copy(
                update={
                    "status": StudyStatus.FAILED,
                    "updated_at": datetime.now(timezone.utc),
                    "failure": "数值计算未完成，请检查网格和边界条件；确认输入已保留。",
                }
            )
            self.repository.save_study(failed)
            raise

    def create_variant_study(
        self,
        base_study_id: str,
        overrides: SimulationOverrides,
    ) -> StudyRecord:
        """Create a policy-validated variant without asking the planner to start over."""
        base = self.repository.get_study(base_study_id)
        if base.plan is None:
            raise ValueError("基准研究没有可复用的仿真方案")
        confirmation = ConfirmationRecord(
            status="confirmed",
            confirmed_by="Agent 授权范围",
            confirmed_at=datetime.now(timezone.utc),
        )
        plan = apply_user_overrides(base.plan, overrides).model_copy(
            update={"confirmation": confirmation}
        )
        workpiece = self.get_workpiece(base.workpiece_id)
        project = self._project_for_workpiece(workpiece)
        policy = validate_plan(workpiece, plan)
        now = datetime.now(timezone.utc)
        simulation_spec = build_simulation_spec(
            project=project,
            workpiece=workpiece,
            plan=plan,
            geometry_snapshot_sha256=_geometry_snapshot_sha256(workpiece),
        )
        record = StudyRecord(
            study_id=_new_id("study"),
            source_study_id=base.study_id,
            workpiece_id=base.workpiece_id,
            project_id=project.project_id,
            created_at=now,
            updated_at=now,
            status=StudyStatus.PLANNED if policy.accepted else StudyStatus.REJECTED,
            overrides=overrides,
            plan=plan,
            simulation_spec=simulation_spec,
            planner=base.planner,
            policy=policy,
            failure=None if policy.accepted else "Agent 候选方案未通过确定性策略校验。",
            purpose=base.purpose,
            confirmation=confirmation,
            input_snapshot_sha256=simulation_spec_sha256(simulation_spec),
        )
        self.repository.save_study(record)
        self._touch_project(project.project_id, workpiece.workpiece_id)
        return record

    def copy_study(self, study_id: str, request: StudyCopyRequest) -> StudyRecord:
        base = self.repository.get_study(study_id)
        if base.plan is None:
            raise ValueError("来源研究没有可复制的仿真方案")
        workpiece = self.get_workpiece(base.workpiece_id)
        project = self._project_for_workpiece(workpiece)
        purpose = request.purpose.strip() if request.purpose is not None else base.purpose
        confirmation = ConfirmationRecord(status="needs_input")
        plan = apply_user_overrides(base.plan, request.overrides).model_copy(
            update={
                "study_name": request.name or _copy_study_name(base.plan.study_name),
                "purpose": purpose,
                "missing_information": [],
                "confirmation": confirmation,
            }
        )
        policy = validate_plan(workpiece, plan)
        simulation_spec = build_simulation_spec(
            project=project,
            workpiece=workpiece,
            plan=plan,
            geometry_snapshot_sha256=_geometry_snapshot_sha256(workpiece),
        )
        now = datetime.now(timezone.utc)
        copied = StudyRecord(
            study_id=_new_id("study"),
            source_study_id=base.study_id,
            workpiece_id=base.workpiece_id,
            project_id=project.project_id,
            created_at=now,
            updated_at=now,
            status=StudyStatus.NEEDS_INPUT if policy.accepted else StudyStatus.REJECTED,
            overrides=request.overrides,
            plan=plan,
            simulation_spec=simulation_spec,
            planner=base.planner,
            policy=policy,
            failure=None if policy.accepted else "复制后的方案未通过确定性策略校验。",
            purpose=purpose,
            confirmation=confirmation,
        )
        self.repository.save_study(copied)
        self._touch_project(project.project_id, workpiece.workpiece_id)
        return copied

    def compare_studies(self, request: StudyComparisonRequest) -> StudyComparisonResponse:
        studies = [self.repository.get_study(study_id) for study_id in request.study_ids]
        for index, study in enumerate(studies):
            if study.project_id is None:
                workpiece = self.get_workpiece(study.workpiece_id)
                project = self._project_for_workpiece(workpiece)
                study = study.model_copy(update={"project_id": project.project_id})
                self.repository.save_study(study)
                studies[index] = study
        project_ids = {study.project_id for study in studies}
        if len(project_ids) != 1 or None in project_ids:
            raise ValueError("只能比较同一项目中的研究")
        if any(study.status is not StudyStatus.SUCCEEDED for study in studies):
            raise ValueError("只能比较已经求解完成的研究")
        baseline_id = request.baseline_study_id or request.study_ids[0]
        indexed_studies = {study.study_id: study for study in studies}
        results = {
            study.study_id: self.repository.get_result(study.study_id) for study in studies
        }
        baseline_study = indexed_studies[baseline_id]
        baseline_result = results[baseline_id]
        workpieces = {
            study.workpiece_id: self.get_workpiece(study.workpiece_id)
            for study in studies
        }
        summaries = [
            _comparison_study_summary(
                study,
                results[study.study_id],
                workpieces[study.workpiece_id],
            )
            for study in studies
        ]
        differences = []
        for candidate in studies:
            if candidate.study_id == baseline_id:
                continue
            result = results[candidate.study_id]
            field, unavailable_reason = _temperature_difference_field(
                baseline_study,
                baseline_result,
                candidate,
                result,
            )
            differences.append(
                StudyDifferenceSummary(
                    candidate_study_id=candidate.study_id,
                    candidate_study_name=(
                        candidate.plan.study_name if candidate.plan is not None else "仿真研究"
                    ),
                    temperature_min_delta=_quantity(
                        result.temperature_min_k - baseline_result.temperature_min_k,
                        "K",
                    ),
                    temperature_max_delta=_quantity(
                        result.temperature_max_k - baseline_result.temperature_max_k,
                        "K",
                    ),
                    heat_rate_delta=_quantity(
                        result.heat_rate_w - baseline_result.heat_rate_w,
                        "W",
                    ),
                    thermal_resistance_delta=_quantity(
                        result.thermal_resistance_k_w
                        - baseline_result.thermal_resistance_k_w,
                        "K/W",
                    ) if result.thermal_resistance_k_w is not None and baseline_result.thermal_resistance_k_w is not None else None,
                    field=field,
                    field_unavailable_reason=unavailable_reason,
                )
            )
        minimum = min(result.temperature_min_k for result in results.values())
        maximum = max(result.temperature_max_k for result in results.values())
        return StudyComparisonResponse(
            project_id=next(iter(project_ids)),
            baseline_study_id=baseline_id,
            common_scale=ComparisonScale(
                minimum=_quantity(minimum, "K"),
                maximum=_quantity(maximum, "K"),
            ),
            studies=summaries,
            differences=differences,
        )

    def get_simulation_spec(self, study_id: str) -> SimulationSpec:
        study = self.repository.get_study(study_id)
        if study.plan is None:
            raise ValueError("该研究没有可读取的仿真规格")
        if study.simulation_spec is not None:
            validate_spec_plan_alignment(study.simulation_spec, study.plan)
            return study.simulation_spec
        workpiece = self.get_workpiece(study.workpiece_id)
        project = self._project_for_workpiece(workpiece)
        return build_simulation_spec(
            project=project,
            workpiece=workpiece,
            plan=study.plan,
            geometry_snapshot_sha256=_geometry_snapshot_sha256(workpiece),
        )

    def _resolve_project(self, project_id: str | None, default_name: str) -> ProjectRecord:
        if project_id is not None:
            return self.repository.get_project(project_id)
        return self.create_project(ProjectCreateRequest(name=default_name))

    def _attach_workpiece(self, project: ProjectRecord, workpiece_id: str) -> ProjectRecord:
        workpiece_ids = list(project.workpiece_ids)
        if workpiece_id not in workpiece_ids:
            workpiece_ids.append(workpiece_id)
        updated = project.model_copy(
            update={
                "workpiece_ids": workpiece_ids,
                "active_workpiece_id": workpiece_id,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self.repository.save_project(updated)
        return updated

    def _project_for_workpiece(self, workpiece: WorkpieceRecord) -> ProjectRecord:
        if workpiece.project_id is not None:
            return self.repository.get_project(workpiece.project_id)
        project = self.create_project(ProjectCreateRequest(name=workpiece.name))
        migrated_workpiece = workpiece.model_copy(update={"project_id": project.project_id})
        self.repository.save_workpiece(migrated_workpiece)
        self._attach_workpiece(project, workpiece.workpiece_id)
        for study in self.repository.list_studies(workpiece_id=workpiece.workpiece_id):
            self.repository.save_study(study.model_copy(update={"project_id": project.project_id}))
        for agent_run in self.repository.list_agent_runs(workpiece_id=workpiece.workpiece_id):
            self.repository.save_agent_run(
                agent_run.model_copy(update={"project_id": project.project_id})
            )
        return self.repository.get_project(project.project_id)

    def _touch_project(self, project_id: str, workpiece_id: str | None = None) -> None:
        project = self.repository.get_project(project_id)
        active_workpiece_id = workpiece_id or project.active_workpiece_id
        updated = project.model_copy(
            update={
                "active_workpiece_id": active_workpiece_id,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self.repository.save_project(updated)

    def _ensure_workpiece_regions(self, workpiece: WorkpieceRecord) -> WorkpieceRecord:
        if workpiece.regions and all(
            isinstance(face.get("region_id"), str) for face in workpiece.geometry.faces
        ) and all(
            region.kind == "native_face" or set(SURFACE_CONDITION_KINDS).issubset(region.supported_condition_kinds)
            for region in workpiece.regions
        ):
            return workpiece
        with self.repository.workpiece_lock(workpiece.workpiece_id):
            return self._migrate_workpiece_regions(self.repository.get_workpiece(workpiece.workpiece_id))

    def _migrate_workpiece_regions(self, workpiece: WorkpieceRecord) -> WorkpieceRecord:
        if workpiece.regions and all(isinstance(face.get("region_id"), str) for face in workpiece.geometry.faces):
            migrated = workpiece.model_copy(update={"regions": [
                region.model_copy(update={"supported_condition_kinds": SURFACE_CONDITION_KINDS})
                if region.kind != "native_face"
                else region for region in workpiece.regions
            ]})
            self.repository.save_workpiece(migrated)
            return migrated
        geometry = workpiece.geometry
        if (
            not geometry.faces
            and workpiece.kind is WorkpieceKind.BOX
            and workpiece.dimensions_mm is not None
        ):
            geometry = geometry.model_copy(
                update={"faces": _box_boundary_faces(workpiece.dimensions_mm)}
            )
        geometry, regions = _attach_geometry_regions(
            geometry,
            workpiece.content_sha256,
            workpiece.components,
        )
        migrated = workpiece.model_copy(
            update={"geometry": geometry, "regions": [*regions, *[
                region for region in workpiece.regions if region.kind == "surface_patch"
            ]]}
        )
        self.repository.save_workpiece(migrated)
        return migrated


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _attach_geometry_regions(
    geometry: GeometryInspection,
    content_sha256: str,
    components: list[GeometryComponent],
) -> tuple[GeometryInspection, list[GeometryRegion]]:
    faces: list[dict[str, object]] = []
    regions: list[GeometryRegion] = []
    face_selectors = {selector.value for selector in FaceSelector}
    for index, face in enumerate(geometry.faces):
        selector = str(face.get("selector") or f"entity/face/{index}")
        kind = "bounding_plane" if selector in face_selectors else "native_face"
        region_id = _stable_region_id(content_sha256, kind, selector)
        component_ids = _components_touching_face(face, selector, components)
        faces.append({**face, "region_id": region_id})
        regions.append(
            GeometryRegion(
                region_id=region_id,
                name=_region_name(selector, index),
                kind=kind,
                selector=selector,
                component_ids=component_ids,
                supported_condition_kinds=(
                    SURFACE_CONDITION_KINDS if kind == "bounding_plane" else []
                ),
            )
        )
    for component in components:
        selector = f"component.{component.component_id}.surface"
        regions.append(
            GeometryRegion(
                region_id=_stable_region_id(
                    content_sha256,
                    "component_surface",
                    selector,
                ),
                name=f"{component.name}外表面",
                kind="component_surface",
                selector=selector,
                component_ids=[component.component_id],
                supported_condition_kinds=SURFACE_CONDITION_KINDS,
            )
        )
    return geometry.model_copy(update={"faces": faces}), regions


def _stable_region_id(content_sha256: str, kind: str, selector: str) -> str:
    payload = f"{content_sha256}\0{kind}\0{selector}".encode()
    return f"region-{hashlib.sha256(payload).hexdigest()[:12]}"


def _components_touching_face(
    face: dict[str, object],
    selector: str,
    components: list[GeometryComponent],
) -> list[str]:
    if not components or selector not in {item.value for item in FaceSelector}:
        return []
    axis = selector[5]
    side = selector[6:]
    axis_index = {"x": 0, "y": 1, "z": 2}[axis]
    coordinate = face.get("coordinate_source", face.get("coordinate_mm"))
    if coordinate is None:
        values = [
            component.bbox_source[axis_index if side == "min" else axis_index + 3]
            for component in components
        ]
        coordinate = min(values) if side == "min" else max(values)
    target = float(coordinate)
    tolerance = max(1e-9, abs(target) * 1e-9)
    return [
        component.component_id
        for component in components
        if math.isclose(
            component.bbox_source[axis_index if side == "min" else axis_index + 3],
            target,
            rel_tol=0,
            abs_tol=tolerance,
        )
    ]


def _region_name(selector: str, index: int) -> str:
    labels = {
        "face.xmin": "X 最小侧边界层",
        "face.xmax": "X 最大侧边界层",
        "face.ymin": "Y 最小侧边界层",
        "face.ymax": "Y 最大侧边界层",
        "face.zmin": "Z 最小侧边界层",
        "face.zmax": "Z 最大侧边界层",
    }
    return labels.get(selector, f"几何面 {index + 1}")


def _box_boundary_faces(dimensions: DimensionsMM) -> list[dict[str, object]]:
    x, y, z = dimensions.as_tuple()
    return [
        {"selector": "face.xmin", "coordinate_mm": 0.0, "area_mm2": y * z},
        {"selector": "face.xmax", "coordinate_mm": x, "area_mm2": y * z},
        {"selector": "face.ymin", "coordinate_mm": 0.0, "area_mm2": x * z},
        {"selector": "face.ymax", "coordinate_mm": y, "area_mm2": x * z},
        {"selector": "face.zmin", "coordinate_mm": 0.0, "area_mm2": x * y},
        {"selector": "face.zmax", "coordinate_mm": z, "area_mm2": x * y},
    ]


def _copy_study_name(name: str) -> str:
    suffix = " 副本"
    return name[: 120 - len(suffix)].rstrip() + suffix


def _comparison_study_summary(
    study: StudyRecord,
    result: SimulationResult,
    workpiece: WorkpieceRecord,
) -> ComparisonStudySummary:
    samples = result.temperature_field_preview
    hotspot_samples = (
        samples.volume_samples or samples.samples
        if samples is not None
        else []
    )
    hottest = max(hotspot_samples, key=lambda item: item[3], default=None)
    hotspot = (
        SimulationPoint3D(
            x=_quantity(hottest[0], "mm"),
            y=_quantity(hottest[1], "mm"),
            z=_quantity(hottest[2], "mm"),
        )
        if hottest is not None
        else None
    )
    return ComparisonStudySummary(
        study_id=study.study_id,
        study_name=study.plan.study_name if study.plan is not None else "仿真研究",
        workpiece_id=study.workpiece_id,
        workpiece_name=workpiece.name,
        completed_at=result.completed_at,
        evaluation_status=result.evaluation_status,
        temperature_min=_quantity(result.temperature_min_k, "K"),
        temperature_max=_quantity(result.temperature_max_k, "K"),
        hotspot=hotspot,
        heat_rate=_quantity(result.heat_rate_w, "W"),
        thermal_resistance=_quantity(result.thermal_resistance_k_w, "K/W") if result.thermal_resistance_k_w is not None else None,
        energy_balance_relative_error=_quantity(
            result.energy_balance_relative_error,
            "1",
        ),
    )


def _temperature_difference_field(
    baseline_study: StudyRecord,
    baseline: SimulationResult,
    candidate_study: StudyRecord,
    candidate: SimulationResult,
) -> tuple[TemperatureDifferenceField | None, str | None]:
    if baseline.analysis_type != candidate.analysis_type or baseline.time_s != candidate.time_s:
        return None, "两个研究的分析类型或结果时刻不同，当前仅比较各研究末态指标，不生成空间差值。"
    if baseline_study.workpiece_id != candidate_study.workpiece_id:
        return None, "两个研究使用不同几何版本，只能比较指标，不能计算空间差值场。"
    if baseline.grid != candidate.grid:
        return None, "两个研究的求解网格不同，只能比较指标；空间差值需要相同网格。"
    base_field = baseline.temperature_field_preview
    candidate_field = candidate.temperature_field_preview
    if base_field is None or candidate_field is None:
        return None, "至少一个研究没有温度场预览，无法计算空间差值。"
    if not math.isclose(base_field.pitch_mm, candidate_field.pitch_mm, rel_tol=0, abs_tol=1e-12):
        return None, "两个研究的网格单元尺寸不同，无法逐点计算空间差值。"
    surface = _subtract_temperature_samples(base_field.samples, candidate_field.samples)
    if surface is None:
        return None, "两个研究的表面温度采样坐标不一致，无法逐点计算空间差值。"
    volume = _subtract_temperature_samples(
        base_field.volume_samples,
        candidate_field.volume_samples,
    )
    if volume is None:
        return None, "两个研究的内部温度采样坐标不一致，无法逐点计算空间差值。"
    deltas = [sample[3] for sample in [*surface, *volume]]
    return (
        TemperatureDifferenceField(
            pitch=_quantity(base_field.pitch_mm, "mm"),
            minimum_delta=_quantity(min(deltas), "K"),
            maximum_delta=_quantity(max(deltas), "K"),
            surface_samples=surface,
            volume_samples=volume,
        ),
        None,
    )


def _subtract_temperature_samples(
    baseline: list[tuple[float, float, float, float]],
    candidate: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]] | None:
    if len(baseline) != len(candidate):
        return None
    difference = []
    for base_sample, candidate_sample in zip(baseline, candidate, strict=True):
        if any(
            not math.isclose(base_sample[index], candidate_sample[index], rel_tol=0, abs_tol=1e-9)
            for index in range(3)
        ):
            return None
        difference.append(
            (
                base_sample[0],
                base_sample[1],
                base_sample[2],
                candidate_sample[3] - base_sample[3],
            )
        )
    return difference


def _quantity(value: float, unit: str) -> Quantity:
    return Quantity(value=float(value), unit=unit)


def _catalog_material_id(material: object) -> str | None:
    for entry in list_materials():
        if entry.material == material:
            return entry.material_id
    return None


def _plan_snapshot_sha256(plan: SimulationPlan) -> str:
    return simulation_plan_sha256(plan)


def _study_input_snapshot_sha256(study: StudyRecord) -> str:
    if study.plan is None:
        raise ValueError("研究缺少可执行的仿真方案")
    if study.simulation_spec is None:
        return _plan_snapshot_sha256(study.plan)
    validate_spec_plan_alignment(study.simulation_spec, study.plan)
    return simulation_spec_sha256(study.simulation_spec)


def _geometry_snapshot_sha256(workpiece: WorkpieceRecord) -> str:
    payload = {
        "content_sha256": workpiece.content_sha256,
        "stored_filename": workpiece.stored_filename,
        "length_unit": workpiece.length_unit.value if workpiece.length_unit else None,
        "dimensions_mm": (
            workpiece.dimensions_mm.model_dump(mode="json") if workpiece.dimensions_mm else None
        ),
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _cad_format(filename: str) -> CadFormat:
    extension = Path(filename).suffix.lower()
    mapping = {
        ".step": CadFormat.STEP,
        ".stp": CadFormat.STEP,
        ".brep": CadFormat.BREP,
        ".brp": CadFormat.BREP,
        ".stl": CadFormat.STL,
    }
    try:
        return mapping[extension]
    except KeyError as exc:
        raise ValueError("支持的 CAD 格式为 STEP、BREP 和 STL") from exc


def _scale_geometry_summary(summary: dict[str, object], scale: float, unit: LengthUnit) -> dict[str, object]:
    scaled = dict(summary)
    bbox = scaled.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 6:
        scaled["bbox"] = [float(value) * scale for value in bbox]
    if isinstance(scaled.get("area"), (int, float)):
        scaled["area"] = float(scaled["area"]) * scale**2
    if isinstance(scaled.get("volume"), (int, float)):
        scaled["volume"] = float(scaled["volume"]) * scale**3
    center = scaled.get("center_of_mass_source", scaled.get("center_of_mass_mm"))
    if isinstance(center, list) and len(center) == 3:
        scaled["center_of_mass_mm"] = [float(value) * scale for value in center]
    preview = scaled.get("preview")
    source_vertices = (
        preview.get("vertices_source", preview.get("vertices_mm"))
        if isinstance(preview, dict)
        else None
    )
    if isinstance(preview, dict) and isinstance(source_vertices, list):
        scaled["preview"] = {
            **preview,
            "vertices_mm": [
                [float(coordinate) * scale for coordinate in vertex]
                for vertex in source_vertices
            ],
        }
    scaled["coordinate_system"] = {
        "length_unit": "mm",
        "source_length_unit": unit.value,
        "scale_to_mm": scale,
        "up_axis": "+Z",
        "unit_basis": "用户确认",
    }
    return scaled


def _scale_geometry_faces(faces: list[dict[str, object]], scale: float) -> list[dict[str, object]]:
    scaled_faces: list[dict[str, object]] = []
    for face in faces:
        updated = dict(face)
        if "coordinate_source" in face or "coordinate_mm" in face:
            updated["coordinate_mm"] = float(
                face.get("coordinate_source", face.get("coordinate_mm"))
            ) * scale
        if "bounding_area_source2" in face or "bounding_area_mm2" in face:
            updated["bounding_area_mm2"] = float(
                face.get("bounding_area_source2", face.get("bounding_area_mm2"))
            ) * scale**2
        scaled_faces.append(updated)
    return scaled_faces


def _draft_missing_information(overrides: SimulationOverrides | None) -> list[str]:
    supplied = overrides.model_dump(exclude_none=True) if overrides else {}
    missing: list[str] = []
    if not {"material_name", "thermal_conductivity_w_m_k", "density_kg_m3", "specific_heat_j_kg_k"}.issubset(supplied):
        missing.append("材料及热物性为系统建议值，需要确认数据来源与适用温度范围。")
    if "heat_source_power_w" not in supplied and supplied.get("enable_heat_source") is not False:
        missing.append("热源功率为系统建议值，需要确认。")
    if "fixed_boundaries" not in supplied and not {"min_face_temperature_k", "max_face_temperature_k"}.issubset(supplied):
        missing.append("温度边界为系统建议值，需要确认边界位置与温度。")
    if supplied.get("enable_global_convection") is not False and ("ambient_temperature_k" not in supplied or "convection_coefficient_w_m2_k" not in supplied):
        missing.append("环境温度和对流换热系数为系统建议值，需要确认。")
    return missing


def _merge_description_overrides(
    description: str,
    explicit: SimulationOverrides | None,
) -> tuple[SimulationOverrides | None, list[str]]:
    values: dict[str, object] = {}
    text = description.strip()
    material_aliases = {
        "6061": "al-6061-t6",
        "铝合金": "al-6061-t6",
        "紫铜": "copper-c110",
        "铜": "copper-c110",
        "304": "steel-304",
        "不锈钢": "steel-304",
        "inconel 718": "inconel-718",
        "高温合金": "inconel-718",
    }
    catalog = {entry.material_id: entry.material for entry in list_materials()}
    lowered = text.lower()
    for alias, material_id in material_aliases.items():
        if alias in lowered:
            material = catalog[material_id]
            values.update(
                {
                    "material_name": material.name,
                    "thermal_conductivity_w_m_k": material.thermal_conductivity_w_m_k,
                    "density_kg_m3": material.density_kg_m3,
                    "specific_heat_j_kg_k": material.specific_heat_j_kg_k,
                }
            )
            break

    power = re.search(r"(?:功率|发热|热源)[^\d]{0,8}(\d+(?:\.\d+)?)\s*(?:w|瓦)", lowered)
    if power:
        values["heat_source_power_w"] = float(power.group(1))
    ambient = re.search(
        r"(?:环境|流体|空气)[^\d-]{0,8}(-?\d+(?:\.\d+)?)\s*(℃|°c|c|k)",
        lowered,
    )
    if ambient:
        ambient_value = float(ambient.group(1))
        values["ambient_temperature_k"] = (
            ambient_value if ambient.group(2) == "k" else ambient_value + 273.15
        )
    convection = re.search(
        r"(?:对流|换热系数)[^\d]{0,8}(\d+(?:\.\d+)?)\s*(?:w\s*/?\s*\(?m[²2]\s*[·*]?\s*k\)?|w/m2k)?",
        lowered,
    )
    if convection:
        values["convection_coefficient_w_m2_k"] = float(convection.group(1))

    if "瞬态" in lowered or "transient" in lowered:
        values["analysis_type"] = "transient_conduction"
    initial = re.search(r"初始(?:温度)?[^\d-]{0,8}(-?\d+(?:\.\d+)?)\s*(℃|°c|c|k)", lowered)
    if initial:
        values["initial_temperature_k"] = float(initial.group(1)) + (0 if initial.group(2) == "k" else 273.15)
    for field, prefix in (("duration_s", r"(?:持续|时长|运行时间)"),
                          ("time_step_s", r"(?:时间步长|步长)")):
        match = re.search(prefix + r"[^\d]{0,8}(\d+(?:\.\d+)?)\s*(秒|分钟|小时|s\b|min\b|h\b)", lowered)
        if match:
            values[field] = float(match.group(1)) * {"秒": 1, "s": 1, "分钟": 60, "min": 60, "小时": 3600, "h": 3600}[match.group(2)]

    maximum = re.search(
        r"(?:最高温度|峰值温度)[^\d-]{0,12}(?:不超过|低于|小于)?\s*(-?\d+(?:\.\d+)?)\s*(℃|°c|c|k)",
        lowered,
    )
    if maximum:
        target = float(maximum.group(1))
        if maximum.group(2) != "k":
            target += 273.15
        values["criteria"] = [
            {
                "metric": "max_temperature",
                "operator": "less_or_equal",
                "target": {"value": target, "unit": "K"},
            }
        ]

    unsupported: list[str] = []
    unsupported_terms = {
        "辐射": "辐射模型待确认：请填写区域、环境辐射温度、发射率及来源。",
        "视角因子": "表面间视角因子辐射尚未接入当前求解器。",
        "表面间辐射": "表面间视角因子辐射尚未接入当前求解器。",
        "自遮挡": "辐射自遮挡尚未接入当前求解器。",
        "参与介质": "参与介质辐射尚未接入当前求解器。",
        "应力": "热应力与形变尚未接入当前求解器。",
        "形变": "热应力与形变尚未接入当前求解器。",
        "压力": "机械载荷与结构响应尚未接入当前求解器。",
        "载荷": "机械载荷与结构响应尚未接入当前求解器。",
        "旋转": "旋转和离心载荷尚未接入当前求解器。",
        "离心": "旋转和离心载荷尚未接入当前求解器。",
        "塑性": "塑性本构尚未接入当前求解器。",
        "疲劳": "疲劳寿命模型尚未接入当前求解器。",
        "蠕变": "蠕变模型尚未接入当前求解器。",
        "摩擦": "机械接触与摩擦尚未接入当前求解器。",
        "各向异性": "各向异性材料模型尚未接入当前求解器。",
        "接触热阻": "组件接触热阻需要在结构化面板确认接触区域和热阻参数；未完成确认前不能评估。",
        "热接触": "组件接触热阻需要在结构化面板确认接触区域和热阻参数；未完成确认前不能评估。",
        "温度相关": "随温度变化的材料属性尚未接入当前求解器。",
        "随温度变化": "随温度变化的材料属性尚未接入当前求解器。",
        "复合材料": "复合材料和方向性尚未接入当前求解器。",
        "涂层": "多层与涂层模型尚未接入当前求解器。",
        "相变": "相变与潜热模型尚未接入当前求解器。",
        "流体": "CFD 与共轭传热尚未接入当前求解器。",
        "cfd": "CFD 与共轭传热尚未接入当前求解器。",
        "共轭传热": "CFD 与共轭传热尚未接入当前求解器。",
        "燃烧": "燃烧与共轭传热尚未接入当前求解器。",
    }
    for term, message in unsupported_terms.items():
        if term in text and message not in unsupported:
            unsupported.append(message)

    if explicit is not None:
        values.update(explicit.model_dump(exclude_none=True))
        if explicit.surface_conditions and any(c.kind == "radiation" for c in explicit.surface_conditions):
            unsupported = [item for item in unsupported if not item.startswith("辐射模型待确认：")]
    return (SimulationOverrides.model_validate(values) if values else None), unsupported


def _evaluate_result(
    plan: SimulationPlan,
    result: SimulationResult,
) -> tuple[SimulationResult, str, list[str]]:
    criteria = plan.criteria
    unsupported = plan.unsupported_physics
    material_range_issues = _material_range_issues(plan, result)
    if not criteria:
        summary = ["未设置工程判据；已有数值结果，但无法判断是否满足设计目标。"]
        summary.extend(material_range_issues)
        if unsupported:
            summary.append("本次简化求解未覆盖用户要求的部分物理效应，不能据此评估这些问题。")
        return result.model_copy(update={"evaluation_status": "indeterminate", "evaluation_summary": summary}), "indeterminate", summary
    values = {
        "max_temperature": result.temperature_max_over_time_k if result.time_steps else result.temperature_max_k,
        "min_temperature": result.temperature_min_over_time_k if result.time_steps else result.temperature_min_k,
        "energy_balance_error": result.maximum_energy_balance_error_over_time if result.time_steps else result.energy_balance_relative_error,
    }
    comparisons = {
        "less_than": lambda actual, target: actual < target,
        "less_or_equal": lambda actual, target: actual <= target,
        "greater_than": lambda actual, target: actual > target,
        "greater_or_equal": lambda actual, target: actual >= target,
    }
    labels = {
        "max_temperature": "最高温度",
        "min_temperature": "最低温度",
        "energy_balance_error": "能量相对误差",
    }
    passed = True
    summary: list[str] = []
    for criterion in criteria:
        actual = values[criterion.metric]
        criterion_passed = comparisons[criterion.operator](actual, criterion.target.value)
        passed = passed and criterion_passed
        summary.append(
            f"{labels[criterion.metric]} {actual:.6g} {criterion.target.unit}："
            f"{'满足' if criterion_passed else '不满足'}设定判据。"
        )
    if unsupported:
        summary.append("用户要求的部分物理效应未纳入计算，不能据此判定完整工程目标。")
    if result.time_steps:
        summary.append("温度与能量误差判据覆盖已计算的全部时间步，包含初始温度场。")
    summary.extend(material_range_issues)
    status = (
        "violates_criteria"
        if not passed
        else "indeterminate"
        if material_range_issues or unsupported
        else "meets_criteria"
    )
    return result.model_copy(update={"evaluation_status": status, "evaluation_summary": summary}), status, summary


def _material_range_issues(plan: SimulationPlan, result: SimulationResult) -> list[str]:
    issues: list[str] = []
    minimum = result.temperature_min_over_time_k if result.time_steps else result.temperature_min_k
    maximum = result.temperature_max_over_time_k if result.time_steps else result.temperature_max_k
    seen: set[tuple[str, str | None, float | None, float | None]] = set()
    for material in [plan.material, *(item.material for item in plan.component_materials)]:
        identity = (
            material.name,
            material.source_version,
            material.valid_temperature_min_k,
            material.valid_temperature_max_k,
        )
        if identity in seen:
            continue
        seen.add(identity)
        if (
            material.valid_temperature_min_k is None
            or material.valid_temperature_max_k is None
        ):
            issues.append(
                f"{material.name} 未提供完整的物性适用温度范围，无法确认本次材料参数是否需要外推。"
            )
            continue
        if minimum < material.valid_temperature_min_k:
            issues.append(
                f"最低温度 {minimum:.6g} K 低于 {material.name} 的物性数据范围下限 "
                f"{material.valid_temperature_min_k:.6g} K。"
            )
        if maximum > material.valid_temperature_max_k:
            issues.append(
                f"最高温度 {maximum:.6g} K 超出 {material.name} 的物性数据范围上限 "
                f"{material.valid_temperature_max_k:.6g} K。"
            )
    return issues


__all__ = ["PlannerUnavailableError", "StudyService"]
