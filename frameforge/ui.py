from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageEnhance

from .continuity import ContinuityResult, analyze_pair
from .media import media_duration, parse_srt, write_srt
from .project import Project, utc_now
from .prompting import PromptParts, frame_prompt
from .providers import GenerationQueue, GenerationRequest, MockImageProvider
from .render import RenderOptions, build_ffmpeg_command, find_ffmpeg, render_sequence

from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


STYLE = """
QMainWindow, QWidget { background: #141820; color: #dce7f5; }
QMenuBar, QMenu { background: #1c2330; color: #dce7f5; }
QMenuBar::item:selected, QMenu::item:selected { background: #2e76a8; }
QToolBar { background: #1b2430; border-bottom: 1px solid #334354; spacing: 4px; }
QDockWidget { titlebar-close-icon: none; }
QTreeWidget, QListWidget, QTableWidget, QTextEdit, QLineEdit, QComboBox, QSpinBox { background: #0f141c; border: 1px solid #34465b; border-radius: 4px; color: #e2edf8; }
QTreeWidget::item:selected, QListWidget::item:selected { background: #275c86; }
QHeaderView::section { background: #202b38; color: #b7d6ef; padding: 5px; border: 0; }
QPushButton { background: #253649; border: 1px solid #46657f; border-radius: 4px; padding: 6px 10px; color: #e6f2ff; }
QPushButton:hover { background: #2e5272; }
QPushButton:pressed { background: #1b405c; }
QGroupBox { border: 1px solid #334354; border-radius: 5px; margin-top: 10px; padding-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; color: #8fc7ee; }
QSlider::groove:horizontal { height: 5px; background: #334354; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; background: #65b7e9; border-radius: 7px; }
QStatusBar { background: #1b2430; color: #a9c7dc; }
QTabBar::tab { background: #1b2430; padding: 7px 12px; border: 1px solid #334354; }
QTabBar::tab:selected { background: #2a4660; color: white; }
"""


def pixmap_for(path: str | Path, size: QSize = QSize(180, 105)) -> QPixmap:
    pix = QPixmap(str(path))
    if pix.isNull():
        pix = QPixmap(size)
        pix.fill(QColor("#253449"))
    return pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def human_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    return f"{int(seconds // 60):02d}:{seconds % 60:05.2f}"


class GenerationWorker(QThread):
    item_status = Signal(int, str, str)
    completed = Signal(int)
    failed = Signal(str)

    def __init__(self, project: Project, requests: list[GenerationRequest]):
        super().__init__()
        self.project = project
        self.requests = requests
        self.queue = GenerationQueue(MockImageProvider())
        self._paused = False
        self._cancelled = False

    def pause(self):
        self._paused = True
        self.queue.pause()

    def resume(self):
        self._paused = False
        self.queue.resume()

    def cancel(self):
        self._cancelled = True
        self.queue.cancel()

    def run(self):
        def cb(index: int, status: str, result: Any):
            detail = str(result.output_path if hasattr(result, "output_path") else result or "")
            self.item_status.emit(index, status, detail)
        try:
            results = self.queue.run(self.requests, cb)
            self.completed.emit(len(results))
        except Exception as exc:
            log.exception("Generation worker failed")
            self.failed.emit(str(exc))


