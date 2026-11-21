import cv2
import numpy as np
import pytest

from ml.evaluate_matching_stress import (
    SCENARIOS,
    gallery_lineages,
    query_variant,
    visible_transfer_error,
)


def test_sparse_lineages_are_reported_and_not_silently_mixed_with_other_objects():
    rows = [{"lineage_id": k} for k in ["small"] * 2 + ["eligible"] * 4]
    eligible, excluded = gallery_lineages(rows)
    assert eligible == {"eligible": [2, 3, 4, 5]} and excluded == {"small": 2}
    with pytest.raises(ValueError, match="NO_FOUR_VIEW_LINEAGES"):
        gallery_lineages(rows[:2])


def test_known_geometry_and_observation_mask_across_stress_cases():
    rgb = np.random.default_rng(26).integers(0, 256, (128, 128, 3), dtype=np.uint8)
    for scenario in SCENARIOS:
        query, truth, visible = query_variant(rgb, scenario, 1)
        assert query.shape == rgb.shape and visible.shape == rgb.shape[:2]
        score = visible_transfer_error(truth, truth, visible)
        assert score["points"] > 100 and score["p95_px"] < 1e-3
        shifted = np.float64([[1, 0, 10], [0, 1, 0], [0, 0, 1]]) @ truth
        assert visible_transfer_error(shifted, truth, visible)["mean_px"] > 9.9
        if scenario == "occlusion":
            assert visible[64, 64] == 0
        if scenario == "close_up":
            # Query center maps inside the cropped reference region.
            point = cv2.perspectiveTransform(np.float32([[[64, 64]]]), np.linalg.inv(truth))[0, 0]
            assert np.all((point > 30) & (point < 100))
    assert visible_transfer_error(None, np.eye(3), np.ones((128, 128), np.uint8)) is None
    assert visible_transfer_error(np.eye(3), np.eye(3), np.zeros((128, 128), np.uint8)) is None
