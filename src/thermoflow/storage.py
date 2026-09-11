"""Filesystem-backed study repository with atomic JSON writes."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .models import (
    AgentRunRecord,
    MeshRecord,
    ProjectRecord,
    SimulationResult,
    StudyRecord,
    TaskRecord,
    WorkpieceRecord,
)

RecordT = TypeVar("RecordT", bound=BaseModel)
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_ARTIFACT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RecordNotFoundError(LookupError):
    pass


class FileRepository:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.projects = self.root / "projects"
        self.workpieces = self.root / "workpieces"
        self.studies = self.root / "studies"
        self.agent_runs = self.root / "agent-runs"
        self.tasks = self.root / "tasks"
        self.projects.mkdir(parents=True, exist_ok=True)
        self.workpieces.mkdir(parents=True, exist_ok=True)
        self.studies.mkdir(parents=True, exist_ok=True)
        self.agent_runs.mkdir(parents=True, exist_ok=True)
        self.tasks.mkdir(parents=True, exist_ok=True)

    def task_dir(self, task_id: str) -> Path:
        return self.tasks / _safe_id(task_id)

    def save_task(self, record: TaskRecord) -> None:
        _write_model(self.task_dir(record.task_id) / "task.json", record)

    def get_task(self, task_id: str) -> TaskRecord:
        return _read_model(self.task_dir(task_id) / "task.json", TaskRecord, "未找到计算任务")

    def list_tasks(self, study_id: str | None = None, project_id: str | None = None) -> list[TaskRecord]:
        records = [_read_model(path, TaskRecord, "未找到计算任务") for path in self.tasks.glob("*/task.json")]
        return sorted([record for record in records
                       if (study_id is None or record.study_id == study_id)
                       and (project_id is None or record.project_id == project_id)],
                      key=lambda record: record.created_at, reverse=True)

    @contextmanager
    def project_lock(self, project_id: str):
        with _file_lock(self.project_dir(project_id) / "project.lock", blocking=True):
            yield

    @contextmanager
    def workpiece_lock(self, workpiece_id: str):
        with _file_lock(self.workpiece_dir(workpiece_id) / "geometry.lock", blocking=False):
            yield

    @contextmanager
    def study_lock(self, study_id: str, *, blocking: bool = False):
        with _file_lock(self.study_dir(study_id) / "execution.lock", blocking=blocking):
            yield

    @contextmanager
    def modeling_lock(self, study_id: str):
        with _file_lock(self.study_dir(study_id) / "modeling.lock", blocking=False):
            yield

    @contextmanager
    def task_lock(self, task_id: str, *, blocking: bool = True):
        with _file_lock(self.task_dir(task_id) / "task.lock", blocking=blocking):
            yield

    def save_project(self, record: ProjectRecord) -> None:
        directory = self.project_dir(record.project_id)
        directory.mkdir(parents=True, exist_ok=True)
        _write_model(directory / "project.json", record)

    def get_project(self, project_id: str) -> ProjectRecord:
        return _read_model(
            self.project_dir(project_id) / "project.json",
            ProjectRecord,
            f"未找到项目 {project_id!r}",
        )

    def list_projects(self) -> list[ProjectRecord]:
        records = [
            ProjectRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.projects.glob("*/project.json")
        ]
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def save_workpiece(self, record: WorkpieceRecord) -> None:
        directory = self.workpiece_dir(record.workpiece_id)
        directory.mkdir(parents=True, exist_ok=True)
        _write_model(directory / "workpiece.json", record)

    def get_workpiece(self, workpiece_id: str) -> WorkpieceRecord:
        return _read_model(
            self.workpiece_dir(workpiece_id) / "workpiece.json",
            WorkpieceRecord,
            f"未找到工件 {workpiece_id!r}",
        )

    def list_workpieces(self, project_id: str | None = None) -> list[WorkpieceRecord]:
        records = [
            WorkpieceRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.workpieces.glob("*/workpiece.json")
        ]
        if project_id is not None:
            records = [record for record in records if record.project_id == project_id]
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    def save_study(self, record: StudyRecord) -> None:
        directory = self.study_dir(record.study_id)
        directory.mkdir(parents=True, exist_ok=True)
        _write_model(directory / "study.json", record)

    def get_study(self, study_id: str) -> StudyRecord:
        return _read_model(
            self.study_dir(study_id) / "study.json",
            StudyRecord,
            f"未找到仿真研究 {study_id!r}",
        )

    def list_studies(
        self,
        workpiece_id: str | None = None,
        project_id: str | None = None,
    ) -> list[StudyRecord]:
        records = [
            StudyRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.studies.glob("*/study.json")
        ]
        if workpiece_id is not None:
            records = [record for record in records if record.workpiece_id == workpiece_id]
        if project_id is not None:
            records = [record for record in records if record.project_id == project_id]
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def save_result(self, result: SimulationResult) -> None:
        _write_model(self.study_dir(result.study_id) / "result.json", result)

    def get_result(self, study_id: str) -> SimulationResult:
        return _read_model(
            self.study_dir(study_id) / "result.json",
            SimulationResult,
            f"未找到仿真研究 {study_id!r} 的结果",
        )

    def save_mesh(self, mesh: MeshRecord) -> None:
        _write_model(self.study_dir(mesh.study_id) / "mesh.json", mesh)

    def get_mesh(self, study_id: str) -> MeshRecord:
        return _read_model(
            self.study_dir(study_id) / "mesh.json",
            MeshRecord,
            f"未找到仿真研究 {study_id!r} 的网格",
        )

    def save_agent_run(self, record: AgentRunRecord) -> None:
        directory = self.agent_run_dir(record.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        _write_model(directory / "agent-run.json", record)

    def get_agent_run(self, run_id: str) -> AgentRunRecord:
        return _read_model(
            self.agent_run_dir(run_id) / "agent-run.json",
            AgentRunRecord,
            f"未找到 Agent 运行 {run_id!r}",
        )

    def list_agent_runs(
        self,
        *,
        project_id: str | None = None,
        workpiece_id: str | None = None,
        base_study_id: str | None = None,
    ) -> list[AgentRunRecord]:
        records = [
            AgentRunRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.agent_runs.glob("*/agent-run.json")
        ]
        if project_id is not None:
            records = [record for record in records if record.project_id == project_id]
        if workpiece_id is not None:
            records = [record for record in records if record.workpiece_id == workpiece_id]
        if base_study_id is not None:
            records = [record for record in records if record.base_study_id == base_study_id]
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    def project_dir(self, project_id: str) -> Path:
        return self.projects / _safe_id(project_id)

    def workpiece_dir(self, workpiece_id: str) -> Path:
        return self.workpieces / _safe_id(workpiece_id)

    def study_dir(self, study_id: str) -> Path:
        return self.studies / _safe_id(study_id)

    def agent_run_dir(self, run_id: str) -> Path:
        return self.agent_runs / _safe_id(run_id)

    def delete_project_dir(self, project_id: str) -> None:
        _remove_dir(self.project_dir(project_id))

    def delete_workpiece_dir(self, workpiece_id: str) -> None:
        _remove_dir(self.workpiece_dir(workpiece_id))

    def delete_study_dir(self, study_id: str) -> None:
        _remove_dir(self.study_dir(study_id))

    def delete_agent_run_dir(self, run_id: str) -> None:
        _remove_dir(self.agent_run_dir(run_id))

    def delete_task_dir(self, task_id: str) -> None:
        _remove_dir(self.task_dir(task_id))

    def artifact_path(self, study_id: str, name: str) -> Path:
        if not _ARTIFACT_PATTERN.fullmatch(name):
            raise ValueError("结果文件名无效")
        path = (self.study_dir(study_id) / "artifacts" / name).resolve()
        expected_parent = (self.study_dir(study_id) / "artifacts").resolve()
        if path.parent != expected_parent:
            raise ValueError("结果文件路径超出研究目录")
        if not path.is_file():
            raise RecordNotFoundError(f"未找到结果文件 {name!r}")
        return path


def _safe_id(value: str) -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError("记录 ID 无效")
    return value


def _remove_dir(path: Path) -> None:
    """Remove one validated record directory, if it still exists."""
    if path.exists():
        if not path.is_dir():
            raise ValueError(f"记录路径不是目录：{path.name}")
        shutil.rmtree(path)


def _write_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = model.model_dump(mode="json")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _file_lock(path: Path, *, blocking: bool):
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise ValueError("该研究已有正在执行的操作，请等待完成或取消任务") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _read_model(path: Path, model_type: type[RecordT], message: str) -> RecordT:
    if not path.is_file():
        raise RecordNotFoundError(message)
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))
