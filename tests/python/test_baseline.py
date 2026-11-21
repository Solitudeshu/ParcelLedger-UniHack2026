from pathlib import Path

from PIL import Image

from services.worker.baseline import analyze_images

ROOT = Path("tests/fixtures/images")


def analyze(name):
    return analyze_images(
        "query", Image.open(ROOT / name), [("ref", Image.open(ROOT / "dispatch.png"))]
    )


def test_identity_illumination_and_local_change():
    identity = analyze("unchanged.png")
    assert identity["match"]["status"] == "MATCHED_SUFFICIENT"
    assert identity["observations"][0]["state"] == "NO_VISIBLE_CHANGE"
    assert identity["match"]["match_probability"] is None
    illumination = analyze("lighting.png")
    assert illumination["observations"][0]["state"] == "NO_VISIBLE_CHANGE"
    changed = analyze("changed.png")
    assert changed["observations"][0]["state"] == "VISIBLE_CHANGE"
    assert all(
        0 <= coord <= 10000
        for point in changed["observations"][0]["query_polygon"]
        for coord in point
    )


def test_missing_reference_abstains():
    result = analyze_images("q", Image.open(ROOT / "changed.png"), [])
    assert result["match"]["status"] == "NO_RELIABLE_MATCH"
    assert result["match"]["reason_codes"] == ["MISSING_REFERENCE"]
    assert result["observations"][0]["state"] == "INCONCLUSIVE"


def test_textureless_abstains():
    assert analyze("no-reference.png")["match"]["status"] == "NO_RELIABLE_MATCH"
