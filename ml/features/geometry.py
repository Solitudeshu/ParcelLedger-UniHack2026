"""Versioned pixel-only geometry features shared by training and inference."""
from __future__ import annotations

import cv2
import numpy as np

VERSION = "sift-pair-features-v2"


def supported_projection(points, homography, inlier_mask):
    """Reject degenerate RANSAC output before deriving evidence features."""
    if (homography is None or inlier_mask is None
            or homography.shape != (3, 3) or not np.isfinite(homography).all()
            or abs(np.linalg.det(homography)) < 1e-9
            or inlier_mask.size != len(points)):
        return None
    known = inlier_mask.ravel().astype(bool)
    if known.sum() < 4:
        return None
    selected = points[known]
    denominator = selected @ homography[2, :2] + homography[2, 2]
    if not np.isfinite(denominator).all() or (np.abs(denominator) < 1e-8).any():
        return None
    projected = cv2.perspectiveTransform(selected[:, None], homography)
    if projected is None or not np.isfinite(projected).all():
        return None
    return known, projected[:, 0]


def describe(image, region=None):
    rgb = np.asarray(image)
    scale = min(1.0, 768 / max(rgb.shape[:2]))
    rgb = cv2.resize(rgb, (round(rgb.shape[1]*scale), round(rgb.shape[0]*scale)))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    mask = np.full(gray.shape, 255, np.uint8)
    if region is not None:
        mask[:] = 0
        points = np.rint(np.array(region)/10000*[rgb.shape[1]-1, rgb.shape[0]-1]).astype(np.int32)
        cv2.fillPoly(mask, [points], 255)
    kp, descriptors = cv2.SIFT_create(nfeatures=1600).detectAndCompute(gray, mask)
    selected = mask > 0
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    return {"keypoints": kp, "descriptors": descriptors, "shape": gray.shape, "mask": mask,
            "sharpness": float(laplacian[selected].var()) if selected.any() else 0.,
            "glare": float((gray[selected] > 248).mean()) if selected.any() else 1.}


def scalar_features(query, reference, cosine: float):
    qd, rd = query["descriptors"], reference["descriptors"]
    count, ratio, error, overlap, coverage = 0., 0., float("nan"), 0., 0.
    if qd is not None and rd is not None and min(len(qd), len(rd)) >= 4:
        good = [pair[0] for pair in cv2.BFMatcher().knnMatch(qd, rd, k=2)
                if len(pair) == 2 and pair[0].distance < 0.72 * pair[1].distance]
        unique = {}
        for match in sorted(good, key=lambda match: match.distance):
            unique.setdefault(match.trainIdx, match)
        good = list(unique.values())
        if len(good) >= 4:
            qp = np.float32([query["keypoints"][match.queryIdx].pt for match in good])
            rp = np.float32([reference["keypoints"][match.trainIdx].pt for match in good])
            cv2.setRNGSeed(26)
            H, inliers = cv2.findHomography(qp, rp, cv2.RANSAC, 4., maxIters=2000)
            support = supported_projection(qp, H, inliers)
            if support is not None:
                known, projected = support
                count, ratio = float(known.sum()), float(known.mean())
                error = float(np.median(np.linalg.norm(projected-rp[known], axis=1))) / 768
                h, w = query["shape"]
                valid = cv2.warpPerspective(np.full(reference["shape"], 255, np.uint8), np.linalg.inv(H), (w, h))
                roi = query["mask"] > 0
                overlap = float((valid[roi] > 250).mean()) if roi.any() else 0.
                hull = np.zeros((h, w), np.uint8)
                if count >= 3:
                    cv2.fillConvexPoly(hull, cv2.convexHull(qp[known]).astype(np.int32), 1)
                coverage = float(hull[roi].mean()) if roi.any() else 0.
    resolution = min(query["shape"]) / max(min(reference["shape"]), 1)
    return np.array([cosine, min(count/100, 1), ratio, error, overlap, coverage,
                     resolution, np.log1p(query["sharpness"]), np.log1p(reference["sharpness"]),
                     query["glare"], reference["glare"], np.nan], dtype=np.float32)
