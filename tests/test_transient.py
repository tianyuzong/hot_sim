from __future__ import annotations

import hashlib
import json
import math
from itertools import pairwise

import numpy as np
import pytest
import trimesh
from scipy.sparse import csr_matrix

from tests.test_confirmed_service import _service
from tests.test_confirmed_workflow import _client
from thermoflow.models import BoxWorkpieceInput, DimensionsMM
from thermoflow.solvers.transient import integrate_thermal_system
from thermoflow.specification import simulation_plan_sha256, simulation_spec_sha256


def test_implicit_temperature_converges_to_analytic_cooling_and_conserves_energy():
    # C dT/dt + G T = G T_inf, with exact exponential cooling.
    errors = []
    exact = 300 + 100 * math.exp(-2 * 3 / 10)
    for dt in (0.3, 0.15):
        steps = list(integrate_thermal_system(
            csr_matrix([[2.0]]), np.array([600.0]), np.array([10.0]), np.array([400.0]),
            duration_s=3, time_step_s=dt, relative_tolerance=1e-10,
            max_iterations=100, preference="cpu",
        ))
        errors.append(abs(steps[-1][1][0] - exact))
        assert steps[-1][0] == pytest.approx(3)
        assert max(step[4] for step in steps) < 1e-10
        assert sum(step[3] for step in steps) == pytest.approx(10 * (steps[-1][1][0] - 400))
        assert all(300 < step[1][0] < 400 for step in steps)
    assert errors[1] < errors[0] * 0.55


def test_last_time_step_ends_at_requested_duration():
    steps = list(integrate_thermal_system(
        csr_matrix([[1.0]]), np.array([300.0]), np.array([1.0]), np.array([400.0]),
        duration_s=1, time_step_s=0.3, relative_tolerance=1e-10,
        max_iterations=100, preference="cpu",
    ))
    assert [step[0] for step in steps] == pytest.approx([0.3, 0.6, 0.9, 1])


def test_nonuniform_outputs_use_real_substeps_below_the_user_limit():
    recorded_steps = []
    reference_powers = []
    capacity = np.array([10.0])
    initial = np.array([400.0])

    steps = list(integrate_thermal_system(
        csr_matrix([[2.0]]), np.array([600.0]), capacity, initial,
        duration_s=1.0, time_step_s=0.2,
        output_times_s=(0.05, 0.3, 1.0),
        integration_steps=recorded_steps,
        energy_reference_powers=reference_powers,
        relative_tolerance=1e-10, max_iterations=100, preference="cpu",
    ))

    assert [step[0] for step in steps] == pytest.approx([0.05, 0.3, 1.0])
    assert len(recorded_steps) == 7
    assert max(recorded_steps) <= 0.2 * (1 + 1e-12)
    assert sum(recorded_steps) == pytest.approx(1.0)
    assert len(reference_powers) == len(steps)
    assert sum(step[3] for step in steps) == pytest.approx(
        capacity[0] * (steps[-1][1][0] - initial[0])
    )


@pytest.mark.parametrize(
    "output_times_s",
    [
        (0.0, 1.0),
        (0.5, 0.5, 1.0),
        (0.6, 0.5, 1.0),
        (0.5, 1.1),
        (0.5,),
    ],
)
def test_nonuniform_outputs_must_be_strict_and_end_at_duration(output_times_s):
    with pytest.raises(ValueError, match="输出时刻"):
        list(integrate_thermal_system(
            csr_matrix([[1.0]]), np.array([300.0]), np.array([1.0]), np.array([400.0]),
            duration_s=1.0, time_step_s=0.2, output_times_s=output_times_s,
            relative_tolerance=1e-10, max_iterations=100, preference="cpu",
        ))


def test_new_optional_fields_preserve_legacy_steady_fingerprints(tmp_path):
    service, _ = _service(tmp_path)
    part = service.register_box(BoxWorkpieceInput(name="Reference", dimensions_mm=DimensionsMM(x=10, y=6, z=4)))
    study = service.create_study(part.workpiece_id)
    plan = study.plan.model_dump(mode="json")
    for key in ("initial_temperature_k", "duration_s", "time_step_s", "surface_conditions", "contacts", "heat_source_enabled", "global_convection_enabled"):
        plan.pop(key)
    for boundary in plan["boundaries"]:
        boundary.pop("region_id")
    spec = study.simulation_spec.model_dump(mode="json")
    spec["scenario"].pop("duration")
    spec["solver"].pop("time_step")
    spec["solver"].pop("time_integration")
    spec["conditions"].pop("contacts")
    for region in spec["geometry"]["regions"]:
        region.pop("triangle_ids")
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    assert simulation_plan_sha256(study.plan) == digest(plan)
    assert simulation_spec_sha256(study.simulation_spec) == digest(spec)


