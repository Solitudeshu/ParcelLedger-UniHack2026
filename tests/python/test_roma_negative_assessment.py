import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_stress_assessment import bundle

from ml.assess_roma_negatives import COMPLETE, MANIFEST_SHA, assess, load_negatives
from ml.roma_matching import REVISION, WEIGHTS_SHA


def inputs():
    positive = bundle(("romav2_fast",), True)
    positive["report"]["identity"].update(revision=REVISION, weights_sha256=WEIGHTS_SHA)
    positive["sources"] = {name: "same" for name in (
        "ml/evaluate_matching_stress.py", "ml/learned_matching.py", "ml/roma_matching.py",
        "ml/data/parcel_pairs_v2.py", "ml/evaluate_matching.py", "ml/features/geometry.py")}
    negative = {"sources": {**positive["sources"], "ml/evaluate_roma_negatives.py": "negative"},
                "provenance": {}, "rows": [], "report": {
                    "status": COMPLETE, "manifest_sha256": MANIFEST_SHA, "preset": "fast",
                    "revision": REVISION, "weights_sha256": WEIGHTS_SHA, "test": "NOT_READ",
                    "calibration": "NOT_RUN", "promotion": "HOLD", "gallery_seed": 2601,
                    "source_lineages": 25, "source_query_images": 50, "completed_queries": 250,
                    "target_queries": 250, "label": "FOUR_DISTINCT_OTHER_DECLARED_SOURCE_OBJECTS",
                    "protocol_source_sha256": "same", "negative_source_sha256": "negative"}}
    for row in positive["rows"]:
        for candidate in row["candidates"]:
            candidate.update(inliers=100, coverage=.8)
        obj = int(row["lineage"])
        negative["rows"].append({
            **{k: row[k] for k in ("query_source", "lineage", "scenario", "query_gallery_seconds")},
            "label": "NO_CORRECT_OBJECT_IN_GALLERY",
            "candidates": [{"candidate_source": f"object{(obj+i)%25}/image0", "lineage": str((obj+i)%25),
                            "score": 100. if i == 1 else 1., "coverage": .8, "ratio": .9, "inliers": 100,
                            "sampled_matches": 1600, "overlap_mean": .9, "overlap_median": .95,
                            "overlap_is_calibrated": False} for i in range(1, 5)],
        })
    return positive, negative


def test_high_confidence_wrong_object_is_counted_as_error_not_promoted():
    result = assess(*inputs())
    overall = result["summaries"]["all"]
    assert overall["queries"] == 250
    assert overall["positive_top_score_greater_than_negative_top"] == 0
    assert all(row["wrong_object_gallery_accepted"] == 250 for row in overall["fixed_legacy_diagnostic_grid"])
    assert result["operating_point"] is None and result["promotion"] == "HOLD"


def test_incomplete_or_relabelled_negative_cannot_pass():
    positive, negative = inputs()
    partial = copy.deepcopy(negative)
    partial["rows"].pop()
    with pytest.raises(ValueError, match="INCOMPLETE_QUERY_GRID"):
        assess(positive, partial)
    wrong_label = negative["rows"][0]["candidates"][0]
    wrong_label["candidate_source"] = positive["rows"][0]["query_source"]
    with pytest.raises(ValueError, match="GALLERY_CONTAINS_WRONG_LABEL"):
        assess(positive, negative)


def test_protocol_drift_and_nonfinite_confidence_rejected():
    positive, negative = inputs()
    negative["sources"]["ml/roma_matching.py"] = "changed"
    with pytest.raises(ValueError, match="SHARED_PROTOCOL_CHANGED"):
        assess(positive, negative)
    negative["sources"]["ml/roma_matching.py"] = "same"
    negative["rows"][0]["candidates"][0]["overlap_mean"] = float("nan")
    with pytest.raises(ValueError, match="INVALID_MATCH_STATISTICS"):
        assess(positive, negative)


def test_negative_receipt_is_bound_to_actual_bytes(tmp_path):
    _, negative = inputs()
    folder = tmp_path / "negative"
    files = []
    for key, name in (("sources", "source-hashes.json"), ("report", "negatives/negative-report.json"),
                      ("rows", "negatives/negative-rows.json")):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(negative[key]).encode()
        path.write_bytes(raw)
        files.append({"name": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    Path(str(folder) + "-outputs.json").write_text(json.dumps({
        "collection_status": "COMPLETE", "run_status": "COMPLETE", "version": 1,
        "reference": "solishu/notebook-unihack26-romav2-negative-selection", "files": files}))
    assert len(load_negatives(folder)["rows"]) == 250
    (folder / "negatives/negative-rows.json").write_text("[]")
    with pytest.raises(ValueError, match="ARTIFACT_HASH_MISMATCH"):
        load_negatives(folder)
