"""From-zero HTTP workflow using real meshing, spawned workers, and numerical fields."""

from copy import deepcopy
import time

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from thermoflow.api import create_app
from thermoflow.settings import Settings
from thermoflow.tasks import TERMINAL_TASK_STATES


def _json(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _wait_task(client, task_id, *, terminal=True, timeout=90):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = _json(client.get(f"/v1/tasks/{task_id}"))
        if terminal and last["status"] in TERMINAL_TASK_STATES and last["finished_at"]:
            return last
        if not terminal and last["status"] == "running":
            return last
        assert terminal or last["status"] not in TERMINAL_TASK_STATES, last
        time.sleep(0.025)
    pytest.fail(f"Task {task_id} did not reach expected state: {last}")


def _task(client, sid, operation):
    return _json(client.post(f"/v1/studies/{sid}/tasks", json={"operation": operation}), 202)


def _mesh_and_review(client, sid):
    task = _task(client, sid, "mesh")
    done = _wait_task(client, task["task_id"])
    assert done["status"] == "succeeded", done
    mesh = _json(client.get(f"/v1/studies/{sid}/mesh"))
    assert mesh["quality_status"] != "blocked", mesh
    if mesh["review_status"] == "pending":
        # Explicit test-fixture consent after inspecting an actual non-blocked mesh.
        assert client.post(f"/v1/studies/{sid}/tasks", json={"operation": "solve"}).status_code == 409
        mesh = _json(client.post(f"/v1/studies/{sid}/mesh/confirm", json={
            "accept_warnings": True, "confirmed_by": "HTTP workflow regression fixture",
        }))
        assert mesh["review_status"] == "accepted"
    view = _json(client.post(f"/v1/studies/{sid}/view", json={"kind": "mesh"}))
    assert view["triangles"] and view["temperature_k"] is None
    return mesh, view


def _solve(client, sid):
    task = _task(client, sid, "solve")
    done = _wait_task(client, task["task_id"])
    assert done["status"] == "succeeded", done
    study = _json(client.get(f"/v1/studies/{sid}"))
    assert study["status"] == "succeeded" and study["active_task_id"] is None
    return _json(client.get(f"/v1/studies/{sid}/result"))


def test_upload_five_components_edit_cancel_solve_playback_copy_compare(tmp_path):
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / "data",
        cadflow_repo=tmp_path / "missing", planner_mode="deterministic", compute_backend="cpu",
        task_workers=1, task_timeout_seconds=90)
    solids = []
    for x in (0, 8, 16, 24, 32):
        solid = trimesh.creation.box(extents=[4, 4, 4])
        solid.apply_translation([x, 0, 0])
        solids.append(solid)
    payload = trimesh.util.concatenate(solids).export(file_type="stl")
    with TestClient(create_app(settings)) as client:
        project = _json(client.post("/v1/projects", json={"name": "HTTP five-component fixture"}), 201)
        workpiece = _json(client.post("/v1/workpieces/files", data={"project_id": project["project_id"]},
            files={"file": ("five-components.stl", payload, "model/stl")}), 201)
        wid = workpiece["workpiece_id"]
        assert len(workpiece["components"]) == 5 and not workpiece["unit_confirmed"]
        original_hash = workpiece["content_sha256"]
        assert client.post("/v1/studies", json={"workpiece_id": wid, "planning_mode": "manual"}).status_code == 409
        workpiece = _json(client.post(f"/v1/workpieces/{wid}/unit", json={"unit": "mm"}))
        geometry = _json(client.get(f"/v1/workpieces/{wid}/view"))
        ids = {component["component_id"] for component in workpiece["components"]}
        assert set(geometry["component_ids"]) == ids and geometry["coordinate_unit"] == "mm"
        draft = _json(client.post("/v1/studies", json={"workpiece_id": wid, "planning_mode": "manual"}), 201)
        sid = draft["study_id"]
        assert not draft["plan"]["heat_sources"] and draft["confirmation"]["status"] == "needs_input"
        assert client.post(f"/v1/studies/{sid}/tasks", json={"operation": "mesh"}).status_code == 409
        assert client.get(f"/v1/studies/{sid}/result").status_code == 404
        assignments = deepcopy(draft["plan"]["component_materials"])
        for index in range(len(assignments)):
            assignment = assignments[index]
            untouched = deepcopy(assignments[index + 1:])
            assignment["material_id"] = None
            assignment["material"] = {
                "name": f"Explicit fixture material {index + 1}",
                "thermal_conductivity_w_m_k": 10 + 10 * index,
                "density_kg_m3": 1000 + 100 * index,
                "specific_heat_j_kg_k": 900 + 10 * index,
                "source_basis": "Synthetic values specified by the regression fixture",
                "source_type": "user",
            }
            draft = _json(client.put(f"/v1/studies/{sid}/draft", json={
                "expected_revision": draft["draft_revision"],
                "overrides": {"component_materials": assignments},
            }))
            actual = draft["plan"]["component_materials"]
            assert actual[index]["material"]["name"] == assignment["material"]["name"]
            assert actual[index + 1:] == untouched
            assignments = deepcopy(actual)
        sources = [
            {"name": "Point", "shape": "point", "center_mm": {"x": 0, "y": 0, "z": 0},
             "total_power_w": 0.01, "radius_mm": 0.3},
            {"name": "Line", "shape": "line", "center_mm": {"x": 8, "y": -0.8, "z": 0},
             "end_mm": {"x": 8, "y": 0.8, "z": 0}, "total_power_w": 0.02, "radius_mm": 0.3},
            {"name": "Surface", "shape": "surface", "placement": "surface",
             "center_mm": {"x": 16, "y": 0, "z": 2}, "total_power_w": 0.03, "radius_mm": 0.3,
             "surface_normal_axis": "z", "surface_width_mm": 2, "surface_height_mm": 2, "surface_thickness_mm": 0.5},
            {"name": "Volume", "shape": "volume", "center_mm": {"x": 24, "y": 0, "z": 0},
             "total_power_w": 0.04, "radius_mm": 0.3,
             "volume_width_mm": 1.5, "volume_height_mm": 1.5, "volume_depth_mm": 1.5},
        ]
        draft = _json(client.put(f"/v1/studies/{sid}/draft", json={
            "expected_revision": draft["draft_revision"], "overrides": {
                "heat_sources": sources, "enable_heat_source": True, "duration_s": 3600,
                "time_step_s": 18, "target_element_size_mm": 0.5,
            },
        }))
        assert draft["policy"]["accepted"], draft["policy"]
        assert [source["shape"] for source in draft["plan"]["heat_sources"]] == ["point", "line", "surface", "volume"]
        invalid = client.put(f"/v1/studies/{sid}/draft", json={
            "expected_revision": draft["draft_revision"], "overrides": {"duration_s": 3601},
        })
        assert invalid.status_code == 422
        assert _json(client.get(f"/v1/studies/{sid}"))["draft_revision"] == draft["draft_revision"]
        assert client.post(f"/v1/studies/{sid}/confirm", json={}).status_code == 409
        confirmed = _json(client.post(f"/v1/studies/{sid}/confirm", json={
            "expected_revision": draft["draft_revision"], "materials_confirmed": True,
            "confirmed_by": "HTTP workflow regression fixture",
        }))
        snapshot = confirmed["input_snapshot_sha256"]
        assert confirmed["plan"]["duration_s"] == 3600 and snapshot
        cancelled_task = _task(client, sid, "mesh")
        _wait_task(client, cancelled_task["task_id"], terminal=False)
        _json(client.post(f"/v1/tasks/{cancelled_task['task_id']}/cancel"))
        cancelled = _wait_task(client, cancelled_task["task_id"])
        assert cancelled["status"] == "cancelled"
        recovered = _json(client.get(f"/v1/studies/{sid}"))
        assert recovered["input_snapshot_sha256"] == snapshot and recovered["active_task_id"] is None
        assert _json(client.get(f"/v1/workpieces/{wid}"))["content_sha256"] == original_hash
        mesh, mesh_view = _mesh_and_review(client, sid)
        assert set(mesh_view["component_ids"]) == ids
        result = _solve(client, sid)
        assert result["time_s"] == 3600 and len(result["time_steps"]) == 201
        assert result["heat_source_power_w"] == pytest.approx(0.1)
        assert len(result["heat_source_mappings"]) == 4
        assert result["energy_balance_relative_error"] < 1e-5
        playback = _json(client.get(f"/v1/studies/{sid}/playback"))
        assert set(playback["component_ids"]) == ids
        assert len(playback["frames"]) == 201
        times = [frame["time_s"] for frame in playback["frames"]]
        assert times[0] == 0 and times[-1] == 3600 and np.all(np.diff(times) > 0)
        for frame_index in (0, 100, 200):
            frame = _json(client.get(f"/v1/studies/{sid}/frames/{frame_index}"))
            view = _json(client.post(f"/v1/studies/{sid}/view", json={"frame_index": frame_index}))
            assert view["time_s"] == playback["frames"][frame_index]["time_s"] == frame["step"]["time_s"]
            # Playback compacts shared vertices; the individual surface keeps
            # face vertices. Compare equal physical positions, not array indices.
            values = {tuple(point): value for point, value in zip(playback["vertices"],
                playback["frames"][frame_index]["temperature_k"], strict=True)}
            assert np.allclose(view["temperature_k"], [values[tuple(point)] for point in view["vertices"]])
        assert np.allclose(playback["frames"][0]["temperature_k"], 293.15)
        assert playback["frames"][-1]["temperature_max_k"] > 293.15
        unheated_id = next(item["component_id"] for item in workpiece["components"] if item["bbox_source"][0] > 29)
        unheated_vertices = {vertex for triangle, component_id in zip(playback["triangles"], playback["component_ids"], strict=True)
                             if component_id == unheated_id for vertex in triangle}
        assert unheated_vertices
        assert np.allclose(np.asarray(playback["frames"][-1]["temperature_k"])[list(unheated_vertices)], 293.15)
        section = _json(client.post(f"/v1/studies/{sid}/view", json={
            "frame_index": 200, "section_axis": "y", "section_position": {"value": 0, "unit": "mm"},
        }))
        assert section["triangles"] and np.allclose(np.asarray(section["vertices"])[:, 1], 0)
        assert set(section["component_ids"]) == ids
        probe = _json(client.get(f"/v1/studies/{sid}/cells/{section['cell_ids'][0]}/probe", params={"frame_index": 200}))
        assert probe["time_s"] == 3600 and probe["component_id"] in ids
        assert probe["source_sha256"] == section["source_sha256"]
        changed = deepcopy(assignments)
        changed[0]["material"]["name"] = "Modified first material"
        changed[0]["material"]["thermal_conductivity_w_m_k"] = 5
        copy = _json(client.post(f"/v1/studies/{sid}/copy", json={"overrides": {
            "component_materials": changed, "duration_s": 1800, "time_step_s": 9,
        }}), 201)
        cid = copy["study_id"]
        assert copy["status"] == "needs_input" and copy["mesh_status"] == "not_generated"
        assert copy["source_study_id"] == sid and copy["plan"]["component_materials"][1:] == assignments[1:]
        assert client.get(f"/v1/studies/{cid}/result").status_code == 404
        _json(client.post(f"/v1/studies/{cid}/confirm", json={"materials_confirmed": True}))
        _mesh_and_review(client, cid)
        copy_result = _solve(client, cid)
        assert copy_result["time_s"] == 1800
        comparison = _json(client.post("/v1/study-comparisons", json={"study_ids": [sid, cid], "baseline_study_id": sid}))
        assert len(comparison["studies"]) == 2
        assert comparison["differences"][0]["field"] is None
        assert "时刻不同" in comparison["differences"][0]["field_unavailable_reason"]
        assert _json(client.get(f"/v1/studies/{sid}"))["input_snapshot_sha256"] == snapshot
        assert _json(client.get(f"/v1/studies/{sid}/result"))["time_s"] == 3600
        workspace = _json(client.get(f"/v1/projects/{project['project_id']}/workspace"))
        assert len(workspace["workpieces"]) == 1 and len(workspace["studies"]) == 2
