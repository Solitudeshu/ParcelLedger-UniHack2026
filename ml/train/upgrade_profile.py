"""Bounded GPU feasibility measurements; does not produce accuracy claims."""
from __future__ import annotations

import gc
import hashlib
import json
import platform
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from ml.data.parcel3d_audit import find_annotations, within_root
from ml.models.paired_transformer import (
    DinoSpatialEncoder,
    PairedChangeTransformer,
    masked_change_loss,
)


def file_sha(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def run(input_root, output_root, max_seconds=1200):
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    torch.manual_seed(26)
    torch.set_num_threads(2)
    report = {"purpose": "FEASIBILITY_ONLY_NOT_TRAINING_OR_ACCURACY",
              "python": platform.python_version(), "torch": torch.__version__,
              "cuda_available": torch.cuda.is_available(), "measurements": []}

    def save():
        report["elapsed_seconds"] = time.monotonic() - started
        (output_root / "profile.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    if not torch.cuda.is_available():
        report["status"] = "BLOCKED_NO_CUDA"
        save()
        raise RuntimeError("GPU_REQUIRED_NO_CPU_FALLBACK")
    report["gpu"] = torch.cuda.get_device_name(0)
    report["gpu_total_bytes"] = torch.cuda.get_device_properties(0).total_memory
    annotations = find_annotations(Path(input_root))["train"]
    source = json.loads(annotations.read_text())["images"][0]
    source_path = within_root(annotations.parent, source["file_name"])
    with Image.open(source_path) as image:
        source_image = image.convert("RGB")
    report["source_image_sha256"] = file_sha(source_path)
    report["source_split"] = "OFFICIAL_TRAIN_ONLY"
    plans = [("dinov2_vitb14", 392, 0), ("dinov2_vitl14", 392, 0),
             ("dinov2_vitl14", 392, 4), ("dinov2_vitl14", 518, 0)]
    for name, size, lora_blocks in plans:
        if time.monotonic() - started >= max_seconds:
            report["status"] = "PARTIAL_TIME_BUDGET"
            break
        row = {"encoder": name, "size": size, "lora_blocks": lora_blocks, "batch_pairs": 1}
        encoder = decoder = optimizer = None
        try:
            encoder = DinoSpatialEncoder(name, lora_blocks, "/kaggle/temp/kc-model-cache").cuda().train()
            decoder = PairedChangeTransformer(encoder.channels).cuda().train()
            params = [p for module in (encoder, decoder) for p in module.parameters() if p.requires_grad]
            optimizer = torch.optim.AdamW(params, lr=1e-4)
            scaler = torch.amp.GradScaler("cuda")
            array = np.asarray(source_image.resize((size, size)), dtype=np.float32).copy() / 255
            reference = torch.from_numpy(array).permute(2, 0, 1)[None].cuda()
            query = (reference * .9 + .025).clamp(0, 1)
            valid = torch.ones_like(query[:, :1])
            target = torch.zeros_like(valid)
            query[:, :, size // 3:size // 3 + 5, size // 4:3 * size // 4] = .25
            target[:, :, size // 3:size // 3 + 5, size // 4:3 * size // 4] = 1
            row["trainable_parameters"] = sum(p.numel() for p in params)
            row["encoder_parameters"] = sum(p.numel() for p in encoder.parameters())
            torch.cuda.reset_peak_memory_stats()
            timings, losses = [], []
            for step in range(6):
                optimizer.zero_grad(set_to_none=True)
                torch.cuda.synchronize()
                step_start = time.monotonic()
                with torch.autocast("cuda", dtype=torch.float16):
                    features = encoder(torch.cat([query, reference], 0))
                    q, r = [x[:1] for x in features], [x[1:] for x in features]
                    logits = decoder(q, r, query, reference, valid)
                    value = masked_change_loss(logits, target, valid)
                if not torch.isfinite(value):
                    raise RuntimeError("NONFINITE_PROFILE_LOSS")
                scaler.scale(value).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(params, 1.)
                scaler.step(optimizer)
                scaler.update()
                torch.cuda.synchronize()
                if step >= 2:
                    timings.append(time.monotonic() - step_start)
                    losses.append(float(value.detach()))
            weight = Path(torch.hub.get_dir()) / "checkpoints" / f"{name}_pretrain.pth"
            row.update(status="PASS", mean_step_seconds=float(np.mean(timings)),
                       p95_step_seconds=float(np.percentile(timings, 95)),
                       peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                       peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                       backbone_sha256=file_sha(weight), finite_losses=losses,
                       note="Single repeated synthetic pair; losses are NOT a benchmark.")
        except torch.cuda.OutOfMemoryError:
            row["status"] = "OOM"
        except Exception as exc:
            row.update(status="ERROR", error_type=type(exc).__name__, message=str(exc)[:300])
        finally:
            # The notebook exits after profiling; weights stay in temporary cache
            # and no trained checkpoint is exported or promoted.
            encoder = decoder = optimizer = None
            params = features = q = r = logits = value = None
            gc.collect()
            torch.cuda.empty_cache()
        report["measurements"].append(row)
        save()
        print(json.dumps(row), flush=True)
    report.setdefault("status", "COMPLETE" if all(r["status"] == "PASS" for r in report["measurements"]) else "REVIEW_REQUIRED")
    save()
    return report
