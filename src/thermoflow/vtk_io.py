"""meshio 5.3 adapter for VTK_VOXEL connectivity used by stored thermal results."""

from types import FunctionType

import numpy as np
from meshio.vtk import _vtk_42


def _translate_cells(connectivity, types, cell_data):
    if np.any(types == 11):
        if not np.isin(types, [11, 12]).all() or len(connectivity) != len(types) * 9:
            raise ValueError("体素网格连接关系无效")
        rows = connectivity.reshape(-1, 9).copy()
        if not np.all(rows[:, 0] == 8):
            raise ValueError("体素单元必须包含八个节点")
        voxel = types == 11
        rows[voxel] = rows[voxel][:, [0, 1, 2, 4, 3, 5, 6, 8, 7]]
        types = types.copy()
        types[voxel] = 12
        connectivity = rows.reshape(-1)
    return _vtk_42.translate_cells(connectivity, types, cell_data)


# Keep meshio's structured VTK parser; extend only its unsupported cell translation.
# The private namespace is cloned, never patched globally across API threads.
_read_buffer = FunctionType(_vtk_42.read_buffer.__code__, {
    **_vtk_42.read_buffer.__globals__, "translate_cells": _translate_cells,
})


def read_solver_vtk(path):
    with path.open("rb") as stream:
        version = stream.readline().decode("ascii").strip()
        if version not in {"# vtk DataFile Version 3.0", "# vtk DataFile Version 4.2"}:
            raise ValueError("当前数值网格版本不受支持")
        return _read_buffer(stream)