def test_transient_confirmation_fields_and_full_numeric_frames(tmp_path, monkeypatch):
    client = _client(tmp_path)
    part = client.post("/v1/workpieces/files", files={
        "file": ("cooling-block.stl", trimesh.creation.box(extents=[10, 6, 4]).export(file_type="stl"), "model/stl"),
    }).json()
    client.post(f"/v1/workpieces/{part['workpiece_id']}/unit", json={"unit": "mm"})
    draft = client.post("/v1/studies", json={
        "workpiece_id": part["workpiece_id"],
        "purpose": "6061 铝合金，瞬态导热，初始温度 450 K，持续 1 秒，热源功率 1 W，最高温度不超过 400 K",
        "overrides": {"target_element_size_mm": 1},
    })
    assert draft.status_code == 201, draft.text
    study = draft.json()
    prefix = f"/v1/studies/{study['study_id']}"
    assert study["status"] == "needs_input"
    assert study["plan"]["initial_temperature_k"] == 450
    assert client.post(prefix + "/confirm", json={}).status_code == 409
    confirmed = client.post(
        prefix + "/confirm",
        json={"overrides": {"time_step_s": 0.3}, "materials_confirmed": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    policy_derived = confirmed.json()["policy"]["derived"]
    assert policy_derived["maximum_internal_step_s"] == pytest.approx(0.3)
    assert policy_derived["saved_time_steps"] == 200
    spec = client.get(prefix + "/spec").json()
    assert spec["schema_version"] == "1.1"
    assert spec["scenario"]["duration"] == {"value": 1, "unit": "s"}
    assert spec["conditions"]["initial"][0]["temperature"] == {"value": 450, "unit": "K"}
    assert spec["solver"]["time_step"] == {"value": 0.3, "unit": "s"}
    mesh = client.post(prefix + "/mesh").json()
    if mesh["review_status"] == "pending":
        client.post(prefix + "/mesh/confirm", json={"accept_warnings": True})
    solved = client.post(prefix + "/run")
    assert solved.status_code == 200, solved.text
    result = client.get(prefix + "/result").json()
    assert result["analysis_type"] == "transient_conduction"
    times = [step["time_s"] for step in result["time_steps"]]
    aluminum_diffusivity = 167.0 / (2700.0 * 896.0)
    expected_early_step = 0.25 * 0.001**2 / aluminum_diffusivity
    assert len(times) == 201
    assert times[0] == 0
    assert times[1] <= expected_early_step * (1 + 1e-12)
    assert times[-1] == 1
    assert all(later > earlier for earlier, later in pairwise(times))
    assert result["temperature_max_over_time_k"] >= 450
    assert result["temperature_max_k"] < 400
    assert result["evaluation_status"] == "violates_criteria"
    assert result["maximum_energy_balance_error_over_time"] < 1e-5
    from thermoflow import visualization
    original_read = visualization.read_solver_vtk
    vtk_reads = []
    def count_read(path):
        vtk_reads.append(path)
        return original_read(path)
    with monkeypatch.context() as patch:
        patch.setattr(visualization, "read_solver_vtk", count_read)
        playback_response = client.get(prefix + "/playback")
    assert len(vtk_reads) == 1, "Playback must read topology once and load verified compact frame arrays"
    assert playback_response.status_code == 200, playback_response.text
    assert playback_response.headers["content-encoding"] == "gzip"
    playback = playback_response.json()
    assert len(playback["vertices"]) == len({tuple(v) for v in playback["vertices"]}), (
        "Playback must share exterior nodes rather than retaining four copies per voxel face"
    )
    assert playback["study_id"] == study["study_id"]
    assert len(playback["frames"]) == len(result["time_steps"])
    assert playback["frames"][0]["index"] == 0
    assert playback["frames"][-1]["time_s"] == pytest.approx(1)
    assert playback["frames"][0]["temperature_k"] != playback["frames"][-1]["temperature_k"]
    assert len(playback["frames"][0]["temperature_k"]) == len(playback["vertices"])
    assert "vertices" not in playback["frames"][0]
    assert "triangles" not in playback["frames"][0]
    assert [frame["time_s"] for frame in playback["frames"]] == pytest.approx(times)
    # Shared topology must not change the actual reconstructed surface temperatures.
    for index in (0, 100, 200):
        surface = client.post(prefix + "/view", json={"frame_index": index}).json()
        original = {tuple(v): t for v, t in zip(surface["vertices"], surface["temperature_k"], strict=True)}
        for vertex, temperature in zip(playback["vertices"], playback["frames"][index]["temperature_k"], strict=True):
            assert temperature == pytest.approx(original[tuple(vertex)], rel=0, abs=1e-8)
    for index, step in enumerate(result["time_steps"]):
        frame = client.get(prefix + f"/frames/{index}")
        assert frame.status_code == 200
        assert frame.json()["step"] == step
        field = np.load(tmp_path / "data" / "studies" / study["study_id"] / "artifacts" / f"thermal-{index:04d}.npz")
        assert len(field["temperature_k"]) == result["grid"]["cells"]
        assert float(field["temperature_k"].max()) == step["temperature_max_k"]
        assert field["heat_flux_w_m2"].shape == (result["grid"]["cells"], 3)
        vtk = client.get(prefix + "/artifacts/" + step["vtk_artifact"]).content
        artifact = next(a for a in result["artifacts"] if a["name"] == step["vtk_artifact"])
        assert hashlib.sha256(vtk).hexdigest() == artifact["sha256"]
    assert client.get(prefix + "/frames/-1").status_code == 404
    assert client.get(prefix + "/frames/1000").status_code == 404
    corrupt_array = tmp_path / "data" / "studies" / study["study_id"] / "artifacts" / "thermal-0200.npz"
    corrupt_array.write_bytes(b"invalid frame")
    damaged_playback = client.get(prefix + "/playback")
    assert damaged_playback.status_code == 409
    assert "校验失败" in damaged_playback.json()["detail"]
    corrupted = tmp_path / "data" / "studies" / study["study_id"] / "artifacts" / "thermal-0000.json"
    corrupted.write_text("{}")
    assert client.get(prefix + "/frames/0").status_code == 409


def test_first_adaptive_point_source_frame_heats_near_cells_before_far_cells(tmp_path):
    client = _client(tmp_path)
    part = client.post("/v1/workpieces/files", files={
        "file": (
            "point-source.stl",
            trimesh.creation.box(extents=[20, 10, 5]).export(file_type="stl"),
            "model/stl",
        ),
    }).json()
    client.post(f"/v1/workpieces/{part['workpiece_id']}/unit", json={"unit": "mm"})
    study = client.post("/v1/studies", json={
        "workpiece_id": part["workpiece_id"],
        "purpose": "C110 紫铜，瞬态导热，初始温度 293.15 K，持续 1 秒，点热源 10 W",
        "overrides": {
            "fixed_boundaries": [],
            "enable_global_convection": False,
            "target_element_size_mm": 2,
            "heat_source_x_mm": 10,
            "heat_source_y_mm": 5,
            "heat_source_z_mm": 2.5,
            "heat_source_radius_mm": 0.5,
        },
    }).json()
    prefix = f"/v1/studies/{study['study_id']}"
    confirmed = client.post(
        prefix + "/confirm",
        json={"overrides": {"time_step_s": 1.0}, "materials_confirmed": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    mesh = client.post(prefix + "/mesh").json()
    if mesh["review_status"] == "pending":
        client.post(prefix + "/mesh/confirm", json={"accept_warnings": True})
    solved = client.post(prefix + "/run")
    assert solved.status_code == 200, solved.text
    result = client.get(prefix + "/result").json()
    assert len(result["time_steps"]) == 201
    source_center = np.asarray(result["heat_source_mapping"]["resolved_center_mm"])
    first = np.load(
        tmp_path / "data" / "studies" / study["study_id"] / "artifacts" / "thermal-0001.npz"
    )
    distances = np.linalg.norm(first["centers_mm"] - source_center, axis=1)
    near_temperature = float(first["temperature_k"][distances.argmin()])
    far_temperature = float(first["temperature_k"][distances.argmax()])
    assert near_temperature > far_temperature
    assert np.linalg.norm(
        np.asarray(result["time_steps"][1]["maximum_position_mm"]) - source_center
    ) <= math.sqrt(3) * 2.0
