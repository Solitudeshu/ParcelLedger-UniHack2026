"""Revision- and weight-bound dense matcher for research comparisons only."""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import numpy as np

REVISION = "95c9968145c8906b7b59383258e9f73b02853d89"
SOURCE_SHA = "da9d16faf6f602a62d7c6f1c03b85aadae3d877834e348b7c8db11fab5ff2629"
WEIGHTS_SHA = "1557dec0d21b62366465f7ff4d5fdf228cc695d0582e196ad2b80e05230828b7"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_matches(a, b, overlaps):
    a, b, overlaps = np.asarray(a), np.asarray(b), np.asarray(overlaps).reshape(-1)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 2 or len(overlaps) != len(a):
        raise ValueError("INVALID_DENSE_MATCH_SHAPES")
    if not all(np.isfinite(x).all() for x in (a, b, overlaps)) or (overlaps < 0).any() or (overlaps > 1).any():
        raise ValueError("NONFINITE_OR_INVALID_DENSE_MATCHES")
    return a.astype(np.float32), b.astype(np.float32), overlaps


class RomaMatcher:
    def __init__(self, preset, cache_dir="/kaggle/temp/kc-roma-cache"):
        import torch
        from romav2 import RoMaV2

        if preset not in {"fast", "precise"}:
            raise ValueError("ROMA_PRESET_NOT_PROFILED")
        if not torch.cuda.is_available():
            raise ValueError("ROMA_RESEARCH_REQUIRES_GPU")
        if sha256_file(inspect.getfile(RoMaV2)) != SOURCE_SHA:
            raise ValueError("ROMA_SOURCE_IDENTITY_CHANGED")
        torch.hub.set_dir(str(cache_dir))
        torch.set_float32_matmul_precision("highest")
        self.torch = torch
        self.model = RoMaV2(RoMaV2.Cfg(setting=preset))
        if sha256_file(Path(cache_dir) / "checkpoints/romav2.0.1.pt") != WEIGHTS_SHA:
            raise ValueError("ROMA_WEIGHTS_IDENTITY_CHANGED")
        self.preset = preset

    def match(self, reference, query):
        self.torch.manual_seed(26)
        preds = self.model.match(reference, query)
        matches, overlaps, _, _ = self.model.sample(preds, 1600)
        a, b = self.model.to_pixel_coordinates(matches, *reference.shape[:2], *query.shape[:2])
        a, b, overlap = validate_matches(a.cpu().numpy(), b.cpu().numpy(), overlaps.cpu().numpy())
        return a, b, {"sampled_matches": len(a), "overlap_mean": float(overlap.mean()),
                      "overlap_median": float(np.median(overlap)), "overlap_min": float(overlap.min()),
                      "overlap_is_calibrated": False}
