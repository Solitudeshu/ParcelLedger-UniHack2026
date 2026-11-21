"""RoMa v2 on the exact stress protocol; separate preset runs, no serving change."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import torch

from ml.data.parcel3d_audit import find_annotations
from ml.data.parcel_pairs_v2 import ParcelPairs
from ml.evaluate_matching_stress import (
    SCENARIOS,
    gallery_lineages,
    query_variant,
    visible_transfer_error,
)
from ml.learned_matching import fit_alignment
from ml.roma_matching import REVISION, SOURCE_SHA, WEIGHTS_SHA, RomaMatcher, sha256_file


def run(input_root, manifest, output, preset, max_seconds=12000):
    torch.set_num_threads(2)
    started_run = time.monotonic()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    dataset = ParcelPairs(find_annotations(Path(input_root))["train"].parent, manifest, "selection", size=518)
    grouped, excluded = gallery_lineages(dataset.rows)
    indices = [i for key in sorted(grouped) for i in grouped[key][:4]]
    images, paths, lineages = [], [], []
    for index in indices:
        sample = dataset[index]
        images.append((sample["reference"].numpy().transpose(1, 2, 0) * 255).round().astype(np.uint8))
        paths.append(sample["source"])
        lineages.append(sample["lineage"])
    queries = [i for key in sorted(grouped) for i in [j for j, value in enumerate(lineages) if value == key][:2]]
    matcher = RomaMatcher(preset)
    records = []
    source_root = Path(__file__).resolve().parents[1]
    identity = {"preset": preset, "revision": REVISION, "model_source_sha256": SOURCE_SHA,
                "weights_sha256": WEIGHTS_SHA, "manifest_sha256": dataset.manifest_sha256,
                "protocol_source_sha256": sha256_file(source_root / "ml/evaluate_matching_stress.py"),
                "run_source_sha256": sha256_file(__file__), "adapter_source_sha256": sha256_file(source_root / "ml/roma_matching.py"),
                "sample_count": 1600, "seed": 26, "gallery": "FOUR_SAME_LINEAGE_VIEWS",
                "source_lineages": len(grouped), "source_query_images": len(queries)}

    def save(status):
        summaries = {}
        for scenario in SCENARIOS:
            rows = [r for r in records if r["scenario"] == scenario]
            summaries[scenario] = {"queries": len(rows),
                "exact_source_top1_proxy": sum(r["exact_source_top1_proxy"] for r in rows),
                "true_pair_visible_p95_within_3px": sum(r["true_pair_visible_p95_within_3px"] for r in rows),
                "p95_query_gallery_seconds": float(np.percentile([r["query_gallery_seconds"] for r in rows], 95)) if rows else None}
        report = {"status": status, "identity": identity, "summaries": {f"romav2_{preset}": summaries},
                  "completed_queries": len(records), "target_queries": len(queries) * len(SCENARIOS),
                  "seconds": time.monotonic() - started_run, "excluded_lineages": excluded,
                  "peak_allocated_bytes": torch.cuda.max_memory_allocated(), "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
                  "test_pixels": "NOT_READ", "calibration": "NOT_RUN", "promotion": "HOLD",
                  "torch": torch.__version__, "gpu": torch.cuda.get_device_name(),
                  "limits": ["Same procedural stress query generation as SIFT/LightGlue, not physical parcel photos.",
                             "Exact-source rank is only a proxy when alternate views can be valid references.",
                             "Geometry is measured on the known source pair, not end-to-end retrieval correctness.",
                             "No per-feature cache: timing includes both encoders for each gallery candidate.",
                             "1600 overlap-weighted samples without a calibrated acceptance filter; scores cannot share LightGlue thresholds.",
                             "Original RoMa/DINOv3 precision and source preserved, including BF16 emulation on T4."]}
        for name, value in (("stress-rows.json", records), ("stress-report.json", report)):
            temporary = output / (name + ".tmp")
            temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
            temporary.replace(output / name)
        print(json.dumps({"status": status, "preset": preset, "completed_queries": len(records),
                          "seconds": report["seconds"]}), flush=True)
        return report

    for index in queries:
        same = [j for j in range(len(images)) if lineages[j] == lineages[index] and j != index]
        candidates = [index, *same[:3]]
        np.random.default_rng([99, index]).shuffle(candidates)
        for scenario in SCENARIOS:
            if time.monotonic() - started_run > max_seconds:
                return save("PARTIAL_SESSION_BUDGET_REQUIRES_REVIEW")
            query, truth, visible = query_variant(images[index], scenario, index)
            torch.cuda.synchronize()
            started = time.perf_counter()
            results = []
            for candidate in candidates:
                rp, qp, statistics = matcher.match(images[candidate], query)
                fit = fit_alignment(rp, qp, query.shape)
                error = visible_transfer_error(fit["homography"], truth, visible) if candidate == index else None
                results.append({"candidate_source": paths[candidate], "exact_source": candidate == index,
                                **statistics, **{k: v for k, v in fit.items() if k != "homography"},
                                "known_source_visible_transfer": error})
            torch.cuda.synchronize()
            elapsed = time.perf_counter() - started
            ordered = sorted(results, key=lambda c: c["score"], reverse=True)
            error = next(r for r in results if r["exact_source"])["known_source_visible_transfer"]
            records.append({"matcher": f"romav2_{preset}", "scenario": scenario, "query_source": paths[index],
                            "lineage": lineages[index],
                            "exact_source_top1_proxy": bool(ordered[0]["exact_source"] and ordered[0]["score"] > ordered[1]["score"]),
                            "true_pair_visible_p95_within_3px": error is not None and error["p95_px"] <= 3,
                            "query_gallery_seconds": elapsed, "candidates": results})
        save("RUNNING")
    return save("STRESS_SELECTION_COMPLETE_NOT_REAL_VIEWPOINT_VALIDATION")
