"""Final trained candidate for local research; no primary report/settlement authority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bundle_identity(folder):
    folder = Path(folder)
    card = json.loads((folder/'model-card.json').read_bytes())
    if (card.get('model_version') not in ('parcel-transformer-c2-final-v1', 'parcel-transformer-c3-final-v1')
            or card.get('training_epochs_completed') != 30 or card.get('mode') != 'SHADOW_ONLY'):
        raise ValueError('TRANSFORMER_BUNDLE_INVALID')
    checkpoint = folder/'model.pt'
    if checkpoint.stat().st_size > 25_000_000 or file_sha(checkpoint) != card['checkpoint_sha256']:
        raise ValueError('TRANSFORMER_CHECKPOINT_MISMATCH')
    for name, digest in card['identity']['source_hashes'].items():
        if file_sha(name) != digest:
            raise ValueError('TRANSFORMER_TRAINING_SOURCE_CHANGED')
    return {'model_version': card['model_version'], 'checkpoint_sha256': card['checkpoint_sha256'],
            'model_card_sha256': file_sha(folder/'model-card.json')}


def tile_windows(width, height, size=518, overlap=96):
    if min(width, height, size) < 1 or not 0 <= overlap < size:
        raise ValueError('INVALID_TILE_GEOMETRY')
    def starts(length):
        if length <= size:
            return [0]
        return sorted(set([*range(0, length-size+1, size-overlap), length-size]))
    return [(x, y, min(width, x+size), min(height, y+size)) for y in starts(height) for x in starts(width)]


def normalized_polygons(probability, valid, threshold=.5):
    mask = ((probability >= threshold) & (valid > 0)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    h, w = mask.shape
    polygons = []
    for label in sorted(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_AREA], reverse=True)[:12]:
        if stats[label, cv2.CC_STAT_AREA] < 16:
            continue
        contours, _ = cv2.findContours((labels == label).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contour = max(contours, key=cv2.contourArea)
        polygon = cv2.approxPolyDP(contour, max(1., .005*cv2.arcLength(contour, True)), True).reshape(-1, 2)
        if len(polygon) > 100:
            x, y, bw, bh = cv2.boundingRect(contour)
            polygon = np.array([[x,y],[x+bw-1,y],[x+bw-1,y+bh-1],[x,y+bh-1]])
        if len(polygon) < 3:
            x, y, bw, bh = cv2.boundingRect(contour)
            polygon = np.array([[x,y],[x+bw-1,y],[x+bw-1,y+bh-1],[x,y+bh-1]])
        polygons.append(np.clip(np.rint(polygon/[max(w-1,1),max(h-1,1)]*10000),0,10000).astype(int).tolist())
    return polygons, int(mask.sum())


class TransformerPredictor:
    def __init__(self, folder, cache='/models/dino'):
        import torch
        from ml.models.paired_transformer import DinoSpatialEncoder, PairedChangeTransformer
        from ml.train.paired_transformer import load_trainable

        if not torch.cuda.is_available():
            raise ValueError('TRANSFORMER_GPU_REQUIRED')
        self.torch = torch
        self.identity = bundle_identity(folder)
        self.card = json.loads((Path(folder)/'model-card.json').read_bytes())
        saved = torch.load(Path(folder)/'model.pt', map_location='cpu', weights_only=True)
        if saved['identity'] != self.card['identity']:
            raise ValueError('TRANSFORMER_IDENTITY_MISMATCH')
        config = saved['identity']['config']
        torch.set_num_threads(2)
        self.encoder = DinoSpatialEncoder(config['encoder'], config['lora_blocks'], cache).cuda().eval()
        if file_sha(Path(cache)/'checkpoints'/f"{config['encoder']}_pretrain.pth") != saved['identity']['backbone_sha256']:
            raise ValueError('TRANSFORMER_BACKBONE_MISMATCH')
        self.decoder = PairedChangeTransformer(self.encoder.channels, config['width']).cuda().eval()
        load_trainable(saved['weights'], self.encoder, self.decoder)
        self.size = config['size']

    def predict_patch(self, query, aligned, valid):
        torch = self.torch
        images = [cv2.resize(rgb, (self.size,self.size), interpolation=cv2.INTER_AREA) for rgb in (query,aligned)]
        tensors = [torch.from_numpy(np.ascontiguousarray(rgb.transpose(2,0,1))).float()[None].cuda()/255 for rgb in images]
        observed = cv2.resize(valid.astype(np.float32), (self.size,self.size), interpolation=cv2.INTER_AREA) > .999
        mask = torch.from_numpy(observed.astype(np.float32))[None,None].cuda()
        with torch.inference_mode(), torch.autocast('cuda', dtype=torch.float16):
            features = self.encoder(torch.cat(tensors))
            logits = self.decoder([x[:1] for x in features], [x[1:] for x in features], *tensors, mask)
        probability = logits.float().sigmoid()[0,0].cpu().numpy()
        if not np.isfinite(probability).all():
            raise ValueError('NONFINITE_TRANSFORMER_OUTPUT')
        return cv2.resize(probability, (query.shape[1],query.shape[0]), interpolation=cv2.INTER_LINEAR)

    def compare_aligned(self, query, aligned, valid):
        if (query.ndim != 3 or query.shape[-1] != 3 or aligned.shape != query.shape
                or valid.shape != query.shape[:2] or not np.isfinite(valid).all()
                or not np.isin(valid, [0, 1]).all()):
            raise ValueError('INVALID_ALIGNED_INPUTS')
        h,w = query.shape[:2]
        probabilities, counts = np.zeros((h,w),np.float32), np.zeros((h,w),np.float32)
        windows = tile_windows(w,h)
        if len(windows) > 9:
            raise ValueError('TRANSFORMER_TILE_BUDGET_EXCEEDED')
        processed = 0
        for x0,y0,x1,y1 in windows:
            observed = valid[y0:y1,x0:x1]
            if observed.sum() < 256:
                continue
            p = self.predict_patch(query[y0:y1,x0:x1], aligned[y0:y1,x0:x1], observed)
            probabilities[y0:y1,x0:x1] += p * observed
            counts[y0:y1,x0:x1] += observed
            processed += 1
        observed = (counts > 0) & (valid > 0)
        probabilities /= np.maximum(counts,1)
        polygons, changed = normalized_polygons(probabilities, observed)
        return {'query_polygons': polygons, 'changed_pixel_count': changed,
                'valid_pixel_count': int(observed.sum()), 'changed_fraction': changed/max(int(observed.sum()),1),
                'tiles_processed': processed, 'threshold': .5, 'threshold_status': 'DIAGNOSTIC_NOT_CALIBRATED',
                'inference_resolution': self.size, 'image_dimensions': [w,h]}, probabilities
