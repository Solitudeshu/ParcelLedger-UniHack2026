"""Recover source-object lineage from Parcel3D metadata without copying images."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

import yaml

from ml.data.labels import locked_split
from ml.data.parcel3d_audit import find_annotations, sha256_file, within_root


def parse_lineage(config: dict, scenario_id: str, split: str, category_id: int) -> dict:
    scenario = config.get("SCENARIO", {})
    model = scenario.get("MODEL", {})
    if scenario.get("ID") != scenario_id or scenario.get("FLAG") != split:
        raise ValueError("SCENARIO_ID_OR_SPLIT_MISMATCH")
    if scenario.get("CATEGORY_ID") != category_id:
        raise ValueError("CATEGORY_MISMATCH")
    name, obj = model.get("NAME"), model.get("OBJ")
    if not isinstance(name, str) or not isinstance(obj, str):
        raise ValueError("SOURCE_OBJECT_ID_MISSING")
    parts = PurePosixPath(obj.replace("\\", "/"))
    if parts.is_absolute() or ".." in parts.parts or ":" in obj:
        raise ValueError("UNSAFE_SOURCE_OBJECT_PATH")
    if len(parts.parts) < 4 or parts.parts[0] != "google_scanned_objects" or parts.parts[1] != name:
        raise ValueError("UNRECOGNIZED_SOURCE_OBJECT_LINEAGE")
    return {"lineage_id": f"google_scanned_objects/{name}", "source_mesh": obj,
            "parcel_type": scenario.get("PARCEL_TYPE"),
            "views_configured": config.get("RENDER", {}).get("VIEWS"),
            "render_seed": config.get("RENDER", {}).get("SEED")}


def index_lineages(input_root: Path, output_root: Path, workers: int = 8) -> dict:
    annotations = find_annotations(input_root)
    root = annotations["train"].parent
    output_root.mkdir(parents=True, exist_ok=True)
    requests, errors = [], []
    annotation_hashes = {}
    for split, path in annotations.items():
        document = json.loads(path.read_text())
        annotation_hashes[split] = sha256_file(path)
        by_image = defaultdict(list)
        for annotation in document["annotations"]:
            by_image[annotation["image_id"]].append(annotation)
        for image in document["images"]:
            associated = by_image[image["id"]]
            if len(associated) != 1:
                errors.append({"split": split, "image_id": image["id"], "code": "EXPECTED_ONE_PARCEL"})
                continue
            requests.append((split, image, associated[0]))

    def read_one(item):
        split, image, annotation = item
        try:
            image_path = within_root(root, image["file_name"])
            scenario_dir = image_path.parent
            metadata_path = scenario_dir / "meta/render_config.yaml"
            if metadata_path.stat().st_size > 64_000:
                raise ValueError("METADATA_SIZE_LIMIT")
            raw = metadata_path.read_bytes()
            config = yaml.safe_load(raw)
            info = parse_lineage(config, scenario_dir.name, split, annotation["category_id"])
            mesh_path = within_root(root, annotation["model"])
            if mesh_path.parent.parent != scenario_dir:
                raise ValueError("MESH_SCENARIO_MISMATCH")
            return {"source_split": split, "image_id": image["id"], "scenario_id": scenario_dir.name,
                    "image_path": image["file_name"], "mesh_path": annotation["model"],
                    "category_id": annotation["category_id"], "metadata_sha256": hashlib.sha256(raw).hexdigest(), **info}
        except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
            known = str(exc) if isinstance(exc, ValueError) else "METADATA_UNREADABLE"
            return {"error": {"split": split, "image_id": image["id"], "code": known}}

    records = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 16))) as pool:
        for index, result in enumerate(pool.map(read_one, requests)):
            if "error" in result:
                errors.append(result["error"])
            else:
                records.append(result)
            if (index + 1) % 500 == 0:
                print(f"Lineage metadata {index + 1}/{len(requests)}", flush=True)
    groups = defaultdict(set)
    scenarios = Counter()
    for record in records:
        groups[record["lineage_id"]].add(record["source_split"])
        scenarios[record["scenario_id"]] += 1
    duplicates = [identifier for identifier, count in scenarios.items() if count != 1]
    overlaps = {lineage: sorted(splits) for lineage, splits in groups.items() if len(splits) > 1}
    result = {"version": "parcel3d-lineage-v1", "source": "ayesha19765/parcel3d", "source_version": 1,
              "annotation_hashes": annotation_hashes, "indexed_records": len(records),
              "source_object_lineages": len(groups), "source_split_counts": dict(Counter(r["source_split"] for r in records)),
              "source_lineages_crossing_splits": overlaps, "duplicate_scenario_ids": duplicates,
              "metadata_gate": "PASS" if records and not errors and not duplicates else "FAIL",
              "original_source_object_split_gate": "PASS" if records and not errors and not overlaps and not duplicates else "FAIL",
              "correspondence_labels": "NOT_PROVIDED_BY_THIS_INDEX", "errors": errors,
              "full_image_hash_audit": "NOT_RUN", "commercial_render_license": "HOLD_ORIGINAL_RENDERS"}
    if len(groups) >= 5 and not errors and not duplicates:
        manifest = locked_split(sorted(groups))
        for record in records:
            record["group_split"] = manifest["lineages"][record["lineage_id"]]
        (output_root / "lineage-split.json").write_text(json.dumps(manifest, indent=2))
        result["proposed_split_sha256"] = manifest["sha256"]
        result["proposed_split_lineage_counts"] = dict(Counter(manifest["lineages"].values()))
        result["proposed_split_record_counts"] = dict(Counter(r["group_split"] for r in records))
    target = output_root / "lineage-index.jsonl"
    target.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    result["index_sha256"] = sha256_file(target)
    (output_root / "lineage-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"errors", "source_lineages_crossing_splits"}}))
    return result
