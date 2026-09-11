"""Real workflow boundaries must remain recoverable after invalid input or failure."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
import trimesh
from fastapi.testclient import TestClient

from thermoflow.api import create_app
from thermoflow.models import (
    BoxWorkpieceInput, DimensionsMM, LengthUnit, ProjectCreateRequest, SimulationOverrides,
    StudyConfirmationRequest, StudyCopyRequest, StudyStatus, TaskCreateRequest,
)
from thermoflow.settings import Settings
from thermoflow.storage import RecordNotFoundError
from thermoflow.tasks import TaskManager

from .test_projects import _service


def _stl(service, extents=(20, 10, 5)):
    return service.register_stl("unit-check.stl", trimesh.creation.box(extents=extents).export(file_type="stl"))


def test_unit_change_uses_source_scale_for_all_geometry_metadata(tmp_path):
    service, repository = _service(tmp_path)
    original = _stl(service)
    service.confirm_workpiece_unit(original.workpiece_id, LengthUnit.METER)
    changed = service.confirm_workpiece_unit(original.workpiece_id, LengthUnit.MILLIMETER)
    assert changed.dimensions_mm == original.source_dimensions
    for field in ("bbox", "volume", "area"):
        assert changed.geometry.summary[field] == pytest.approx(original.geometry.summary[field])
    assert changed.geometry.summary["preview"]["vertices_mm"] == original.geometry.summary["preview"]["vertices_source"]
    assert repository.get_workpiece(original.workpiece_id).geometry == changed.geometry


def test_unit_confirmation_retry_is_idempotent_after_creating_draft(tmp_path):
    service, repository = _service(tmp_path)
    original = _stl(service)
    confirmed = service.confirm_workpiece_unit(original.workpiece_id, LengthUnit.MILLIMETER)
    draft = service.create_study(original.workpiece_id, require_confirmation=True, planning_mode="manual")
    retry = service.confirm_workpiece_unit(original.workpiece_id, LengthUnit.MILLIMETER)
    assert retry == confirmed
    assert repository.get_study(draft.study_id) == draft
    with pytest.raises(ValueError, match="几何尺度"):
        service.confirm_workpiece_unit(original.workpiece_id, LengthUnit.METER)


def test_invalid_unit_scale_does_not_corrupt_any_saved_workpiece(tmp_path):
    service, repository = _service(tmp_path)
    original = _stl(service, (2000, 10, 5))
    directory = repository.workpiece_dir(original.workpiece_id)
    before = (directory / "workpiece.json").read_bytes()
    with pytest.raises(ValueError):
        service.confirm_workpiece_unit(original.workpiece_id, LengthUnit.METER)
    assert (directory / "workpiece.json").read_bytes() == before
    assert not (directory / "source_mm.stl").exists()
    assert service.list_workpieces() == [original]
    assert service.confirm_workpiece_unit(original.workpiece_id, LengthUnit.MILLIMETER).unit_confirmed


def test_copy_with_invalid_settings_remains_editable_and_can_be_repaired_on_confirm(tmp_path):
    service, repository = _service(tmp_path)
    original = _stl(service)
    service.confirm_workpiece_unit(original.workpiece_id, LengthUnit.MILLIMETER)
    draft = service.create_study(original.workpiece_id, require_confirmation=True, planning_mode="manual")
    copied = service.copy_study(draft.study_id, StudyCopyRequest(
        overrides=SimulationOverrides(duration_s=60, time_step_s=100),
    ))
    assert not copied.policy.accepted
    assert copied.status == StudyStatus.NEEDS_INPUT
    with pytest.raises(ValueError, match="未通过校验"):
        service.confirm_study(copied.study_id, StudyConfirmationRequest(materials_confirmed=True))
    # Persisted older rejected drafts are recoverable by the same confirmation path.
    repository.save_study(copied.model_copy(update={"status": StudyStatus.REJECTED}))
    repaired = service.confirm_study(copied.study_id, StudyConfirmationRequest(
        materials_confirmed=True, overrides=SimulationOverrides(time_step_s=0.3),
    ))
    assert repaired.status == StudyStatus.READY
    assert repaired.policy.accepted
    assert repository.get_study(draft.study_id) == draft


def test_failed_confirmed_solve_keeps_inputs_and_accepts_a_retry(tmp_path, monkeypatch):
    service, repository = _service(tmp_path)
    workpiece = service.register_box(BoxWorkpieceInput(name="retry", dimensions_mm=DimensionsMM(x=20, y=10, z=5)))
    study = service.create_study(workpiece.workpiece_id)
    solver = service.solvers[study.plan.solver.backend]
    with monkeypatch.context() as patch:
        def fail(**kwargs):
            raise RuntimeError("temporary numerical worker failure")
        patch.setattr(solver, "solve", fail)
        with pytest.raises(RuntimeError):
            service.run_study(study.study_id)
    failed = repository.get_study(study.study_id)
    assert failed.status == StudyStatus.FAILED
    assert failed.input_snapshot_sha256 == study.input_snapshot_sha256
    manager = TaskManager(repository, compute_backend="cpu")
    monkeypatch.setattr(manager, "start", lambda: None)
    task = manager.submit(study.study_id, TaskCreateRequest(operation="solve"))
    assert task.status == "queued"
    assert task.input_snapshot_sha256 == study.input_snapshot_sha256
    repository.save_study(failed.model_copy(update={"active_task_id": None}))
    repository.delete_task_dir(task.task_id)
    assert service.run_study(study.study_id).status == StudyStatus.SUCCEEDED


def _app(tmp_path):
    return create_app(Settings(project_root=tmp_path, data_dir=tmp_path / "data",
        cadflow_repo=tmp_path / "missing", planner_mode="deterministic", compute_backend="cpu"))


def test_upload_to_missing_project_returns_not_found(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        response = client.post("/v1/workpieces/files", data={"project_id": "project-missing"},
            files={"file": ("test.stl", b"invalid", "model/stl")})
        assert response.status_code == 404


def test_upload_geometry_inspection_does_not_block_other_requests(tmp_path, monkeypatch):
    app = _app(tmp_path)
    entered, release = Event(), Event()
    def inspect(*args):
        entered.set()
        assert release.wait(10)
        raise RecordNotFoundError("test project unavailable")
    monkeypatch.setattr(app.state.service, "register_file", inspect)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        upload = pool.submit(client.post, "/v1/workpieces/files", files={"file": ("large.stl", b"STL", "model/stl")})
        try:
            assert entered.wait(2)
            materials = pool.submit(client.get, "/v1/materials")
            assert materials.result(timeout=2).status_code == 200
        finally:
            release.set()
        assert upload.result(timeout=2).status_code == 404


def test_concurrent_uploads_preserve_every_project_geometry_version(tmp_path, monkeypatch):
    service, repository = _service(tmp_path)
    project = service.create_project(ProjectCreateRequest(name="parallel imports"))
    import thermoflow.service as service_module
    original_inspect = service_module.inspect_stl
    both_inspecting = Barrier(2)
    def inspect(path):
        both_inspecting.wait(timeout=5)
        return original_inspect(path)
    monkeypatch.setattr(service_module, "inspect_stl", inspect)
    payload = trimesh.creation.box(extents=[20, 10, 5]).export(file_type="stl")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.register_stl, f"part-{index}.stl", payload, project.project_id)
                   for index in range(2)]
        imported = [future.result(timeout=10) for future in futures]
    assert set(repository.get_project(project.project_id).workpiece_ids) == {item.workpiece_id for item in imported}
