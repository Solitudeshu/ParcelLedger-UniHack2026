"""Create immutable, blinded annotation tasks from team-supplied parcel photographs.

Layout: <root>/<box-id>/dispatch/* and <root>/<box-id>/receipt/*.
Labels are UNKNOWN until reviewed. This tool never invents physical photographs.
"""
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps

from ml.data.labels import locked_split, validate_pairs


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def build(root, output, dataset_id, source_kind="REAL"):
    root, output = Path(root).resolve(), Path(output)
    if output.exists():
        raise ValueError("MANIFEST_EXISTS_USE_A_NEW_VERSION")
    declarations = root / "rights.json"
    rights = json.loads(declarations.read_text(encoding="utf-8"))
    if rights.get("source") != "TEAM_OWNED" or rights.get("permission") != "RESEARCH_AND_COMMERCIAL" or not rights.get("declared_by"):
        raise ValueError("MISSING_RIGHTS_DECLARATION")
    boxes = sorted(p.name for p in root.iterdir() if p.is_dir() and (p/"dispatch").is_dir() and (p/"receipt").is_dir())
    split = locked_split(boxes, seed=dataset_id)
    assets, by_box = [], defaultdict(lambda: defaultdict(list))
    for box in boxes:
        for stage in ("dispatch", "receipt"):
            for path in sorted((root/box/stage).iterdir()):
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    continue
                if not path.resolve().is_relative_to(root):
                    raise ValueError("IMAGE_OUTSIDE_DATASET")
                raw = path.read_bytes()
                if len(raw) > 20*1024*1024:
                    raise ValueError("IMAGE_TOO_LARGE")
                with Image.open(path) as image:
                    if getattr(image, "n_frames", 1) != 1 or image.width*image.height > 40_000_000:
                        raise ValueError("IMAGE_FORMAT_UNSUPPORTED")
                    oriented = ImageOps.exif_transpose(image)
                    oriented.load()
                    size = oriented.size
                asset = {"id": "photo-"+digest(path.relative_to(root).as_posix().encode())[:20],
                         "path": path.relative_to(root).as_posix(), "sha256": digest(raw), "lineage_id": box,
                         "stage": stage, "width": size[0], "height": size[1], "split": split["lineages"][box]}
                assets.append(asset)
                by_box[box][stage].append(asset)
            if not by_box[box][stage]:
                raise ValueError("EMPTY_CAPTURE_STAGE")
    tasks = []
    for box in boxes:
        same_split = [b for b in boxes if b != box and split["lineages"][b] == split["lineages"][box]]
        references = by_box[box]["dispatch"]
        if same_split:
            references = references + by_box[same_split[0]]["dispatch"][:2]
        for query in by_box[box]["receipt"]:
            for reference in references:
                tasks.append({"pair_id": digest(f"{query['id']}:{reference['id']}".encode())[:24],
                    "query_id": query["id"], "reference_id": reference["id"], "split": query["split"],
                    "query_lineage_id": box, "reference_lineage_id": reference["lineage_id"],
                    "query_sha256": query["sha256"], "reference_sha256": reference["sha256"],
                    "source_kind": source_kind, "source_ref": dataset_id, "license_ref": "rights.json",
                    "annotator_id": "UNASSIGNED", "correspondence": "UNKNOWN", "sufficiency": "UNKNOWN",
                    "observable_change": "UNKNOWN", "change_polygons": [], "ignore_polygons": [], "reason_codes": []})
    validation = validate_pairs(tasks, split)
    if validation["gate"] != "PASS":
        raise ValueError("DATASET_SPLIT_OR_SCHEMA_INVALID:"+json.dumps(validation["errors"]))
    payload = {"version": "real-collection-v1", "dataset_id": dataset_id, "source_kind": source_kind,
               "rights_declaration_sha256": digest(declarations.read_bytes()), "rights_review": "SELF_DECLARED_REQUIRES_REVIEW",
               "split_manifest": split, "assets": assets, "pairs": tasks, "annotation_status": "NOT_ANNOTATED",
               "near_duplicate_review": "PENDING", "physical_box_count": len(boxes) if source_kind == "REAL" else 0}
    payload["manifest_sha256"] = digest(canonical(payload))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "TASKS_CREATED_NOT_VALIDATED", "boxes": len(boxes), "assets": len(assets), "pairs": len(tasks),
            "manifest_sha256": payload["manifest_sha256"], "source_kind": source_kind}


