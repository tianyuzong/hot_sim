"""A manual STL workflow must remain usable without a model service."""
import pytest
import trimesh
from fastapi.testclient import TestClient

from thermoflow.api import create_app
from thermoflow.settings import Settings


def test_manual_template_uses_available_mesh_budget_for_thin_bracket():
    from thermoflow.manual import manual_stl_template
    from thermoflow.models import WorkpieceRecord

    workpiece = WorkpieceRecord.model_validate({
        "workpiece_id": "wp-bracket", "kind": "cad_file", "name": "bracket",
        "content_sha256": "0" * 64, "cad_format": "stl",
        "dimensions_mm": {"x": 80, "y": 50, "z": 59},
        "geometry": {"available": True, "engine": "fixture", "summary": {
            "bbox": [0, 0, 0, 80, 50, 59], "volume": 74018.281773, "area": 23101.021564,
        }},
    })
    assert manual_stl_template(workpiece).plan.mesh.target_element_size_mm == pytest.approx(1.1)


class UnavailablePlanner:
    def plan(self, *args, **kwargs):
        raise AssertionError("Manual inputs must not call an AI planner")


def test_manual_upload_to_confirmed_transient_and_editable_copy(tmp_path):
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / "data",
                        cadflow_repo=tmp_path / "missing", planner_mode="deterministic",
                        compute_backend="cpu")
    with TestClient(create_app(settings, UnavailablePlanner())) as client:
        mesh = trimesh.creation.box(extents=[80, 50, 59])
        uploaded = client.post("/v1/workpieces/files", files={
            "file": ("bracket-envelope.stl", mesh.export(file_type="stl"), "model/stl")
        }).json()
        wid = uploaded["workpiece_id"]
        assert client.post(f"/v1/workpieces/{wid}/unit", json={"unit": "mm"}).status_code == 200
        created = client.post("/v1/studies", json={
            "workpiece_id": wid, "planning_mode": "manual",
            "overrides": {"material_name": "铝合金 6061-T6", "target_element_size_mm": 1.1},
        })
        assert created.status_code == 201, created.text
        study = created.json()
        plan = study["plan"]
        assert study["status"] == "needs_input"
        assert study["planner"]["provider"] == "manual-template"
        assert plan["analysis_type"] == "transient_conduction"
        assert plan["initial_temperature_k"] == pytest.approx(293.15)
        assert plan["duration_s"] == 60
        assert plan["heat_source"] is None
        assert plan["heat_sources"] == []
        assert plan["heat_source_enabled"] is False
        assert plan["boundaries"] == [], "Do not invent a cold clamp on either end"
        assert plan["component_materials"][0]["material_id"] == "al-6061-t6"
        assert study["policy"]["accepted"], study["policy"]["errors"]
        # An explicit user request must still be able to add a source to the empty draft.
        heating = client.post("/v1/studies", json={
            "workpiece_id": wid, "planning_mode": "manual",
            "overrides": {"enable_heat_source": True, "heat_source_power_w": 3,
                          "heat_source_x_mm": 0, "heat_source_y_mm": 0, "heat_source_z_mm": 0},
        })
        assert heating.status_code == 201, heating.text
        assert heating.json()["plan"]["heat_source_enabled"] is True
        assert heating.json()["plan"]["heat_sources"][0]["total_power_w"] == 3
        sid = study["study_id"]
        assert client.post(f"/v1/studies/{sid}/confirm", json={}).status_code == 409
        confirmed = client.post(f"/v1/studies/{sid}/confirm", json={"materials_confirmed": True})
        assert confirmed.status_code == 200, confirmed.text
        copied = client.post(f"/v1/studies/{sid}/copy", json={
            "overrides": {"material_name": "304 不锈钢"}
        })
        assert copied.status_code == 201, copied.text
        assert copied.json()["status"] == "needs_input"
        assert copied.json()["plan"]["component_materials"][0]["material"]["thermal_conductivity_w_m_k"] == 16.2
        assert client.get(f"/v1/studies/{sid}").json()["plan"]["material"]["thermal_conductivity_w_m_k"] == 167