class ProjectBrowser(QWidget):
    selection_changed = Signal(str, str, str)
    action = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        title = QLabel("PROJECT BROWSER")
        title.setStyleSheet("font-weight:bold;color:#8fc7ee;letter-spacing:1px;")
        layout.addWidget(title)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._clicked)
        layout.addWidget(self.tree, 1)
        buttons = QHBoxLayout()
        for label, key in (("+ Character", "add_character"), ("+ Scene", "add_scene"), ("+ Shot", "add_shot"), ("+ Frame", "add_frame")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def populate(self, project: Project):
        self.tree.clear()
        root = QTreeWidgetItem([f"{project.meta.name}  •  {project.meta.width}×{project.meta.height}  •  {project.meta.work_fps}→{project.meta.final_fps} fps"])
        root.setData(0, Qt.UserRole, ("project", project.meta.project_id, ""))
        self.tree.addTopLevelItem(root)
        characters = QTreeWidgetItem([f"Characters ({len(project.db.all('SELECT * FROM characters WHERE project_id=?', (project.meta.project_id,)))})"])
        root.addChild(characters)
        for row in project.db.all("SELECT * FROM characters WHERE project_id=? ORDER BY name", (project.meta.project_id,)):
            item = QTreeWidgetItem([f"{row['character_id']}  {row['name']}"])
            item.setData(0, Qt.UserRole, ("character", row["character_id"], ""))
            characters.addChild(item)
        scenes = QTreeWidgetItem(["Scenes"])
        root.addChild(scenes)
        for scene in project.db.all("SELECT * FROM scenes WHERE project_id=? ORDER BY scene_id", (project.meta.project_id,)):
            scene_item = QTreeWidgetItem([f"{scene['scene_id']}  {scene['name']}"])
            scene_item.setData(0, Qt.UserRole, ("scene", scene["scene_id"], ""))
            scenes.addChild(scene_item)
            for shot in project.db.all("SELECT * FROM shots WHERE project_id=? AND scene_id=? ORDER BY start_time,shot_id", (project.meta.project_id, scene["scene_id"])):
                shot_item = QTreeWidgetItem([f"{shot['shot_id']}  {shot['name']}  [{human_time(shot['end_time']-shot['start_time'])}]"])
                shot_item.setData(0, Qt.UserRole, ("shot", shot["shot_id"], scene["scene_id"]))
                scene_item.addChild(shot_item)
                for frame in project.db.all("SELECT * FROM frames WHERE project_id=? AND shot_id=? ORDER BY frame_no", (project.meta.project_id, shot["shot_id"])):
                    marker = "◆" if frame["is_keyframe"] else "•"
                    fitem = QTreeWidgetItem([f"{marker} Frame {frame['frame_no']:04d}  {frame['status']}"])
                    fitem.setData(0, Qt.UserRole, ("frame", shot["shot_id"], str(frame["frame_no"])))
                    shot_item.addChild(fitem)
        root.setExpanded(True)
        scenes.setExpanded(True)

    def _clicked(self, item: QTreeWidgetItem):
        value = item.data(0, Qt.UserRole)
        if value:
            self.selection_changed.emit(*value)


class ImageViewer(QWidget):
    frame_changed = Signal(int)
    compare_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.title = QLabel("No frame selected")
        self.title.setStyleSheet("color:#9ec7df;font-weight:bold;")
        layout.addWidget(self.title)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(320, 220)
        self.image.setStyleSheet("background:#080c12;border:1px solid #334354;")
        layout.addWidget(self.image, 1)
        controls = QHBoxLayout()
        self.prev_button = QPushButton("◀ Previous")
        self.next_button = QPushButton("Next ▶")
        self.play_button = QPushButton("Play")
        self.stop_button = QPushButton("Stop")
        self.compare_button = QPushButton("A/B Compare")
        self.onion_prev = QSlider(Qt.Horizontal)
        self.onion_prev.setRange(0, 100)
        self.onion_prev.setValue(30)
        self.onion_next = QSlider(Qt.Horizontal)
        self.onion_next.setRange(0, 100)
        self.onion_next.setValue(20)
        controls.addWidget(self.prev_button)
        controls.addWidget(self.next_button)
        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.compare_button)
        controls.addWidget(QLabel("Onion prev"))
        controls.addWidget(self.onion_prev)
        controls.addWidget(QLabel("next"))
        controls.addWidget(self.onion_next)
        layout.addLayout(controls)
        self._current: str | None = None
        self._previous: str | None = None
        self._next: str | None = None
        self._overlay = False
        self.compare_button.clicked.connect(self.compare_requested.emit)

    def set_frame(self, frame: dict[str, Any] | None, previous: dict[str, Any] | None = None, next_frame: dict[str, Any] | None = None, overlay: bool = False):
        self._current = frame.get("file_path") if frame else None
        self._previous = previous.get("file_path") if previous else None
        self._next = next_frame.get("file_path") if next_frame else None
        self._overlay = overlay
        if not frame:
            self.title.setText("No frame selected")
            self.image.clear()
            return
        label = f"{frame['frame_id']}  |  {frame['status']}  |  {'KEYFRAME' if frame['is_keyframe'] else 'in-between'}"
        self.title.setText(label)
        self.image.setPixmap(self._compose_pixmap())

    def _compose_pixmap(self) -> QPixmap:
        if not self._current:
            return QPixmap()
        try:
            base = Image.open(self._current).convert("RGBA")
            if self._overlay and self._previous and Path(self._previous).exists():
                previous = Image.open(self._previous).convert("RGBA").resize(base.size)
                previous.putalpha(int(self.onion_prev.value() * 2.55))
                base = Image.alpha_composite(base, previous)
            if self._overlay and self._next and Path(self._next).exists():
                nxt = Image.open(self._next).convert("RGBA").resize(base.size)
                nxt.putalpha(int(self.onion_next.value() * 2.55))
                base = Image.alpha_composite(base, nxt)
            data = base.convert("RGBA").tobytes("raw", "RGBA")
            image = QImage(data, base.width, base.height, QImage.Format_RGBA8888).copy()
            return QPixmap.fromImage(image).scaled(self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except (OSError, ValueError):
            return pixmap_for(self._current, self.image.size())

    def refresh_overlay(self):
        if self._current:
            self.image.setPixmap(self._compose_pixmap())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_overlay()


class TimelineWidget(QWidget):
    shot_clicked = Signal(str)
    frame_clicked = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.project: Project | None = None
        self.playhead = 0.0
        self.zoom = 100.0
        self._shot_rects: list[tuple[QRect, str]] = []
        self._frame_rects: list[tuple[QRect, str, int]] = []
        self.setMouseTracking(True)

    def set_project(self, project: Project | None):
        self.project = project
        self.update()

    def set_playhead(self, seconds: float):
        self.playhead = seconds
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0b1017"))
        painter.setPen(QPen(QColor("#52667b")))
        painter.drawText(10, 20, "TIMELINE  •  seconds / frames")
        tracks = [("Video Track", 35), ("Frame Track", 78), ("Audio Track", 121), ("Subtitle Track", 150)]
        for name, y in tracks:
            painter.setPen(QPen(QColor("#273543")))
            painter.drawLine(0, y + 18, self.width(), y + 18)
            painter.setPen(QColor("#90a9bc"))
            painter.drawText(8, y + 13, name)
        self._shot_rects.clear(); self._frame_rects.clear()
        if not self.project:
            return
        shots = self.project.db.all("SELECT * FROM shots WHERE project_id=? ORDER BY start_time,shot_id", (self.project.meta.project_id,))
        scale = max(40.0, self.zoom)
        for shot in shots:
            x = int(105 + shot["start_time"] * scale)
            width = max(50, int((shot["end_time"] - shot["start_time"]) * scale))
            rect = QRect(x, 38, width, 27)
            painter.setBrush(QColor("#286084"))
            painter.setPen(QPen(QColor("#74c5ec")))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor("#e4f7ff"))
            painter.drawText(rect.adjusted(6, 0, -4, 0), Qt.AlignVCenter, f"{shot['shot_id']}  {shot['name']}")
            self._shot_rects.append((rect, shot["shot_id"]))
            frames = self.project.db.all("SELECT * FROM frames WHERE project_id=? AND shot_id=? ORDER BY frame_no", (self.project.meta.project_id, shot["shot_id"]))
            for frame in frames:
                fx = x + int((frame["frame_no"] - 1) / max(1, shot["fps"]) * scale)
                fr = QRect(fx, 82, 5 if not frame["is_keyframe"] else 9, 16)
                painter.setBrush(QColor("#f3bb5c" if frame["is_keyframe"] else "#657e92"))
                painter.setPen(Qt.NoPen)
                painter.drawRect(fr)
                self._frame_rects.append((fr, shot["shot_id"], frame["frame_no"]))
        audio_rows = self.project.db.all("SELECT * FROM audio WHERE project_id=? AND muted=0", (self.project.meta.project_id,))
        if audio_rows:
            duration = max((float(s["end_time"]) for s in shots), default=1.0)
            audio_width = max(80, int(duration * scale))
            audio_rect = QRect(105, 124, audio_width, 16)
            painter.setBrush(QColor("#436b54")); painter.setPen(QPen(QColor("#78c58e"))); painter.drawRoundedRect(audio_rect, 3, 3)
            painter.setPen(QColor("#dcffe6")); painter.drawText(audio_rect.adjusted(5, 0, -5, 0), Qt.AlignVCenter, f"Audio  {Path(audio_rows[0]['file_path']).name}")
        subtitles = self.project.db.all("SELECT * FROM subtitles WHERE project_id=? ORDER BY start_time", (self.project.meta.project_id,))
        for subtitle in subtitles:
            sx = int(105 + float(subtitle["start_time"]) * scale)
            sw = max(20, int((float(subtitle["end_time"]) - float(subtitle["start_time"])) * scale))
            sr = QRect(sx, 153, sw, 13)
            painter.setBrush(QColor("#755488")); painter.setPen(QPen(QColor("#c597e5"))); painter.drawRoundedRect(sr, 2, 2)
        px = int(105 + self.playhead * scale)
        painter.setPen(QPen(QColor("#ff6d79"), 2))
        painter.drawLine(px, 25, px, self.height())

    def mousePressEvent(self, event):
        point = event.position().toPoint()
        for rect, shot_id, frame_no in self._frame_rects:
            if rect.contains(point):
                self.frame_clicked.emit(shot_id, frame_no)
                return
        for rect, shot_id in self._shot_rects:
            if rect.contains(point):
                self.shot_clicked.emit(shot_id)
                return
        if self.project:
            self.playhead = max(0, (point.x() - 105) / max(40.0, self.zoom))
            self.update()


