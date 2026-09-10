from __future__ import annotations

import time
from pathlib import Path

import trimesh
from fastapi.testclient import TestClient

from thermoflow.api import create_app
from thermoflow.assistant import AssistantService, DeterministicKnowledgeProvider
from thermoflow.cadflow_adapter import CadFlowGeometryInspector
from thermoflow.modeling import ModelingService
from thermoflow.models import (
    AgentLaunchRequest,
    AgentModelingTurn,
    AgentQuestion,
    AgentQuestionAnswer,
    AgentQuestionOption,
    AgentSessionCreateRequest,
    AgentTurnRequest,
    BoxWorkpieceInput,
    EngineeringCriterion,
    FaceSelector,
    FixedTemperatureBoundary,
    Point3DMM,
    Quantity,
    SimulationOverrides,
    StudyConfirmationRequest,
    VolumetricHeatSource,
)
from thermoflow.planner import DeterministicPlanner, PlannerUnavailableError
from thermoflow.service import StudyService
from thermoflow.settings import Settings
from thermoflow.storage import FileRepository
from thermoflow.tasks import TaskManager


class ScriptedModelingProvider:
    provider = "test-model"
    model = "test-model-v1"

    def __init__(self, turns: list[AgentModelingTurn | Exception]) -> None:
        self.turns = list(turns)
        self.calls: list[dict] = []

    def respond(self, **kwargs) -> AgentModelingTurn:
        self.calls.append(kwargs)
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


def client(tmp_path: Path, modeling_provider=None) -> TestClient:
    return TestClient(create_app(Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        cadflow_repo=tmp_path / "missing-cadflow",
        planner_mode="deterministic",
    ), DeterministicPlanner(), modeling_provider=modeling_provider))


def modeling_assistant(tmp_path: Path, provider: ScriptedModelingProvider) -> tuple[
    FileRepository, StudyService, AssistantService,
]:
    repository = FileRepository(tmp_path / "data")
    service = StudyService(
        repository=repository,
        planner=DeterministicPlanner(),
        geometry=CadFlowGeometryInspector(tmp_path / "missing-cadflow"),
    )
    assistant = AssistantService(
        repository=repository,
        service=service,
        modeling=ModelingService(service),
        task_manager=TaskManager(repository),
        provider=DeterministicKnowledgeProvider(),
        modeling_provider=provider,
    )
    return repository, service, assistant


def test_assistant_answers_from_local_documents_and_persists(tmp_path: Path) -> None:
    with client(tmp_path) as http:
        response = http.post("/v1/assistant-sessions", json={"message": "什么是瞬态导热？"})
        assert response.status_code == 201
        session = response.json()
        assert session["mode"] == "qa"
        assert session["revision"] == 1
        assert "初始温度" in session["answer"]
        assert session["citations"]
        restored = http.get(f"/v1/assistant-sessions/{session['session_id']}")
        assert restored.status_code == 200
        assert restored.json()["answer"] == session["answer"]
        assert restored.json()["study_id"] is None
        assert restored.json()["questions"] == []
        assert len(restored.json()["messages"]) == 2
        followup = http.post(f"/v1/assistant-sessions/{session['session_id']}/turns", json={
            "expected_revision": session["revision"], "message": "请解释一下时间步长",
        })
        assert followup.status_code == 200
        assert followup.json()["study_id"] is None
        assert followup.json()["questions"] == []


def test_assistant_session_rejects_invalid_id_without_internal_error(tmp_path: Path) -> None:
    with client(tmp_path) as http:
        response = http.get("/v1/assistant-sessions/当前会话")
        assert response.status_code == 400
        assert response.json()["detail"] == "记录 ID 无效"


