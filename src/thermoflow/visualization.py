"""Engineering display surfaces derived from verified geometry and solver VTK files."""

from __future__ import annotations

import hashlib
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .models import Quantity, StrictModel
from .stl_geometry import (
    _component_face_groups,
    _component_record,
    _is_component_usable,
    load_stl_mesh,
)
from .storage import FileRepository, RecordNotFoundError
from .vtk_io import read_solver_vtk

HEX_FACES = np.array(
    [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
)


class ViewRequest(StrictModel):
    kind: Literal["mesh", "result"] = "result"
    frame_index: int | None = Field(default=None, ge=0)
    section_axis: Literal["x", "y", "z"] | None = None
    section_position: Quantity | None = None
    difference_study_id: str | None = Field(default=None, pattern=r"^study-[a-z0-9-]+$")

    @model_validator(mode="after")
    def validate_selection(self):
        if (self.section_axis is None) != (self.section_position is None):
            raise ValueError("截面需要同时指定方向与位置")
        if self.section_position and self.section_position.unit not in {"mm", "um", "m"}:
            raise ValueError("截面位置必须使用长度单位")
        if self.kind == "mesh" and (self.frame_index is not None or self.difference_study_id):
            raise ValueError("网格预览不能选择结果时刻或研究差值")
        return self


class EngineeringSurface(StrictModel):
    coordinate_unit: Literal["mm", "source"] = "mm"
    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]
    component_ids: list[str | None]
    cell_ids: list[int] = Field(default_factory=list)
    temperature_k: list[float] | None = None
    heat_flux_w_m2: list[tuple[float, float, float]] | None = None
    source_sha256: str
    time_s: float | None = None
    total_cells: int = 0
    sampled: Literal[False] = False
    minimum_position_mm: tuple[float, float, float] | None = None
    maximum_position_mm: tuple[float, float, float] | None = None
    temperature_min_k: float | None = None
    temperature_max_k: float | None = None
    heat_flux_min_w_m2: float | None = None
    heat_flux_max_w_m2: float | None = None


class EngineeringPlaybackFrame(StrictModel):
    index: int = Field(ge=0)
    time_s: float = Field(ge=0)
    temperature_k: list[float]
    source_sha256: str
    source_artifact: str | None = None
    minimum_position_mm: tuple[float, float, float] | None = None
    maximum_position_mm: tuple[float, float, float] | None = None
    temperature_min_k: float
    temperature_max_k: float


class EngineeringPlayback(StrictModel):
    study_id: str
    coordinate_unit: Literal["mm"] = "mm"
    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]
    component_ids: list[str | None]
    cell_ids: list[int]
    total_cells: int
    frames: list[EngineeringPlaybackFrame] = Field(min_length=1, max_length=201)


class CellProbe(StrictModel):
    study_id: str
    cell_id: int
    source_sha256: str
    frame_index: int | None
    time_s: float | None
    component_id: str | None
    position_mm: tuple[float, float, float]
    temperature: Quantity
    heat_flux_w_m2: tuple[float, float, float] | None
    location: Literal["cell_center"] = "cell_center"


def geometry_surface(repository: FileRepository, workpiece_id: str) -> EngineeringSurface:
    workpiece = repository.get_workpiece(workpiece_id)
    if workpiece.kind.value == "box":
        import trimesh

        dimensions = np.asarray(workpiece.dimensions_mm.as_tuple())
        mesh = trimesh.creation.box(extents=dimensions)
        mesh.apply_translation(dimensions / 2)
        return EngineeringSurface(
            vertices=mesh.vertices.tolist(),
            triangles=mesh.faces.tolist(),
            component_ids=[None] * len(mesh.faces),
            source_sha256=workpiece.content_sha256,
        )
    if workpiece.cad_format.value != "stl":
        raise ValueError("当前三维几何读取仅支持 STL")
    # IDs derive from the original STL, before unit normalization.
    path = repository.workpiece_dir(workpiece_id) / "source.stl"
    if hashlib.sha256(path.read_bytes()).hexdigest() != workpiece.content_sha256:
        raise ValueError("几何文件校验失败，请重新导入几何")
    mesh = load_stl_mesh(path)
    components = [None] * len(mesh.faces)
    for group, face_indices in _component_face_groups(mesh):
        if _is_component_usable(group):
            component_id = _component_record(group, 0).component_id
            for index in face_indices:
                components[int(index)] = component_id
    scale = workpiece.length_unit.scale_to_mm if workpiece.unit_confirmed else 1
    return EngineeringSurface(
        vertices=(mesh.vertices * scale).tolist(),
        triangles=mesh.faces.tolist(),
        component_ids=components,
        source_sha256=workpiece.content_sha256,
        coordinate_unit="mm" if workpiece.unit_confirmed else "source",
    )


