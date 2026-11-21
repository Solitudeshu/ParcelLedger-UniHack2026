import base64
import io
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ml.inference.transformer import TransformerPredictor, normalized_polygons, tile_windows
from services.vision import server
from services.worker.transformer_shadow import validate_result


def test_tiles_cover_nonsquare_images_and_average_overlap_without_coordinates_shifting():
    h, w = 673, 1024
    valid = np.ones((h, w), np.uint8)
    valid[:, :201] = 0
    predictor = object.__new__(TransformerPredictor)
    predictor.size = 392
    predictor.predict_patch = lambda q, r, v: q[:, :, 0].astype(np.float32)/255
    rgb = np.zeros((h, w, 3), np.uint8)
    rgb[300:340, 500:600, 0] = 255
    result, p = predictor.compare_aligned(rgb, rgb.copy(), valid)
    assert len(tile_windows(w, h)) <= 9
    assert result['valid_pixel_count'] == int(valid.sum())
    assert result['changed_pixel_count'] == 4000
    assert np.array_equal(p, rgb[:, :, 0]/255)
    polygon = np.asarray(result['query_polygons'][0])
    assert abs(polygon[:, 0].min()/10000*(w-1) - 500) < 1
    assert abs(polygon[:, 1].max()/10000*(h-1) - 339) < 1
    valid[:] = 0
    assert predictor.compare_aligned(rgb, rgb, valid)[0]['query_polygons'] == []
    with pytest.raises(ValueError, match='INVALID_ALIGNED_INPUTS'):
        predictor.compare_aligned(rgb, rgb, np.full((h, w), np.nan))


def test_polygons_never_mark_unobserved_pixels():
    p = np.ones((50, 90), np.float32)
    valid = np.zeros_like(p, np.uint8)
    valid[10:30, 40:65] = 1
    polygons, count = normalized_polygons(p, valid)
    assert count == 500 and len(polygons) == 1
    assert min(x for x, y in polygons[0]) >= 4000


def response(query, reference, identity):
    return dict(mode='SHADOW_ONLY', changes_product_decision=False, status='EVALUATED',
                query_asset_id=query, query_region=None, reference_asset_id=reference,
                query_polygons=[[[100,100],[200,100],[200,200]]], gate={'accepted':True},
                changed_fraction=.1, **identity)


@pytest.mark.parametrize('change', [
    {'changes_product_decision':True}, {'query_asset_id':'other'},
    {'reference_asset_id':'outside'}, {'query_region':[[1,2],[3,4],[5,6]]},
    {'checkpoint_sha256':'changed'}, {'changed_fraction':float('nan')},
    {'status':'ABSTAIN'}, {'query_polygons':[[[1,1],[2,2],[10001,3]]]},
])
def test_worker_rejects_unbound_or_invalid_outputs(change):
    identity = {'checkpoint_sha256':'a'*64}
    result = response('q','r',identity)
    assert validate_result(result,identity,'q',['r'],None) == result
    result.update(change)
    with pytest.raises(ValueError):
        validate_result(result,identity,'q',['r'],None)


@pytest.fixture
def gpu_service(monkeypatch, tmp_path):
    token = tmp_path/'token'
    token.write_text('t'*48)
    monkeypatch.setenv('RESEARCH_VISION_TOKEN_FILE', str(token))
    identity = {'checkpoint_sha256':'a'*64}
    def compare(q, image, refs, region):
        assert image.shape == (64,80,3)
        return response(q,refs[0][0],identity)
    monkeypatch.setattr(server,'pipeline',SimpleNamespace(identity=identity,compare=compare))
    # Deliberately no lifespan: the service boundary is tested without a GPU.
    client = TestClient(server.app)
    output = io.BytesIO()
    Image.new('RGB',(80,64),'white').save(output,format='JPEG')
    encoded = base64.b64encode(output.getvalue()).decode()
    payload = {'query':{'asset_id':str(uuid4()),'image':encoded},
               'references':[{'asset_id':str(uuid4()),'image':encoded}],
               'expected_identity':identity}
    return client, payload, {'X-Vision-Token':'t'*48}


def test_gpu_boundary_checks_token_identity_region_input_and_busy(gpu_service):
    client, payload, headers = gpu_service
    assert client.post('/v1/evaluate',json=payload).status_code == 401
    assert client.post('/v1/evaluate',json=payload,headers=headers).status_code == 200
    modified = deepcopy(payload)
    modified['expected_identity'] = {}
    assert client.post('/v1/evaluate',json=modified,headers=headers).status_code == 409
    modified = deepcopy(payload)
    modified['references'][0]['asset_id'] = modified['query']['asset_id']
    assert client.post('/v1/evaluate',json=modified,headers=headers).status_code == 422
    modified = deepcopy(payload)
    modified['query']['image'] = 'not an encoded image'
    assert client.post('/v1/evaluate',json=modified,headers=headers).status_code == 422
    assert client.post('/v1/evaluate',json={**payload,'region':[[0,0],[1,1]]},headers=headers).status_code == 422
    server.lock.acquire()
    try:
        assert client.post('/v1/evaluate',json=payload,headers=headers).status_code == 429
    finally:
        server.lock.release()


@pytest.mark.parametrize('failure_at', ['intake','worker'])
def test_optional_gpu_failure_preserves_primary_report_and_no_confirmation(clients, monkeypatch, failure_at):
    from conftest import post
    from test_workflow import ready_case
    from services.api.config import settings
    from services.worker import transformer_shadow
    from services.worker.runner import run_once
    sid, _, query = ready_case(clients)
    _, buyer, _ = clients
    monkeypatch.setattr(settings(),'research_transformer_path','configured-research-bundle')
    def fail(*args, **kwargs):
        raise ValueError('TEST_UNAVAILABLE')
    monkeypatch.setattr(transformer_shadow,'identity',fail if failure_at=='intake' else lambda: {'checkpoint_sha256':'a'*64})
    monkeypatch.setattr(transformer_shadow,'evaluate',fail)
    submitted = post(buyer,f'/v1/shipments/{sid}/analyses',{'query_asset_id':query})
    assert submitted.status_code == 202, submitted.text
    assert run_once()
    report = buyer.get(f'/v1/shipments/{sid}/reports').json()[0]
    assert report['document']['match']['model_version'].startswith('sift-')
    assert report['document']['observations']
    assert report['document']['research_transformer']['status'] == 'UNAVAILABLE'
    assert report['document']['research_transformer']['changes_product_decision'] is False
    assert report['confirmations'] == []
