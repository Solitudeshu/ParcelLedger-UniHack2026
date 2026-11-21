"""Canonical pixel fingerprints and bounded near-duplicate screening, not training.

Reads source pixels only for dataset hygiene. No images, thumbnails, labels or
model results from the held-out sets are exported as examples for model tuning.
"""
from __future__ import annotations

import hashlib
import heapq
import io
import json
import tarfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

import cv2
import numpy as np
from PIL import Image

from ml.data.parcel3d_audit import find_annotations, within_root
from ml.data.visa_audit import ARCHIVE_BYTES, ARCHIVE_URL, normalized_member

PARCEL_SHA = "267dd46092d3429dc66f539fb897e79b4be3f68beb94b75cd19f7289250e4d38"
VISA_SHA = "fba1c68957b8e79056696b0a1af96acca1b9faddb0ebc77353819cb56c80e730"
ARCHIVE_SHA = "2eb8690c803ab37de0324772964100169ec8ba1fa3f7e94291c9ca673f40f362"
BIT_COUNTS = np.array([i.bit_count() for i in range(256)], dtype=np.uint8)


def load_manifest(path, expected_sha):
    content = Path(path).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha:
        raise ValueError("AUDIT_MANIFEST_IDENTITY_CHANGED")
    rows = [json.loads(line) for line in content.splitlines()]
    if len({r["image_path"] for r in rows}) != len(rows):
        raise ValueError("DUPLICATE_MANIFEST_PATH")
    return rows


def bit_hash(bits):
    value = sum(int(bit) << index for index, bit in enumerate(np.asarray(bits).flatten()))
    return f"{value:016x}"


def fingerprint(raw, expected_sha):
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha:
        raise ValueError("SOURCE_IMAGE_BYTES_CHANGED")
    with Image.open(io.BytesIO(raw)) as source:
        rgb = source.convert("RGB")
        rgb.load()
        shape = [rgb.height, rgb.width, 3]
        canonical = hashlib.sha256(f"RGB:{rgb.width}:{rgb.height}:".encode() + rgb.tobytes()).hexdigest()
        gray = rgb.convert("L")
        small = np.asarray(gray.resize((9, 8), Image.Resampling.LANCZOS))
        dhash = bit_hash(small[:, 1:] > small[:, :-1])
        low = cv2.dct(np.float32(gray.resize((32, 32), Image.Resampling.LANCZOS)))[:8, :8].copy()
        # Ignore DC in the threshold and set its bit to zero explicitly.
        bits = low > np.median(low.flatten()[1:])
        bits[0, 0] = False
        return {"sha256": digest, "canonical_rgb_sha256": canonical, "shape": shape,
                "dhash64": dhash, "phash64": bit_hash(bits), "gray_std": float(np.std(small))}


def identify(row, source, raw):
    return {"dataset": source, "image_path": row["image_path"], "role": row["role"],
            "lineage_id": row.get("lineage_id"), "category": row.get("category"),
            **fingerprint(raw, row["sha256"])}


def scope(row):
    return f"{row['dataset']}:{row['role']}"


def cross_boundary(a, b):
    return a["dataset"] != b["dataset"] or a["role"] != b["role"]


def exact_groups(rows):
    output = []
    for field in ("sha256", "canonical_rgb_sha256"):
        groups = defaultdict(list)
        for index, row in enumerate(rows):
            groups[row[field]].append(index)
        for digest, indices in groups.items():
            if len(indices) > 1:
                output.append({"field": field, "digest": digest, "indices": indices,
                               "cross_boundary": len({scope(rows[i]) for i in indices}) > 1})
    return output


def distances(values, query):
    xor = np.bitwise_xor(values, np.uint64(query))
    return BIT_COUNTS[xor.view(np.uint8).reshape(-1, 8)].sum(axis=1)


def screen(rows, threshold=4, sample_limit=40):
    """Count all qualifying boundary pairs; retain deterministic closest examples.

    Both full-frame hashes must be close. This is a screening rule, not a
    duplicate verdict, and does not detect all crops/rotations or shared meshes.
    """
    if not 0 <= threshold <= 64 or sample_limit < 1:
        raise ValueError("INVALID_DUPLICATE_SCREEN_PARAMETERS")
    p = np.array([int(r["phash64"], 16) for r in rows], dtype=np.uint64)
    d = np.array([int(r["dhash64"], 16) for r in rows], dtype=np.uint64)
    scopes = np.array([scope(r) for r in rows])
    counts, samples = Counter(), defaultdict(list)
    comparisons = 0
    for i in range(len(rows) - 1):
        indices = np.flatnonzero(scopes[i + 1:] != scopes[i]) + i + 1
        comparisons += len(indices)
        pd = distances(p[indices], p[i])
        chosen = pd <= threshold
        indices, pd = indices[chosen], pd[chosen]
        dd = distances(d[indices], d[i])
        for j, dp, dh in zip(indices[dd <= threshold], pd[dd <= threshold], dd[dd <= threshold], strict=True):
            j, dp, dh = int(j), int(dp), int(dh)
            boundary = " <> ".join(sorted((str(scopes[i]), str(scopes[j]))))
            counts[boundary] += 1
            heap = samples[boundary]
            # Negated lexicographic key: largest distance/index is discarded first.
            item = (-max(dp, dh), -(dp + dh), -i, -j, dp, dh)
            if len(heap) < sample_limit:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
    candidates = []
    for boundary, heap in sorted(samples.items()):
        for _, _, ni, nj, dp, dh in sorted(heap, reverse=True):
            candidates.append({"boundary": boundary, "indices": [-ni, -nj],
                               "phash_distance": dp, "dhash_distance": dh,
                               "verdict": "UNREVIEWED_NOT_PROVEN_DUPLICATE"})
    return {"boundary_comparisons": comparisons, "candidate_pairs": sum(counts.values()),
            "candidate_counts_by_boundary": dict(sorted(counts.items())),
            "phash_and_dhash_max_distance": threshold, "retained_per_boundary_limit": sample_limit,
            "retained_candidates": candidates,
            "review_status": "PENDING" if candidates else "NO_CANDIDATES_AT_THIS_RULE"}


