"""CPU-only audit of a mounted Parcel3D input. Does not copy its image archive."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
from collections import Counter, deque
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from PIL import Image

SOURCE = "ayesha19765/parcel3d"
ORIGINAL = "https://zenodo.org/records/8032204"
ALLOWED_SPLITS = ("train", "validation", "test")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def within_root(root: Path, relative: str) -> Path:
    relative = relative.replace("\\", "/")
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or ".." in parsed.parts or ":" in relative:
        raise ValueError("UNSAFE_ANNOTATION_PATH")
    result = (root / Path(*parsed.parts)).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("PATH_OUTSIDE_INPUT")
    return result


def find_annotations(input_root: Path) -> dict[str, Path]:
    # Kaggle can expose both legacy and owner-qualified aliases of one input.
    # Annotation files sit above per-image directories; never walk 13,400 samples
    # just to locate three files. Bound discovery and compare duplicate contents.
    roots, queue, visited = [], deque([(input_root, 0)]), set()
    while queue:
        directory, depth = queue.popleft()
        resolved = directory.resolve()
        if resolved in visited:
            continue
        visited.add(resolved)
        if len(visited) > 200:
            raise RuntimeError("ANNOTATION_DISCOVERY_LIMIT")
        if all((directory / f"parcel3d_{split}.json").is_file() for split in ALLOWED_SPLITS):
            roots.append(directory)
            continue
        if depth < 5:
            for child in sorted(directory.iterdir()):
                if (
                    child.name not in {*ALLOWED_SPLITS, "misc"}
                    and not child.name.startswith(".")
                    and child.is_dir()
                ):
                    queue.append((child, depth + 1))
    if not roots:
        raise RuntimeError("ANNOTATION_ROOT_NOT_FOUND")
    roots.sort(key=lambda path: (len(path.parts), str(path)))
    if len(roots) > 1:
        digests = {
            tuple(sha256_file(root / f"parcel3d_{split}.json") for split in ALLOWED_SPLITS)
            for root in roots
        }
        if len(digests) != 1:
            raise RuntimeError("ANNOTATION_ALIASES_HAVE_DIFFERENT_CONTENT")
    print(f"Annotation roots: {len(roots)} equivalent mount(s); selected {roots[0]}", flush=True)
    return {split: roots[0] / f"parcel3d_{split}.json" for split in ALLOWED_SPLITS}


def resolve_image(annotation_path: Path, image_record: dict, split: str) -> Path | None:
    filename = image_record.get("file_name")
    if not isinstance(filename, str):
        return None
    candidates = [
        within_root(annotation_path.parent, filename),
        within_root(annotation_path.parent, f"{split}/{filename}"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def audit(input_root: Path, output_root: Path, source_version: int = 1) -> dict:
    input_root = input_root.resolve()
    output_root = output_root.resolve()
    if output_root.is_relative_to(input_root):
        raise ValueError("OUTPUT_MUST_NOT_BE_INSIDE_READ_ONLY_INPUT")
    output_root.mkdir(parents=True, exist_ok=True)
    print("Locating Parcel3D annotation roots (bounded discovery)", flush=True)
    annotations = find_annotations(input_root)
    result = {
        "schema_version": "parcel3d-audit-v2",
        "source": SOURCE,
        "source_version": source_version,
        "original_record": ORIGINAL,
        "checked_at": datetime.now(UTC).isoformat(),
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
        "source_kind": "SYNTHETIC",
        "splits": {},
        "errors": [],
        "license": {
            "mirror_metadata": "Apache-2.0; not authoritative for upstream assets",
            "original_full_dataset": "Academic research only; Zenodo other-nc",
            "textureless_obj_meshes": "CC-BY-4.0, separate grant in original record",
            "cardboard_textures": "CC-BY-4.0, separate record 8041823",
            "training_policy": "Full rendered-image training remains gated pending scope review.",
            "commercial_release": "NOT_CLEARED_FOR_ORIGINAL_RENDERED_IMAGES",
            "reference_matching_labels": "Must inspect; COCO damage labels alone do not establish cross-view correspondence or evidence sufficiency.",
        },
    }
    for package in ("numpy", "Pillow", "torch", "opencv-python"):
        try:
            result["runtime"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    group_sets = {}
    sample_directories = set()
    for split, path in annotations.items():
        print(f"Auditing {split}: {path.name}", flush=True)
        document = json.loads(path.read_text(encoding="utf-8"))
        images = document.get("images", [])
        labels = document.get("annotations", [])
        record = {
            "annotation_path": str(path.relative_to(input_root)),
            "annotation_sha256": sha256_file(path),
            "annotation_bytes": path.stat().st_size,
            "top_level_keys": sorted(document),
            "image_count": len(images),
            "annotation_count": len(labels),
            "categories": document.get("categories", []),
            "image_fields": dict(Counter(k for image in images for k in image)),
            "annotation_fields": dict(Counter(k for annotation in labels for k in annotation)),
            "sample_records": images[:2],
            "sample_annotations": labels[:2],
            "samples": [],
        }
        ids = [image.get("id") for image in images]
        record["duplicate_image_ids"] = len(ids) - len(set(ids))
        group_key = next(
            (
                key
                for key in ("mesh_id", "object_id", "parcel_id", "model_id")
                if images and all(key in item for item in images)
            ),
            None,
        )
        record["explicit_group_key"] = group_key
        record["group_split_verified"] = False
        if group_key:
            group_sets[split] = {str(item[group_key]) for item in images}
        # Deterministic stratified positions; no exhaustive image read or copy.
        positions = sorted({int(i * max(len(images) - 1, 0) / 7) for i in range(8)})
        for index in positions if images else []:
            image_record = images[index]
            image_path = resolve_image(path, image_record, split)
            sample = {
                "image_id": image_record.get("id"),
                "file_name": image_record.get("file_name"),
            }
            if image_path is None:
                sample["status"] = "PATH_UNRESOLVED"
                result["errors"].append(
                    {"split": split, "image_id": sample["image_id"], "code": "PATH_UNRESOLVED"}
                )
            else:
                try:
                    with Image.open(image_path) as image:
                        image.load()
                        sample.update(
                            status="READ_OK",
                            width=image.width,
                            height=image.height,
                            format=image.format,
                            mode=image.mode,
                        )
                    sample["sha256"] = sha256_file(image_path)
                    sample["bytes"] = image_path.stat().st_size
                    sample["relative_path"] = str(image_path.relative_to(input_root))
                    sample_directories.add(image_path.parent)
                except (OSError, ValueError) as exc:
                    sample.update(status="READ_FAILED", error_type=type(exc).__name__)
                    result["errors"].append(
                        {"split": split, "image_id": sample["image_id"], "code": "READ_FAILED"}
                    )
            record["samples"].append(sample)
        result["splits"][split] = record
    root = annotations["train"].parent
    extensions = Counter()
    mesh_examples = []
    metadata_examples = []
    # Inventory only sampled image neighborhoods. Do not imply a complete count.
    print(f"Inspecting metadata/meshes near {len(sample_directories)} sampled images", flush=True)
    for sample_directory in sorted(sample_directories):
        for directory, _, filenames in os.walk(sample_directory):
            for filename in sorted(filenames):
                suffix = Path(filename).suffix.lower()
                extensions[suffix] += 1
                path = Path(directory) / filename
                if suffix == ".obj" and len(mesh_examples) < 8:
                    mesh_examples.append(
                        {
                            "path": str(path.relative_to(root)),
                            "bytes": path.stat().st_size,
                            "sha256": sha256_file(path),
                        }
                    )
                if (
                    suffix == ".json"
                    and path not in annotations.values()
                    and len(metadata_examples) < 8
                ):
                    meta = {"path": str(path.relative_to(root)), "bytes": path.stat().st_size}
                    if path.stat().st_size < 100_000:
                        try:
                            meta["document"] = json.loads(path.read_text(encoding="utf-8"))
                        except (ValueError, UnicodeError):
                            meta["status"] = "UNREADABLE_JSON"
                    metadata_examples.append(meta)
    result["inventory"] = {
        "scope": "SAMPLED_IMAGE_NEIGHBORHOODS",
        "sample_directory_count": len(sample_directories),
        "sampled_extension_counts": dict(extensions),
        "mesh_examples": mesh_examples,
        "metadata_examples": metadata_examples,
    }
    if len(group_sets) == 3:
        overlaps = {
            f"{a}:{b}": len(group_sets[a] & group_sets[b])
            for a, b in (("train", "validation"), ("train", "test"), ("validation", "test"))
        }
        result["group_overlap_counts"] = overlaps
        for record in result["splits"].values():
            record["group_split_verified"] = not any(overlaps.values())
    result["mount_read_gate"] = (
        "PASS"
        if not result["errors"]
        and all(
            any(s["status"] == "READ_OK" for s in record["samples"])
            for record in result["splits"].values()
        )
        else "FAIL"
    )
    result["correspondence_label_gate"] = "PENDING_REVIEW"
    result["commercial_license_gate"] = "HOLD_ORIGINAL_RENDERS"
    result["full_archive_copied"] = False
    target = output_root / "parcel3d-audit.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "audit-sha256.txt").write_text(
        sha256_file(target) + "  parcel3d-audit.json\n", encoding="ascii"
    )
    print(
        json.dumps(
            {
                "mount_read_gate": result["mount_read_gate"],
                "counts": {k: v["image_count"] for k, v in result["splits"].items()},
                "outputs": str(output_root),
            },
            ensure_ascii=False,
        )
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("/kaggle/input"))
    parser.add_argument("--output", type=Path, default=Path("/kaggle/working/unihack26-audit"))
    parser.add_argument("--source-version", type=int, default=1)
    args = parser.parse_args()
    audit(args.input, args.output, args.source_version)
