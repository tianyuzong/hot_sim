from __future__ import annotations

import math
from itertools import pairwise

import pytest

from thermoflow.solvers.time_grid import (
    build_transient_time_grid,
    integration_targets,
)


def test_adaptive_grid_resolves_early_copper_diffusion():
    copper_diffusivity = 391.0 / (8940.0 * 385.0)

    grid = build_transient_time_grid(
        duration_s=60.0,
        maximum_step_s=1.0,
        pitch_m=0.002,
        diffusivities_m2_s=[copper_diffusivity],
    )

    expected_early_step = 0.25 * 0.002**2 / copper_diffusivity
    assert grid.output_times_s[0] == 0.0
    assert grid.output_times_s[-1] == 60.0
    assert len(grid.output_times_s) == 201
    assert grid.early_step_s == pytest.approx(expected_early_step)
    assert grid.output_times_s[1] <= expected_early_step * (1 + 1e-12)
    assert all(later > earlier for earlier, later in pairwise(grid.output_times_s))
    assert grid.adaptive is True
    assert 1.0 < grid.exponent <= 4.0
    assert grid.fallback_reason is None


def test_slow_diffusion_uses_uniform_output_grid():
    grid = build_transient_time_grid(
        duration_s=1.0,
        maximum_step_s=0.1,
        pitch_m=0.01,
        diffusivities_m2_s=[1e-8],
    )

    assert grid.adaptive is False
    assert grid.exponent == 1.0
    assert len(grid.output_times_s) == 201
    assert grid.output_times_s[1] == pytest.approx(0.005)
    assert grid.output_times_s[-1] == 1.0
    assert grid.fallback_reason is None


def test_invalid_diffusivity_falls_back_to_legacy_uniform_steps():
    grid = build_transient_time_grid(
        duration_s=1.0,
        maximum_step_s=0.3,
        pitch_m=0.002,
        diffusivities_m2_s=[0.0, -1.0, math.nan, math.inf],
    )

    assert grid.output_times_s == pytest.approx((0.0, 0.3, 0.6, 0.9, 1.0))
    assert grid.early_step_s is None
    assert grid.exponent == 1.0
    assert grid.adaptive is False
    assert grid.fallback_reason == "材料热扩散率无效，已退回用户最大步长的均匀时间表。"
    assert grid.estimated_integration_steps == 4


def test_extreme_diffusivity_caps_exponent_and_preserves_valid_times():
    grid = build_transient_time_grid(
        duration_s=60.0,
        maximum_step_s=1.0,
        pitch_m=1e-6,
        diffusivities_m2_s=[1.0],
    )

    assert grid.exponent == 4.0
    assert len(grid.output_times_s) == 201
    assert all(math.isfinite(value) for value in grid.output_times_s)
    assert all(later > earlier for earlier, later in pairwise(grid.output_times_s))


def test_integration_targets_never_exceed_maximum_step():
    targets = integration_targets(0.0, 1.0, 0.3)

    assert targets == pytest.approx((0.3, 0.6, 0.9, 1.0))
    assert max(later - earlier for earlier, later in pairwise((0.0, *targets))) <= 0.3
    assert targets[-1] == 1.0


@pytest.mark.parametrize(
    ("start_s", "end_s", "maximum_step_s"),
    [
        (1.0, 1.0, 0.1),
        (1.0, 0.5, 0.1),
        (0.0, 1.0, 0.0),
        (0.0, math.inf, 0.1),
    ],
)
def test_integration_targets_reject_invalid_intervals(start_s, end_s, maximum_step_s):
    with pytest.raises(ValueError, match="积分时间区间"):
        integration_targets(start_s, end_s, maximum_step_s)
