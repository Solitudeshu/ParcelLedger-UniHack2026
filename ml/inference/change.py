"""Verified synthetic change model. Never adjudicates a shipment or changes its report."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


def bundle_identity(folder):
    """Metadata verification also runs in the API without importing torch."""
    folder = Path(folder)
    card_bytes = (folder / "model-card.json").read_bytes()
    card = json.loads(card_bytes)
    if card.get("model_version") != "synthetic-change-head-v1" or card.get("source_kind") != "SYNTHETIC":
        raise ValueError("CHANGE_MODEL_VERSION_INVALID")
    threshold = card.get("threshold")
    if not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise ValueError("CHANGE_THRESHOLD_INVALID")
    weights = folder / "change-head.pt"
    if weights.stat().st_size > 2_000_000:
        raise ValueError("CHANGE_MODEL_SIZE_INVALID")
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    if digest != card.get("checkpoint_sha256"):
        raise ValueError("MODEL_CHECKSUM_MISMATCH")
    return {"checkpoint_sha256": digest, "model_card_sha256": hashlib.sha256(card_bytes).hexdigest()}


def aligned_features(pair):
    """Same channel order/normalization as training; geometric validity only.

    Training has oracle ignore masks for simulated occluders. Runtime cannot
    detect all occluders, and explicitly records that domain gap in the output.
    """
    query, reference, homography = pair["q"], pair["r"], pair["H"]
    warped = cv2.warpPerspective(reference, np.linalg.inv(homography), (query.shape[1], query.shape[0]))
    valid = cv2.erode(pair["valid_mask"] & pair["mask"], np.ones((11, 11), np.uint8)) > 0
    q = cv2.resize(query, (128, 128), interpolation=cv2.INTER_AREA).astype(np.float32)
    r = cv2.resize(warped, (128, 128), interpolation=cv2.INTER_AREA).astype(np.float32)
    # Every source pixel must be valid; do not include boundary mixtures.
    valid = cv2.resize(valid.astype(np.float32), (128, 128), interpolation=cv2.INTER_AREA) > .999
    valid[:3] = valid[-3:] = False
    valid[:, :3] = valid[:, -3:] = False
    if valid.sum() < 256:
        raise ValueError("CHANGE_INSUFFICIENT_VALID_AREA")
    for channel in range(3):
        rq = np.percentile(r[:, :, channel][valid], [20, 50, 80])
        qq = np.percentile(q[:, :, channel][valid], [20, 50, 80])
        gain = np.clip((qq[2]-qq[0])/max(rq[2]-rq[0], 10), .5, 2)
        r[:, :, channel] = r[:, :, channel]*gain+qq[1]-rq[1]*gain
    q, r = q/255, np.clip(r, 0, 255)/255
    features = np.concatenate([q, r, np.abs(q-r), valid[:, :, None]], axis=2).transpose(2, 0, 1)
    return features.astype(np.float32), valid


class ChangePredictor:
    def __init__(self, folder):
        import torch
        from ml.train.change_head import ChangeHead

        self.folder = Path(folder)
        self.identity = bundle_identity(self.folder)
        self.card = json.loads((self.folder / "model-card.json").read_text(encoding="utf-8"))
        self.model = ChangeHead().cpu().eval()
        state = torch.load(self.folder / "change-head.pt", map_location="cpu", weights_only=True)
        expected = self.model.state_dict()
        if not isinstance(state, dict) or set(state) != set(expected):
            raise ValueError("CHANGE_MODEL_KEYS_INVALID")
        for key, value in state.items():
            if not isinstance(value, torch.Tensor) or value.shape != expected[key].shape or not torch.isfinite(value).all():
                raise ValueError("CHANGE_MODEL_WEIGHTS_INVALID")
        self.model.load_state_dict(state, strict=True)
        self.parity()

    def logits(self, features):
        import torch
        x = np.asarray(features, dtype=np.float32)
        if x.ndim != 4 or x.shape[1:] != (10, 128, 128) or not 1 <= len(x) <= 16 or not np.isfinite(x).all():
            raise ValueError("CHANGE_INPUT_INVALID")
        with torch.inference_mode():
            return self.model(torch.from_numpy(x)).cpu().numpy()

    def parity(self):
        with np.load(self.folder / "export-parity.npz", allow_pickle=False) as probes:
            actual = self.logits(probes["features"])
            np.testing.assert_allclose(actual, probes["logits"], atol=3e-5, rtol=3e-5)
            error = float(np.max(np.abs(actual-probes["logits"])))
        return {"status": "PASS", **self.identity, "maximum_absolute_error": error, "atol": 3e-5, "rtol": 3e-5}

    def compare(self, query, reference, match):
        from services.worker.baseline import match_pair, normalized_polygon
        base = {"mode": "SHADOW_ONLY", "model_version": self.card["model_version"], **self.identity,
                "validation_scope": "SYNTHETIC_ONLY", "changes_product_decision": False,
                "query_asset_id": match["query_asset_id"], "reference_asset_id": match["reference_asset_id"],
                "validity_mask_kind": "GEOMETRIC_ONLY_NO_OCCLUSION_DETECTOR", "query_polygons": [],
                "limitations": self.card["limitations"] + ["128-pixel inference can miss small scratches."]}
        if match["status"] != "MATCHED_SUFFICIENT" or reference is None:
            return {**base, "status": "ABSTAIN", "reason": "INSUFFICIENT_EVIDENCE"}
        pair = match_pair(query, reference, match.get("query_region"))
        if not pair["valid"]:
            return {**base, "status": "ABSTAIN", "reason": "GEOMETRY_NOT_REPRODUCED"}
        try:
            features, valid = aligned_features(pair)
        except ValueError:
            return {**base, "status": "ABSTAIN", "reason": "INSUFFICIENT_VALID_AREA"}
        logits = self.logits(features[None])[0, 0]
        probability = 1/(1+np.exp(-np.clip(logits, -50, 50)))
        mask = ((probability >= self.card["threshold"]) & valid).astype(np.uint8)
        count, components, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        polygons = []
        for label in sorted(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_AREA], reverse=True)[:8]:
            if stats[label, cv2.CC_STAT_AREA] < 6:
                continue
            contours, _ = cv2.findContours((components == label).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            contour = max(contours, key=cv2.contourArea)
            polygon = cv2.approxPolyDP(contour, .01*cv2.arcLength(contour, True), True)
            if len(polygon) < 3:
                x, y, w, h = cv2.boundingRect(contour)
                polygon = np.array([[x, y], [x+w-1, y], [x+w-1, y+h-1], [x, y+h-1]])
            polygons.append(normalized_polygon(polygon, 128, 128))
        return {**base, "status": "EVALUATED", "threshold": self.card["threshold"],
                "valid_pixel_count": int(valid.sum()), "changed_pixel_count": int(mask.sum()),
                "changed_fraction": float(mask.sum()/valid.sum()), "query_polygons": polygons}