def test_assistant_applies_explicit_inputs_and_launches(tmp_path: Path) -> None:
    with client(tmp_path) as http:
        workpiece = http.post("/v1/workpieces/boxes", json={
            "name": "assistant block", "dimensions_mm": {"x": 100, "y": 20, "z": 10},
        }).json()
        created = http.post("/v1/assistant-sessions", json={
            "message": (
                "建立稳态导热仿真。材料为 6061-T6 铝合金。热源功率 40 W，"
                "X 最小侧施加热源，X 最大侧保持 25 ℃，环境 25 ℃，"
                "对流换热系数 10 W/(m²·K)，目标单元尺寸 0.5 mm，"
                "最高温度不超过 80 ℃。"
            ),
            "workpiece_id": workpiece["workpiece_id"],
        })
        assert created.status_code == 201
        session = created.json()
        assert session["study_id"]
        assert session["status"] == "ready_for_review"
        assert session["questions"] == []
        assert session["readiness"] == []
        assert http.get(f"/v1/studies/{session['study_id']}").json()["plan"]["material"]["name"] == "铝合金 6061-T6"

        launched = http.post(f"/v1/assistant-sessions/{session['session_id']}/launch", json={
            "expected_revision": session["revision"],
            "expected_study_revision": session["study_revision"],
            "summary_confirmed": True,
            "materials_confirmed": True,
            "form_reviewed": True,
        })
        assert launched.status_code == 200
        running = launched.json()
        assert running["status"] == "queued"
        assert running["task_id"]
        study = http.get(f"/v1/studies/{running['study_id']}").json()
        assert study["confirmation"]["status"] == "confirmed"


def test_assistant_asks_typed_questions_for_missing_hyperparameters(tmp_path: Path) -> None:
    with client(tmp_path) as http:
        workpiece = http.post("/v1/workpieces/boxes", json={
            "name": "missing inputs", "dimensions_mm": {"x": 100, "y": 20, "z": 10},
        }).json()
        session = http.post("/v1/assistant-sessions", json={
            "message": "建立稳态导热仿真",
            "workpiece_id": workpiece["workpiece_id"],
        }).json()
        questions = {item["question_id"]: item for item in session["questions"]}
        assert questions["material_selection"]["type"] == "single_choice"
        assert questions["heat_source_details"]["type"] == "text"
        assert questions["boundary_details"]["type"] == "text"


def test_assistant_model_applies_explicit_parameters_without_reasking(tmp_path: Path) -> None:
    provider = ScriptedModelingProvider([AgentModelingTurn(
        answer="已写入你提供的材料、热源、边界、对流、网格和温度判据，请查看右侧表单复核。",
        catalog_material_id="al-6061-t6",
        overrides=SimulationOverrides(
            analysis_type="steady_state_conduction",
            enable_heat_source=True,
            heat_sources=[VolumetricHeatSource(
                shape="surface",
                placement="surface",
                center_mm=Point3DMM(x=0, y=10, z=5),
                total_power_w=40,
                radius_mm=0.5,
                surface_normal_axis="x",
                surface_width_mm=20,
                surface_height_mm=10,
                surface_thickness_mm=0.5,
            )],
            fixed_boundaries=[FixedTemperatureBoundary(
                selector=FaceSelector.X_MAX,
                temperature_k=298.15,
            )],
            enable_global_convection=True,
            ambient_temperature_k=298.15,
            convection_coefficient_w_m2_k=10,
            target_element_size_mm=0.5,
            criteria=[EngineeringCriterion(
                metric="max_temperature",
                operator="less_or_equal",
                target=Quantity(value=353.15, unit="K"),
            )],
        ),
        ready_for_review=True,
    )])
    prompt = (
        "请建立稳态导热仿真，材料为 6061-T6 铝合金；X 最小侧施加 40 W 热源，"
        "X 最大侧为 25 ℃，其余面与 25 ℃ 环境对流，h=10 W/(m²·K)，"
        "目标单元尺寸 0.5 mm，评估最高温度是否低于 80 ℃。"
    )
    repository, service, assistant = modeling_assistant(tmp_path, provider)
    workpiece = service.register_box(BoxWorkpieceInput(
        name="explicit model turn",
        dimensions_mm={"x": 100, "y": 20, "z": 10},
    ))
    session = assistant.create(AgentSessionCreateRequest(
        message=prompt,
        workpiece_id=workpiece.workpiece_id,
    ))
    assert session.status == "ready_for_review"
    assert session.questions == []
    assert provider.calls[0]["user_message"] == prompt
    plan = repository.get_study(session.study_id).plan
    assert plan is not None
    assert plan.material.name == "铝合金 6061-T6"
    assert plan.heat_sources[0].total_power_w == 40
    assert plan.mesh.target_element_size_mm == 0.5
    assert plan.mesh.max_axis_intervals == 100
    assert plan.criteria[0].target.value == 353.15


