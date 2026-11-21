import numpy as np
import pytest

from ml.features.pair_features import ScalarNormalizer, pair_features


def test_feature_order_shape_and_missing_mask():
    rng = np.random.default_rng(26)
    scalars = rng.normal(size=(8, 12)).astype(np.float32)
    scalars[:, -1] = np.nan
    scalars[0, 3] = np.nan
    normalizer = ScalarNormalizer.fit(scalars, ["train"] * 8)
    embedding = rng.normal(size=(8, 384)).astype(np.float32)
    result = pair_features(embedding, embedding, scalars, normalizer)
    assert result.shape == (8, 1560)
    assert np.isfinite(result).all()
    np.testing.assert_allclose(result[:, 768:1152], 0)
    assert result[0, -9] == 1
    assert (result[:, -1] == 1).all()
    assert (result[:, -13] == 0).all()


def test_statistics_cannot_fit_validation_or_test():
    with pytest.raises(ValueError, match="TRAIN_ONLY"):
        ScalarNormalizer.fit(np.ones((2, 12)), ["train", "test"])


def test_invalid_embedding_does_not_get_silent_confidence():
    normalizer = ScalarNormalizer.fit(np.ones((2, 12)), ["train", "train"])
    with pytest.raises(ValueError, match="ZERO_EMBEDDING"):
        pair_features(np.zeros((2, 384)), np.ones((2, 384)), np.ones((2, 12)), normalizer)
