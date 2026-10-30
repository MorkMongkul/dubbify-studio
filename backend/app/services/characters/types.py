"""What recognition knows about one line. Light on purpose (numpy only), so code that compares prints doesn't load the
models' libraries."""
from dataclasses import dataclass

import numpy as np


@dataclass
class LinePrints:
    voice: np.ndarray | None  # ERes2NetV2's + CAM++'s, float32 (384,), unit length; None under 0.3 s or without vocals
    face: np.ndarray | None  # 0.5.0/0.5.1: the speaking face's SFace print, float32 (128,), L2-normalised; else None
    speaking_score: float | None  # that face's mean lip-sync score (> 0)
    face_crop: bytes | None  # a JPEG of that face, long side <= 160 px
