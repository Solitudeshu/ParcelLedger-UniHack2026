"""Known-transform stress probes with same-lineage gallery distractors.

Exact-source retrieval is a proxy: another view of the same source object can
also be a valid reference. This benchmark cannot assign real wrong-face labels.
"""
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
from ml.evaluate_matching import known_view
from ml.learned_matching import AlikedLightGlueMatcher, SIFTMatcher, fit_alignment

SCENARIOS = ("planar", "close_up", "blur", "shadow", "occlusion")


def gallery_lineages(rows):
    grouped = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[row["lineage_id"]].append(index)
    eligible = {key: values for key, values in grouped.items() if len(values) >= 4}
    excluded = {key: len(values) for key, values in grouped.items() if len(values) < 4}
    if not eligible:
        raise ValueError("NO_FOUR_VIEW_LINEAGES")
    return eligible, excluded


def query_variant(rgb, scenario, seed):
    if scenario not in SCENARIOS:
        raise ValueError("UNKNOWN_STRESS_SCENARIO")
    h, w = rgb.shape[:2]
    rng = np.random.default_rng([26, seed])
    query, truth = known_view(rgb, [26, seed])
    ignore = np.zeros((h, w), dtype=np.uint8)
    if scenario == "close_up":
        fraction = .55
        x = float(rng.uniform(.08, .37) * w)
        y = float(rng.uniform(.08, .37) * h)
        truth = np.float64([[1 / fraction, 0, -x / fraction], [0, 1 / fraction, -y / fraction], [0, 0, 1]])
        query = cv2.warpPerspective(rgb, truth, (w, h))
    elif scenario == "blur":
        query = cv2.GaussianBlur(query, (13, 13), 3.)
    elif scenario == "shadow":
        gain = np.linspace(.35, 1.1, w, dtype=np.float32)[None, :, None]
        query = np.clip(query * gain, 0, 255).astype(np.uint8)
    elif scenario == "occlusion":
        # Known synthetic obstruction; do not score hidden pixels as observable.
        x0, x1, y0, y1 = int(.3 * w), int(.7 * w), int(.2 * h), int(.8 * h)
        query[y0:y1, x0:x1] = [160, 130, 110]
        ignore[y0:y1, x0:x1] = 1
    visible = cv2.warpPerspective(np.ones((h, w), np.uint8), truth, (w, h), flags=cv2.INTER_NEAREST)
    visible = ((visible > 0) & (ignore == 0)).astype(np.uint8)
    return query, truth, visible


def visible_transfer_error(estimated, truth, visible):
    if estimated is None:
        return None
    h, w = visible.shape
    xx, yy = np.meshgrid(np.linspace(.05 * w, .95 * w, 19), np.linspace(.05 * h, .95 * h, 19))
    points = np.stack((xx.flatten(), yy.flatten()), axis=1).astype(np.float32)
    pixels = points.round().astype(int)
    points = points[visible[pixels[:, 1], pixels[:, 0]] > 0]
    if len(points) < 4:
        return None
    reference = cv2.perspectiveTransform(points[:, None], np.linalg.inv(truth))
    mapped = cv2.perspectiveTransform(reference, estimated)[:, 0]
    if not np.isfinite(mapped).all():
        return None
    errors = np.linalg.norm(mapped - points, axis=1)
    return {"points": len(points), "mean_px": float(errors.mean()), "p95_px": float(np.percentile(errors, 95))}


