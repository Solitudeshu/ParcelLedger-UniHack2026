"""Versioned 1,560-D pair features; statistics may only be fit on training rows."""
from __future__ import annotations

import numpy as np

EMBEDDING_DIM = 384
SCALAR_NAMES = ("cosine", "normalized_match_count", "inlier_ratio", "normalized_reprojection_error",
                "overlap", "roi_coverage", "resolution_ratio", "query_blur", "reference_blur",
                "query_glare", "reference_glare", "view_angle_difference")
FEATURE_DIM = EMBEDDING_DIM * 4 + len(SCALAR_NAMES) * 2
FEATURE_VERSION = "dinov2-s14-pair-v1"


class ScalarNormalizer:
    def __init__(self, mean, scale):
        self.mean = np.asarray(mean, dtype=np.float32)
        self.scale = np.asarray(scale, dtype=np.float32)
        if self.mean.shape != (12,) or self.scale.shape != (12,):
            raise ValueError("EXPECTED_TWELVE_STATISTICS")
        if not np.isfinite(self.mean).all() or not np.isfinite(self.scale).all() or (self.scale <= 0).any():
            raise ValueError("INVALID_STATISTICS")

    @classmethod
    def fit(cls, scalars: np.ndarray, splits: list[str]):
        values = np.asarray(scalars, dtype=np.float32)
        if values.ndim != 2 or values.shape[1] != 12 or len(values) == 0:
            raise ValueError("INVALID_SCALAR_MATRIX")
        if len(splits) != len(values) or any(split != "train" for split in splits):
            raise ValueError("NORMALIZER_TRAIN_ONLY")
        means, scales = [], []
        for column in values.T:
            known = column[np.isfinite(column)]
            means.append(float(known.mean()) if len(known) else 0.0)
            scales.append(max(float(known.std()), 1e-6) if len(known) else 1.0)
        return cls(means, scales)

    def transform(self, values):
        values = np.asarray(values, dtype=np.float32)
        if values.ndim != 2 or values.shape[1] != 12:
            raise ValueError("INVALID_SCALAR_MATRIX")
        missing = ~np.isfinite(values)
        normalized = (np.where(missing, self.mean, values) - self.mean) / self.scale
        return normalized, missing.astype(np.float32)

    def state(self):
        return {"version": FEATURE_VERSION, "scalar_names": list(SCALAR_NAMES),
                "mean": self.mean.tolist(), "scale": self.scale.tolist(), "fit_split": "train"}


def pair_features(query, reference, scalars, normalizer: ScalarNormalizer) -> np.ndarray:
    query, reference = (np.asarray(array, dtype=np.float32) for array in (query, reference))
    if query.ndim != 2 or query.shape[1] != EMBEDDING_DIM or query.shape != reference.shape:
        raise ValueError("INVALID_EMBEDDING_SHAPE")
    if not np.isfinite(query).all() or not np.isfinite(reference).all():
        raise ValueError("NONFINITE_EMBEDDING")
    query_norm, reference_norm = np.linalg.norm(query, axis=1, keepdims=True), np.linalg.norm(reference, axis=1, keepdims=True)
    if (query_norm < 1e-8).any() or (reference_norm < 1e-8).any():
        raise ValueError("ZERO_EMBEDDING")
    query, reference = query / query_norm, reference / reference_norm
    normalized, missing = normalizer.transform(scalars)
    if len(normalized) != len(query):
        raise ValueError("FEATURE_ROW_MISMATCH")
    result = np.concatenate([query, reference, abs(query-reference), query*reference, normalized, missing], axis=1)
    if result.shape[1] != FEATURE_DIM or not np.isfinite(result).all():
        raise ValueError("FEATURE_ASSEMBLY_FAILED")
    return result.astype(np.float32)
