"""Assess saved selection correspondences without fitting a deployment threshold."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

MATCHERS = ("sift", "aliked_lightglue")
METRICS = ("top1", "correct_and_aligned_3px")


def ordered_candidates(row, without_reference=False):
    candidates = [c for c in row["candidates"]
                  if not without_reference or c["candidate_source"] != row["query_source"]]
    return sorted(candidates, key=lambda c: c["score"], reverse=True)


def validate(rows):
    groups = {name: {} for name in MATCHERS}
    for row in rows:
        name, query = row["matcher"], row["query_source"]
        if name not in groups or query in groups[name]:
            raise ValueError("DUPLICATE_OR_UNKNOWN_MATCHER_QUERY")
        candidates = row["candidates"]
        if (len(candidates) != 4 or len({c["candidate_source"] for c in candidates}) != 4
                or sum(c["candidate_source"] == query for c in candidates) != 1):
            raise ValueError("INVALID_GALLERY_OR_REFERENCE_IDENTITY")
        for c in candidates:
            if any(not np.isfinite(c[k]) or c[k] < 0 for k in ("score", "inliers", "coverage", "ratio")):
                raise ValueError("NONFINITE_OR_NEGATIVE_MATCH_STATISTIC")
            error = c["true_pair_corner_error_px"]
            if error is not None and (not np.isfinite(error) or error < 0):
                raise ValueError("INVALID_ALIGNMENT_ERROR")
        groups[name][query] = row
    if not groups[MATCHERS[0]] or set(groups[MATCHERS[0]]) != set(groups[MATCHERS[1]]):
        raise ValueError("UNPAIRED_QUERY_SETS")
    for query, row in groups[MATCHERS[0]].items():
        other = groups[MATCHERS[1]][query]
        if (row["lineage"] != other["lineage"]
                or {c["candidate_source"] for c in row["candidates"]}
                != {c["candidate_source"] for c in other["candidates"]}):
            raise ValueError("UNPAIRED_LINEAGE_OR_GALLERY")
    return groups


def outcome(row):
    best = ordered_candidates(row)[0]
    # Ties are ambiguous and cannot count as safe retrieval.
    ordered = ordered_candidates(row)
    correct = (best["score"] > 0 and best["score"] > ordered[1]["score"]
               and best["candidate_source"] == row["query_source"])
    error = best["true_pair_corner_error_px"]
    return {"top1": int(correct), "correct_and_aligned_3px": int(correct and error is not None and error <= 3)}


def accepted(ordered, minimum_score):
    best, second = ordered[:2]
    return (best["score"] >= minimum_score and best["inliers"] >= 8 and best["coverage"] >= .04
            and best["score"] > second["score"] and best["score"] >= 1.3 * second["score"])


def diagnostic_gate(rows, minimum_score):
    accepted_count = unsafe = absent_accepts = 0
    for row in rows:
        if accepted(ordered_candidates(row), minimum_score):
            accepted_count += 1
            unsafe += 1 - outcome(row)["correct_and_aligned_3px"]
        absent_accepts += int(accepted(ordered_candidates(row, without_reference=True), minimum_score))
    return {"minimum_score": minimum_score, "queries": len(rows), "accepted": accepted_count,
            "coverage": accepted_count / len(rows), "unsafe_accepted": unsafe,
            "unsafe_rate_among_accepted": unsafe / accepted_count if accepted_count else None,
            "reference_removed_accepts": absent_accepts, "reference_removed_queries": len(rows),
            "reference_removed_false_accept_rate": absent_accepts / len(rows)}


def compare(rows, repetitions=4000):
    groups = validate(rows)
    queries = sorted(groups[MATCHERS[0]])
    lineages = sorted({r["lineage"] for r in groups[MATCHERS[0]].values()})
    indices = {name: index for index, name in enumerate(lineages)}
    counts = np.zeros(len(lineages), dtype=int)
    sums = {name: np.zeros((len(lineages), len(METRICS)), dtype=int) for name in MATCHERS}
    for query in queries:
        i = indices[groups[MATCHERS[0]][query]["lineage"]]
        counts[i] += 1
        for name in MATCHERS:
            result = outcome(groups[name][query])
            sums[name][i] += [result[metric] for metric in METRICS]
    rng = np.random.default_rng(26)
    draws = rng.integers(0, len(lineages), (repetitions, len(lineages)))
    denominator = counts[draws].sum(axis=1)
    delta = sums[MATCHERS[1]] - sums[MATCHERS[0]]
    distribution = delta[draws].sum(axis=1) / denominator[:, None]
    comparison = {}
    for j, metric in enumerate(METRICS):
        comparison[metric] = {"sift_successes": int(sums[MATCHERS[0]][:, j].sum()),
                              "learned_successes": int(sums[MATCHERS[1]][:, j].sum()),
                              "queries": len(queries), "learned_minus_sift": float(delta[:, j].sum() / len(queries)),
                              "paired_lineage_bootstrap_95pct": np.percentile(distribution[:, j], [2.5, 97.5]).tolist()}
    return {"status": "SELECTION_ASSESSMENT_COMPLETE_NOT_CALIBRATED", "queries": len(queries),
            "source_lineages": len(lineages), "comparison": comparison,
            "diagnostic_gates": {name: [diagnostic_gate(list(groups[name].values()), score)
                                        for score in (5, 10, 25, 50)] for name in MATCHERS},
            "bootstrap": {"seed": 26, "repetitions": repetitions, "unit": "source_lineage", "paired": True},
            "promotion": "HOLD", "chosen_operating_point": None,
            "limits": ["Known planar transforms and supplied foreground crops, not real viewpoints.",
                       "Gallery has 4 images; removing the positive leaves only 3 distractors.",
                       "Reference-removed probe reuses cached correspondences; it is not an independent dataset.",
                       "Fixed score/inlier/coverage/margin grid is diagnostic, not calibrated confidence.",
                       "Unsafe means wrong reference or >3px/unknown alignment, not liability or damage.",
                       "Bootstrap clusters are source objects, not verified independent physical parcels.",
                       "SIFT here is a comparator, not the entire serving A pipeline."]}


def run(folder, output):
    folder, output = Path(folder), Path(output)
    report_path, rows_path = folder / "matching-report.json", folder / "matching-rows.json"
    source = json.loads(report_path.read_text(encoding="utf-8"))
    if (source["status"] != "SYNTHETIC_SELECTION_COMPARISON_COMPLETE"
            or source["test_pixels"] != "NOT_READ" or source["promotion"] != "HOLD"):
        raise ValueError("SOURCE_BENCHMARK_SCOPE_MISMATCH")
    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    result = compare(rows)
    for name in MATCHERS:
        if source["summaries"][name]["queries"] != result["queries"]:
            raise ValueError("SOURCE_BENCHMARK_QUERY_COUNT_MISMATCH")
    result["source_report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    result["source_rows_sha256"] = hashlib.sha256(rows_path.read_bytes()).hexdigest()
    result["source_manifest_sha256"] = source["manifest_sha256"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.folder, args.output)
