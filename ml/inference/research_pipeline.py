"""RoMa + appearance verification + final decoder, always local shadow output."""
import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np

from ml.correspondence_gate import POLICY, decide, extract_evidence
from ml.inference.transformer import TransformerPredictor, bundle_identity
from ml.learned_matching import fit_alignment
from ml.roma_matching import RomaMatcher

RUNTIME_SOURCES = ('ml/correspondence_gate.py','ml/inference/transformer.py',
                   'ml/inference/research_pipeline.py','ml/learned_matching.py','ml/roma_matching.py',
                   'services/worker/transformer_shadow.py','services/vision/server.py',
                   'ml/features/geometry.py')


def runtime_identity(folder, policy_path):
    identity = bundle_identity(folder)
    raw = Path(policy_path).read_bytes()
    policy = json.loads(raw)
    if policy.get('scope') != 'RESEARCH_ONLY' or policy.get('version') != 'correspondence-gate-v1':
        raise ValueError('RESEARCH_POLICY_INVALID')
    # Extraction constants are frozen in the submitted research notebook.
    if any(policy.get(k) != POLICY[k] for k in ('tile_size','tile_min_std','tile_ncc')):
        raise ValueError('RESEARCH_FEATURE_CONFIGURATION_CHANGED')
    sources = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in RUNTIME_SOURCES}
    return {**identity, 'gate_policy_sha256': hashlib.sha256(raw).hexdigest(),
            'runtime_source_sha256': hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest()}


def scaled(rgb, size=518):
    h,w = rgb.shape[:2]
    scale = min(1.,size/max(h,w))
    resized = cv2.resize(rgb,(max(1,round(w*scale)),max(1,round(h*scale))),interpolation=cv2.INTER_AREA)
    return resized, np.diag([resized.shape[1]/w,resized.shape[0]/h,1.])


class ResearchPipeline:
    def __init__(self, folder, policy_path, cache='/models'):
        self.folder, self.policy_path = folder, policy_path
        self.identity = runtime_identity(folder,policy_path)
        self.policy = json.loads(Path(policy_path).read_bytes())
        self.matcher = RomaMatcher('fast',str(Path(cache)/'roma'))
        self.decoder = TransformerPredictor(folder,str(Path(cache)/'dino'))

    def compare(self, query_id, query, references, region=None):
        if runtime_identity(self.folder,self.policy_path) != self.identity:
            raise ValueError('RESEARCH_RUNTIME_CHANGED_RESTART_REQUIRED')
        before = time.perf_counter()
        base = {'mode':'SHADOW_ONLY', **self.identity, 'changes_product_decision':False,
                'validation_scope':'SYNTHETIC_ONLY', 'query_asset_id':query_id,
                'query_region':region, 'query_polygons':[], 'reference_asset_id':None,
                'validity_mask_kind':'INFERRED_GEOMETRIC_SUPPORT_NO_OCCLUSION_LABELS',
                'limitations':['Chưa kiểm chứng trên kiện hàng vật lý.',
                    'Vùng gợi ý có thể do ánh sáng, vật che hoặc căn chỉnh; cần người dùng kiểm tra.',
                    'Không xác định nguyên nhân, trách nhiệm hay số tiền bồi hoàn.']}
        if not 1 <= len(references) <= 8:
            return {**base,'status':'ABSTAIN','reason_codes':['REFERENCE_GALLERY_LIMIT'],
                    'instruction':'Chọn một ảnh trước khi gửi hoặc dùng bộ tối đa 8 ảnh đúng mặt kiện.'}
        q, sq = scaled(query)
        candidates = []
        for asset_id, rgb in references:
            r, sr = scaled(rgb)
            rp, qp, _ = self.matcher.match(r,q)
            fit = fit_alignment(rp,qp,q.shape)
            features, _, valid = extract_evidence(r,q,rp,qp,fit,region)
            decision = decide(features,self.policy)
            candidates.append({'asset_id':asset_id,'features':features,'decision':decision,
                               '_reference':rgb,'_H':fit['homography'],'_valid':valid,'_sr':sr})
        candidates.sort(key=lambda x:(-x['features']['rank_score'],x['asset_id']))
        public = [{k:v for k,v in row.items() if not k.startswith('_')} for row in candidates]
        accepted = [c for c in candidates if c['decision']['accepted']]
        if not accepted:
            return {**base,'status':'ABSTAIN','reason_codes':candidates[0]['decision']['reason_codes'],
                    'candidates':public,'seconds':time.perf_counter()-before,
                    'instruction':'Chụp thêm ảnh bao quát đúng mặt hộp, thấy nhãn hoặc băng keo; sau đó chụp cận cảnh vùng nghi vấn.'}
        chosen = accepted[0]
        H = np.linalg.inv(sq) @ chosen['_H'] @ chosen['_sr']
        h,w = query.shape[:2]
        aligned = cv2.warpPerspective(chosen['_reference'],H,(w,h))
        valid = cv2.resize(chosen['_valid'],(w,h),interpolation=cv2.INTER_NEAREST)
        if region is not None:
            roi = np.zeros((h,w), np.uint8)
            points = np.rint(np.asarray(region)/10000*[w-1,h-1]).astype(np.int32)
            cv2.fillPoly(roi,[points],1)
            valid &= roi
        result, _ = self.decoder.compare_aligned(query,aligned,valid)
        if result['valid_pixel_count'] == 0:
            return {**base,'status':'ABSTAIN','reason_codes':['INSUFFICIENT_OVERLAP'],
                    'candidates':public,'instruction':'Chụp rộng hơn vùng cần kiểm tra.'}
        return {**base,**result,'status':'EVALUATED','reference_asset_id':chosen['asset_id'],
                'gate':chosen['decision'],'candidates':public,'seconds':time.perf_counter()-before}
