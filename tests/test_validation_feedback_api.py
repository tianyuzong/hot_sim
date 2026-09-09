import trimesh
from fastapi.testclient import TestClient

from thermoflow.api import create_app
from thermoflow.settings import Settings


def test_validation_refresh_is_readonly_and_reports_saved_conflicts(tmp_path):
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / 'data',
                        cadflow_repo=tmp_path / 'missing', planner_mode='deterministic')
    with TestClient(create_app(settings)) as client:
        part = client.post('/v1/workpieces/files', files={
            'file': ('block.stl', trimesh.creation.box(extents=[20, 12, 8]).export(file_type='stl'), 'model/stl'),
        }).json()
        client.post(f"/v1/workpieces/{part['workpiece_id']}/unit", json={'unit': 'mm'})
        study = client.post('/v1/studies', json={
            'workpiece_id': part['workpiece_id'], 'planning_mode': 'manual',
            'overrides': {'fixed_boundaries': [
                {'selector': 'face.xmin', 'temperature_k': 7},
                {'selector': 'face.xmin', 'temperature_k': 4},
            ]},
        }).json()
        sid = study['study_id']
        before = client.get(f'/v1/studies/{sid}').json()
        response = client.get(f'/v1/studies/{sid}/validation')
        assert response.status_code == 200, response.text
        assert response.json()['accepted'] is False
        assert response.json()['errors']
        assert client.get(f'/v1/studies/{sid}').json() == before
        assert client.get(f'/v1/studies/{sid}/validation', params={
            'expected_revision': before['draft_revision'],
        }).status_code == 200
        stale = client.get(f'/v1/studies/{sid}/validation', params={
            'expected_revision': before['draft_revision'] - 1,
        })
        assert stale.status_code == 409
        assert str(before['draft_revision']) in stale.json()['detail']
        assert client.get(f'/v1/studies/{sid}').json() == before
        assert client.get('/v1/studies/study-missing/validation').status_code == 404
