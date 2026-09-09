from __future__ import annotations

from thermoflow.models import (
    DimensionsMM,
    GeometryInspection,
    WorkpieceKind,
    WorkpieceRecord,
)


def box_workpiece(
    *,
    workpiece_id: str = "wp-test",
    name: str = "test block",
    dimensions: DimensionsMM | None = None,
) -> WorkpieceRecord:
    resolved = dimensions or DimensionsMM(x=100.0, y=20.0, z=10.0)
    return WorkpieceRecord(
        workpiece_id=workpiece_id,
        kind=WorkpieceKind.BOX,
        name=name,
        content_sha256="0" * 64,
        dimensions_mm=resolved,
        geometry=GeometryInspection(
            available=False,
            engine="analytic-box-metadata",
            summary={"kind": "solid"},
        ),
    )
