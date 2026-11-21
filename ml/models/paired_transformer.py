"""Spatial paired-image candidate, not a validated damage detector.

Both images must already be in the query coordinate system. Matching/visibility
is an upstream task. Unknown pixels are excluded from change supervision.
"""
from __future__ import annotations

import math

import torch
from torch import nn
from torch.nn import functional as F

DINO_REVISION = "7764ea0f912e53c92e82eb78a2a1631e92725fc8"
BACKBONES = {
    "dinov2_vitb14": {"channels": 768, "layers": (2, 5, 8, 11)},
    "dinov2_vitl14": {"channels": 1024, "layers": (5, 11, 17, 23)},
}


class LoRALinear(nn.Module):
    """Low-rank adaptation of a frozen attention projection."""

    def __init__(self, base: nn.Linear, rank: int = 8, alpha: float = 16):
        super().__init__()
        if rank < 1:
            raise ValueError("LORA_RANK_MUST_BE_POSITIVE")
        self.base = base.requires_grad_(False)
        self.down = nn.Linear(base.in_features, rank, bias=False)
        self.up = nn.Linear(rank, base.out_features, bias=False)
        self.scale = alpha / rank
        nn.init.kaiming_uniform_(self.down.weight, a=math.sqrt(5))
        nn.init.zeros_(self.up.weight)

    def forward(self, x):
        return self.base(x) + self.up(self.down(x)) * self.scale


class DinoSpatialEncoder(nn.Module):
    def __init__(self, name="dinov2_vitl14", lora_blocks=0, cache_dir=None):
        super().__init__()
        if name not in BACKBONES or not 0 <= lora_blocks <= 4:
            raise ValueError("UNSUPPORTED_ENCODER_CONFIGURATION")
        if cache_dir:
            torch.hub.set_dir(str(cache_dir))
        self.backbone = torch.hub.load(
            f"facebookresearch/dinov2:{DINO_REVISION}", name,
            pretrained=True, trust_repo=True,
        ).requires_grad_(False)
        self.name = name
        self.channels = BACKBONES[name]["channels"]
        self.layers = BACKBONES[name]["layers"]
        self.adapted = lora_blocks > 0
        if self.adapted:
            for block in self.backbone.blocks[-lora_blocks:]:
                block.attn.qkv = LoRALinear(block.attn.qkv)
        self.register_buffer("mean", torch.tensor([.485, .456, .406])[None, :, None, None])
        self.register_buffer("std", torch.tensor([.229, .224, .225])[None, :, None, None])

    def forward(self, images):
        if images.shape[-1] % 14 or images.shape[-2] % 14:
            raise ValueError("ENCODER_SIZE_MUST_BE_MULTIPLE_OF_14")
        # No stochastic depth in the frozen foundation model. LoRA still gets
        # gradients in training; no inference-mode tensors enter the decoder.
        self.backbone.eval()
        gradients = torch.is_grad_enabled() and self.training and self.adapted
        with torch.set_grad_enabled(gradients):
            return list(self.backbone.get_intermediate_layers(
                (images - self.mean) / self.std, n=self.layers, reshape=True,
            ))


