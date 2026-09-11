"""Implicit integration of the finite-volume thermal capacity equation."""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from itertools import pairwise
from typing import Any

from .compute import solve_sparse_system
from .time_grid import integration_targets


def integrate_thermal_system(
    matrix: Any,
    rhs: Any,
    capacity_j_k: Any,
    initial: Any,
    *,
    duration_s: float,
    time_step_s: float,
    relative_tolerance: float,
    max_iterations: int,
    preference: str,
    output_times_s: Sequence[float] | None = None,
    integration_steps: list[float] | None = None,
    radiation: Any = None,
    nonlinear_history: list | None = None,
    energy_reference_powers: list[float] | None = None,
) -> Iterator[tuple[float, Any, Any, float, float, float]]:
    import numpy as np
    from scipy.sparse import diags

    if not (0 < time_step_s <= duration_s) or math.ceil(
        math.nextafter(duration_s / time_step_s, -math.inf)
    ) > 200:
        raise ValueError("瞬态时长或步长不符合资源限制")
    if np.any(capacity_j_k <= 0) or not np.isfinite(capacity_j_k).all():
        raise ValueError("所有瞬态单元必须具有有效的正热容")
    if output_times_s is None:
        output_times = integration_targets(0.0, duration_s, time_step_s)
    else:
        output_times = tuple(float(value) for value in output_times_s)
        if (
            not output_times
            or len(output_times) > 200
            or any(not math.isfinite(value) for value in output_times)
            or output_times[0] <= 0
            or any(later <= earlier for earlier, later in pairwise(output_times))
            or not math.isclose(output_times[-1], duration_s, rel_tol=1e-12, abs_tol=1e-12)
            or output_times[-1] > duration_s
        ):
            raise ValueError("瞬态输出时刻必须有限、严格递增并精确结束于仿真时长")
        output_times = (*output_times[:-1], duration_s)
    previous = np.asarray(initial, dtype=float).copy()
    previous_time = 0.0
    for output_time in output_times:
        interval_energy_change = 0.0
        interval_energy_error = 0.0
        interval_residual = 0.0
        interval_reference_power = 1e-30
        outcome = None
        for time in integration_targets(previous_time, output_time, time_step_s):
            dt = time - previous_time
            if integration_steps is not None:
                integration_steps.append(dt)
            storage = capacity_j_k / dt
            implicit_matrix = matrix + diags(storage)
            implicit_rhs = rhs + storage * previous
            if radiation is not None and np.any(radiation):
                from .radiation import solve_radiating_system

                outcome = solve_radiating_system(
                    implicit_matrix,
                    implicit_rhs,
                    radiation,
                    previous,
                    relative_tolerance=relative_tolerance,
                    max_iterations=max_iterations,
                    preference=preference,
                    history=nonlinear_history,
                    time_s=time,
                )
            else:
                outcome = solve_sparse_system(
                    implicit_matrix,
                    implicit_rhs,
                    relative_tolerance=relative_tolerance,
                    max_iterations=max_iterations,
                    preference=preference,
                )
            current = outcome.solution
            radiation_loss = 0 if radiation is None else radiation * current**4
            residual = float(
                np.linalg.norm(implicit_matrix @ current + radiation_loss - implicit_rhs)
            ) / max(float(np.linalg.norm(implicit_rhs)), 1e-30)
            if residual > max(10 * relative_tolerance, 1e-10) or np.any(current < 0):
                raise RuntimeError(
                    f"瞬态温度线性系统在 {time:.9g} s、dt={dt:.9g} s 时"
                    "残差或绝对温度校验失败"
                )
            energy_change = float(np.dot(capacity_j_k, current - previous))
            net_cell_power = rhs - matrix @ current - radiation_loss
            energy_residual = float(net_cell_power.sum()) - energy_change / dt
            reference_power = max(
                float(np.abs(implicit_rhs).sum())
                + float(np.abs(implicit_matrix @ current).sum())
                + float(np.sum(radiation_loss)),
                1e-30,
            )
            interval_energy_change += energy_change
            interval_energy_error = max(
                interval_energy_error, abs(energy_residual) / reference_power
            )
            interval_residual = max(interval_residual, residual)
            interval_reference_power = max(interval_reference_power, reference_power)
            previous, previous_time = current.copy(), time
        if energy_reference_powers is not None:
            energy_reference_powers.append(interval_reference_power)
        yield (
            output_time,
            previous.copy(),
            outcome,
            interval_energy_change,
            interval_energy_error,
            interval_residual,
        )
