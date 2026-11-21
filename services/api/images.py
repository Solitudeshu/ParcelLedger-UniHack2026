import io
import warnings

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from services.api.config import settings
from services.api.errors import DomainError


def decode_image(data: bytes, expected_mime: str | None = None) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                mime = Image.MIME.get(source.format)
                if mime not in {"image/jpeg", "image/png", "image/webp"}:
                    raise ValueError("Unsupported image")
                if expected_mime and mime != expected_mime:
                    raise ValueError("Incorrect media type")
                if source.width * source.height > settings().max_image_pixels:
                    raise ValueError("Image too large")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("Animated image")
                source.load()
                oriented = ImageOps.exif_transpose(source).convert("RGB")
                oriented.info.clear()
                return oriented
    except (
        ValueError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise DomainError(
            "INVALID_IMAGE", "Ảnh không hợp lệ. Hãy dùng ảnh JPG, PNG hoặc WebP tĩnh.", 422
        ) from exc


def image_quality(image: Image.Image) -> dict:
    resized = image.copy()
    resized.thumbnail((1024, 1024))
    gray = cv2.cvtColor(np.asarray(resized), cv2.COLOR_RGB2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    bright_fraction = float(np.mean(gray > 248))
    dark_fraction = float(np.mean(gray < 10))
    reasons = []
    if min(image.size) < 320:
        reasons.append("LOW_DETAIL")
    if sharpness < 35:
        reasons.append("BLUR")
    if bright_fraction > 0.35:
        reasons.append("GLARE")
    if dark_fraction > 0.5:
        reasons.append("LOW_LIGHT")
    return {
        "sharpness": round(sharpness, 3),
        "bright_fraction": round(bright_fraction, 4),
        "dark_fraction": round(dark_fraction, 4),
        "reason_codes": reasons,
        "method": "capture-quality-heuristic-v1",
    }


def preview_bytes(data: bytes, max_side: int = 1400) -> bytes:
    image = decode_image(data)
    image.thumbnail((max_side, max_side))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=88)
    return output.getvalue()