def test_assistant_replays_dynamic_answers_to_model_without_question_registry(tmp_path: Path) -> None:
    provider = ScriptedModelingProvider([
        AgentModelingTurn(
            answer="我已记录稳态分析和材料，请补充散热方式。",
            catalog_material_id="al-6061-t6",
            questions=[AgentQuestion(
                question_id="cooling_profile",
                group="散热",
                type="text",
                prompt="工件外表面如何散热？",
                rationale="需要据此建立边界条件。",
            )],
            missing_information=["需要确认散热方式"],
        ),
        AgentModelingTurn(
            answer="散热条件已记录。请查看右侧表单完成最终确认。",
            ready_for_review=True,
        ),
    ])
    _, service, assistant = modeling_assistant(tmp_path, provider)
    workpiece = service.register_box(BoxWorkpieceInput(
        name="dynamic question",
        dimensions_mm={"x": 20, "y": 10, "z": 5},
    ))
    created = assistant.create(AgentSessionCreateRequest(
        message="建立稳态导热仿真，材料为 6061-T6。",
        workpiece_id=workpiece.workpiece_id,
    ))
    assert created.questions[0].question_id == "cooling_profile"
    answered = assistant.turn(created.session_id, AgentTurnRequest(
        expected_revision=created.revision,
        answers=[AgentQuestionAnswer(
            question_id="cooling_profile",
            text_value="其余外表面与 25 ℃ 空气自然对流。",
        )],
    ))
    assert answered.status == "ready_for_review"
    assert "对“工件外表面如何散热？”的回答是" in provider.calls[1]["conversation"][-1].content


def test_assistant_unit_question_and_model_failure_are_retryable(tmp_path: Path) -> None:
    provider = ScriptedModelingProvider([
        AgentModelingTurn(
            answer="我需要确认导入模型的尺度。",
            questions=[AgentQuestion(
                question_id="geometry_unit",
                group="几何",
                type="single_choice",
                prompt="导入坐标采用什么单位？",
                rationale="单位决定热源、网格和面积尺度。",
                options=[
                    AgentQuestionOption(option_id="um", label="微米"),
                    AgentQuestionOption(option_id="mm", label="毫米"),
                    AgentQuestionOption(option_id="m", label="米"),
                ],
            )],
            missing_information=["需要确认 STL 坐标单位"],
        ),
        PlannerUnavailableError("模型响应超时，请稍后重试；已保存的草案不受影响"),
        AgentModelingTurn(
            answer="单位和已提供参数均已保留，请查看右侧表单完成最终确认。",
            catalog_material_id="al-6061-t6",
            ready_for_review=True,
        ),
    ])
    _, service, assistant = modeling_assistant(tmp_path, provider)
    workpiece = service.register_stl(
        "dynamic-unit.stl",
        trimesh.creation.box(extents=[20, 10, 5]).export(file_type="stl"),
    )
    initial = assistant.create(AgentSessionCreateRequest(
        message="建立稳态导热仿真，材料为 6061-T6。",
        workpiece_id=workpiece.workpiece_id,
    ))
    assert initial.questions[0].prompt == "导入坐标采用什么单位？"
    unit = assistant.turn(initial.session_id, AgentTurnRequest(
        expected_revision=initial.revision,
        answers=[AgentQuestionAnswer(question_id="geometry_unit", option_ids=["mm"])],
    ))
    assert unit.failure
    assert unit.study_id is None
    retried = assistant.turn(unit.session_id, AgentTurnRequest(
        expected_revision=unit.revision,
        retry=True,
    ))
    assert retried.status == "ready_for_review"
    assert not retried.failure
    assert retried.study_id
    assert any(
        "对“导入坐标采用什么单位？”的回答是：毫米。" in message.content
        for message in provider.calls[2]["conversation"]
    )


def test_assistant_treats_action_with_question_word_as_modeling(tmp_path: Path) -> None:
    with client(tmp_path) as http:
        workpiece = http.post("/v1/workpieces/boxes", json={
            "name": "intent block", "dimensions_mm": {"x": 100, "y": 20, "z": 10},
        }).json()
        response = http.post("/v1/assistant-sessions", json={
            "message": "请建立稳态导热仿真，并判断最高温度是否低于 80 ℃。",
            "workpiece_id": workpiece["workpiece_id"],
        })
        assert response.status_code == 201
        assert response.json()["mode"] == "modeling"
        assert response.json()["study_id"]


