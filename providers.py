from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from PIL import Image, ImageDraw, ImageFont, ImageFilter

log = logging.getLogger(__name__)


@dataclass
class GenerationRequest:
    output_path: Path
    width: int
    height: int
    prompt: str
    negative_prompt: str = ""
    seed: int | None = None
    references: list[Path] = field(default_factory=list)
    frame_no: int = 0


@dataclass
class GenerationResult:
    output_path: Path
    seed: int
    provider: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ImageProvider(Protocol):
    name: str

    def generate(self, request: GenerationRequest) -> GenerationResult: ...

    def edit(self, request: GenerationRequest) -> GenerationResult: ...

    def variation(self, request: GenerationRequest) -> GenerationResult: ...

    def reference_generate(self, request: GenerationRequest) -> GenerationResult: ...


class InterpolationProvider(Protocol):
    """Optional 12→24 provider interface (RIFE/FILM can implement later)."""

    name: str

    def interpolate(self, previous: Path, current: Path, output: Path, factor: int = 2) -> Path: ...


class DuplicateInterpolationProvider:
    name = "Frame Duplicate (baseline)"

    def interpolate(self, previous: Path, current: Path, output: Path, factor: int = 2) -> Path:
        """Baseline provider: copy the current frame, preserving deterministic timing."""
        import shutil
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, output)
        return output


class MockImageProvider:
    """Deterministic local provider used for development and automated tests."""

    name = "Mock Image Provider"

    def __init__(self, style: str = "neon storyboard"):
        self.style = style

    @staticmethod
    def _seed(request: GenerationRequest) -> int:
        if request.seed is not None:
            return int(request.seed)
        digest = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        seed = self._seed(request)
        rnd = random.Random(seed)
        w, h = max(64, int(request.width)), max(64, int(request.height))
        image = Image.new("RGB", (w, h), (12, 18, 31))
        draw = ImageDraw.Draw(image)
        # Smooth bands create a useful visual target for continuity tests without
        # pretending this is a real image-generation API.
        for y in range(h):
            t = y / max(1, h - 1)
            color = (int(10 + 22 * t), int(23 + 50 * t), int(48 + 80 * t))
            draw.line((0, y, w, y), fill=color)
        for _ in range(14):
            x = rnd.randint(0, w)
            y = rnd.randint(0, h)
            r = rnd.randint(max(5, w // 100), max(10, w // 30))
            color = (rnd.randint(25, 90), rnd.randint(90, 190), rnd.randint(120, 240))
            draw.ellipse((x - r, y - r, x + r, y + r), fill=color, outline=(100, 210, 255))
        # A simple silhouette-like subject marker; it is intentionally abstract.
        cx = int(w * (0.5 + 0.12 * (rnd.random() - 0.5)))
        cy = int(h * 0.54)
        head = max(18, int(min(w, h) * 0.11))
        draw.ellipse((cx - head, cy - head * 2, cx + head, cy), fill=(218, 166, 150), outline=(255, 226, 210), width=2)
        draw.polygon([(cx - head * 1.6, cy), (cx + head * 1.6, cy), (cx + head * 2.3, h), (cx - head * 2.3, h)], fill=(50, 74, 125), outline=(112, 178, 244))
        draw.line((cx - head * 2.0, cy + head * 0.6, cx - head * 3.0, cy + head * 2.3), fill=(180, 220, 255), width=max(2, head // 8))
        draw.line((cx + head * 2.0, cy + head * 0.6, cx + head * 3.0, cy + head * 2.3), fill=(180, 220, 255), width=max(2, head // 8))
        # Keep the prompt visible in generated samples, making the queue traceable.
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", max(12, min(w, h) // 42))
        except OSError:
            font = ImageFont.load_default()
        label = f"MOCK • FRAME {request.frame_no:04d} • seed {seed}\n{request.prompt[:100]}"
        draw.rounded_rectangle((18, 18, min(w - 18, 18 + max(240, w * 0.72)), 18 + max(48, h // 12)), radius=10, fill=(3, 8, 18, 210), outline=(92, 178, 255), width=2)
        draw.multiline_text((30, 28), label, fill=(205, 235, 255), font=font, spacing=4)
        image = image.filter(ImageFilter.GaussianBlur(radius=0.15))
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(request.output_path, format="PNG")
        return GenerationResult(request.output_path, seed, self.name, {"style": self.style, "mock": True})

    def edit(self, request: GenerationRequest) -> GenerationResult:
        return self.generate(request)

    def variation(self, request: GenerationRequest) -> GenerationResult:
        request.seed = (self._seed(request) + 17) % (2**31 - 1)
        return self.generate(request)

    def reference_generate(self, request: GenerationRequest) -> GenerationResult:
        return self.generate(request)


class ProviderRegistry:
    def __init__(self):
        self.providers: dict[str, ImageProvider] = {"mock": MockImageProvider()}

    def register(self, key: str, provider: ImageProvider) -> None:
        self.providers[key] = provider

    def get(self, key: str = "mock") -> ImageProvider:
        return self.providers[key]


class GenerationQueue:
    """Small thread-safe queue facade; GUI uses it from a QThread worker."""

    def __init__(self, provider: ImageProvider | None = None):
        self.provider = provider or MockImageProvider()
        self.paused = False
        self.cancelled = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def cancel(self) -> None:
        self.cancelled = True

    def run(self, requests: list[GenerationRequest], callback: Callable[[int, str, GenerationResult | Exception | None], None] | None = None) -> list[GenerationResult]:
        results: list[GenerationResult] = []
        for index, request in enumerate(requests):
            while self.paused and not self.cancelled:
                import time
                time.sleep(0.05)
            if self.cancelled:
                break
            if callback:
                callback(index, "Generating", None)
            try:
                result = self.provider.generate(request)
                results.append(result)
                if callback:
                    callback(index, "Done", result)
            except Exception as exc:  # provider failures must not kill the project
                log.exception("Generation failed")
                if callback:
                    callback(index, "Failed", exc)
        return results
