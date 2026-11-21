import copy

import pytest

from ml.assess_matching import MATCHERS, compare, diagnostic_gate, outcome


def sample(matcher, query, lineage, error=1):
    candidates = [{"candidate_source": source, "score": score, "inliers": 20,
                   "coverage": .3, "ratio": .8, "true_pair_corner_error_px": error if source == query else None}
                  for source, score in ((query, 30), ("wrong-1", 2), ("wrong-2", 1), ("wrong-3", .5))]
    return {"matcher": matcher, "query_source": query, "lineage": lineage, "candidates": candidates}


def test_confident_wrong_or_bad_alignment_is_unsafe_and_no_reference_is_measured():
    good = sample("sift", "query", "box")
    assert diagnostic_gate([good], 10)["unsafe_accepted"] == 0
    assert diagnostic_gate([good], 10)["reference_removed_accepts"] == 0
    wrong = copy.deepcopy(good)
    wrong["candidates"][1]["score"] = 100
    report = diagnostic_gate([wrong], 10)
    assert report["coverage"] == 1 and report["unsafe_accepted"] == 1
    assert report["reference_removed_accepts"] == 1
    misaligned = sample("sift", "query", "box", error=10)
    assert diagnostic_gate([misaligned], 10)["unsafe_accepted"] == 1
    tied = copy.deepcopy(good)
    tied["candidates"][1]["score"] = 30
    assert outcome(tied)["top1"] == 0


def test_paired_cluster_bootstrap_and_identity_checks():
    rows = [sample(matcher, f"q{i}", f"box{i // 2}") for matcher in MATCHERS for i in range(4)]
    result = compare(rows, repetitions=100)
    assert result["source_lineages"] == 2
    assert result["comparison"]["top1"]["paired_lineage_bootstrap_95pct"] == [0, 0]
    assert result["chosen_operating_point"] is None and result["promotion"] == "HOLD"
    rows[-1]["lineage"] = "another-box"
    with pytest.raises(ValueError, match="UNPAIRED_LINEAGE_OR_GALLERY"):
        compare(rows)
    with pytest.raises(ValueError, match="UNPAIRED_QUERY_SETS"):
        compare(rows[:4])


def test_nonfinite_statistic_is_rejected():
    rows = [sample(matcher, "query", "box") for matcher in MATCHERS]
    rows[0]["candidates"][0]["score"] = float("nan")
    with pytest.raises(ValueError, match="NONFINITE_OR_NEGATIVE_MATCH_STATISTIC"):
        compare(rows)
