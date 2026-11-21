"""Identity-checked wrong-object diagnostics, never an operational gate.

Positive and negative galleries share queries, but contain different source
objects. Scores are uncalibrated; physical-box identity remains unvalidated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from ml.assess_matching_stress import MANIFEST_SHA, SCENARIOS, checked_rows, load_bundle
from ml.roma_matching import REVISION, WEIGHTS_SHA

COMPLETE = "NEGATIVE_SELECTION_COMPLETE_NOT_CALIBRATED"
SCORE_GRID = (5, 10, 25, 50)


def load_negatives(folder):
    folder = Path(folder)
    receipt = json.loads(Path(str(folder) + "-outputs.json").read_bytes())
    if (receipt["collection_status"] != "COMPLETE" or receipt["run_status"] != "COMPLETE"
            or receipt["version"] != 1
            or receipt["reference"] != "solishu/notebook-unihack26-romav2-negative-selection"):
        raise ValueError("NEGATIVE_COLLECTION_IDENTITY_MISMATCH")
    entries = {row["name"]: row for row in receipt["files"]}
    bundle = {}
    for key, name in (("sources", "source-hashes.json"), ("report", "negatives/negative-report.json"),
                      ("rows", "negatives/negative-rows.json")):
        raw = (folder / name).read_bytes()
        entry = entries[name]
        if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("NEGATIVE_ARTIFACT_HASH_MISMATCH")
        bundle[key] = json.loads(raw)
    bundle["provenance"] = {"reference": receipt["reference"], "version": receipt["version"],
                            "files": {name: entry["sha256"] for name, entry in entries.items()}}
    return bundle


def checked_negatives(positive, negative):
    rows = checked_rows(positive, ("romav2_fast",))["romav2_fast"]
    identity, report = positive["report"]["identity"], negative["report"]
    if (identity["preset"] != "fast" or identity["revision"] != REVISION
            or identity["weights_sha256"] != WEIGHTS_SHA
            or report["status"] != COMPLETE or report["manifest_sha256"] != MANIFEST_SHA
            or report["revision"] != REVISION or report["weights_sha256"] != WEIGHTS_SHA
            or report["preset"] != "fast" or report["test"] != "NOT_READ"
            or report["calibration"] != "NOT_RUN" or report["promotion"] != "HOLD"
            or report["gallery_seed"] != 2601 or report["source_lineages"] != 25
            or report["source_query_images"] != 50 or report["completed_queries"] != 250
            or report["target_queries"] != 250
            or report["label"] != "FOUR_DISTINCT_OTHER_DECLARED_SOURCE_OBJECTS"):
        raise ValueError("NEGATIVE_SCOPE_OR_COMPLETION_MISMATCH")
    for name in ("ml/evaluate_matching_stress.py", "ml/learned_matching.py", "ml/roma_matching.py",
                 "ml/data/parcel_pairs_v2.py", "ml/evaluate_matching.py", "ml/features/geometry.py"):
        if not positive["sources"].get(name) or positive["sources"][name] != negative["sources"].get(name):
            raise ValueError("NEGATIVE_SHARED_PROTOCOL_CHANGED")
    if (report["protocol_source_sha256"] != negative["sources"]["ml/evaluate_matching_stress.py"]
            or report["negative_source_sha256"] != negative["sources"].get("ml/evaluate_roma_negatives.py")):
        raise ValueError("NEGATIVE_REPORTED_SOURCE_MISMATCH")
    source_objects = {}
    for row in rows.values():
        for candidate in row["candidates"]:
            source = candidate["candidate_source"]
            if source_objects.setdefault(source, row["lineage"]) != row["lineage"]:
                raise ValueError("POSITIVE_SOURCE_OBJECT_CONFLICT")
    negatives = {}
    for row in negative["rows"]:
        key = (row["lineage"], row["query_source"], row["scenario"])
        candidates = row["candidates"]
        if key not in rows or key in negatives or row["label"] != "NO_CORRECT_OBJECT_IN_GALLERY":
            raise ValueError("NEGATIVE_QUERY_IDENTITY_MISMATCH")
        if (len(candidates) != 4 or len({c["lineage"] for c in candidates}) != 4
                or any(c["lineage"] == row["lineage"]
                       or source_objects.get(c["candidate_source"]) != c["lineage"] for c in candidates)):
            raise ValueError("NEGATIVE_GALLERY_CONTAINS_WRONG_LABEL")
        if not np.isfinite(row["query_gallery_seconds"]) or row["query_gallery_seconds"] <= 0:
            raise ValueError("NEGATIVE_INVALID_TIMING")
        for c in candidates:
            if (not all(np.isfinite(c[k]) for k in ("score", "coverage", "ratio", "overlap_mean", "overlap_median"))
                    or c["score"] < 0 or c["sampled_matches"] != 1600
                    or not 0 <= c["inliers"] <= c["sampled_matches"]
                    or any(not 0 <= c[k] <= 1 for k in ("coverage", "ratio", "overlap_mean", "overlap_median"))
                    or c["overlap_is_calibrated"] is not False):
                raise ValueError("NEGATIVE_INVALID_MATCH_STATISTICS")
        negatives[key] = row
    if rows.keys() != negatives.keys():
        raise ValueError("NEGATIVE_INCOMPLETE_QUERY_GRID")
    return rows, negatives


def ordered(row):
    return sorted(row["candidates"], key=lambda item: item["score"], reverse=True)


def accepts(row, threshold):
    first, second = ordered(row)[:2]
    return (first["score"] >= threshold and first["inliers"] >= 8 and first["coverage"] >= .04
            and first["score"] > second["score"] and first["score"] >= 1.3 * second["score"])


def quantiles(values):
    return dict(zip(("min", "p05", "p50", "p95", "max"),
                    np.percentile(values, (0, 5, 50, 95, 100)).tolist(), strict=True))


def assess(positive, negative):
    positives, negatives = checked_negatives(positive, negative)
    summaries = {}
    for scenario in (*SCENARIOS, "all"):
        keys = [key for key in positives if scenario == "all" or key[2] == scenario]
        paired = [(ordered(positives[k])[0], ordered(negatives[k])[0]) for k in keys]
        summaries[scenario] = {
            "queries": len(keys),
            "positive_top_score_greater_than_negative_top": sum(p["score"] > n["score"] for p, n in paired),
            "equal_top_score": sum(p["score"] == n["score"] for p, n in paired),
            "positive_top_score": quantiles([p["score"] for p, _ in paired]),
            "wrong_object_top_score": quantiles([n["score"] for _, n in paired]),
            "wrong_object_top_overlap_mean": quantiles([n["overlap_mean"] for _, n in paired]),
            "negative_query_gallery_seconds": quantiles([negatives[k]["query_gallery_seconds"] for k in keys]),
            "fixed_legacy_diagnostic_grid": [{
                "min_score": threshold, "min_inliers": 8, "min_coverage": .04, "min_margin": 1.3,
                "positive_gallery_accepted": sum(accepts(positives[k], threshold) for k in keys),
                "wrong_object_gallery_accepted": sum(accepts(negatives[k], threshold) for k in keys),
            } for threshold in SCORE_GRID],
        }
    return {"status": "WRONG_OBJECT_SELECTION_ASSESSMENT_COMPLETE", "summaries": summaries,
            "positive_provenance": positive["provenance"], "negative_provenance": negative["provenance"],
            "source_objects": 25, "operating_point": None, "promotion": "HOLD",
            "calibration": "NOT_RUN", "test": "NOT_READ", "real_box_validation": "NOT_RUN",
            "limits": ["Selection-only synthetic queries, 50 source images from 25 declared objects.",
                       "Other declared objects may look alike; identity is not inferred from appearance.",
                       "Positive galleries may contain several valid views; exact-source rank is a proxy.",
                       "The inherited fixed diagnostic grid is not a calibrated RoMa acceptance policy.",
                       "No threshold is selected; do not promote from this report."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--positive", type=Path, default=Path("artifacts/kaggle/romav2-stress-fast-v1"))
    parser.add_argument("--negative", type=Path, default=Path("artifacts/kaggle/romav2-negative-selection-v1"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/evaluation/roma-negative-assessment.json"))
    args = parser.parse_args()
    report = assess(load_bundle(args.positive), load_negatives(args.negative))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "all": report["summaries"]["all"], "promotion": "HOLD"}))


if __name__ == "__main__":
    main()
