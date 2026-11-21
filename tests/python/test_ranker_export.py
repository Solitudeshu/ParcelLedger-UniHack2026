import hashlib
import json

import numpy as np
import pytest

from ml.features.pair_features import FEATURE_VERSION
from ml.inference.ranker import SHAPES, NumpyRanker, gelu


def fixture_bundle(path):
    # Deliberately artificial weights test serialization only, never model quality.
    np.savez_compressed(path / "ranker.npz", **{key: np.zeros(shape, np.float32) for key, shape in SHAPES.items()})
    (path / "normalizer.json").write_text(json.dumps({"version": FEATURE_VERSION, "fit_split": "train",
        "mean": [0.]*12, "scale": [1.]*12}))
    card = {"feature_version": FEATURE_VERSION, "temperatures": [1., 1.], "threshold": .9,
            "checkpoint_sha256": hashlib.sha256((path / "ranker.npz").read_bytes()).hexdigest(),
            "normalizer_sha256": hashlib.sha256((path / "normalizer.json").read_bytes()).hexdigest()}
    (path / "model-card.json").write_text(json.dumps(card))


def test_numpy_export_rejects_tampered_weights_and_nonfinite_features(tmp_path):
    fixture_bundle(tmp_path)
    ranker = NumpyRanker(tmp_path)
    assert all(np.all(value == 0) for value in ranker.predict(np.ones((2, 1560))).values())
    with pytest.raises(ValueError, match="INPUT_INVALID"):
        ranker.predict(np.full((1, 1560), np.nan))
    with (tmp_path / "ranker.npz").open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(ValueError, match="CHECKSUM"):
        NumpyRanker(tmp_path)


def test_exact_gelu_analytic_values():
    result = gelu(np.array([-1., 0., 1.], np.float32))
    np.testing.assert_allclose(result, [-.1586552539, 0., .8413447461], atol=1e-7)


def test_shadow_cannot_be_enabled_in_nonlocal_environment(monkeypatch):
    from types import SimpleNamespace
    from services.worker import shadow
    monkeypatch.setattr(shadow, "settings", lambda: SimpleNamespace(shadow_ranker_path="model", app_env="production"))
    with pytest.raises(ValueError, match="LOCAL_ONLY"):
        shadow.checkpoint_identity()
