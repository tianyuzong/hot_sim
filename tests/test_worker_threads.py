"""The worker thread budget reaches loaded BLAS libraries and is reversible."""

import numpy as np
import pytest
from scipy.sparse import diags
from threadpoolctl import threadpool_info, threadpool_limits

from thermoflow.solvers.compute import limit_worker_blas_threads, solve_sparse_system


def _blas_threads():
    return {item["filepath"]: item["num_threads"] for item in threadpool_info() if item["user_api"] == "blas"}


def test_worker_limits_both_blas_pools_and_restores_the_callers_budget():
    # Test an actual loaded-library change, even on hosts that default to one thread.
    with threadpool_limits(limits=2, user_api="blas"):
        before = _blas_threads()
        assert before and set(before.values()) == {2}
        with pytest.raises(RuntimeError, match="test exit"), limit_worker_blas_threads():
            assert set(_blas_threads().values()) == {1}
            raise RuntimeError("test exit")
        assert _blas_threads() == before


def test_worker_thread_budget_preserves_sparse_solution_and_residual():
    n = 301
    matrix = diags([-np.ones(n - 1), 4 * np.ones(n), -np.ones(n - 1)], [-1, 0, 1]).tocsr()
    expected = np.linspace(290, 350, n)
    rhs = matrix @ expected
    with threadpool_limits(limits=2, user_api="blas"):
        reference = solve_sparse_system(matrix, rhs, relative_tolerance=1e-10, max_iterations=1000, preference="cpu")
        with limit_worker_blas_threads():
            limited = solve_sparse_system(matrix, rhs, relative_tolerance=1e-10, max_iterations=1000, preference="cpu")
    assert np.allclose(reference.solution, limited.solution, rtol=0, atol=1e-9)
    assert np.linalg.norm(matrix @ limited.solution - rhs) / np.linalg.norm(rhs) < 1e-10
