"""Optional local-only checkpoint evaluation, isolated from product decisions."""
from functools import lru_cache
from pathlib import Path

from services.api.config import settings
from services.api.canonical import bytes_hash, document_hash


def bundle_identity():
    if not settings().shadow_ranker_path:
        return None
    folder = Path(settings().shadow_ranker_path)
    checkpoint = checkpoint_identity()
    files = {name: bytes_hash((folder/name).read_bytes()) for name in ("model-card.json", "normalizer.json", "ranker.npz")}
    pipeline = {path: bytes_hash(Path(path).read_bytes()) for path in ("ml/features/dino.py", "ml/features/geometry.py", "ml/features/pair_features.py", "ml/inference/ranker.py")}
    return {"checkpoint_sha256": checkpoint, "bundle_sha256": document_hash({"files": files, "pipeline": pipeline})}


def checkpoint_identity():
    path = settings().shadow_ranker_path
    if not path:
        return None
    if settings().app_env != "local":
        raise ValueError("SHADOW_MODEL_LOCAL_ONLY")
    from ml.inference.ranker import NumpyRanker
    model = NumpyRanker(path)
    return model.card["checkpoint_sha256"]


@lru_cache(maxsize=2)
def advisor(folder, bundle_sha256, checkpoint_sha256):
    from ml.features.dino import DinoEncoder
    from ml.inference.ranker import ShadowAdvisor
    result = ShadowAdvisor(folder, DinoEncoder(cache_dir=Path(settings().dino_cache_path)))
    if result.ranker.card["checkpoint_sha256"] != checkpoint_sha256:
        raise ValueError("MODEL_CHECKSUM_MISMATCH")
    return result


def evaluate(query, references, region, expected_checkpoint, expected_bundle=None):
    if checkpoint_identity() != expected_checkpoint:
        raise ValueError("MODEL_CHECKSUM_MISMATCH")
    identity = bundle_identity()
    if expected_bundle and identity != expected_bundle:
        raise ValueError("MODEL_BUNDLE_MISMATCH")
    result = advisor(settings().shadow_ranker_path, identity["bundle_sha256"], expected_checkpoint).compare(query, references, region)
    return {**result, **identity}
