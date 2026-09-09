from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import trimesh
from fastapi.testclient import TestClient

from thermoflow.api import create_app
from thermoflow.models import (
    BoxWorkpieceInput, DimensionsMM, LengthUnit, MeshReviewRequest, StudyConfirmationRequest,
    TaskCreateRequest, SimulationOverrides,
)
from thermoflow.planner import DeterministicPlanner
from thermoflow.settings import Settings
from thermoflow.tasks import TERMINAL_TASK_STATES, TaskManager, TaskMonitor
from thermoflow.storage import FileRepository
from tests.test_confirmed_service import _service


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("Task did not reach the expected state before the test deadline")


def finished(repository, task_id):
    task = repository.get_task(task_id)
    return task if task.status in TERMINAL_TASK_STATES and task.finished_at is not None else None


def blocking_worker(data_dir, task_id, compute_backend, parent_pid):
    repository = FileRepository(Path(data_dir))
    TaskMonitor(repository, task_id).report("solving", None)
    time.sleep(60)


def ready_box(service):
    part = service.register_box(BoxWorkpieceInput(name="Thermal reference", dimensions_mm=DimensionsMM(x=10, y=6, z=4)))
    return service.create_study(part.workpiece_id)


def interrupted_publication_worker(data_dir, task_id, compute_backend, parent_pid):
    from thermoflow.cadflow_adapter import CadFlowGeometryInspector
    from thermoflow.service import StudyService

    repository = FileRepository(Path(data_dir))
    original_save = repository.save_result
    def pause_after_result_write(result):
        original_save(result)
        time.sleep(60)
    repository.save_result = pause_after_result_write
    task = repository.get_task(task_id)
    service = StudyService(repository, DeterministicPlanner(), CadFlowGeometryInspector(Path(data_dir) / "missing"), "cpu")
    service.execute_study(task.study_id, task_id=task_id, monitor=TaskMonitor(repository, task_id))


def interrupted_mesh_publication_worker(data_dir, task_id, compute_backend, parent_pid):
    from thermoflow.cadflow_adapter import CadFlowGeometryInspector
    from thermoflow.service import StudyService

    repository = FileRepository(Path(data_dir))
    original_save = repository.save_mesh

    def pause_after_mesh_write(mesh):
        original_save(mesh)
        time.sleep(60)

    repository.save_mesh = pause_after_mesh_write
    task = repository.get_task(task_id)
    service = StudyService(repository, DeterministicPlanner(), CadFlowGeometryInspector(Path(data_dir) / "missing"), "cpu")
    service.execute_mesh(task.study_id, task_id=task_id, monitor=TaskMonitor(repository, task_id))


def resource_failure_worker(data_dir, task_id, compute_backend, parent_pid):
    from thermoflow.solvers.analytic_box import AnalyticBoxSolver
    from thermoflow.tasks import run_task_worker

    def fail(*args, **kwargs):
        raise MemoryError("sensitive-token /private/internal/path")

    AnalyticBoxSolver.solve = fail
    run_task_worker(data_dir, task_id, compute_backend, parent_pid)


