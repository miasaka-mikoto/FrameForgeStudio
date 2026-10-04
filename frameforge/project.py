from __future__ import annotations

import json
import logging
import shutil
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_FINAL_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH, DEFAULT_WORK_FPS
from .db import Database

log = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ProjectMeta:
    project_id: str
    name: str
    path: str
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    aspect_ratio: str = "16:9"
    target_fps: int = DEFAULT_FINAL_FPS
    work_fps: int = DEFAULT_WORK_FPS
    final_fps: int = DEFAULT_FINAL_FPS
    description: str = ""
    created_at: str = ""
    updated_at: str = ""


class Project:
    DIRS = ("references", "characters", "scenes", "shots", "frames", "audio", "subtitles", "exports", "cache", "logs", "database")

    def __init__(self, root: Path, meta: ProjectMeta, db: Database):
        self.root = Path(root)
        self.meta = meta
        self.db = db
        self.autosave_path = self.root / "cache" / "autosave.json"

    @classmethod
    def create(cls, root: Path, name: str, **kwargs: Any) -> "Project":
        root = Path(root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        for dirname in cls.DIRS:
            (root / dirname).mkdir(parents=True, exist_ok=True)
        now = utc_now()
        project_id = kwargs.pop("project_id", f"PROJ_{uuid.uuid4().hex[:10].upper()}")
        meta = ProjectMeta(project_id=project_id, name=name, path=str(root), created_at=now, updated_at=now, **kwargs)
        (root / "project.json").write_text(json.dumps(asdict(meta), indent=2, ensure_ascii=False), encoding="utf-8")
        db = Database(root / "database" / "project.sqlite3")
        db.execute(
            "INSERT INTO projects(project_id,name,path,width,height,aspect_ratio,target_fps,work_fps,final_fps,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (meta.project_id, meta.name, str(root), meta.width, meta.height, meta.aspect_ratio, meta.target_fps, meta.work_fps, meta.final_fps, meta.description, now, now),
        )
        log.info("Created project %s at %s", meta.project_id, root)
        return cls(root, meta, db)

    @classmethod
    def open(cls, root: Path) -> "Project":
        root = Path(root).expanduser().resolve()
        meta_path = root / "project.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Not a FrameForge project: {meta_path}")
        meta = ProjectMeta(**json.loads(meta_path.read_text(encoding="utf-8")))
        db_path = root / "database" / "project.sqlite3"
        db = Database(db_path)
        project = cls(root, meta, db)
        project._repair_media_paths()
        # Projects are portable folders. Older/sample databases may contain
        # absolute paths from the machine that created them; persist the
        # current root after repairing those references.
        if project.meta.path != str(root):
            project.meta.path = str(root)
            project.save()
        log.info("Opened project %s", root)
        return project

    def _repair_media_paths(self) -> None:
        """Relink media paths after a project folder is moved or cloned."""

        def resolve(raw: str, preferred: Path | None = None) -> Path | None:
            if not raw:
                return None
            value = Path(raw)
            if value.is_absolute() and value.exists():
                return value
            candidate = (self.root / value).resolve()
            if candidate.exists():
                return candidate
            # Recover an absolute path by keeping the project-relative suffix.
            parts = value.parts
            markers = {"references", "characters", "scenes", "shots", "frames", "audio", "subtitles", "exports"}
            for index, part in enumerate(parts):
                if part in markers:
                    candidate = self.root.joinpath(*parts[index:])
                    if candidate.exists():
                        return candidate
            if preferred and preferred.exists():
                return preferred
            return None

        for row in self.db.all("SELECT id,scene_id,shot_id,frame_no,file_path FROM frames WHERE project_id=?", (self.meta.project_id,)):
            raw = row.get("file_path", "")
            suffix = Path(raw).suffix or ".png"
            preferred = self.root / "frames" / row["scene_id"] / row["shot_id"] / f"frame_{int(row['frame_no']):04d}{suffix}"
            resolved = resolve(raw, preferred)
            if resolved and str(resolved) != raw:
                self.db.execute("UPDATE frames SET file_path=? WHERE id=?", (str(resolved), row["id"]))

        for row in self.db.all("SELECT id,file_path FROM audio WHERE project_id=?", (self.meta.project_id,)):
            raw = row.get("file_path", "")
            resolved = resolve(raw, self.root / "audio" / Path(raw).name)
            if resolved and str(resolved) != raw:
                self.db.execute("UPDATE audio SET file_path=? WHERE id=?", (str(resolved), row["id"]))

        for row in self.db.all("SELECT id,reference_path FROM scenes WHERE project_id=?", (self.meta.project_id,)):
            resolved = resolve(row.get("reference_path", ""))
            if resolved and str(resolved) != row.get("reference_path", ""):
                self.db.execute("UPDATE scenes SET reference_path=? WHERE id=?", (str(resolved), row["id"]))

        for row in self.db.all("SELECT id,storyboard_path FROM shots WHERE project_id=?", (self.meta.project_id,)):
            resolved = resolve(row.get("storyboard_path", ""))
            if resolved and str(resolved) != row.get("storyboard_path", ""):
                self.db.execute("UPDATE shots SET storyboard_path=? WHERE id=?", (str(resolved), row["id"]))

    def save(self) -> None:
        self.meta.updated_at = utc_now()
        (self.root / "project.json").write_text(json.dumps(asdict(self.meta), indent=2, ensure_ascii=False), encoding="utf-8")
        self.db.execute("UPDATE projects SET name=?,width=?,height=?,aspect_ratio=?,target_fps=?,work_fps=?,final_fps=?,description=?,updated_at=? WHERE project_id=?", (
            self.meta.name, self.meta.width, self.meta.height, self.meta.aspect_ratio, self.meta.target_fps, self.meta.work_fps, self.meta.final_fps, self.meta.description, self.meta.updated_at, self.meta.project_id))

    def autosave(self) -> None:
        payload = {"meta": asdict(self.meta), "saved_at": utc_now()}
        self.autosave_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def close(self) -> None:
        self.save()
        self.db.close()

    def path_for_frame(self, scene_id: str, shot_id: str, frame_no: int, suffix: str = ".png") -> Path:
        path = self.root / "frames" / scene_id / shot_id
        path.mkdir(parents=True, exist_ok=True)
        return path / f"frame_{frame_no:04d}{suffix}"

    def log_action(self, action: str, payload: dict[str, Any] | None = None) -> None:
        self.db.execute("INSERT INTO history(project_id,action,payload,created_at) VALUES (?,?,?,?)", (self.meta.project_id, action, json.dumps(payload or {}, ensure_ascii=False), utc_now()))

    def add_character(self, name: str, character_id: str | None = None, **fields: Any) -> dict[str, Any]:
        character_id = character_id or f"CHAR_{name.upper().replace(' ', '_')[:16]}_{uuid.uuid4().hex[:3].upper()}"
        self.db.execute("INSERT INTO characters(project_id,character_id,name,reference_json,body_proportion,color_reference,notes,prompt,negative_prompt,locked,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            self.meta.project_id, character_id, name, json.dumps(fields.pop("references", {}), ensure_ascii=False), fields.get("body_proportion", ""), fields.get("color_reference", ""), fields.get("notes", ""), fields.get("prompt", ""), fields.get("negative_prompt", ""), int(fields.get("locked", False)), utc_now()))
        self.log_action("character.created", {"character_id": character_id})
        return self.db.one("SELECT * FROM characters WHERE project_id=? AND character_id=?", (self.meta.project_id, character_id)) or {}

    def add_scene(self, name: str, scene_id: str | None = None, **fields: Any) -> dict[str, Any]:
        scene_id = scene_id or f"SCENE_{uuid.uuid4().hex[:6].upper()}"
        self.db.execute("INSERT INTO scenes(project_id,scene_id,name,reference_path,time_of_day,weather,lighting,palette,description,camera_info,prompt,negative_prompt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            self.meta.project_id, scene_id, name, fields.get("reference_path", ""), fields.get("time_of_day", ""), fields.get("weather", ""), fields.get("lighting", ""), fields.get("palette", ""), fields.get("description", ""), fields.get("camera_info", ""), fields.get("prompt", ""), fields.get("negative_prompt", "")))
        self.log_action("scene.created", {"scene_id": scene_id})
        return self.db.one("SELECT * FROM scenes WHERE project_id=? AND scene_id=?", (self.meta.project_id, scene_id)) or {}

    def add_shot(self, scene_id: str, name: str, shot_id: str | None = None, **fields: Any) -> dict[str, Any]:
        shot_id = shot_id or f"SHOT_{uuid.uuid4().hex[:6].upper()}"
        start = float(fields.get("start_time", 0))
        end = float(fields.get("end_time", start + float(fields.get("duration", 2.5))))
        self.db.execute("INSERT INTO shots(project_id,scene_id,shot_id,name,start_time,end_time,fps,camera_type,framing,camera_motion,characters_json,action,emotion,prompt,negative_prompt,storyboard_path) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            self.meta.project_id, scene_id, shot_id, name, start, end, int(fields.get("fps", self.meta.work_fps)), fields.get("camera_type", ""), fields.get("framing", ""), fields.get("camera_motion", ""), json.dumps(fields.get("characters", [])), fields.get("action", ""), fields.get("emotion", ""), fields.get("prompt", ""), fields.get("negative_prompt", ""), fields.get("storyboard_path", "")))
        self.log_action("shot.created", {"shot_id": shot_id})
        return self.db.one("SELECT * FROM shots WHERE project_id=? AND shot_id=?", (self.meta.project_id, shot_id)) or {}

    def add_frame(self, scene_id: str, shot_id: str, frame_no: int, file_path: str | None = None, **fields: Any) -> dict[str, Any]:
        frame_id = fields.pop("frame_id", f"FRAME_{frame_no:04d}")
        path = file_path or str(self.path_for_frame(scene_id, shot_id, frame_no))
        now = utc_now()
        self.db.execute("INSERT OR REPLACE INTO frames(project_id,scene_id,shot_id,frame_no,frame_id,file_path,status,prompt,seed,reference_json,is_keyframe,is_approved,is_locked,issue,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            self.meta.project_id, scene_id, shot_id, frame_no, frame_id, path, fields.get("status", "Draft"), fields.get("prompt", ""), fields.get("seed"), json.dumps(fields.get("references", {}), ensure_ascii=False), int(fields.get("is_keyframe", False)), int(fields.get("is_approved", False)), int(fields.get("is_locked", False)), fields.get("issue", ""), fields.get("created_at", now), now))
        self.log_action("frame.created", {"shot_id": shot_id, "frame_no": frame_no})
        return self.db.one("SELECT * FROM frames WHERE project_id=? AND shot_id=? AND frame_no=?", (self.meta.project_id, shot_id, frame_no)) or {}

    def ensure_sample_content(self) -> None:
        """Create a small usable sample project if it has no scene yet."""
        if not self.db.one("SELECT id FROM scenes WHERE project_id=? LIMIT 1", (self.meta.project_id,)):
            char = self.add_character("Misaka (Mock)", "CHAR_MISAKA_001", prompt="short chestnut hair, confident anime heroine", notes="Sample Character Master", locked=True)
            scene = self.add_scene("Neon Bridge", "SCENE_001", time_of_day="Night", weather="Light wind", lighting="Blue-green city light", palette="teal / amber", description="A quiet bridge over a glowing city", prompt="cinematic night bridge")
            for i, action in enumerate(("approaches the railing", "looks toward the city", "raises a hand"), start=1):
                self.add_shot(scene["scene_id"], f"Sample Shot {i:03d}", f"SHOT_{i:03d}", start_time=(i - 1) * 2.5, end_time=i * 2.5, fps=self.meta.work_fps, framing="Wide Shot" if i == 1 else "Medium Shot", camera_motion="slow push-in", characters=[char["character_id"]], action=action, emotion="focused", prompt=action)
        self._ensure_sample_media()
        self.save()

    def _ensure_sample_media(self) -> None:
        """Populate a few local mock frames, a silent WAV, and subtitles."""
        from .providers import GenerationRequest, MockImageProvider
        import wave
        shots = self.db.all("SELECT * FROM shots WHERE project_id=? ORDER BY start_time,shot_id", (self.meta.project_id,))
        provider = MockImageProvider()
        for shot in shots:
            count = min(8, max(4, int(round((shot["end_time"] - shot["start_time"]) * shot["fps"]))))
            for no in range(1, count + 1):
                path = self.path_for_frame(shot["scene_id"], shot["shot_id"], no)
                if not path.exists():
                    provider.generate(GenerationRequest(path, min(self.meta.width, 960), min(self.meta.height, 540), shot.get("prompt") or shot["name"], frame_no=no))
                if not self.db.one("SELECT id FROM frames WHERE project_id=? AND shot_id=? AND frame_no=?", (self.meta.project_id, shot["shot_id"], no)):
                    self.add_frame(shot["scene_id"], shot["shot_id"], no, str(path), status="Done", prompt=shot.get("prompt", ""), is_keyframe=no in (1, count), is_approved=True)
        audio_path = self.root / "audio" / "sample_silence.wav"
        if not audio_path.exists():
            with wave.open(str(audio_path), "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(44100)
                wav.writeframes(b"\x00\x00" * 44100 * 2)
        if not self.db.one("SELECT id FROM audio WHERE project_id=? LIMIT 1", (self.meta.project_id,)):
            self.db.execute("INSERT INTO audio(project_id,file_path,duration) VALUES (?,?,?)", (self.meta.project_id, str(audio_path), 2.0))
        if not self.db.one("SELECT id FROM subtitles WHERE project_id=? LIMIT 1", (self.meta.project_id,)):
            self.db.executemany("INSERT INTO subtitles(project_id,start_time,end_time,text,position) VALUES (?,?,?,?,?)", [
                (self.meta.project_id, 0.0, 2.4, "The city holds its breath.", "bottom-left"),
                (self.meta.project_id, 2.5, 5.0, "A frame at a time, the world moves.", "bottom-left"),
            ])

    def clone_to(self, destination: Path) -> "Project":
        destination = Path(destination).resolve()
        if destination.exists():
            raise FileExistsError(destination)
        shutil.copytree(self.root, destination)
        return Project.open(destination)
