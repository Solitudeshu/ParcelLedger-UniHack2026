from services.worker.recapture import instructions


def test_missing_before_image_requests_existing_archive_not_fabricated_capture():
    steps = instructions({"status": "NO_RELIABLE_MATCH", "reason_codes": ["MISSING_REFERENCE"]})
    assert len(steps) == 1
    assert steps[0]["responsible_party"] == "MERCHANT"
    assert steps[0]["action"] == "ADD_DISPATCH_VIEW"


def test_quality_guidance_is_specific_and_sufficient_evidence_has_no_recapture():
    base = {"status": "MATCHED_INSUFFICIENT", "reason_codes": ["GLARE", "BLUR"], "reference_asset_id": "ref"}
    assert {step["action"] for step in instructions(base)} == {"REMOVE_GLARE", "SHARP_DETAIL"}
    assert all(step["reference_asset_id"] == "ref" for step in instructions(base))
    assert instructions({**base, "status": "MATCHED_SUFFICIENT"}) == []