def fetch_archive(path):
    digest, total = hashlib.sha256(), 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(ARCHIVE_URL, timeout=60) as response, path.open("wb") as stream:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            total += len(chunk)
            if total > ARCHIVE_BYTES:
                raise ValueError("OFFICIAL_ARCHIVE_SIZE_CHANGED")
            digest.update(chunk)
            stream.write(chunk)
    if total != ARCHIVE_BYTES or digest.hexdigest() != ARCHIVE_SHA:
        raise ValueError("OFFICIAL_ARCHIVE_IDENTITY_CHANGED")


def run(input_root, parcel_manifest, visa_manifest, output, temp):
    cv2.setNumThreads(1)
    output, temp = Path(output), Path(temp)
    output.mkdir(parents=True, exist_ok=True)
    parcel = load_manifest(parcel_manifest, PARCEL_SHA)
    visa = load_manifest(visa_manifest, VISA_SHA)
    root = find_annotations(Path(input_root))["train"].parent

    def inspect_parcel(row):
        return identify(row, "parcel3d", within_root(root, row["image_path"]).read_bytes())

    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, result in enumerate(pool.map(inspect_parcel, parcel)):
            rows.append(result)
            if (index + 1) % 1000 == 0:
                print(json.dumps({"dataset": "parcel3d", "decoded": index + 1}), flush=True)
    archive = temp / "VisA_20220922.tar"
    fetch_archive(archive)
    expected, seen = {r["image_path"]: r for r in visa}, set()
    with tarfile.open(archive) as tar:
        for member in tar:
            name = normalized_member(member.name)
            if name not in expected:
                continue
            if name in seen or not member.isfile() or not 0 < member.size <= 32_000_000:
                raise ValueError("INVALID_OR_DUPLICATE_IMAGE_MEMBER")
            with tar.extractfile(member) as stream:
                raw = stream.read(32_000_001)
            if len(raw) != member.size:
                raise ValueError("IMAGE_SIZE_MISMATCH")
            rows.append(identify(expected[name], "visa", raw))
            seen.add(name)
            if len(seen) % 1000 == 0:
                print(json.dumps({"dataset": "visa", "decoded": len(seen)}), flush=True)
    if seen != set(expected):
        raise ValueError("OFFICIAL_IMAGES_MISSING_FROM_ARCHIVE")
    rows.sort(key=lambda r: (r["dataset"], r["image_path"]))
    manifest = output / "fingerprints.jsonl"
    manifest.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    exact, near = exact_groups(rows), screen(rows)
    (output / "exact-groups.json").write_text(json.dumps(exact, indent=2), encoding="utf-8")
    (output / "near-screen.json").write_text(json.dumps(near, indent=2), encoding="utf-8")
    report = {"status": "CANONICAL_AUDIT_COMPLETE_REVIEW_REQUIRED", "images": len(rows),
              "scope_counts": dict(sorted(Counter(scope(r) for r in rows).items())),
              "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
              "input_manifest_hashes": {"parcel3d": PARCEL_SHA, "visa": VISA_SHA},
              "visa_archive_sha256": ARCHIVE_SHA, "exact_groups": len(exact),
              "exact_cross_boundary_groups": sum(g["cross_boundary"] for g in exact),
              "near_candidate_pairs": near["candidate_pairs"], "training_ready": False,
              "test_pixels_used_for": "HYGIENE_ONLY_NO_TRAINING_OR_MODEL_EVALUATION",
              "split_reassigned": False, "serving_changed": False,
              "fingerprint_definition": "RGB:width:height: prefix; LANCZOS right>left dHash; 32px DCT pHash DC=0",
              "limits": ["Perceptual matches are review candidates, not proof of leakage.",
                         "Full-frame screening may miss crops, rotations and shared objects with new views.",
                         "VisA physical-instance identities remain unknown.",
                         "No training data or existing source manifest is modified.",
                         "No pixels are exported; source licenses remain unchanged."]}
    (output / "audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return report
