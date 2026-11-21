"""Audit official VisA on Kaggle; export metadata only, never the image archive."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import tarfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

import numpy as np
from PIL import Image

REVISION = "2a692ab575001cbde74d402d897a7286086c6199"
ROOT_URL = f"https://raw.githubusercontent.com/amazon-science/spot-diff/{REVISION}/"
SPLIT_SHA = "a48557e6033318cb90556f706196bc9d247a776a23ea51aecee5a80dd0332995"
README_SHA = "803d1a86c80fcdebc63dfabd041f78336c433ca635b735668aadb24bfc124917"
ARCHIVE_URL = "https://amazon-visual-anomaly.s3.us-west-2.amazonaws.com/VisA_20220922.tar"
ARCHIVE_BYTES = 1_929_840_640


def fetch_metadata(relative, expected):
    with urlopen(ROOT_URL + relative, timeout=60) as response:
        content = response.read(2_000_001)
    if len(content) > 2_000_000 or hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("OFFICIAL_METADATA_CHECKSUM_MISMATCH")
    return content


def normalized_member(name):
    while name.startswith("./"):
        name = name[2:]
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError("UNSAFE_ARCHIVE_MEMBER")
    if path.parts and path.parts[0] == "VisA":
        path = PurePosixPath(*path.parts[1:])
    return path.as_posix()


def audit_archive(archive, split_bytes, output, expected_count=10821):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    split_rows = list(csv.DictReader(io.StringIO(split_bytes.decode("utf-8-sig"))))
    expected = {row["image"]: row for row in split_rows}
    if len(split_rows) != len(expected) or len(expected) != expected_count:
        raise ValueError("UNEXPECTED_OFFICIAL_SPLIT")
    if any(row["split"] not in {"train", "test"} or (row["split"] == "train" and row["label"] != "normal")
           for row in split_rows):
        raise ValueError("NONSTANDARD_TRAIN_TEST_SPLIT")
    records, seen = [], set()
    hashes = {"sha256": defaultdict(list), "rgb_sha256": defaultdict(list)}
    with tarfile.open(archive, "r:*") as tar:
        for member in tar:
            name = normalized_member(member.name)
            if name not in expected:
                continue
            if name in seen or not member.isfile() or not 0 < member.size <= 32_000_000:
                raise ValueError("INVALID_OR_DUPLICATE_IMAGE_MEMBER")
            seen.add(name)
            with tar.extractfile(member) as stream:
                raw = stream.read(32_000_001)
            if len(raw) != member.size:
                raise ValueError("IMAGE_SIZE_MISMATCH")
            with Image.open(io.BytesIO(raw)) as image:
                rgb = image.convert("RGB")
                rgb.load()
                pixels = np.asarray(rgb)
                thumbnail = np.asarray(rgb.convert("L").resize((9, 8)))
                bits = (thumbnail[:, 1:] > thumbnail[:, :-1]).flatten()
                dhash = sum(int(value) << index for index, value in enumerate(bits))
                shape = list(pixels.shape)
                decoded_hash = hashlib.sha256(pixels.tobytes()).hexdigest()
            row = expected[name]
            record = {"image_path": name, "category": row["object"], "source_split": row["split"],
                      "role": row["split"], "label": row["label"], "shape": shape,
                      "sha256": hashlib.sha256(raw).hexdigest(), "rgb_sha256": decoded_hash,
                      "dhash64": f"{dhash:016x}", "bytes": len(raw),
                      "physical_instance_id": None}
            records.append(record)
            for kind in hashes:
                hashes[kind][record[kind]].append(record)
            if len(records) % 1000 == 0:
                print(json.dumps({"audited_images": len(records), "expected": expected_count}), flush=True)
    if seen != set(expected):
        raise ValueError("OFFICIAL_IMAGES_MISSING_FROM_ARCHIVE")
    duplicate_groups, cross_split = [], []
    for kind, groups in hashes.items():
        for digest, items in groups.items():
            if len(items) > 1:
                item = {"kind": kind, "hash": digest, "images": [r["image_path"] for r in items],
                        "roles": sorted({r["role"] for r in items})}
                duplicate_groups.append(item)
                if len(item["roles"]) > 1:
                    cross_split.append(item)
    manifest = output / "images.jsonl"
    manifest.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in sorted(records, key=lambda r: r["image_path"])),
                        encoding="utf-8")
    (output / "duplicates.json").write_text(json.dumps(duplicate_groups, indent=2), encoding="utf-8")
    report = {"status": "PIXEL_AUDIT_COMPLETE", "images": len(records),
              "counts": dict(Counter(r["role"] for r in records)), "categories": dict(Counter(r["category"] for r in records)),
              "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
              "duplicate_groups_by_hash_type": dict(Counter(r["kind"] for r in duplicate_groups)),
              "cross_split_duplicate_groups": len(cross_split),
              "split_sha256": hashlib.sha256(split_bytes).hexdigest(), "source_revision": REVISION,
              "license": "CC BY 4.0 (data, per pinned official README)",
              "training_ready": False, "test_used_for_training_or_metrics": False,
              "limits": ["Near-duplicate and cross-dataset review pending.",
                         "Official split has no verified physical-instance lineage IDs.",
                         "Industrial anomaly data is not paired parcel handover ground truth.",
                         "Mask files are not audited or used here; only normal official train is a pretraining candidate."]}
    (output / "audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def run(output="/kaggle/working/visa-audit", temp="/kaggle/temp/visa-audit"):
    output, temp = Path(output), Path(temp)
    output.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    split = fetch_metadata("split_csv/1cls.csv", SPLIT_SHA)
    readme = fetch_metadata("README.md", README_SHA)
    if b"CC BY 4.0" not in readme:
        raise ValueError("DATA_LICENSE_NOT_FOUND")
    (output / "1cls.csv").write_bytes(split)
    (output / "official-README.md").write_bytes(readme)
    archive = temp / "VisA_20220922.tar"
    digest, total = hashlib.sha256(), 0
    with urlopen(ARCHIVE_URL, timeout=60) as response:
        with archive.open("wb") as stream:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                total += len(chunk)
                if total > ARCHIVE_BYTES:
                    raise ValueError("OFFICIAL_ARCHIVE_SIZE_CHANGED")
                stream.write(chunk)
                digest.update(chunk)
    if total != ARCHIVE_BYTES:
        raise ValueError("OFFICIAL_ARCHIVE_SIZE_CHANGED")
    download = {"url": ARCHIVE_URL, "bytes": total, "sha256": digest.hexdigest(),
                "archive_location": "KAGGLE_TEMP_ONLY_NOT_EXPORTED"}
    (output / "download.json").write_text(json.dumps(download, indent=2), encoding="utf-8")
    report = audit_archive(archive, split, output)
    print(json.dumps(report), flush=True)
    return report
