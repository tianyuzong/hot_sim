"""Geometry inspection through CadFlow's public Python frontend only."""

from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path
from typing import Any

from .models import CadFormat, DimensionsMM, GeometryInspection


class CadFlowGeometryInspector:
    def __init__(self, cadflow_repo: Path) -> None:
        self.cadflow_repo = cadflow_repo.resolve()

    def inspect_box(
        self,
        dimensions: DimensionsMM,
        preview_path: Path | None = None,
    ) -> GeometryInspection:
        cad, error = self._load_cadflow()
        analytic = _box_summary(dimensions)
        if cad is None:
            return GeometryInspection(
                available=False,
                engine="analytic-box-metadata",
                summary=analytic,
                faces=_box_faces(dimensions),
                diagnostics=[error or "CadFlow 不可用"],
            )
        try:
            with cad.Model() as model:
                shape = model.box(*dimensions.as_tuple())
                report = shape.validate()
                summary = dict(shape.describe())
                summary["validation"] = report.to_dict()
                if preview_path is not None:
                    preview_path.parent.mkdir(parents=True, exist_ok=True)
                    shape.export_preview_glb(str(preview_path))
                faces = _native_face_summaries(model, shape)
            return GeometryInspection(
                available=True,
                engine="cadflow",
                engine_version=getattr(cad, "__version__", None),
                summary=summary,
                faces=faces or _box_faces(dimensions),
            )
        except Exception as exc:  # noqa: BLE001 - isolate failures from the native CAD boundary
            return GeometryInspection(
                available=False,
                engine="analytic-box-metadata",
                summary=analytic,
                faces=_box_faces(dimensions),
                diagnostics=[f"CadFlow 几何检查失败：{type(exc).__name__}: {exc}"],
            )

    def inspect_file(
        self,
        path: Path,
        cad_format: CadFormat,
        preview_path: Path | None = None,
    ) -> GeometryInspection:
        cad, error = self._load_cadflow()
        if cad is None:
            return GeometryInspection(
                available=False,
                engine="cadflow",
                diagnostics=[error or "CadFlow 不可用"],
            )
        operation = {
            CadFormat.STEP: "import_step",
            CadFormat.BREP: "import_brep",
            CadFormat.STL: "import_stl",
        }[cad_format]
        try:
            with cad.Model() as model:
                shape = getattr(model, operation)(str(path))
                report = shape.validate()
                summary = dict(shape.describe())
                summary["validation"] = report.to_dict()
                faces = _native_face_summaries(model, shape)
                if preview_path is not None:
                    preview_path.parent.mkdir(parents=True, exist_ok=True)
                    shape.export_preview_glb(str(preview_path))
            return GeometryInspection(
                available=report.ok,
                engine="cadflow",
                engine_version=getattr(cad, "__version__", None),
                summary=summary,
                faces=faces,
                diagnostics=[] if report.ok else ["CadFlow 几何有效性校验失败"],
            )
        except Exception as exc:  # noqa: BLE001 - isolate failures from the native CAD boundary
            return GeometryInspection(
                available=False,
                engine="cadflow",
                diagnostics=[f"CadFlow 几何检查失败：{type(exc).__name__}: {exc}"],
            )

    def _load_cadflow(self) -> tuple[Any | None, str | None]:
        python_dir = self.cadflow_repo / "python"
        inserted = False
        if python_dir.is_dir() and str(python_dir) not in sys.path:
            sys.path.insert(0, str(python_dir))
            inserted = True
        try:
            return importlib.import_module("cadflow"), None
        except Exception as exc:  # noqa: BLE001 - optional native package can fail during import
            return None, f"CadFlow 导入失败：{type(exc).__name__}: {exc}"
        finally:
            if inserted:
                try:
                    sys.path.remove(str(python_dir))
                except ValueError:
                    pass


def box_content_hash(name: str, dimensions: DimensionsMM) -> str:
    payload = f"box\0{name}\0{dimensions.x:.17g}\0{dimensions.y:.17g}\0{dimensions.z:.17g}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _box_summary(dimensions: DimensionsMM) -> dict[str, Any]:
    x, y, z = dimensions.as_tuple()
    return {
        "kind": "solid",
        "coordinate_system": {"length_unit": "mm", "up_axis": "+Z"},
        "bbox": [0.0, 0.0, 0.0, x, y, z],
        "volume": x * y * z,
        "area": 2.0 * (x * y + y * z + x * z),
        "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
    }


def _box_faces(dimensions: DimensionsMM) -> list[dict[str, Any]]:
    x, y, z = dimensions.as_tuple()
    return [
        {"selector": "face.xmin", "area_mm2": y * z, "normal": [-1.0, 0.0, 0.0]},
        {"selector": "face.xmax", "area_mm2": y * z, "normal": [1.0, 0.0, 0.0]},
        {"selector": "face.ymin", "area_mm2": x * z, "normal": [0.0, -1.0, 0.0]},
        {"selector": "face.ymax", "area_mm2": x * z, "normal": [0.0, 1.0, 0.0]},
        {"selector": "face.zmin", "area_mm2": x * y, "normal": [0.0, 0.0, -1.0]},
        {"selector": "face.zmax", "area_mm2": x * y, "normal": [0.0, 0.0, 1.0]},
    ]


def _native_face_summaries(model: Any, shape: Any) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for index, face in enumerate(model.faces(shape)):
        item: dict[str, Any] = {
            "selector": f"entity/face/{index}",
            "area_mm2": face.area,
            "center_mm": list(face.center_of_mass),
            "bbox_mm": list(face.bbox),
        }
        try:
            properties = face.face_properties()
            item["normal"] = list(properties.get("normal", ()))
        except Exception as exc:  # noqa: BLE001 - optional metadata must not reject valid geometry
            item["normal_diagnostic"] = f"{type(exc).__name__}: {exc}"
        summaries.append(item)
    return summaries
