"""Local-only final Transformer advisor over an authenticated internal GPU API."""
import base64
import io
import json
import math
from pathlib import Path

import httpx

from services.api.config import settings


def identity():
    cfg = settings()
    if not cfg.research_transformer_path:
        return None
    if cfg.app_env != 'local':
        raise ValueError('RESEARCH_TRANSFORMER_LOCAL_ONLY')
    from ml.inference.research_pipeline import runtime_identity
    return runtime_identity(cfg.research_transformer_path,cfg.research_vision_policy_path)


def encode(image):
    preview = image.copy()
    preview.thumbnail((1024,1024))
    output = io.BytesIO()
    preview.convert('RGB').save(output,format='JPEG',quality=94)
    value = base64.b64encode(output.getvalue()).decode('ascii')
    if len(value)>2_000_000:
        raise ValueError('RESEARCH_IMAGE_TOO_LARGE')
    return value


def validate_result(result, expected, query_id, reference_ids, region):
    if (result.get('mode') != 'SHADOW_ONLY' or result.get('changes_product_decision') is not False
            or result.get('status') not in ('EVALUATED','ABSTAIN')
            or result.get('query_asset_id') != query_id or result.get('query_region') != region
            or any(result.get(k) != v for k,v in expected.items())
            or result.get('reference_asset_id') not in {None,*reference_ids}):
        raise ValueError('RESEARCH_RESPONSE_IDENTITY_MISMATCH')
    polygons = result.get('query_polygons')
    if not isinstance(polygons,list) or len(polygons)>12:
        raise ValueError('RESEARCH_POLYGONS_INVALID')
    for polygon in polygons:
        if not isinstance(polygon,list) or not 3<=len(polygon)<=100:
            raise ValueError('RESEARCH_POLYGONS_INVALID')
        for point in polygon:
            if len(point)!=2 or any(type(v) is not int or not 0<=v<=10000 for v in point):
                raise ValueError('RESEARCH_POLYGONS_INVALID')
    if result['status']=='ABSTAIN' and polygons:
        raise ValueError('ABSTENTION_CANNOT_HAVE_PREDICTIONS')
    if result['status']=='EVALUATED':
        if (result.get('reference_asset_id') is None or result.get('gate',{}).get('accepted') is not True
                or not isinstance(result.get('changed_fraction'),(int,float))
                or not math.isfinite(result['changed_fraction']) or not 0<=result['changed_fraction']<=1):
            raise ValueError('RESEARCH_OUTPUT_INVALID')
    return result


def evaluate(query_id, query, references, region, expected):
    if identity()!=expected:
        raise ValueError('RESEARCH_RUNTIME_IDENTITY_CHANGED')
    refs = []
    for asset_id,image in references:
        if len(refs)>=8:
            return {'mode':'SHADOW_ONLY',**expected,'changes_product_decision':False,'status':'ABSTAIN',
                    'query_asset_id':query_id,'query_region':region,'reference_asset_id':None,'query_polygons':[],
                    'reason_codes':['REFERENCE_GALLERY_LIMIT'],
                    'instruction':'Chọn một ảnh trước khi gửi hoặc dùng bộ tối đa 8 ảnh đúng mặt kiện.'}
        refs.append({'asset_id':asset_id,'image':encode(image)})
    if not refs:
        raise ValueError('RESEARCH_REFERENCES_REQUIRED')
    cfg = settings()
    token = Path(cfg.research_vision_token_file).read_text().strip()
    if len(token)<32:
        raise ValueError('RESEARCH_TOKEN_MISSING')
    with httpx.stream('POST',cfg.research_vision_url+'/v1/evaluate',
        headers={'X-Vision-Token':token},json={'query':{'asset_id':query_id,'image':encode(query)},
            'references':refs,'region':region,'expected_identity':expected},
            timeout=httpx.Timeout(90,connect=5),follow_redirects=False) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body)>500_000:
                raise ValueError('RESEARCH_RESPONSE_TOO_LARGE')
    return validate_result(json.loads(body),expected,query_id,[r['asset_id'] for r in refs],region)
