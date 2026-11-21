"""Finalize human labels while retaining both independent submissions and reasons."""
import argparse
import json
from pathlib import Path

from ml.data.labels import validate_pairs
from scripts.real_data import LABEL_FIELDS, canonical, digest, resolved, review


def adjudicate(manifest, submissions, decisions):
    comparison = review(manifest, submissions)
    if decisions.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("ADJUDICATION_MANIFEST_MISMATCH")
    actor = decisions.get("adjudicator_id", "").strip()
    if not actor:
        raise ValueError("ADJUDICATOR_REQUIRED")
    pending = set(comparison["conflicts"] + comparison["unresolved"])
    resolutions = decisions.get("resolutions", [])
    if len(resolutions) != len(pending) or {row.get("pair_id") for row in resolutions} != pending:
        raise ValueError("RESOLUTIONS_MUST_COVER_EXACTLY_PENDING_PAIRS")
    source = {r["pair_id"]: r for r in manifest["pairs"]}
    rows = {r["pair_id"]: r for r in comparison["agreed_pairs"]}
    audit = []
    for decision in resolutions:
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            raise ValueError("ADJUDICATION_REASON_REQUIRED")
        row = {**source[decision["pair_id"]], **{k:decision[k] for k in LABEL_FIELDS}, "annotator_id": actor}
        if not resolved(row):
            raise ValueError("UNRESOLVED_LABEL_REQUIRES_MORE_EVIDENCE")
        rows[row["pair_id"]] = row
        audit.append({"pair_id":row["pair_id"], "reason":decision["reason"], "adjudicator_id":actor})
    finalized = [rows[row["pair_id"]] for row in manifest["pairs"]]
    check = validate_pairs(finalized, manifest["split_manifest"])
    if check["gate"] != "PASS":
        raise ValueError("INVALID_FINAL_LABELS:"+json.dumps(check["errors"]))
    result = {"version":"adjudicated-labels-v1", "status":"LABELS_FINALIZED_NOT_MODEL_VALIDATED",
              "manifest_sha256":manifest["manifest_sha256"], "source_kind":manifest["source_kind"],
              "reviewer_ids":comparison["reviewers"], "adjudicator_id":actor,
              "submissions":submissions, "submission_sha256":[digest(canonical(s)) for s in submissions],
              "adjudication":audit, "pairs":finalized,
              "independence":"Reviewer identifiers are self-declared; organizational independence requires human review.",
              "rights_review":"PENDING", "near_duplicate_review":"PENDING"}
    result["labels_sha256"] = digest(canonical(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("manifest", "reviewer-a", "reviewer-b", "decisions", "output"):
        parser.add_argument("--"+name, required=True)
    args = parser.parse_args()
    read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    result = adjudicate(read(args.manifest), [read(args.reviewer_a), read(args.reviewer_b)], read(args.decisions))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"status":result["status"], "pairs":len(result["pairs"]), "labels_sha256":result["labels_sha256"]}))
