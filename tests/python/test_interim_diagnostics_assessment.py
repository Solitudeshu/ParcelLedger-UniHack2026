import copy

import numpy as np
import pytest

from ml.assess_interim_diagnostics import assess_rows, validate_rows
from ml.evaluate_transformer_selection import MODES, pixel_metrics


def data(reference_dependent):
    rows, expected = [], {}
    for index in range(4):
        source = str(index)
        expected[source] = (f"box{index // 2}", index % 6)
        for mode in MODES:
            score = .0 if reference_dependent and mode == "query_as_reference" else .9
            metric = pixel_metrics(np.full((8, 8), score), np.ones((8, 8)), np.ones((8, 8)))
            rows.append({"source": source, "lineage": expected[source][0], "scenario": index % 6,
                         "mode": mode, **metric})
    return rows, expected


def test_reference_ignoring_model_cannot_gain_from_counterfactual_metrics():
    result = assess_rows(*data(False))
    assert result["reference_probability_drop"]["mean"] == 0
    assert result["query_as_reference_flag_rate_all_usable"]["mean"] == 1
    assert result["reference_probability_drop"]["source_objects"] == 2
    dependent = assess_rows(*data(True))
    assert dependent["reference_probability_drop"]["mean"] == pytest.approx(.9)
    assert dependent["query_as_reference_flag_rate_all_usable"]["mean"] == 0


def test_missing_counterfactual_or_target_relabelling_is_rejected():
    rows, expected = data(True)
    missing = copy.deepcopy(rows)
    missing.pop()
    with pytest.raises(ValueError, match="INCOMPLETE_COHORT"):
        validate_rows(missing, expected)
    # A different valid mask would make the paired comparison invalid even if
    # the individual pixel metrics are internally consistent.
    rows[1]["valid_pixels"] += 1
    with pytest.raises(ValueError, match="COUNTERFACTUAL_TARGET_CHANGED"):
        validate_rows(rows, expected)


def test_inconsistent_counts_and_nan_are_rejected():
    rows, expected = data(True)
    rows[0]["tp"] += 1
    with pytest.raises(ValueError, match="PIXEL_COUNTS_INCONSISTENT"):
        validate_rows(rows, expected)
    rows, expected = data(True)
    rows[0]["target_mean_probability"] = float("nan")
    with pytest.raises(ValueError, match="INVALID_TARGET_PROBABILITY"):
        validate_rows(rows, expected)