def test_assistant_persists_task_completion_conclusion(tmp_path: Path) -> None:
    with client(tmp_path) as http:
        workpiece = http.post("/v1/workpieces/boxes", json={
            "name": "conclusion block", "dimensions_mm": {"x": 20, "y": 10, "z": 5},
        }).json()
        session = http.post("/v1/assistant-sessions", json={
            "message": (
                "建立稳态导热仿真。热源功率 40 W，"
                "X 最小侧施加热源，X 最大侧保持 25 ℃，环境 25 ℃，"
                "对流换热系数 10 W/(m²·K)，目标单元尺寸 0.5 mm，"
                "最高温度不超过 80 ℃。"
            ),
            "workpiece_id": workpiece["workpiece_id"],
        }).json()
        assert session["status"] == "ready_for_review"
        launched = http.post(f"/v1/assistant-sessions/{session['session_id']}/launch", json={
            "expected_revision": session["revision"],
            "expected_study_revision": session["study_revision"],
            "summary_confirmed": True,
            "materials_confirmed": True,
            "form_reviewed": True,
        })
        assert launched.status_code == 200
        deadline = time.monotonic() + 10
        completed = launched.json()
        while time.monotonic() < deadline and completed["status"] not in {"completed", "failed"}:
            time.sleep(0.05)
            completed = http.get(f"/v1/assistant-sessions/{session['session_id']}").json()
        assert completed["status"] == "completed"
        assert "最高温度" in completed["answer"]
        assert completed["messages"][-1]["role"] == "assistant"


def test_assistant_rejects_stale_revision(tmp_path: Path) -> None:
    with client(tmp_path) as http:
        created = http.post("/v1/assistant-sessions", json={"message": "什么是网格 warning？"}).json()
        stale = http.post(f"/v1/assistant-sessions/{created['session_id']}/turns", json={
            "expected_revision": 0, "message": "为什么？",
        })
        assert stale.status_code == 409


def test_assistant_confirms_stl_unit_then_each_component_material(tmp_path: Path) -> None:
    with client(tmp_path) as http:
        left = trimesh.creation.box(extents=[10, 6, 4])
        right = left.copy()
        right.apply_translation([15, 0, 0])
        workpiece = http.post("/v1/workpieces/files", files={
            "file": ("two-components.stl", trimesh.util.concatenate([left, right]).export(file_type="stl"), "model/stl"),
        }).json()
        session = http.post("/v1/assistant-sessions", json={
            "message": "帮我设置稳态仿真", "workpiece_id": workpiece["workpiece_id"],
        }).json()
        assert session["status"] == "awaiting_unit"
        assert session["study_id"] is None
        unit = http.post(f"/v1/assistant-sessions/{session['session_id']}/turns", json={
            "expected_revision": session["revision"],
            "answers": [{"question_id": "geometry_unit", "option_ids": ["mm"]}],
        })
        assert unit.status_code == 200, unit.text
        session = unit.json()
        assert session["study_id"]
        material_questions = [q for q in session["questions"] if q["question_id"] == "material_selection"]
        assert len(material_questions) == 1
        answers = [{"question_id": "material_selection", "option_ids": ["al-6061-t6"]}]
        material = http.post(f"/v1/assistant-sessions/{session['session_id']}/turns", json={
            "expected_revision": session["revision"], "answers": answers,
        })
        assert material.status_code == 200, material.text
        assert material.json()["status"] == "clarifying"
        plan = http.get(f"/v1/studies/{session['study_id']}").json()["plan"]
        assert all(item["material_id"] == "al-6061-t6" for item in plan["component_materials"])


def test_assistant_qa_reads_existing_study_without_modifying_it(tmp_path: Path) -> None:
    with client(tmp_path) as http:
        part = http.post("/v1/workpieces/boxes", json={
            "name": "qa block", "dimensions_mm": {"x": 100, "y": 20, "z": 10},
        }).json()
        draft = http.app.state.service.create_study(part["workpiece_id"], purpose="导热仿真", require_confirmation=True)
        before = http.get(f"/v1/studies/{draft.study_id}").json()
        response = http.post("/v1/assistant-sessions", json={
            "message": "请解释当前仿真的边界条件", "study_id": draft.study_id,
        })
        assert response.status_code == 201, response.text
        session = response.json()
        assert session["mode"] == "result_explanation"
        assert session["study_id"] == draft.study_id
        assert session["questions"] == []
        assert http.get(f"/v1/studies/{draft.study_id}").json() == before
        assert len(http.get("/v1/studies").json()) == 1


