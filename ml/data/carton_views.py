"""Procedural carton-face pretraining with ground-truth planar correspondence.

No Parcel3D pixels, external textures, brand assets or user photos are used.
This is synthetic pretraining, NOT a physical damage or real-box benchmark.
Faces from a box share print style; distinct faces and nuisance views are hard
negatives. Labels derive from generation geometry, never SIFT/model predictions.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import cv2
import numpy as np

from ml.data.labels import locked_split

VERSION = "carton-planar-pretrain-v1"
SIZE = 384
ROI = [[2500, 2500], [7500, 2500], [7500, 7500], [2500, 7500]]


@dataclass
class View:
    id: str
    face: int
    image: np.ndarray
    transform: np.ndarray
    visible: np.ndarray
    condition: str


def face_texture(box: int, face: int, size: int = SIZE) -> np.ndarray:
    rng = np.random.default_rng(np.random.SeedSequence([26, box, face]))
    base = np.array([185, 150, 104]) + rng.integers(-25, 26, 3)
    image = np.clip(base + rng.normal(0, 3, (size, size, 1)), 0, 255).astype(np.uint8)
    ink = tuple(int(x) for x in np.array([45, 38, 30]) + (box % 7) * 3)
    # Common carton seam, variable tape and repeated generic packing marks.
    seam = int(rng.integers(140, 240))
    cv2.rectangle(image, (seam, 0), (seam + 26, size), (166, 133, 90), -1)
    for _ in range(30):
        x, y = rng.integers(8, size - 30, 2)
        w, h = rng.integers(4, 45, 2)
        cv2.rectangle(image, (int(x), int(y)), (int(x+w), int(y+h)), ink, int(rng.integers(1, 3)))
    # Neutral synthetic label; no stable face/class identifier is printed.
    x, y = (int(v) for v in rng.integers(20, 145, 2))
    cv2.rectangle(image, (x, y), (x+160, y+90), (230, 225, 211), -1)
    for index in range(45):
        thickness = int(rng.integers(1, 4))
        cv2.line(image, (x+8+index*3, y+12), (x+8+index*3, y+52), ink, thickness)
    code = "".join(str(v) for v in rng.integers(0, 10, 10))
    cv2.putText(image, code, (x+8, y+76), cv2.FONT_HERSHEY_SIMPLEX, 0.45, ink, 1, cv2.LINE_AA)
    return image


def render_view(texture: np.ndarray, rng, identifier: str, face: int, condition: str) -> View:
    source = np.float32([[0, 0], [SIZE-1, 0], [SIZE-1, SIZE-1], [0, SIZE-1]])
    destination = source + rng.uniform(-38, 38, (4, 2)).astype(np.float32)
    if condition == "partial":
        destination[:, 0] -= 165
    transform = cv2.getPerspectiveTransform(source, destination)
    image = cv2.warpPerspective(texture, transform, (SIZE, SIZE), borderValue=(73, 79, 82))
    visible = cv2.warpPerspective(np.ones((SIZE, SIZE), np.uint8), transform, (SIZE, SIZE), flags=cv2.INTER_NEAREST)
    gain = rng.uniform(0.65, 1.3)
    bias = rng.uniform(-16, 16)
    image = np.clip(image.astype(np.float32) * gain + bias, 0, 255).astype(np.uint8)
    if condition == "blur":
        image = cv2.GaussianBlur(image, (27, 27), 7)
    elif condition == "occlusion":
        cv2.rectangle(image, (95, 120), (290, 275), (75, 83, 90), -1)
        visible[120:276, 95:291] = 0
    elif condition == "glare":
        cv2.ellipse(image, (200, 200), (100, 60), 20, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(visible, (200, 200), (100, 60), 20, 0, 360, 0, -1)
    return View(identifier, face, image, transform, visible, condition)


def box_views(box: int):
    rng = np.random.default_rng(np.random.SeedSequence([2626, box]))
    textures = [face_texture(box, face) for face in range(3)]
    references = [render_view(textures[face], rng, f"b{box}:r{face}:{view}", face,
                              "partial" if view == 1 else "clean")
                  for face in range(3) for view in range(2)]
    # Rotate face/condition independently of the locked split.
    conditions = ["clean", "blur", "occlusion", "glare"]
    queries = [render_view(textures[(box+i) % 3], rng, f"b{box}:q{i}", (box+i) % 3, condition)
               for i, condition in enumerate(conditions)]
    return queries, references


def pair_label(query: View, reference: View):
    same = query.face == reference.face
    overlap = 0.0
    if same:
        q_to_r = reference.transform @ np.linalg.inv(query.transform)
        available = cv2.warpPerspective(reference.visible, np.linalg.inv(q_to_r), (SIZE, SIZE), flags=cv2.INTER_NEAREST)
        roi = np.zeros((SIZE, SIZE), np.uint8)
        cv2.fillPoly(roi, [np.rint(np.array(ROI)/10000*(SIZE-1)).astype(np.int32)], 1)
        overlap = float(np.sum(available * query.visible * roi) / roi.sum())
    sufficient = same and overlap >= 0.90 and query.condition != "blur"
    return {"same_region": float(same), "sufficient": float(sufficient),
            "ground_truth_overlap": overlap, "label_method": "KNOWN_GENERATOR_GEOMETRY"}


def corpus_split(boxes: int):
    return locked_split([f"carton:{box}" for box in range(boxes)], seed=VERSION)


def image_hash(image):
    return hashlib.sha256(image.tobytes()).hexdigest()
