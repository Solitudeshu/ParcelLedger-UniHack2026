import copy
import json

import pytest

from ml.assess_matching_stress import (
    COMPLETE,
    GEOMETRY,
    MANIFEST_SHA,
    RETRIEVAL,
    SCENARIOS,
    assess,
    checked_rows,
    load_bundle,
)


def bundle(names, good):
    rows = []
    for matcher in names:
        for obj in range(25):
            for query in range(2):
                path = f"object{obj}/image{query}"
                for scenario in SCENARIOS:
                    candidates = [{"candidate_source": f"object{obj}/image{i}", "exact_source": i == query,
                                   "score": 10.0 if i == query else 1.0,
                                   "known_source_visible_transfer": {"p95_px": 1. if good else 5.} if i == query else None}
                                  for i in range(4)]
                    rows.append({"matcher": matcher, "lineage": str(obj), "query_source": path,
                                 "scenario": scenario, "candidates": candidates, "query_gallery_seconds": 1.,
                                 GEOMETRY: good, RETRIEVAL: True})
    report = {"status": COMPLETE, "source_lineages": 25, "source_query_images": 50,
              "manifest_sha256": MANIFEST_SHA, "test_pixels": "NOT_READ", "calibration": "NOT_RUN", "promotion": "HOLD",
              "summaries": {name: {s: {"queries": 50, GEOMETRY: 50 if good else 0, RETRIEVAL: 50}
                                   for s in SCENARIOS} for name in names}}
    if names == ("romav2_fast",):
        report.update(completed_queries=250, target_queries=250)
        report["identity"] = {key: report[key] for key in ("source_lineages", "source_query_images", "manifest_sha256")}
        report["identity"]["preset"] = "fast"
    return {"report": report, "rows": rows, "sources": {"protocol.py": "same"}, "provenance": {}}


def test_stress_pairing_preserves_geometry_endpoint_and_cluster_unit():
    base, candidate = bundle(("sift", "aliked_lightglue"), False), bundle(("romav2_fast",), True)
    result = assess(base, candidate, repetitions=100)
    assert result["promotion"] == "HOLD" and result["operating_point"] is None
    assert result["source_lineages"] == 25
    assert result["candidate_minus_baseline"]["blur"]["sift"][GEOMETRY] == {
        "gain_percentage_points": 100., "paired_lineage_bootstrap_95_percent": [100., 100.]}
    assert result["candidate_minus_baseline"]["all"]["sift"][RETRIEVAL]["gain_percentage_points"] == 0.


def test_incomplete_or_falsely_reported_geometry_cannot_pass():
    candidate = bundle(("romav2_fast",), True)
    candidate["report"]["status"] = "PARTIAL_SESSION_BUDGET_REQUIRES_REVIEW"
    with pytest.raises(ValueError, match="SCOPE_OR_COMPLETION"):
        checked_rows(candidate, ("romav2_fast",))
    candidate["report"]["status"] = COMPLETE
    candidate["rows"][0]["candidates"][0]["known_source_visible_transfer"]["p95_px"] = 5.
    with pytest.raises(ValueError, match="REPORTED_ENDPOINT"):
        checked_rows(candidate, ("romav2_fast",))
    candidate["rows"][0]["candidates"][0]["known_source_visible_transfer"]["p95_px"] = float("nan")
    with pytest.raises(ValueError, match="INVALID_GEOMETRY"):
        checked_rows(candidate, ("romav2_fast",))


def test_protocol_or_gallery_drift_cannot_be_compared():
    base, candidate = bundle(("sift", "aliked_lightglue"), False), bundle(("romav2_fast",), True)
    changed = copy.deepcopy(candidate)
    changed["sources"]["protocol.py"] = "different"
    with pytest.raises(ValueError, match="SHARED_PROTOCOL_CHANGED"):
        assess(base, changed, 100)
    candidate["rows"][0]["candidates"][3]["candidate_source"] = "other-object/image3"
    with pytest.raises(ValueError, match="GALLERY_IDENTITIES_CHANGED"):
        assess(base, candidate, 100)


def test_collection_hash_mismatch_is_rejected(tmp_path):
    folder = tmp_path / "result"
    folder.mkdir()
    (folder / "source-hashes.json").write_text("{}")
    (tmp_path / "result-outputs.json").write_text(json.dumps({
        "collection_status": "COMPLETE", "run_status": "COMPLETE", "files": [
            {"name": "source-hashes.json", "bytes": 2, "sha256": "wrong"}]}))
    with pytest.raises(ValueError, match="ARTIFACT_HASH_MISMATCH"):
        load_bundle(folder)
