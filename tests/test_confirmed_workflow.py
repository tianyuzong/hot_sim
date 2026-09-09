from __future__ import annotations

import pytest
import trimesh
from fastapi.testclient import TestClient

from thermoflow.api import create_app
from thermoflow.planner import DeterministicPlanner
from thermoflow.settings import Settings


def _client(tmp_path) -> TestClient:
    settings = Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        cadflow_repo=tmp_path / "missing-cadflow",
        planner_mode="deterministic",
        compute_backend="cpu",
    )
    return TestClient(create_app(settings, DeterministicPlanner()))


def test_stl_requires_scale_and_user_confirmation_before_solving(tmp_path) -> None:
    client = _client(tmp_path)
    payload = trimesh.creation.box(extents=[60, 30, 15]).export(file_type="stl")

    imported = client.post(
        "/v1/workpieces/files",
        files={"file": ("heat-sink.stl", payload, "model/stl")},
    )

    assert imported.status_code == 201
    workpiece = imported.json()
    assert workpiece["unit_confirmed"] is False
    assert workpiece["dimensions_mm"] is None
    assert workpiece["geometry"]["summary"]["coordinate_system"]["length_unit"] is None
    assert workpiece["geometry"]["summary"]["preview"]["vertices_source"]
    assert "vertices_mm" not in workpiece["geometry"]["summary"]["preview"]
    assert workpiece["source_dimensions"] == {"x": 60.0, "y": 30.0, "z": 15.0}
    assert len(workpiece["components"]) == 1

    premature = client.post(
        "/v1/studies",
        json={"workpiece_id": workpiece["workpiece_id"], "require_confirmation": True},
    )
    assert premature.status_code == 409
    assert "确认 STL" in premature.json()["detail"]

    confirmed_scale = client.post(
        f"/v1/workpieces/{workpiece['workpiece_id']}/unit",
        json={"unit": "mm"},
    )
    assert confirmed_scale.status_code == 200
    assert confirmed_scale.json()["dimensions_mm"] == {"x": 60.0, "y": 30.0, "z": 15.0}
    assert confirmed_scale.json()["geometry"]["summary"]["preview"]["vertices_mm"]

    draft = client.post(
        "/v1/studies",
        json={
            "workpiece_id": workpiece["workpiece_id"],
            "purpose": (
                "6061 铝合金散热件，环境 25 ℃，热源功率 40 W，"
                "最高温度不超过 80 ℃，并评估热应力。"
            ),
            "require_confirmation": True,
        },
    )
    assert draft.status_code == 201
    study = draft.json()
    assert study["status"] == "needs_input"
    assert study["confirmation"]["status"] == "needs_input"
    assert study["plan"]["material"]["name"] == "铝合金 6061-T6"
    assert study["plan"]["heat_source"]["total_power_w"] == pytest.approx(40)
    assert study["plan"]["convection"]["ambient_temperature_k"] == pytest.approx(298.15)
    assert study["plan"]["criteria"][0]["target"]["value"] == pytest.approx(353.15)
    assert "热应力" in " ".join(study["plan"]["unsupported_physics"])

    blocked = client.post(f"/v1/studies/{study['study_id']}/run")
    assert blocked.status_code == 409

    rejected_materials = client.post(
        f"/v1/studies/{study['study_id']}/confirm", json={}
    )
    assert rejected_materials.status_code == 409
    assert "明确确认" in rejected_materials.json()["detail"]

    confirmed = client.post(
        f"/v1/studies/{study['study_id']}/confirm",
        json={"materials_confirmed": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "ready"
    assert confirmed.json()["input_snapshot_sha256"]
    assert confirmed.json()["mesh_status"] == "not_generated"

    blocked_without_mesh = client.post(f"/v1/studies/{study['study_id']}/run")
    assert blocked_without_mesh.status_code == 409
    assert "真实网格" in blocked_without_mesh.json()["detail"]

    generated_mesh = client.post(f"/v1/studies/{study['study_id']}/mesh")
    assert generated_mesh.status_code == 201
    mesh = generated_mesh.json()
    assert mesh["active_cells"] > 0
    assert mesh["surface_cells"] > 0
    assert mesh["cell_samples_mm"]
    assert mesh["artifacts"][0]["name"] == "mesh.vtk"

    stored_mesh = client.get(f"/v1/studies/{study['study_id']}/mesh")
    assert stored_mesh.status_code == 200
    assert stored_mesh.json()["plan_snapshot_sha256"] == confirmed.json()["input_snapshot_sha256"]
    if mesh["review_status"] == "pending":
        reviewed = client.post(
            f"/v1/studies/{study['study_id']}/mesh/confirm",
            json={"accept_warnings": True, "confirmed_by": "测试工程师"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["review_status"] == "accepted"

    completed = client.post(f"/v1/studies/{study['study_id']}/run")
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert completed.json()["evaluation_status"] in {"indeterminate", "violates_criteria"}

    result = client.get(f"/v1/studies/{study['study_id']}/result").json()
    assert result["evaluation_status"] == completed.json()["evaluation_status"]
    assert result["evaluation_summary"]
    assert result["grid"] == mesh["grid"]


def test_unit_conversion_creates_millimeter_solver_geometry(tmp_path) -> None:
    client = _client(tmp_path)
    payload = trimesh.creation.box(extents=[10_000, 5_000, 1_000]).export(file_type="stl")
    workpiece = client.post(
        "/v1/workpieces/files",
        files={"file": ("micro-device.stl", payload, "model/stl")},
    ).json()

    response = client.post(
        f"/v1/workpieces/{workpiece['workpiece_id']}/unit",
        json={"unit": "um"},
    )

    assert response.status_code == 200
    converted = response.json()
    assert converted["dimensions_mm"] == {"x": 10.0, "y": 5.0, "z": 1.0}
    assert converted["geometry"]["summary"]["coordinate_system"]["unit_basis"] == "用户确认"
