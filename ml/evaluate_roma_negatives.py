"""Selection-only wrong-object gallery probe, separate from positive stress runs.

Distinct declared source objects are controlled negatives. They do not establish
wrong-face labels or the ability to distinguish two physically identical boxes.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import torch

from ml.data.parcel3d_audit import find_annotations
from ml.data.parcel_pairs_v2 import ParcelPairs
from ml.evaluate_matching_stress import SCENARIOS, gallery_lineages, query_variant
from ml.learned_matching import fit_alignment
from ml.roma_matching import REVISION, WEIGHTS_SHA, RomaMatcher, sha256_file


def wrong_object_gallery(lineages, query_index):
    unique = sorted(set(lineages) - {lineages[query_index]})
    if len(unique) < 4:
        raise ValueError("NEED_FOUR_OTHER_OBJECTS")
    rng = np.random.default_rng([2601, query_index])
    selected = rng.choice(unique, 4, replace=False)
    return [int(rng.choice([i for i, value in enumerate(lineages) if value == name])) for name in selected]


def run(input_root, manifest, output, max_seconds=7200):
    torch.set_num_threads(2)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    data = ParcelPairs(find_annotations(Path(input_root))["train"].parent, manifest, "selection", size=518)
    grouped, excluded = gallery_lineages(data.rows)
    indices = [i for key in sorted(grouped) for i in grouped[key][:4]]
    images, paths, lineages = [], [], []
    for i in indices:
        sample = data[i]
        images.append((sample["reference"].numpy().transpose(1, 2, 0) * 255).round().astype(np.uint8))
        paths.append(sample["source"])
        lineages.append(sample["lineage"])
    queries = [i for key in sorted(grouped) for i in [j for j, value in enumerate(lineages) if value == key][:2]]
    matcher = RomaMatcher("fast")
    records = []
    root = Path(__file__).resolve().parents[1]

    def save(status):
        report = {"status": status, "preset": "fast", "revision": REVISION, "weights_sha256": WEIGHTS_SHA,
                  "manifest_sha256": data.manifest_sha256, "source_lineages": len(grouped),
                  "source_query_images": len(queries), "completed_queries": len(records),
                  "target_queries": len(queries) * len(SCENARIOS), "excluded_lineages": excluded,
                  "protocol_source_sha256": sha256_file(root / "ml/evaluate_matching_stress.py"),
                  "negative_source_sha256": sha256_file(__file__), "gallery_seed": 2601,
                  "test": "NOT_READ", "calibration": "NOT_RUN", "promotion": "HOLD",
                  "label": "FOUR_DISTINCT_OTHER_DECLARED_SOURCE_OBJECTS", "seconds": time.monotonic() - started,
                  "gpu": torch.cuda.get_device_name(), "torch": torch.__version__,
                  "limits": ["Synthetic stress queries on selection; no calibration or physical parcels.",
                             "Other declared objects are negatives, not a proof of visually distinguishable identity.",
                             "Does not label alternate views of the same source object as wrong-face.",
                             "Scores and overlap statistics are uncalibrated; no threshold is chosen here."]}
        for name, content in (("negative-rows.json", records), ("negative-report.json", report)):
            temporary = output / (name + ".tmp")
            temporary.write_text(json.dumps(content, indent=2), encoding="utf-8")
            temporary.replace(output / name)
        print(json.dumps({"status": status, "completed_queries": len(records), "seconds": report["seconds"]}), flush=True)
        return report

    for index in queries:
        candidates = wrong_object_gallery(lineages, index)
        for scenario in SCENARIOS:
            if time.monotonic() - started > max_seconds:
                return save("PARTIAL_SESSION_BUDGET_REQUIRES_REVIEW")
            query, _, _ = query_variant(images[index], scenario, index)
            torch.cuda.synchronize()
            before = time.perf_counter()
            results = []
            for candidate in candidates:
                rp, qp, stats = matcher.match(images[candidate], query)
                fit = fit_alignment(rp, qp, query.shape)
                results.append({"candidate_source": paths[candidate], "lineage": lineages[candidate],
                                **stats, **{key: value for key, value in fit.items() if key != "homography"}})
            torch.cuda.synchronize()
            records.append({"query_source": paths[index], "lineage": lineages[index], "scenario": scenario,
                            "candidates": results, "query_gallery_seconds": time.perf_counter() - before,
                            "label": "NO_CORRECT_OBJECT_IN_GALLERY"})
        save("RUNNING")
    return save("NEGATIVE_SELECTION_COMPLETE_NOT_CALIBRATED")
