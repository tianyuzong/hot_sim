"""Registered deterministic solver implementations."""

from .analytic_box import AnalyticBoxSolver
from .voxel_stl import VoxelStlSolver

__all__ = ["AnalyticBoxSolver", "VoxelStlSolver"]
