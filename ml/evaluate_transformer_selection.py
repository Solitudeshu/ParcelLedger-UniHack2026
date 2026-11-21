"""Selection-only diagnostics. Never calibrate thresholds or read final-test pixels."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

from ml.data.parcel3d_audit import find_annotations
from ml.data.parcel_pairs_v2 import ParcelPairs
from ml.models.paired_transformer import DinoSpatialEncoder, PairedChangeTransformer
from ml.train.paired_transformer import load_trainable

MODES = ("paired", "query_as_reference", "constant_reference")
THRESHOLD = .5
MIN_PIXELS = 16


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_metrics(probability, target, valid):
    """All metrics exclude unknown pixels; empty-positive IoU is not an accuracy."""
    p, y, v = [np.asarray(value).squeeze() for value in (probability, target, valid)]
    if p.shape != y.shape or p.shape != v.shape or p.ndim != 2:
        raise ValueError("METRIC_SHAPE_MISMATCH")
    if not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
        raise ValueError("INVALID_PROBABILITY")
    observed = v > .5
    positive = (y > .5) & observed
    predicted = (p >= THRESHOLD) & observed
    tp = int((positive & predicted).sum())
    fp = int((~positive & predicted).sum())
    fn = int((positive & ~predicted).sum())
    return {"valid_pixels": int(observed.sum()), "positive_pixels": int(positive.sum()),
            "predicted_pixels": int(predicted.sum()), "tp": tp, "fp": fp, "fn": fn,
            "positive_iou": tp / (tp + fp + fn) if positive.any() else None,
            "positive_dice": 2 * tp / (2 * tp + fp + fn) if positive.any() else None,
            "positive_recall": tp / (tp + fn) if positive.any() else None,
            "image_flag": bool(predicted.sum() >= MIN_PIXELS) if observed.any() else None,
            "target_mean_probability": float(p[positive].mean()) if positive.any() else None}


def aggregate(rows):
    usable = [row for row in rows if row["valid_pixels"] > 0]
    positive = [row for row in usable if row["positive_pixels"] > 0]
    negative = [row for row in usable if row["positive_pixels"] == 0]
    def mean(selected, key):
        return float(np.mean([row[key] for row in selected])) if selected else None
    return {"samples": len(rows), "unknown_only": len(rows) - len(usable),
            "positive_samples": len(positive), "negative_samples": len(negative),
            "positive_mean_iou": mean(positive, "positive_iou"),
            "positive_mean_dice": mean(positive, "positive_dice"),
            "positive_mean_pixel_recall": mean(positive, "positive_recall"),
            "positive_image_flag_rate": mean(positive, "image_flag"),
            "negative_false_alarm_rate": mean(negative, "image_flag"),
            "target_mean_probability": mean(positive, "target_mean_probability")}


def summarize(rows):
    modes = {}
    for mode in MODES:
        selected = [row for row in rows if row["mode"] == mode]
        modes[mode] = {"overall": aggregate(selected), "by_scenario": {
            str(scenario): aggregate([row for row in selected if row["scenario"] == scenario])
            for scenario in sorted({row["scenario"] for row in selected})}}
    paired = {row["source"]: row for row in rows if row["mode"] == "paired"}
    differences = []
    for row in rows:
        if row["mode"] == "query_as_reference" and row["target_mean_probability"] is not None:
            differences.append(paired[row["source"]]["target_mean_probability"] - row["target_mean_probability"])
    return {"modes": modes, "source_objects": len({row["lineage"] for row in rows}),
            "reference_sensitivity": {
                "positive_samples": len(differences),
                "mean_target_probability_drop_when_reference_is_query": float(np.mean(differences)) if differences else None,
                "interpretation": "Diagnostic only; a positive drop supports reference dependence, not physical accuracy."}}


def run(input_root, manifest, checkpoint, checkpoint_sha, output, fixture=False):
    if sha256_file(checkpoint) != checkpoint_sha:
        raise ValueError("CHECKPOINT_HASH_MISMATCH")
    saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
    identity = saved["identity"]
    if "weights" not in saved or "epoch" not in saved:
        raise ValueError("BEST_SELECTION_CHECKPOINT_REQUIRED")
    root = Path(__file__).resolve().parents[1]
    for name, expected in identity["source_hashes"].items():
        source = (root / name).resolve()
        if not source.is_relative_to(root) or sha256_file(source) != expected:
            raise ValueError("CHECKPOINT_SOURCE_MISMATCH")
    if sha256_file(manifest) != identity["manifest_sha256"]:
        raise ValueError("CHECKPOINT_DATA_MISMATCH")
    config = identity["config"]
    device = torch.device("cuda" if torch.cuda.is_available() and not fixture else "cpu")
    if not fixture and device.type != "cuda":
        raise ValueError("DIAGNOSTICS_REQUIRES_GPU")
    torch.set_num_threads(2)
    encoder = DinoSpatialEncoder(config["encoder"], config["lora_blocks"], "/kaggle/temp/kc-model-cache").to(device)
    if not fixture:
        backbone = Path(torch.hub.get_dir()) / "checkpoints" / f"{config['encoder']}_pretrain.pth"
        if sha256_file(backbone) != identity["backbone_sha256"]:
            raise ValueError("CHECKPOINT_BACKBONE_MISMATCH")
    decoder = PairedChangeTransformer(encoder.channels, config["width"]).to(device)
    load_trainable(saved["weights"], encoder, decoder)
    encoder.eval()
    decoder.eval()
    dataset = ParcelPairs(find_annotations(Path(input_root))["train"].parent, manifest,
                          "selection", config["size"], fixture=fixture)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows, latencies = [], []
    with torch.no_grad(), (output / "selection-predictions.jsonl").open("w", encoding="utf-8") as stream:
        for batch in DataLoader(dataset, batch_size=1, num_workers=0):
            q, r, valid = [batch[key].to(device) for key in ("query", "reference", "valid")]
            for mode in MODES:
                reference = r if mode == "paired" else (q if mode == "query_as_reference" else torch.full_like(r, .5))
                if device.type == "cuda":
                    torch.cuda.synchronize()
                started = time.perf_counter()
                with torch.autocast(device.type, dtype=torch.float16, enabled=device.type == "cuda"):
                    features = encoder(torch.cat([q, reference]))
                    logits = decoder([x[:1] for x in features], [x[1:] for x in features], q, reference, valid)
                probability = logits.float().sigmoid().cpu().numpy()
                latencies.append(time.perf_counter() - started)
                row = {"source": batch["source"][0], "lineage": batch["lineage"][0],
                       "scenario": int(batch["scenario"][0]), "mode": mode,
                       **pixel_metrics(probability, batch["target"].numpy(), batch["valid"].numpy())}
                stream.write(json.dumps(row) + "\n")
                rows.append(row)
    report = {"status": "SELECTION_DIAGNOSTICS_COMPLETE", "client_date": "2026-11-15",
              "identity": identity, "checkpoint_sha256": checkpoint_sha, "selected_epoch": saved["epoch"],
              "scope": "SOFTWARE_FIXTURE" if fixture else "SYNTHETIC_APPEARANCE_SELECTION_ONLY",
              "threshold": THRESHOLD, "image_flag_min_pixels": MIN_PIXELS,
              "calibration": "NOT_RUN", "test": "NOT_READ", "real_box_validation": "NOT_RUN", "promotion": "HOLD",
              **summarize(rows), "inference_seconds": {"p50": float(np.percentile(latencies, 50)),
                                                        "p95": float(np.percentile(latencies, 95))},
              "limitations": ["Known foreground crop and alignment; not end-to-end matching.",
                              "Fixed diagnostic thresholds are not a calibrated operating point.",
                              "Image flag rate is not localization/region recall; see IoU and pixel recall.",
                              "Latency excludes image loading, upstream matching, queue and network.",
                              "No measured comparison with legacy A in this notebook."]}
    (output / "selection-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "epoch": saved["epoch"], **report["reference_sensitivity"]}), flush=True)
    return report
