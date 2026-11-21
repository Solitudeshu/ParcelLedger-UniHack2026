import cv2
import numpy as np
import pytest

from ml.evaluate_matching import corner_error, known_view
from ml.learned_matching import SIFTMatcher, fit_alignment


def test_alignment_has_correct_reference_to_query_direction():
    points = np.float32([[10, 10], [90, 10], [90, 90], [10, 90], [50, 50], [30, 70]])
    expected = np.float64([[.8, .03, 6], [.01, .9, 3], [.0001, -.0002, 1]])
    transformed = cv2.perspectiveTransform(points[:, None], expected)[:, 0]
    result = fit_alignment(points, transformed, (100, 100))
    assert result["inliers"] == 6 and result["score"] > 0
    assert corner_error(result["homography"], expected, (100, 100)) < 1e-3
    assert corner_error(np.linalg.inv(result["homography"]), expected, (100, 100)) > 1


def test_degenerate_and_blank_images_do_not_produce_alignment():
    points = np.float32([[0, 0], [10, 0], [20, 0], [30, 0]])
    assert fit_alignment(points, points, (100, 100))["homography"] is None
    matcher = SIFTMatcher()
    features = matcher.extract(np.zeros((64, 64, 3), np.uint8))
    rp, qp = matcher.match(features, features)
    assert fit_alignment(rp, qp, (64, 64))["score"] == 0
    with pytest.raises(ValueError, match="INVALID_MATCH_COORDINATES"):
        fit_alignment(np.zeros((3, 3)), np.zeros((3, 3)), (64, 64))


def test_sift_can_recover_fixed_synthetic_transform():
    rng = np.random.default_rng(26)
    image = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    image = cv2.GaussianBlur(image, (3, 3), .7)
    query, truth = known_view(image, 26)
    matcher = SIFTMatcher()
    rp, qp = matcher.match(matcher.extract(image), matcher.extract(query))
    fit = fit_alignment(rp, qp, query.shape)
    error = corner_error(fit["homography"], truth, query.shape)
    assert error is not None and error < 3
