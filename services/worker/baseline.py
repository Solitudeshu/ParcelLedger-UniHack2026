"""Conservative, untrained SIFT/RANSAC reference baseline.

Scores are geometric heuristics, never probabilities. This is an executable
baseline for evaluating the planned trained ranker; it does not establish damage
causality or determine compensation. Coordinates refer to EXIF-oriented images.
"""

from pathlib import Path
from collections.abc import Iterable

import cv2
import numpy as np
from PIL import Image

from ml.features.geometry import supported_projection
from services.api.canonical import bytes_hash, document_hash
from services.api.images import image_quality
from services.api.schemas import MatchResult

BASELINE_VERSION = "sift-local-planar-v1"
BASELINE_CONFIG = {
    "max_side": 1400,
    "features": 3500,
    "ratio": 0.72,
    "ransac_px": 4.0,
    "min_inliers": 12,
    "min_inlier_ratio": 0.45,
    "min_feature_coverage": 0.25,
    "sufficient_inliers": 20,
    "min_overlap": 0.75,
    "min_sharpness": 35,
    "residual_threshold": 28,
    "min_change_fraction": 0.003,
    "coordinate_frame": "exif-oriented-query-to-reference-pixels",
}
SOURCE_SHA256 = bytes_hash(Path(__file__).read_bytes())


def scaled(image: Image.Image):
    ratio = min(1.0, BASELINE_CONFIG["max_side"] / max(image.size))
    output = image.resize((round(image.width * ratio), round(image.height * ratio)))
    return np.asarray(output), ratio


def region_mask(shape, region):
    h, w = shape[:2]
    mask = np.ones((h, w), np.uint8) * 255
    if region:
        mask[:] = 0
        vertices = np.rint(np.asarray(region) / 10000 * [w - 1, h - 1]).astype(np.int32)
        cv2.fillPoly(mask, [vertices], 255)
    return mask


def normalized_polygon(points, width, height):
    points = np.asarray(points).reshape(-1, 2) / [max(width - 1, 1), max(height - 1, 1)]
    return np.clip(np.rint(points * 10000), 0, 10000).astype(int).tolist()


def match_pair(query: Image.Image, reference: Image.Image, region=None):
    q, q_scale = scaled(query)
    r, r_scale = scaled(reference)
    qgray = cv2.cvtColor(q, cv2.COLOR_RGB2GRAY)
    rgray = cv2.cvtColor(r, cv2.COLOR_RGB2GRAY)
    mask = region_mask(q.shape, region)
    detector = cv2.SIFT_create(nfeatures=BASELINE_CONFIG["features"])
    qk, qd = detector.detectAndCompute(qgray, mask)
    rk, rd = detector.detectAndCompute(rgray, None)
    empty = {"valid": False, "score": 0.0, "reason": "LOW_TEXTURE", "metrics": {}}
    if qd is None or rd is None or min(len(qd), len(rd)) < 4:
        return empty
    matches = cv2.BFMatcher().knnMatch(qd, rd, k=2)
    good = [
        pair[0]
        for pair in matches
        if len(pair) == 2 and pair[0].distance < BASELINE_CONFIG["ratio"] * pair[1].distance
    ]
    # Repeated printed motifs cannot inflate support by matching one target keypoint repeatedly.
    unique = {}
    for m in sorted(good, key=lambda x: x.distance):
        unique.setdefault(m.trainIdx, m)
    good = list(unique.values())
    if len(good) < 4:
        return {**empty, "reason": "WRONG_REGION"}
    qp = np.float32([qk[m.queryIdx].pt for m in good])
    rp = np.float32([rk[m.trainIdx].pt for m in good])
    cv2.setRNGSeed(26)
    H, inlier_mask = cv2.findHomography(
        qp, rp, cv2.RANSAC, BASELINE_CONFIG["ransac_px"], maxIters=3000
    )
    support = supported_projection(qp, H, inlier_mask)
    if support is None or abs(H[2, 2]) < 1e-8:
        return {**empty, "reason": "NONPLANAR_GEOMETRY"}
    inliers, projected = support
    count = int(inliers.sum())
    ratio = count / len(good)
    hull = cv2.convexHull(qp[inliers]) if count >= 3 else None
    hull_mask = np.zeros_like(mask)
    if hull is not None:
        cv2.fillConvexPoly(hull_mask, np.rint(hull).astype(np.int32), 255)
    coverage = float(np.count_nonzero(hull_mask & mask) / max(np.count_nonzero(mask), 1))
    error = float(np.median(np.linalg.norm(projected - rp[inliers], axis=1))) if count else 999.0
    valid_mask = cv2.warpPerspective(
        np.ones(r.shape[:2], np.uint8) * 255,
        np.linalg.inv(H),
        (q.shape[1], q.shape[0]),
        flags=cv2.INTER_NEAREST,
    )
    overlap = float(np.count_nonzero(valid_mask & mask) / max(np.count_nonzero(mask), 1))
    score = min(1.0, count / 50) * ratio * min(1.0, coverage / 0.5)
    metrics = {
        "inliers": float(count),
        "inlier_ratio": ratio,
        "feature_coverage": coverage,
        "reprojection_error_px": error,
        "overlap": overlap,
    }
    valid = (
        count >= BASELINE_CONFIG["min_inliers"]
        and ratio >= BASELINE_CONFIG["min_inlier_ratio"]
        and coverage >= BASELINE_CONFIG["min_feature_coverage"]
    )
    # Return homography in ORIGINAL oriented image pixels, not resized coordinates.
    original_H = np.diag([1 / r_scale, 1 / r_scale, 1]) @ H @ np.diag([q_scale, q_scale, 1])
    original_H /= original_H[2, 2]
    return {
        "valid": valid,
        "score": float(score),
        "reason": "INSUFFICIENT_OVERLAP" if not valid else None,
        "metrics": metrics,
        "H": H,
        "original_H": original_H,
        "q": q,
        "r": r,
        "mask": mask,
        "valid_mask": valid_mask,
    }


