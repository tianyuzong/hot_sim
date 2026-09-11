from copy import deepcopy
import trimesh
from fastapi.testclient import TestClient
from thermoflow.api import create_app
from thermoflow.settings import Settings
from tests.test_manual_workflow import UnavailablePlanner


def test_five_component_materials_persist_independently(tmp_path):
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / 'data',
                        cadflow_repo=tmp_path / 'missing', planner_mode='deterministic', compute_backend='cpu')
    shells=[]
    for i in range(5):
        shell=trimesh.creation.box(extents=[5,5,5]); shell.apply_translation([i*12,0,0]); shells.append(shell)
    with TestClient(create_app(settings, UnavailablePlanner())) as client:
        part=client.post('/v1/workpieces/files',files={'file':('five.stl',trimesh.util.concatenate(shells).export(file_type='stl'),'model/stl')}).json()
        wid=part['workpiece_id']
        assert len(part['components']) == 5
        assert client.post(f'/v1/workpieces/{wid}/unit',json={'unit':'mm'}).status_code==200
        study=client.post('/v1/studies',json={'workpiece_id':wid,'planning_mode':'manual'}).json()
        sid=study['study_id']; assignments=deepcopy(study['plan']['component_materials'])
        for i,assignment in enumerate(assignments):
            assignment['material_id']=None
            assignment['material'].update(name=f'Test fabric {i+1}',thermal_conductivity_w_m_k=.05+i*.02,
                density_kg_m3=200+i*100,specific_heat_j_kg_k=1000+i*200,source_type='user',
                source_basis='Synthetic independent-component regression values',source_reference=None,
                source_version=None,source_citation=None,valid_temperature_min_k=None,valid_temperature_max_k=None)
        saved=client.put(f'/v1/studies/{sid}/draft',json={'expected_revision':study['draft_revision'],'overrides':{'component_materials':assignments}})
        assert saved.status_code==200,saved.text
        reread=client.get(f'/v1/studies/{sid}').json()
        assert reread['plan']['component_materials']==assignments
        changed=deepcopy(assignments); changed[2]['material']['thermal_conductivity_w_m_k']=.19
        saved=client.put(f'/v1/studies/{sid}/draft',json={'expected_revision':reread['draft_revision'],'overrides':{'component_materials':changed}})
        assert saved.status_code==200,saved.text
    # A fresh application instance must read the same values from disk.
    with TestClient(create_app(settings, UnavailablePlanner())) as client:
        restored=client.get(f'/v1/studies/{sid}').json()['plan']
        assert restored['component_materials']==changed
        for i in [0,1,3,4]: assert restored['component_materials'][i]==assignments[i]
        assert restored['heat_source_enabled'] is False and restored['heat_sources']==[]
