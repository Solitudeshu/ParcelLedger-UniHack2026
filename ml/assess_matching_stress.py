"""Compare completed, identity-checked stress runs without selecting a gate.

The geometry endpoint uses the known source pair. Retrieval is an exact-source
proxy, not a label for whether another view is a valid reference. No calibration
or test predictions are used, and this module cannot promote a serving model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np

SCENARIOS = ("planar", "close_up", "blur", "shadow", "occlusion")
MANIFEST_SHA = "267dd46092d3429dc66f539fb897e79b4be3f68beb94b75cd19f7289250e4d38"
COMPLETE = "STRESS_SELECTION_COMPLETE_NOT_REAL_VIEWPOINT_VALIDATION"
GEOMETRY = "true_pair_visible_p95_within_3px"
RETRIEVAL = "exact_source_top1_proxy"


def load_bundle(folder):
    folder = Path(folder)
    receipt = json.loads(Path(str(folder) + "-outputs.json").read_bytes())
    if receipt["collection_status"] != "COMPLETE" or receipt["run_status"] != "COMPLETE":
        raise ValueError("STRESS_COLLECTION_NOT_COMPLETE")
    files = {row["name"]: row for row in receipt["files"]}
    output = {}
    for key, name in (("sources", "source-hashes.json"), ("report", "stress/stress-report.json"),
                      ("rows", "stress/stress-rows.json")):
        raw = (folder / name).read_bytes()
        if (len(raw) != files[name]["bytes"]
                or hashlib.sha256(raw).hexdigest() != files[name]["sha256"]):
            raise ValueError("STRESS_ARTIFACT_HASH_MISMATCH")
        output[key] = json.loads(raw)
    output["provenance"] = {"reference": receipt["reference"], "version": receipt["version"],
                            "files": {name: files[name]["sha256"] for name in files}}
    return output


def checked_rows(bundle, expected_names):
    report = bundle["report"]
    identity = report.get("identity", report)
    if (report["status"] != COMPLETE or identity["manifest_sha256"] != MANIFEST_SHA
            or report["test_pixels"] != "NOT_READ" or report["calibration"] != "NOT_RUN"
            or report["promotion"] != "HOLD"):
        raise ValueError("STRESS_SCOPE_OR_COMPLETION_MISMATCH")
    if identity["source_lineages"] != 25 or identity["source_query_images"] != 50:
        raise ValueError("STRESS_COHORT_SIZE_MISMATCH")
    groups = {name: {} for name in expected_names}
    for row in bundle["rows"]:
        name = row["matcher"]
        key = (row["lineage"], row["query_source"], row["scenario"])
        if name not in groups or key in groups[name] or row["scenario"] not in SCENARIOS:
            raise ValueError("STRESS_DUPLICATE_OR_UNKNOWN_ROW")
        candidates = row["candidates"]
        if (len(candidates) != 4 or len({c["candidate_source"] for c in candidates}) != 4
                or sum(c["exact_source"] for c in candidates) != 1):
            raise ValueError("STRESS_GALLERY_IDENTITY_MISMATCH")
        positive = next(c for c in candidates if c["exact_source"])
        if positive["candidate_source"] != row["query_source"]:
            raise ValueError("STRESS_POSITIVE_IDENTITY_MISMATCH")
        if not np.isfinite(row["query_gallery_seconds"]) or row["query_gallery_seconds"] <= 0:
            raise ValueError("STRESS_INVALID_TIMING")
        if not all(np.isfinite(c["score"]) and c["score"] >= 0 for c in candidates):
            raise ValueError("STRESS_INVALID_MATCH_SCORE")
        error = positive["known_source_visible_transfer"]
        if error is not None and (not np.isfinite(error["p95_px"]) or error["p95_px"] < 0):
            raise ValueError("STRESS_INVALID_GEOMETRY")
        ordered = sorted(candidates, key=lambda c: c["score"], reverse=True)
        geometry = error is not None and error["p95_px"] <= 3
        retrieval = bool(ordered[0]["exact_source"] and ordered[0]["score"] > ordered[1]["score"])
        if row[GEOMETRY] is not geometry or row[RETRIEVAL] is not retrieval:
            raise ValueError("STRESS_REPORTED_ENDPOINT_MISMATCH")
        groups[name][key] = row
    for name, rows in groups.items():
        if (len(rows) != 250 or len({k[0] for k in rows}) != 25
                or set(Counter(k[:2] for k in rows).values()) != {5}
                or set(Counter(k[0] for k in rows).values()) != {10}):
            raise ValueError("STRESS_INCOMPLETE_QUERY_GRID")
        for scenario in SCENARIOS:
            selected = [r for r in rows.values() if r["scenario"] == scenario]
            supplied = report["summaries"][name][scenario]
            if supplied["queries"] != len(selected) or any(
                supplied[metric] != sum(r[metric] for r in selected) for metric in (GEOMETRY, RETRIEVAL)
            ):
                raise ValueError("STRESS_SUMMARY_MISMATCH")
    return groups


def paired_gain(first, second, metric, repetitions):
    lineages = sorted({k[0] for k in first})
    differences = np.array([np.mean([int(second[k][metric]) - int(first[k][metric])
                                    for k in first if k[0] == lineage]) for lineage in lineages])
    rng = np.random.default_rng(26)
    samples = differences[rng.integers(0, len(lineages), (repetitions, len(lineages)))].mean(axis=1)
    return {"gain_percentage_points": float(differences.mean() * 100),
            "paired_lineage_bootstrap_95_percent": (np.percentile(samples, [2.5, 97.5]) * 100).tolist()}


def assess(baseline, candidate, repetitions=4000):
    if repetitions < 100:
        raise ValueError("TOO_FEW_BOOTSTRAP_REPETITIONS")
    base = checked_rows(baseline, ("sift", "aliked_lightglue"))
    preset = candidate["report"]["identity"]["preset"]
    if preset not in {"fast", "precise"}:
        raise ValueError("UNKNOWN_ROMA_STRESS_PRESET")
    if candidate["report"].get("completed_queries") != 250 or candidate["report"].get("target_queries") != 250:
        raise ValueError("STRESS_DENSE_RUN_NOT_COMPLETE")
    name = f"romav2_{preset}"
    dense = checked_rows(candidate, (name,))[name]
    shared = baseline["sources"]
    if not shared or any(candidate["sources"].get(key) != value for key, value in shared.items()):
        raise ValueError("STRESS_SHARED_PROTOCOL_CHANGED")
    for rows in base.values():
        if rows.keys() != dense.keys():
            raise ValueError("STRESS_QUERY_IDENTITIES_CHANGED")
        for key, row in rows.items():
            if [c["candidate_source"] for c in row["candidates"]] != [
                c["candidate_source"] for c in dense[key]["candidates"]
            ]:
                raise ValueError("STRESS_GALLERY_IDENTITIES_CHANGED")
    summaries, comparisons = {}, {}
    for scenario in (*SCENARIOS, "all"):
        filtered = {matcher: {k: r for k, r in rows.items() if scenario == "all" or k[2] == scenario}
                    for matcher, rows in {**base, name: dense}.items()}
        summaries[scenario] = {matcher: {"queries": len(rows),
            GEOMETRY: sum(r[GEOMETRY] for r in rows.values()),
            RETRIEVAL: sum(r[RETRIEVAL] for r in rows.values()),
            "p95_query_gallery_seconds": float(np.percentile([r["query_gallery_seconds"] for r in rows.values()], 95))}
            for matcher, rows in filtered.items()}
        comparisons[scenario] = {matcher: {metric: paired_gain(rows, filtered[name], metric, repetitions)
                                           for metric in (GEOMETRY, RETRIEVAL)}
                                 for matcher, rows in filtered.items() if matcher != name}
    return {"status": "PAIRED_STRESS_ASSESSMENT_COMPLETE", "candidate": name,
            "baseline_provenance": baseline["provenance"], "candidate_provenance": candidate["provenance"],
            "summaries": summaries, "candidate_minus_baseline": comparisons,
            "bootstrap_repetitions": repetitions, "bootstrap_seed": 26, "source_lineages": 25,
            "operating_point": None, "promotion": "HOLD", "real_box_validation": "NOT_RUN",
            "limits": ["Synthetic stress on selection only; 250 queries are derived from 50 images/25 objects.",
                       "Geometry uses the known source pair, not the reference selected by the model.",
                       "Exact-source retrieval is a proxy, not real wrong-face adjudication.",
                       "SIFT/LightGlue cache reference features; RoMa repeats both encodings per pair.",
                       "Confidence scores are not interchangeable or calibrated across matchers.",
                       "Intervals are descriptive, without multiple-comparison adjustment; no winner or deployment gate is selected."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=Path("artifacts/kaggle/matching-stress-v2"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = assess(load_bundle(args.baseline), load_bundle(args.candidate))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "candidate": report["candidate"],
                      "summaries": report["summaries"], "promotion": "HOLD"}))


if __name__ == "__main__":
    main()
