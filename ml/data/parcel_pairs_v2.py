"""Appearance-change pretraining on audited Parcel3D pixels; not physical dent labels."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset

from ml.data.parcel3d_audit import within_root

MANIFEST_SHA = "267dd46092d3429dc66f539fb897e79b4be3f68beb94b75cd19f7289250e4d38"
PAIR_VERSION = "parcel-appearance-pairs-v2"


def appearance_pair(rgb, foreground, seed, scenario):
    """Aligned pair and exact masks for introduced appearance changes only."""
    rng = np.random.default_rng(seed)
    h, w = foreground.shape
    reference = rgb.copy()
    region = cv2.erode(foreground.astype(np.uint8), np.ones((7, 7), np.uint8))
    coordinates = np.argwhere(region > 0)
    if len(coordinates) < 16:
        raise ValueError("INSUFFICIENT_FOREGROUND")
    y, x = coordinates[int(rng.integers(len(coordinates)))]
    change = np.zeros((h, w), np.uint8)
    if scenario in (1, 3):
        end = (int(np.clip(x + rng.integers(-w // 6, w // 6 + 1), 0, w - 1)),
               int(np.clip(y + rng.integers(-h // 6, h // 6 + 1), 0, h - 1)))
        cv2.line(change, (int(x), int(y)), end, 1, int(rng.integers(2, 6)))
    elif scenario in (2, 5):
        radius = max(4, min(h, w) // 20)
        pts = np.array([x, y]) + rng.integers(-radius, radius + 1, (8, 2))
        cv2.fillConvexPoly(change, cv2.convexHull(pts.astype(np.int32)), 1)
    change *= region
    query = reference.copy()
    if change.any():
        color = rng.integers(20, 240, 3).astype(np.float32)
        alpha = rng.uniform(.45, .95)
        query[change > 0] = np.clip(query[change > 0] * (1 - alpha) + color * alpha, 0, 255)
    if scenario == 3:
        # A pre-existing mark appears in BOTH images. Its target is unchanged.
        reference = query.copy()
        change[:] = 0
    gradient = np.linspace(rng.uniform(.8, 1.2), rng.uniform(.8, 1.2), w)[None, :, None]
    query = np.clip(query * gradient + rng.uniform(-10, 10), 0, 255).astype(np.uint8)
    if rng.random() < .3:
        query = cv2.GaussianBlur(query, (3, 3), .5)
    valid = np.ones((h, w), np.uint8)
    if scenario in (4, 5):
        cx, cy = int(rng.integers(w // 4, w // 2)), int(rng.integers(h // 4, h // 2))
        ex, ey = min(w - 1, cx + w // 5), min(h - 1, cy + h // 5)
        query[cy:ey, cx:ex] = rng.integers(20, 240, 3)
        valid[max(0, cy - 3):min(h, ey + 3), max(0, cx - 3):min(w, ex + 3)] = 0
    shift = np.float32([[1, 0, rng.uniform(-.6, .6)], [0, 1, rng.uniform(-.6, .6)]])
    reference = cv2.warpAffine(reference, shift, (w, h), borderMode=cv2.BORDER_REFLECT_101)
    valid[:3] = valid[-3:] = 0
    valid[:, :3] = valid[:, -3:] = 0
    def convert(a):
        return torch.from_numpy(np.ascontiguousarray(a.transpose(2, 0, 1))).float() / 255
    return {"query": convert(query), "reference": convert(reference),
            "target": torch.from_numpy(change[None].astype(np.float32)),
            "valid": torch.from_numpy(valid[None].astype(np.float32))}


class ParcelPairs(Dataset):
    def __init__(self, root, manifest, role, size=392, fixture=False):
        if role not in ("train", "selection", "calibration", "test") or size % 14:
            raise ValueError("INVALID_ROLE_OR_RESOLUTION")
        self.root, self.role, self.size, self.epoch = Path(root), role, size, 0
        raw = Path(manifest).read_bytes()
        self.manifest_sha256 = hashlib.sha256(raw).hexdigest()
        if not fixture and self.manifest_sha256 != MANIFEST_SHA:
            raise ValueError("AUDITED_MANIFEST_HASH_MISMATCH")
        self.rows = sorted((json.loads(line) for line in raw.splitlines()), key=lambda r: r["image_path"])
        self.rows = [r for r in self.rows if r["role"] == role]
        if not self.rows:
            raise ValueError("EMPTY_ROLE")
        self.annotations = {}
        for split in sorted({r["source_split"] for r in self.rows}):
            document = json.loads((self.root / f"parcel3d_{split}.json").read_text())
            images = {item["id"]: item["file_name"] for item in document["images"]}
            for annotation in document["annotations"]:
                self.annotations[images[annotation["image_id"]]] = annotation

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        row = self.rows[index]
        raw = within_root(self.root, row["image_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("IMAGE_CHECKSUM_MISMATCH")
        import io
        with Image.open(io.BytesIO(raw)) as source:
            rgb = np.asarray(source.convert("RGB"))
        annotation = self.annotations[row["image_path"]]
        if annotation.get("bbox_mode") != 1 or not isinstance(annotation["segmentation"], list):
            raise ValueError("UNSUPPORTED_PARCEL_ANNOTATION")
        foreground = np.zeros(rgb.shape[:2], np.uint8)
        polygons = [np.array(p, dtype=np.float32).reshape(-1, 2).round().astype(np.int32)
                    for p in annotation["segmentation"]]
        cv2.fillPoly(foreground, polygons, 1)
        x, y, w, h = annotation["bbox"]
        pad = int(max(w, h) * .12)
        x0, y0 = max(0, int(x) - pad), max(0, int(y) - pad)
        x1, y1 = min(rgb.shape[1], int(x + w) + pad), min(rgb.shape[0], int(y + h) + pad)
        rgb = cv2.resize(rgb[y0:y1, x0:x1], (self.size, self.size))
        foreground = cv2.resize(foreground[y0:y1, x0:x1], (self.size, self.size), interpolation=cv2.INTER_NEAREST)
        epoch = self.epoch if self.role == "train" else 0
        identity = int(hashlib.sha256(row["image_path"].encode()).hexdigest()[:8], 16)
        scenario = (index + epoch) % 6
        pair = appearance_pair(rgb, foreground, [26, identity, epoch], scenario)
        return {**pair, "source": row["image_path"], "lineage": row["lineage_id"], "scenario": scenario}
