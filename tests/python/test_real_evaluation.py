import copy

import pytest

from ml.eval.real_boxes import evaluate
from scripts.adjudicate_data import adjudicate
from scripts.evaluate_real import freeze, predict
from scripts.real_data import canonical, digest, review
from test_real_collection import collection

POLYGON = [[[.2,.2],[.7,.2],[.7,.7],[.2,.7]]]


def seal(value, field):
    value[field] = digest(canonical({k:v for k,v in value.items() if k != field}))
    return value


def annotated(manifest):
    rows = copy.deepcopy(manifest["pairs"])
    for row in rows:
        same = row["query_lineage_id"] == row["reference_lineage_id"]
        row.update(correspondence="MATCH" if same else "WRONG_OBJECT", sufficiency="SUFFICIENT" if same else "INSUFFICIENT",
                   observable_change="CHANGED" if same else "UNKNOWN", change_polygons=copy.deepcopy(POLYGON) if same else [])
    return [{"manifest_sha256":manifest["manifest_sha256"], "annotator_id":reviewer,"annotations":copy.deepcopy(rows)} for reviewer in ("a","b")]


def setup(tmp_path):
    manifest = collection(tmp_path, count=10)
    submissions = annotated(manifest)
    labels = adjudicate(manifest,submissions,{"manifest_sha256":manifest["manifest_sha256"],"adjudicator_id":"c","resolutions":[]})
    protocol = seal(freeze(manifest,"baseline","test"),"protocol_sha256")
    by_query = {}
    for row in labels["pairs"]:
        if row["split"] == "test":
            by_query.setdefault(row["query_id"],[]).append(row)
    queries = []
    for query,rows in by_query.items():
        rows.sort(key=lambda row:row["correspondence"] != "MATCH")
        queries.append({"query_id":query,"ranking":[r["reference_id"] for r in rows],"selected_reference_id":rows[0]["reference_id"],
                        "accepted":True,"change":{"state":"EVALUATED","polygons":copy.deepcopy(POLYGON)}})
    predictions = seal({"manifest_sha256":manifest["manifest_sha256"],"protocol_sha256":protocol["protocol_sha256"],"queries":queries},"predictions_sha256")
    return manifest, labels, predictions, protocol


def test_adjudication_retains_originals_and_requires_complete_reasoned_decisions(tmp_path):
    manifest = collection(tmp_path)
    submissions = annotated(manifest)
    row = submissions[1]["annotations"][0]
    row.update(correspondence="UNKNOWN",sufficiency="UNKNOWN",observable_change="UNKNOWN",change_polygons=[])
    pending = review(manifest,submissions)
    assert pending["conflicts"] == [row["pair_id"]]
    decision = {"manifest_sha256":manifest["manifest_sha256"],"adjudicator_id":"c","resolutions":[]}
    with pytest.raises(ValueError,match="EXACTLY_PENDING"):
        adjudicate(manifest,submissions,decision)
    decision["resolutions"] = [{**submissions[0]["annotations"][0],"reason":"Fixture reviewer resolved this deliberate test conflict."}]
    result = adjudicate(manifest,submissions,decision)
    assert result["submissions"][1]["annotations"][0]["correspondence"] == "UNKNOWN"
    assert result["pairs"][0]["correspondence"] == "MATCH"
    assert result["rights_review"] == "PENDING"
    decision["resolutions"][0]["reason"] = ""
    with pytest.raises(ValueError,match="REASON_REQUIRED"):
        adjudicate(manifest,submissions,decision)


def test_unknown_change_and_missing_polygon_are_not_final_labels(tmp_path):
    manifest = collection(tmp_path)
    submissions = annotated(manifest)
    for sub in submissions:
        sub["annotations"][0]["change_polygons"] = []
    assert review(manifest,submissions)["unresolved"]
    for sub in submissions:
        sub["annotations"][0]["observable_change"] = "UNKNOWN"
    assert review(manifest,submissions)["unresolved"]


def test_metrics_do_not_turn_abstention_into_accuracy_or_fixture_into_real_data(tmp_path):
    manifest,labels,predictions,protocol = setup(tmp_path)
    result = evaluate(manifest,labels,predictions,protocol)
    assert result["physical_box_count"] == 0
    assert result["metrics"]["positive_mean_iou"]["value"] == 1
    assert result["metrics"]["recall_at_1"]["value"] == 1
    for query in predictions["queries"]:
        query.update(accepted=False,change={"state":"ABSTAIN","polygons":[]})
    seal(predictions,"predictions_sha256")
    result = evaluate(manifest,labels,predictions,protocol)
    assert result["metrics"]["coverage"]["value"] == 0
    assert result["metrics"]["unsafe_fraction_of_accepts"]["value"] is None
    assert result["metrics"]["positive_pixel_recall"]["value"] == 0


def test_evaluator_rejects_dropped_queries_wrong_hash_and_changed_identity(tmp_path):
    manifest,labels,predictions,protocol = setup(tmp_path)
    invalid = copy.deepcopy(predictions); invalid["queries"] = []
    with pytest.raises(ValueError,match="SHA256_MISMATCH"):
        evaluate(manifest,labels,invalid,protocol)
    seal(invalid,"predictions_sha256")
    with pytest.raises(ValueError,match="MISSING_OR_DUPLICATE_QUERIES"):
        evaluate(manifest,labels,invalid,protocol)
    labels["pairs"][0]["reference_id"] = "tampered"
    seal(labels,"labels_sha256")
    with pytest.raises(ValueError,match="LABEL_IDENTITY_CHANGED"):
        evaluate(manifest,labels,predictions,protocol)


def test_unsafe_accepts_remain_errors_even_when_returned_polygon_looks_correct(tmp_path):
    manifest,labels,predictions,protocol = setup(tmp_path)
    for query in predictions["queries"]:
        query["ranking"].reverse()
        query["selected_reference_id"] = query["ranking"][0]
    seal(predictions,"predictions_sha256")
    result = evaluate(manifest,labels,predictions,protocol)
    assert result["metrics"]["coverage"]["value"] == 1
    assert result["metrics"]["unsafe_fraction_of_accepts"]["value"] == 1
    assert result["metrics"]["positive_mean_iou"]["value"] is None


def test_ignore_region_excludes_uncertain_pixels_from_segmentation(tmp_path):
    manifest,labels,predictions,protocol = setup(tmp_path)
    for row in labels["pairs"]:
        row["ignore_polygons"] = copy.deepcopy(POLYGON)
    seal(labels,"labels_sha256")
    result = evaluate(manifest,labels,predictions,protocol)
    assert result["metrics"]["positive_mean_iou"]["value"] is None
    assert all(r["all_positive_pixels_ignored"] == 1 for r in result["queries"])


def test_prediction_runner_checks_frozen_identity_and_original_image_bytes(tmp_path):
    manifest = collection(tmp_path)
    protocol = seal(freeze(manifest,"baseline","test"),"protocol_sha256")
    result = predict(manifest,protocol,tmp_path)
    assert result["queries"] and result["source_kind"] == "SOFTWARE_FIXTURE"
    assert all(not q["accepted"] for q in result["queries"])
    target = next(a for a in manifest["assets"] if a["split"] == "test" and a["stage"] == "receipt")
    (tmp_path/target["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError,match="IMAGE_CHECKSUM"):
        predict(manifest,protocol,tmp_path)
    protocol["identity"]["services/worker/baseline.py"] = "0"*64
    seal(protocol,"protocol_sha256")
    with pytest.raises(ValueError,match="FROZEN_PROTOCOL_CHANGED"):
        predict(manifest,protocol,tmp_path)
