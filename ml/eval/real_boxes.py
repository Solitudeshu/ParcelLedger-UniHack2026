"""Query-level evaluation; UNKNOWN labels never become negative examples."""
from collections import defaultdict

import cv2
import numpy as np

from ml.data.labels import valid_polygons, validate_pairs
from scripts.real_data import canonical, digest, resolved


def verify_hash(value, field):
    if value.get(field) != digest(canonical({k:v for k,v in value.items() if k != field})):
        raise ValueError(field.upper()+"_MISMATCH")


def polygon_mask(polygons, width, height):
    if not valid_polygons(polygons):
        raise ValueError("INVALID_PREDICTION_POLYGONS")
    scale = min(1., 512/max(width, height))
    w, h = max(2, round(width*scale)), max(2, round(height*scale))
    mask = np.zeros((h, w), np.uint8)
    for polygon in polygons:
        cv2.fillPoly(mask, [np.rint(np.asarray(polygon)*[w-1, h-1]).astype(np.int32)], 1)
    return mask.astype(bool)


def ratio(rows, numerator, denominator):
    n = sum(row.get(numerator, 0) for row in rows)
    d = sum(row.get(denominator, 0) for row in rows)
    return {"value":n/d if d else None, "numerator":n, "denominator":d}


METRICS = {
    "recall_at_1":("top1", "rank_eligible"), "recall_at_3":("top3", "rank_eligible"),
    "mrr":("reciprocal_rank", "rank_eligible"), "coverage":("accepted", "query_count"),
    "unsafe_fraction_of_accepts":("unsafe", "accepted"),
    "unsafe_fraction_of_all_queries":("unsafe", "query_count"),
    "sufficient_query_recall":("safe_accept", "sufficient_eligible"),
    "positive_mean_iou":("iou", "positive_mask_count"),
    "positive_mean_dice":("dice", "positive_mask_count"),
    "positive_pixel_recall":("tp_pixels", "gt_pixels"),
    "positive_image_recall":("positive_detected", "positive_image_count"),
    "negative_false_alarm_rate":("false_alarm", "negative_image_count"),
    "change_evaluation_coverage":("change_evaluated", "safe_selected_count"),
}


def summarize(rows, bootstrap=1000):
    result = {key:ratio(rows, *fields) for key,fields in METRICS.items()}
    groups = defaultdict(list)
    for row in rows:
        groups[row["lineage_id"]].append(row)
    result["query_count"], result["box_count"] = len(rows), len(groups)
    if bootstrap and len(groups) >= 2:
        rng, names = np.random.default_rng(26), sorted(groups)
        samples = defaultdict(list)
        for _ in range(bootstrap):
            sample = [row for name in rng.choice(names, size=len(names), replace=True) for row in groups[name]]
            for key, fields in METRICS.items():
                score = ratio(sample, *fields)["value"]
                if score is not None:
                    samples[key].append(score)
        for key in METRICS:
            result[key]["cluster_bootstrap_95_ci"] = np.percentile(samples[key], [2.5,97.5]).tolist() if len(samples[key]) >= bootstrap*.8 else None
    else:
        for key in METRICS:
            result[key]["cluster_bootstrap_95_ci"] = None
    return result


