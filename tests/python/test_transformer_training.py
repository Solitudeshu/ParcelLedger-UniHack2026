import hashlib
import json

import numpy as np
import pytest
import torch
from PIL import Image
from torch import nn
from torch.nn import functional as F

from ml.data.parcel_pairs_v2 import appearance_pair
from ml.train import paired_transformer as training


def test_new_and_preexisting_marks_have_different_targets():
    rgb = np.full((56, 56, 3), 170, np.uint8)
    foreground = np.zeros((56, 56), np.uint8)
    foreground[5:51, 5:51] = 1
    new = appearance_pair(rgb, foreground, 42, 1)
    old = appearance_pair(rgb, foreground, 42, 3)
    assert new["target"].sum() > 0 and old["target"].sum() == 0
    assert new["query"].shape == (3, 56, 56)
    assert torch.equal(new["query"], appearance_pair(rgb, foreground, 42, 1)["query"])
    assert (new["target"][0].numpy() * (1 - foreground)).sum() == 0
    occluded = appearance_pair(rgb, foreground, 42, 5)
    assert (occluded["valid"] == 0).sum() > (new["valid"] == 0).sum()


class TinyEncoder(nn.Module):
    def __init__(self, *args):
        super().__init__()
        self.channels = 8
        self.project = nn.Conv2d(3, 8, 1).requires_grad_(False)

    def forward(self, image):
        value = self.project(F.avg_pool2d(image, 14))
        return [value] * 4


def corpus(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    rows = []
    rng = np.random.default_rng(17)
    for split, count in (("train", 3), ("validation", 2), ("test", 1)):
        images, annotations = [], []
        (root / split).mkdir()
        for i in range(count):
            name = f"{split}/{i}.png"
            Image.fromarray(rng.integers(70, 210, (56, 56, 3), dtype=np.uint8)).save(root / name)
            images.append({"id": i, "file_name": name})
            annotations.append({"image_id": i, "bbox_mode": 1, "bbox": [4, 4, 48, 48],
                                "segmentation": [[4, 4, 51, 4, 51, 51, 4, 51]]})
            rows.append({"image_path": name, "lineage_id": name, "source_split": split,
                         "role": "selection" if split == "validation" else split,
                         "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()})
        (root / f"parcel3d_{split}.json").write_text(json.dumps({"images": images, "annotations": annotations}))
    manifest = tmp_path / "images.jsonl"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
    # Deliberately corrupt test bytes: pretraining must never read test images.
    (root / "test/0.png").write_bytes(b"TEST_MUST_REMAIN_UNTOUCHED")
    return root, manifest


def test_resume_mid_epoch_matches_uninterrupted_training_without_test_access(tmp_path, monkeypatch):
    monkeypatch.setattr(training, "DinoSpatialEncoder", TinyEncoder)
    root, manifest = corpus(tmp_path)
    config = {"encoder": "dinov2_vitl14", "lora_blocks": 0, "size": 56, "width": 32,
              "seed": 26, "epochs": 2, "accumulation_steps": 1, "head_lr": .001,
              "adapter_lr": .0001, "max_seconds": 10000}
    full = training.run(root, manifest, tmp_path / "full", config, {}, fixture=True)
    clock_values = iter([0., 0., 100., 100.])
    partial = training.run(root, manifest, tmp_path / "partial", {**config, "max_seconds": 50}, {},
                           fixture=True, clock=lambda: next(clock_values, 100.))
    assert partial["next_sample_offset"] == 1 and partial["optimizer_updates"] == 1
    resumed = training.run(root, manifest, tmp_path / "resumed", config, {},
                           resume=tmp_path / "partial/resume.pt", fixture=True)
    assert full["optimizer_updates"] == resumed["optimizer_updates"] == 6
    assert full["epochs_completed"] == resumed["epochs_completed"] == 2
    a = torch.load(tmp_path / "full/resume.pt", weights_only=True)
    b = torch.load(tmp_path / "resumed/resume.pt", weights_only=True)
    assert a["scheduler"] == b["scheduler"]
    for key, value in a["last"]["decoder"].items():
        assert torch.allclose(value, b["last"]["decoder"][key], atol=1e-7, rtol=1e-6)
    assert resumed["source_kind"] == "SOFTWARE_FIXTURE" and resumed["test_evaluation"] == "NOT_RUN"
    with pytest.raises(ValueError, match="CONFIGURATION_OR_DATA"):
        training.run(root, manifest, tmp_path / "bad", {**config, "seed": 99}, {},
                     resume=tmp_path / "partial/resume.pt", fixture=True)