def test_real_background_mesh_and_transient_solve_are_restorable(tmp_path):
    service, repository = _service(tmp_path)
    part = service.register_stl("bracket.stl", trimesh.creation.box(extents=[10, 6, 4]).export(file_type="stl"))
    service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    study = service.create_study(part.workpiece_id, purpose="6061，瞬态导热，初始温度 350 K，持续 1 秒，步长 0.25 秒", require_confirmation=True)
    service.confirm_study(study.study_id, StudyConfirmationRequest(materials_confirmed=True))
    manager = TaskManager(repository, compute_backend="cpu")
    try:
        mesh_task = manager.submit(study.study_id, TaskCreateRequest(operation="mesh"))
        assert repository.get_study(study.study_id).active_task_id == mesh_task.task_id
        mesh_done = wait_for(lambda: finished(repository, mesh_task.task_id))
        assert mesh_done.status == "succeeded", mesh_done.message
        mesh = repository.get_mesh(study.study_id)
        if mesh.review_status == "pending":
            service.confirm_mesh(study.study_id, MeshReviewRequest(accept_warnings=True))
        solve_task = manager.submit(study.study_id, TaskCreateRequest(operation="solve"))
        # A new repository object (as on refresh) sees the persisted active operation.
        restored = FileRepository(repository.root).get_task(solve_task.task_id)
        assert restored.input_snapshot_sha256 == repository.get_study(study.study_id).input_snapshot_sha256
        result_task = wait_for(lambda: finished(repository, solve_task.task_id))
        assert result_task.status == "succeeded", result_task.message
        completed = repository.get_study(study.study_id)
        assert completed.status.value == "succeeded"
        assert completed.active_task_id is None
        transient_result = repository.get_result(study.study_id)
        assert len(transient_result.time_steps) == 201
        assert transient_result.time_steps[-1].time_s == pytest.approx(1)
        assert result_task.progress == 100
        assert not result_task.cancellable
        with pytest.raises(ValueError, match="尚未求解"):
            manager.submit(study.study_id, TaskCreateRequest(operation="solve"))
    finally:
        manager.close()


def test_running_cancel_and_timeout_stop_the_process_and_preserve_inputs(tmp_path, monkeypatch):
    import thermoflow.tasks as tasks
    monkeypatch.setattr(tasks, "run_task_worker", blocking_worker)
    service, repository = _service(tmp_path)
    study = ready_box(service)
    snapshot = study.input_snapshot_sha256
    manager = TaskManager(repository, compute_backend="cpu", timeout_seconds=10)
    try:
        task = manager.submit(study.study_id, TaskCreateRequest(operation="solve"))
        wait_for(lambda: repository.get_task(task.task_id).stage == "solving")
        process = manager._handles[task.task_id][0]
        assert process.is_alive()
        with pytest.raises(ValueError, match="占用|执行"):
            service.run_study(study.study_id)
        manager.cancel(task.task_id)
        assert wait_for(lambda: finished(repository, task.task_id)).status == "cancelled"
        assert task.task_id not in manager._handles
        restored = repository.get_study(study.study_id)
        assert restored.status.value == "ready"
        assert restored.input_snapshot_sha256 == snapshot
        assert restored.active_task_id is None

        timed = manager.submit(study.study_id, TaskCreateRequest(operation="solve", timeout_seconds=1))
        assert wait_for(lambda: finished(repository, timed.task_id)).status == "timed_out"
        assert timed.task_id not in manager._handles
        assert repository.get_study(study.study_id).input_snapshot_sha256 == snapshot
        assert not (repository.study_dir(study.study_id) / "result.json").exists()
    finally:
        manager.close()


def test_queue_limits_duplicate_submission_and_queued_cancel(tmp_path, monkeypatch):
    import thermoflow.tasks as tasks
    monkeypatch.setattr(tasks, "run_task_worker", blocking_worker)
    service, repository = _service(tmp_path)
    first, second, third = (ready_box(service) for _ in range(3))
    manager = TaskManager(repository, compute_backend="cpu", workers=1, queue_limit=1)
    try:
        active = manager.submit(first.study_id, TaskCreateRequest(operation="solve"))
        wait_for(lambda: repository.get_task(active.task_id).stage == "solving")
        queued = manager.submit(second.study_id, TaskCreateRequest(operation="solve"))
        with pytest.raises(ValueError, match="占用"):
            service.confirm_study(
                second.study_id, StudyConfirmationRequest(materials_confirmed=True)
            )
        assert repository.get_study(second.study_id).input_snapshot_sha256 == second.input_snapshot_sha256
        duplicate = manager.submit(second.study_id, TaskCreateRequest(operation="solve"))
        assert duplicate.task_id == queued.task_id
        with pytest.raises(ValueError, match="队列已满"):
            manager.submit(third.study_id, TaskCreateRequest(operation="solve"))
        manager.cancel(queued.task_id)
        assert wait_for(lambda: finished(repository, queued.task_id)).status == "cancelled"
        assert queued.task_id not in manager._handles
        assert repository.get_study(second.study_id).active_task_id is None
    finally:
        manager.close()