def test_assistant_rejects_launch_of_changed_or_incomplete_plan(tmp_path: Path) -> None:
    with client(tmp_path) as http:
        part = http.post("/v1/workpieces/boxes", json={
            "name": "review block", "dimensions_mm": {"x": 100, "y": 20, "z": 10},
        }).json()
        session = http.post("/v1/assistant-sessions", json={
            "message": "设置稳态导热仿真", "workpiece_id": part["workpiece_id"],
        }).json()
        payload = {"expected_revision": session["revision"], "expected_study_revision": session["study_revision"],
                   "summary_confirmed": True, "materials_confirmed": True, "form_reviewed": True}
        response = http.post(f"/v1/assistant-sessions/{session['session_id']}/launch", json=payload)
        assert response.status_code == 409
        assert "尚未就绪" in response.json()["detail"]
        payload["expected_study_revision"] += 1
        response = http.post(f"/v1/assistant-sessions/{session['session_id']}/launch", json=payload)
        assert response.status_code == 409
        assert "已更新" in response.json()["detail"]
        assert http.get("/v1/tasks").json() == []


def test_assistant_persists_terminal_task_conclusion_without_session_polling(tmp_path: Path) -> None:
    """The task manager, rather than an open browser, finalizes the Agent session."""
    repository = FileRepository(tmp_path / "data")
    service = StudyService(
        repository=repository,
        planner=DeterministicPlanner(),
        geometry=CadFlowGeometryInspector(tmp_path / "missing-cadflow"),
    )
    modeling = ModelingService(service)
    tasks = TaskManager(repository, timeout_seconds=30)
    assistant = AssistantService(
        repository=repository,
        service=service,
        modeling=modeling,
        task_manager=tasks,
        provider=DeterministicKnowledgeProvider(),
    )
    tasks.start()
    try:
        workpiece = service.register_box(BoxWorkpieceInput(
            name="background conclusion block",
            dimensions_mm={"x": 20, "y": 10, "z": 5},
        ))
        session = assistant.create(AgentSessionCreateRequest(
            workpiece_id=workpiece.workpiece_id,
            message=(
                "建立稳态导热仿真。材料为 6061-T6 铝合金。热源功率 40 W，"
                "X 最小侧施加热源，X 最大侧保持 25 ℃，环境 25 ℃，"
                "对流换热系数 10 W/(m²·K)，目标单元尺寸 0.5 mm，"
                "最高温度不超过 80 ℃。"
            ),
        ))
        draft = repository.get_study(session.study_id)
        assert draft.plan is not None
        assert draft.plan.solver.backend == "voxel_stl_v1"
        assert draft.plan.heat_source is not None
        assert draft.plan.heat_source.total_power_w == 40
        assert draft.plan.heat_source.shape == "surface"
        assert draft.plan.heat_source.center_mm.x == 0
        assert [(item.selector.value, item.temperature_k) for item in draft.plan.boundaries] == [
            ("face.xmax", 298.15),
        ]
        assert draft.plan.convection is not None
        assert draft.plan.convection.ambient_temperature_k == 298.15
        assert draft.plan.convection.heat_transfer_coefficient_w_m2_k == 10
        assert draft.plan.mesh.target_element_size_mm == 0.5
        assert draft.plan.criteria[0].target.value == 353.15
        assert session.status == "ready_for_review"
        launched = assistant.launch(session.session_id, AgentLaunchRequest(
            expected_revision=session.revision,
            expected_study_revision=session.study_revision,
            summary_confirmed=True,
            materials_confirmed=True,
            form_reviewed=True,
        ))
        assert launched.answer is not None and "任务设置预检已通过" in launched.answer
        deadline = time.monotonic() + 15
        completed = repository.get_agent_session(launched.session_id)
        while time.monotonic() < deadline and completed.status not in {"completed", "failed"}:
            time.sleep(0.05)
            completed = repository.get_agent_session(launched.session_id)

        assert completed.status == "completed"
        assert completed.answer is not None and "最高温度" in completed.answer
        assert completed.messages[-1].role == "assistant"
    finally:
        tasks.close()


