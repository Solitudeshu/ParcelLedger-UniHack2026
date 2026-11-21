"""Train/evaluate a real image-feature model on explicitly synthetic carton views.

Object-disjoint train/model-selection/calibration/test sets. The final test is
opened only after seed selection, temperature fitting and threshold selection.
This experiment cannot authorize real-world evidence acceptance on its own.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw
from torch.nn import functional as F

from ml.data.carton_views import ROI, VERSION, box_views, corpus_split, image_hash, pair_label
from ml.features.dino import DINO_REVISION, DinoEncoder
from ml.features.geometry import describe, scalar_features
from ml.features.pair_features import FEATURE_VERSION, ScalarNormalizer, pair_features
from ml.train.ranker import EvidenceRanker, multitask_loss


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def sigmoid(values):
    return 1 / (1 + np.exp(-np.clip(values, -50, 50)))


def temperatures(logits, targets):
    options = np.geomspace(.25, 8., 80)
    losses = []
    for temp in options:
        p = np.clip(sigmoid(logits / temp), 1e-6, 1-1e-6)
        losses.append(float(-(targets*np.log(p) + (1-targets)*np.log(1-p)).mean()))
    return float(options[np.argmin(losses)])


def metrics(prediction, rows, threshold=None, temperatures_=(1., 1.)):
    ranking = prediction["ranking"].reshape(-1, 6)
    target = np.array([row["same_region"] for row in rows]).reshape(-1, 6)
    suff = np.array([row["sufficient"] for row in rows]).reshape(-1, 6)
    best = ranking.argmax(axis=1)
    indices = np.arange(len(best))
    top_correct = target[indices, best] == 1
    safe = suff[indices, best] == 1
    region_probability = sigmoid(prediction["same_region"] / temperatures_[0]).reshape(-1, 6)
    suff_probability = sigmoid(prediction["sufficiency"] / temperatures_[1]).reshape(-1, 6)
    accepted = ((region_probability[indices, best] >= threshold) &
                (suff_probability[indices, best] >= threshold)) if threshold is not None else np.ones(len(best), bool)
    groups = np.array([row["box_id"] for row in rows[::6]])
    rng = np.random.default_rng(2626)
    # Cluster bootstrap resamples entire boxes, preserving correlated views.
    unique = np.unique(groups)
    samples = []
    if len(unique):
        grouped = {box: np.flatnonzero(groups == box) for box in unique}
        for _ in range(400):
            sample = np.concatenate([grouped[box] for box in rng.choice(unique, len(unique))])
            samples.append(float(top_correct[sample].mean()))
    result = {"queries": len(best), "boxes": len(unique), "top1_correspondence": float(top_correct.mean()),
              "top1_box_bootstrap_ci95": np.quantile(samples, [.025, .975]).tolist(),
              "accepted_queries": int(accepted.sum()), "coverage": float(accepted.mean()),
              "unsafe_accepts": int((accepted & ~safe).sum()),
              "accepted_precision": float(safe[accepted].mean()) if accepted.any() else None,
              "sufficient_query_recall": float((accepted & safe).sum()/max((suff.max(axis=1) == 1).sum(), 1))}
    result["by_condition"] = {}
    conditions = np.array([row["condition"] for row in rows[::6]])
    for condition in np.unique(conditions):
        selected = conditions == condition
        result["by_condition"][condition] = {"queries": int(selected.sum()),
            "top1": float(top_correct[selected].mean()), "accepted": int(accepted[selected].sum()),
            "unsafe_accepts": int((accepted[selected] & ~safe[selected]).sum())}
    return result


def train_candidate(features, labels, train_idx, select_idx, seed, device, epochs=24):
    torch.manual_seed(seed)
    model = EvidenceRanker().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=8e-4, weight_decay=.01)
    rng = np.random.default_rng(seed)
    best_loss, best_state, logs = float("inf"), None, []
    train_queries = train_idx.reshape(-1, 6)
    for epoch in range(epochs):
        model.train()
        losses = []
        order = rng.permutation(len(train_queries))
        for start in range(0, len(order), 16):
            idx = train_queries[order[start:start+16]].reshape(-1)
            x = torch.from_numpy(features[idx]).to(device)
            batch_labels = {key: torch.from_numpy(value[idx]).to(device) for key, value in labels.items()}
            output = model(x)
            query_ids = [f"q{i//6}" for i in range(len(idx))]
            loss = multitask_loss(output, batch_labels, query_ids)["total"]
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.)
            optimizer.step()
            losses.append(float(loss.detach()))
        model.eval()
        with torch.inference_mode():
            output = model(torch.from_numpy(features[select_idx]).to(device))
            known = torch.from_numpy(labels["same_region_target"][select_idx]).to(device)
            sufficient = torch.from_numpy(labels["sufficiency_target"][select_idx]).to(device)
            positive = known == 1
            selection_loss = float(F.binary_cross_entropy_with_logits(output["same_region"], known) +
                                   F.binary_cross_entropy_with_logits(output["sufficiency"][positive], sufficient[positive]))
        logs.append({"epoch": epoch+1, "training_loss": float(np.mean(losses)), "selection_loss": selection_loss})
        if selection_loss < best_loss:
            best_loss = selection_loss
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    model.load_state_dict(best_state)
    model.eval()
    print(json.dumps({"seed_complete": seed, "selection_loss": best_loss}), flush=True)
    return model, best_loss, logs


def run(output: Path, boxes=300, epochs=24):
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    cv2.setNumThreads(1)
    torch.set_num_threads(2)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    split = corpus_split(boxes)
    dump(output / "split.json", split)
    encoder = DinoEncoder(device=device, cache_dir=Path("/tmp/kienchung-dino"))
    rows, query_embeddings, reference_embeddings, scalars = [], [], [], []
    gallery = []
    for box in range(boxes):
        queries, references = box_views(box)
        all_views = queries + references
        embeddings = encoder.encode([view.image for view in all_views])
        descriptions = [describe(view.image, ROI if index < 4 else None) for index, view in enumerate(all_views)]
        for qi, query in enumerate(queries):
            for ri, reference in enumerate(references):
                qembedding, rembedding = embeddings[qi], embeddings[4+ri]
                scalar = scalar_features(descriptions[qi], descriptions[4+ri], float(qembedding @ rembedding))
                rows.append({"pair_id": f"{query.id}:{reference.id}", "query_id": query.id,
                    "box_id": f"carton:{box}", "split": split["lineages"][f"carton:{box}"],
                    "condition": query.condition, "query_sha256": image_hash(query.image),
                    "reference_sha256": image_hash(reference.image), **pair_label(query, reference)})
                query_embeddings.append(qembedding)
                reference_embeddings.append(rembedding)
                scalars.append(scalar)
        if box < 4:
            gallery.extend(queries + references[:2])
        if (box+1) % 25 == 0:
            print(json.dumps({"images_processed": (box+1)*10, "pairs": len(rows), "seconds": time.monotonic()-started}), flush=True)
    scalar_array = np.stack(scalars)
    splits = np.array([row["split"] for row in rows])
    train_idx = np.flatnonzero(splits == "train")
    validation_boxes = sorted({row["box_id"] for row in rows if row["split"] == "validation"})
    selection_boxes = set(validation_boxes[::2])
    selection_idx = np.array([i for i, row in enumerate(rows) if row["box_id"] in selection_boxes])
    calibration_idx = np.array([i for i, row in enumerate(rows) if row["split"] == "validation" and row["box_id"] not in selection_boxes])
    test_idx = np.flatnonzero(splits == "test")
    normalizer = ScalarNormalizer.fit(scalar_array[train_idx], ["train"]*len(train_idx))
    features = pair_features(np.stack(query_embeddings), np.stack(reference_embeddings), scalar_array, normalizer)
    labels = {"same_region_target": np.array([row["same_region"] for row in rows], np.float32),
              "same_region_mask": np.ones(len(rows), bool),
              "sufficiency_target": np.array([row["sufficient"] for row in rows], np.float32),
              "sufficiency_mask": np.array([row["same_region"] == 1 for row in rows])}
    candidates, training_logs = [], {}
    for seed in (26, 27, 28):
        model, loss, log = train_candidate(features, labels, train_idx, selection_idx, seed, device, epochs)
        candidates.append((loss, seed, model.cpu()))
        training_logs[str(seed)] = log
    _, chosen_seed, model = min(candidates, key=lambda item: item[0])
    model = model.to(device)
    with torch.inference_mode():
        calibration = {key: value.cpu().numpy() for key, value in model(torch.from_numpy(features[calibration_idx]).to(device)).items()}
    positive = labels["same_region_target"][calibration_idx] == 1
    temp = (temperatures(calibration["same_region"], labels["same_region_target"][calibration_idx]),
            temperatures(calibration["sufficiency"][positive], labels["sufficiency_target"][calibration_idx][positive]))
    calibration_rows = [rows[i] for i in calibration_idx]
    chosen_threshold, best_recall = 1., -1.
    for threshold in (.70, .80, .85, .90, .95, .975, .99):
        measured = metrics(calibration, calibration_rows, threshold, temp)
        if measured["unsafe_accepts"] == 0 and measured["sufficient_query_recall"] > best_recall:
            chosen_threshold, best_recall = threshold, measured["sufficient_query_recall"]
    # Test is evaluated only now; do not select checkpoints, thresholds or seeds here.
    with torch.inference_mode():
        test_predictions = {key: value.cpu().numpy() for key, value in model(torch.from_numpy(features[test_idx]).to(device)).items()}
    test_rows = [rows[i] for i in test_idx]
    test_metrics = metrics(test_predictions, test_rows, chosen_threshold, temp)
    baselines = {}
    for name, score in {"dino_cosine": scalar_array[test_idx, 0],
                        "sift_geometry": scalar_array[test_idx, 1]*scalar_array[test_idx, 2]*scalar_array[test_idx, 5]}.items():
        baseline = {"ranking": score, "same_region": score, "sufficiency": score}
        baseline_metrics = metrics(baseline, test_rows)
        baselines[name] = {key: baseline_metrics[key] for key in ("queries", "boxes", "top1_correspondence", "top1_box_bootstrap_ci95")}
    weights = {key: value.detach().cpu().numpy() for key, value in model.state_dict().items()}
    np.savez_compressed(output / "ranker.npz", **weights)
    np.savez_compressed(output / "test-predictions.npz", **test_predictions, indices=test_idx)
    # Probe enables independently checking the NumPy deployment implementation.
    probe_idx = selection_idx[:12]
    with torch.inference_mode():
        probe_outputs = {key: value.cpu().numpy() for key, value in model(torch.from_numpy(features[probe_idx]).to(device)).items()}
    np.savez_compressed(output / "export-parity.npz", features=features[probe_idx], **probe_outputs)
    dump(output / "normalizer.json", normalizer.state())
    dump(output / "training-log.json", training_logs)
    (output / "labels.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    sheet = Image.new("RGB", (6*192, 4*220), "white")
    draw = ImageDraw.Draw(sheet)
    for index, view in enumerate(gallery):
        x, y = index % 6 * 192, index // 6 * 220
        sheet.paste(Image.fromarray(view.image).resize((192, 192)), (x, y))
        draw.text((x+4, y+196), f"{view.id} {view.condition}", fill="black")
    sheet.save(output / "sample-gallery.jpg", quality=90)
    card = {"model_version": "synthetic-dino-ranker-v1", "experiment_kind": "SYNTHETIC_IMAGE_PRETRAINING",
        "real_image_validation": "NOT_RUN", "release_gate": "HOLD_REAL_BOX_VALIDATION",
        "dataset": VERSION, "boxes": boxes, "images": boxes*10, "pairs": len(rows),
        "split_hash": split["sha256"], "selected_seed": chosen_seed,
        "selection_boxes": sorted(selection_boxes),
        "calibration_boxes": sorted(set(validation_boxes)-selection_boxes),
        "temperatures": list(temp), "threshold": chosen_threshold,
        "feature_version": FEATURE_VERSION, "dino_revision": DINO_REVISION,
        "dino_weights_sha256": encoder.weights_sha256,
        "checkpoint_sha256": hashlib.sha256((output/"ranker.npz").read_bytes()).hexdigest(),
        "normalizer_sha256": hashlib.sha256((output/"normalizer.json").read_bytes()).hexdigest(),
        "test": test_metrics, "baselines": baselines,
        "seed_selection_losses": {str(seed): loss for loss, seed, _ in candidates},
        "runtime": {"python": platform.python_version(), "torch": torch.__version__, "numpy": np.__version__,
                    "opencv": cv2.__version__, "device": device, "seconds": time.monotonic()-started},
        "limitations": ["Planar synthetic faces only; no physical dent model or damage masks.",
                        "No real customer photos or human sufficiency annotation.",
                        "Calibration applies only to this synthetic distribution.",
                        "Not permitted to authorize settlement or replace human review."]}
    dump(output / "model-card.json", card)
    print(json.dumps(card), flush=True)
    return card


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--boxes", type=int, default=300)
    parser.add_argument("--epochs", type=int, default=24)
    args = parser.parse_args()
    run(args.output, args.boxes, args.epochs)
