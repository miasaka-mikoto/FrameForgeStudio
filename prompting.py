from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class PromptParts:
    character: str = ""
    scene: str = ""
    shot: str = ""
    motion: str = ""
    camera: str = ""
    lighting: str = ""
    style: str = ""
    negative: str = ""
    references: list[str] = field(default_factory=list)

    def compose(self) -> str:
        parts = [p.strip() for p in (self.character, self.scene, self.shot, self.motion, self.camera, self.lighting, self.style) if p and p.strip()]
        if self.references:
            parts.append("reference images: " + ", ".join(self.references))
        return ", ".join(parts)

    def compose_negative(self) -> str:
        return ", ".join(dict.fromkeys(p.strip() for p in self.negative.split(",") if p.strip()))


def final_generation_prompt(parts: PromptParts) -> tuple[str, str]:
    return parts.compose(), parts.compose_negative()


def frame_prompt(start_prompt: str, end_prompt: str, action: str, frame_no: int, total: int, previous: str = "") -> str:
    blend = ""
    if total > 1:
        blend = f"transition {frame_no}/{total} between start and end keyframes"
    values = [start_prompt, end_prompt, action, blend, f"animation frame {frame_no:04d}"]
    if previous:
        values.append(f"continue previous frame: {previous}")
    return ", ".join(v.strip() for v in values if v and v.strip())


def prompt_from_rows(rows: Iterable[dict], key: str = "prompt") -> str:
    return ", ".join(str(row.get(key, "")).strip() for row in rows if row.get(key))

