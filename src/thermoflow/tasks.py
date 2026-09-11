"""Persistent, bounded computation queue with isolated worker processes."""

from __future__ import annotations

import hashlib
import json
import logging
import multiprocessing
import os
import signal
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import MeshStatus, StudyStatus, TaskCreateRequest, TaskRecord
from .storage import FileRepository, RecordNotFoundError

TERMINAL_TASK_STATES = {"succeeded", "needs_review", "cancelled", "failed", "timed_out", "interrupted"}
STAGES = {
    "queued": "等待计算资源", "validating": "检查确认输入", "geometry": "读取几何",
    "meshing": "构建计算网格", "quality": "检查网格质量", "assembly": "组装热传导系统",
    "solving": "计算温度场", "fields": "生成结果场", "saving": "保存研究结果", "finished": "任务结束",
}


def _now():
    return datetime.now(timezone.utc)


class TaskCancelled(RuntimeError):
    pass


class TaskMonitor:
    def __init__(self, repository: FileRepository, task_id: str):
        self.repository, self.task_id = repository, task_id
        self.last_update = 0.0
        self.last_stage = None
        self.offset, self.span = 0, 1

    def report(self, stage: str, progress: float | None = None):
        with self.repository.task_lock(self.task_id):
            task = self.repository.get_task(self.task_id)
            if task.status == "cancelling" or task.status in TERMINAL_TASK_STATES:
                raise TaskCancelled("任务已取消")
            now = time.monotonic()
            if now - self.last_update < 0.25 and stage == self.last_stage:
                return
            self.repository.save_task(task.model_copy(update={
                "status": "running", "stage": stage,
                "progress": self.offset + self.span * progress if progress is not None else None,
                "cancellable": True,
                "updated_at": _now(), "message": STAGES[stage],
            }))
            self.last_update, self.last_stage = now, stage

    @contextmanager
    def publishing(self):
        # Cancel and result publication serialize on the same lock.
        with self.repository.task_lock(self.task_id):
            task = self.repository.get_task(self.task_id)
            if task.status == "cancelling" or task.status in TERMINAL_TASK_STATES:
                raise TaskCancelled("任务已取消")
            self.repository.save_task(task.model_copy(update={
                "stage": "saving", "progress": self.offset + self.span * 98, "cancellable": False,
                "message": STAGES["saving"], "updated_at": _now(),
            }))
            yield


def run_task_worker(data_dir: str, task_id: str, compute_backend: str, parent_pid: int):
    # On Linux a server crash must not leave an unowned numerical process alive.
    if os.name == "posix" and Path("/proc").exists():
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            raise RuntimeError("无法设置计算进程生命周期")
        if os.getppid() != parent_pid:
            return
    from .solvers.compute import limit_worker_blas_threads

    with limit_worker_blas_threads():
        _execute_task_worker(data_dir, task_id, compute_backend)


