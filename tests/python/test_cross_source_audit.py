import hashlib
import io

import numpy as np
import pytest
from PIL import Image

from ml.data.cross_source_audit import exact_groups, fingerprint, screen


def encoded(pixels, format="PNG"):
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format=format)
    return buffer.getvalue()


def fp(raw):
    return fingerprint(raw, hashlib.sha256(raw).hexdigest())


def test_canonical_pixels_match_across_lossless_encodings_and_bind_shape():
    pixels = np.random.default_rng(26).integers(0, 256, (16, 32, 3), dtype=np.uint8)
    a, b = fp(encoded(pixels)), fp(encoded(pixels, "BMP"))
    assert a["sha256"] != b["sha256"]
    assert a["canonical_rgb_sha256"] == b["canonical_rgb_sha256"]
    assert a["phash64"] == b["phash64"] and a["dhash64"] == b["dhash64"]
    assert a["canonical_rgb_sha256"] != fp(encoded(pixels.reshape(32, 16, 3)))["canonical_rgb_sha256"]
    with pytest.raises(ValueError, match="SOURCE_IMAGE_BYTES_CHANGED"):
        fingerprint(encoded(pixels), "0" * 64)


def test_dhash_direction_is_explicit_and_bounded():
    gradient = np.broadcast_to(np.arange(32, dtype=np.uint8)[None, :, None] * 7, (16, 32, 3)).copy()
    assert fp(encoded(gradient))["dhash64"] == "ffffffffffffffff"
    assert fp(encoded(gradient[:, ::-1]))["dhash64"] == "0000000000000000"


def row(dataset, role, value=0):
    return {"dataset": dataset, "role": role, "phash64": f"{value:016x}", "dhash64": f"{value:016x}",
            "sha256": "same", "canonical_rgb_sha256": "same"}


def test_screen_counts_boundaries_deterministically_without_declaring_leakage():
    rows = [row("p", "train"), row("p", "train"), row("p", "test"), row("v", "train"),
            row("v", "test", (1 << 64) - 1)]
    result = screen(rows, sample_limit=1)
    assert result["candidate_pairs"] == 5
    assert result["boundary_comparisons"] == 9
    assert len(result["retained_candidates"]) == 3
    assert result == screen(rows, sample_limit=1)
    assert all(r["verdict"] == "UNREVIEWED_NOT_PROVEN_DUPLICATE" for r in result["retained_candidates"])
    assert all(g["cross_boundary"] for g in exact_groups(rows))
    assert not any(g["cross_boundary"] for g in exact_groups(rows[:2]))


def test_empty_and_threshold_edges():
    assert screen([])["candidate_pairs"] == 0
    assert screen([row("p", "train"), row("v", "train", 15)], threshold=4)["candidate_pairs"] == 1
    assert screen([row("p", "train"), row("v", "train", 31)], threshold=4)["candidate_pairs"] == 0
    with pytest.raises(ValueError, match="INVALID_DUPLICATE_SCREEN_PARAMETERS"):
        screen([], threshold=-1)