def _read_volume(repository, study_id, request):
    study = repository.get_study(study_id)
    time = None
    if request.kind == "mesh":
        if study.mesh_status.value not in {"ready", "needs_review", "blocked"}:
            raise RecordNotFoundError("该研究没有已完成的网格")
        record = repository.get_mesh(study_id)
        name = "mesh.vtk"
    else:
        if study.status.value != "succeeded":
            raise RecordNotFoundError("该研究没有已完成的结果")
        record = repository.get_result(study_id)
        name, time = "temperature.vtk", record.time_s
        if request.frame_index is not None:
            step = next((s for s in record.time_steps if s.index == request.frame_index), None)
            if step is None:
                raise RecordNotFoundError("该研究不存在指定时间步")
            name, time = step.vtk_artifact, step.time_s
    artifact = next((item for item in record.artifacts if item.name == name), None)
    if artifact is None:
        raise RecordNotFoundError("该研究缺少对应数值网格文件")
    path = repository.artifact_path(study_id, name)
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
        raise ValueError("数值网格文件校验失败，请重新生成研究")
    try:
        mesh = read_solver_vtk(path)
    except Exception as exc:
        raise ValueError("数值网格文件无法读取，请重新生成研究") from exc
    if (
        not mesh.cells
        or len({block.type for block in mesh.cells}) != 1
        or mesh.cells[0].type not in {"hexahedron", "tetra"}
    ):
        raise ValueError("三维结果需要统一的六面体或四面体网格")
    cells = np.concatenate([block.data for block in mesh.cells])
    points = np.asarray(mesh.points, dtype=float)
    cell_fields = {key: np.concatenate(value) for key, value in mesh.cell_data.items()}
    nodal = mesh.point_data.get("temperature_k")
    temperature = cell_fields.get("temperature_k")
    if temperature is not None:
        temperature = temperature.reshape(-1)
    elif nodal is not None:
        nodal = nodal.reshape(-1)
        temperature = nodal[cells].mean(axis=1)
    if request.kind == "result" and temperature is None:
        raise ValueError("结果网格缺少真实温度场")
    flux = cell_fields.get("heat_flux_w_m2")
    if not len(cells) or any(
        not np.isfinite(a).all() for a in (points, temperature, flux) if a is not None
    ):
        raise ValueError("结果网格为空或包含无效数值")
    return study, points, cells, temperature, nodal, flux, artifact.sha256, time


def _cell_to_nodal(cells, values, point_count):
    """Reconstruct a continuous display field by averaging adjacent real cells."""
    sums = np.zeros(point_count, dtype=float)
    counts = np.zeros(point_count, dtype=np.int64)
    np.add.at(sums, cells.reshape(-1), np.repeat(values, cells.shape[1]))
    np.add.at(counts, cells.reshape(-1), 1)
    return np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)


def _cell_components(repository, study, cells, points):
    workpiece = repository.get_workpiece(study.workpiece_id)
    if cells.shape[1] == 4:
        from .tetrahedral import read_tetra_mesh

        data, _ = read_tetra_mesh(repository.study_dir(study.study_id) / "artifacts")
        if not np.array_equal(points, data["points"]) or not np.array_equal(cells, data["cells"]):
            raise ValueError("结果四面体拓扑与已检查的组件网格不一致")
        return data["component_keys"][data["component_indices"]]
    ids = [component.component_id for component in workpiece.components]
    if len(ids) <= 1:
        return np.full(len(cells), ids[0] if ids else None, dtype=object)
    from scipy.ndimage import label

    from .regions import ComponentMappingError, RegionFaceMap, component_cell_masks

    # Reconstruct the regular voxel grid from the verified VTK. Component order
    # can change on quantization, so share the solver's original-surface ownership.
    centers = points[cells].mean(axis=1)
    pitch = np.ptp(points[cells[0]], axis=0)
    origin = centers.min(axis=0)
    coordinates = np.rint((centers - origin) / pitch).astype(int)
    active = np.zeros(tuple(coordinates.max(axis=0) + 1), dtype=bool)
    indices = tuple(coordinates.T)
    active[indices] = True
    if label(active)[1] != len(ids):
        # A blocked mesh remains inspectable, but must not invent component IDs.
        return np.full(len(cells), None, dtype=object)
    transform = np.eye(4)
    transform[:3, :3] = np.diag(pitch)
    transform[:3, 3] = origin
    face_map = RegionFaceMap(
        active,
        transform,
        workpiece,
        repository.workpiece_dir(workpiece.workpiece_id) / "source.stl",
    )
    mapping = np.full(len(cells), None, dtype=object)
    try:
        component_cells = component_cell_masks(active, workpiece, face_map)
    except ComponentMappingError:
        if study.mesh_status.value != "blocked":
            raise
        # Keep a rejected mesh diagnosable without guessing ownership. Hash or
        # parsing failures are not ComponentMappingError and must still propagate.
        return mapping
    for component_id, mask in component_cells.items():
        mapping[mask[indices]] = component_id
    return mapping


