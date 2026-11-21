"""Resumable appearance pretraining, selection-only evaluation, no test tuning."""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Subset

from ml.data.parcel3d_audit import find_annotations
from ml.data.parcel_pairs_v2 import PAIR_VERSION, ParcelPairs
from ml.models.paired_transformer import (
    DINO_REVISION,
    DinoSpatialEncoder,
    PairedChangeTransformer,
    masked_change_loss,
)


def trainable_state(encoder, decoder):
    return {"encoder": {name: p.detach().cpu().clone() for name, p in encoder.named_parameters() if p.requires_grad},
            "decoder": {name: value.detach().cpu().clone() for name, value in decoder.state_dict().items()}}


def load_trainable(state, encoder, decoder):
    expected = {name: p for name, p in encoder.named_parameters() if p.requires_grad}
    if set(expected) != set(state["encoder"]):
        raise ValueError("ADAPTER_PARAMETER_SET_MISMATCH")
    with torch.no_grad():
        for name, value in state["encoder"].items():
            if expected[name].shape != value.shape:
                raise ValueError("ADAPTER_PARAMETER_SHAPE_MISMATCH")
            expected[name].copy_(value)
    decoder.load_state_dict(state["decoder"], strict=True)


def atomic_save(value, path):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(value, temporary)
    temporary.replace(path)


def forward_pair(encoder, decoder, batch, device):
    q, r, y, valid = [batch[key].to(device) for key in ("query", "reference", "target", "valid")]
    with torch.autocast(device.type, dtype=torch.float16, enabled=device.type == "cuda"):
        n = len(q)
        features = encoder(torch.cat([q, r]))
        logits = decoder([x[:n] for x in features], [x[n:] for x in features], q, r, valid)
        value = masked_change_loss(logits, y, valid)
    return value