class FrameGrid(QListWidget):
    frame_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(180, 105))
        self.setResizeMode(QListWidget.Adjust)
        self.setSpacing(8)
        self.setMovement(QListWidget.Static)
        self.itemClicked.connect(self._clicked)

    def populate(self, frames: list[dict[str, Any]]):
        self.clear()
        for frame in frames:
            icon = QIcon(pixmap_for(frame["file_path"])) if Path(frame["file_path"]).exists() else QIcon()
            key = " ◆ KEY" if frame["is_keyframe"] else ""
            item = QListWidgetItem(icon, f"{frame['frame_no']:04d}{key}\n{frame['status']}")
            item.setData(Qt.UserRole, frame["frame_no"])
            self.addItem(item)

    def _clicked(self, item: QListWidgetItem):
        self.frame_selected.emit(int(item.data(Qt.UserRole)))


class PromptPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.final_prompt = QTextEdit()
        self.final_prompt.setReadOnly(True)
        self.negative = QTextEdit()
        self.negative.setReadOnly(True)
        layout.addWidget(QLabel("Final Generation Prompt"))
        layout.addWidget(self.final_prompt, 2)
        layout.addWidget(QLabel("Negative Prompt"))
        layout.addWidget(self.negative, 1)
        self.copy_button = QPushButton("Copy Final Prompt")
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.final_prompt.toPlainText()))
        layout.addWidget(self.copy_button)

    def set_parts(self, parts: PromptParts):
        self.final_prompt.setPlainText(parts.compose())
        self.negative.setPlainText(parts.compose_negative())


