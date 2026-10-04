from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class RenderOptions:
    fps: int = 24
    output_fps: int | None = None
    width: int = 1920
    height: int = 1080
    codec: str = "libx264"
    bitrate: str = "8M"
    output_path: Path = Path("output.mp4")
    audio_path: Path | None = None
    subtitle_path: Path | None = None
    burn_subtitles: bool = False
    duplicate_frames: bool = True


def find_ffmpeg(configured: str = "ffmpeg") -> str | None:
    if Path(configured).exists():
        return configured
    return shutil.which(configured)


def build_ffmpeg_command(frame_pattern: str, options: RenderOptions, ffmpeg: str = "ffmpeg") -> list[str]:
    cmd = [ffmpeg, "-y", "-framerate", str(options.fps), "-i", frame_pattern]
    if options.audio_path:
        cmd += ["-i", str(options.audio_path)]
    if options.subtitle_path and options.burn_subtitles:
        # Escape is intentionally conservative; paths are passed as individual argv items.
        cmd += ["-vf", f"subtitles={options.subtitle_path}"]
    cmd += ["-c:v", options.codec, "-b:v", options.bitrate, "-pix_fmt", "yuv420p", "-s", f"{options.width}x{options.height}"]
    if options.output_fps and options.output_fps != options.fps:
        # FFmpeg duplicates or drops frames to reach the final delivery rate.
        # This is the deterministic Frame Duplicate mode; optical-flow
        # interpolation can be plugged in before this stage later.
        cmd += ["-r", str(options.output_fps)]
    if options.audio_path:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(options.output_path))
    return cmd


def render_sequence(frame_pattern: str, options: RenderOptions, ffmpeg: str = "ffmpeg", timeout: int = 600) -> tuple[bool, str]:
    executable = find_ffmpeg(ffmpeg)
    if not executable:
        return False, "FFmpeg was not found. Set its path in Settings → FFmpeg."
    options.output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(frame_pattern, options, executable)
    log.info("FFmpeg render: %s", json.dumps(command, ensure_ascii=False))
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout)[-4000:]
    return True, proc.stdout[-4000:]
