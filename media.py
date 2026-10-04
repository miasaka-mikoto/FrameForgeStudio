from __future__ import annotations

import re
import subprocess
from pathlib import Path


def media_duration(path: str | Path, ffprobe: str = "ffprobe") -> float:
    try:
        p = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, timeout=15, check=False)
        return float(p.stdout.strip()) if p.returncode == 0 else 0.0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        secs += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(rows: list[dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for index, row in enumerate(rows, start=1):
        lines.extend([str(index), f"{srt_timestamp(row['start_time'])} --> {srt_timestamp(row['end_time'])}", str(row.get("text", "")), ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def parse_srt(path: str | Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    chunks = re.split(r"\n\s*\n", text.strip()) if text.strip() else []
    rows: list[dict] = []
    for chunk in chunks:
        lines = chunk.splitlines()
        if len(lines) < 3:
            continue
        times = re.split(r"\s+-->\s+", lines[1])
        if len(times) != 2:
            continue
        def parse(value: str) -> float:
            h, m, s = value.replace(",", ".").split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        rows.append({"start_time": parse(times[0]), "end_time": parse(times[1]), "text": "\n".join(lines[2:]), "position": "bottom-left"})
    return rows