def test_assistant_applies_explicit_prompt_after_stl_unit_confirmation(tmp_path: Path) -> None:
    repository = FileRepository(tmp_path / "data")
    service = StudyService(
        repository=repository,
        planner=DeterministicPlanner(),
        geometry=CadFlowGeometryInspector(tmp_path / "missing-cadflow"),
    )
    assistant = AssistantService(
        repository=repository,
        service=service,
        modeling=ModelingService(service),
        task_manager=TaskManager(repository),
        provider=DeterministicKnowledgeProvider(),
    )
    workpiece = service.register_stl(
        "thermal-part.stl",
        trimesh.creation.box(extents=[60, 30, 15]).export(file_type="stl"),
    )
    prompt = (
        "请基于当前工件建立稳态导热仿真。材料为 6061-T6 铝合金。"
        "在 X 最小侧施加 40 W 总功率的热源，X 最大侧保持 25 ℃，"
        "其余外表面与 25 ℃ 环境自然对流，换热系数取 10 W/(m²·K)。"
        "目标单元尺寸为 0.5 mm。评估最高温度是否低于 80 ℃。"
    )

    initial = assistant.create(AgentSessionCreateRequest(
        workpiece_id=workpiece.workpiece_id,
        message=prompt,
    ))
    assert initial.status == "awaiting_unit"
    assert initial.study_id is None

    completed = assistant.turn(initial.session_id, AgentTurnRequest(
        expected_revision=initial.revision,
        answers=[AgentQuestionAnswer(question_id="geometry_unit", option_ids=["mm"])],
    ))

    assert completed.status == "ready_for_review"
    assert completed.questions == []
    assert "更新可确定的建模参数" in (completed.answer or "")
    draft = repository.get_study(completed.study_id)
    assert draft.plan is not None
    assert draft.plan.material.name == "铝合金 6061-T6"
    assert {item.material_id for item in draft.plan.component_materials} == {"al-6061-t6"}
    assert draft.plan.heat_source is not None
    assert draft.plan.heat_source.shape == "surface"
    assert draft.plan.heat_source.total_power_w == 40
    assert draft.plan.heat_source.center_mm.x == 0
    assert [(item.selector.value, item.temperature_k) for item in draft.plan.boundaries] == [
        ("face.xmax", 298.15),
    ]
    assert draft.plan.convection is not None
    assert draft.plan.convection.ambient_temperature_k == 298.15
    assert draft.plan.convection.heat_transfer_coefficient_w_m2_k == 10
    assert draft.plan.mesh.target_element_size_mm == 0.5
    assert draft.plan.criteria[0].target.value == 353.15


def test_assistant_copies_confirmed_study_before_applying_explicit_prompt(tmp_path: Path) -> None:
    repository = FileRepository(tmp_path / "data")
    service = StudyService(
        repository=repository,
        planner=DeterministicPlanner(),
        geometry=CadFlowGeometryInspector(tmp_path / "missing-cadflow"),
    )
    assistant = AssistantService(
        repository=repository,
        service=service,
        modeling=ModelingService(service),
        task_manager=TaskManager(repository),
        provider=DeterministicKnowledgeProvider(),
    )
    workpiece = service.register_box(BoxWorkpieceInput(
        name="confirmed source",
        dimensions_mm={"x": 60, "y": 30, "z": 15},
    ))
    original = service.create_study(
        workpiece.workpiece_id,
        purpose="旧研究",
        require_confirmation=True,
    )
    original = service.confirm_study(original.study_id, StudyConfirmationRequest(
        expected_revision=original.draft_revision,
        materials_confirmed=True,
    ))

    session = assistant.create(AgentSessionCreateRequest(
        study_id=original.study_id,
        message=(
            "建立稳态导热仿真，材料为 6061-T6 铝合金，"
            "在 X 最小侧施加 40 W 总功率的热源，X 最大侧保持 25 ℃，"
            "环境 25 ℃，对流换热系数 10 W/(m²·K)，"
            "目标单元尺寸 0.5 mm，最高温度不超过 80 ℃。"
        ),
    ))

    copied = repository.get_study(session.study_id)
    assert copied.study_id != original.study_id
    assert copied.source_study_id == original.study_id
    assert copied.confirmation.status == "needs_input"
    assert session.status == "ready_for_review"
    assert "创建可编辑副本" in (session.answer or "")
    assert "无法建立可验证" not in (session.answer or "")
    assert copied.plan is not None
    assert copied.plan.heat_source is not None
    assert copied.plan.heat_source.total_power_w == 40
    assert copied.plan.mesh.target_element_size_mm == 0.5
    assert copied.plan.criteria[0].target.value == 353.15
