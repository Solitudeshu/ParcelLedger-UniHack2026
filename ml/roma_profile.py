"""Measure unmodified RoMa v2 execution on Kaggle. No accuracy or model selection."""
from __future__ import annotations

import gc
import hashlib
import inspect
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run():
    from romav2 import RoMaV2

    output = Path("/kaggle/working/roma-profile")
    output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    torch.set_float32_matmul_precision("highest")
    torch.manual_seed(26)
    if not torch.cuda.is_available():
        raise ValueError("ROMA_PROFILE_REQUIRES_GPU")
    cache = Path("/kaggle/temp/kc-roma-cache")
    torch.hub.set_dir(str(cache))
    report = {"scope": "PROCEDURAL_FEASIBILITY_ONLY_NOT_PARCEL_ACCURACY", "promotion": "HOLD",
              "revision": "95c9968145c8906b7b59383258e9f73b02853d89", "gpu": torch.cuda.get_device_name(),
              "torch": torch.__version__, "bf16_native": torch.cuda.is_bf16_supported(including_emulation=False),
              "bf16_including_emulation": torch.cuda.is_bf16_supported(), "source_modified": False,
              "model_source_sha256": file_hash(inspect.getfile(RoMaV2)), "measurements": [],
              "license": "RoMa code MIT; DINOv3 custom license remains applicable"}
    started = time.perf_counter()
    model = RoMaV2(RoMaV2.Cfg(setting="fast"))
    report["load_seconds"] = time.perf_counter() - started
    weights = cache / "checkpoints/romav2.0.1.pt"
    report["weights_sha256"] = file_hash(weights)
    report["weights_bytes"] = weights.stat().st_size
    if report["weights_bytes"] != 1_095_883_548:
        raise ValueError("ROMA_RELEASE_SIZE_CHANGED")
    rng = np.random.default_rng(26)
    image = np.full((518, 518, 3), 150, np.uint8)
    for _ in range(100):
        xy = rng.integers(0, 470, (2,))
        color = tuple(int(x) for x in rng.integers(0, 256, 3))
        cv2.rectangle(image, tuple(xy), tuple(xy + rng.integers(5, 45, 2)), color, -1)
    cv2.putText(image, "KIEN CHUNG 26", (15, 250), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (20, 20, 20), 3)
    truth = np.float64([[.88, .02, 25], [-.01, .9, 20], [.00005, -.00008, 1.]])
    query = cv2.warpPerspective(image, truth, (518, 518))
    for setting in ("fast", "precise"):
        model.apply_setting(setting)
        times = []
        result = {"setting": setting, "lr_size": [model.H_lr, model.W_lr],
                  "hr_size": [model.H_hr, model.W_hr], "bidirectional": model.bidirectional}
        try:
            for attempt in range(3):
                torch.cuda.reset_peak_memory_stats()
                torch.cuda.synchronize()
                started = time.perf_counter()
                preds = model.match(image, query)
                for value in preds.values():
                    if value is not None and not torch.isfinite(value).all():
                        raise ValueError("NONFINITE_DENSE_MATCH_OUTPUT")
                torch.manual_seed(26)
                matches, overlaps, _, _ = model.sample(preds, 1600)
                a, b = model.to_pixel_coordinates(matches, 518, 518, 518, 518)
                torch.cuda.synchronize()
                seconds = time.perf_counter() - started
                if attempt:
                    times.append(seconds)
                else:
                    result["first_call_seconds"] = seconds
                a, b = a.cpu().numpy(), b.cpu().numpy()
                projected = cv2.perspectiveTransform(a[:, None].astype(np.float32), truth)[:, 0]
                result.update(sampled_matches=len(a),
                              synthetic_median_transfer_error_px=float(np.median(np.linalg.norm(projected - b, axis=1))),
                              peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                              peak_reserved_bytes=torch.cuda.max_memory_reserved())
                del preds, matches, overlaps
            result.update(status="PASS", repeated_pair_seconds=times)
        except (RuntimeError, ValueError) as exc:
            result.update(status="FAILED", error_type=type(exc).__name__, error_message=str(exc)[:1000])
            gc.collect()
            torch.cuda.empty_cache()
        report["measurements"].append(result)
        report["status"] = "PROFILE_COMPLETE" if len(report["measurements"]) == 2 else "PROFILE_RUNNING"
        (output / "profile.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)
    return report
