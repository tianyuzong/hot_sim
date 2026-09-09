"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    data_dir: Path
    cadflow_repo: Path
    planner_mode: str = "openai"
    openai_model: str = "gpt-5.5"
    max_upload_bytes: int = 100 * 1024 * 1024
    compute_backend: str = "auto"
    host: str = "127.0.0.1"
    port: int = 8000
    task_workers: int = 1
    task_queue_limit: int = 8
    task_timeout_seconds: int = 900
    retain_modeling_history: bool = True
    codex_executable: str = "codex"
    codex_home: Path | None = None
    codex_timeout_seconds: int = 120

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> Settings:
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        data_dir = _resolve_from_root(root, os.getenv("THERMOFLOW_DATA_DIR", "data"))
        cadflow_repo = _resolve_from_root(root, os.getenv("CADFLOW_REPO", "../CadFlow"))
        planner_mode = os.getenv("THERMOFLOW_PLANNER", "openai").strip().lower()
        if planner_mode not in {"openai", "codex", "deterministic"}:
            raise ValueError("THERMOFLOW_PLANNER 必须为 'openai'、'codex' 或 'deterministic'")
        codex_executable = os.getenv("THERMOFLOW_CODEX_EXECUTABLE", "codex").strip()
        codex_home = os.getenv("THERMOFLOW_CODEX_HOME", "").strip()
        codex_timeout = int(os.getenv("THERMOFLOW_CODEX_TIMEOUT_SECONDS", "120"))
        if not codex_executable or not 5 <= codex_timeout <= 600:
            raise ValueError("Codex 可执行程序不能为空，响应超时必须为 5-600 秒")
        max_upload_bytes = int(os.getenv("THERMOFLOW_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
        if max_upload_bytes <= 0:
            raise ValueError("THERMOFLOW_MAX_UPLOAD_BYTES 必须为正数")
        compute_backend = os.getenv("THERMOFLOW_COMPUTE", "auto").strip().lower()
        if compute_backend not in {"auto", "cpu", "cuda"}:
            raise ValueError("THERMOFLOW_COMPUTE 必须为 'auto'、'cpu' 或 'cuda'")
        port = int(os.getenv("THERMOFLOW_PORT", "8000"))
        if not 1 <= port <= 65_535:
            raise ValueError("THERMOFLOW_PORT 必须在 1 到 65535 之间")
        workers = int(os.getenv("THERMOFLOW_TASK_WORKERS", "1"))
        queue_limit = int(os.getenv("THERMOFLOW_TASK_QUEUE_LIMIT", "8"))
        timeout = int(os.getenv("THERMOFLOW_TASK_TIMEOUT_SECONDS", "900"))
        if not 1 <= workers <= 4 or not 0 <= queue_limit <= 100 or not 1 <= timeout <= 7200:
            raise ValueError("计算任务并发数、队列长度或超时设置超出允许范围")
        retain_history = os.getenv("THERMOFLOW_RETAIN_MODELING_HISTORY", "true").strip().lower()
        if retain_history not in {"true", "false"}:
            raise ValueError("THERMOFLOW_RETAIN_MODELING_HISTORY 必须为 true 或 false")
        return cls(
            project_root=root,
            data_dir=data_dir,
            cadflow_repo=cadflow_repo,
            planner_mode=planner_mode,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5").strip() or "gpt-5.5",
            max_upload_bytes=max_upload_bytes,
            compute_backend=compute_backend,
            host=os.getenv("THERMOFLOW_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=port,
            task_workers=workers,
            task_queue_limit=queue_limit,
            task_timeout_seconds=timeout,
            retain_modeling_history=retain_history == "true",
            codex_executable=codex_executable,
            codex_home=_resolve_from_root(root, codex_home) if codex_home else None,
            codex_timeout_seconds=codex_timeout,
        )


def _resolve_from_root(root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()
