"""Run a compact end-to-end verification without opening a GUI."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from frameforge.continuity import analyze_pair
from frameforge.project import Project
from frameforge.render import RenderOptions, render_sequence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", type=Path, help="Keep the temporary verification project at this path")
    args = parser.parse_args()
    root = args.keep or Path(tempfile.mkdtemp(prefix="frameforge_verify_")) / "Sample"
    project = Project.create(root, "Verification Project")
    project.ensure_sample_content()
    shot = project.db.one("SELECT * FROM shots ORDER BY shot_id LIMIT 1")
    frames = project.db.all("SELECT * FROM frames WHERE shot_id=? ORDER BY frame_no", (shot["shot_id"],))
    result = analyze_pair(frames[0]["file_path"], frames[1]["file_path"])
    output = project.root / "exports" / "verify.mp4"
    ok, message = render_sequence(str(project.root / "frames" / shot["scene_id"] / shot["shot_id"] / "frame_%04d.png"), RenderOptions(fps=12, output_fps=24, width=320, height=180, output_path=output))
    print(f"project={project.root}")
    print(f"frames={len(project.db.all('SELECT * FROM frames'))}")
    print(f"continuity_overall={result.overall}")
    print(f"render_ok={ok} output={output}")
    if not ok:
        print(message)
    project.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

