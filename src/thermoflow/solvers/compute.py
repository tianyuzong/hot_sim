"""CPU/CUDA sparse linear-system dispatch with explicit runtime diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any


@dataclass(frozen=True, slots=True)
class SparseSolveOutcome:
    solution: Any
    backend: str
    device: str | None = None
    fallback_reason: str | None = None


def compute_runtime(preference: str) -> dict[str, object]:
    """Describe configured and currently usable sparse-compute resources."""
    _validate_preference(preference)
    cuda_available, device, runtime_version, error = _probe_cuda()
    selected_backend = "cuda-cupy" if preference != "cpu" and cuda_available else "cpu-scipy"
    return {
        "preference": preference,
        "ready": preference != "cuda" or cuda_available,
        "selected_backend": selected_backend,
        "cuda_available": cuda_available,
        "device": device,
        "cuda_runtime_version": runtime_version,
        "detail": error,
    }


def solve_sparse_system(
    matrix: Any,
    rhs: Any,
    *,
    relative_tolerance: float,
    max_iterations: int,
    preference: str,
) -> SparseSolveOutcome:
    """Solve on CUDA when requested and available, otherwise use SciPy on CPU."""
    _validate_preference(preference)
    if preference == "cpu":
        return _solve_cpu(matrix, rhs, relative_tolerance, max_iterations)

    try:
        return _solve_cuda(matrix, rhs, relative_tolerance, max_iterations)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        if preference == "cuda":
            raise RuntimeError(f"CUDA 后端不可用或求解失败：{reason}") from exc
        cpu_outcome = _solve_cpu(matrix, rhs, relative_tolerance, max_iterations)
        return SparseSolveOutcome(
            solution=cpu_outcome.solution,
            backend=cpu_outcome.backend,
            device=cpu_outcome.device,
            fallback_reason=reason,
        )


def _solve_cpu(
    matrix: Any,
    rhs: Any,
    relative_tolerance: float,
    max_iterations: int,
) -> SparseSolveOutcome:
    import numpy as np
    from scipy.sparse.linalg import cg, spsolve

    solution, info = cg(
        matrix,
        rhs,
        rtol=relative_tolerance,
        atol=0.0,
        maxiter=max_iterations,
    )
    backend = "cpu-scipy-cg"
    if info != 0 or not np.isfinite(solution).all():
        solution = spsolve(matrix, rhs)
        backend = "cpu-scipy-spsolve"
    if not np.isfinite(solution).all():
        raise RuntimeError("CPU 稳态温度线性系统未收敛")
    return SparseSolveOutcome(solution=np.asarray(solution), backend=backend, device="CPU")


def _solve_cuda(
    matrix: Any,
    rhs: Any,
    relative_tolerance: float,
    max_iterations: int,
) -> SparseSolveOutcome:
    import cupy as cp
    from cupyx.scipy.sparse import csr_matrix
    from cupyx.scipy.sparse.linalg import cg, spsolve

    if cp.cuda.runtime.getDeviceCount() < 1:
        raise RuntimeError("CuPy 未检测到可见 CUDA 设备")
    device_id = int(cp.cuda.Device().id)
    gpu_matrix = csr_matrix(
        (
            cp.asarray(matrix.data),
            cp.asarray(matrix.indices),
            cp.asarray(matrix.indptr),
        ),
        shape=matrix.shape,
    )
    gpu_rhs = cp.asarray(rhs)
    solution, info = cg(
        gpu_matrix,
        gpu_rhs,
        rtol=relative_tolerance,
        atol=0.0,
        maxiter=max_iterations,
    )
    backend = "cuda-cupy-cg"
    if int(info) != 0 or not bool(cp.isfinite(solution).all().item()):
        solution = spsolve(gpu_matrix, gpu_rhs)
        backend = "cuda-cupy-spsolve"
    if not bool(cp.isfinite(solution).all().item()):
        raise RuntimeError("CUDA 稳态温度线性系统未收敛")
    return SparseSolveOutcome(
        solution=cp.asnumpy(solution),
        backend=backend,
        device=_cuda_device_name(cp, device_id),
    )


@lru_cache(maxsize=1)
def _probe_cuda() -> tuple[bool, str | None, int | None, str | None]:
    try:
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() < 1:
            return False, None, None, "CuPy 未检测到可见 CUDA 设备"
        device_id = int(cp.cuda.Device().id)
        runtime_version = int(cp.cuda.runtime.runtimeGetVersion())
        return True, _cuda_device_name(cp, device_id), runtime_version, None
    except Exception as exc:  # noqa: BLE001 - health diagnostics must survive optional CUDA errors
        return False, None, None, f"{type(exc).__name__}: {exc}"


def _cuda_device_name(cp: Any, device_id: int) -> str:
    properties = cp.cuda.runtime.getDeviceProperties(device_id)
    raw_name = properties.get("name", f"CUDA device {device_id}")
    name = raw_name.decode("utf-8", errors="replace") if isinstance(raw_name, bytes) else str(raw_name)
    return f"{name} (visible device {device_id})"


def _validate_preference(preference: str) -> None:
    if preference not in {"auto", "cpu", "cuda"}:
        raise ValueError("计算后端必须为 auto、cpu 或 cuda")