def study_surface(
    repository: FileRepository, study_id: str, request: ViewRequest
) -> EngineeringSurface:
    # Rapid view changes overlap on large models; readonly requests should wait
    # for each other, not report a fictitious active computation to the user.
    with repository.study_lock(study_id, blocking=True):
        study, points, cells, temperature, nodal, flux, fingerprint, time = _read_volume(
            repository, study_id, request
        )
        if request.difference_study_id:
            other_request = request.model_copy(update={"difference_study_id": None})
            other, other_points, other_cells, other_t, other_nodal, _, other_hash, other_time = (
                _read_volume(
                    repository,
                    request.difference_study_id,
                    other_request,
                )
            )
            if (
                other.project_id != study.project_id
                or other.workpiece_id != study.workpiece_id
                or time != other_time
                or not np.array_equal(points, other_points)
                or not np.array_equal(cells, other_cells)
                or (nodal is None) != (other_nodal is None)
            ):
                raise ValueError("只有同一项目、几何、完整网格和时刻的结果可以显示空间差值")
            temperature = other_t - temperature
            nodal = other_nodal - nodal if nodal is not None else None
            flux = None
            fingerprint = hashlib.sha256((fingerprint + other_hash).encode()).hexdigest()
        components = _cell_components(repository, study, cells, points)
        result_nodal = nodal
        if nodal is None and temperature is not None:
            nodal = _cell_to_nodal(cells, temperature, len(points))
        if request.section_axis:
            if cells.shape[1] == 4:
                vertices, owners, nodal_values = _tetra_section(points, cells, nodal, request)
            else:
                vertices, owners, nodal_values = _section(points, cells, nodal, request)
                vertices = vertices[:, [[0, 1, 2], [0, 2, 3]]].reshape(-1, 3, 3)
                owners = np.repeat(owners, 2)
                if nodal_values is not None:
                    nodal_values = nodal_values[:, [[0, 1, 2], [0, 2, 3]]].reshape(-1, 3)
        else:
            face_nodes, owners = _exterior_triangles(cells)
            vertices = points[face_nodes]
            nodal_values = nodal[face_nodes] if nodal is not None else None
        triangles = np.arange(len(owners) * 3).reshape(-1, 3)
        vertex_temperature = (
            nodal_values
            if nodal_values is not None
            else (np.repeat(temperature[owners], 3) if temperature is not None else None)
        )
        positions = points if result_nodal is not None else points[cells].mean(axis=1)
        values = result_nodal if result_nodal is not None else temperature
        return EngineeringSurface(
            vertices=vertices.reshape(-1, 3).tolist(),
            triangles=triangles.tolist(),
            component_ids=components[owners].tolist(),
            cell_ids=owners.tolist(),
            temperature_k=vertex_temperature.reshape(-1).tolist()
            if vertex_temperature is not None
            else None,
            heat_flux_w_m2=np.repeat(flux[owners], 3, axis=0).tolist()
            if flux is not None
            else None,
            source_sha256=fingerprint,
            time_s=time,
            total_cells=len(cells),
            minimum_position_mm=tuple(positions[int(values.argmin())])
            if values is not None
            else None,
            maximum_position_mm=tuple(positions[int(values.argmax())])
            if values is not None
            else None,
            temperature_min_k=float(values.min()) if values is not None else None,
            temperature_max_k=float(values.max()) if values is not None else None,
            heat_flux_min_w_m2=float(np.linalg.norm(flux, axis=1).min())
            if flux is not None
            else None,
            heat_flux_max_w_m2=float(np.linalg.norm(flux, axis=1).max())
            if flux is not None
            else None,
        )


