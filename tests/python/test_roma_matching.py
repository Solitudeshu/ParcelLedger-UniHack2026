import numpy as np
import pytest

from ml.roma_matching import validate_matches


def test_dense_matches_keep_direction_and_validate_confidence():
    a = np.array([[1, 2], [3, 4]], dtype=np.float64)
    b = a + [3, 9]
    first, second, overlap = validate_matches(a, b, np.array([[.2], [.9]]))
    assert first.dtype == np.float32 and np.array_equal(second - first, [[3, 9], [3, 9]])
    assert overlap.shape == (2,)
    for bad in (np.array([np.nan, .5]), np.array([-.1, .5]), np.array([.5, 1.1])):
        with pytest.raises(ValueError, match="NONFINITE_OR_INVALID_DENSE_MATCHES"):
            validate_matches(a, b, bad)
    with pytest.raises(ValueError, match="INVALID_DENSE_MATCH_SHAPES"):
        validate_matches(a, b[:1], np.ones(2))