def test_queued_mesh_cancel_preserves_existing_mesh_and_review(tmp_path, monkeypatch):
    import thermoflow.tasks as tasks
    monkeypatch.setattr(tasks, "run_task_worker", blocking_worker)
    service, repository = _service(tmp_path)
    part = service.register_stl("bracket.stl", trimesh.creation.box(extents=[12, 8, 4]).export(file_type="stl"))
    service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    study = service.create_study(part.workpiece_id, require_confirmation=True,
                                overrides=SimulationOverrides(target_element_size_mm=1.0))
    service.confirm_study(study.study_id, StudyConfirmationRequest(materials_confirmed=True))
    service.generate_mesh(study.study_id)
    with repository.study_lock(study.study_id), pytest.raises(ValueError, match="执行"):
        service.confirm_mesh(study.study_id, MeshReviewRequest(accept_warnings=True))
    mesh = service.confirm_mesh(study.study_id, MeshReviewRequest(accept_warnings=True))
    manager = TaskManager(repository, compute_backend="cpu")
    try:
        active = manager.submit(ready_box(service).study_id, TaskCreateRequest(operation="solve"))
        wait_for(lambda: repository.get_task(active.task_id).stage == "solving")
        task = manager.submit(study.study_id, TaskCreateRequest(operation="mesh"))
        manager.cancel(task.task_id)
        assert wait_for(lambda: finished(repository, task.task_id)).status == "cancelled"
        assert repository.get_study(study.study_id).mesh_status.value == "ready"
        assert repository.get_mesh(study.study_id) == mesh
    finally:
        manager.close()


def test_incomplete_submission_is_interrupted_without_starting_worker(tmp_path, monkeypatch):
    service, repository = _service(tmp_path)
    study = ready_box(service)
    manager = TaskManager(repository, compute_backend="cpu")
    try:
        manager.start()
        with manager._lock:
            with monkeypatch.context() as patch:
                def fail_to_reserve(record):
                    raise OSError("Simulated write failure")
                patch.setattr(repository, "save_study", fail_to_reserve)
                with pytest.raises(OSError, match="Simulated"):
                    manager.submit(study.study_id, TaskCreateRequest(operation="solve"))
            task = repository.list_tasks(study_id=study.study_id)[0]
            assert repository.get_study(study.study_id).active_task_id is None
        recovered = wait_for(lambda: finished(repository, task.task_id))
        assert recovered.status == "interrupted"
        assert recovered.started_at is None
        assert repository.get_study(study.study_id) == study
    finally:
        manager.close()


def test_one_manager_per_directory_and_failed_start_releases_lease(tmp_path, monkeypatch):
    _, repository = _service(tmp_path)
    first, second = TaskManager(repository), TaskManager(repository)
    try:
        with monkeypatch.context() as patch:
            def fail_recovery():
                raise OSError("Simulated recovery failure")
            patch.setattr(first, "_recover", fail_recovery)
            with pytest.raises(OSError):
                first.start()
        second.start()
        with pytest.raises(ValueError, match="只允许一个"):
            first.start()
        second.close()
        first.start()
    finally:
        first.close()
        second.close()


def test_resource_failure_is_actionable_and_diagnostics_are_sanitized(tmp_path, monkeypatch):
    import thermoflow.tasks as tasks
    monkeypatch.setattr(tasks, "run_task_worker", resource_failure_worker)
    service, repository = _service(tmp_path)
    study = ready_box(service)
    manager = TaskManager(repository, compute_backend="cpu")
    try:
        task = manager.submit(study.study_id, TaskCreateRequest(operation="solve"))
        failed = wait_for(lambda: finished(repository, task.task_id))
        assert failed.status == "failed"
        assert failed.failure_reason == "resource_exhausted"
        assert "资源不足" in failed.message
        diagnostic_text = (repository.task_dir(task.task_id) / "diagnostic.json").read_text()
        assert "sensitive-token" not in diagnostic_text
        assert "/private/" not in diagnostic_text
        assert json.loads(diagnostic_text)["type"] == "MemoryError"
        assert repository.get_study(study.study_id).active_task_id is None
        assert repository.get_study(study.study_id).status.value == "ready"
    finally:
        manager.close()


