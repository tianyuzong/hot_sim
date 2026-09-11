"""User timing survives the API, immutable copy, solver and playback paths."""
import pytest
import trimesh
from fastapi.testclient import TestClient
from pydantic import ValidationError

from thermoflow.api import create_app
from thermoflow.models import SimulationOverrides
from thermoflow.settings import Settings


@pytest.mark.parametrize("duration", [0, -1, 3600.0001, 3601, float("inf")])
def test_duration_contract_rejects_values_outside_one_hour(duration):
    with pytest.raises(ValidationError):
        SimulationOverrides(duration_s=duration)


@pytest.mark.parametrize("duration", [0.5, 57, 3600])
def test_duration_survives_copy_solve_and_playback(tmp_path, duration):
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / "data",
                        cadflow_repo=tmp_path / "missing", planner_mode="deterministic",
                        compute_backend="cpu")
    with TestClient(create_app(settings)) as client:
        part = client.post("/v1/workpieces/files", files={
            "file": ("timing-block.stl", trimesh.creation.box(extents=[4, 4, 4]).export(file_type="stl"), "model/stl"),
        }).json()
        wid = part["workpiece_id"]
        assert client.post(f"/v1/workpieces/{wid}/unit", json={"unit": "mm"}).status_code == 200
        response = client.post("/v1/studies", json={
            "workpiece_id": wid, "planning_mode": "manual",
            "overrides": {"material_name": "铝合金 6061-T6", "target_element_size_mm": 1,
                          "enable_heat_source": False, "initial_temperature_k": 323.15},
        })
        assert response.status_code == 201, response.text
        original = response.json()
        sid = original["study_id"]
        assert client.post(f"/v1/studies/{sid}/confirm", json={"materials_confirmed": True}).status_code == 200
        for invalid in [0, -1, 3600.0001]:
            rejected = client.post(f"/v1/studies/{sid}/copy", json={"overrides": {"duration_s": invalid}})
            assert rejected.status_code == 422, rejected.text
        timing = {"analysis_type": "transient_conduction", "duration_s": duration, "time_step_s": duration / 200}
        response = client.post(f"/v1/studies/{sid}/copy", json={"overrides": timing})
        assert response.status_code == 201, response.text
        copied = response.json()
        assert copied["plan"]["duration_s"] == duration
        assert copied["plan"]["heat_source_enabled"] is False
        assert copied["plan"]["component_materials"] == original["plan"]["component_materials"]
        prefix = f"/v1/studies/{copied['study_id']}"
        confirmed = client.post(prefix + "/confirm", json={"materials_confirmed": True})
        assert confirmed.status_code == 200, confirmed.text
        spec = client.get(prefix + "/spec").json()
        assert spec["scenario"]["duration"] == {"value": duration, "unit": "s"}
        mesh = client.post(prefix + "/mesh")
        assert mesh.status_code == 201, mesh.text
        if mesh.json()["review_status"] == "pending":
            assert client.post(prefix + "/mesh/confirm", json={"accept_warnings": True}).status_code == 200
        solved = client.post(prefix + "/run")
        assert solved.status_code == 200, solved.text
        result = client.get(prefix + "/result").json()
        assert len(result["time_steps"]) == 201
        assert result["time_steps"][0]["time_s"] == 0
        assert result["time_steps"][-1]["time_s"] == duration
        playback = client.get(prefix + "/playback")
        assert playback.status_code == 200, playback.text
        assert playback.json()["frames"][-1]["time_s"] == duration
        assert client.get(f"/v1/studies/{sid}").json()["plan"]["duration_s"] == 60
