"""Generate a real numerical study for browser acceptance checks."""
import json
import tempfile
from pathlib import Path

import trimesh

from thermoflow.cadflow_adapter import CadFlowGeometryInspector
from thermoflow.models import (
    LengthUnit,
    MeshReviewRequest,
    SimulationOverrides,
    StudyConfirmationRequest,
)
from thermoflow.planner import DeterministicPlanner
from thermoflow.service import StudyService
from thermoflow.storage import FileRepository

data_dir = Path(tempfile.mkdtemp(prefix="thermoflow-browser-"))
repository = FileRepository(data_dir)
service = StudyService(repository=repository, planner=DeterministicPlanner(),
                       geometry=CadFlowGeometryInspector(data_dir / "missing"), compute_backend="cpu")
part = service.register_stl("cooling-block.stl", trimesh.creation.box(extents=[20, 12, 8]).export(file_type="stl"))
service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
study = service.create_study(part.workpiece_id, purpose="6061 铝合金，瞬态导热，初始温度 480 K，持续 2 秒，步长 0.25 秒，热源功率 1 W，最高温度不超过 400 K",
                             overrides=SimulationOverrides(target_element_size_mm=2), require_confirmation=True)
service.confirm_study(
    study.study_id,
    StudyConfirmationRequest(materials_confirmed=True),
)
mesh = service.generate_mesh(study.study_id)
if mesh.review_status == "pending":
    service.confirm_mesh(study.study_id, MeshReviewRequest(accept_warnings=True))
service.run_study(study.study_id)
print(json.dumps({"data_dir": str(data_dir), "study_id": study.study_id,
                  "workpiece_id": part.workpiece_id, "project_id": study.project_id}))
