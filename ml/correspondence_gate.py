"""Pixel agreement after a rigid planar fit; never an identity or liability proof."""
from __future__ import annotations

import cv2
import numpy as np

VERSION = 'correspondence-gate-v1'
POLICY = {'version': VERSION, 'min_inliers': 24, 'min_inlier_ratio': .35,
          'min_coverage': .20, 'min_valid_fraction': .20, 'min_texture_tiles': 6,
          'min_median_ncc': .65, 'min_agreeing_fraction': .60,
          'tile_ncc': .60, 'tile_size': 32, 'tile_min_std': 6.,
          'calibration': 'NOT_RUN', 'scope': 'RESEARCH_ONLY'}


def extract_evidence(reference, query, rp, qp, fit, region=None):
    """Use only observed pixels and correspondences, never target/visibility labels."""
    h, w = query.shape[:2]
    empty = np.zeros((h, w), np.uint8)
    features = {'inliers': 0, 'inlier_ratio': 0., 'coverage': 0., 'valid_fraction': 0.,
                'texture_tiles': 0, 'median_ncc': -1., 'agreeing_fraction': 0.,
                'query_sharpness': 0., 'rank_score': 0.}
    H = fit.get('homography')
    if H is None or H.shape != (3, 3) or not np.isfinite(H).all():
        return features, None, empty
    rp, qp = np.asarray(rp, np.float32), np.asarray(qp, np.float32)
    if rp.shape != qp.shape or rp.ndim != 2 or rp.shape[1] != 2 or len(rp) < 4:
        return features, None, empty
    if not np.isfinite(rp).all() or not np.isfinite(qp).all():
        return features, None, empty
    rh, rw = reference.shape[:2]
    inside = ((rp[:, 0] >= 0) & (rp[:, 0] < rw) & (rp[:, 1] >= 0) & (rp[:, 1] < rh)
              & (qp[:, 0] >= 0) & (qp[:, 0] < w) & (qp[:, 1] >= 0) & (qp[:, 1] < h))
    mapped = cv2.perspectiveTransform(rp[:, None], H)[:, 0]
    known = inside & np.isfinite(mapped).all(axis=1) & (np.linalg.norm(mapped - qp, axis=1) <= 4.)
    if known.sum() < 4:
        return features, None, empty
    roi = np.ones((h, w), np.uint8)
    if region is not None:
        points = np.asarray(region, np.float64)
        if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
            raise ValueError('INVALID_QUERY_REGION')
        if (points < 0).any() or (points > 10000).any():
            raise ValueError('INVALID_QUERY_REGION')
        roi[:] = 0
        cv2.fillPoly(roi, [np.rint(points / 10000 * [w-1, h-1]).astype(np.int32)], 1)
    if roi.sum() < 256:
        return features, None, empty
    hull = np.zeros_like(roi)
    cv2.fillConvexPoly(hull, cv2.convexHull(qp[known]).round().astype(np.int32), 1)
    overlap = cv2.warpPerspective(np.ones((rh, rw), np.uint8), H, (w, h), flags=cv2.INTER_NEAREST)
    valid = cv2.erode((overlap & hull & roi), np.ones((7, 7), np.uint8))
    aligned = cv2.warpPerspective(reference, H, (w, h))
    qgray, rgray = [cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32) for img in (query, aligned)]
    features.update(inliers=int(known.sum()), inlier_ratio=float(known.mean()),
                    coverage=float(hull[roi > 0].mean()), valid_fraction=float(valid[roi > 0].mean()),
                    query_sharpness=float(cv2.Laplacian(qgray, cv2.CV_32F)[roi > 0].var()))
    qgray, rgray = [cv2.GaussianBlur(img, (5, 5), 1.) for img in (qgray, rgray)]
    correlations = []
    tile = POLICY['tile_size']
    for y in range(0, h-tile+1, tile):
        for x in range(0, w-tile+1, tile):
            mask = valid[y:y+tile, x:x+tile] > 0
            if mask.mean() < .8:
                continue
            q, r = [img[y:y+tile, x:x+tile][mask].astype(np.float64) for img in (qgray, rgray)]
            if min(q.std(), r.std()) < POLICY['tile_min_std']:
                continue
            q, r = q-q.mean(), r-r.mean()
            ncc = float(np.clip(np.dot(q, r) / (np.linalg.norm(q) * np.linalg.norm(r)), -1, 1))
            correlations.append(ncc)
    if correlations:
        features.update(texture_tiles=len(correlations), median_ncc=float(np.median(correlations)),
                        agreeing_fraction=float(np.mean(np.asarray(correlations) >= POLICY['tile_ncc'])))
        features['rank_score'] = max(0., features['median_ncc']) * np.sqrt(features['coverage']) * features['inlier_ratio']
    return features, aligned, valid


def decide(features, policy=None):
    policy = POLICY if policy is None else policy
    comparisons = [('inliers', 'min_inliers', 'TOO_FEW_CORRESPONDENCES'),
                   ('inlier_ratio', 'min_inlier_ratio', 'INCONSISTENT_GEOMETRY'),
                   ('coverage', 'min_coverage', 'INSUFFICIENT_OVERLAP'),
                   ('valid_fraction', 'min_valid_fraction', 'INSUFFICIENT_OVERLAP'),
                   ('texture_tiles', 'min_texture_tiles', 'LOW_TEXTURE'),
                   ('median_ncc', 'min_median_ncc', 'APPEARANCE_MISMATCH'),
                   ('agreeing_fraction', 'min_agreeing_fraction', 'APPEARANCE_MISMATCH')]
    reasons = []
    for feature, threshold, reason in comparisons:
        value = features.get(feature)
        if value is None or not np.isfinite(value) or value < policy[threshold]:
            if reason not in reasons:
                reasons.append(reason)
    return {'accepted': not reasons, 'reason_codes': reasons, 'policy_version': policy['version'],
            'calibration': policy['calibration'], 'scope': 'RESEARCH_ONLY', 'proves_same_physical_box': False}
