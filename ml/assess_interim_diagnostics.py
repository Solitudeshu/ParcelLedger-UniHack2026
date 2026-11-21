"""Assess collected interim diagnostics without reading images or selecting thresholds."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ml.assess_matching_stress import MANIFEST_SHA
from ml.evaluate_transformer_selection import MIN_PIXELS, MODES, THRESHOLD, summarize


def checked_file(folder, receipt, name):
    entries = [entry for entry in receipt["files"] if entry["name"] == name]
    if len(entries) != 1:
        raise ValueError("DIAGNOSTICS_RECEIPT_ENTRY_MISSING_OR_DUPLICATE")
    raw = (folder / name).read_bytes()
    if len(raw) != entries[0]["bytes"] or hashlib.sha256(raw).hexdigest() != entries[0]["sha256"]:
        raise ValueError("DIAGNOSTICS_ARTIFACT_HASH_MISMATCH")
    return raw


def validate_rows(rows, expected):
    grouped = {mode: {} for mode in MODES}
    for row in rows:
        mode, source = row["mode"], row["source"]
        if (mode not in MODES or source not in expected or source in grouped[mode]
                or (row["lineage"], row["scenario"]) != expected[source]):
            raise ValueError("DIAGNOSTICS_ROW_IDENTITY_MISMATCH")
        v, y, p, tp, fp, fn = [row[key] for key in
                             ("valid_pixels", "positive_pixels", "predicted_pixels", "tp", "fp", "fn")]
        if (any(type(value) is not int or value < 0 for value in (v, y, p, tp, fp, fn))
                or y > v or p > v or tp + fn != y or tp + fp != p or fp > v - y):
            raise ValueError("DIAGNOSTICS_PIXEL_COUNTS_INCONSISTENT")
        expected_flag = bool(p >= MIN_PIXELS) if v else None
        if row["image_flag"] is not expected_flag:
            raise ValueError("DIAGNOSTICS_FLAG_MISMATCH")
        metrics = {"positive_iou": tp / (tp + fp + fn) if y else None,
                   "positive_dice": 2 * tp / (2 * tp + fp + fn) if y else None,
                   "positive_recall": tp / (tp + fn) if y else None}
        for key, value in metrics.items():
            if (value is None and row[key] is not None) or (value is not None and
                    (row[key] is None or not math.isclose(row[key], value, rel_tol=1e-8, abs_tol=1e-10))):
                raise ValueError("DIAGNOSTICS_REPORTED_METRIC_MISMATCH")
        probability = row["target_mean_probability"]
        if ((y == 0 and probability is not None) or
                (y > 0 and (probability is None or not np.isfinite(probability) or not 0 <= probability <= 1))):
            raise ValueError("DIAGNOSTICS_INVALID_TARGET_PROBABILITY")
        grouped[mode][source] = row
    if any(set(group) != set(expected) for group in grouped.values()):
        raise ValueError("DIAGNOSTICS_INCOMPLETE_COHORT")
    for source in expected:
        if len({(group[source]["valid_pixels"], group[source]["positive_pixels"])
                for group in grouped.values()}) != 1:
            raise ValueError("DIAGNOSTICS_COUNTERFACTUAL_TARGET_CHANGED")
    return grouped


def cluster_mean_interval(values, repetitions=4000):
    """Resample source objects; preserve the image-weighted estimand."""
    if not values:
        return {"mean": None, "source_objects": 0, "bootstrap_95_percent": None}
    objects = sorted({obj for obj, _ in values})
    totals = np.array([sum(v for obj, v in values if obj == key) for key in objects])
    counts = np.array([sum(obj == key for obj, _ in values) for key in objects])
    samples = np.random.default_rng(26).integers(0, len(objects), (repetitions, len(objects)))
    estimates = totals[samples].sum(axis=1) / counts[samples].sum(axis=1)
    return {"mean": float(totals.sum() / counts.sum()), "source_objects": len(objects),
            "bootstrap_95_percent": np.percentile(estimates, [2.5, 97.5]).tolist()}


def assess_rows(rows, expected):
    grouped = validate_rows(rows, expected)
    sensitivity, flags = [], []
    for source, paired in grouped["paired"].items():
        counterfactual = grouped["query_as_reference"][source]
        if paired["positive_pixels"]:
            sensitivity.append((paired["lineage"], paired["target_mean_probability"] - counterfactual["target_mean_probability"]))
        if counterfactual["valid_pixels"]:
            flags.append((paired["lineage"], int(counterfactual["image_flag"])))
    return {**summarize(rows), "reference_probability_drop": cluster_mean_interval(sensitivity),
            "query_as_reference_flag_rate_all_usable": cluster_mean_interval(flags)}


def run(folder, manifest):
    folder, manifest = Path(folder), Path(manifest)
    receipt = json.loads(Path(str(folder) + "-outputs.json").read_bytes())
    if (receipt["collection_status"] != "COMPLETE" or receipt["run_status"] != "COMPLETE"
            or receipt["version"] != 1
            or receipt["reference"] != "solishu/notebook-unihack26-transformer-early-diagnostics"):
        raise ValueError("INTERIM_DIAGNOSTICS_COLLECTION_REQUIRED")
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA:
        raise ValueError("INTERIM_DIAGNOSTICS_MANIFEST_CHANGED")
    selection = sorted((json.loads(line) for line in raw.splitlines() if line), key=lambda r: r["image_path"])
    selection = [row for row in selection if row["role"] == "selection"]
    expected = {row["image_path"]: (row["lineage_id"], index % 6) for index, row in enumerate(selection)}
    if len(expected) != 1069 or len({x[0] for x in expected.values()}) != 26:
        raise ValueError("INTERIM_DIAGNOSTICS_COHORT_CHANGED")
    sources = json.loads(checked_file(folder, receipt, "source-hashes.json"))
    evaluator = Path(__file__).resolve().parent / "evaluate_transformer_selection.py"
    if sources.get("ml/evaluate_transformer_selection.py") != hashlib.sha256(evaluator.read_bytes()).hexdigest():
        raise ValueError("INTERIM_DIAGNOSTICS_EVALUATOR_SOURCE_CHANGED")
    results = {}
    for candidate in ("c2", "c3"):
        report = json.loads(checked_file(folder, receipt, f"diagnostics-{candidate}/selection-report.json"))
        rows = [json.loads(line) for line in checked_file(folder, receipt,
                f"diagnostics-{candidate}/selection-predictions.jsonl").splitlines()]
        training = Path(f"artifacts/kaggle/transformer-{candidate}-v1")
        training_receipt = json.loads(Path(str(training) + "-outputs.json").read_bytes())
        card = json.loads(checked_file(training, training_receipt, "training/model-card.json"))
        checkpoint = next(r for r in training_receipt["files"] if r["name"] == "training/best.pt")
        if (report["checkpoint_sha256"] != checkpoint["sha256"] or card["status"] != "PAUSED_SESSION_BUDGET"
                or report["status"] != "SELECTION_DIAGNOSTICS_COMPLETE"
                or report["training_phase"] != "INTERIM_SESSION_1_NOT_FINAL_MODEL"
                or report["scope"] != "SYNTHETIC_APPEARANCE_SELECTION_ONLY"
                or report["threshold"] != THRESHOLD or report["image_flag_min_pixels"] != MIN_PIXELS
                or report["test"] != "NOT_READ" or report["calibration"] != "NOT_RUN"
                or report["promotion"] != "HOLD" or report["real_box_validation"] != "NOT_RUN"
                or any(card.get(k) != value for k, value in report["identity"].items())):
            raise ValueError("INTERIM_DIAGNOSTICS_SCOPE_OR_IDENTITY_CHANGED")
        if any(sources.get(name) != sha for name, sha in card["source_hashes"].items()):
            raise ValueError("INTERIM_DIAGNOSTICS_TRAINING_SOURCE_CHANGED")
        result = assess_rows(rows, expected)
        result.update(checkpoint_sha256=report["checkpoint_sha256"], selected_epoch=report["selected_epoch"])
        results[candidate] = result
    return {"status": "INTERIM_REFERENCE_DIAGNOSTICS_ASSESSED", "candidates": results,
            "promotion": "HOLD", "calibration": "NOT_RUN", "test": "NOT_READ", "real_box_validation": "NOT_RUN",
            "bootstrap_repetitions": 4000, "bootstrap_unit": "source_object", "bootstrap_seed": 26,
            "provenance": {"reference": receipt["reference"], "version": receipt["version"],
                           "files": {r["name"]: r["sha256"] for r in receipt["files"]}},
            "limits": ["Interim checkpoints, not final training or production accuracy.",
                       "Known alignment and foreground; no upstream retrieval errors included.",
                       "Counterfactual query-as-reference should show no new change; original targets are used only to measure probability drop.",
                       "Constant-reference inputs are out of distribution, not a valid operating regime.",
                       "Diagnostic thresholds remain fixed and uncalibrated; intervals are descriptive."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", default="artifacts/kaggle/transformer-early-diagnostics-v1")
    parser.add_argument("--manifest", default="artifacts/kaggle/full-pixel-audit-v1/audit/images.jsonl")
    parser.add_argument("--output", type=Path, default=Path("artifacts/evaluation/interim-reference-diagnostics.json"))
    args = parser.parse_args()
    report = run(args.folder, args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "promotion": "HOLD"}))


if __name__ == "__main__":
    main()
