"""Optional synthetic change-head evaluation isolated from primary observations."""
from functools import lru_cache

from ml.inference.change import bundle_identity
from services.api.config import settings


def identity():
    if not settings().shadow_change_path:
        return None
    if settings().app_env != "local":
        raise ValueError("SHADOW_MODEL_LOCAL_ONLY")
    return bundle_identity(settings().shadow_change_path)


@lru_cache(maxsize=2)
def predictor(folder, checkpoint_sha256, card_sha256):
    from ml.inference.change import ChangePredictor
    model = ChangePredictor(folder)
    if model.identity != {"checkpoint_sha256": checkpoint_sha256, "model_card_sha256": card_sha256}:
        raise ValueError("MODEL_CHECKSUM_MISMATCH")
    return model


def evaluate(query, reference, match, expected):
    if identity() != expected:
        raise ValueError("MODEL_CHECKSUM_MISMATCH")
    return predictor(settings().shadow_change_path, expected["checkpoint_sha256"], expected["model_card_sha256"]).compare(query, reference, match)
