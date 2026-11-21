import hashlib
import math
import unicodedata
from typing import Any

import rfc8785


def normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Document keys must be strings")
            normalized_key = normalize(key)
            if normalized_key in output:
                raise ValueError("Duplicate key after Unicode normalization")
            output[normalized_key] = normalize(item)
        return output
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite numbers cannot be committed")
    if isinstance(value, int) and abs(value) > 2**53 - 1:
        raise ValueError("Use a decimal string for integers outside the safe JSON range")
    return value


def canonical_bytes(document: dict) -> bytes:
    return rfc8785.dumps(normalize(document))


def document_hash(document: dict) -> str:
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


def bytes_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
