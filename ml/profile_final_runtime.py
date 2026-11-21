"""Procedural final-bundle parity and runtime smoke, never a quality benchmark."""
import gc
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch

from ml.inference.research_pipeline import ResearchPipeline
from ml.train.paired_transformer import load_trainable


def main():
    output = Path('/runoutputs')
    output.mkdir(parents=True, exist_ok=True)
    report = {'scope':'FINAL_RUNTIME_PROCEDURAL_SMOKE', 'promotion':'HOLD',
              'real_box_validation':'NOT_RUN', 'candidates':{},
              'gpu':torch.cuda.get_device_name(), 'torch':torch.__version__}
    def save():
        (output/'profile.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    reference = cv2.cvtColor(cv2.imread('tests/fixtures/images/dispatch.png'),cv2.COLOR_BGR2RGB)
    changed = cv2.cvtColor(cv2.imread('tests/fixtures/images/changed.png'),cv2.COLOR_BGR2RGB)
    wrong = np.random.default_rng(812).integers(0,256,reference.shape,dtype=np.uint8)
    for candidate in ('c2','c3'):
        before = time.perf_counter()
        pipeline = ResearchPipeline(f'artifacts/models/candidates/transformer-v2/{candidate}-final-v1',
                                    'artifacts/models/candidates/transformer-v2/correspondence-policy.json')
        row = {'identity':pipeline.identity,'load_seconds':time.perf_counter()-before,'cases':{}}
        report['candidates'][candidate] = row
        predictor = pipeline.decoder
        size = predictor.size
        q, r = [cv2.resize(x,(size,size),interpolation=cv2.INTER_AREA) for x in (changed,reference)]
        valid = np.ones((size,size),np.uint8)
        first = predictor.predict_patch(q,r,valid)
        # Independently rehydrate the original selected training checkpoint after perturbation.
        original = torch.load(f'artifacts/kaggle/transformer-{candidate}-s04-v1/training/best.pt',
                              map_location='cpu',weights_only=True)
        with torch.no_grad():
            for parameter in predictor.decoder.parameters():
                parameter.add_(.1)
            for parameter in predictor.encoder.parameters():
                if parameter.requires_grad:
                    parameter.add_(.1)
        load_trainable(original['weights'],predictor.encoder,predictor.decoder)
        tensors = [torch.from_numpy(np.ascontiguousarray(x.transpose(2,0,1))).float()[None].cuda()/255 for x in (q,r)]
        observed = torch.ones((1,1,size,size),device='cuda')
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):
            features = predictor.encoder(torch.cat(tensors))
            logits = predictor.decoder([x[:1] for x in features],[x[1:] for x in features],*tensors,observed)
        direct = logits.float().sigmoid()[0,0].cpu().numpy()
        row['final_export_reload_max_abs_error'] = float(np.abs(first-direct).max())
        assert row['final_export_reload_max_abs_error'] == 0
        del original, tensors, observed, features, logits
        torch.cuda.reset_peak_memory_stats()
        for name, query in [('unchanged',reference),('changed',changed),('wrong_texture',wrong)]:
            torch.cuda.synchronize()
            before = time.perf_counter()
            result = pipeline.compare('query',query,[('wrong',wrong),('reference',reference)])
            torch.cuda.synchronize()
            result['wall_seconds'] = time.perf_counter()-before
            row['cases'][name] = result
            if name != 'wrong_texture':
                assert result['status'] == 'EVALUATED' and result['reference_asset_id'] == 'reference', (name,result)
            # wrong_texture is also present in this gallery: this is a positive identity case.
            save()
        result = pipeline.compare('query',wrong,[('reference',reference)])
        row['cases']['no_matching_reference'] = result
        assert result['status'] == 'ABSTAIN', result
        roi = [[4000,3500],[8500,3500],[8500,8500],[4000,8500]]
        row['cases']['roi'] = pipeline.compare('query',changed,[('reference',reference)],roi)
        row['peak_allocated_bytes'] = torch.cuda.max_memory_allocated()
        row['status'] = 'PASS'
        save()
        del pipeline, predictor
        gc.collect()
        torch.cuda.empty_cache()
    report['status'] = 'PASS'
    save()
    print(json.dumps({'status':'PASS','report':str(output/'profile.json')}))


if __name__ == '__main__':
    main()