def run(input_root, manifest, output, config, profile, resume=None, fixture=False, clock=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    now = clock or time.monotonic
    started = now()
    device = torch.device("cuda" if torch.cuda.is_available() and not fixture else "cpu")
    if not fixture:
        if device.type != "cuda":
            raise ValueError("TRAINING_REQUIRES_GPU")
        matching = [r for r in profile.get("measurements", []) if r.get("status") == "PASS"
                    and r["encoder"] == config["encoder"] and r["size"] == config["size"]
                    and r["lora_blocks"] == config["lora_blocks"]]
        if not matching:
            raise ValueError("EXACT_CONFIGURATION_PROFILE_REQUIRED")
        backbone_sha = matching[0]["backbone_sha256"]
    else:
        backbone_sha = "SOFTWARE_FIXTURE_NO_PRETRAINED_WEIGHTS"
    torch.manual_seed(config["seed"])
    torch.set_num_threads(2)
    root = find_annotations(Path(input_root))["train"].parent
    train = ParcelPairs(root, manifest, "train", config["size"], fixture=fixture)
    selection = ParcelPairs(root, manifest, "selection", config["size"], fixture=fixture)
    encoder = DinoSpatialEncoder(config["encoder"], config["lora_blocks"], "/kaggle/temp/kc-model-cache").to(device)
    if not fixture:
        weights = Path(torch.hub.get_dir()) / "checkpoints" / f"{config['encoder']}_pretrain.pth"
        digest = hashlib.sha256()
        with weights.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != backbone_sha:
            raise ValueError("BACKBONE_PROFILE_HASH_MISMATCH")
    decoder = PairedChangeTransformer(encoder.channels, width=config["width"]).to(device)
    adapters = [p for p in encoder.parameters() if p.requires_grad]
    parameters = list(decoder.parameters()) + adapters
    groups = [{"params": list(decoder.parameters()), "lr": config["head_lr"]}]
    if adapters:
        groups.append({"params": adapters, "lr": config["adapter_lr"]})
    optimizer = torch.optim.AdamW(groups, weight_decay=.01)
    total_updates = math.ceil(len(train) / config["accumulation_steps"]) * config["epochs"]
    warmup = min(200, max(1, total_updates // 10))
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lambda step: min(1., (step + 1) / warmup) *
        (.1 + .45 * (1 + math.cos(math.pi * min(step, total_updates) / total_updates))),
    )
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    source_root = Path(__file__).resolve().parents[2]
    source_hashes = {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in
                     ("ml/models/paired_transformer.py", "ml/data/parcel_pairs_v2.py", "ml/train/paired_transformer.py")}
    identity = {"config": {k: v for k, v in config.items() if k != "max_seconds"}, "source_hashes": source_hashes,
                "manifest_sha256": train.manifest_sha256, "pair_version": PAIR_VERSION,
                "backbone_revision": DINO_REVISION, "backbone_sha256": backbone_sha,
                "source_kind": "SOFTWARE_FIXTURE" if fixture else "SYNTHETIC_CHANGES_ON_PARCEL3D_RENDERS"}
    epoch, offset, updates, attempts, skipped, best = 0, 0, 0, 0, 0, None
    history = []
    if resume:
        state = torch.load(resume, map_location="cpu", weights_only=True)
        if state["identity"] != identity:
            raise ValueError("RESUME_CONFIGURATION_OR_DATA_CHANGED")
        load_trainable(state["last"], encoder, decoder)
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        scaler.load_state_dict(state["scaler"])
        epoch, offset, updates, best, history = [state[k] for k in ("epoch", "offset", "updates", "best", "history")]
        attempts, skipped = state["attempts"], state["skipped"]
        torch.set_rng_state(state["rng_cpu"])
        if device.type == "cuda":
            torch.cuda.set_rng_state_all(state["rng_cuda"])

    def checkpoint(status):
        atomic_save({"identity": identity, "last": trainable_state(encoder, decoder),
                     "optimizer": optimizer.state_dict(), "scheduler": scheduler.state_dict(), "scaler": scaler.state_dict(),
                     "epoch": epoch, "offset": offset, "updates": updates, "best": best,
                     "attempts": attempts, "skipped": skipped,
                     "history": history, "rng_cpu": torch.get_rng_state(),
                     "rng_cuda": torch.cuda.get_rng_state_all() if device.type == "cuda" else []}, output / "resume.pt")
        card = {"status": status, **identity, "epochs_completed": epoch, "next_sample_offset": offset,
                "optimizer_updates": updates, "train_source_images": len(train),
                "optimizer_attempts": attempts, "skipped_amp_steps": skipped,
                "selection_source_images": len(selection), "selection_loss": None if best is None else best["loss"],
                "seconds_this_session": now() - started, "test_evaluation": "NOT_RUN",
                "real_box_validation": "NOT_RUN", "promotion": "HOLD",
                "limitations": ["Paired labels are synthetic appearance changes, not 3D dent truth.",
                                "Inputs use known alignment and foreground; upstream matching is not evaluated.",
                                "No evidence of outperforming the legacy model on real parcels."]}
        (output / "model-card.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
        (output / "training-log.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
        print(json.dumps(card), flush=True)
        return card

    accumulation = config["accumulation_steps"]
    for current_epoch in range(epoch, config["epochs"]):
        epoch = current_epoch
        train.epoch = epoch
        encoder.train()
        decoder.train()
        order = np.random.default_rng([config["seed"], epoch]).permutation(len(train)).tolist()
        loader = DataLoader(Subset(train, order[offset:]), batch_size=1, shuffle=False, num_workers=0)
        iterator = iter(loader)
        while offset < len(train):
            # Check only at optimizer boundaries so no uncommitted gradients or
            # consumed samples disappear when a session resumes.
            if now() - started >= config["max_seconds"]:
                return checkpoint("PAUSED_SESSION_BUDGET")
            count = min(accumulation, len(train) - offset)
            optimizer.zero_grad(set_to_none=True)
            total = 0.
            for _ in range(count):
                value = forward_pair(encoder, decoder, next(iterator), device)
                if not torch.isfinite(value):
                    checkpoint("FAILED_NONFINITE_LOSS")
                    raise ValueError("NONFINITE_TRAINING_LOSS")
                scaler.scale(value / count).backward()
                total += float(value.detach())
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(parameters, 1.)
            scale_before = scaler.get_scale()
            scaler.step(optimizer)
            scaler.update()
            offset += count
            attempts += 1
            if scaler.get_scale() < scale_before:
                skipped += 1
            else:
                updates += 1
                scheduler.step()
            if attempts % 100 == 0:
                history.append({"epoch": epoch, "offset": offset, "update": updates, "last_batch_loss": total / count})
                checkpoint("RUNNING")
        # Selection is deterministic and never touches calibration/test images.
        encoder.eval()
        decoder.eval()
        losses = []
        with torch.no_grad():
            for batch in DataLoader(selection, batch_size=1, num_workers=0):
                value = forward_pair(encoder, decoder, batch, device)
                if not torch.isfinite(value):
                    raise ValueError("NONFINITE_SELECTION_LOSS")
                losses.append(float(value))
        score = float(np.mean(losses))
        history.append({"epoch": epoch + 1, "selection_loss": score, "update": updates})
        if best is None or score < best["loss"]:
            best = {"loss": score, "epoch": epoch + 1, "weights": trainable_state(encoder, decoder)}
            atomic_save({"identity": identity, **best}, output / "best.pt")
        epoch, offset = epoch + 1, 0
        checkpoint("EPOCH_COMPLETE")
    if best is not None:
        # Also export the best checkpoint carried from a previous session.
        atomic_save({"identity": identity, **best}, output / "best.pt")
    return checkpoint("PRETRAINING_COMPLETE_REQUIRES_CALIBRATION_AND_REAL_VALIDATION")
