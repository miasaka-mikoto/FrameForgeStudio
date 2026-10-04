from __future__ import annotations

from pathlib import Path

import pytest


def test_main_window_smoke(tmp_path: Path, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from frameforge.project import Project
    from frameforge.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    project = Project.create(tmp_path / "Gui", "GUI")
    project.ensure_sample_content()
    window = MainWindow(project, {"autosave_seconds": 30, "ffmpeg_path": "ffmpeg"})
    window.show()
    assert "FrameForge Studio" in window.windowTitle()
    QTimer.singleShot(20, app.quit)
    app.exec()
    window.close()

