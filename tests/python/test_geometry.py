from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from ml.features.geometry import describe, scalar_features, supported_projection
from services.worker.baseline import match_pair


@pytest.mark.parametrize("count", [0, 1, 3])
def test_insufficient_ransac_support_abstains_in_training_and_serving(monkeypatch, count):
    image = Image.open(Path("tests/fixtures/images/dispatch.png"))

    def degenerate(source, target, *args, **kwargs):
        mask = np.zeros((len(source), 1), np.uint8)
        mask[:count] = 1
        return np.eye(3), mask

    monkeypatch.setattr(cv2, "findHomography", degenerate)
    description = describe(image)
    features = scalar_features(description, description, 1.)
    assert np.all(features[[1, 2, 4, 5]] == 0)
    assert np.isnan(features[3])  # Missing, never a perfect zero-error match.
    match = match_pair(image, image)
    assert match["valid"] is False
    assert match["reason"] == "NONPLANAR_GEOMETRY"


def test_invalid_homography_and_projection_are_rejected(monkeypatch):
    points = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])
    mask = np.ones((4, 1), np.uint8)
    assert supported_projection(points, np.eye(3), None) is None
    assert supported_projection(points, np.eye(3), mask[:3]) is None
    assert supported_projection(points, np.full((3, 3), np.nan), mask) is None
    horizon = np.array([[1., 0., 1.], [0., 1., 0.], [1., 0., 0.]])
    assert supported_projection(points, horizon, mask) is None
    monkeypatch.setattr(cv2, "perspectiveTransform", lambda *args: None)
    assert supported_projection(points, np.eye(3), mask) is None
    monkeypatch.setattr(cv2, "perspectiveTransform", lambda *args: np.full((4, 1, 2), np.nan))
    assert supported_projection(points, np.eye(3), mask) is None


def test_supported_projection_preserves_correspondence():
    points = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])
    known, projected = supported_projection(points, np.eye(3), np.ones((4, 1), np.uint8))
    assert known.all()
    np.testing.assert_array_equal(projected, points)
