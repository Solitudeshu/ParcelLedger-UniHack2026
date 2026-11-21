import json

import numpy as np
import pytest

from ml.inference.change import aligned_features, bundle_identity


def test_alignment_masks_exclude_invalid_pixels_and_normalize_lighting():
    rng = np.random.default_rng(26)
    before = rng.integers(20, 180, (256, 256, 3), dtype=np.uint8)
    query = np.clip(before.astype(np.float32)*1.1+12, 0, 255).astype(np.uint8)
    valid = np.full((256, 256), 255, np.uint8)
    valid[:60] = 0
    features, mask = aligned_features({"q": query, "r": before, "H": np.eye(3), "mask": valid, "valid_mask": valid})
    assert features.shape == (10, 128, 128)
    assert np.isfinite(features).all()
    assert features[6:9, mask].mean() < .01
    assert not mask[:32].any()
    np.testing.assert_array_equal(features[9], mask)
    assert np.all((features >= 0) & (features <= 1))


def test_change_bundle_identity_rejects_tampering(tmp_path):
    import hashlib
    (tmp_path / "change-head.pt").write_bytes(b"test fixture, not a checkpoint")
    card = {"model_version": "synthetic-change-head-v1", "source_kind": "SYNTHETIC", "threshold": .3,
            "checkpoint_sha256": hashlib.sha256((tmp_path / "change-head.pt").read_bytes()).hexdigest()}
    (tmp_path / "model-card.json").write_text(json.dumps(card))
    first = bundle_identity(tmp_path)
    card["threshold"] = .4
    (tmp_path / "model-card.json").write_text(json.dumps(card))
    assert bundle_identity(tmp_path)["model_card_sha256"] != first["model_card_sha256"]
    (tmp_path / "change-head.pt").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="CHECKSUM"):
        bundle_identity(tmp_path)


def test_empty_overlap_cannot_produce_neural_claims():
    image = np.ones((128, 128, 3), np.uint8)*80
    with pytest.raises(ValueError, match="INSUFFICIENT_VALID_AREA"):
        aligned_features({"q": image, "r": image, "H": np.eye(3), "mask": np.zeros((128, 128), np.uint8),
                          "valid_mask": np.ones((128, 128), np.uint8)*255})
