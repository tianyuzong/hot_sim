"""Isolated editable invalid draft; never writes to the live user's workspace."""
import json
import tempfile
from pathlib import Path

import trimesh

from thermoflow.cadflow_adapter import CadFlowGeometryInspector
from thermoflow.models import LengthUnit, SimulationOverrides
from thermoflow.planner import DeterministicPlanner
from thermoflow.service import StudyService
from thermoflow.storage import FileRepository

data_dir = Path(tempfile.mkdtemp(prefix='thermoflow-validation-test-'))
service = StudyService(repository=FileRepository(data_dir), planner=DeterministicPlanner(),
                       geometry=CadFlowGeometryInspector(data_dir / 'missing'), compute_backend='cpu')
part = service.register_stl('validation-block.stl', trimesh.creation.box(extents=[20, 12, 8]).export(file_type='stl'))
service.confirm_workpiece_unit(part.workpiece_id, LengthUnit.MILLIMETER)
study = service.create_study(part.workpiece_id, planning_mode='manual', require_confirmation=True,
                             overrides=SimulationOverrides(fixed_boundaries=[
                                 {'selector': 'face.xmin', 'temperature_k': 7},
                                 {'selector': 'face.xmin', 'temperature_k': 4},
                             ]))
print(json.dumps({'data_dir': str(data_dir), 'study_id': study.study_id,
                  'workpiece_id': part.workpiece_id, 'project_id': study.project_id}))
