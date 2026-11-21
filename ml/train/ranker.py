"""Trainable heads for correspondence ranking and conditional evidence sufficiency.

Defining/running these heads is not evidence of quality on real parcel images.
No network downloads or implicit pretrained weights occur in this module.
"""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F

INPUT_DIM = 1560


class EvidenceRanker(nn.Module):
    def __init__(self, dropout: float = 0.1):
        super().__init__()
        self.shared = nn.Sequential(nn.Linear(INPUT_DIM, 256), nn.GELU(), nn.Dropout(dropout),
                                    nn.Linear(256, 64), nn.GELU(), nn.Dropout(dropout))
        self.ranking = nn.Linear(64, 1)
        self.same_region = nn.Linear(64, 1)
        self.sufficiency = nn.Linear(64, 1)

    def forward(self, features):
        if features.ndim != 2 or features.shape[1] != INPUT_DIM:
            raise ValueError("RANKER_INPUT_SHAPE")
        hidden = self.shared(features)
        return {"ranking": self.ranking(hidden).squeeze(-1),
                "same_region": self.same_region(hidden).squeeze(-1),
                "sufficiency": self.sufficiency(hidden).squeeze(-1)}


def masked_bce(logits, targets, mask):
    mask = mask.bool()
    # Empty supervision contributes no gradient while preserving the graph.
    return F.binary_cross_entropy_with_logits(logits[mask], targets[mask]) if mask.any() else logits.sum() * 0


def multitask_loss(outputs, labels, query_ids, ranking_weight=1.0, region_weight=1.0, sufficiency_weight=1.0):
    count = len(outputs["ranking"])
    if len(query_ids) != count or any(len(value) != count for value in labels.values()):
        raise ValueError("LOSS_ROW_MISMATCH")
    region = masked_bce(outputs["same_region"], labels["same_region_target"], labels["same_region_mask"])
    sufficient = labels["sufficiency_mask"].bool() & labels["same_region_mask"].bool() & (labels["same_region_target"] == 1)
    sufficiency = masked_bce(outputs["sufficiency"], labels["sufficiency_target"], sufficient)
    ranking_terms = []
    for query in dict.fromkeys(query_ids):
        indices = torch.tensor([i for i, name in enumerate(query_ids) if name == query], device=outputs["ranking"].device)
        known = labels["same_region_mask"][indices].bool()
        positive = indices[known & (labels["same_region_target"][indices] == 1)]
        negative = indices[known & (labels["same_region_target"][indices] == 0)]
        if len(positive) and len(negative):
            differences = outputs["ranking"][positive, None] - outputs["ranking"][negative][None, :]
            ranking_terms.append(F.softplus(-differences).mean())
    ranking = torch.stack(ranking_terms).mean() if ranking_terms else outputs["ranking"].sum() * 0
    total = ranking_weight * ranking + region_weight * region + sufficiency_weight * sufficiency
    return {"total": total, "ranking": ranking, "same_region": region, "sufficiency": sufficiency,
            "ranked_queries": len(ranking_terms), "sufficiency_rows": int(sufficient.sum())}


def pipeline_check():
    """CPU plumbing tests on fabricated tensors; not an accuracy experiment."""
    torch.manual_seed(26)
    model = EvidenceRanker(dropout=0)
    inputs = torch.randn(8, INPUT_DIM)
    labels = {
        "same_region_target": torch.tensor([1, 0, 1, 0, 1, 0, 0, 0.], dtype=torch.float32),
        "same_region_mask": torch.tensor([1, 1, 1, 1, 1, 1, 0, 0], dtype=torch.bool),
        "sufficiency_target": torch.tensor([1, 0, 0, 0, 1, 0, 0, 0.], dtype=torch.float32),
        "sufficiency_mask": torch.tensor([1, 0, 1, 0, 1, 0, 0, 0], dtype=torch.bool),
    }
    queries = ["q1", "q1", "q2", "q2", "q3", "q3", "q4", "q4"]
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    initial = float(multitask_loss(model(inputs), labels, queries)["total"].detach())
    for _ in range(12):
        optimizer.zero_grad()
        losses = multitask_loss(model(inputs), labels, queries)
        losses["total"].backward()
        assert all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)
        optimizer.step()
    final = float(multitask_loss(model(inputs), labels, queries)["total"].detach())
    assert final < initial, "OPTIMIZER_DID_NOT_REDUCE_TOY_LOSS"
    unknown = {key: value.clone() for key, value in labels.items()}
    unknown["same_region_mask"][:] = False
    unknown["sufficiency_mask"][:] = False
    assert multitask_loss(model(inputs), unknown, queries)["total"].item() == 0
    with torch.no_grad():
        first = model(inputs)
        restored = EvidenceRanker(dropout=0)
        restored.load_state_dict(model.state_dict())
        for key, value in restored(inputs).items():
            torch.testing.assert_close(value, first[key])
    return {"gate": "PASS", "test_kind": "FABRICATED_TENSOR_PIPELINE_CHECK",
            "real_data_accuracy_measured": False, "trained_release_checkpoint": False,
            "parameter_count": sum(p.numel() for p in model.parameters()),
            "torch_version": torch.__version__, "device": "cpu", "optimizer_steps": 12,
            "initial_toy_loss": initial, "final_toy_loss": final}
