import copy

import pytest

from ml.data.labels import locked_split, supervision, validate_pairs


def pair(**kwargs):
    return {"pair_id": "p1", "query_id": "q1", "split": "train", "query_lineage_id": "box1",
            "reference_lineage_id": "box1", "query_sha256": "a" * 64, "reference_sha256": "b" * 64,
            "source_kind": "REAL", "source_ref": "test-only", "license_ref": "test-only",
            "annotator_id": "reviewer1", "correspondence": "MATCH", "sufficiency": "SUFFICIENT",
            "observable_change": "UNCHANGED", "change_polygons": [], "ignore_polygons": [],
            "reason_codes": [], **kwargs}


def test_locked_split_is_order_invariant_and_exact_for_120_boxes():
    boxes = [f"box-{i}" for i in range(120)]
    manifest = locked_split(boxes)
    assert manifest == locked_split(list(reversed(boxes)))
    assert [list(manifest["lineages"].values()).count(name) for name in ("train", "validation", "test")] == [72, 24, 24]


def test_cross_split_negative_reference_is_rejected():
    result = validate_pairs([pair(reference_lineage_id="box2", correspondence="WRONG_OBJECT",
                                  sufficiency="UNKNOWN", observable_change="UNKNOWN")],
                            {"lineages": {"box1": "train", "box2": "test"}})
    assert result["gate"] == "FAIL"
    assert any(e["code"] == "REFERENCE_LINEAGE_SPLIT_MISMATCH" for e in result["errors"])


def test_duplicate_content_cannot_leak_even_with_different_box_ids():
    second = pair(pair_id="p2", query_id="q2", split="test", query_lineage_id="box2", reference_lineage_id="box2")
    result = validate_pairs([pair(), second], {"lineages": {"box1": "train", "box2": "test"}})
    assert any(e["code"] == "IMAGE_HASH_SPLIT_LEAKAGE" for e in result["errors"])


def test_unknown_labels_do_not_become_negative_training_targets():
    labels = supervision(pair(correspondence="UNKNOWN", sufficiency="UNKNOWN", observable_change="UNKNOWN"))
    assert not labels["same_region_mask"]
    assert not labels["sufficiency_mask"]
    assert not labels["change_mask"]


@pytest.mark.parametrize("updates,code", [
    ({"sufficiency": "INSUFFICIENT"}, "CHANGE_REQUIRES_SUFFICIENT_MATCH"),
    ({"correspondence": "WRONG_REGION"}, "SUFFICIENT_REQUIRES_MATCH"),
    ({"change_polygons": [[[0, 0], [1, 1], [2, 0]]]}, "INVALID_NORMALIZED_POLYGON"),
    ({"ignore_polygons": [[[0, 0], [0, 0], [0, 0]]]}, "INVALID_NORMALIZED_POLYGON"),
])
def test_inconsistent_or_out_of_bounds_supervision_is_rejected(updates, code):
    result = validate_pairs([pair(**updates)], {"lineages": {"box1": "train"}})
    assert any(e["code"] == code for e in result["errors"])


def test_valid_record_passes_without_modification():
    record = pair()
    original = copy.deepcopy(record)
    result = validate_pairs([record], {"lineages": {"box1": "train"}})
    assert result["gate"] == "PASS"
    assert record == original