def evaluate(manifest, labels, predictions, protocol):
    for data,field in [(manifest,"manifest_sha256"),(labels,"labels_sha256"),(protocol,"protocol_sha256"),(predictions,"predictions_sha256")]:
        verify_hash(data, field)
    manifest_sha = manifest["manifest_sha256"]
    if any(data["manifest_sha256"] != manifest_sha for data in (labels,protocol,predictions)):
        raise ValueError("EVALUATION_MANIFEST_MISMATCH")
    if predictions["protocol_sha256"] != protocol["protocol_sha256"]:
        raise ValueError("PREDICTIONS_PROTOCOL_MISMATCH")
    if labels.get("source_kind") != manifest["source_kind"]:
        raise ValueError("SOURCE_KIND_MISMATCH")
    if labels.get("status") != "LABELS_FINALIZED_NOT_MODEL_VALIDATED":
        raise ValueError("FINAL_HUMAN_LABELS_REQUIRED")
    if validate_pairs(labels["pairs"], manifest["split_manifest"])["gate"] != "PASS" or not all(resolved(r) for r in labels["pairs"]):
        raise ValueError("UNRESOLVED_OR_INVALID_LABELS")
    expected = {r["pair_id"]:r for r in manifest["pairs"]}
    if len(labels["pairs"]) != len(expected) or {r["pair_id"] for r in labels["pairs"]} != set(expected):
        raise ValueError("LABELS_MISSING_OR_DUPLICATE_PAIRS")
    immutable = ("query_id","reference_id","query_sha256","reference_sha256","query_lineage_id","reference_lineage_id","split")
    by_query = defaultdict(dict)
    for row in labels["pairs"]:
        if any(row[key] != expected[row["pair_id"]][key] for key in immutable):
            raise ValueError("LABEL_IDENTITY_CHANGED")
        if row["split"] == protocol["split"]:
            by_query[row["query_id"]][row["reference_id"]] = row
    predicted = predictions["queries"]
    if len(predicted) != len(by_query) or {r["query_id"] for r in predicted} != set(by_query):
        raise ValueError("PREDICTIONS_MISSING_OR_DUPLICATE_QUERIES")
    assets = {a["id"]:a for a in manifest["assets"]}
    rows = []
    for prediction in predicted:
        query_id, ranking = prediction["query_id"], prediction["ranking"]
        targets = by_query[query_id]
        if len(ranking) != len(targets) or set(ranking) != set(targets):
            raise ValueError("CANDIDATE_POOL_CHANGED")
        if not isinstance(prediction["accepted"], bool) or prediction["selected_reference_id"] not in targets:
            raise ValueError("INVALID_SELECTION")
        if prediction["selected_reference_id"] != ranking[0]:
            raise ValueError("RANKING_SELECTION_MISMATCH")
        matches = {key for key,r in targets.items() if r["correspondence"] == "MATCH"}
        sufficient = {key for key,r in targets.items() if r["correspondence"] == "MATCH" and r["sufficiency"] == "SUFFICIENT"}
        selected = targets[prediction["selected_reference_id"]]
        accepted, safe_selected = prediction["accepted"], prediction["selected_reference_id"] in sufficient
        rank = next((i+1 for i,key in enumerate(ranking) if key in matches), None)
        row = {"query_id":query_id, "lineage_id":selected["query_lineage_id"], "query_count":1,
               "rank_eligible":int(bool(matches)), "top1":int(rank == 1), "top3":int(rank is not None and rank <= 3),
               "reciprocal_rank":1/rank if rank else 0, "accepted":int(accepted),
               "unsafe":int(accepted and not safe_selected), "sufficient_eligible":int(bool(sufficient)),
               "safe_accept":int(accepted and safe_selected), "safe_selected_count":int(safe_selected),
               "conditions":sorted(set(selected["reason_codes"])) or ["NO_RECORDED_CONDITION"]}
        change = prediction["change"]
        if change["state"] not in {"EVALUATED","ABSTAIN","NOT_SUPPORTED"}:
            raise ValueError("INVALID_CHANGE_STATE")
        if change["state"] != "EVALUATED" and change["polygons"]:
            raise ValueError("POLYGONS_REQUIRE_EVALUATED_CHANGE")
        if change["state"] == "EVALUATED" and not accepted:
            raise ValueError("CHANGE_REQUIRES_ACCEPTED_MATCH")
        asset = assets[query_id]
        predicted_mask = polygon_mask(change["polygons"], asset["width"], asset["height"])
        row["change_evaluated"] = int(safe_selected and change["state"] == "EVALUATED")
        if safe_selected and change["state"] != "NOT_SUPPORTED":
            valid = ~polygon_mask(selected["ignore_polygons"], asset["width"], asset["height"])
            target_mask = polygon_mask(selected["change_polygons"], asset["width"], asset["height"]) & valid
            predicted_mask &= valid
            if selected["observable_change"] == "CHANGED":
                if target_mask.any():
                    tp = int((target_mask & predicted_mask).sum())
                    union = int((target_mask | predicted_mask).sum())
                    total = int(target_mask.sum()+predicted_mask.sum())
                    row.update(positive_mask_count=1, positive_image_count=1, positive_detected=int(tp>0),
                               tp_pixels=tp, gt_pixels=int(target_mask.sum()), iou=tp/union, dice=2*tp/total)
                else:
                    row["all_positive_pixels_ignored"] = 1
            elif valid.any():
                row.update(negative_image_count=1, false_alarm=int(predicted_mask.any()))
        rows.append(row)
    return {"status":"MEASURED_NOT_RELEASE_APPROVED", "source_kind":manifest["source_kind"],
            "physical_box_count":len({r["lineage_id"] for r in rows}) if manifest["source_kind"] == "REAL" else 0,
            "manifest_sha256":manifest_sha, "labels_sha256":labels["labels_sha256"],
            "protocol_sha256":protocol["protocol_sha256"], "predictions_sha256":predictions["predictions_sha256"],
            "split":protocol["split"], "metrics":summarize(rows),
            "by_condition":{condition:summarize([r for r in rows if condition in r["conditions"]], 0)
                for condition in sorted({c for r in rows for c in r["conditions"]})},
            "queries":rows, "limits":["Change metrics condition on selecting a human-labeled sufficient pair; always read ranking/coverage alongside them.",
                "Abstention on a sufficient selected pair counts as missed change. Ranker-only has no change score.",
                "Rasterization is capped at 512px; tiny defects need a dedicated full-resolution protocol.",
                "Bootstrap resamples boxes; zero observed errors may still have a degenerate bootstrap interval, not a zero-risk guarantee.",
                "Hashes bind artifacts but do not prove human independence, rights, or that test labels were never inspected."]}
