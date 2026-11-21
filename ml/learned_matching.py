"""Optional research matcher. Does not change the serving SIFT implementation."""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import cv2
import numpy as np

from ml.features.geometry import supported_projection

LIGHTGLUE_REVISION = "5a650c444718caffc2669dc0094cde908f42dd73"
ALIKED_REVISION = "683d7c65197395c0b3f01ebe76e1084a27e73a65"


def fit_alignment(reference_points, query_points, shape):
    r, q = np.asarray(reference_points, np.float32), np.asarray(query_points, np.float32)
    empty = {"homography": None, "inliers": 0, "ratio": 0., "coverage": 0., "score": 0.}
    if r.shape != q.shape or r.ndim != 2 or r.shape[1] != 2:
        raise ValueError("INVALID_MATCH_COORDINATES")
    if len(r) < 4 or not np.isfinite(r).all() or not np.isfinite(q).all():
        return empty
    cv2.setRNGSeed(26)
    homography, mask = cv2.findHomography(r, q, cv2.RANSAC, 4., maxIters=2000)
    support = supported_projection(r, homography, mask)
    if support is None:
        return empty
    known, _ = support
    h, w = shape[:2]
    hull = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(hull, cv2.convexHull(q[known]).round().astype(np.int32), 1)
    coverage = float(hull.mean())
    count, ratio = int(known.sum()), float(known.mean())
    return {"homography": homography, "inliers": count, "ratio": ratio,
            "coverage": coverage, "score": count * ratio * np.sqrt(coverage)}


class SIFTMatcher:
    def __init__(self):
        self.extractor = cv2.SIFT_create(nfeatures=1600)

    def extract(self, rgb):
        keypoints, descriptors = self.extractor.detectAndCompute(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), None)
        return np.float32([p.pt for p in keypoints]).reshape(-1, 2), descriptors

    def match(self, reference, query):
        rp, rd = reference
        qp, qd = query
        if rd is None or qd is None or min(len(rd), len(qd)) < 4:
            return np.empty((0, 2), np.float32), np.empty((0, 2), np.float32)
        good = [p[0] for p in cv2.BFMatcher().knnMatch(rd, qd, k=2)
                if len(p) == 2 and p[0].distance < .72 * p[1].distance]
        unique = {}
        for match in sorted(good, key=lambda p: p.distance):
            unique.setdefault(match.trainIdx, match)
        good = list(unique.values())
        return (np.float32([rp[m.queryIdx] for m in good]).reshape(-1, 2),
                np.float32([qp[m.trainIdx] for m in good]).reshape(-1, 2))


class AlikedLightGlueMatcher:
    def __init__(self, cache_dir, device="cuda"):
        import torch
        from lightglue import ALIKED, LightGlue

        self.torch, self.device = torch, device
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError("MATCHER_PROFILE_REQUIRES_GPU")
        expected = {ALIKED: "bf579653b2d1b5b4d0c88cefd87113b4ea8ce1ad094fa07e2db41c9bc47b178e",
                    LightGlue: "dcb75b9cad1985c5e7a537c512d4318d47fbcc99df75b39a513de60e027c039f"}
        for module, digest in expected.items():
            if hashlib.sha256(Path(inspect.getfile(module)).read_bytes()).hexdigest() != digest:
                raise ValueError("MATCHER_SOURCE_REVISION_MISMATCH")
        torch.hub.set_dir(str(cache_dir))
        ALIKED.checkpoint_url = f"https://raw.githubusercontent.com/Shiaoming/ALIKED/{ALIKED_REVISION}/models/{{}}.pth"
        self.extractor = ALIKED(max_num_keypoints=1600).eval().to(device)
        self.matcher = LightGlue(features="aliked", depth_confidence=-1, width_confidence=-1).eval().to(device)
        self.weights = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in (Path(cache_dir) / "checkpoints").glob("*.pth")}

    def extract(self, rgb):
        tensor = self.torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1))).float().to(self.device) / 255
        with self.torch.no_grad():
            return self.extractor.extract(tensor, resize=None)

    def match(self, reference, query):
        with self.torch.no_grad():
            matches = self.matcher({"image0": reference, "image1": query})["matches"][0]
            rp = reference["keypoints"][0][matches[:, 0]].cpu().numpy()
            qp = query["keypoints"][0][matches[:, 1]].cpu().numpy()
        return rp, qp
