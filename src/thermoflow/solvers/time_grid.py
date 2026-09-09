"""Material-aware output schedules for transient thermal integration."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise

MAX_SAVED_INTERVALS = 200


@dataclass(frozen=True)
class TransientTimeGrid:
    output_times_s: tuple[float, ...]
    early_step_s: float | None
    exponent: float
    adaptive: bool
    fallback_reason: str | None
    estimated_integration_steps: int


def integration_targets(
    start_s: float,
    end_s: float,
    maximum_step_s: float,
) -> tuple[float, ...]:
    """Return integration targets whose individual steps do not exceed the limit."""

    if (
        not all(math.isfinite(value) for value in (start_s, end_s, maximum_step_s))
        or start_s < 0
        or end_s <= start_s
        or maximum_step_s <= 0
    ):
        raise ValueError("积分时间区间或最大步长无效")
    span = end_s - start_s
    count = max(1, math.ceil(span / maximum_step_s - 1e-12))
    targets = [start_s + index * maximum_step_s for index in range(1, count)]
    targets.append(end_s)
    return tuple(targets)


def build_transient_time_grid(
    *,
    duration_s: float,
    maximum_step_s: float,
    pitch_m: float,
    diffusivities_m2_s: Iterable[float],
    output_intervals: int = MAX_SAVED_INTERVALS,
) -> TransientTimeGrid:
    """Build an output grid that resolves the earliest material diffusion scale."""

    if (
        not math.isfinite(duration_s)
        or not math.isfinite(maximum_step_s)
        or duration_s <= 0
        or maximum_step_s <= 0
        or maximum_step_s > duration_s
        or not 1 <= output_intervals <= MAX_SAVED_INTERVALS
    ):
        raise ValueError("瞬态时长、最大积分步长或输出区间数无效")

    diffusivities = [
        float(value)
        for value in diffusivities_m2_s
        if math.isfinite(float(value)) and float(value) > 0
    ]
    if not math.isfinite(pitch_m) or pitch_m <= 0 or not diffusivities:
        targets = integration_targets(0.0, duration_s, maximum_step_s)
        if len(targets) > output_intervals:
            raise ValueError("用户最大积分步长产生的时间区间数超过保存上限")
        return TransientTimeGrid(
            output_times_s=(0.0, *targets),
            early_step_s=None,
            exponent=1.0,
            adaptive=False,
            fallback_reason="材料热扩散率无效，已退回用户最大步长的均匀时间表。",
            estimated_integration_steps=len(targets),
        )

    alpha_max = max(diffusivities)
    early_step_s = min(maximum_step_s, 0.25 * pitch_m**2 / alpha_max)
    uniform_step_s = duration_s / output_intervals
    if early_step_s >= uniform_step_s:
        exponent = 1.0
        adaptive = False
    else:
        exponent = math.log(early_step_s / duration_s) / math.log(1 / output_intervals)
        exponent = min(4.0, max(1.0, exponent))
        adaptive = exponent > 1.0

    times = tuple(
        0.0
        if index == 0
        else duration_s
        if index == output_intervals
        else duration_s * (index / output_intervals) ** exponent
        for index in range(output_intervals + 1)
    )
    if any(not math.isfinite(value) for value in times) or any(
        later <= earlier for earlier, later in pairwise(times)
    ):
        raise ValueError("自适应输出时间表不是有限且严格递增的序列")

    integration_steps = sum(
        len(integration_targets(start, end, maximum_step_s))
        for start, end in pairwise(times)
    )
    return TransientTimeGrid(
        output_times_s=times,
        early_step_s=early_step_s,
        exponent=exponent,
        adaptive=adaptive,
        fallback_reason=None,
        estimated_integration_steps=integration_steps,
    )


__all__ = [
    "MAX_SAVED_INTERVALS",
    "TransientTimeGrid",
    "build_transient_time_grid",
    "integration_targets",
]
