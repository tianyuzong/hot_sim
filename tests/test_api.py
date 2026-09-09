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
    )
    return TestClient(create_app(settings, DeterministicPlanner()))


def test_workpiece_only_api_runs_end_to_end(tmp_path) -> None:
    client = _client(tmp_path)
    workbench = client.get("/docs")
    assert workbench.status_code == 200
    assert "ThermoFlow 热仿真工作台" in workbench.text
    assert 'id="sourceEditor"' in workbench.text
    assert 'id="sliceViewButton"' in workbench.text
    assert 'id="meshViewButton"' in workbench.text
    assert 'id="importProjectMode"' in workbench.text
    assert 'id="copyStudyButton"' in workbench.text
    assert 'id="comparisonDifferenceMode"' in workbench.text
    assert 'id="exitComparisonButton"' in workbench.text
    assert 'id="probeReadout"' in workbench.text
    assert 'id="resultHotspot"' in workbench.text
    assert 'id="confirmMaterials"' in workbench.text
    assert "应用并求解" in workbench.text
    assert client.get("/assets/styles.css").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/docs").status_code == 200

    health = client.get("/health").json()
    assert health["compute"]["preference"] == "auto"
    assert health["compute"]["selected_backend"] in {"cpu-scipy", "cuda-cupy"}
    assert health["compute"]["ready"] is True

    openapi = client.get("/api/openapi.json").json()
    assert openapi["info"]["title"] == "ThermoFlow 热仿真平台 API"
    assert openapi["tags"][1]["name"] == "项目"
    assert openapi["tags"][2]["name"] == "工件"
    assert openapi["tags"][-1]["name"] == "仿真 Agent"
    assert "/v1/studies/{study_id}/agent-runs" in openapi["paths"]
    assert "/v1/studies/{study_id}/mesh" in openapi["paths"]
    assert "/v1/studies/{study_id}/mesh/confirm" in openapi["paths"]
    assert "/v1/projects/{project_id}/workspace" in openapi["paths"]
    assert "/v1/studies/{study_id}/spec" in openapi["paths"]
    assert "/v1/studies/{study_id}/copy" in openapi["paths"]
    assert "/v1/study-comparisons" in openapi["paths"]
    create_study_operation = openapi["paths"]["/v1/studies"]["post"]
    assert create_study_operation["summary"] == "生成结构化仿真草案"
    assert "`overrides`" in create_study_operation["description"]

    registered = client.post(
        "/v1/workpieces/boxes",
        json={
            "name": "Heat sink coupon",
            "dimensions_mm": {"x": 100, "y": 20, "z": 10},
        },
    )
    assert registered.status_code == 201
    workpiece = registered.json()
    assert workpiece["project_id"]
    assert workpiece["geometry"]["engine"] == "analytic-box-metadata"
    listed_workpieces = client.get("/v1/workpieces").json()
    assert [item["workpiece_id"] for item in listed_workpieces] == [workpiece["workpiece_id"]]

    rejected_extra = client.post(
        "/v1/studies",
        json={"workpiece_id": workpiece["workpiece_id"], "material": "copper"},
    )
    assert rejected_extra.status_code == 422

    created = client.post(
        "/v1/studies",
        json={"workpiece_id": workpiece["workpiece_id"]},
    )
    assert created.status_code == 201
    study = created.json()
    assert study["status"] == "needs_input"
    assert study["confirmation"]["status"] == "needs_input"
    assert study["planner"]["provider"] == "deterministic-offline"
    assert study["policy"]["accepted"] is True
    listed_studies = client.get(
        "/v1/studies", params={"workpiece_id": workpiece["workpiece_id"]}
    ).json()
    assert [item["study_id"] for item in listed_studies] == [study["study_id"]]

    unconfirmed = client.post(f"/v1/studies/{study['study_id']}/run")
    assert unconfirmed.status_code == 409

    confirmed = client.post(
        f"/v1/studies/{study['study_id']}/confirm",
        json={"materials_confirmed": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "ready"

    completed = client.post(f"/v1/studies/{study['study_id']}/run")
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"

    result_response = client.get(f"/v1/studies/{study['study_id']}/result")
    assert result_response.status_code == 200
    result = result_response.json()
    assert result["heat_rate_w"] == pytest.approx(26.72)
    assert result["energy_balance_relative_error"] == 0.0

    artifact = client.get(f"/v1/studies/{study['study_id']}/artifacts/temperature.vtk")
    assert artifact.status_code == 200
    assert b"SCALARS temperature_k double 1" in artifact.content


def test_uploaded_cad_is_rejected_for_planning_when_cadflow_is_unavailable(tmp_path) -> None:
    client = _client(tmp_path)
    uploaded = client.post(
        "/v1/workpieces/files",
        files={"file": ("part.step", b"not-a-real-step", "application/step")},
    )
    assert uploaded.status_code == 201

    created = client.post(
        "/v1/studies",
        json={"workpiece_id": uploaded.json()["workpiece_id"]},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "rejected"


def test_single_stl_input_runs_complete_simulation(tmp_path) -> None:
    client = _client(tmp_path)
    stl = trimesh.creation.box(extents=[60, 30, 15]).export(file_type="stl")

    response = client.post(
        "/v1/simulations/stl",
        files={"file": ("cadflow-part.stl", stl, "model/stl")},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["workpiece"]["cad_format"] == "stl"
    assert payload["workpiece"]["geometry"]["available"] is True
    assert payload["study"]["status"] == "succeeded"
    assert payload["study"]["plan"]["solver"]["backend"] == "voxel_stl_v1"
    assert payload["result"]["solver_backend"] == "voxel_stl_v1"
    assert payload["result"]["compute_backend"].startswith(("cpu-", "cuda-"))
    assert payload["result"]["compute_device"]
    assert payload["result"]["artifacts"][0]["name"] == "temperature.vtk"

    openapi = client.get("/api/openapi.json").json()
    operation = openapi["paths"]["/v1/simulations/stl"]["post"]
    assert operation["summary"] == "兼容模式：上传 STL 并同步求解"


def test_single_stl_input_rejects_other_extensions(tmp_path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/v1/simulations/stl",
        files={"file": ("part.step", b"not-step", "application/step")},
    )
    assert response.status_code == 400
    assert "只接受" in response.json()["detail"]


def test_open_stl_input_is_reconstructed_end_to_end(tmp_path) -> None:
    client = _client(tmp_path)
    mesh = trimesh.creation.box(extents=[40, 24, 12])
    mesh.update_faces([*range(len(mesh.faces) - 1)])

    response = client.post(
        "/v1/simulations/stl",
        files={"file": ("open-part.stl", mesh.export(file_type="stl"), "model/stl")},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["workpiece"]["geometry"]["available"] is True
    assert payload["workpiece"]["geometry"]["summary"]["volume"] is None
    assert payload["workpiece"]["geometry"]["summary"]["quality"]["watertight"] is False
    assert "开放网格" in " ".join(payload["study"]["policy"]["warnings"])
    assert payload["study"]["status"] == "succeeded"
    assert payload["result"]["energy_balance_relative_error"] < 1e-5


def test_single_stl_input_applies_user_parameters(tmp_path) -> None:
    client = _client(tmp_path)
    stl = trimesh.creation.box(extents=[60, 30, 15]).export(file_type="stl")

    response = client.post(
        "/v1/simulations/stl",
        files={"file": ("configured-part.stl", stl, "model/stl")},
        data={
            "material_name": "用户材料 A",
            "thermal_conductivity_w_m_k": "42.5",
            "density_kg_m3": "7800",
            "specific_heat_j_kg_k": "460",
            "heat_axis": "y",
            "min_face_temperature_k": "310",
            "max_face_temperature_k": "350",
            "heat_source_x_mm": "5",
            "heat_source_y_mm": "0",
            "heat_source_z_mm": "0",
            "heat_source_shape": "line",
            "heat_source_placement": "embedded",
            "heat_source_embedding_depth_mm": "3",
            "heat_source_power_w": "40",
            "heat_source_radius_mm": "4",
            "heat_source_end_x_mm": "5",
            "heat_source_end_y_mm": "10",
            "heat_source_end_z_mm": "0",
            "ambient_temperature_k": "300",
            "convection_coefficient_w_m2_k": "25",
            "target_element_size_mm": "3",
            "max_axis_intervals": "50",
            "relative_tolerance": "1e-7",
            "max_iterations": "1200",
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    overrides = payload["study"]["overrides"]
    plan = payload["study"]["plan"]
    assert overrides["thermal_conductivity_w_m_k"] == pytest.approx(42.5)
    assert overrides["heat_axis"] == "y"
    assert plan["material"]["name"] == "用户材料 A"
    assert plan["material"]["thermal_conductivity_w_m_k"] == pytest.approx(42.5)
    assert plan["material"]["density_kg_m3"] == pytest.approx(7800)
    assert plan["material"]["specific_heat_j_kg_k"] == pytest.approx(460)
    assert [item["selector"] for item in plan["boundaries"]] == ["face.ymin", "face.ymax"]
    assert [item["temperature_k"] for item in plan["boundaries"]] == [310, 350]
    assert plan["mesh"]["target_element_size_mm"] == pytest.approx(3)
    assert plan["mesh"]["max_axis_intervals"] == 50
    assert plan["solver"]["relative_tolerance"] == pytest.approx(1e-7)
    assert plan["solver"]["max_iterations"] == 1200
    assert plan["heat_source"] == {
        "kind": "volumetric_power",
        "source_id": None,
        "name": "热源",
        "shape": "line",
        "placement": "embedded",
        "center_mm": {"x": 5.0, "y": 0.0, "z": 0.0},
        "total_power_w": 40.0,
        "radius_mm": 4.0,
        "embedding_depth_mm": 3.0,
        "end_mm": {"x": 5.0, "y": 10.0, "z": 0.0},
        "surface_normal_axis": None,
        "surface_width_mm": None,
        "surface_height_mm": None,
        "surface_thickness_mm": None,
        "volume_width_mm": None,
        "volume_height_mm": None,
        "volume_depth_mm": None,
    }
    assert plan["convection"]["ambient_temperature_k"] == pytest.approx(300)
    assert plan["convection"]["heat_transfer_coefficient_w_m2_k"] == pytest.approx(25)
    assert "Y 轴" in plan["decision_summary"]
    assert "40 K" in plan["decision_summary"]
    assert payload["result"]["heat_source_power_w"] == pytest.approx(40)
    assert payload["result"]["heat_source_cells"] > 0
    assert payload["result"]["heat_source_mapping"]["shape"] == "line"
    assert payload["result"]["energy_balance_relative_error"] < 1e-5

    openapi = client.get("/api/openapi.json").json()
    schema_ref = openapi["paths"]["/v1/simulations/stl"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]["$ref"]
    schema = openapi["components"]["schemas"][schema_ref.rsplit("/", 1)[-1]]
    assert "target_element_size_mm" in schema["properties"]
    assert "relative_tolerance" in schema["properties"]
    assert "heat_source_power_w" in schema["properties"]
    assert "heat_source_shape" in schema["properties"]
    assert "heat_source_surface_width_mm" in schema["properties"]
    assert "heat_source_volume_width_mm" in schema["properties"]
    assert "heat_source_volume_height_mm" in schema["properties"]
    assert "heat_source_volume_depth_mm" in schema["properties"]
    assert "convection_coefficient_w_m2_k" in schema["properties"]
