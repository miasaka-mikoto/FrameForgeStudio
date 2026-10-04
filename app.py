from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from .config import APP_NAME, app_data_dir, load_settings, save_settings
from .project import Project
from .ui import MainWindow


def configure_logging(project: Project | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if project:
        project.root.joinpath("logs").mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(project.root / "logs" / "frameforge.log", encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers, force=True)


def create_sample(root: Path) -> Project:
    if (root / "project.json").exists():
        project = Project.open(root)
    else:
        project = Project.create(root, "FrameForge Sample Project")
    project.ensure_sample_content()
    return project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--project", type=Path, help="Open a FrameForge project")
    parser.add_argument("--sample", type=Path, help="Create/open a sample project at this path")
    parser.add_argument("--no-sample", action="store_true", help="Do not create a sample project automatically")
    args = parser.parse_args(argv)
    settings = load_settings()
    root = args.project or args.sample
    if root:
        project = create_sample(root) if args.sample else Project.open(root)
    else:
        # Keep first-run data outside the installed application directory. This
        # matters on Windows where Program Files is normally not writable.
        documents = Path.home() / "Documents"
        base = documents if documents.exists() else app_data_dir() / "projects"
        root = base / "FrameForgeStudio" / "FrameForge_Sample_Project"
        project = create_sample(root) if not args.no_sample else Project.create(base / "FrameForgeStudio" / "FrameForge_Untitled", "Untitled Animation")
    configure_logging(project)
    app = QApplication(sys.argv if argv is None else [sys.argv[0], *argv])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("FrameForge")
    window = MainWindow(project, settings)
    window.show()
    result = app.exec()
    save_settings(settings)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
