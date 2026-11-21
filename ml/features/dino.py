"""Frozen DINOv2 ViT-S/14. Original DINOv2, not the noncommercial X-ray variant."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

DINO_REVISION = "7764ea0f912e53c92e82eb78a2a1631e92725fc8"
WEIGHTS_URL = "https://dl.fbaipublicfiles.com/dinov2/dinov2_vits14/dinov2_vits14_pretrain.pth"


class DinoEncoder:
    def __init__(self, device="cpu", cache_dir=None):
        import torch
        self.torch, self.device = torch, device
        if cache_dir is not None:
            torch.hub.set_dir(str(cache_dir))
        self.model = torch.hub.load(f"facebookresearch/dinov2:{DINO_REVISION}",
                                   "dinov2_vits14", pretrained=True, trust_repo=True).to(device).eval()
        weights = Path(torch.hub.get_dir()) / "checkpoints/dinov2_vits14_pretrain.pth"
        self.weights_sha256 = hashlib.sha256(weights.read_bytes()).hexdigest()

    def encode(self, images, batch_size=32):
        import cv2
        torch = self.torch
        outputs = []
        with torch.inference_mode():
            for start in range(0, len(images), batch_size):
                arrays = [cv2.resize(np.asarray(image), (224, 224)).astype(np.float32)/255
                          for image in images[start:start+batch_size]]
                batch = torch.from_numpy(np.stack(arrays).transpose(0, 3, 1, 2)).to(self.device)
                mean = torch.tensor([.485, .456, .406], device=self.device)[None, :, None, None]
                std = torch.tensor([.229, .224, .225], device=self.device)[None, :, None, None]
                embeddings = self.model((batch-mean)/std)
                embeddings = torch.nn.functional.normalize(embeddings, dim=1)
                outputs.append(embeddings.cpu().numpy())
        return np.concatenate(outputs).astype(np.float32)
