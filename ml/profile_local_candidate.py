"""Isolated GPU smoke and interim reload parity; not physical accuracy or rollout.

Uses procedural RGB pixels only. The serving worker/source/weights are mounted
read-only. Official model downloads go to a separate cache, never the A cache.
"""
from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import importlib.util
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch

from ml.learned_matching import fit_alignment
from ml.models.paired_transformer import DinoSpatialEncoder, PairedChangeTransformer
from ml.roma_matching import RomaMatcher, sha256_file
from ml.train.paired_transformer import load_trainable, trainable_state


def fixture():
    rng = np.random.default_rng(26)
    rgb = np.full((518, 518, 3), (180, 155, 115), np.uint8)
    for _ in range(120):
        x, y = rng.integers(10, 480, 2)
        color = tuple(int(v) for v in rng.integers(30, 240, 3))
        cv2.rectangle(rgb, (int(x), int(y)), (int(x + 15), int(y + 10)), color, -1)
    cv2.putText(rgb, "KIEN CHUNG 26", (25, 260), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (25, 25, 25), 3)
    homography = np.float64([[.9, .02, 24], [-.015, .92, 18], [.00008, -.00004, 1.]])
    query = cv2.warpPerspective(rgb, homography, (518, 518))
    cv2.line(query, (240, 270), (300, 300), (35, 30, 25), 5)
    return rgb, query


def checked_checkpoint(candidate):
    root = Path(f"/app/artifacts/kaggle/transformer-{candidate}-v1")
    receipt = json.loads(Path(str(root) + "-outputs.json").read_bytes())
    if receipt["version"] != 1 or receipt["run_status"] != "COMPLETE":
        raise ValueError("INTERIM_CHECKPOINT_COLLECTION_REQUIRED")
    entry = next(row for row in receipt["files"] if row["name"] == "training/best.pt")
    path = root / entry["name"]
    if sha256_file(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]:
        raise ValueError("INTERIM_CHECKPOINT_IDENTITY_CHANGED")
    saved = torch.load(path, map_location="cpu", weights_only=True)
    for name, digest in saved["identity"]["source_hashes"].items():
        source = (Path("/app") / name).resolve()
        if not source.is_relative_to("/app") or sha256_file(source) != digest:
            raise ValueError("INTERIM_CHECKPOINT_SOURCE_CHANGED")
    for group in saved["weights"].values():
        if not all(torch.isfinite(t).all() for t in group.values()):
            raise ValueError("NONFINITE_INTERIM_WEIGHTS")
    return saved, entry["sha256"]


def pair_tensor(query, reference, valid, size):
    tensors = []
    for image in (query, reference):
        rgb = cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)
        tensors.append(torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1))).float()[None].cuda() / 255)
    mask = cv2.resize(valid.astype(np.float32), (size, size), interpolation=cv2.INTER_NEAREST)
    return (*tensors, torch.from_numpy(mask)[None, None].cuda())


def predict(encoder, decoder, q, r, valid):
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
        features = encoder(torch.cat([q, r]))
        logits = decoder([x[:1] for x in features], [x[1:] for x in features], q, r, valid)
    probability = logits.float().sigmoid()
    if not torch.isfinite(probability).all():
        raise ValueError("NONFINITE_LOCAL_INFERENCE")
    return probability