def study_playback(repository: FileRepository, study_id: str) -> EngineeringPlayback:
    result = repository.get_result(study_id)
    if not result.time_steps:
        raise RecordNotFoundError("该研究没有瞬态播放帧")
    # Extract exterior topology once. Keeping 201 complete EngineeringSurfaces
    # also kept duplicate geometry and unused vector fields for every frame.
    with repository.study_lock(study_id, blocking=True):
        frames = []
        artifacts = {item.name: item for item in result.artifacts}
        reference_points = reference_cells = None
        for step in result.time_steps:
            compact_name = f"thermal-{step.index:04d}.npz"
            compact_artifact = artifacts.get(compact_name)
            source_artifact = step.vtk_artifact
            if reference_points is None or compact_artifact is None:
                study, points, cells, temperature, nodal, _, fingerprint, time = _read_volume(
                    repository,
                    study_id,
                    ViewRequest(frame_index=step.index),
                )
            if reference_points is None:
                reference_points, reference_cells = points, cells
                faces, owners = _exterior_triangles(cells)
                surface_nodes, surface_indices = np.unique(faces, return_inverse=True)
                triangles = surface_indices.reshape(-1, 3)
                components = _cell_components(repository, study, cells, points)
                centers = points[cells].mean(axis=1)
            elif not np.array_equal(points, reference_points) or not np.array_equal(
                cells, reference_cells
            ):
                raise ValueError("瞬态播放帧的计算网格不一致，请重新计算研究")
            if compact_artifact is not None:
                path = repository.artifact_path(study_id, compact_name)
                fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
                if fingerprint != compact_artifact.sha256:
                    raise ValueError("播放帧数值文件校验失败，请重新生成研究")
                # These are the full solver arrays, not preview samples. Preserve
                # their original precision and verify cell ownership and time.
                with np.load(path, allow_pickle=False) as data:
                    temperature = data["temperature_k"]
                    nodal = (
                        data["nodal_temperature_k"].copy()
                        if "nodal_temperature_k" in data.files
                        else None
                    )
                    frame_centers = data["centers_mm"]
                    time = float(data["time_s"])
                if (
                    temperature.shape != (len(reference_cells),)
                    or (
                        nodal is not None
                        and (
                            nodal.shape != (len(reference_points),) or not np.isfinite(nodal).all()
                        )
                    )
                    or (reference_cells.shape[1] == 4 and nodal is None)
                    or frame_centers.shape != centers.shape
                    or not np.isfinite(temperature).all()
                    or not np.allclose(frame_centers, centers, rtol=0, atol=1e-8)
                    or not np.isclose(time, step.time_s, rtol=1e-12, atol=1e-12)
                ):
                    raise ValueError("播放帧的真实温度、网格或时刻不一致")
                points, cells = reference_points, reference_cells
                source_artifact = compact_name
            field = nodal if nodal is not None else _cell_to_nodal(cells, temperature, len(points))
            values, positions = (nodal, points) if nodal is not None else (temperature, centers)
            frames.append(
                EngineeringPlaybackFrame(
                    index=step.index,
                    time_s=time if time is not None else step.time_s,
                    temperature_k=field[surface_nodes].reshape(-1).tolist(),
                    source_sha256=fingerprint,
                    source_artifact=source_artifact,
                    minimum_position_mm=tuple(positions[int(values.argmin())]),
                    maximum_position_mm=tuple(positions[int(values.argmax())]),
                    temperature_min_k=float(values.min()),
                    temperature_max_k=float(values.max()),
                )
            )
        return EngineeringPlayback(
            study_id=study_id,
            vertices=reference_points[surface_nodes].tolist(),
            triangles=triangles.tolist(),
            component_ids=components[owners].tolist(),
            cell_ids=owners.tolist(),
            total_cells=len(reference_cells),
            frames=frames,
        )


def _exterior_triangles(cells):
    if cells.shape[1] == 4:
        from .tetrahedral import exterior_faces

        return exterior_faces(cells)
    faces = cells[:, HEX_FACES].reshape(-1, 4)
    _, inverse, counts = np.unique(
        np.sort(faces, axis=1), axis=0, return_inverse=True, return_counts=True
    )
    exterior = counts[inverse] == 1
    owners = np.repeat(np.arange(len(cells)), 6)[exterior]
    return faces[exterior][:, [[0, 1, 2], [0, 2, 3]]].reshape(-1, 3), np.repeat(owners, 2)


