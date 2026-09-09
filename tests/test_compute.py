from __future__ import annotations

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from thermoflow.solvers import compute as compute_module


def test_cpu_sparse_backend_solves_known_system() -> None:
    matrix = csr_matrix([[4.0, 1.0], [1.0, 3.0]])
    rhs = np.asarray([1.0, 2.0])

    outcome = compute_module.solve_sparse_system(
        matrix,
        rhs,
        relative_tolerance=1e-10,
        max_iterations=100,
        preference="cpu",
    )

    assert outcome.backend == "cpu-scipy-cg"
    assert outcome.device == "CPU"
    assert outcome.solution == pytest.approx([1 / 11, 7 / 11])


def test_auto_backend_records_cuda_fallback(monkeypatch) -> None:
    matrix = csr_matrix([[2.0]])
    rhs = np.asarray([6.0])

    def fail_cuda(*_args, **_kwargs):
        raise RuntimeError("no CUDA device")

    monkeypatch.setattr(compute_module, "_solve_cuda", fail_cuda)
    outcome = compute_module.solve_sparse_system(
        matrix,
        rhs,
        relative_tolerance=1e-10,
        max_iterations=100,
        preference="auto",
    )

    assert outcome.solution == pytest.approx([3.0])
    assert outcome.backend == "cpu-scipy-cg"
    assert outcome.fallback_reason == "RuntimeError: no CUDA device"


def test_required_cuda_backend_never_silently_falls_back(monkeypatch) -> None:
    def fail_cuda(*_args, **_kwargs):
        raise RuntimeError("no CUDA device")

    monkeypatch.setattr(compute_module, "_solve_cuda", fail_cuda)
    with pytest.raises(RuntimeError, match="CUDA 后端不可用"):
        compute_module.solve_sparse_system(
            csr_matrix([[1.0]]),
            np.asarray([1.0]),
            relative_tolerance=1e-8,
            max_iterations=10,
            preference="cuda",
        )