def run(input_root, manifest, output):
    torch.set_num_threads(2)
    root = find_annotations(Path(input_root))["train"].parent
    dataset = ParcelPairs(root, manifest, "selection", size=518)
    by_lineage, excluded = gallery_lineages(dataset.rows)
    indices = [i for lineage in sorted(by_lineage) for i in by_lineage[lineage][:4]]
    images, paths, lineages = [], [], []
    for index in indices:
        sample = dataset[index]
        images.append((sample["reference"].numpy().transpose(1, 2, 0) * 255).round().astype(np.uint8))
        paths.append(sample["source"])
        lineages.append(sample["lineage"])
    queries = [i for lineage in sorted(by_lineage)
               for i in [j for j, value in enumerate(lineages) if value == lineage][:2]]
    matchers = {"sift": SIFTMatcher(), "aliked_lightglue": AlikedLightGlueMatcher("/kaggle/temp/kc-stress-cache")}
    records = []
    for name, matcher in matchers.items():
        references = [matcher.extract(rgb) for rgb in images]
        for index in queries:
            same = [j for j in range(len(images)) if lineages[j] == lineages[index] and j != index]
            if len(same) < 3:
                raise ValueError("NEED_FOUR_VIEWS_PER_LINEAGE")
            candidates = [index, *same[:3]]
            np.random.default_rng([99, index]).shuffle(candidates)
            for scenario in SCENARIOS:
                query, truth, visible = query_variant(images[index], scenario, index)
                if name != "sift":
                    torch.cuda.synchronize()
                started = time.perf_counter()
                query_features = matcher.extract(query)
                results = []
                for candidate in candidates:
                    rp, qp = matcher.match(references[candidate], query_features)
                    fit = fit_alignment(rp, qp, query.shape)
                    error = visible_transfer_error(fit["homography"], truth, visible) if candidate == index else None
                    results.append({"candidate_source": paths[candidate], "exact_source": candidate == index,
                                    **{k: v for k, v in fit.items() if k != "homography"},
                                    "known_source_visible_transfer": error})
                if name != "sift":
                    torch.cuda.synchronize()
                elapsed = time.perf_counter() - started
                ordered = sorted(results, key=lambda c: c["score"], reverse=True)
                exact = ordered[0]["exact_source"] and ordered[0]["score"] > ordered[1]["score"]
                positive = next(r for r in results if r["exact_source"])
                error = positive["known_source_visible_transfer"]
                records.append({"matcher": name, "scenario": scenario, "query_source": paths[index],
                                "lineage": lineages[index], "exact_source_top1_proxy": bool(exact),
                                "true_pair_visible_p95_within_3px": error is not None and error["p95_px"] <= 3,
                                "query_gallery_seconds": elapsed, "candidates": results})
    summaries = {}
    for name in matchers:
        summaries[name] = {}
        for scenario in SCENARIOS:
            rows = [r for r in records if r["matcher"] == name and r["scenario"] == scenario]
            summaries[name][scenario] = {"queries": len(rows),
                "exact_source_top1_proxy": sum(r["exact_source_top1_proxy"] for r in rows),
                "true_pair_visible_p95_within_3px": sum(r["true_pair_visible_p95_within_3px"] for r in rows),
                "p95_query_gallery_seconds": float(np.percentile([r["query_gallery_seconds"] for r in rows], 95))}
    report = {"status": "STRESS_SELECTION_COMPLETE_NOT_REAL_VIEWPOINT_VALIDATION", "summaries": summaries,
              "source_lineages": len(by_lineage), "source_query_images": len(queries), "scenarios": SCENARIOS,
              "excluded_lineages_with_fewer_than_four_images": excluded,
              "manifest_sha256": dataset.manifest_sha256, "weights_sha256": matchers["aliked_lightglue"].weights,
              "test_pixels": "NOT_READ", "calibration": "NOT_RUN", "promotion": "HOLD",
              "gpu": torch.cuda.get_device_name(), "torch": torch.__version__,
              "limits": ["Query transforms, crops, shadows and obstruction are synthetic.",
                         "Same-lineage gallery may contain multiple valid references; exact-source rank is only a proxy.",
                         "Geometry is scored only against the known original source, not unknown cross-view correspondence.",
                         "Provided foreground crop and known visibility are evaluation conveniences, not product capabilities.",
                         "Two source queries per eligible source object are not independent physical parcels.",
                         "Objects with fewer than four selection images are explicitly excluded from this four-view probe.",
                         "Visible-grid p95 transfer differs from full-frame mean corner error; do not compare them as the same metric."]}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "stress-rows.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    (output / "stress-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return report