def observed_changes(pair):
    q, r, H = pair["q"], pair["r"], pair["H"]
    warped = cv2.warpPerspective(r, np.linalg.inv(H), (q.shape[1], q.shape[0]))
    valid = cv2.erode(pair["valid_mask"] & pair["mask"], np.ones((11, 11), np.uint8)) > 0
    if np.count_nonzero(valid) < 1000:
        return [], {"valid_pixels": float(np.count_nonzero(valid))}
    # Robust per-channel affine normalization reduces global illumination changes.
    corrected = warped.astype(np.float32)
    for channel in range(3):
        rv, qv = corrected[:, :, channel][valid], q[:, :, channel][valid].astype(np.float32)
        rq, qq = np.percentile(rv, [20, 50, 80]), np.percentile(qv, [20, 50, 80])
        gain = np.clip((qq[2] - qq[0]) / max(rq[2] - rq[0], 10), 0.5, 2)
        corrected[:, :, channel] = corrected[:, :, channel] * gain + qq[1] - rq[1] * gain
    residual = np.max(
        np.abs(
            cv2.GaussianBlur(q.astype(np.float32), (7, 7), 0)
            - cv2.GaussianBlur(corrected, (7, 7), 0)
        ),
        axis=2,
    )
    change = ((residual > BASELINE_CONFIG["residual_threshold"]) & valid).astype(np.uint8) * 255
    change = cv2.morphologyEx(change, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    change = cv2.morphologyEx(change, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    change[~valid] = 0
    contours, _ = cv2.findContours(change, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polygons = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        if (
            cv2.contourArea(contour) / np.count_nonzero(valid)
            < BASELINE_CONFIG["min_change_fraction"]
        ):
            continue
        polygon = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if 3 <= len(polygon) <= 100:
            polygons.append(normalized_polygon(polygon, q.shape[1], q.shape[0]))
    return polygons, {
        "valid_pixels": float(np.count_nonzero(valid)),
        "changed_fraction": float(np.mean(change[valid] > 0)),
    }


def analyze_images(
    query_id: str,
    query: Image.Image,
    references: Iterable[tuple[str, Image.Image]],
    region=None,
    *,
    run_id: str = "baseline",
) -> dict:
    candidates = []
    best = None
    for asset_id, reference in references:
        try:
            pair = match_pair(query, reference, region)
        except cv2.error:
            pair = {"valid": False, "score": 0.0, "reason": "NONPLANAR_GEOMETRY", "metrics": {}}
        candidates.append({"asset_id": asset_id, "score": pair["score"], "geometry_valid": pair["valid"]})
        # Retain one full image/pair, not all up to 40 original photographs.
        if best is None or (pair["valid"], pair["score"]) > (best[2]["valid"], best[2]["score"]):
            best = asset_id, reference, pair
    candidates.sort(key=lambda c: (c["geometry_valid"], c["score"]), reverse=True)
    result = {
        "query_asset_id": query_id,
        "reference_asset_id": None,
        "status": "NO_RELIABLE_MATCH",
        "query_region": region,
        "reference_region": None,
        "match_score": 0.0,
        "sufficiency_score": 0.0,
        "score_kind": "HEURISTIC",
        "reason_codes": ["MISSING_REFERENCE"]
        if not best
        else [best[2]["reason"] or "WRONG_REGION"],
        "model_version": BASELINE_VERSION,
        "checkpoint_sha256": SOURCE_SHA256,
        "artifact_kind": "BASELINE_SOURCE",
        "config_sha256": document_hash(BASELINE_CONFIG),
        "metrics": best[2]["metrics"] if best else {},
    }
    polygons, change_metrics = [], {}
    if best and best[2]["valid"]:
        asset_id, reference, pair = best
        qq, rq = image_quality(query), image_quality(reference)
        reasons = list(dict.fromkeys(qq["reason_codes"] + rq["reason_codes"]))
        if pair["metrics"]["overlap"] < BASELINE_CONFIG["min_overlap"]:
            reasons.append("INSUFFICIENT_OVERLAP")
        if pair["metrics"]["inliers"] < BASELINE_CONFIG["sufficient_inliers"]:
            reasons.append("LOW_DETAIL")
        qp = np.float32(region or [[0, 0], [10000, 0], [10000, 10000], [0, 10000]])
        qp = qp / 10000 * [query.width - 1, query.height - 1]
        rp = cv2.perspectiveTransform(qp.astype(np.float32)[:, None], pair["original_H"])
        result.update(
            reference_asset_id=asset_id,
            status="MATCHED_INSUFFICIENT" if reasons else "MATCHED_SUFFICIENT",
            match_score=pair["score"],
            sufficiency_score=min(
                pair["metrics"]["overlap"], min(qq["sharpness"], rq["sharpness"]) / 100, 1.0
            ),
            reason_codes=reasons,
            transform_type="LOCAL_PLANAR_HOMOGRAPHY",
            transform=pair["original_H"].ravel().tolist(),
            reference_region=normalized_polygon(rp, reference.width, reference.height),
        )
        if not reasons:
            polygons, change_metrics = observed_changes(pair)
    match = MatchResult.model_validate(result).model_dump(mode="json")
    evidence = [query_id] + ([match["reference_asset_id"]] if match["reference_asset_id"] else [])
    base_observation = {
        "type": "SURFACE_CONDITION",
        "match_id": run_id,
        "model_run_id": run_id,
        "evidence_asset_ids": evidence,
        "author_type": "MODEL",
        "review_status": "PENDING",
    }
    if match["status"] != "MATCHED_SUFFICIENT":
        observations = [
            {
                **base_observation,
                "id": f"{run_id}:0",
                "state": "INCONCLUSIVE",
                "query_polygon": region,
                "text": "Chưa đủ bằng chứng ảnh để đối chiếu vùng đang kiểm tra. Cần bổ sung ảnh cùng mặt hộp, đủ sáng và rõ chi tiết.",
            }
        ]
    elif polygons:
        observations = [
            {
                **base_observation,
                "id": f"{run_id}:{i}",
                "state": "VISIBLE_CHANGE",
                "query_polygon": polygon,
                "text": "Bộ đối chiếu phát hiện khác biệt hình ảnh tại vùng đánh dấu sau khi căn chỉnh. Hai bên cần xem ảnh gốc để xác nhận tình trạng quan sát được.",
            }
            for i, polygon in enumerate(polygons)
        ]
    else:
        observations = [
            {
                **base_observation,
                "id": f"{run_id}:0",
                "state": "NO_VISIBLE_CHANGE",
                "query_polygon": region,
                "text": "Chưa ghi nhận khác biệt vượt ngưỡng của bộ đối chiếu tại vùng quan sát được. Kết quả không loại trừ thay đổi nhỏ hoặc phần bị che khuất.",
            }
        ]
    return {
        "match": match,
        "observations": observations,
        "change_metrics": change_metrics,
        "candidates": candidates,
        "inference_kind": "UNTRAINED_BASELINE",
    }
