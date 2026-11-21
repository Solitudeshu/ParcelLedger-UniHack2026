"""Pickle-free NumPy deployment of the trained heads, with verified artifacts.

The synthetic checkpoint is a shadow advisor until real-box evaluation passes.
Its calibration must never be described as a real-world correctness probability.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ml.features.geometry import describe, scalar_features
from ml.features.pair_features import FEATURE_VERSION, ScalarNormalizer, pair_features

SHAPES = {"shared.0.weight": (256, 1560), "shared.0.bias": (256,),
          "shared.3.weight": (64, 256), "shared.3.bias": (64,),
          **{f"{head}.weight": (1, 64) for head in ("ranking", "same_region", "sufficiency")},
          **{f"{head}.bias": (1,) for head in ("ranking", "same_region", "sufficiency")}}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gelu(values):
    # PyTorch nn.GELU defaults to exact erf, not the tanh approximation.
    erf = np.fromiter((math.erf(float(x)) for x in (values / math.sqrt(2)).flat),
                      dtype=np.float32, count=values.size).reshape(values.shape)
    return .5 * values * (1 + erf)


class NumpyRanker:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.card = json.loads((self.folder / "model-card.json").read_text(encoding="utf-8"))
        if self.card.get("feature_version") != FEATURE_VERSION:
            raise ValueError("FEATURE_VERSION_MISMATCH")
        for filename, key in [("ranker.npz", "checkpoint_sha256"), ("normalizer.json", "normalizer_sha256")]:
            path = self.folder / filename
            if path.stat().st_size > 16_000_000 or digest(path) != self.card[key]:
                raise ValueError("MODEL_CHECKSUM_MISMATCH")
        with np.load(self.folder / "ranker.npz", allow_pickle=False) as artifact:
            if set(artifact.files) != set(SHAPES):
                raise ValueError("MODEL_KEYS_MISMATCH")
            self.weights = {key: artifact[key].astype(np.float32) for key in SHAPES}
        for key, shape in SHAPES.items():
            if self.weights[key].shape != shape or not np.isfinite(self.weights[key]).all():
                raise ValueError("MODEL_WEIGHT_INVALID")
        state = json.loads((self.folder / "normalizer.json").read_text(encoding="utf-8"))
        if state.get("fit_split") != "train" or state.get("version") != FEATURE_VERSION:
            raise ValueError("NORMALIZER_LINEAGE_INVALID")
        self.normalizer = ScalarNormalizer(state["mean"], state["scale"])
        self.temperatures = np.asarray(self.card["temperatures"], dtype=np.float32)
        if self.temperatures.shape != (2,) or not np.isfinite(self.temperatures).all() or (self.temperatures <= 0).any():
            raise ValueError("CALIBRATION_INVALID")
        if not 0 <= self.card["threshold"] <= 1:
            raise ValueError("CALIBRATION_INVALID")

    def predict(self, features):
        x = np.asarray(features, dtype=np.float32)
        if x.ndim != 2 or x.shape[1] != 1560 or not np.isfinite(x).all():
            raise ValueError("RANKER_INPUT_INVALID")
        for prefix in ("shared.0", "shared.3"):
            x = gelu(x @ self.weights[prefix+".weight"].T + self.weights[prefix+".bias"])
        return {head: (x @ self.weights[head+".weight"].T + self.weights[head+".bias"]).ravel()
                for head in ("ranking", "same_region", "sufficiency")}

    def parity(self, atol=2e-5, rtol=2e-5):
        with np.load(self.folder / "export-parity.npz", allow_pickle=False) as probes:
            actual = self.predict(probes["features"])
            errors = {}
            for key, output in actual.items():
                np.testing.assert_allclose(output, probes[key], atol=atol, rtol=rtol)
                errors[key] = float(np.abs(output-probes[key]).max())
        return {"status": "PASS", "checkpoint_sha256": self.card["checkpoint_sha256"],
                "maximum_absolute_errors": errors, "atol": atol, "rtol": rtol}


class ShadowAdvisor:
    def __init__(self, folder, encoder):
        self.ranker = NumpyRanker(folder)
        self.ranker.parity()
        if encoder.weights_sha256 != self.ranker.card["dino_weights_sha256"]:
            raise ValueError("ENCODER_CHECKSUM_MISMATCH")
        self.encoder = encoder

    def compare(self, query, references, region=None):
        base = {"mode": "SHADOW_ONLY", "calibration_scope": "SYNTHETIC_ONLY",
                "real_image_validation": self.ranker.card["real_image_validation"],
                "model_version": self.ranker.card["model_version"],
                "checkpoint_sha256": self.ranker.card["checkpoint_sha256"],
                "changes_product_decision": False}
        query_embedding = self.encoder.encode([query])[0]
        qdesc = describe(query, region)
        asset_ids, reference_embeddings, scalar_rows = [], [], []
        for asset_id, image in references:
            embedding = self.encoder.encode([image])[0]
            asset_ids.append(asset_id)
            reference_embeddings.append(embedding)
            scalar_rows.append(scalar_features(qdesc, describe(image), float(query_embedding @ embedding)))
        if not asset_ids:
            return {**base, "status": "NO_REFERENCE", "candidates": []}
        features = pair_features(np.repeat(query_embedding[None], len(asset_ids), axis=0),
                                 np.stack(reference_embeddings), np.stack(scalar_rows), self.ranker.normalizer)
        predictions = self.ranker.predict(features)
        region_scores, suff_scores = [1 / (1 + np.exp(-np.clip(predictions[key]/temp, -50, 50)))
            for key, temp in zip(("same_region", "sufficiency"), self.ranker.temperatures, strict=True)]
        candidates = [{"asset_id": asset_id, "ranking_logit": float(predictions["ranking"][i]),
                       "synthetic_region_score": float(region_scores[i]), "synthetic_sufficiency_score": float(suff_scores[i])}
                       for i, asset_id in enumerate(asset_ids)]
        candidates.sort(key=lambda row: row["ranking_logit"], reverse=True)
        best = candidates[0]
        accepts = min(best["synthetic_region_score"], best["synthetic_sufficiency_score"]) >= self.ranker.card["threshold"]
        return {**base, "status": "EVALUATED", "suggested_reference_asset_id": best["asset_id"],
                "synthetic_threshold_accepts": bool(accepts), "candidates": candidates}
