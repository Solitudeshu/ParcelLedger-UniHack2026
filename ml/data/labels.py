"""Validate pair supervision and detect split leakage before feature extraction."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict

SPLITS = ("train", "validation", "test")
CORRESPONDENCE = {"MATCH", "WRONG_REGION", "WRONG_OBJECT", "UNKNOWN"}
SUFFICIENCY = {"SUFFICIENT", "INSUFFICIENT", "UNKNOWN"}
CHANGE = {"CHANGED", "UNCHANGED", "UNKNOWN"}
SHA256 = re.compile(r"^[a-f0-9]{64}$")
REASONS = {"BLUR", "GLARE", "OCCLUSION", "ROI_OUTSIDE_OVERLAP", "LOW_RESOLUTION",
           "VIEW_MISMATCH", "NO_REFERENCE", "UNKNOWN"}


def locked_split(lineages: list[str], seed: str = "unihack26-v1") -> dict:
    """Split a fixed, deduplicated corpus 60/20/20. Persist; never refit mid-run."""
    if len(lineages) != len(set(lineages)) or any(not item for item in lineages):
        raise ValueError("LINEAGE_IDS_MUST_BE_UNIQUE_AND_NONEMPTY")
    if len(lineages) < 5:
        raise ValueError("INSUFFICIENT_LINEAGES_FOR_THREE_SPLITS")
    ordered = sorted(lineages, key=lambda item: hashlib.sha256(f"{seed}:{item}".encode()).digest())
    train_end, validation_end = int(len(ordered) * 0.6), int(len(ordered) * 0.8)
    groups = {name: split for split, names in zip(
        SPLITS, (ordered[:train_end], ordered[train_end:validation_end], ordered[validation_end:]), strict=True)
        for name in names}
    payload = {"version": "lineage-split-v1", "seed": seed, "lineages": groups}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {**payload, "sha256": hashlib.sha256(canonical.encode()).hexdigest()}


def valid_polygons(polygons) -> bool:
    if not isinstance(polygons, list):
        return False
    for polygon in polygons:
        if not isinstance(polygon, list) or len(polygon) < 3:
            return False
        for point in polygon:
            if not isinstance(point, list) or len(point) != 2:
                return False
            if any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) or not 0 <= v <= 1 for v in point):
                return False
        area = abs(sum(polygon[i][0] * polygon[(i + 1) % len(polygon)][1]
                       - polygon[(i + 1) % len(polygon)][0] * polygon[i][1]
                       for i in range(len(polygon)))) / 2
        if area <= 1e-8:
            return False
    return True


def validate_pairs(records: list[dict], split_manifest: dict) -> dict:
    """Return explicit failures. UNKNOWN is masked, never interpreted as a negative."""
    errors = []
    seen_pairs = set()
    hashes = defaultdict(set)
    queries = defaultdict(set)
    mapping = split_manifest.get("lineages", {})
    counts = defaultdict(int)
    for index, record in enumerate(records):
        def fail(code):
            errors.append({"row": index, "pair_id": record.get("pair_id"), "code": code})

        required = ("pair_id", "query_id", "split", "query_lineage_id", "reference_lineage_id",
                    "query_sha256", "reference_sha256", "source_kind", "source_ref", "license_ref",
                    "annotator_id", "correspondence", "sufficiency", "observable_change",
                    "change_polygons", "ignore_polygons", "reason_codes")
        if any(key not in record for key in required):
            fail("MISSING_REQUIRED_FIELDS")
            continue
        text_fields = set(required) - {"change_polygons", "ignore_polygons", "reason_codes"}
        if any(not isinstance(record[key], str) or not record[key].strip() for key in text_fields):
            fail("INVALID_TEXT_FIELD")
            continue
        pair_id, split = record["pair_id"], record["split"]
        if pair_id in seen_pairs:
            fail("DUPLICATE_PAIR_ID")
        seen_pairs.add(pair_id)
        if split not in SPLITS:
            fail("INVALID_SPLIT")
        counts[split] += 1
        for side in ("query", "reference"):
            if mapping.get(record[f"{side}_lineage_id"]) != split:
                fail(f"{side.upper()}_LINEAGE_SPLIT_MISMATCH")
            digest = record[f"{side}_sha256"]
            if not SHA256.fullmatch(digest):
                fail("INVALID_IMAGE_SHA256")
            hashes[digest].add(split)
        queries[record["query_id"]].add(split)
        correspondence, sufficiency, change = (record[key] for key in (
            "correspondence", "sufficiency", "observable_change"))
        if correspondence not in CORRESPONDENCE or sufficiency not in SUFFICIENCY or change not in CHANGE:
            fail("INVALID_LABEL_ENUM")
        if correspondence != "MATCH" and sufficiency == "SUFFICIENT":
            fail("SUFFICIENT_REQUIRES_MATCH")
        if change != "UNKNOWN" and (correspondence != "MATCH" or sufficiency != "SUFFICIENT"):
            fail("CHANGE_REQUIRES_SUFFICIENT_MATCH")
        if correspondence in ("MATCH", "WRONG_REGION") and record["query_lineage_id"] != record["reference_lineage_id"]:
            fail("SAME_OBJECT_LABEL_LINEAGE_MISMATCH")
        if correspondence == "WRONG_OBJECT" and record["query_lineage_id"] == record["reference_lineage_id"]:
            fail("WRONG_OBJECT_REQUIRES_DIFFERENT_LINEAGE")
        if record["source_kind"] not in {"REAL", "SYNTHETIC", "SOFTWARE_FIXTURE"}:
            fail("INVALID_SOURCE_KIND")
        if not valid_polygons(record["change_polygons"]) or not valid_polygons(record["ignore_polygons"]):
            fail("INVALID_NORMALIZED_POLYGON")
        if record["change_polygons"] and change != "CHANGED":
            fail("CHANGE_MASK_WITHOUT_CHANGED_LABEL")
        if not isinstance(record["reason_codes"], list) or any(not isinstance(reason, str) or reason not in REASONS for reason in record["reason_codes"]):
            fail("INVALID_REASON_CODES")
    for digest, splits in hashes.items():
        if len(splits) > 1:
            errors.append({"code": "IMAGE_HASH_SPLIT_LEAKAGE", "sha256": digest, "splits": sorted(splits)})
    for query_id, splits in queries.items():
        if len(splits) > 1:
            errors.append({"code": "QUERY_SPLIT_LEAKAGE", "query_id": query_id, "splits": sorted(splits)})
    return {"gate": "PASS" if records and not errors else "FAIL", "rows": len(records),
            "split_counts": dict(counts), "errors": errors,
            "license_clearance": "SEPARATE_REVIEW_REQUIRED", "near_duplicate_audit": "SEPARATE_REVIEW_REQUIRED"}


def supervision(record: dict) -> dict:
    match = record["correspondence"]
    sufficient = record["sufficiency"]
    return {
        "same_region_target": float(match == "MATCH"),
        "same_region_mask": match != "UNKNOWN",
        "sufficiency_target": float(sufficient == "SUFFICIENT"),
        "sufficiency_mask": match == "MATCH" and sufficient != "UNKNOWN",
        "change_mask": match == "MATCH" and sufficient == "SUFFICIENT" and record["observable_change"] != "UNKNOWN",
    }