def main():
    output = Path("/runoutputs")
    output.mkdir(parents=True, exist_ok=True)
    if not torch.cuda.is_available():
        raise ValueError("LOCAL_GPU_NOT_AVAILABLE")
    torch.set_num_threads(2)
    torch.set_float32_matmul_precision("highest")
    torch.cuda.set_per_process_memory_fraction(.9)
    torch.manual_seed(26)
    report = {"status": "RUNNING", "scope": "PROCEDURAL_LOCAL_GPU_SMOKE_INTERIM_CHECKPOINTS",
              "gpu": torch.cuda.get_device_name(), "torch": torch.__version__,
              "bf16_native": torch.cuda.is_bf16_supported(including_emulation=False),
              "fused_local_corr_installed": importlib.util.find_spec("local_corr") is not None,
              "packages": {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "romav2", "numpy", "opencv-python-headless")},
              "candidates": {}, "promotion": "HOLD", "real_box_validation": "NOT_RUN",
              "limits": ["One generated reference/query pair; not accuracy or latency over a dataset.",
                         "Warm timing includes one RoMa pair, homography, warp and one 392px change inference.",
                         "No gallery retrieval, acceptance calibration, API, network or queue timing.",
                         "Both decoders are interim session-1 best checkpoints, not the completed training runs.",
                         "Source renders used for pretraining remain academic-only; this is research-only."]}
    def save():
        (output / "profile.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    save()
    before = time.perf_counter()
    matcher = RomaMatcher("fast", "/models/roma")
    torch.cuda.synchronize()
    report["matcher_load_seconds_including_cache_or_download"] = time.perf_counter() - before
    reference, query = fixture()
    for candidate in ("c2", "c3"):
        row = {"status": "RUNNING"}
        report["candidates"][candidate] = row
        try:
            saved, digest = checked_checkpoint(candidate)
            config = saved["identity"]["config"]
            row.update(checkpoint_sha256=digest, selected_epoch=saved["epoch"], identity=saved["identity"])
            before = time.perf_counter()
            encoder = DinoSpatialEncoder(config["encoder"], config["lora_blocks"], "/models/dino").cuda().eval()
            if sha256_file(Path("/models/dino/checkpoints") / f"{config['encoder']}_pretrain.pth") != saved["identity"]["backbone_sha256"]:
                raise ValueError("LOCAL_BACKBONE_IDENTITY_CHANGED")
            decoder = PairedChangeTransformer(encoder.channels, config["width"]).cuda().eval()
            load_trainable(saved["weights"], encoder, decoder)
            torch.cuda.synchronize()
            row["change_load_seconds_including_cache_or_download"] = time.perf_counter() - before
            times, probabilities = [], []
            torch.cuda.reset_peak_memory_stats()
            for attempt in range(4):
                torch.cuda.synchronize()
                before = time.perf_counter()
                rp, qp, statistics = matcher.match(reference, query)
                fit = fit_alignment(rp, qp, query.shape)
                if fit["homography"] is None:
                    raise ValueError("PROCEDURAL_ALIGNMENT_FAILED")
                aligned = cv2.warpPerspective(reference, fit["homography"], (518, 518))
                valid = cv2.warpPerspective(np.ones((518, 518), np.uint8), fit["homography"], (518, 518), flags=cv2.INTER_NEAREST)
                q, r, v = pair_tensor(query, aligned, valid, config["size"])
                probability = predict(encoder, decoder, q, r, v)
                torch.cuda.synchronize()
                elapsed = time.perf_counter() - before
                if attempt:
                    times.append(elapsed)
                    probabilities.append(probability.detach().cpu())
                else:
                    row["first_pipeline_seconds"] = elapsed
                row["matching"] = {**statistics, **{k: value for k, value in fit.items() if k != "homography"}}
            row.update(repeated_pipeline_seconds=times,
                       peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                       peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                       repeated_probability_max_abs_difference=float(max((p - probabilities[0]).abs().max() for p in probabilities)))
            # Save trainable parameters only, perturb the live adapter, and reload
            # into a fresh decoder to verify an actual export/reload path.
            export = output / f"{candidate}-interim-trainable.pt"
            torch.save({"identity": saved["identity"], "source_checkpoint_sha256": digest,
                        "weights": trainable_state(encoder, decoder)}, export)
            reloaded = torch.load(export, map_location="cpu", weights_only=True)
            with torch.no_grad():
                for parameter in encoder.parameters():
                    if parameter.requires_grad:
                        parameter.zero_()
            restored_decoder = PairedChangeTransformer(encoder.channels, config["width"]).cuda().eval()
            load_trainable(reloaded["weights"], encoder, restored_decoder)
            restored = predict(encoder, restored_decoder, q, r, v).detach().cpu()
            difference = float((restored - probabilities[-1]).abs().max())
            if difference > 1e-5:
                raise ValueError("LOCAL_INTERIM_RELOAD_PARITY_FAILED")
            row.update(status="PASS", export_sha256=sha256_file(export), reload_probability_max_abs_difference=difference,
                       reload_parity="PASS", export_is_final_model=False,
                       output_probability_sha256=hashlib.sha256(restored.numpy().tobytes()).hexdigest())
            del encoder, decoder, restored_decoder, q, r, v, probability
            gc.collect()
            torch.cuda.empty_cache()
        except (RuntimeError, ValueError) as exc:
            row.update(status="FAILED", error_type=type(exc).__name__, error_message=str(exc)[:800])
            save()
            raise
        save()
        print(json.dumps({"candidate": candidate, "status": row["status"], "repeated_pipeline_seconds": times,
                          "peak_allocated_bytes": row["peak_allocated_bytes"], "reload_parity": row["reload_parity"]}), flush=True)
    report["status"] = "LOCAL_GPU_SMOKE_AND_INTERIM_PARITY_PASS"
    save()


if __name__ == "__main__":
    main()