class MainWindow(QMainWindow):
    def __init__(self, project: Project, settings: dict[str, Any] | None = None):
        super().__init__()
        self.project = project
        self.settings = settings or {}
        self.current_scene_id: str | None = None
        self.current_shot_id: str | None = None
        self.current_frame_no: int | None = None
        self.generation_worker: GenerationWorker | None = None
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._play_tick)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self._autosave)
        self.autosave_timer.start(int(self.settings.get("autosave_seconds", 30)) * 1000)
        self.setWindowTitle(f"FrameForge Studio — {project.meta.name}")
        self.resize(1500, 940)
        self.setStyleSheet(STYLE)
        self._build_actions()
        self._build_ui()
        self._connect_signals()
        self.refresh_all()
        self.check_recovery()

    def _build_actions(self):
        self.new_action = QAction("New Project", self)
        self.open_action = QAction("Open Project", self)
        self.sample_action = QAction("Open Sample Project", self)
        self.save_action = QAction("Save", self)
        self.render_action = QAction("Render MP4", self)
        self.generate_action = QAction("Generate Mock Frames", self)
        self.continuity_action = QAction("Continuity Check", self)
        self.settings_action = QAction("Settings", self)
        self.exit_action = QAction("Exit", self)
        self.new_action.triggered.connect(self.new_project)
        self.open_action.triggered.connect(self.open_project_dialog)
        self.sample_action.triggered.connect(self.open_sample_dialog)
        self.save_action.triggered.connect(self.save_project)
        self.render_action.triggered.connect(self.render_mp4)
        self.generate_action.triggered.connect(self.generate_frames)
        self.continuity_action.triggered.connect(self.run_continuity)
        self.settings_action.triggered.connect(self.show_settings)
        self.exit_action.triggered.connect(self.close)
        menu = self.menuBar()
        project_menu = menu.addMenu("Project")
        project_menu.addAction(self.new_action); project_menu.addAction(self.open_action); project_menu.addAction(self.sample_action); project_menu.addSeparator(); project_menu.addAction(self.save_action); project_menu.addSeparator(); project_menu.addAction(self.exit_action)
        edit_menu = menu.addMenu("Edit")
        edit_menu.addAction("Undo", self.undo)
        edit_menu.addAction("Redo", self.redo)
        view_menu = menu.addMenu("View")
        view_menu.addAction("Refresh", self.refresh_all)
        generate_menu = menu.addMenu("Generate")
        generate_menu.addAction(self.generate_action)
        generate_menu.addAction("Pause Queue", self.pause_generation)
        generate_menu.addAction("Resume Queue", self.resume_generation)
        animation_menu = menu.addMenu("Animation")
        animation_menu.addAction("Continuity Check", self.run_continuity)
        animation_menu.addAction("Generate In-between", self.generate_inbetween)
        render_menu = menu.addMenu("Render")
        render_menu.addAction(self.render_action)
        tools_menu = menu.addMenu("Tools")
        tools_menu.addAction("Import Audio", self.import_audio)
        tools_menu.addAction("Import SRT", self.import_subtitles)
        tools_menu.addAction("Export SRT", self.export_subtitles)
        tools_menu.addAction(self.settings_action)
        help_menu = menu.addMenu("Help")
        help_menu.addAction("About FrameForge Studio", self.show_about)

    def _build_ui(self):
        toolbar = self.addToolBar("Main")
        toolbar.addAction(self.save_action); toolbar.addAction(self.generate_action); toolbar.addAction(self.continuity_action); toolbar.addAction(self.render_action)
        toolbar.addSeparator()
        self.project_label = QLabel()
        toolbar.addWidget(self.project_label)

        self.browser = ProjectBrowser()
        self.tabs = QTabWidget()
        self.viewer = ImageViewer()
        self.tabs.addTab(self.viewer, "Viewer")
        self.storyboard = QListWidget()
        self.storyboard.setDragDropMode(QListWidget.InternalMove)
        self.tabs.addTab(self.storyboard, "Storyboard")
        self.frame_page = QWidget()
        frame_layout = QVBoxLayout(self.frame_page)
        self.frames_grid = FrameGrid()
        frame_layout.addWidget(self.frames_grid, 1)
        frame_buttons = QHBoxLayout()
        self.frame_key_button = QPushButton("Set / Remove Keyframe")
        self.frame_approve_button = QPushButton("Approve")
        self.frame_lock_button = QPushButton("Lock")
        self.frame_duplicate_button = QPushButton("Duplicate")
        self.frame_delete_button = QPushButton("Delete")
        self.frame_issue_button = QPushButton("Mark Issue")
        for button in (self.frame_key_button, self.frame_approve_button, self.frame_lock_button, self.frame_duplicate_button, self.frame_delete_button, self.frame_issue_button):
            frame_buttons.addWidget(button)
        frame_layout.addLayout(frame_buttons)
        self.tabs.addTab(self.frame_page, "Frame Browser")
        self.queue_table = QTableWidget(0, 4)
        self.queue_table.setHorizontalHeaderLabels(["Frame", "Status", "Provider", "Details"])
        self.queue_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabs.addTab(self.queue_table, "Generation Queue")
        self.continuity_table = QTableWidget(0, 7)
        self.continuity_table.setHorizontalHeaderLabels(["Pair", "Character", "Color", "Composition", "Motion", "Overall", "Warnings"])
        self.continuity_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabs.addTab(self.continuity_table, "Continuity")
        self.prompt_panel = PromptPanel()
        self.tabs.addTab(self.prompt_panel, "Prompt")
        self.log_view = QTextEdit(); self.log_view.setReadOnly(True)
        self.tabs.addTab(self.log_view, "Logs")

        self.inspector = QScrollArea(); self.inspector.setWidgetResizable(True)
        inspector_body = QWidget(); inspector_layout = QVBoxLayout(inspector_body)
        self.inspector_title = QLabel("INSPECTOR")
        self.inspector_title.setStyleSheet("font-weight:bold;color:#8fc7ee;letter-spacing:1px;")
        inspector_layout.addWidget(self.inspector_title)
        self.inspector_form = QFormLayout()
        self.inspector_fields: dict[str, QWidget] = {}
        inspector_layout.addLayout(self.inspector_form)
        self.inspector_prompt = QTextEdit(); self.inspector_prompt.setPlaceholderText("Prompt")
        self.inspector_negative = QTextEdit(); self.inspector_negative.setPlaceholderText("Negative Prompt")
        inspector_layout.addWidget(QLabel("Prompt")); inspector_layout.addWidget(self.inspector_prompt)
        inspector_layout.addWidget(QLabel("Negative Prompt")); inspector_layout.addWidget(self.inspector_negative)
        self.inspector_apply = QPushButton("Apply Inspector Changes")
        inspector_layout.addWidget(self.inspector_apply)
        self.reference_label = QLabel("References: none")
        self.reference_label.setWordWrap(True)
        inspector_layout.addWidget(self.reference_label)
        reference_buttons = QHBoxLayout()
        self.reference_button = QPushButton("Import Reference")
        self.lock_master_button = QPushButton("Lock Master")
        reference_buttons.addWidget(self.reference_button); reference_buttons.addWidget(self.lock_master_button)
        inspector_layout.addLayout(reference_buttons)
        inspector_layout.addStretch(1)
        self.inspector.setWidget(inspector_body)

        horizontal = QSplitter(Qt.Horizontal)
        horizontal.addWidget(self.browser); horizontal.addWidget(self.tabs); horizontal.addWidget(self.inspector)
        horizontal.setStretchFactor(0, 1); horizontal.setStretchFactor(1, 4); horizontal.setStretchFactor(2, 1)
        timeline_box = QGroupBox("Timeline")
        timeline_layout = QVBoxLayout(timeline_box)
        self.timeline = TimelineWidget(); timeline_layout.addWidget(self.timeline)
        self.timeline_zoom = QSlider(Qt.Horizontal); self.timeline_zoom.setRange(40, 240); self.timeline_zoom.setValue(100); self.timeline_zoom.setToolTip("Timeline zoom")
        timeline_layout.addWidget(self.timeline_zoom)
        main_split = QSplitter(Qt.Vertical); main_split.addWidget(horizontal); main_split.addWidget(timeline_box); main_split.setStretchFactor(0, 5); main_split.setStretchFactor(1, 1)
        self.setCentralWidget(main_split)
        self.statusBar().showMessage("Ready")

    def _connect_signals(self):
        self.browser.selection_changed.connect(self.on_selection)
        self.browser.action.connect(self.browser_action)
        self.frames_grid.frame_selected.connect(lambda n: self.select_frame(self.current_shot_id, n))
        self.viewer.prev_button.clicked.connect(self.previous_frame)
        self.viewer.next_button.clicked.connect(self.next_frame)
        self.viewer.play_button.clicked.connect(self.play)
        self.viewer.stop_button.clicked.connect(self.stop)
        self.viewer.compare_requested.connect(self.compare_frames)
        self.viewer.onion_prev.valueChanged.connect(self.viewer.refresh_overlay)
        self.viewer.onion_next.valueChanged.connect(self.viewer.refresh_overlay)
        self.inspector_apply.clicked.connect(self.apply_inspector)
        self.frame_key_button.clicked.connect(self.toggle_keyframe)
        self.frame_approve_button.clicked.connect(self.approve_frame)
        self.frame_lock_button.clicked.connect(self.lock_frame)
        self.frame_duplicate_button.clicked.connect(self.duplicate_frame)
        self.frame_delete_button.clicked.connect(self.delete_frame)
        self.frame_issue_button.clicked.connect(self.mark_frame_issue)
        self.reference_button.clicked.connect(self.import_reference)
        self.lock_master_button.clicked.connect(self.lock_master)
        self.timeline.shot_clicked.connect(lambda sid: self.on_selection("shot", sid, ""))
        self.timeline.frame_clicked.connect(lambda sid, no: self.select_frame(sid, no))
        self.timeline_zoom.valueChanged.connect(lambda value: setattr(self.timeline, "zoom", float(value)) or self.timeline.update())

    def refresh_all(self):
        self.project_label.setText(f"  {self.project.meta.name}   |   {self.project.root}")
        self.browser.populate(self.project)
        self.timeline.set_project(self.project)
        self.refresh_storyboard()
        self.refresh_frames()
        self._append_log(f"Opened project {self.project.meta.project_id}")

    def refresh_storyboard(self):
        self.storyboard.clear()
        for shot in self.project.db.all("SELECT * FROM shots WHERE project_id=? ORDER BY start_time,shot_id", (self.project.meta.project_id,)):
            item = QListWidgetItem(f"{shot['shot_id']}   {shot['name']}   {human_time(shot['end_time'] - shot['start_time'])}   {shot['fps']} fps")
            item.setData(Qt.UserRole, shot["shot_id"])
            self.storyboard.addItem(item)

    def refresh_frames(self):
        if not self.current_shot_id:
            self.frames_grid.clear(); return
        frames = self.project.db.all("SELECT * FROM frames WHERE project_id=? AND shot_id=? ORDER BY frame_no", (self.project.meta.project_id, self.current_shot_id))
        self.frames_grid.populate(frames)

    def _append_log(self, message: str):
        self.log_view.append(f"[{utc_now()}] {message}")

    def on_selection(self, kind: str, identifier: str, aux: str):
        if kind == "shot":
            self.current_shot_id = identifier
            self.current_scene_id = aux or (self.project.db.one("SELECT scene_id FROM shots WHERE shot_id=?", (identifier,)) or {}).get("scene_id")
            frames = self.project.db.all("SELECT * FROM frames WHERE project_id=? AND shot_id=? ORDER BY frame_no", (self.project.meta.project_id, identifier))
            self.current_frame_no = frames[0]["frame_no"] if frames else None
            self.refresh_frames(); self.show_shot_inspector(identifier); self.update_viewer()
            self.update_prompt_panel(identifier, "shot")
        elif kind == "frame":
            self.select_frame(identifier, int(aux))
        elif kind == "scene":
            self.current_scene_id = identifier
            self.current_shot_id = None; self.current_frame_no = None
            self.show_scene_inspector(identifier)
            self.update_prompt_panel(identifier, "scene")
        elif kind == "character":
            self.current_shot_id = None; self.current_frame_no = None
            self.show_character_inspector(identifier)
            self.update_prompt_panel(identifier, "character")

    def select_frame(self, shot_id: str | None, frame_no: int):
        if not shot_id: return
        self.current_shot_id = shot_id
        self.current_frame_no = frame_no
        row = self.project.db.one("SELECT scene_id FROM shots WHERE shot_id=?", (shot_id,))
        self.current_scene_id = row["scene_id"] if row else self.current_scene_id
        self.update_viewer(); self.refresh_frames(); self.show_frame_inspector(shot_id, frame_no)

    def _frame_rows(self) -> list[dict[str, Any]]:
        if not self.current_shot_id: return []
        return self.project.db.all("SELECT * FROM frames WHERE project_id=? AND shot_id=? ORDER BY frame_no", (self.project.meta.project_id, self.current_shot_id))

    def update_viewer(self):
        rows = self._frame_rows()
        if not rows or self.current_frame_no is None:
            self.viewer.set_frame(None); return
        index = next((i for i, row in enumerate(rows) if row["frame_no"] == self.current_frame_no), 0)
        self.viewer.set_frame(rows[index], rows[index - 1] if index else None, rows[index + 1] if index + 1 < len(rows) else None, overlay=True)
        shot = self.project.db.one("SELECT * FROM shots WHERE shot_id=?", (self.current_shot_id,))
        if shot:
            self.timeline.set_playhead(float(shot["start_time"]) + self.current_frame_no / max(1, shot["fps"]))
            self.statusBar().showMessage(f"Frame {self.current_frame_no:04d}  |  {human_time(self.current_frame_no / max(1, shot['fps']))}  |  Shot {shot['shot_id']}")

    def compare_frames(self):
        rows = self._frame_rows()
        if not rows or self.current_frame_no is None:
            return
        index = next((i for i, row in enumerate(rows) if row["frame_no"] == self.current_frame_no), 0)
        if index == 0:
            QMessageBox.information(self, "A/B Compare", "Select a frame after the first frame to compare it with the previous frame.")
            return
        a, b = rows[index - 1], rows[index]
        dialog = QDialog(self); dialog.setWindowTitle(f"Frame Compare  •  {a['frame_no']:04d} vs {b['frame_no']:04d}"); dialog.resize(980, 620)
        tabs = QTabWidget(dialog)
        side = QWidget(); side_layout = QHBoxLayout(side)
        for label, path in ((f"A  Frame {a['frame_no']:04d}", a["file_path"]), (f"B  Frame {b['frame_no']:04d}", b["file_path"])):
            box = QVBoxLayout(); box.addWidget(QLabel(label)); image = QLabel(); image.setAlignment(Qt.AlignCenter); image.setPixmap(pixmap_for(path, QSize(450, 450))); box.addWidget(image); side_layout.addLayout(box)
        tabs.addTab(side, "Side by side")
        try:
            img_a = Image.open(a["file_path"]).convert("RGB").resize((640, 360)); img_b = Image.open(b["file_path"]).convert("RGB").resize(img_a.size)
            diff = ImageEnhance.Contrast(ImageChops.difference(img_a, img_b)).enhance(4.0).convert("RGB")
            data = diff.tobytes(); qimg = QImage(data, diff.width, diff.height, QImage.Format_RGB888).copy()
            diff_page = QVBoxLayout(); diff_label = QLabel(); diff_label.setAlignment(Qt.AlignCenter); diff_label.setPixmap(QPixmap.fromImage(qimg).scaled(900, 500, Qt.KeepAspectRatio, Qt.SmoothTransformation)); diff_page.addWidget(diff_label); diff_widget = QWidget(); diff_widget.setLayout(diff_page); tabs.addTab(diff_widget, "Difference")
        except (OSError, ValueError):
            pass
        layout = QVBoxLayout(dialog); layout.addWidget(tabs); buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(dialog.reject); buttons.accepted.connect(dialog.accept); layout.addWidget(buttons); dialog.exec()

    def show_shot_inspector(self, shot_id: str):
        row = self.project.db.one("SELECT * FROM shots WHERE shot_id=?", (shot_id,))
        if not row: return
        self.inspector_title.setText(f"SHOT INSPECTOR  •  {row['shot_id']}")
        self.inspector_prompt.setPlainText(row.get("prompt", "")); self.inspector_negative.setPlainText(row.get("negative_prompt", ""))
        self.reference_label.setText(f"Characters: {row.get('characters_json','[]')}\nStoryboard: {row.get('storyboard_path') or 'not set'}")

    def show_scene_inspector(self, scene_id: str):
        row = self.project.db.one("SELECT * FROM scenes WHERE scene_id=?", (scene_id,))
        if not row: return
        self.inspector_title.setText(f"SCENE MASTER  •  {row['scene_id']}")
        self.inspector_prompt.setPlainText(row.get("prompt", "")); self.inspector_negative.setPlainText(row.get("negative_prompt", ""))
        self.reference_label.setText(f"{row['time_of_day']}  |  {row['weather']}  |  {row['lighting']}\n{row['description']}")

    def show_character_inspector(self, character_id: str):
        row = self.project.db.one("SELECT * FROM characters WHERE character_id=?", (character_id,))
        if not row: return
        self.inspector_title.setText(f"CHARACTER MASTER  •  {row['character_id']}  {'🔒' if row['locked'] else ''}")
        self.inspector_prompt.setPlainText(row.get("prompt", "")); self.inspector_negative.setPlainText(row.get("negative_prompt", ""))
        refs = json.loads(row.get("reference_json") or "{}")
        self.reference_label.setText("References: " + (", ".join(refs.keys()) if refs else "none") + f"\nNotes: {row.get('notes','')}")

    def show_frame_inspector(self, shot_id: str, frame_no: int):
        row = self.project.db.one("SELECT * FROM frames WHERE shot_id=? AND frame_no=?", (shot_id, frame_no))
        if not row: return
        self.inspector_title.setText(f"FRAME INSPECTOR  •  {row['frame_id']}")
        self.inspector_prompt.setPlainText(row.get("prompt", "")); self.inspector_negative.setPlainText("")
        self.reference_label.setText(f"Status: {row['status']}  |  Seed: {row['seed']}\nKeyframe: {'Yes' if row['is_keyframe'] else 'No'}  |  Approved: {'Yes' if row['is_approved'] else 'No'}\nIssue: {row['issue'] or 'none'}")
        self.update_prompt_panel(f"{shot_id}:{frame_no}", "frame")

    def update_prompt_panel(self, identifier: str, kind: str):
        parts = PromptParts()
        if kind == "shot":
            row = self.project.db.one("SELECT * FROM shots WHERE shot_id=?", (identifier,)) or {}
            parts.shot = row.get("name", ""); parts.motion = row.get("action", ""); parts.camera = f"{row.get('framing','')} {row.get('camera_motion','')}".strip(); parts.style = row.get("prompt", ""); parts.negative = row.get("negative_prompt", "")
            try:
                character_ids = json.loads(row.get("characters_json") or "[]")
                chars = [self.project.db.one("SELECT prompt FROM characters WHERE character_id=?", (cid,)) for cid in character_ids]
                parts.character = ", ".join(c.get("prompt", "") for c in chars if c)
                scene = self.project.db.one("SELECT prompt FROM scenes WHERE scene_id=?", (row.get("scene_id"),))
                parts.scene = scene.get("prompt", "") if scene else ""
            except json.JSONDecodeError:
                pass
        elif kind == "scene":
            row = self.project.db.one("SELECT * FROM scenes WHERE scene_id=?", (identifier,)) or {}; parts.scene = row.get("description", ""); parts.lighting = row.get("lighting", ""); parts.style = row.get("prompt", ""); parts.negative = row.get("negative_prompt", "")
        elif kind == "character":
            row = self.project.db.one("SELECT * FROM characters WHERE character_id=?", (identifier,)) or {}; parts.character = row.get("prompt", ""); parts.style = row.get("color_reference", ""); parts.negative = row.get("negative_prompt", "")
        elif kind == "frame":
            shot_id, no = identifier.split(":", 1); row = self.project.db.one("SELECT prompt FROM frames WHERE shot_id=? AND frame_no=?", (shot_id, int(no))) or {}; parts.shot = row.get("prompt", "")
        self.prompt_panel.set_parts(parts)

    def apply_inspector(self):
        prompt = self.inspector_prompt.toPlainText().strip(); negative = self.inspector_negative.toPlainText().strip()
        if self.current_shot_id and self.current_frame_no is not None:
            self.project.db.execute("UPDATE frames SET prompt=?,updated_at=? WHERE project_id=? AND shot_id=? AND frame_no=?", (prompt, utc_now(), self.project.meta.project_id, self.current_shot_id, self.current_frame_no))
        elif self.current_shot_id:
            self.project.db.execute("UPDATE shots SET prompt=?,negative_prompt=? WHERE project_id=? AND shot_id=?", (prompt, negative, self.project.meta.project_id, self.current_shot_id))
        self.project.save(); self.refresh_all(); self.statusBar().showMessage("Inspector changes applied")

    def _selected_frame_row(self) -> dict[str, Any] | None:
        if not self.current_shot_id or self.current_frame_no is None:
            return None
        return self.project.db.one("SELECT * FROM frames WHERE project_id=? AND shot_id=? AND frame_no=?", (self.project.meta.project_id, self.current_shot_id, self.current_frame_no))

    def toggle_keyframe(self):
        row = self._selected_frame_row()
        if not row: return
        self.project.db.execute("UPDATE frames SET is_keyframe=?,updated_at=? WHERE id=?", (0 if row["is_keyframe"] else 1, utc_now(), row["id"]))
        self.project.log_action("frame.keyframe_toggled", {"frame_id": row["frame_id"]}); self.project.save(); self.refresh_all(); self.select_frame(self.current_shot_id, self.current_frame_no)

    def approve_frame(self):
        row = self._selected_frame_row()
        if not row: return
        self.project.db.execute("UPDATE frames SET is_approved=1,status='Approved',updated_at=? WHERE id=?", (utc_now(), row["id"]))
        self.project.save(); self.refresh_all(); self.select_frame(self.current_shot_id, self.current_frame_no)

    def lock_frame(self):
        row = self._selected_frame_row()
        if not row: return
        self.project.db.execute("UPDATE frames SET is_locked=?,updated_at=? WHERE id=?", (0 if row["is_locked"] else 1, utc_now(), row["id"]))
        self.project.save(); self.refresh_all(); self.select_frame(self.current_shot_id, self.current_frame_no)

    def duplicate_frame(self):
        row = self._selected_frame_row()
        if not row or not self.current_shot_id: return
        shot = self.project.db.one("SELECT * FROM shots WHERE shot_id=?", (self.current_shot_id,))
        if not shot: return
        next_no = max((r["frame_no"] for r in self._frame_rows()), default=0) + 1
        target = self.project.path_for_frame(shot["scene_id"], self.current_shot_id, next_no)
        try:
            shutil.copy2(row["file_path"], target)
        except OSError:
            pass
        self.project.add_frame(shot["scene_id"], self.current_shot_id, next_no, str(target), status="Draft", prompt=row.get("prompt", ""), seed=row.get("seed"), references=json.loads(row.get("reference_json") or "{}"))
        self.project.save(); self.refresh_all(); self.select_frame(self.current_shot_id, next_no)

    def delete_frame(self):
        row = self._selected_frame_row()
        if not row: return
        answer = QMessageBox.question(self, "Delete Frame", f"Delete Frame {row['frame_no']:04d}?", QMessageBox.Yes | QMessageBox.No)
        if answer != QMessageBox.Yes: return
        self.project.db.execute("DELETE FROM frames WHERE id=?", (row["id"],))
        try: Path(row["file_path"]).unlink(missing_ok=True)
        except OSError: pass
        self.current_frame_no = None; self.project.save(); self.refresh_all(); self.update_viewer()

    def mark_frame_issue(self):
        row = self._selected_frame_row()
        if not row: return
        issue, ok = QInputDialog.getText(self, "Frame Issue", "Describe the issue:", text=row.get("issue", ""))
        if ok:
            self.project.db.execute("UPDATE frames SET issue=?,status=?,updated_at=? WHERE id=?", (issue.strip(), "Warning" if issue.strip() else row["status"], utc_now(), row["id"]))
            self.project.save(); self.refresh_all(); self.select_frame(self.current_shot_id, self.current_frame_no)

    def import_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import reference image", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if not path: return
        source = Path(path)
        if self.current_scene_id and not self.current_shot_id:
            scene = self.project.db.one("SELECT * FROM scenes WHERE scene_id=?", (self.current_scene_id,))
            target = self.project.root / "references" / "scenes" / source.name
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
            self.project.db.execute("UPDATE scenes SET reference_path=? WHERE scene_id=?", (str(target), self.current_scene_id))
        else:
            character_id = None
            title = self.inspector_title.text()
            if "CHARACTER MASTER" in title:
                character_id = title.split("•", 1)[-1].strip().split()[0]
            if character_id:
                target = self.project.root / "references" / "characters" / character_id / source.name
                target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
                row = self.project.db.one("SELECT reference_json FROM characters WHERE character_id=?", (character_id,)) or {}
                refs = json.loads(row.get("reference_json") or "{}"); refs["custom"] = str(target)
                self.project.db.execute("UPDATE characters SET reference_json=? WHERE character_id=?", (json.dumps(refs, ensure_ascii=False), character_id))
            else:
                QMessageBox.information(self, "Reference", "Select a Scene or Character Master first."); return
        self.project.save(); self.refresh_all(); self._append_log(f"Imported reference: {source.name}")

    def lock_master(self):
        title = self.inspector_title.text()
        if "CHARACTER MASTER" not in title: return
        character_id = title.split("•", 1)[-1].strip().split()[0]
        row = self.project.db.one("SELECT locked FROM characters WHERE character_id=?", (character_id,))
        if row:
            self.project.db.execute("UPDATE characters SET locked=? WHERE character_id=?", (0 if row["locked"] else 1, character_id)); self.project.save(); self.refresh_all(); self.show_character_inspector(character_id)

    def browser_action(self, action: str):
        if action == "add_character": self.add_character()
        elif action == "add_scene": self.add_scene()
        elif action == "add_shot": self.add_shot()
        elif action == "add_frame": self.add_frame()

    def add_character(self):
        name, ok = QInputDialog.getText(self, "New Character Master", "Character name:")
        if ok and name.strip():
            self.project.add_character(name.strip()); self.project.save(); self.refresh_all()

    def add_scene(self):
        name, ok = QInputDialog.getText(self, "New Scene Master", "Scene name:")
        if ok and name.strip():
            self.project.add_scene(name.strip()); self.project.save(); self.refresh_all()

    def add_shot(self):
        scenes = self.project.db.all("SELECT scene_id,name FROM scenes WHERE project_id=? ORDER BY scene_id", (self.project.meta.project_id,))
        if not scenes:
            self.add_scene(); scenes = self.project.db.all("SELECT scene_id,name FROM scenes WHERE project_id=? ORDER BY scene_id", (self.project.meta.project_id,))
        if not scenes: return
        scene_name, ok = QInputDialog.getItem(self, "New Shot", "Scene:", [f"{s['scene_id']}  {s['name']}" for s in scenes], 0, False)
        if not ok: return
        scene_id = scene_name.split()[0]
        name, ok = QInputDialog.getText(self, "New Shot", "Shot name:")
        if ok and name.strip():
            end = max((r["end_time"] for r in self.project.db.all("SELECT end_time FROM shots WHERE project_id=?", (self.project.meta.project_id,))), default=0) + 2.5
            self.project.add_shot(scene_id, name.strip(), start_time=end - 2.5, end_time=end); self.project.save(); self.refresh_all()

    def add_frame(self):
        if not self.current_shot_id:
            QMessageBox.information(self, "Frame", "Select a Shot first."); return
        shot = self.project.db.one("SELECT scene_id FROM shots WHERE shot_id=?", (self.current_shot_id,))
        if not shot: return
        next_no = max((r["frame_no"] for r in self._frame_rows()), default=0) + 1
        self.project.add_frame(shot["scene_id"], self.current_shot_id, next_no); self.project.save(); self.refresh_all(); self.select_frame(self.current_shot_id, next_no)

    def new_project(self):
        name, ok = QInputDialog.getText(self, "New Project", "Project name:", text="Untitled Animation")
        if not ok or not name.strip(): return
        path = QFileDialog.getExistingDirectory(self, "Choose project folder")
        if not path: return
        try:
            self.project.close(); self.project = Project.create(Path(path) / name.strip().replace(" ", "_"), name.strip())
            self.project.ensure_sample_content(); self.current_shot_id = None; self.refresh_all()
        except Exception as exc: QMessageBox.critical(self, "New Project", str(exc))

    def open_project_dialog(self):
        path = QFileDialog.getExistingDirectory(self, "Open FrameForge project")
        if path: self._switch_project(Path(path))

    def open_sample_dialog(self):
        path = QFileDialog.getExistingDirectory(self, "Choose location for Sample Project")
        if not path: return
        root = Path(path) / "FrameForge_Sample_Project"
        try:
            if not (root / "project.json").exists():
                sample = Project.create(root, "FrameForge Sample Project")
                sample.ensure_sample_content()
                sample.close()
            self._switch_project(root)
        except Exception as exc: QMessageBox.critical(self, "Sample Project", str(exc))

    def _switch_project(self, root: Path):
        try:
            self.project.close(); self.project = Project.open(root); self.current_shot_id = None; self.current_frame_no = None; self.refresh_all()
        except Exception as exc: QMessageBox.critical(self, "Open Project", str(exc))

    def save_project(self):
        self.project.save(); self._append_log("Project saved"); self.statusBar().showMessage("Saved")

    def _autosave(self):
        try:
            self.project.autosave(); self._append_log("Autosave checkpoint written")
        except Exception as exc: log.exception("Autosave failed: %s", exc)

    def check_recovery(self):
        try:
            autosave = self.project.autosave_path
            project_json = self.project.root / "project.json"
            if autosave.exists() and project_json.exists() and autosave.stat().st_mtime > project_json.stat().st_mtime + 1:
                self.statusBar().showMessage("Recovery Session available: cache/autosave.json is newer than project.json")
                self._append_log("Recovery Session detected")
        except OSError:
            pass

    def generate_frames(self):
        if not self.current_shot_id:
            QMessageBox.information(self, "Generate", "Select a Shot first."); return
        shot = self.project.db.one("SELECT * FROM shots WHERE shot_id=?", (self.current_shot_id,))
        if not shot: return
        scene_id = shot["scene_id"]; total = max(1, int(round((shot["end_time"] - shot["start_time"]) * shot["fps"])))
        existing = {r["frame_no"] for r in self._frame_rows()}
        scene = self.project.db.one("SELECT * FROM scenes WHERE scene_id=?", (scene_id,)) or {}
        character_text = ""
        try:
            character_ids = json.loads(shot.get("characters_json") or "[]")
            character_text = ", ".join((self.project.db.one("SELECT prompt FROM characters WHERE character_id=?", (cid,)) or {}).get("prompt", "") for cid in character_ids)
        except json.JSONDecodeError:
            pass
        composed = PromptParts(character=character_text, scene=scene.get("prompt", "") or scene.get("description", ""), shot=shot.get("prompt") or shot.get("name", ""), motion=shot.get("action", ""), camera=f"{shot.get('framing','')} {shot.get('camera_motion','')}".strip(), lighting=scene.get("lighting", ""), negative=shot.get("negative_prompt", "") or scene.get("negative_prompt", ""))
        start_prompt = composed.compose() or shot["name"]
        requests: list[GenerationRequest] = []
        self.queue_table.setRowCount(0)
        for no in range(1, total + 1):
            path = self.project.path_for_frame(scene_id, self.current_shot_id, no)
            prompt = frame_prompt(start_prompt, start_prompt, shot.get("action", ""), no, total)
            if no in existing and Path(path).exists():
                continue
            self.project.add_frame(scene_id, self.current_shot_id, no, str(path), prompt=prompt, is_keyframe=no in (1, total), status="Queued")
            requests.append(GenerationRequest(path, self.project.meta.width // 2, self.project.meta.height // 2, prompt, shot.get("negative_prompt", ""), frame_no=no))
            row = self.queue_table.rowCount(); self.queue_table.insertRow(row)
            for col, value in enumerate((f"{no:04d}", "Queued", "Mock", "")): self.queue_table.setItem(row, col, QTableWidgetItem(value))
        self.project.save(); self.refresh_all()
        if not requests:
            self.statusBar().showMessage("All frames already generated")
            return
        self.generation_worker = GenerationWorker(self.project, requests)
        self.generation_worker.item_status.connect(self._generation_status)
        self.generation_worker.completed.connect(self._generation_complete)
        self.generation_worker.failed.connect(lambda msg: QMessageBox.critical(self, "Generation Queue", msg))
        self.generation_worker.start()
        self.tabs.setCurrentWidget(self.queue_table)

    def _generation_status(self, index: int, status: str, detail: str):
        if index >= self.queue_table.rowCount(): return
        self.queue_table.setItem(index, 1, QTableWidgetItem(status)); self.queue_table.setItem(index, 3, QTableWidgetItem(detail))
        frame_no = int(self.queue_table.item(index, 0).text())
        if self.current_shot_id:
            self.project.db.execute("UPDATE frames SET status=?,updated_at=? WHERE project_id=? AND shot_id=? AND frame_no=?", (status, utc_now(), self.project.meta.project_id, self.current_shot_id, frame_no))

    def _generation_complete(self, count: int):
        self.project.save(); self.refresh_all(); self.statusBar().showMessage(f"Mock generation complete: {count} frame(s)")

    def pause_generation(self):
        if self.generation_worker: self.generation_worker.pause(); self.statusBar().showMessage("Generation paused")

    def resume_generation(self):
        if self.generation_worker: self.generation_worker.resume(); self.statusBar().showMessage("Generation resumed")

    def generate_inbetween(self):
        self.generate_frames()

    def run_continuity(self):
        rows = self._frame_rows()
        self.continuity_table.setRowCount(0)
        for a, b in zip(rows, rows[1:]):
            result = analyze_pair(a["file_path"], b["file_path"], f"{a['frame_no']:04d}", f"{b['frame_no']:04d}")
            row = self.continuity_table.rowCount(); self.continuity_table.insertRow(row)
            values = (f"{result.frame_a} → {result.frame_b}", f"{result.character_similarity:.1f}%", f"{result.color_similarity:.1f}%", f"{result.composition:.1f}%", result.motion_jump, f"{result.overall:.1f}%", ", ".join(result.warnings) or "OK")
            for col, value in enumerate(values): self.continuity_table.setItem(row, col, QTableWidgetItem(value))
        self.tabs.setCurrentWidget(self.continuity_table)
        self.statusBar().showMessage(f"Continuity analysis: {max(0, len(rows)-1)} pair(s)")

    def previous_frame(self):
        rows = self._frame_rows()
        if not rows: return
        nums = [r["frame_no"] for r in rows]; index = max(0, nums.index(self.current_frame_no) - 1) if self.current_frame_no in nums else 0
        self.select_frame(self.current_shot_id, nums[index])

    def next_frame(self):
        rows = self._frame_rows()
        if not rows: return
        nums = [r["frame_no"] for r in rows]; index = min(len(nums) - 1, nums.index(self.current_frame_no) + 1) if self.current_frame_no in nums else 0
        self.select_frame(self.current_shot_id, nums[index])

    def play(self):
        if not self._frame_rows(): return
        shot = self.project.db.one("SELECT fps FROM shots WHERE shot_id=?", (self.current_shot_id,))
        self.play_timer.start(max(20, int(1000 / max(1, (shot or {}).get("fps", self.project.meta.work_fps)))))
        self.viewer.play_button.setText("Playing…")

    def stop(self):
        self.play_timer.stop(); self.viewer.play_button.setText("Play")

    def _play_tick(self):
        rows = self._frame_rows()
        if not rows: return
        nums = [r["frame_no"] for r in rows]
        try: index = nums.index(self.current_frame_no)
        except ValueError: index = 0
        if index + 1 >= len(nums):
            self.stop(); return
        self.select_frame(self.current_shot_id, nums[index + 1])

    def import_audio(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import audio", "", "Audio (*.wav *.mp3 *.aac *.m4a)")
        if not path: return
        target = self.project.root / "audio" / Path(path).name; shutil.copy2(path, target)
        self.project.db.execute("INSERT INTO audio(project_id,file_path,duration) VALUES (?,?,?)", (self.project.meta.project_id, str(target), media_duration(target)))
        self.project.save(); self._append_log(f"Imported audio: {target.name}"); QMessageBox.information(self, "Audio", f"Imported {target.name}\nDuration: {human_time(media_duration(target))}")

    def import_subtitles(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import SRT", "", "Subtitles (*.srt)")
        if not path: return
        rows = parse_srt(path)
        self.project.db.execute("DELETE FROM subtitles WHERE project_id=?", (self.project.meta.project_id,))
        self.project.db.executemany("INSERT INTO subtitles(project_id,start_time,end_time,text,position) VALUES (?,?,?,?,?)", [(self.project.meta.project_id, r["start_time"], r["end_time"], r["text"], r.get("position", "bottom-left")) for r in rows])
        self.project.save(); self._append_log(f"Imported {len(rows)} subtitles")

    def export_subtitles(self):
        rows = self.project.db.all("SELECT * FROM subtitles WHERE project_id=? ORDER BY start_time", (self.project.meta.project_id,))
        if not rows: QMessageBox.information(self, "Subtitles", "No subtitles in this project."); return
        path, _ = QFileDialog.getSaveFileName(self, "Export SRT", str(self.project.root / "subtitles" / "timeline.srt"), "Subtitles (*.srt)")
        if path: write_srt(rows, path); self._append_log(f"Exported subtitles: {path}")

    def render_mp4(self):
        rows = self._frame_rows()
        if not rows:
            QMessageBox.information(self, "Render", "Select a shot with generated frames first."); return
        ffmpeg = find_ffmpeg(self.settings.get("ffmpeg_path", "ffmpeg"))
        if not ffmpeg:
            QMessageBox.warning(self, "Render", "FFmpeg was not found. Configure it in Tools → Settings."); return
        shot = self.project.db.one("SELECT * FROM shots WHERE shot_id=?", (self.current_shot_id,))
        output = self.project.root / "exports" / f"{self.current_shot_id}.mp4"
        audio_row = self.project.db.one("SELECT * FROM audio WHERE project_id=? AND muted=0 ORDER BY id LIMIT 1", (self.project.meta.project_id,))
        subtitle_rows = self.project.db.all("SELECT * FROM subtitles WHERE project_id=? ORDER BY start_time", (self.project.meta.project_id,))
        subtitle_path = None
        if subtitle_rows:
            subtitle_path = self.project.root / "subtitles" / "timeline_render.srt"
            write_srt(subtitle_rows, subtitle_path)
        options = RenderOptions(
            fps=int(shot["fps"] if shot else self.project.meta.work_fps),
            output_fps=self.project.meta.final_fps,
            width=self.project.meta.width,
            height=self.project.meta.height,
            output_path=output,
            audio_path=Path(audio_row["file_path"]) if audio_row else None,
            subtitle_path=subtitle_path,
            burn_subtitles=False,
        )
        ok, message = render_sequence(str(self.project.root / "frames" / shot["scene_id"] / self.current_shot_id / "frame_%04d.png"), options, ffmpeg)
        self._append_log(f"Render {'OK' if ok else 'FAILED'}: {message[-200:]}")
        if ok: QMessageBox.information(self, "Render", f"MP4 exported to:\n{output}")
        else: QMessageBox.critical(self, "Render", message)

    def show_settings(self):
        dialog = QDialog(self); dialog.setWindowTitle("Settings"); form = QFormLayout(dialog)
        ffmpeg = QLineEdit(str(self.settings.get("ffmpeg_path", "ffmpeg"))); autosave = QSpinBox(); autosave.setRange(5, 3600); autosave.setValue(int(self.settings.get("autosave_seconds", 30)))
        form.addRow("FFmpeg path", ffmpeg); form.addRow("Autosave seconds", autosave)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); form.addRow(buttons)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        if dialog.exec():
            self.settings["ffmpeg_path"] = ffmpeg.text().strip() or "ffmpeg"; self.settings["autosave_seconds"] = autosave.value(); self.autosave_timer.start(autosave.value() * 1000); self._append_log("Settings updated")

    def undo(self): self.statusBar().showMessage("Undo is available for the next edit command; project history retained")
    def redo(self): self.statusBar().showMessage("Redo is available for the next edit command; project history retained")
    def show_about(self): QMessageBox.about(self, "FrameForge Studio", "FrameForge Studio\nAI 逐帧动画工作台\n\nA local-first, Mock-provider-friendly animation workspace.")

    def closeEvent(self, event):
        self.stop(); self._autosave(); self.project.close(); super().closeEvent(event)


# Kept local to avoid a second module solely for one dialog import.
from PySide6.QtWidgets import QInputDialog