def test_server_shutdown_and_restart_recover_interrupted_study(tmp_path, monkeypatch):
    import thermoflow.tasks as tasks
    real_worker = tasks.run_task_worker
    monkeypatch.setattr(tasks, "run_task_worker", blocking_worker)
    service, repository = _service(tmp_path)
    study = ready_box(service)
    manager = TaskManager(repository, compute_backend="cpu")
    task = manager.submit(study.study_id, TaskCreateRequest(operation="solve"))
    wait_for(lambda: repository.get_task(task.task_id).stage == "solving")
    queued_study = ready_box(service)
    queued = manager.submit(queued_study.study_id, TaskCreateRequest(operation="solve"))
    manager.close()
    assert repository.get_task(task.task_id).status == "interrupted"
    assert repository.get_task(queued.task_id).status == "queued"
    monkeypatch.setattr(tasks, "run_task_worker", real_worker)
    restarted = TaskManager(FileRepository(repository.root), compute_backend="cpu")
    try:
        retried = restarted.submit(study.study_id, TaskCreateRequest(operation="solve"))
        assert retried.task_id != task.task_id
        assert wait_for(lambda: finished(repository, queued.task_id)).status == "succeeded"
        assert wait_for(lambda: finished(repository, retried.task_id)).status == "succeeded"
        assert repository.get_study(study.study_id).input_snapshot_sha256 == study.input_snapshot_sha256
    finally:
        restarted.close()


@pytest.mark.parametrize("name, value", [
    ("THERMOFLOW_TASK_WORKERS", "0"), ("THERMOFLOW_TASK_WORKERS", "5"),
    ("THERMOFLOW_TASK_QUEUE_LIMIT", "-1"), ("THERMOFLOW_TASK_QUEUE_LIMIT", "101"),
    ("THERMOFLOW_TASK_TIMEOUT_SECONDS", "0"), ("THERMOFLOW_TASK_TIMEOUT_SECONDS", "7201"),
])
def test_invalid_task_deployment_limits_are_rejected(tmp_path, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="超出允许范围"):
        Settings.from_env(tmp_path)


def test_background_api_returns_quickly_and_restores_task_by_study(tmp_path):
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / "data", cadflow_repo=tmp_path / "missing",
                        planner_mode="deterministic", compute_backend="cpu")
    app = create_app(settings, DeterministicPlanner())
    with TestClient(app) as client:
        study = ready_box(app.state.service)
        submitted = client.post(f"/v1/studies/{study.study_id}/tasks", json={"operation": "solve"})
        assert submitted.status_code == 202, submitted.text
        task = submitted.json()
        assert task["status"] == "queued"
        queried = client.get("/v1/tasks", params={"study_id": study.study_id}).json()
        assert queried[0]["task_id"] == task["task_id"]
        done = wait_for(lambda: finished(app.state.repository, task["task_id"]))
        assert done.status == "succeeded", done.message
        assert client.get(f"/v1/studies/{study.study_id}/result").status_code == 200
        assert client.get(f"/v1/tasks/{task['task_id']}").json()["progress"] == 100
        assert client.post(f"/v1/tasks/{task['task_id']}/cancel").json()["status"] == "succeeded"


