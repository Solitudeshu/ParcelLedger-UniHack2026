"""Known-transform selection benchmark. No claim about real viewpoint performance."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import torch

from ml.data.parcel3d_audit import find_annotations
from ml.data.parcel_pairs_v2 import ParcelPairs
from ml.learned_matching import (
    ALIKED_REVISION,
    LIGHTGLUE_REVISION,
    AlikedLightGlueMatcher,
    SIFTMatcher,
    fit_alignment,
)


def known_view(rgb, seed):
    rng = np.random.default_rng(seed)
    h, w = rgb.shape[:2]
    source = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    # One planar synthetic transform, not a physically unseen face of a parcel.
    inset = source * .78 + np.float32([w, h]) * .11
    target = inset + rng.uniform(-.08, .08, (4, 2)).astype(np.float32) * [w, h]
    homography = cv2.getPerspectiveTransform(source, target.astype(np.float32))
    query = cv2.warpPerspective(rgb, homography, (w, h), borderValue=(127, 127, 127))
    query = np.clip(query.astype(np.float32) * rng.uniform(.75, 1.25) + rng.uniform(-10, 10), 0, 255).astype(np.uint8)
    return query, homography


def corner_error(estimated, expected, shape):
    if estimated is None:
        return None
    h, w = shape[:2]
    corners = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])[:, None]
    a = cv2.perspectiveTransform(corners, estimated)
    b = cv2.perspectiveTransform(corners, expected)
    if a is None or b is None or not np.isfinite(a).all():
        return None
    return float(np.linalg.norm(a - b, axis=2).mean())


def run(input_root, manifest, output, full_selection=False):
    torch.set_num_threads(2)
    root = find_annotations(Path(input_root))["train"].parent
    dataset = ParcelPairs(root, manifest, "selection", size=518)
    groups = defaultdict(list)
    for index, row in enumerate(dataset.rows):
        groups[row["lineage_id"]].append(index)
    # Two source images per lineage. These are independent source objects, not
    # proof of two independent physical parcels per object.
    indices = [index for lineage in sorted(groups)
               for index in (groups[lineage] if full_selection else groups[lineage][:2])]
    images, lineages, paths = [], [], []
    for index in indices:
        sample = dataset[index]
        rgb = (sample["reference"].numpy().transpose(1, 2, 0) * 255).round().astype(np.uint8)
        images.append(rgb)
        lineages.append(sample["lineage"])
        paths.append(sample["source"])
    matchers = {"sift": SIFTMatcher(), "aliked_lightglue": AlikedLightGlueMatcher("/kaggle/temp/kc-matcher-cache")}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    records, summaries = [], {}
    for name, matcher in matchers.items():
        started = time.perf_counter()
        references = [matcher.extract(rgb) for rgb in images]
        if name != "sift":
            torch.cuda.synchronize()
        extraction_seconds = time.perf_counter() - started
        for index, reference_image in enumerate(images):
            query, truth = known_view(reference_image, [26, index])
            rng = np.random.default_rng([99, index])
            distractors = [i for i in range(len(images)) if lineages[i] != lineages[index]]
            candidates = [index] + rng.choice(distractors, 3, replace=False).tolist()
            rng.shuffle(candidates)
            if name != "sift":
                torch.cuda.synchronize()
            started = time.perf_counter()
            query_features = matcher.extract(query)
            results = []
            for candidate in candidates:
                rp, qp = matcher.match(references[candidate], query_features)
                fit = fit_alignment(rp, qp, query.shape)
                error = corner_error(fit["homography"], truth, query.shape) if candidate == index else None
                results.append({"candidate": candidate, "candidate_source": paths[candidate], "matches": len(rp),
                                **{key: value for key, value in fit.items() if key != "homography"},
                                "true_pair_corner_error_px": error})
            if name != "sift":
                torch.cuda.synchronize()
            elapsed = time.perf_counter() - started
            ordered = sorted(results, key=lambda r: r["score"], reverse=True)
            # Zero-score ties count as no successful retrieval.
            top1 = ordered[0]["score"] > 0 and ordered[0]["candidate"] == index
            positive = next(row for row in results if row["candidate"] == index)
            row = {"matcher": name, "query_source": paths[index], "lineage": lineages[index],
                   "top1_correct": top1, "true_pair_corner_error_px": positive["true_pair_corner_error_px"],
                   "query_and_gallery_seconds": elapsed, "candidates": results}
            records.append(row)
        selected = [row for row in records if row["matcher"] == name]
        errors = [r["true_pair_corner_error_px"] for r in selected]
        summaries[name] = {"queries": len(selected), "source_lineages": len(groups),
                           "top1_accuracy": float(np.mean([r["top1_correct"] for r in selected])),
                           "true_pair_alignment_within_3px_rate": float(np.mean([e is not None and e <= 3 for e in errors])),
                           "true_pair_alignment_failures": sum(e is None for e in errors),
                           "p95_query_and_4_references_seconds": float(np.percentile([r["query_and_gallery_seconds"] for r in selected], 95)),
                           "reference_gallery_extraction_seconds": extraction_seconds}
    report = {"status": "SYNTHETIC_SELECTION_COMPARISON_COMPLETE", "manifest_sha256": dataset.manifest_sha256,
              "sampling": "ALL_SELECTION_IMAGES" if full_selection else "FIRST_TWO_PER_LINEAGE",
              "lightglue_revision": LIGHTGLUE_REVISION, "aliked_weights_revision": ALIKED_REVISION,
              "weights_sha256": matchers["aliked_lightglue"].weights, "summaries": summaries,
              "torch": torch.__version__, "gpu": torch.cuda.get_device_name(), "promotion": "HOLD",
              "test_pixels": "NOT_READ", "calibration": "NOT_RUN", "real_box_validation": "NOT_RUN",
              "limits": ["Known planar transforms of selection renders, not photographs from new viewpoints.",
                         "Foreground crop is annotation-provided. No parcel detection or segmentation claim.",
                         "Four-candidate gallery; no no-reference acceptance calibration.",
                         "SIFT comparator follows feature settings; not the entire A serving pipeline.",
                         "Reference gallery is cached; timing excludes loading, network and queue."]}
    (output / "matching-rows.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    (output / "matching-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return report
