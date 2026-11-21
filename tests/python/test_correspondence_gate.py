import cv2
import numpy as np

from ml.correspondence_gate import decide, extract_evidence


def fixture():
    rng = np.random.default_rng(26)
    image = rng.integers(20, 230, (256, 256, 3), dtype=np.uint8)
    image = cv2.GaussianBlur(image, (5, 5), .8)
    xx, yy = np.meshgrid(np.arange(5, 252, 12), np.arange(5, 252, 12))
    points = np.stack([xx.ravel(), yy.ravel()], axis=1).astype(np.float32)
    return image, points, {'homography': np.eye(3)}


def test_same_pixels_brightness_and_small_change_pass():
    image, points, fit = fixture()
    query = np.clip(image * .8 + 10, 0, 255).astype(np.uint8)
    query[100:120, 100:120] = 30
    features, aligned, valid = extract_evidence(image, query, points, points, fit)
    assert decide(features)['accepted']
    assert aligned.shape == image.shape and valid.sum() > 0
    assert not decide(features)['proves_same_physical_box']


def test_wrong_texture_rejected_even_with_fabricated_perfect_geometry():
    image, points, fit = fixture()
    other = np.roll(image, 60, axis=1)
    features, _, _ = extract_evidence(image, other, points, points, fit)
    assert not decide(features)['accepted']
    assert 'APPEARANCE_MISMATCH' in decide(features)['reason_codes']


def test_blank_image_and_small_overlap_abstain():
    image, points, fit = fixture()
    blank = np.full_like(image, 150)
    features, _, _ = extract_evidence(blank, blank, points, points, fit)
    assert 'LOW_TEXTURE' in decide(features)['reason_codes']
    points = points[(points[:, 0] < 60) & (points[:, 1] < 60)]
    features, _, valid = extract_evidence(image, image, points, points, fit)
    assert not decide(features)['accepted'] and valid.sum() < 60 * 60


def test_missing_or_nonfinite_inputs_fail_closed():
    image, points, _ = fixture()
    features, aligned, valid = extract_evidence(image, image, points, points, {'homography': None})
    assert aligned is None and not valid.any() and not decide(features)['accepted']
    features['inliers'] = float('nan')
    assert not decide(features)['accepted']
