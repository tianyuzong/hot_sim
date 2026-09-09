"""Damped Newton solution of conduction plus diffuse-gray surface radiation."""

from __future__ import annotations

import numpy as np
from scipy.sparse import diags

from .compute import solve_sparse_system


def solve_radiating_system(matrix, rhs, radiation, initial, *, relative_tolerance,
                           max_iterations, preference, history=None, time_s=None):
    temperature = np.maximum(np.asarray(initial, dtype=float), 1.0)
    scale = max(float(np.linalg.norm(rhs, ord=np.inf)), 1e-12)
    tolerance = max(relative_tolerance, 1e-11)

    def residual(value):
        return matrix @ value + radiation * value**4 - rhs

    for iteration in range(1, 61):
        before = float(np.linalg.norm(residual(temperature), ord=np.inf))
        jacobian = matrix + diags(4 * radiation * temperature**3)
        linear_rhs = rhs + 3 * radiation * temperature**4
        outcome = solve_sparse_system(jacobian, linear_rhs,
                                      relative_tolerance=min(relative_tolerance, 1e-10),
                                      max_iterations=max_iterations, preference=preference)
        direction = outcome.solution - temperature
        damping = 1.0
        for _ in range(50):
            candidate = temperature + damping * direction
            error = float(np.linalg.norm(residual(candidate), ord=np.inf)) if np.all(candidate >= 1) else float("inf")
            if np.isfinite(error) and (error <= before * (1 - 1e-4 * damping) or error / scale <= tolerance):
                break
            damping *= 0.5
        else:
            raise RuntimeError("辐射非线性迭代无法降低残差，或工况导致非物理绝对温度；请检查热流与换热条件")
        temperature = candidate
        if history is not None:
            history.append({"time_s": time_s, "iteration": iteration,
                            "residual_relative": error / scale, "damping": damping})
        if error / scale <= tolerance:
            return type(outcome)(solution=temperature, backend=outcome.backend,
                                 device=outcome.device, fallback_reason=outcome.fallback_reason)
    raise RuntimeError("辐射非线性求解未在 60 次迭代内收敛，请检查工况或缩小时间步长")