LABEL_FIELDS = ("correspondence", "sufficiency", "observable_change", "change_polygons", "ignore_polygons", "reason_codes")


def resolved(row):
    if row["correspondence"] == "UNKNOWN":
        return False
    if row["correspondence"] == "MATCH":
        if row["sufficiency"] == "UNKNOWN":
            return False
        if row["sufficiency"] == "SUFFICIENT":
            return row["observable_change"] != "UNKNOWN" and (row["observable_change"] != "CHANGED" or bool(row["change_polygons"]))
    return True


def review(manifest, submissions):
    expected = manifest["manifest_sha256"]
    if digest(canonical({k:v for k,v in manifest.items() if k != "manifest_sha256"})) != expected:
        raise ValueError("MANIFEST_CHECKSUM_MISMATCH")
    if len(submissions) != 2 or len({s.get("annotator_id") for s in submissions}) != 2:
        raise ValueError("TWO_INDEPENDENT_REVIEWERS_REQUIRED")
    by_id = {r["pair_id"]: r for r in manifest["pairs"]}
    reviewers = []
    for submitted in submissions:
        if submitted.get("manifest_sha256") != expected or not submitted.get("annotator_id"):
            raise ValueError("REVIEW_MANIFEST_MISMATCH")
        rows = submitted["annotations"]
        if len(rows) != len(by_id) or {r["pair_id"] for r in rows} != set(by_id):
            raise ValueError("INCOMPLETE_OR_DUPLICATE_REVIEW")
        trusted = []
        for row in rows:
            baseline = by_id[row["pair_id"]]
            trusted.append({**baseline, **{k:row[k] for k in LABEL_FIELDS}, "annotator_id": submitted["annotator_id"]})
        validation = validate_pairs(trusted, manifest["split_manifest"])
        if validation["gate"] != "PASS":
            raise ValueError("INVALID_REVIEW_LABELS:"+json.dumps(validation["errors"]))
        reviewers.append({row["pair_id"]: row for row in trusted})
    conflicts, unresolved, agreed = [], [], []
    for pair_id in by_id:
        a, b = (reviewer[pair_id] for reviewer in reviewers)
        if any(a[k] != b[k] for k in LABEL_FIELDS):
            conflicts.append(pair_id)
        elif not resolved(a):
            unresolved.append(pair_id)
        else:
            agreed.append(a)
    return {"status": "REQUIRES_ADJUDICATION" if conflicts or unresolved else "LABELS_AGREED_PENDING_RIGHTS_AND_REAL_EVALUATION",
            "manifest_sha256": expected, "reviewers": [s["annotator_id"] for s in submissions],
            "pair_count": len(by_id), "conflicts": conflicts, "unresolved": unresolved, "agreed_pairs": agreed,
            "physical_box_validation": "NOT_RUN", "near_duplicate_review": manifest["near_duplicate_review"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("build")
    create.add_argument("--root", required=True)
    create.add_argument("--output", required=True)
    create.add_argument("--dataset-id", required=True)
    validate = commands.add_parser("review")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--reviewer-a", required=True)
    validate.add_argument("--reviewer-b", required=True)
    validate.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "build":
        result = build(args.root, args.output, args.dataset_id)
    else:
        read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
        result = review(read(args.manifest), [read(args.reviewer_a), read(args.reviewer_b)])
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "agreed_pairs"}))
