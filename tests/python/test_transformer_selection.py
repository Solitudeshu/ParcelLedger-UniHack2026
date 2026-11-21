import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import torch
from PIL import Image
from torch import nn
from torch.nn import functional as F

from ml import evaluate_transformer_selection as evaluation
from ml.models.paired_transformer import PairedChangeTransformer


def test_unknown_pixels_are_not_false_alarms_or_positive_accuracy():
    p = np.ones((8, 8))
    y = np.zeros((8, 8))
    empty = evaluation.pixel_metrics(p, y, np.zeros((8, 8)))
    assert empty["fp"] == 0 and empty["image_flag"] is None
    assert empty["positive_iou"] is None
    valid = np.zeros((8, 8))
    valid[:4, :4] = 1
    negative = evaluation.pixel_metrics(p, y, valid)
    assert negative["fp"] == 16 and negative["image_flag"] is True
    assert negative["positive_iou"] is None
    summary = evaluation.aggregate([negative, empty])
    assert summary["negative_false_alarm_rate"] == 1
    assert summary["unknown_only"] == 1 and summary["positive_mean_iou"] is None


def test_reference_ignoring_predictions_have_zero_sensitivity():
    rows = []
    for mode in evaluation.MODES:
        metrics = evaluation.pixel_metrics(np.full((8, 8), .8), np.ones((8, 8)), np.ones((8, 8)))
        rows.append({"source": "box", "lineage": "box", "mode": mode, "scenario": 1, **metrics})
    report = evaluation.summarize(rows)
    assert report["reference_sensitivity"]["mean_target_probability_drop_when_reference_is_query"] == 0


class FixtureEncoder(nn.Module):
    def __init__(self, *args):
        super().__init__()
        self.channels = 8
        self.project = nn.Conv2d(3, 8, 1).requires_grad_(False)

    def forward(self, image):
        return [self.project(F.avg_pool2d(image, 14))] * 4


def test_checkpoint_diagnostics_leave_corrupt_test_pixels_unread(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    rows, images, annotations = [], [], []
    for i in range(6):
        name = f"selection-{i}.png"
        Image.fromarray(np.full((56, 56, 3), 160, np.uint8)).save(root / name)
        rows.append({"image_path": name, "lineage_id": str(i), "source_split": "validation",
                     "role": "selection", "sha256": evaluation.sha256_file(root / name)})
        images.append({"id": i, "file_name": name})
        annotations.append({"image_id": i, "bbox_mode": 1, "bbox": [4, 4, 48, 48],
                            "segmentation": [[4, 4, 51, 4, 51, 51, 4, 51]]})
    for split in ("train", "validation", "test"):
        (root / f"parcel3d_{split}.json").write_text(json.dumps({"images": images, "annotations": annotations}))
    for role in ("calibration", "test"):
        name = f"{role}.png"
        (root / name).write_bytes(b"THIS_IS_NOT_AN_IMAGE")
        rows.append({"image_path": name, "lineage_id": role, "source_split": role,
                     "role": role, "sha256": "deliberately-invalid"})
    manifest = tmp_path / "images.jsonl"
    manifest.write_text("\n".join(json.dumps(row) for row in rows))
    config = {"encoder": "dinov2_vitl14", "lora_blocks": 0, "size": 56, "width": 8}
    repo = Path(__file__).resolve().parents[2]
    names = ("ml/models/paired_transformer.py", "ml/train/paired_transformer.py", "ml/data/parcel_pairs_v2.py")
    checkpoint = tmp_path / "best.pt"
    identity = {"config": config, "manifest_sha256": evaluation.sha256_file(manifest),
                "source_hashes": {name: evaluation.sha256_file(repo / name) for name in names}}
    torch.save({"identity": identity, "epoch": 1, "weights": {
        "encoder": {}, "decoder": PairedChangeTransformer(8, 8).state_dict()}}, checkpoint)
    monkeypatch.setattr(evaluation, "DinoSpatialEncoder", FixtureEncoder)
    digest = evaluation.sha256_file(checkpoint)
    report = evaluation.run(root, manifest, checkpoint, digest, tmp_path / "output", fixture=True)
    assert report["modes"]["paired"]["overall"]["samples"] == 6
    assert report["test"] == "NOT_READ" and report["calibration"] == "NOT_RUN"
    assert report["promotion"] == "HOLD" and report["scope"] == "SOFTWARE_FIXTURE"
    with pytest.raises(ValueError, match="CHECKPOINT_HASH_MISMATCH"):
        evaluation.run(root, manifest, checkpoint, hashlib.sha256(b"wrong").hexdigest(), tmp_path / "bad", fixture=True)
    with manifest.open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="CHECKPOINT_DATA_MISMATCH"):
        evaluation.run(root, manifest, checkpoint, digest, tmp_path / "bad", fixture=True)
