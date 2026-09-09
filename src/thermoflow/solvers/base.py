"""Solver adapter contract."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from thermoflow.models import PolicyReport, SimulationPlan, SimulationResult, WorkpieceRecord
from thermoflow.execution import ProgressCallback


class ThermalSolver(Protocol):
    solver_id: str

    def solve(
        self,
        *,
        study_id: str,
        workpiece: WorkpieceRecord,
        plan: SimulationPlan,
        policy: PolicyReport,
        artifact_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> SimulationResult: ...
