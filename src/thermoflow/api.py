"""FastAPI surface: the client supplies a workpiece, GPT supplies the plan."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware

from .agent import SimulationAgent, build_agent_policy
from .cadflow_adapter import CadFlowGeometryInspector
from .materials import list_materials
from .modeling import ModelingService
from .models import (
    AgentGoalRequest,
    AgentRunRecord,
    BoxWorkpieceInput,
    ComponentUpdateRequest,
    DraftUpdateRequest,
    GeometryRegion,
    MaterialCatalogEntry,
    MeshRecord,
    MeshReviewRequest,
    ModelingDecisionRequest,
    ModelingMessageRequest,
    PolicyReport,
    ProjectCreateRequest,
    ProjectRecord,
    ProjectUpdateRequest,
    ProjectWorkspace,
    SimulationOverrides,
    SimulationResult,
    SimulationSpec,
    StlSimulationResponse,
    StudyComparisonRequest,
    StudyComparisonResponse,
    StudyConfirmationRequest,
    StudyCopyRequest,
    StudyCreateRequest,
    StudyRecord,
    TaskCreateRequest,
    TaskRecord,
    ThermalTimeFrame,
    WorkpieceRecord,
    WorkpieceUnitConfirmation,
)
from .planner import PlannerUnavailableError, SimulationPlanner, build_planner
from .policy import validate_plan
from .regions import SurfaceRegionRequest, create_surface_region
from .service import StudyService
from .settings import Settings
from .storage import FileRepository, RecordNotFoundError
from .tasks import TaskManager
from .visualization import (
    CellProbe,
    EngineeringPlayback,
    EngineeringSurface,
    ViewRequest,
    geometry_surface,
    probe_cell,
    study_playback,
    study_surface,
)

OPENAPI_TAGS = [
    {
        "name": "系统",
        "description": "检查服务状态、规划模式和 CadFlow 连接情况。",
    },
    {
        "name": "项目",
        "description": "管理几何版本以及归属于同一工程项目的全部研究历史。",
    },
    {
        "name": "工件",
        "description": "注册长方体工件，或上传 STEP、BREP、STL 格式的 CAD 工件。",
    },
    {
        "name": "仿真研究",
        "description": (
            "生成、确认和运行热仿真研究。结构化草案必须由用户确认后才能进入求解。"
        ),
    },
    {
        "name": "网格",
        "description": "生成、检查并读取与已确认仿真输入绑定的真实求解网格。",
    },
    {
        "name": "结果",
        "description": "读取仿真数值结果和 VTK 等结果文件。",
    },
    {
        "name": "仿真 Agent",
        "description": "围绕已完成研究执行有预算、可审计的规划、求解、评估与修正闭环。",
    },
]


def create_app(
    settings: Settings | None = None,
    planner: SimulationPlanner | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    web_root = Path(__file__).resolve().parent / "web"
    repository = FileRepository(resolved.data_dir)
    service = StudyService(
        repository=repository,
        planner=planner or build_planner(resolved),
        geometry=CadFlowGeometryInspector(resolved.cadflow_repo),
        compute_backend=resolved.compute_backend,
    )
    simulation_agent = SimulationAgent(
        repository=repository,
        service=service,
        policy=build_agent_policy(resolved),
    )
    modeling = ModelingService(service, retain_history=resolved.retain_modeling_history)
    task_manager = TaskManager(repository, compute_backend=resolved.compute_backend,
                               workers=resolved.task_workers, queue_limit=resolved.task_queue_limit,
                               timeout_seconds=resolved.task_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app):
        task_manager.start()
        try:
            yield
        finally:
            task_manager.close()

    app = FastAPI(
        lifespan=lifespan,
        title="ThermoFlow 热仿真平台 API",
        version="0.1.0",
        description=(
            "将几何和自然语言工况转换为用户确认的稳态或瞬态热仿真规格，再由确定性求解器计算。\n\n"
            "### 推荐流程\n"
            "1. 调用 `POST /v1/projects` 创建项目。\n"
            "2. 调用 `POST /v1/workpieces/files` 将 STL 导入项目，只执行几何检查。\n"
            "3. 调用 `POST /v1/workpieces/{id}/unit` 确认 STL 单位与实际尺度。\n"
            "4. 调用 `POST /v1/studies` 提交用途和工况，获得结构化草案及缺失信息。\n"
            "5. 用户复核材料、边界、网格和判据后调用 `POST /v1/studies/{id}/confirm`。\n"
            "6. 调用 `POST /v1/studies/{id}/mesh` 生成并检查真实网格。\n"
            "7. 调用 `POST /v1/studies/{id}/run` 执行确定性求解并读取真实 VTK 场。\n\n"
            "**约束：** GPT 只生成结构化草案，不执行代码，也不生成数值场。当前求解能力为简化稳态和瞬态导热。"
        ),
        openapi_tags=OPENAPI_TAGS,
        license_info={"name": "MIT 许可证", "identifier": "MIT"},
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        swagger_ui_parameters={
            "defaultModelsExpandDepth": 1,
            "displayRequestDuration": True,
            "docExpansion": "list",
            "filter": True,
        },
    )
    app.add_middleware(GZipMiddleware, minimum_size=1_024)
    app.state.settings = resolved
    app.state.repository = repository
    app.state.service = service
    app.state.modeling = modeling
    app.state.simulation_agent = simulation_agent
    app.state.task_manager = task_manager
    app.mount("/assets", StaticFiles(directory=web_root / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    @app.get("/docs", include_in_schema=False, response_class=FileResponse)
    def workbench() -> FileResponse:
        return FileResponse(web_root / "index.html", media_type="text/html")

    @app.get(
        "/health",
        tags=["系统"],
        summary="检查服务状态",
        description="返回当前规划器、计算后端、仿真 Agent、GPT 模型以及 CadFlow 连接状态。",
    )
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "planner_mode": resolved.planner_mode,
            "openai_model": resolved.openai_model if resolved.planner_mode == "openai" else None,
            "agent_mode": simulation_agent.policy.provider,
            "agent_model": simulation_agent.policy.model,
            "cadflow_repo_found": resolved.cadflow_repo.is_dir(),
            "compute": service.compute_runtime(),
        }

    @app.post(
        "/v1/projects",
        response_model=ProjectRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["项目"],
        summary="创建项目",
        description="创建用于保存几何版本、材料场景和全部研究历史的工程项目。",
    )
    def create_project(request: ProjectCreateRequest) -> ProjectRecord:
        return service.create_project(request)

    @app.get(
        "/v1/projects",
        response_model=list[ProjectRecord],
        tags=["项目"],
        summary="列出项目",
        description="按最近更新时间倒序返回项目；旧工件会自动迁移到独立项目。",
    )
    def list_projects() -> list[ProjectRecord]:
        return service.list_projects()

    @app.get(
        "/v1/projects/{project_id}",
        response_model=ProjectRecord,
        tags=["项目"],
        summary="查询项目",
    )
    def get_project(project_id: str) -> ProjectRecord:
        try:
            return repository.get_project(project_id)
        except (RecordNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch(
        "/v1/projects/{project_id}",
        response_model=ProjectRecord,
        tags=["项目"],
        summary="重命名项目",
    )
    def update_project(project_id: str, request: ProjectUpdateRequest) -> ProjectRecord:
        try:
            return service.update_project(project_id, request)
        except (RecordNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete(
        "/v1/projects/{project_id}",
        response_model=dict[str, object],
        tags=["项目"],
        summary="删除项目",
        description="删除项目、其几何版本、研究、计算任务和 Agent 记录；运行中的任务必须先取消。",
    )
    def delete_project(project_id: str) -> dict[str, object]:
        try:
            return service.delete_project(project_id)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v1/projects/{project_id}/workspace",
        response_model=ProjectWorkspace,
        tags=["项目"],
        summary="读取项目工作区",
        description="一次返回项目的几何版本和研究历史，不包含大型场数据。",
    )
    def get_project_workspace(project_id: str) -> ProjectWorkspace:
        try:
            return service.get_project_workspace(project_id)
        except (RecordNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/v1/workpieces/boxes",
        response_model=WorkpieceRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["工件"],
        summary="注册长方体工件",
        description="用毫米尺寸创建一个长方体工件，并通过 CadFlow 或解析元数据检查几何。",
        response_description="已登记的工件记录",
    )
    def register_box(request: BoxWorkpieceInput) -> WorkpieceRecord:
        return service.register_box(request)

    @app.get(
        "/v1/workpieces",
        response_model=list[WorkpieceRecord],
        tags=["工件"],
        summary="列出工件",
        description="按登记时间倒序返回全部工件。",
        response_description="工件记录列表",
    )
    def list_workpieces(project_id: str | None = None) -> list[WorkpieceRecord]:
        return service.list_workpieces(project_id=project_id)

    @app.get(
        "/v1/materials",
        response_model=list[MaterialCatalogEntry],
        tags=["工件"],
        summary="列出首期材料目录",
        description="返回带版本、来源和适用温度范围的材料数据。",
    )
    def materials() -> list[MaterialCatalogEntry]:
        return list_materials()

    @app.post("/v1/studies/{study_id}/tasks", response_model=TaskRecord, status_code=202,
              tags=["仿真研究"], summary="提交后台网格或求解任务")
    def submit_task(study_id: str, request: TaskCreateRequest) -> TaskRecord:
        try:
            return task_manager.submit(study_id, request)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail="未找到对应研究") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/tasks", response_model=list[TaskRecord], tags=["仿真研究"], summary="恢复计算任务状态")
    def list_tasks(study_id: str | None = None, project_id: str | None = None):
        return repository.list_tasks(study_id=study_id, project_id=project_id)

    @app.get("/v1/tasks/{task_id}", response_model=TaskRecord, tags=["仿真研究"], summary="查看计算阶段和进度")
    def get_task(task_id: str):
        try:
            return repository.get_task(task_id)
        except (RecordNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="未找到计算任务") from exc

    @app.post("/v1/tasks/{task_id}/cancel", response_model=TaskRecord, tags=["仿真研究"], summary="取消计算任务")
    def cancel_task(task_id: str):
        try:
            return task_manager.cancel(task_id)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail="未找到计算任务") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/workpieces/files",
        response_model=WorkpieceRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["工件"],
        summary="上传 CAD 工件",
        description="上传 STEP、BREP 或 STL 文件，并通过 CadFlow 检查其几何。",
        response_description="已登记的 CAD 工件记录",
    )
    async def register_file(
        file: Annotated[UploadFile, File(description="待检查的 STEP、BREP 或 STL 工件")],
        project_id: Annotated[
            str | None,
            Form(description="目标项目 ID；为空时按文件名自动创建项目"),
        ] = None,
    ) -> WorkpieceRecord:
        filename = file.filename or ""
        payload = await file.read(resolved.max_upload_bytes + 1)
        await file.close()
        if not payload:
            raise HTTPException(status_code=400, detail="上传的 CAD 文件为空")
        if len(payload) > resolved.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"上传的 CAD 文件超过 {resolved.max_upload_bytes} 字节限制",
            )
        try:
            return service.register_file(filename, payload, project_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/v1/simulations/stl",
        response_model=StlSimulationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["仿真研究"],
        summary="兼容模式：上传 STL 并同步求解",
        description=(
            "仅供受信自动化集成和回归测试。该接口将 STL 坐标明确解释为毫米并同步求解，"
            "不属于需要用户确认的工作台主流程。平台接受封闭或开放网格，"
            "依次完成几何诊断、体素域重建、参数补齐、确定性策略校验和稳态导热求解。"
            "用户可以锁定点/线/面/体热源、表面/嵌入方式、位置与功率，以及材料、边界、网格或求解参数；"
            "空字段继续由平台补齐。STL 坐标按毫米解释。"
        ),
        response_description="工件记录、仿真研究和结构化求解结果",
    )
    async def simulate_stl(
        file: Annotated[UploadFile, File(description="CadFlow 导出的 STL 工件，可为开放网格")],
        material_name: Annotated[
            str | None, Form(min_length=1, max_length=100, description="材料名称")
        ] = None,
        thermal_conductivity_w_m_k: Annotated[
            float | None, Form(gt=0.01, le=5_000, description="导热系数，W/(m·K)")
        ] = None,
        density_kg_m3: Annotated[
            float | None, Form(gt=1, le=30_000, description="材料密度，kg/m³")
        ] = None,
        specific_heat_j_kg_k: Annotated[
            float | None, Form(gt=1, le=20_000, description="材料比热容，J/(kg·K)")
        ] = None,
        heat_axis: Annotated[
            Literal["x", "y", "z"] | None,
            Form(description="定温边界所在的包围盒轴"),
        ] = None,
        min_face_temperature_k: Annotated[
            float | None, Form(ge=1, le=5_000, description="所选轴最小侧温度，K")
        ] = None,
        max_face_temperature_k: Annotated[
            float | None, Form(ge=1, le=5_000, description="所选轴最大侧温度，K")
        ] = None,
        heat_source_x_mm: Annotated[
            float | None, Form(ge=-1_000_000, le=1_000_000, description="热源中心 X 坐标，mm")
        ] = None,
        heat_source_y_mm: Annotated[
            float | None, Form(ge=-1_000_000, le=1_000_000, description="热源中心 Y 坐标，mm")
        ] = None,
        heat_source_z_mm: Annotated[
            float | None, Form(ge=-1_000_000, le=1_000_000, description="热源中心 Z 坐标，mm")
        ] = None,
        heat_source_shape: Annotated[
            Literal["point", "line", "surface", "volume"] | None,
            Form(description="热源类型：point、line、surface 或 volume"),
        ] = None,
        heat_source_placement: Annotated[
            Literal["surface", "embedded"] | None,
            Form(description="定位方式：贴附表面或嵌入重建域"),
        ] = None,
        heat_source_embedding_depth_mm: Annotated[
            float | None, Form(ge=0, le=1_000_000, description="目标嵌入深度，mm")
        ] = None,
        heat_source_power_w: Annotated[
            float | None, Form(gt=0, le=10_000_000, description="热源总功率，W")
        ] = None,
        heat_source_radius_mm: Annotated[
            float | None, Form(gt=0, le=1_000_000, description="点或线热源半径，mm")
        ] = None,
        heat_source_end_x_mm: Annotated[
            float | None, Form(ge=-1_000_000, le=1_000_000, description="线热源终点 X，mm")
        ] = None,
        heat_source_end_y_mm: Annotated[
            float | None, Form(ge=-1_000_000, le=1_000_000, description="线热源终点 Y，mm")
        ] = None,
        heat_source_end_z_mm: Annotated[
            float | None, Form(ge=-1_000_000, le=1_000_000, description="线热源终点 Z，mm")
        ] = None,
        heat_source_surface_axis: Annotated[
            Literal["x", "y", "z"] | None, Form(description="面热源法向轴")
        ] = None,
        heat_source_surface_width_mm: Annotated[
            float | None, Form(gt=0, le=1_000_000, description="面热源宽度，mm")
        ] = None,
        heat_source_surface_height_mm: Annotated[
            float | None, Form(gt=0, le=1_000_000, description="面热源高度，mm")
        ] = None,
        heat_source_surface_thickness_mm: Annotated[
            float | None, Form(gt=0, le=1_000_000, description="面热源映射厚度，mm")
        ] = None,
        heat_source_volume_width_mm: Annotated[
            float | None, Form(gt=0, le=1_000_000, description="体热源 X 向尺寸，mm")
        ] = None,
        heat_source_volume_height_mm: Annotated[
            float | None, Form(gt=0, le=1_000_000, description="体热源 Y 向尺寸，mm")
        ] = None,
        heat_source_volume_depth_mm: Annotated[
            float | None, Form(gt=0, le=1_000_000, description="体热源 Z 向尺寸，mm")
        ] = None,
        ambient_temperature_k: Annotated[
            float | None, Form(ge=1, le=5_000, description="环境温度，K")
        ] = None,
        convection_coefficient_w_m2_k: Annotated[
            float | None,
            Form(gt=0, le=1_000_000, description="外表面对流换热系数，W/(m²·K)"),
        ] = None,
        target_element_size_mm: Annotated[
            float | None, Form(gt=0, le=100_000, description="目标体素尺寸，mm")
        ] = None,
        max_axis_intervals: Annotated[
            int | None, Form(ge=2, le=100, description="单轴最大区间数")
        ] = None,
        relative_tolerance: Annotated[
            float | None, Form(ge=1e-12, le=1e-3, description="线性求解相对容差")
        ] = None,
        max_iterations: Annotated[
            int | None, Form(ge=10, le=100_000, description="线性求解最大迭代次数")
        ] = None,
    ) -> StlSimulationResponse:
        filename = file.filename or ""
        payload = await file.read(resolved.max_upload_bytes + 1)
        await file.close()
        if not payload:
            raise HTTPException(status_code=400, detail="上传的 STL 文件为空")
        if len(payload) > resolved.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"上传的 STL 文件超过 {resolved.max_upload_bytes} 字节限制",
            )
        override_values = {
            key: value
            for key, value in {
                "material_name": material_name,
                "thermal_conductivity_w_m_k": thermal_conductivity_w_m_k,
                "density_kg_m3": density_kg_m3,
                "specific_heat_j_kg_k": specific_heat_j_kg_k,
                "heat_axis": heat_axis,
                "min_face_temperature_k": min_face_temperature_k,
                "max_face_temperature_k": max_face_temperature_k,
                "heat_source_x_mm": heat_source_x_mm,
                "heat_source_y_mm": heat_source_y_mm,
                "heat_source_z_mm": heat_source_z_mm,
                "heat_source_shape": heat_source_shape,
                "heat_source_placement": heat_source_placement,
                "heat_source_embedding_depth_mm": heat_source_embedding_depth_mm,
                "heat_source_power_w": heat_source_power_w,
                "heat_source_radius_mm": heat_source_radius_mm,
                "heat_source_end_x_mm": heat_source_end_x_mm,
                "heat_source_end_y_mm": heat_source_end_y_mm,
                "heat_source_end_z_mm": heat_source_end_z_mm,
                "heat_source_surface_axis": heat_source_surface_axis,
                "heat_source_surface_width_mm": heat_source_surface_width_mm,
                "heat_source_surface_height_mm": heat_source_surface_height_mm,
                "heat_source_surface_thickness_mm": heat_source_surface_thickness_mm,
                "heat_source_volume_width_mm": heat_source_volume_width_mm,
                "heat_source_volume_height_mm": heat_source_volume_height_mm,
                "heat_source_volume_depth_mm": heat_source_volume_depth_mm,
                "ambient_temperature_k": ambient_temperature_k,
                "convection_coefficient_w_m2_k": convection_coefficient_w_m2_k,
                "target_element_size_mm": target_element_size_mm,
                "max_axis_intervals": max_axis_intervals,
                "relative_tolerance": relative_tolerance,
                "max_iterations": max_iterations,
            }.items()
            if value is not None
        }
        overrides = SimulationOverrides.model_validate(override_values) if override_values else None
        try:
            workpiece = await run_in_threadpool(service.register_stl, filename, payload)
            if not workpiece.geometry.available:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "STL 未通过可求解几何检查",
                        "workpiece_id": workpiece.workpiece_id,
                        "diagnostics": workpiece.geometry.diagnostics,
                    },
                )
            workpiece = await run_in_threadpool(
                service.confirm_workpiece_unit,
                workpiece.workpiece_id,
                WorkpieceUnitConfirmation(unit="mm").unit,
            )
            study = await run_in_threadpool(service.create_study, workpiece.workpiece_id, overrides)
            if study.status.value != "planned":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": study.failure or "仿真方案未通过校验",
                        "workpiece_id": workpiece.workpiece_id,
                        "study_id": study.study_id,
                        "errors": study.policy.errors if study.policy else [],
                    },
                )
            mesh = await run_in_threadpool(service.generate_mesh, study.study_id)
            if mesh.review_status == "pending":
                await run_in_threadpool(
                    service.confirm_mesh,
                    study.study_id,
                    MeshReviewRequest(
                        accept_warnings=True,
                        confirmed_by="受信自动化接口",
                    ),
                )
            completed = await run_in_threadpool(service.run_study, study.study_id)
            result = await run_in_threadpool(repository.get_result, study.study_id)
            return StlSimulationResponse(workpiece=workpiece, study=completed, result=result)
        except HTTPException:
            raise
        except PlannerUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="STL 热仿真未完成，请检查输入或通过高级诊断查询任务记录",
            ) from exc

    @app.get(
        "/v1/workpieces/{workpiece_id}",
        response_model=WorkpieceRecord,
        tags=["工件"],
        summary="查询工件",
        description="根据工件 ID 返回几何摘要、面信息和 CadFlow 检查结果。",
        response_description="工件记录",
    )
    def get_workpiece(workpiece_id: str) -> WorkpieceRecord:
        try:
            return service.get_workpiece(workpiece_id)
        except (RecordNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch(
        "/v1/workpieces/{workpiece_id}/components/{component_id}",
        response_model=WorkpieceRecord,
        tags=["工件"],
        summary="重命名组件",
        description=(
            "更新断开壳体的用户可读名称，保持稳定组件 ID、材料绑定和已确认研究快照不变。"
        ),
    )
    def update_component(
        workpiece_id: str,
        component_id: str,
        request: ComponentUpdateRequest,
    ) -> WorkpieceRecord:
        try:
            return service.update_component(workpiece_id, component_id, request)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete(
        "/v1/workpieces/{workpiece_id}/components/{component_id}",
        response_model=WorkpieceRecord,
        tags=["工件"],
        summary="删除 STL 组件",
        description="从未建立研究的 STL 工件中移除一个断开壳体并重建几何；至少保留一个组件。",
    )
    def delete_component(workpiece_id: str, component_id: str) -> WorkpieceRecord:
        try:
            return service.delete_component(workpiece_id, component_id)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/workpieces/{workpiece_id}/unit",
        response_model=WorkpieceRecord,
        tags=["工件"],
        summary="确认 STL 长度单位",
        description="将 STL 无单位坐标解释为微米、毫米或米，并生成毫米制求解几何。",
    )
    def confirm_workpiece_unit(
        workpiece_id: str,
        request: WorkpieceUnitConfirmation,
    ) -> WorkpieceRecord:
        try:
            return service.confirm_workpiece_unit(workpiece_id, request.unit)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="仿真草案生成失败，请稍后重试或检查 Agent 服务状态",
            ) from exc

    @app.post(
        "/v1/studies",
        response_model=StudyRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["仿真研究"],
        summary="生成结构化仿真草案",
        description=(
            "请求体包含 `workpiece_id` 和自然语言 `purpose`，还可通过 `overrides` 填入"
            "材料、固定温度面、网格和求解参数。平台返回经过确定性校验的待确认草案；"
            "该接口不会触发求解。"
        ),
        response_description="待用户复核和确认的结构化仿真草案",
    )
    def create_study(request: StudyCreateRequest) -> StudyRecord:
        try:
            return service.create_study(
                request.workpiece_id,
                request.overrides,
                request.purpose,
                request.require_confirmation,
                planning_mode=request.planning_mode,
            )
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PlannerUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/studies/{study_id}/copy",
        response_model=StudyRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["仿真研究"],
        summary="复制仿真研究",
        description=(
            "复制来源研究的材料、工况、边界、网格和判据，可同时应用结构化修改。"
            "新研究始终回到待确认状态，不复制网格或结果。"
        ),
    )
    def copy_study(study_id: str, request: StudyCopyRequest) -> StudyRecord:
        try:
            return service.copy_study(study_id, request)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="研究复制失败，请通过高级诊断查询任务记录",
            ) from exc

    def modeling_operation(operation, study_id, request):
        try:
            return operation(study_id, request)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail="未找到该仿真草案") from exc
        except PlannerUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail="草案操作未完成，请重新载入并检查保存状态") from exc

    @app.put("/v1/studies/{study_id}/draft", response_model=StudyRecord, tags=["仿真研究"],
             summary="保存未确认的结构化草案")
    def update_study_draft(study_id: str, request: DraftUpdateRequest) -> StudyRecord:
        return modeling_operation(modeling.update_draft, study_id, request)

    @app.post("/v1/studies/{study_id}/modeling/messages", response_model=StudyRecord, tags=["仿真研究"],
              summary="基于当前草案继续建模对话，不应用建议或执行求解")
    def modeling_message(study_id: str, request: ModelingMessageRequest) -> StudyRecord:
        return modeling_operation(modeling.message, study_id, request)

    @app.post("/v1/studies/{study_id}/modeling/decision", response_model=StudyRecord, tags=["仿真研究"],
              summary="应用、放弃或撤销建模建议，输入仍须单独确认")
    def modeling_decision(study_id: str, request: ModelingDecisionRequest) -> StudyRecord:
        return modeling_operation(modeling.decide, study_id, request)

    @app.post(
        "/v1/studies/{study_id}/confirm",
        response_model=StudyRecord,
        tags=["仿真研究"],
        summary="确认仿真输入",
        description=(
            "保存用户复核后的结构化参数快照，并将研究标记为已准备求解。"
            "请求必须明确声明已逐组件确认材料及热物性。"
        ),
    )
    def confirm_study(study_id: str, request: StudyConfirmationRequest) -> StudyRecord:
        try:
            return service.confirm_study(study_id, request)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/studies/{study_id}/mesh",
        response_model=MeshRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["网格"],
        summary="生成真实求解网格",
        description=(
            "根据用户已确认的网格参数对 STL 生成真实活动体素网格，保存 VTK 文件、"
            "表面单元预览和确定性质量指标。质量阻断时不会允许求解。"
        ),
        response_description="与研究输入快照绑定的网格记录",
    )
    def generate_mesh(study_id: str) -> MeshRecord:
        try:
            return service.generate_mesh(study_id)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="网格生成失败，请调整网格参数或通过高级诊断查询任务记录",
            ) from exc

    @app.get(
        "/v1/studies/{study_id}/mesh",
        response_model=MeshRecord,
        tags=["网格"],
        summary="查询研究网格",
        description="返回实际活动体素、质量指标、表面预览和网格文件索引。",
        response_description="已保存的网格记录",
    )
    def get_mesh(study_id: str) -> MeshRecord:
        try:
            if repository.get_study(study_id).mesh_status.value not in {"ready", "needs_review", "blocked"}:
                raise RecordNotFoundError("该研究尚无已完成的网格")
            return repository.get_mesh(study_id)
        except (RecordNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/v1/studies/{study_id}/mesh/confirm",
        response_model=MeshRecord,
        tags=["网格"],
        summary="确认网格质量风险",
        description=(
            "仅用于质量状态为需关注的网格。用户查看实际网格和提示后明确接受风险，"
            "研究才进入可求解状态；被阻断的网格不能通过该接口放行。"
        ),
        response_description="带用户风险确认记录的网格",
    )
    def confirm_mesh(study_id: str, request: MeshReviewRequest) -> MeshRecord:
        try:
            return service.confirm_mesh(study_id, request)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v1/studies",
        response_model=list[StudyRecord],
        tags=["仿真研究"],
        summary="列出仿真研究",
        description="按更新时间倒序返回研究；可使用项目 ID 或工件 ID 进行筛选。",
        response_description="仿真研究列表",
    )
    def list_studies(
        workpiece_id: str | None = None,
        project_id: str | None = None,
    ) -> list[StudyRecord]:
        return repository.list_studies(workpiece_id, project_id)

    @app.get(
        "/v1/studies/{study_id}",
        response_model=StudyRecord,
        tags=["仿真研究"],
        summary="查询仿真研究",
        description="返回补齐后的方案、模型来源、策略校验报告和当前运行状态。",
        response_description="仿真研究记录",
    )
    def get_study(study_id: str) -> StudyRecord:
        try:
            return repository.get_study(study_id)
        except (RecordNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/studies/{study_id}/validation", response_model=PolicyReport, tags=["仿真研究"],
             summary="只读检查当前参数与修改建议")
    def get_study_validation(study_id: str, expected_revision: int | None = None) -> PolicyReport:
        try:
            study = repository.get_study(study_id)
            if expected_revision is not None and expected_revision != study.draft_revision:
                raise HTTPException(status_code=409, detail=(
                    f"草案版本已变化：页面为第 {expected_revision} 版，服务器为第 {study.draft_revision} 版。"
                    "请先检查未保存修改，再刷新数据后重试。"
                ))
            if study.plan is None:
                return PolicyReport(accepted=False, errors=["尚未生成仿真方案，请先确认几何尺度并建立草案。"])
            return validate_plan(repository.get_workpiece(study.workpiece_id), study.plan)
        except (RecordNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/v1/studies/{study_id}/spec",
        response_model=SimulationSpec,
        tags=["仿真研究"],
        summary="读取版本化仿真规格",
        description=(
            "返回 Agent、结构化表单、网格、求解器和报告共享的 SimulationSpec；"
            "所有物理量均携带显式单位。"
        ),
    )
    def get_simulation_spec(study_id: str) -> SimulationSpec:
        try:
            return service.get_simulation_spec(study_id)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/studies/{study_id}/run",
        response_model=StudyRecord,
        tags=["仿真研究"],
        summary="运行仿真研究",
        description="运行用户已确认且通过确定性策略校验的仿真方案；该接口不接受额外参数。",
        response_description="更新后的仿真研究记录",
    )
    def run_study(study_id: str) -> StudyRecord:
        try:
            return service.run_study(study_id)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="求解器运行失败，请在高级诊断中查询任务记录",
            ) from exc

    @app.get(
        "/v1/studies/{study_id}/result",
        response_model=SimulationResult,
        tags=["结果"],
        summary="查询仿真结果",
        description="返回真实温度/热流场摘要、热阻、能量平衡、判据评估和结果文件索引。",
        response_description="结构化仿真结果",
    )
    def get_result(study_id: str) -> SimulationResult:
        try:
            if repository.get_study(study_id).status.value != "succeeded":
                raise RecordNotFoundError("该研究尚无已完成的结果")
            return repository.get_result(study_id)
        except (RecordNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/studies/{study_id}/frames/{index}", response_model=ThermalTimeFrame,
             tags=["结果"], summary="读取指定时刻的真实温度和热流")
    def get_time_frame(study_id: str, index: int) -> ThermalTimeFrame:
        try:
            if repository.get_study(study_id).status.value != "succeeded":
                raise RecordNotFoundError("该研究尚无已完成的结果")
            result = repository.get_result(study_id)
            step = next((item for item in result.time_steps if item.index == index), None)
            if step is None:
                raise HTTPException(status_code=404, detail="该研究没有指定的时间步")
            artifact = next(item for item in result.artifacts if item.name == step.field_artifact)
            content = repository.artifact_path(study_id, artifact.name).read_bytes()
            if hashlib.sha256(content).hexdigest() != artifact.sha256:
                raise HTTPException(status_code=409, detail="该时刻的结果数据校验失败，请重新计算研究")
            return ThermalTimeFrame.model_validate_json(content)
        except (RecordNotFoundError, FileNotFoundError, StopIteration) as exc:
            raise HTTPException(status_code=404, detail="该时刻的结果数据不存在") from exc

    @app.post(
        "/v1/study-comparisons",
        response_model=StudyComparisonResponse,
        tags=["结果"],
        summary="比较仿真研究",
        description=(
            "比较同一项目中 2 至 4 个已完成研究，返回统一温标和指标差值。"
            "只有相同几何、网格和采样坐标的研究才返回真实逐点温度差值场。"
        ),
    )
    def compare_studies(request: StudyComparisonRequest) -> StudyComparisonResponse:
        try:
            return service.compare_studies(request)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/studies/{study_id}/agent-runs",
        response_model=AgentRunRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["仿真 Agent"],
        summary="运行仿真优化 Agent",
        description=(
            "以已成功求解的研究为基准，读取 STL 诊断和数值结果，校核温度、能量平衡及"
            "热源映射目标，并在用户授权范围内调整热源功率或网格后重新求解。每轮生成独立研究。"
        ),
        response_description="优化目标、轮次结果与最终采用研究",
    )
    def run_simulation_agent(study_id: str, request: AgentGoalRequest) -> AgentRunRecord:
        try:
            return simulation_agent.run(study_id, request)
        except RecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PlannerUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="仿真优化未完成，请通过高级诊断查询任务记录",
            ) from exc

    @app.get(
        "/v1/agent-runs",
        response_model=list[AgentRunRecord],
        tags=["仿真 Agent"],
        summary="列出 Agent 运行",
        description="按时间倒序返回 Agent 运行，可按工件或基准研究筛选。",
    )
    def list_agent_runs(
        project_id: str | None = None,
        workpiece_id: str | None = None,
        base_study_id: str | None = None,
    ) -> list[AgentRunRecord]:
        return repository.list_agent_runs(
            project_id=project_id,
            workpiece_id=workpiece_id,
            base_study_id=base_study_id,
        )

    @app.get(
        "/v1/agent-runs/{run_id}",
        response_model=AgentRunRecord,
        tags=["仿真 Agent"],
        summary="查询 Agent 运行",
        description="返回目标、权限边界、候选研究、轮次指标和最终状态。",
    )
    def get_agent_run(run_id: str) -> AgentRunRecord:
        try:
            return repository.get_agent_run(run_id)
        except (RecordNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/v1/studies/{study_id}/artifacts/{name}",
        response_class=FileResponse,
        tags=["结果"],
        summary="下载仿真结果文件",
        description="下载指定研究生成的 VTK 温度与热流场或其他结果文件。",
        response_description="仿真结果文件",
    )
    def get_artifact(study_id: str, name: str) -> FileResponse:
        try:
            study = repository.get_study(study_id)
            published = []
            if study.status.value == "succeeded":
                published.extend(repository.get_result(study_id).artifacts)
            if study.mesh_status.value in {"ready", "needs_review", "blocked"}:
                published.extend(repository.get_mesh(study_id).artifacts)
            artifact = next((item for item in published if item.name == name), None)
            if artifact is None:
                raise RecordNotFoundError("该结果文件尚未生成或尚未完成保存")
            path = repository.artifact_path(study_id, name)
            if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
                raise HTTPException(status_code=409, detail="结果文件校验失败，请重新生成对应研究")
        except (RecordNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type=_artifact_media_type(path), filename=path.name)

    @app.post("/v1/workpieces/{workpiece_id}/regions", response_model=GeometryRegion,
              tags=["工件"], summary="保存稳定选面区域")
    def save_surface_region(workpiece_id: str, request: SurfaceRegionRequest):
        try:
            return create_surface_region(repository, workpiece_id, request)
        except (RecordNotFoundError, OSError) as exc:
            raise HTTPException(404, "未找到几何") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/workpieces/{workpiece_id}/view", response_model=EngineeringSurface, tags=["工件"],
             summary="读取完整三维几何与稳定组件标识")
    def get_geometry_view(workpiece_id: str):
        try:
            return geometry_surface(repository, workpiece_id)
        except (RecordNotFoundError, OSError) as exc:
            raise HTTPException(404, "几何数据不存在，请重新导入") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/studies/{study_id}/view", response_model=EngineeringSurface, tags=["结果"],
              summary="读取真实网格表面、截面或结果差值")
    def get_study_view(study_id: str, request: ViewRequest):
        try:
            return study_surface(repository, study_id, request)
        except (RecordNotFoundError, OSError) as exc:
            raise HTTPException(404, "该研究没有可读取的数值网格") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/studies/{study_id}/playback", response_model=EngineeringPlayback, tags=["结果"],
             summary="一次读取瞬态温度播放所需的共享网格与全部真实帧")
    def get_study_playback(study_id: str):
        try:
            return study_playback(repository, study_id)
        except (RecordNotFoundError, OSError) as exc:
            raise HTTPException(404, "该研究没有可播放的瞬态温度场") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/studies/{study_id}/cells/{cell_id}/probe", response_model=CellProbe, tags=["结果"],
             summary="读取指定真实单元的温度和热流")
    def get_cell_probe(study_id: str, cell_id: int, frame_index: int | None = None):
        try:
            return probe_cell(repository, study_id, cell_id, frame_index)
        except (RecordNotFoundError, OSError) as exc:
            raise HTTPException(404, "该研究没有对应数值单元") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return app


def _artifact_media_type(path: Path) -> str:
    return {".vtk": "application/vnd.vtk", ".json": "application/json"}.get(
        path.suffix.lower(), "application/octet-stream"
    )


app = create_app()