def _tetra_section(points, cells, nodal, request):
    axis = "xyz".index(request.section_axis)
    tangent = [i for i in range(3) if i != axis]
    position = (
        request.section_position.value
        * {"mm": 1, "um": 0.001, "m": 1000}[request.section_position.unit]
    )
    corners = points[cells]
    low, high = corners[:, :, axis].min(axis=1), corners[:, :, axis].max(axis=1)
    candidates = np.flatnonzero((low <= position) & (high >= position))
    vertices = []
    owners = []
    values = []
    seen = set()
    for owner in candidates:
        polygon = []
        temperatures = []
        for a, b in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)):
            pa, pb = corners[owner, a], corners[owner, b]
            da, db = pa[axis] - position, pb[axis] - position
            fractions = (
                (0.0, 1.0)
                if da == db == 0
                else ((da / (da - db),) if da * db <= 0 and da != db else ())
            )
            for fraction in fractions:
                point = pa + fraction * (pb - pa)
                if any(np.allclose(point, p, rtol=0, atol=1e-9) for p in polygon):
                    continue
                polygon.append(point)
                if nodal is not None:
                    ta, tb = nodal[cells[owner, [a, b]]]
                    temperatures.append(ta + fraction * (tb - ta))
        if len(polygon) < 3:
            continue
        polygon = np.asarray(polygon)
        offsets = polygon[:, tangent] - polygon[:, tangent].mean(axis=0)
        order = np.argsort(np.arctan2(offsets[:, 1], offsets[:, 0]))
        polygon = polygon[order]
        for j in range(1, len(polygon) - 1):
            tri = polygon[[0, j, j + 1]]
            # A plane coincident with an interior facet must appear only once.
            key = tuple(sorted(tuple(np.round(p, 9)) for p in tri))
            if key in seen or np.linalg.norm(np.cross(tri[1] - tri[0], tri[2] - tri[0])) < 1e-16:
                continue
            seen.add(key)
            vertices.append(tri)
            owners.append(owner)
            if nodal is not None:
                values.append(np.asarray(temperatures)[order][[0, j, j + 1]])
    return (
        np.asarray(vertices).reshape(-1, 3, 3),
        np.asarray(owners, dtype=int),
        np.asarray(values).reshape(-1, 3) if nodal is not None else None,
    )


def _section(points, cells, nodal, request):
    axis = "xyz".index(request.section_axis)
    position = (
        request.section_position.value
        * {"mm": 1, "um": 0.001, "m": 1000}[request.section_position.unit]
    )
    corners = points[cells]
    low, high = corners.min(axis=1), corners.max(axis=1)
    owners = np.flatnonzero(
        (low[:, axis] <= position)
        & (
            (position < high[:, axis])
            | ((position == high[:, axis]) & (position == high[:, axis].max()))
        )
    )
    tangent = [i for i in range(3) if i != axis]
    vertices = np.repeat(low[owners, None, :], 4, axis=1)
    vertices[:, :, axis] = position
    vertices[:, [1, 2], tangent[0]] = high[owners, tangent[0]][:, None]
    vertices[:, [2, 3], tangent[1]] = high[owners, tangent[1]][:, None]
    values = None
    if nodal is not None:
        # Trilinear interpolation of actual nodal values on the axis-aligned solver hexes.
        local = (vertices[:, :, None, :] - low[owners, None, None, :]) / (high - low)[
            owners, None, None, :
        ]
        upper = np.isclose(corners[owners], high[owners, None, :])
        weights = np.where(upper[:, None], local, 1 - local).prod(axis=-1)
        values = (weights * nodal[cells[owners]][:, None, :]).sum(axis=-1)
    return vertices, owners, values


def probe_cell(
    repository: FileRepository, study_id: str, cell_id: int, frame_index: int | None
) -> CellProbe:
    with repository.study_lock(study_id, blocking=True):
        study, points, cells, temperature, _, flux, fingerprint, time = _read_volume(
            repository,
            study_id,
            ViewRequest(frame_index=frame_index),
        )
        if not 0 <= cell_id < len(cells):
            raise RecordNotFoundError("该研究不存在指定数值单元")
        component = _cell_components(repository, study, cells, points)[cell_id]
        return CellProbe(
            study_id=study_id,
            cell_id=cell_id,
            frame_index=frame_index,
            source_sha256=fingerprint,
            time_s=time,
            component_id=component,
            position_mm=tuple(points[cells[cell_id]].mean(axis=0)),
            temperature=Quantity(value=float(temperature[cell_id]), unit="K"),
            heat_flux_w_m2=tuple(flux[cell_id]) if flux is not None else None,
        )
