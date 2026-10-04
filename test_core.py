from __future__ import annotations

import json
from pathlib import Path

from frameforge.continuity import analyze_pair
from frameforge.media import parse_srt, write_srt
from frameforge.project import Project
from frameforge.prompting import PromptParts, frame_prompt
from frameforge.providers import DuplicateInterpolationProvider, GenerationQueue, GenerationRequest, MockImageProvider
from frameforge.render import RenderOptions, build_ffmpeg_command


def test_project_structure_and_crud(tmp_path: Path):
    project = Project.create(tmp_path / "Project", "Test Project")
    assert (project.root / "project.json").exists()
    assert all((project.root / directory).is_dir() for directory in Project.DIRS)
    character = project.add_character("Hero", "CHAR_HERO_001", locked=True)
    scene = project.add_scene("Street", "SCENE_001")
    shot = project.add_shot(scene["scene_id"], "Opening", "SHOT_001", end_time=2.0)
    frame = project.add_frame(scene["scene_id"], shot["shot_id"], 1, is_keyframe=True)
    assert character["locked"] == 1
    assert shot["scene_id"] == scene["scene_id"]
    assert frame["is_keyframe"] == 1
    project.autosave()
    assert json.loads(project.autosave_path.read_text())["meta"]["name"] == "Test Project"
    project.close()
    reopened = Project.open(project.root)
    assert reopened.db.one("SELECT * FROM frames WHERE frame_id=?", ("FRAME_0001",))
    reopened.close()


def test_sample_project_has_media(tmp_path: Path):
    project = Project.create(tmp_path / "Sample", "Sample")
    project.ensure_sample_content()
    assert len(project.db.all("SELECT * FROM characters")) == 1
    assert len(project.db.all("SELECT * FROM scenes")) == 1
    assert len(project.db.all("SELECT * FROM shots")) == 3
    assert len(project.db.all("SELECT * FROM frames")) >= 12
    assert len(project.db.all("SELECT * FROM audio")) == 1
    assert len(project.db.all("SELECT * FROM subtitles")) == 2
    project.close()


def test_prompt_and_inbetween_prompt():
    parts = PromptParts(character="hero", scene="night bridge", shot="wide shot", motion="slow push", negative="blur, blur, anatomy")
    assert parts.compose() == "hero, night bridge, wide shot, slow push"
    assert parts.compose_negative() == "blur, anatomy"
    assert "transition 2/10" in frame_prompt("start", "end", "walk", 2, 10)


def test_mock_provider_and_continuity(tmp_path: Path):
    provider = MockImageProvider()
    a = tmp_path / "a.png"; b = tmp_path / "b.png"
    provider.generate(GenerationRequest(a, 320, 180, "same scene", frame_no=1))
    provider.generate(GenerationRequest(b, 320, 180, "same scene", frame_no=2))
    assert a.exists() and b.exists()
    result = analyze_pair(a, b)
    assert 0 <= result.overall <= 100
    assert isinstance(result.warnings, list)


def test_generation_queue_reports_done(tmp_path: Path):
    statuses = []
    requests = [GenerationRequest(tmp_path / f"{i}.png", 160, 90, str(i), frame_no=i) for i in range(3)]
    results = GenerationQueue(MockImageProvider()).run(requests, lambda i, status, result: statuses.append((i, status)))
    assert len(results) == 3
    assert [status for _, status in statuses] == ["Generating", "Done", "Generating", "Done", "Generating", "Done"]


def test_duplicate_interpolation_provider(tmp_path: Path):
    source = tmp_path / "source.png"; target = tmp_path / "interpolated.png"
    MockImageProvider().generate(GenerationRequest(source, 64, 64, "x"))
    DuplicateInterpolationProvider().interpolate(source, source, target)
    assert target.exists() and target.read_bytes() == source.read_bytes()


def test_ffmpeg_command_generation(tmp_path: Path):
    output = tmp_path / "out.mp4"
    command = build_ffmpeg_command("frames/frame_%04d.png", RenderOptions(fps=24, width=1920, height=1080, output_path=output), "ffmpeg")
    assert command[:6] == ["ffmpeg", "-y", "-framerate", "24", "-i", "frames/frame_%04d.png"]
    assert "-c:v" in command and str(output) in command


def test_srt_roundtrip(tmp_path: Path):
    path = tmp_path / "test.srt"
    rows = [{"start_time": 0.0, "end_time": 1.25, "text": "Hello", "position": "bottom-left"}]
    write_srt(rows, path)
    parsed = parse_srt(path)
    assert parsed[0]["text"] == "Hello"
    assert abs(parsed[0]["end_time"] - 1.25) < 0.001
