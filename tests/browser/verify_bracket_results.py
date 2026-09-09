"""Read-only numeric acceptance checks for the two user-supplied STL studies."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from thermoflow.storage import FileRepository


def verify(repository, study_id):
    study = repository.get_study(study_id)
    result = repository.get_result(study_id)
    workpiece = repository.get_workpiece(study.workpiece_id)
    assert study.status == "succeeded"
    assert study.plan.initial_temperature_k == 293.15
    assert study.plan.duration_s == 60
    assert study.plan.boundaries == []
    assert len(result.time_steps) == 201
    assert result.time_steps[0].time_s == 0 and result.time_steps[-1].time_s == 60
    assert result.maximum_energy_balance_error_over_time < 1e-5
    artifacts = {item.name: item for item in result.artifacts}
    frames = []
    probe_indices = None
    for target in (0, 1, 10, 30, 60):
        step = min(result.time_steps, key=lambda item: abs(item.time_s - target))
        name = f"thermal-{step.index:04d}.npz"
        path = repository.artifact_path(study_id, name)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifacts[name].sha256
        with np.load(path) as data:
            temperature = data["temperature_k"]
            centers = data["centers_mm"]
            assert float(temperature.min()) == step.temperature_min_k
            assert float(temperature.max()) == step.temperature_max_k
            if probe_indices is None:
                source = np.asarray(result.heat_source_mappings[0]["resolved_center_mm"])
                distances = np.linalg.norm(centers - source, axis=1)
                far = int(distances.argmax())
                middle = (source + centers[far]) / 2
                probe_indices = [int(distances.argmin()), int(np.linalg.norm(centers - middle, axis=1).argmin()), far]
                probe_points = centers[probe_indices].tolist()
                assert np.allclose(temperature, 293.15, rtol=0, atol=1e-9)
            if target == 60:
                assert float(temperature.min()) > 293.151, "Unheated regions must also gain heat in this 60 s aluminum example"
                assert float(np.ptp(temperature)) > 0.01, "The local heat source must not heat every cell identically"
            frames.append({"index": step.index, "time_s": step.time_s,
                           "minimum_c": float(temperature.min() - 273.15),
                           "maximum_c": float(temperature.max() - 273.15),
                           "probe_temperatures_c": (temperature[probe_indices] - 273.15).tolist()})
    mesh = repository.get_mesh(study_id)
    return {"study_id": study_id, "workpiece_id": workpiece.workpiece_id,
            "name": workpiece.name, "stl_sha256": workpiece.content_sha256,
            "material": study.plan.material.name, "initial_c": 20, "duration_s": 60,
            "compute_backend": result.compute_backend, "cell_count": result.grid["cells"],
            "mesh_pitch_mm": mesh.pitch_mm, "mesh_volume_deviation_percent": mesh.quality.volume_deviation_percent,
            "maximum_energy_balance_error": result.maximum_energy_balance_error_over_time,
            "heat_source_mappings": result.heat_source_mappings,
            "probe_positions_mm": probe_points, "frames": frames}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("study_ids", nargs="+")
    args = parser.parse_args()
    print(json.dumps([verify(FileRepository(args.data_dir), sid) for sid in args.study_ids], ensure_ascii=False, indent=2))