def _execute_task_worker(data_dir: str, task_id: str, compute_backend: str):
    from .cadflow_adapter import CadFlowGeometryInspector
    from .planner import DeterministicPlanner
    from .service import StudyService, _study_input_snapshot_sha256

    repository = FileRepository(Path(data_dir))
    task = repository.get_task(task_id)
    monitor = TaskMonitor(repository, task_id)
    try:
        monitor.report("validating", 0)
        study = repository.get_study(task.study_id)
        if task.input_snapshot_sha256 != _study_input_snapshot_sha256(study):
            raise ValueError("任务输入与确认快照不一致")
        service = StudyService(repository, DeterministicPlanner(),
                               CadFlowGeometryInspector(Path(data_dir) / "unused-cadflow"), compute_backend)
        if task.operation in {"mesh", "apply_and_solve"}:
            monitor.span = 0.4 if task.operation == "apply_and_solve" else 1
            service.execute_mesh(task.study_id, task_id=task_id, monitor=monitor)
        if task.operation == "solve" or (
            task.operation == "apply_and_solve"
            and repository.get_study(task.study_id).mesh_status == MeshStatus.READY
        ):
            monitor.offset, monitor.span = (40, 0.6) if task.operation == "apply_and_solve" else (0, 1)
            service.execute_study(task.study_id, task_id=task_id, monitor=monitor)
    except TaskCancelled:
        return
    except Exception as exc:
        # Persist useful failure locations without raw exception text, credentials or absolute paths.
        diagnostic = {
            "code": task.diagnostic_code, "type": type(exc).__name__,
            "frames": [{"module": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                       for frame in traceback.extract_tb(exc.__traceback__)],
        }
        (repository.task_dir(task_id) / "diagnostic.json").write_text(
            json.dumps(diagnostic, ensure_ascii=True), encoding="utf-8",
        )
        with repository.task_lock(task_id):
            current = repository.get_task(task_id)
            if current.status != "cancelling":
                repository.save_task(current.model_copy(update={
                    "cancellable": False, "updated_at": _now(),
                    "failure_reason": "resource_exhausted" if isinstance(exc, MemoryError) or type(exc).__name__ in {"OutOfMemoryError", "MemoryAllocationError"}
                    else "invalid_input" if isinstance(exc, ValueError) else "solver_error",
                    "message": "计算未完成，请检查几何、网格与边界条件后重试；诊断编号 " + task.diagnostic_code,
                }))


class TaskManager:
    def __init__(self, repository: FileRepository, *, compute_backend="auto", workers=1,
                 queue_limit=8, timeout_seconds=900):
        self.repository = repository
        self.compute_backend = compute_backend
        self.workers, self.queue_limit, self.timeout_seconds = workers, queue_limit, timeout_seconds
        self._handles = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._lease = None
        self._context = multiprocessing.get_context("spawn")
        self._scheduler_error = False

    def start(self):
        import fcntl
        with self._lock:
            if self._thread is not None:
                return
            lease = (self.repository.tasks / "manager.lock").open("a")
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                lease.close()
                raise ValueError("同一数据目录只允许一个计算任务调度进程，请使用单个服务工作进程") from exc
            self._lease = lease
            self._stop.clear()
            try:
                self._recover()
                self._thread = threading.Thread(target=self._watch, name="thermoflow-tasks", daemon=True)
                self._thread.start()
            except Exception:
                lease.close()
                self._lease = self._thread = None
                raise

    def submit(self, study_id: str, request: TaskCreateRequest) -> TaskRecord:
        from .service import _study_input_snapshot_sha256
        self.start()
        with self._lock, self.repository.study_lock(study_id):
            if self._scheduler_error:
                raise ValueError("计算调度服务暂时不可用，请稍后重试")
            study = self.repository.get_study(study_id)
            if study.active_task_id:
                existing = self.repository.get_task(study.active_task_id)
                if existing.operation == request.operation and existing.status not in TERMINAL_TASK_STATES:
                    return existing
                raise ValueError("该研究已有计算任务，请等待完成或取消")
            if study.status not in {StudyStatus.PLANNED, StudyStatus.READY, StudyStatus.FAILED} or study.confirmation.status != "confirmed":
                raise ValueError("只有输入已确认且尚未求解的研究可以提交任务")
            if study.plan is None or study.policy is None or not study.policy.accepted:
                raise ValueError("研究输入未通过校验")
            snapshot = _study_input_snapshot_sha256(study)
            if snapshot != study.input_snapshot_sha256:
                raise ValueError("研究已确认输入发生变化，请重新确认")
            uses_voxel_mesh = study.plan.solver.backend in {"voxel_stl_v1", "tetra_stl_v1"}
            if request.operation in {"mesh", "apply_and_solve"} and not uses_voxel_mesh:
                raise ValueError("当前研究使用解析求解器，不需要网格任务")
            if request.operation == "solve" and uses_voxel_mesh and study.mesh_status != MeshStatus.READY:
                raise ValueError("请先生成网格并确认质量风险")
            active = [task for task in self.repository.list_tasks() if task.status not in TERMINAL_TASK_STATES]
            if len(active) >= self.workers + self.queue_limit:
                raise ValueError("计算队列已满，请稍后重试")
            timeout = request.timeout_seconds or self.timeout_seconds
            if timeout > self.timeout_seconds:
                raise ValueError("任务超时设置超出本部署允许的计算时长")
            task = TaskRecord(
                task_id="task-" + uuid.uuid4().hex, study_id=study_id, project_id=study.project_id,
                study_name=study.plan.study_name, operation=request.operation, timeout_seconds=timeout,
                input_snapshot_sha256=snapshot, diagnostic_code="TF-" + uuid.uuid4().hex[:10].upper(),
            )
            self.repository.save_task(task)
            self.repository.save_study(study.model_copy(update={"active_task_id": task.task_id, "updated_at": _now()}))
            return task

    def cancel(self, task_id: str) -> TaskRecord:
        self.start()
        with self._lock, self.repository.task_lock(task_id, blocking=False):
            task = self.repository.get_task(task_id)
            if task.status in TERMINAL_TASK_STATES:
                return task
            if not task.cancellable:
                raise ValueError("结果正在保存，请等待完成")
            cancelled = task.model_copy(update={"status": "cancelling", "message": "正在取消计算",
                                                "updated_at": _now(), "cancellable": False})
            self.repository.save_task(cancelled)
            return cancelled

    def _watch(self):
        while not self._stop.wait(0.2):
            with self._lock:
                try:
                    self._tick()
                    self._scheduler_error = False
                except Exception:
                    self._scheduler_error = True
                    logging.getLogger(__name__).exception("Computation scheduler could not update task state")

    def _tick(self):
        for task_id, (process, started) in list(self._handles.items()):
            task = self.repository.get_task(task_id)
            timed_out = time.monotonic() - started >= task.timeout_seconds
            if process.is_alive() and (task.status == "cancelling" or timed_out):
                process.terminate()
                process.join(timeout=0.5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=0.5)
            if not process.is_alive():
                process.join()
                self._handles.pop(task_id)
                self._settle(task, "timed_out" if timed_out else "cancelled" if task.status == "cancelling" else "failed")
                process.close()
        self._recover()
        queued = sorted((task for task in self.repository.list_tasks() if task.status == "queued"),
                        key=lambda task: task.created_at)
        for task in queued[:max(0, self.workers - len(self._handles))]:
            with self.repository.task_lock(task.task_id):
                current = self.repository.get_task(task.task_id)
                if current.status != "queued":
                    continue
                self.repository.save_task(current.model_copy(update={
                    "status": "running", "started_at": _now(), "updated_at": _now(),
                    "message": "准备计算", "stage": "validating", "progress": 0,
                }))
            process = self._context.Process(target=run_task_worker,
                args=(str(self.repository.root), task.task_id, self.compute_backend, os.getpid()), daemon=True)
            try:
                process.start()
                self._handles[task.task_id] = (process, time.monotonic())
            except Exception:
                self._settle(task, "failed")

    def _recover(self):
        for task in self.repository.list_tasks():
            if task.status == "queued" and self.repository.get_study(task.study_id).active_task_id != task.task_id:
                # A crash between the task write and study reservation must not start unowned work.
                self._settle(task, "interrupted")
            elif task.task_id not in self._handles and task.status not in TERMINAL_TASK_STATES | {"queued"}:
                self._settle(task, "cancelled" if task.status == "cancelling" else "interrupted")
            elif task.status in TERMINAL_TASK_STATES:
                study = self.repository.get_study(task.study_id)
                if study.active_task_id == task.task_id:
                    self._settle(task, task.status)

    def _committed(self, task, study):
        try:
            if study.input_snapshot_sha256 != task.input_snapshot_sha256:
                return False
            if task.operation in {"solve", "apply_and_solve"}:
                if study.status != StudyStatus.SUCCEEDED:
                    return False
                record = self.repository.get_result(task.study_id)
            else:
                if study.mesh_status not in {MeshStatus.READY, MeshStatus.NEEDS_REVIEW, MeshStatus.BLOCKED}:
                    return False
                record = self.repository.get_mesh(task.study_id)
                if record.generated_at < task.created_at or record.plan_snapshot_sha256 != task.input_snapshot_sha256:
                    return False
            return bool(record.artifacts) and all(
                hashlib.sha256(self.repository.artifact_path(task.study_id, item.name).read_bytes()).hexdigest() == item.sha256
                for item in record.artifacts
            )
        except (RecordNotFoundError, OSError, ValueError):
            return False

    def _settle(self, task: TaskRecord, reason: str):
        try:
            with self.repository.study_lock(task.study_id), self.repository.task_lock(task.task_id):
                current = self.repository.get_task(task.task_id)
                study = self.repository.get_study(task.study_id)
                committed = self._committed(current, study)
                review_required = (
                    task.operation == "apply_and_solve"
                    and study.mesh_status in {MeshStatus.NEEDS_REVIEW, MeshStatus.BLOCKED}
                    and self._committed(current.model_copy(update={"operation": "mesh"}), study)
                )
                state = "succeeded" if committed else "needs_review" if review_required else reason
                messages = {"succeeded": "网格已生成，请检查质量" if task.operation == "mesh" else "求解完成，可以查看结果",
                            "cancelled": "计算已取消，确认输入已保留，可重新提交", "timed_out": "计算超过时间上限，输入已保留，请调整网格或计算时长后重试",
                            "interrupted": "计算服务中断，输入已保留，可重新提交",
                            "failed": "计算未完成，输入已保留，请检查网格与边界后重试；诊断编号 " + current.diagnostic_code}
                messages["needs_review"] = "网格质量未达到求解要求，请修改网格后再运行" if study.mesh_status == MeshStatus.BLOCKED else "网格已生成，请先查看并确认质量风险，然后继续求解"
                if current.failure_reason == "resource_exhausted":
                    messages["failed"] = "计算资源不足，输入已保留，请减小网格规模或缩短计算时段后重试"
                if study.active_task_id == task.task_id:
                    updates = {"active_task_id": None, "updated_at": _now()}
                    if not committed and not review_required:
                        if task.operation == "mesh" or (task.operation == "apply_and_solve" and study.mesh_status == MeshStatus.GENERATING):
                            if current.started_at is not None or study.mesh_status in {MeshStatus.GENERATING, MeshStatus.FAILED}:
                                updates.update(mesh_status=MeshStatus.FAILED, mesh_snapshot_sha256=None, mesh_failure=messages[state])
                        else:
                            updates.update(status=StudyStatus.READY, failure=messages[state],
                                           evaluation_status="not_evaluated", evaluation_summary=[])
                    self.repository.save_study(study.model_copy(update=updates))
                self.repository.save_task(current.model_copy(update={
                    "status": state, "stage": "finished", "progress": 100 if committed else current.progress,
                    "finished_at": _now(), "updated_at": _now(), "cancellable": False, "message": messages[state],
                    "failure_reason": None if committed or review_required else {"cancelled": "cancelled", "timed_out": "timeout", "interrupted": "worker_lost"}.get(state, current.failure_reason or "solver_error"),
                }))
        except ValueError:
            # A surviving worker owns the study lock; it must exit before recovery writes.
            return

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self._lock:
            for task_id, (process, _) in list(self._handles.items()):
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=0.5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=0.5)
                self._settle(self.repository.get_task(task_id), "interrupted")
                process.close()
            self._handles.clear()
            self._thread = None
            if self._lease is not None:
                self._lease.close()
                self._lease = None
