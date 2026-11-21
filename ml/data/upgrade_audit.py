"""Full Parcel3D pixel/duplicate audit on Kaggle, without exporting source images."""
from __future__ import annotations

import hashlib
import io
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from ml.data.parcel3d_audit import find_annotations, within_root

EXPECTED_INDEX_SHA = "55cf3a02babbe1e902b7524ec39d0a7195b2a4f4b2a1ac001a066668e4600a6d"


def assign_roles(records):
    """Keep official train/test, partition official validation by object identity."""
    splits = defaultdict(set)
    for row in records:
        splits[row["lineage_id"]].add(row["source_split"])
    if any(len(values) != 1 for values in splits.values()):
        raise ValueError("OFFICIAL_SOURCE_LINEAGE_LEAKAGE")
    validation = sorted(
        (key for key, values in splits.items() if values == {"validation"}),
        key=lambda key: hashlib.sha256(f"kc-upgrade-v2:{key}".encode()).digest(),
    )
    if len(validation) < 2:
        raise ValueError("NEED_TWO_VALIDATION_LINEAGES")
    selection = set(validation[:len(validation) // 2])
    return {
        key: ("selection" if key in selection else "calibration")
        if values == {"validation"} else next(iter(values))
        for key, values in splits.items()
    }


def duplicate_groups(rows, field):
    groups = defaultdict(list)
    for row in rows:
        groups[row[field]].append(row)
    return [{"digest": digest, "paths": [r["image_path"] for r in group],
             "roles": sorted({r["role"] for r in group})}
            for digest, group in groups.items() if len(group) > 1]


def run(input_root, output_root, index_path, workers=4):
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    raw = Path(index_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_INDEX_SHA:
        raise ValueError("LINEAGE_INDEX_HASH_MISMATCH")
    records = [json.loads(line) for line in raw.splitlines()]
    roles = assign_roles(records)
    root = find_annotations(Path(input_root))["train"].parent

    def inspect(row):
        path = within_root(root, row["image_path"])
        raw_image = path.read_bytes()
        with Image.open(io.BytesIO(raw_image)) as source:
            rgb = source.convert("RGB")
            pixel_hash = hashlib.sha256(
                f"RGB:{rgb.width}:{rgb.height}:".encode() + rgb.tobytes(),
            ).hexdigest()
            # This fingerprint is for later near-duplicate review, not a proof
            # that perceptually similar images are independent.
            thumb = rgb.convert("L").resize((9, 8))
            pixels = list(thumb.getdata())
            bits = [pixels[y * 9 + x] > pixels[y * 9 + x + 1] for y in range(8) for x in range(8)]
            dhash = sum(int(bit) << index for index, bit in enumerate(bits))
            return {"image_path": row["image_path"], "lineage_id": row["lineage_id"],
                    "source_split": row["source_split"], "role": roles[row["lineage_id"]],
                    "width": rgb.width, "height": rgb.height, "bytes": len(raw_image),
                    "sha256": hashlib.sha256(raw_image).hexdigest(), "pixel_sha256": pixel_hash,
                    "dhash": f"{dhash:016x}", "category_id": row["category_id"],
                    "parcel_type": row["parcel_type"]}

    output = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, result in enumerate(pool.map(inspect, records)):
            output.append(result)
            if (index + 1) % 500 == 0:
                print(json.dumps({"decoded": index + 1, "total": len(records)}), flush=True)
    byte_duplicates = duplicate_groups(output, "sha256")
    pixel_duplicates = duplicate_groups(output, "pixel_sha256")
    leakage = [group for group in byte_duplicates + pixel_duplicates if len(group["roles"]) > 1]
    expected = {Path(row["image_path"]).parts[1] for row in records}
    actual = {p.name for split in ("train", "validation", "test") for p in (root / split).iterdir() if p.is_dir()}
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in output)
    (output_root / "images.jsonl").write_text(payload, encoding="utf-8")
    report = {"status": "PASS" if not leakage else "HOLD_DUPLICATE_LEAKAGE",
              "images": len(output), "lineages": len(roles), "roles": dict(Counter(r["role"] for r in output)),
              "lineage_roles": dict(Counter(roles.values())), "bytes_read": sum(r["bytes"] for r in output),
              "index_sha256": EXPECTED_INDEX_SHA, "manifest_sha256": hashlib.sha256(payload.encode()).hexdigest(),
              "byte_duplicate_groups": byte_duplicates, "decoded_duplicate_groups": pixel_duplicates,
              "cross_role_duplicate_groups": leakage, "unannotated_directories": sorted(actual - expected),
              "missing_directories": sorted(expected - actual),
              "unannotated_policy": "EXCLUDED_NO_LABEL_INFERENCE", "near_duplicate_review": "PENDING",
              "pair_labels": "NOT_CREATED", "physical_box_validation": "NOT_RUN",
              "license": "ACADEMIC_RESEARCH_ONLY_UPSTREAM_RENDER_TERMS"}
    (output_root / "audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if not isinstance(v, list)}), flush=True)
    return report
