from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class ContinuityResult:
    frame_a: str
    frame_b: str
    character_similarity: float
    color_similarity: float
    composition: float
    motion_jump: str
    overall: float
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _load(path: str | Path, size: tuple[int, int] = (256, 144)) -> np.ndarray:
    image = Image.open(path).convert("RGB").resize(size)
    return np.asarray(image).astype(np.float32) / 255.0


def _similarity(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a - b) ** 2))
    return max(0.0, min(100.0, 100.0 * (1.0 - math.sqrt(mse))))


def analyze_pair(path_a: str | Path, path_b: str | Path, label_a: str = "A", label_b: str = "B") -> ContinuityResult:
    try:
        a, b = _load(path_a), _load(path_b)
    except (OSError, ValueError):
        return ContinuityResult(label_a, label_b, 0, 0, 0, "Unknown", 0, ["Missing frame image"])
    color = _similarity(a.mean(axis=(0, 1)), b.mean(axis=(0, 1)))
    comp = _similarity(a[::8, ::8], b[::8, ::8])
    char = _similarity(a[48:132, 76:180], b[48:132, 76:180])
    motion_delta = 100.0 - comp
    motion = "Low" if motion_delta < 8 else "Medium" if motion_delta < 22 else "High"
    warnings: list[str] = []
    if char < 78:
        warnings.append("Face / character drift")
    if color < 80:
        warnings.append("Color change")
    if comp < 75:
        warnings.append("Composition or background drift")
    if motion == "High":
        warnings.append("Camera jump")
    overall = round(char * 0.42 + color * 0.23 + comp * 0.35, 1)
    return ContinuityResult(label_a, label_b, round(char, 1), round(color, 1), round(comp, 1), motion, overall, warnings)


def analyze_sequence(paths: list[str | Path]) -> list[ContinuityResult]:
    return [analyze_pair(a, b, str(i + 1), str(i + 2)) for i, (a, b) in enumerate(zip(paths, paths[1:]))]