def test_partial_publication_is_not_exposed_as_completed_result(tmp_path, monkeypatch):
    import thermoflow.tasks as tasks
    monkeypatch.setattr(tasks, "run_task_worker", interrupted_publication_worker)
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / "data", cadflow_repo=tmp_path / "missing",
                        planner_mode="deterministic", compute_backend="cpu", task_timeout_seconds=3)
    app = create_app(settings, DeterministicPlanner())
    with TestClient(app) as client:
        study = ready_box(app.state.service)
        task = app.state.task_manager.submit(study.study_id, TaskCreateRequest(operation="solve"))
        wait_for(lambda: app.state.repository.get_task(task.task_id).stage == "saving")
        assert client.get(f"/v1/studies/{study.study_id}/result").status_code == 404
        assert client.get(f"/v1/studies/{study.study_id}/artifacts/temperature.vtk").status_code == 404
        cancelled = client.post(f"/v1/tasks/{task.task_id}/cancel")
        assert cancelled.status_code == 409
        assert wait_for(lambda: finished(app.state.repository, task.task_id)).status == "timed_out"
        assert app.state.repository.get_study(study.study_id).status.value == "ready"
        assert client.get(f"/v1/studies/{study.study_id}/result").status_code == 404


def test_partial_mesh_publication_is_not_exposed_as_usable_mesh(tmp_path, monkeypatch):
    import thermoflow.tasks as tasks
    monkeypatch.setattr(tasks, "run_task_worker", interrupted_mesh_publication_worker)
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / "data", cadflow_repo=tmp_path / "missing",
                        planner_mode="deterministic", compute_backend="cpu", task_timeout_seconds=3)
    app = create_app(settings, DeterministicPlanner())
    with TestClient(app) as client:
        service, repository = app.state.service, app.state.repository
        part = service.register_stl("bracket.stl", trimesh.creation.box(extents=[12, 8, 4]).export(file_type="stl"))
        service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
        study = service.create_study(part.workpiece_id, require_confirmation=True)
        service.confirm_study(
            study.study_id, StudyConfirmationRequest(materials_confirmed=True)
        )
        task = app.state.task_manager.submit(study.study_id, TaskCreateRequest(operation="mesh"))
        wait_for(lambda: repository.get_task(task.task_id).stage == "saving")
        assert client.get(f"/v1/studies/{study.study_id}/mesh").status_code == 404
        artifact_name = repository.get_mesh(study.study_id).artifacts[0].name
        assert client.get(f"/v1/studies/{study.study_id}/artifacts/{artifact_name}").status_code == 404
        assert wait_for(lambda: finished(repository, task.task_id)).status == "timed_out"
        assert repository.get_study(study.study_id).mesh_status.value == "failed"
        assert client.get(f"/v1/studies/{study.study_id}/mesh").status_code == 404


@pytest.mark.parametrize("mesh_size, expected_status", [(0.5, "succeeded"), (1.0, "needs_review")])
def test_apply_and_solve_runs_once_or_pauses_for_mesh_review(tmp_path, mesh_size, expected_status):
    service, repository = _service(tmp_path)
    part = service.register_stl("bracket.stl", trimesh.creation.box(extents=[12, 8, 4]).export(file_type="stl"))
    service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
    study = service.create_study(part.workpiece_id, require_confirmation=True,
                                overrides=SimulationOverrides(target_element_size_mm=mesh_size))
    service.confirm_study(study.study_id, StudyConfirmationRequest(materials_confirmed=True))
    manager = TaskManager(repository, compute_backend="cpu")
    try:
        task = manager.submit(study.study_id, TaskCreateRequest(operation="apply_and_solve"))
        completed = wait_for(lambda: finished(repository, task.task_id))
        assert completed.status == expected_status, completed.message
        assert repository.get_study(study.study_id).active_task_id is None
        if expected_status == "needs_review":
            assert repository.get_mesh(study.study_id).review_status == "pending"
            assert not (repository.study_dir(study.study_id) / "result.json").exists()
            service.confirm_mesh(study.study_id, MeshReviewRequest(accept_warnings=True))
            continuation = manager.submit(study.study_id, TaskCreateRequest(operation="solve"))
            assert wait_for(lambda: finished(repository, continuation.task_id)).status == "succeeded"
        assert repository.get_study(study.study_id).status.value == "succeeded"
    finally:
        manager.close()