class CrossViewBlock(nn.Module):
    """Query patches attend to the aligned reference's spatial features."""

    def __init__(self, width=128, heads=4):
        super().__init__()
        self.q_norm = nn.LayerNorm(width)
        self.r_norm = nn.LayerNorm(width)
        self.attention = nn.MultiheadAttention(width, heads, batch_first=True)
        self.position = nn.Conv2d(2, width, 1)
        self.fuse = nn.Sequential(
            nn.Conv2d(width * 3, width, 1), nn.GroupNorm(8, width), nn.GELU(),
            nn.Conv2d(width, width, 3, padding=1), nn.GroupNorm(8, width), nn.GELU(),
        )

    def forward(self, query, reference, valid):
        b, _, h, w = query.shape
        yy, xx = torch.meshgrid(
            torch.linspace(-1, 1, h, device=query.device, dtype=query.dtype),
            torch.linspace(-1, 1, w, device=query.device, dtype=query.dtype), indexing="ij",
        )
        position = self.position(torch.stack([xx, yy])[None]).flatten(2).transpose(1, 2)
        q = self.q_norm(query.flatten(2).transpose(1, 2))
        r = self.r_norm(reference.flatten(2).transpose(1, 2))
        keep = F.interpolate(valid.float(), (h, w), mode="area").flatten(1) >= .5
        empty = ~keep.any(dim=1)
        # PyTorch attention is undefined if every key is masked. A dummy key is
        # enabled only for the numerical operation; its output is then zeroed.
        safe_keep = keep.clone()
        safe_keep[empty, 0] = True
        context, _ = self.attention(
            q + position, r + position, r,
            key_padding_mask=~safe_keep, need_weights=False,
        )
        context = context.masked_fill(empty[:, None, None], 0)
        context = context.transpose(1, 2).reshape(b, -1, h, w)
        return self.fuse(torch.cat([query, query - reference, query - context], 1))


class PairedChangeTransformer(nn.Module):
    def __init__(self, encoder_channels=1024, width=128, levels=4):
        super().__init__()
        if width % 8 or levels < 1:
            raise ValueError("INVALID_DECODER_CONFIGURATION")
        self.levels = levels
        self.projections = nn.ModuleList(nn.Conv2d(encoder_channels, width, 1) for _ in range(levels))
        self.cross_view = nn.ModuleList(CrossViewBlock(width) for _ in range(levels))
        self.semantic = nn.Sequential(nn.Conv2d(levels * width, width, 1), nn.GELU())
        # RGB detail bypass preserves narrow scratches that patch tokens lose.
        self.detail = nn.Sequential(
            nn.Conv2d(10, 32, 5, stride=2, padding=2), nn.GroupNorm(8, 32), nn.GELU(),
            nn.Conv2d(32, 32, 3, stride=2, padding=1), nn.GroupNorm(8, 32), nn.GELU(),
        )
        self.decode = nn.Sequential(
            nn.Conv2d(width + 32, 64, 3, padding=1), nn.GroupNorm(8, 64), nn.GELU(),
            nn.Conv2d(64, 32, 3, padding=1), nn.GELU(), nn.Conv2d(32, 1, 1),
        )

    def forward(self, query_features, reference_features, query, reference, valid):
        if len(query_features) != self.levels or len(reference_features) != self.levels:
            raise ValueError("FEATURE_LEVEL_COUNT_MISMATCH")
        shape = query_features[0].shape[-2:]
        fused = []
        for q, r, projection, block in zip(
            query_features, reference_features, self.projections, self.cross_view, strict=True,
        ):
            item = block(projection(q), projection(r), valid)
            fused.append(F.interpolate(item, shape, mode="bilinear", align_corners=False))
        semantic = self.semantic(torch.cat(fused, 1))
        detail = self.detail(torch.cat([query, reference, (query - reference).abs(), valid], 1))
        semantic = F.interpolate(semantic, detail.shape[-2:], mode="bilinear", align_corners=False)
        logits = self.decode(torch.cat([semantic, detail], 1))
        return F.interpolate(logits, query.shape[-2:], mode="bilinear", align_corners=False)


def masked_change_loss(logits, target, valid):
    """Unknown pixels contribute neither gradients nor positive/negative labels."""
    # Half-precision sums overflow above 65,504 pixels at the target resolutions.
    logits, target, valid = logits.float(), target.float(), valid.float()
    bce = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
    bce = (bce * valid).sum() / valid.sum().clamp_min(1)
    p, t = logits.sigmoid() * valid, target * valid
    dims = (1, 2, 3)
    dice = 1 - (2 * (p * t).sum(dims) + 1) / (p.sum(dims) + t.sum(dims) + 1)
    observed = valid.sum(dims) > 0
    return bce + (dice * observed).sum() / observed.sum().clamp_min(1)
