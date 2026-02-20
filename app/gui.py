"""PySide6 GUI orchestrator for WUT Batcher."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
from pathlib import Path
import subprocess
import sys
import traceback
from typing import Any, Callable, Dict, List, Mapping, Optional

from app.doctor_service import run_doctor_checks
from app.constants import DEFAULT_RUNNER_MODE
from app.models import AppConfig, Batch, Project, ProjectConstraints
from app.project_issue_model import UiProjectIssue, classify_ui_severity, issue_counts, normalize_project_issues
from app.services import OrchestratorService, PreviewGenerationCancelled
from app.settings_store import UserSettings
from app.ui_validation import UiValidationEngine
from ui.batch_export_panel import BatchExportPanel
from ui.batch_parameter_form import BatchParameterForm
from ui.batch_preview_placeholder import BatchPreviewPlaceholder
from ui.compat_ui_adapter import CompatUiAdapter
from ui.form_builder import ParameterForm
from ui.form_metrics import FORM_METRICS
from ui.form_schema import build_project_form_schema
from ui.theme import apply_theme, apply_windows_dark_titlebar, configure_windows_qt_darkmode_env

LOGGER = logging.getLogger(__name__)

try:
    from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QEvent, QObject, Qt, QThread, QTimer, Signal, QSize
    from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap, QIcon, QPalette
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QComboBox,
        QCheckBox,
        QDialog,
        QFormLayout,
        QFrame,
        QGraphicsOpacityEffect,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QListView,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QProgressBar,
        QScrollArea,
        QSizePolicy,
        QSplashScreen,
        QStackedWidget,
        QStatusBar,
        QTextEdit,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("PySide6 is required for GUI mode. Install it with 'pip install PySide6'.") from exc


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            self.clicked.emit()


class IssueRowButton(QPushButton):
    def __init__(self, full_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = str(full_text or "")
        self.setText(self._full_text)
        self.setToolTip(self._full_text)

    def set_full_text(self, text: str) -> None:
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self._apply_elide()

    def _apply_elide(self) -> None:
        available = max(int(self.width()) - 14, 24)
        elided = self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, available)
        self.setText(elided)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_elide()


class _BatchPreviewWorker(QObject):
    finished = Signal(int, dict)
    failed = Signal(int, str)
    canceled = Signal(int, str)

    def __init__(
        self,
        *,
        service: OrchestratorService,
        project_id: str,
        selected_params: Dict[str, Any],
        sweep_mode: str,
        request_id: int,
    ) -> None:
        super().__init__()
        self._service = service
        self._project_id = str(project_id)
        self._selected_params = dict(selected_params or {})
        self._sweep_mode = str(sweep_mode or "single")
        self._request_id = int(request_id)
        self._cancelled = False
        self._process: Optional[subprocess.Popen[str]] = None

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._process
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception as exc:
            LOGGER.debug("Preview worker cancel terminate failed: %s", exc)
            return

    def _cancel_check(self) -> bool:
        return bool(self._cancelled)

    def _on_process_started(self, process: subprocess.Popen[str]) -> None:
        self._process = process
        if self._cancelled:
            try:
                if process.poll() is None:
                    process.terminate()
            except Exception as exc:
                LOGGER.debug("Preview worker post-start terminate failed: %s", exc)
                return

    def run(self) -> None:
        try:
            result = self._service.generate_preview_stl(
                project_id=self._project_id,
                selected_params=self._selected_params,
                sweep_mode=self._sweep_mode,
                run_id=f"ui_preview_{self._request_id}",
                cancel_check=self._cancel_check,
                process_handle_cb=self._on_process_started,
            )
        except PreviewGenerationCancelled as exc:
            self.canceled.emit(self._request_id, str(exc))
            return
        except Exception as exc:  # pragma: no cover - integration surface
            self.failed.emit(self._request_id, str(exc))
            return
        self.finished.emit(self._request_id, dict(result))


class _BatchRunWorker(QObject):
    finished = Signal(str, dict)
    failed = Signal(str, str)

    def __init__(
        self,
        *,
        service: OrchestratorService,
        project_id: str,
        batch_id: str,
        continue_on_error: bool,
    ) -> None:
        super().__init__()
        self._service = service
        self._project_id = str(project_id)
        self._batch_id = str(batch_id)
        self._continue_on_error = bool(continue_on_error)

    def run(self) -> None:
        try:
            summary = self._service.run_batch(
                self._project_id,
                self._batch_id,
                continue_on_error=self._continue_on_error,
            )
            payload = asdict(summary)
            self.finished.emit(self._batch_id, payload)
        except Exception:
            self.failed.emit(self._batch_id, traceback.format_exc())


def _severity_rank(value: str) -> int:
    order = {"fatal": 0, "warn": 1, "info": 2}
    return order.get(str(value).lower(), 99)


def _highest_issue_severity(issues: List[Dict[str, Any]]) -> str:
    if not issues:
        return ""
    ranked = sorted((str(item.get("severity", "info")).lower() for item in issues), key=_severity_rank)
    return ranked[0] if ranked else ""


def _status_entries(detail: str) -> List[Dict[str, str]]:
    def _humanize_rule_id(value: str) -> str:
        token = str(value or "").strip()
        if not token:
            return "Issue"
        return token.replace("_", " ").replace(".", " ").strip().title()

    raw = str(detail or "").strip()
    if not raw:
        return [{"severity": "info", "title": "Status", "text": "No details available."}]

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return [{"severity": "info", "title": "Status", "text": raw}]

    entries: List[Dict[str, str]] = []
    if isinstance(payload, dict):
        overall = str(payload.get("overall_status", "")).strip().lower()
        if overall:
            overall_map = {"ok": "ok", "warn": "warn", "fail": "fatal"}
            entries.append(
                {
                    "severity": overall_map.get(overall, "info"),
                    "title": "Doctor Overall",
                    "text": f"Overall status: {overall.upper()}",
                }
            )
        checks = payload.get("checks")
        if isinstance(checks, list):
            for item in checks:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status", "")).strip().lower()
                severity_map = {"ok": "ok", "warn": "warn", "fail": "fatal"}
                entries.append(
                    {
                        "severity": severity_map.get(status, "info"),
                        "title": str(item.get("label", "Check")).strip() or "Check",
                        "text": str(item.get("detail", "")).strip() or "No detail.",
                    }
                )
        issues = payload.get("issues")
        if isinstance(issues, list):
            for item in issues:
                if not isinstance(item, dict):
                    continue
                entries.append(
                    {
                        "severity": str(item.get("severity", "info")).strip().lower(),
                        "title": _humanize_rule_id(str(item.get("rule_id", "Issue"))),
                        "text": str(item.get("message", "")).strip() or "No detail.",
                    }
                )
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                entries.append(
                    {
                        "severity": str(item.get("severity", "info")).strip().lower(),
                        "title": _humanize_rule_id(str(item.get("rule_id", "Issue"))),
                        "text": str(item.get("message", "")).strip() or str(item),
                    }
                )
            else:
                entries.append({"severity": "info", "title": "Status", "text": str(item)})

    if not entries:
        entries.append({"severity": "info", "title": "Status", "text": raw})
    return entries


def _win32_force_foreground(window: QWidget) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(window.winId())
        SW_MAXIMIZE = 3
        SW_SHOWNORMAL = 1
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        is_maximized = bool(window.windowState() & Qt.WindowMaximized)
        user32.ShowWindow(hwnd, SW_MAXIMIZE if is_maximized else SW_SHOWNORMAL)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        return


def _ensure_maximized_foreground(window: QWidget) -> None:
    if window is None:
        return
    state = window.windowState()
    state = (state | Qt.WindowMaximized) & ~Qt.WindowFullScreen & ~Qt.WindowMinimized
    window.setWindowState(state)
    window.showMaximized()
    window.raise_()
    window.activateWindow()
    _win32_force_foreground(window)


def _ensure_normal_foreground(window: QWidget) -> None:
    if window is None:
        return
    state = window.windowState()
    state = state & ~Qt.WindowFullScreen & ~Qt.WindowMinimized & ~Qt.WindowMaximized
    window.setWindowState(state)
    window.showNormal()
    window.raise_()
    window.activateWindow()
    _win32_force_foreground(window)


def _ensure_fullscreen_foreground(window: QWidget) -> None:
    if window is None:
        return
    state = window.windowState()
    state = (state | Qt.WindowFullScreen) & ~Qt.WindowMinimized
    window.setWindowState(state)
    window.showFullScreen()
    window.raise_()
    window.activateWindow()
    _win32_force_foreground(window)


def _center_window(window: QWidget) -> None:
    app = QApplication.instance()
    if app is None:
        return
    screen = window.screen() or app.primaryScreen()
    if screen is None:
        return
    area = screen.availableGeometry()
    frame = window.frameGeometry()
    frame.moveCenter(area.center())
    window.move(frame.topLeft())


class CompatibilityPanel(QGroupBox):
    request_show_details = Signal()

    def __init__(self, title: str = "Compatibility") -> None:
        super().__init__(title)
        root = QVBoxLayout(self)

        counts = QHBoxLayout()
        self.visible_count = QLabel("Visible fields: 0")
        self.locked_count = QLabel("Locked fields: 0")
        self.sweepable_count = QLabel("Sweepable fields: 0")
        counts.addWidget(self.visible_count)
        counts.addWidget(self.locked_count)
        counts.addWidget(self.sweepable_count)
        counts.addStretch(1)
        root.addLayout(counts)

        lists = QHBoxLayout()
        self.visible_list = QListWidget()
        self.visible_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.locked_list = QListWidget()
        self.locked_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.locked_list.setEnabled(False)
        self.locked_list.setToolTip("Locked by runner mode")
        lists.addWidget(self._wrap_list("Visible", self.visible_list), 2)
        lists.addWidget(self._wrap_list("Locked by runner mode", self.locked_list), 1)
        root.addLayout(lists)

        self.summary = QLabel("No issues.")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("IssueHint")
        root.addWidget(self.summary)

        self.show_details_btn = QPushButton("Show details")
        self.show_details_btn.clicked.connect(self.request_show_details.emit)
        root.addWidget(self.show_details_btn, alignment=Qt.AlignLeft)

        self._issues: List[Dict[str, Any]] = []
        self._update_lists([], [], [])

    def _wrap_list(self, label: str, widget: QListWidget) -> QWidget:
        box = QGroupBox(label)
        layout = QVBoxLayout(box)
        layout.addWidget(widget)
        return box

    def _update_lists(self, visible: List[str], locked: List[str], sweepable: List[str]) -> None:
        self.visible_list.clear()
        self.locked_list.clear()
        for key in visible:
            self.visible_list.addItem(QListWidgetItem(key))
        for key in locked:
            item = QListWidgetItem(key)
            item.setToolTip("Locked by runner mode")
            self.locked_list.addItem(item)
        self.visible_count.setText(f"Visible fields: {len(visible)}")
        self.locked_count.setText(f"Locked fields: {len(locked)}")
        self.sweepable_count.setText(f"Sweepable fields: {len(sweepable)}")

    def update_state(self, state: Dict[str, Any]) -> None:
        visible = sorted(str(item) for item in list(state.get("visible_keys", []) or []))
        locked = sorted(str(item) for item in list(state.get("locked_keys", []) or []))
        sweepable = sorted(str(item) for item in list(state.get("sweepable_keys", []) or []))
        issues = [item for item in list(state.get("issues", []) or []) if isinstance(item, dict)]
        self._issues = issues
        self._update_lists(visible, locked, sweepable)

        top = issues[:5]
        if not top:
            self.summary.setText("No validation issues.")
            self.summary.setProperty("severity", "")
            self.show_details_btn.setEnabled(False)
        else:
            lines = []
            for issue in top:
                severity = str(issue.get("severity", "info")).upper()
                rule_id = str(issue.get("rule_id", "unknown_rule"))
                evidence_type = str(issue.get("evidence_type", "hypothesis"))
                message = str(issue.get("message", ""))
                lines.append(f"[{severity}] {rule_id} ({evidence_type}) - {message}")
            self.summary.setText("\n".join(lines))
            self.summary.setProperty("severity", _highest_issue_severity(issues))
            self.show_details_btn.setEnabled(True)
        self.style().unpolish(self.summary)
        self.style().polish(self.summary)

    def issues(self) -> List[Dict[str, Any]]:
        return list(self._issues)


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About WUT Batcher")
        self.setModal(True)
        self.resize(360, 220)

        layout = QVBoxLayout(self)
        logo = QLabel("[ LOGO ]")
        logo.setAlignment(Qt.AlignCenter)
        logo.setObjectName("SectionTitle")
        layout.addWidget(logo)

        version = QLabel("Version: 0.1-rebuild")
        version.setAlignment(Qt.AlignCenter)
        layout.addWidget(version)

        author = QLabel("Entwickelt von Maximilian Heinze")
        author.setAlignment(Qt.AlignCenter)
        layout.addWidget(author)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        apply_windows_dark_titlebar(self)


class StatusDetailDialog(QDialog):
    def __init__(self, detail_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.setWindowTitle("Status")
        self.resize(760, 520)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setProperty("framelessShell", True)
        self._drag_offset: Optional[QPoint] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)
        shell = QFrame()
        shell.setObjectName("FramelessShell")
        outer.addWidget(shell)
        root = QVBoxLayout(shell)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title_bar = QWidget()
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        icon = QLabel("●")
        icon.setObjectName("StatusSymbol")
        title_row.addWidget(icon)
        title = QLabel("Status Details")
        title.setObjectName("SectionTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        close_btn = QPushButton("X")
        close_btn.setObjectName("WindowCloseButton")
        close_btn.setFixedSize(28, 24)
        close_btn.clicked.connect(self.accept)
        title_row.addWidget(close_btn, alignment=Qt.AlignRight)
        root.addWidget(title_bar)
        title_bar.mousePressEvent = self._title_mouse_press  # type: ignore[assignment]
        title_bar.mouseMoveEvent = self._title_mouse_move  # type: ignore[assignment]
        title_bar.mouseReleaseEvent = self._title_mouse_release  # type: ignore[assignment]

        scroll = QListWidget()
        scroll.setSelectionMode(QAbstractItemView.NoSelection)
        entries = _status_entries(detail_text)
        for entry in entries:
            sev = str(entry.get("severity", "info")).lower()
            title_text = str(entry.get("title", "Status"))
            body_text = str(entry.get("text", ""))
            item = QListWidgetItem()
            row_widget = QWidget()
            row_layout = QVBoxLayout(row_widget)
            row_layout.setContentsMargins(10, 8, 10, 8)
            row_layout.setSpacing(4)

            title_label = QLabel(title_text)
            title_label.setObjectName("SectionTitle")
            body_label = QLabel(body_text)
            body_label.setWordWrap(True)
            body_label.setObjectName("IssueHint")
            body_label.setProperty("severity", sev if sev in {"fatal", "warn", "ok"} else "")
            row_layout.addWidget(title_label)
            row_layout.addWidget(body_label)

            item.setSizeHint(row_widget.sizeHint())
            scroll.addItem(item)
            scroll.setItemWidget(item, row_widget)
        root.addWidget(scroll, 1)

    def _title_mouse_press(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.LeftButton:
            return
        self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def _title_mouse_move(self, event) -> None:  # type: ignore[override]
        if self._drag_offset is None:
            return
        if not (event.buttons() & Qt.LeftButton):
            return
        self.move(event.globalPosition().toPoint() - self._drag_offset)
        event.accept()

    def _title_mouse_release(self, event) -> None:  # type: ignore[override]
        self._drag_offset = None
        event.accept()


class BatchRunDefaultsDialog(QDialog):
    def __init__(
        self,
        *,
        missing_keys: List[str],
        default_values: Dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setProperty("framelessShell", True)
        self.setModal(True)
        self.setMinimumSize(560, 360)
        self.resize(620, 420)
        self._drag_offset: Optional[QPoint] = None
        self._decision = "cancel"
        self._missing_keys = [str(item) for item in list(missing_keys or []) if str(item).strip()]
        self._default_values = dict(default_values or {})

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)
        shell = QFrame()
        shell.setObjectName("FramelessShell")
        outer.addWidget(shell)
        root = QVBoxLayout(shell)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(10)

        title_bar = QWidget()
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title = QLabel("Undefined Parameters For Run")
        title.setObjectName("SectionTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        close_btn = QPushButton("X")
        close_btn.setObjectName("WindowCloseButton")
        close_btn.setFixedSize(28, 24)
        close_btn.clicked.connect(self.reject)
        title_row.addWidget(close_btn, alignment=Qt.AlignRight)
        root.addWidget(title_bar)
        title_bar.mousePressEvent = self._title_mouse_press  # type: ignore[assignment]
        title_bar.mouseMoveEvent = self._title_mouse_move  # type: ignore[assignment]
        title_bar.mouseReleaseEvent = self._title_mouse_release  # type: ignore[assignment]

        text = QLabel(
            "The current configuration contains undefined policy-minimal parameters.\n"
            "Do you want to inspect them or use defaults for this run?"
        )
        text.setWordWrap(True)
        text.setObjectName("SummaryText")
        root.addWidget(text)

        list_box = QListWidget()
        list_box.setSelectionMode(QAbstractItemView.NoSelection)
        for key in self._missing_keys[:18]:
            hint = self._default_hint_for_key(key)
            label = f"{key}  ->  {hint}" if hint else key
            list_box.addItem(label)
        if len(self._missing_keys) > 18:
            list_box.addItem(f"... +{len(self._missing_keys) - 18} more")
        root.addWidget(list_box, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        show_btn = QPushButton("Show undefined")
        show_btn.setProperty("segment", "true")
        show_btn.setFixedHeight(32)
        defaults_btn = QPushButton("Use defaults")
        defaults_btn.setObjectName("PrimaryButton")
        defaults_btn.setFixedHeight(32)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(32)
        show_btn.clicked.connect(self._accept_show)
        defaults_btn.clicked.connect(self._accept_defaults)
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(show_btn)
        buttons.addWidget(defaults_btn)
        root.addLayout(buttons)

    def _default_hint_for_key(self, key: str) -> str:
        token = str(key or "").strip()
        if not token:
            return ""
        if token.startswith("R-OSSE."):
            obj = dict(self._default_values.get("R-OSSE", {}) or {})
            return str(obj.get(token.split(".", 1)[1], ""))
        value = self._default_values.get(token)
        if value is None:
            return ""
        if isinstance(value, Mapping):
            return "{...}"
        return str(value)

    def _accept_show(self) -> None:
        self._decision = "show"
        self.accept()

    def _accept_defaults(self) -> None:
        self._decision = "use_defaults"
        self.accept()

    def decision(self) -> str:
        return str(self._decision or "cancel")

    def _title_mouse_press(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.LeftButton:
            return
        self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def _title_mouse_move(self, event) -> None:  # type: ignore[override]
        if self._drag_offset is None:
            return
        if not (event.buttons() & Qt.LeftButton):
            return
        self.move(event.globalPosition().toPoint() - self._drag_offset)
        event.accept()

    def _title_mouse_release(self, event) -> None:  # type: ignore[override]
        self._drag_offset = None
        event.accept()


class SettingsDialog(QDialog):
    settings_saved = Signal(dict)

    def __init__(self, service: OrchestratorService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(560, 260)

        self.library_root = QLineEdit()
        self.ath_exe = QLineEdit()
        self.akabak_exe = QLineEdit()
        self.vacs_exe = QLineEdit()
        self.template_cfg = QLineEdit()
        self.background_automation_mode = QCheckBox("Enable Background Automation Mode")
        self.background_automation_mode.setToolTip(
            "When enabled, the RUN screen stays in front while AKABAK/VACS automation runs in the background."
        )

        form = QFormLayout()
        form.addRow("Library Folder", self.library_root)
        form.addRow("ATH", self.ath_exe)
        form.addRow("AKABAK", self.akabak_exe)
        form.addRow("VACS", self.vacs_exe)
        form.addRow("Template CFG", self.template_cfg)
        form.addRow("Automation", self.background_automation_mode)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(buttons)

        self._load()

    def _load(self) -> None:
        settings = self.service.settings
        self.library_root.setText(settings.library_root)
        self.ath_exe.setText(settings.ath_exe or "")
        self.akabak_exe.setText(settings.akabak_exe or "")
        self.vacs_exe.setText(settings.vacs_exe or "")
        self.template_cfg.setText(settings.template_cfg or "")
        self.background_automation_mode.setChecked(bool(getattr(settings, "background_automation_mode", True)))

    def _save(self) -> None:
        settings = UserSettings(
            library_root=self.library_root.text().strip(),
            ath_exe=self.ath_exe.text().strip() or None,
            akabak_exe=self.akabak_exe.text().strip() or None,
            vacs_exe=self.vacs_exe.text().strip() or None,
            template_cfg=self.template_cfg.text().strip() or None,
            background_automation_mode=bool(self.background_automation_mode.isChecked()),
        )
        result = self.service.save_settings(settings)
        issues = result.get("validation", {})
        self.settings_saved.emit(result)
        if issues:
            detail = "\n".join(f"- {key}: {value}" for key, value in issues.items())
            QMessageBox.warning(self, "Settings saved with warnings", detail)
        self.accept()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        apply_windows_dark_titlebar(self)


class ExportDialog(QDialog):
    def __init__(self, versions_by_batch: Dict[str, List[str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.versions_by_batch = versions_by_batch
        self.setWindowTitle("Export Version")
        self.setModal(True)
        self.resize(420, 220)

        self.batch_combo = QComboBox()
        self.version_combo = QComboBox()
        self.export_stl = QCheckBox("STL")
        self.export_abec = QCheckBox("ABEC")
        self.export_abec.setChecked(True)

        for batch_id in sorted(self.versions_by_batch.keys()):
            self.batch_combo.addItem(batch_id)

        form = QFormLayout()
        form.addRow("Batch", self.batch_combo)
        form.addRow("Version", self.version_combo)
        form.addRow("Export STL", self.export_stl)
        form.addRow("Export ABEC", self.export_abec)

        self.batch_combo.currentTextChanged.connect(self._reload_versions)
        self._reload_versions(self.batch_combo.currentText())

        export_btn = QPushButton("Export")
        export_btn.setObjectName("PrimaryButton")
        export_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(export_btn)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(buttons)

    def _reload_versions(self, batch_id: str) -> None:
        self.version_combo.clear()
        for version_id in self.versions_by_batch.get(batch_id, []):
            self.version_combo.addItem(version_id)

    def payload(self) -> Dict[str, object]:
        return {
            "batch_id": self.batch_combo.currentText().strip(),
            "version_id": self.version_combo.currentText().strip(),
            "export_stl": self.export_stl.isChecked(),
            "export_abec": self.export_abec.isChecked(),
        }

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        apply_windows_dark_titlebar(self)


class RunManagerDialog(QDialog):
    def __init__(self, service: OrchestratorService, project_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.project_id = project_id
        self.setWindowTitle("Runs verwalten")
        self.setModal(True)
        self.resize(760, 420)

        self.batch_filter = QComboBox()
        self.run_list = QListWidget()
        self.run_list.setSelectionMode(QAbstractItemView.SingleSelection)

        refresh_btn = QPushButton("Refresh")
        pin_btn = QPushButton("Pin")
        pin_btn.setToolTip("Markiert einen Run als Ergebnis, das behalten werden soll.")
        unpin_btn = QPushButton("Unpin")
        close_btn = QPushButton("Close")

        top = QFormLayout()
        top.addRow("Batch Filter", self.batch_filter)

        actions = QHBoxLayout()
        actions.addWidget(refresh_btn)
        actions.addWidget(pin_btn)
        actions.addWidget(unpin_btn)
        actions.addStretch(1)
        actions.addWidget(close_btn)

        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self.run_list, 1)
        root.addLayout(actions)

        refresh_btn.clicked.connect(self._reload_runs)
        pin_btn.clicked.connect(self._pin_selected)
        unpin_btn.clicked.connect(self._unpin_selected)
        close_btn.clicked.connect(self.accept)
        self.batch_filter.currentTextChanged.connect(lambda _: self._reload_runs())

        self._reload_batches()
        self._reload_runs()

    def _reload_batches(self) -> None:
        self.batch_filter.clear()
        self.batch_filter.addItem("(all)")
        for batch in self.service.repo.list_batches(self.project_id):
            self.batch_filter.addItem(batch.batch_id)

    def _selected_run_id(self) -> Optional[str]:
        item = self.run_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        return str(value) if value else None

    def _reload_runs(self) -> None:
        self.run_list.clear()
        batch_text = self.batch_filter.currentText().strip()
        batch_id = None if batch_text in {"", "(all)"} else batch_text
        rows = self.service.list_runs(project_id=self.project_id, batch_id=batch_id)
        for row in rows:
            status = str(row.get("status", ""))
            pinned = bool(row.get("pinned", False))
            tag = str(row.get("tag") or "")
            pin_flag = "PINNED" if pinned else "unpinned"
            tag_text = f" [{tag}]" if tag else ""
            label = f"{row['run_id']} | {row['batch_id']} | {status} | {pin_flag}{tag_text}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(row["run_id"]))
            self.run_list.addItem(item)

    def _pin_selected(self) -> None:
        run_id = self._selected_run_id()
        if not run_id:
            return
        tag, ok = QInputDialog.getText(self, "Run pinnen", "Tag (optional):")
        if not ok:
            return
        self.service.pin_run(project_id=self.project_id, run_id=run_id, tag=tag.strip() or None)
        self._reload_runs()

    def _unpin_selected(self) -> None:
        run_id = self._selected_run_id()
        if not run_id:
            return
        self.service.unpin_run(project_id=self.project_id, run_id=run_id)
        self._reload_runs()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        apply_windows_dark_titlebar(self)


class CleanupTestDataDialog(QDialog):
    def __init__(self, service: OrchestratorService, project_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.project_id = project_id
        self.setWindowTitle("Testdaten aufraeumen")
        self.setModal(True)
        self.resize(760, 500)
        self._last_preview: Dict[str, Any] = {}

        info = QLabel("Behalten: angeheftete Runs. Loeschen: alle anderen Runs (Testdaten).")
        info.setWordWrap(True)

        self.delete_exports = QCheckBox("Exportdateien ebenfalls loeschen (empfohlen)")
        self.delete_exports.setChecked(True)

        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)

        self.confirm_input = QLineEdit()
        self.confirm_input.setPlaceholderText("Type DELETE to confirm")

        preview_btn = QPushButton("Preview")
        cleanup_btn = QPushButton("Cleanup")
        cleanup_btn.setObjectName("PrimaryButton")
        cancel_btn = QPushButton("Cancel")

        actions = QHBoxLayout()
        actions.addWidget(preview_btn)
        actions.addWidget(cleanup_btn)
        actions.addStretch(1)
        actions.addWidget(cancel_btn)

        root = QVBoxLayout(self)
        root.addWidget(info)
        root.addWidget(self.delete_exports)
        root.addWidget(self.preview_text, 1)
        root.addWidget(QLabel("Confirmation"))
        root.addWidget(self.confirm_input)
        root.addLayout(actions)

        preview_btn.clicked.connect(self._preview)
        cleanup_btn.clicked.connect(self._cleanup)
        cancel_btn.clicked.connect(self.reject)
        self._preview()

    def _preview(self) -> None:
        result = self.service.cleanup_test_data(
            project_id=self.project_id,
            delete_exports=self.delete_exports.isChecked(),
            dry_run=True,
        )
        self._last_preview = result
        run_ids = list(result.get("run_ids", []))
        counts = dict(result.get("counts", {}) or {})
        lines = [
            f"Project: {self.project_id}",
            f"Runs to delete: {len(run_ids)}",
            f"Counts: {json.dumps(counts, ensure_ascii=False)}",
            "",
            "Run IDs:",
            *[f"- {run_id}" for run_id in run_ids],
        ]
        self.preview_text.setPlainText("\n".join(lines))

    def _cleanup(self) -> None:
        if self.confirm_input.text().strip() != "DELETE":
            QMessageBox.warning(self, "Confirmation required", 'Type "DELETE" to continue.')
            return
        result = self.service.cleanup_test_data(
            project_id=self.project_id,
            delete_exports=self.delete_exports.isChecked(),
            dry_run=False,
        )
        self._last_preview = result
        QMessageBox.information(
            self,
            "Cleanup finished",
            f"Deleted runs: {len(list(result.get('run_ids', [])))}\nAudit: {result.get('audit_log')}",
        )
        self.accept()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        apply_windows_dark_titlebar(self)


class ConstraintSummaryGrid(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectSummaryPanel")
        self._entries: List[tuple[str, str]] = []
        self._last_render_cols: Optional[int] = None
        self._last_render_signature: Optional[tuple[tuple[str, str], ...]] = None
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        title = QLabel("Project Constraints")
        title.setObjectName("SummaryTitle")
        root.addWidget(title)
        self._grid_wrap = QWidget()
        self._grid = QGridLayout(self._grid_wrap)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        root.addWidget(self._grid_wrap)
        self._empty = QLabel("No project loaded.")
        self._empty.setObjectName("SummaryText")
        root.addWidget(self._empty)
        self._clear_grid()

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.4g}"
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    def set_constraints_payload(self, payload: Optional[Dict[str, Any]]) -> None:
        raw = dict(payload or {})
        entries: List[tuple[str, str]] = []
        for key, value in sorted(dict(raw.get("fixed_params", {}) or {}).items()):
            entries.append((str(key), self._format_value(value)))
        for key, value in sorted(dict(raw.get("limits", {}) or {}).items()):
            entries.append((f"{key} (limit)", self._format_value(value)))
        for row in list(raw.get("param_states", []) or []):
            if not isinstance(row, dict):
                continue
            if not bool(row.get("is_set")):
                continue
            key = str(row.get("param_name", "")).strip()
            if not key:
                continue
            if any(item[0] == key for item in entries):
                continue
            entries.append((key, self._format_value(row.get("value"))))
        self._entries = entries
        self._rebuild_grid()

    def _target_cols(self) -> int:
        width = max(int(self.width()), 1)
        return 1 if width < 620 else (2 if width < 980 else 3)

    def _rebuild_grid(self) -> None:
        signature = tuple((str(key), str(value)) for key, value in list(self._entries))
        cols = self._target_cols()
        if self._last_render_cols == cols and self._last_render_signature == signature:
            return
        self._last_render_cols = cols
        self._last_render_signature = signature
        self.setUpdatesEnabled(False)
        self._clear_grid()
        if not self._entries:
            self._empty.setVisible(True)
            self.setUpdatesEnabled(True)
            return
        self._empty.setVisible(False)
        for index, (key, value) in enumerate(list(self._entries)):
            row = index // cols
            col = index % cols
            card = QFrame()
            card.setObjectName("ConstraintCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 8, 8, 8)
            card_layout.setSpacing(2)
            k = QLabel(str(key))
            k.setObjectName("SummaryMeta")
            v = QLabel(str(value))
            v.setObjectName("SummaryText")
            v.setWordWrap(True)
            card_layout.addWidget(k)
            card_layout.addWidget(v)
            self._grid.addWidget(card, row, col)
        for col in range(cols):
            self._grid.setColumnStretch(col, 1)
        self.setUpdatesEnabled(True)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._rebuild_grid()


class DashboardPage(QWidget):
    request_new_batch = Signal()
    request_edit_batch = Signal(str)
    request_clone_batch = Signal(str)
    request_open_export_dialog = Signal()
    request_manage_runs = Signal()
    request_cleanup_testdata = Signal()
    request_settings = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 14)
        root.setSpacing(10)

        title = QLabel("DASHBOARD")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        self.constraints_summary = ConstraintSummaryGrid()
        root.addWidget(self.constraints_summary)

        batch_card = QFrame()
        batch_card.setObjectName("ProjectSummaryPanel")
        batch_layout = QVBoxLayout(batch_card)
        batch_layout.setContentsMargins(10, 8, 10, 10)
        batch_layout.setSpacing(8)
        batch_title = QLabel("Batches")
        batch_title.setObjectName("SummaryTitle")
        batch_layout.addWidget(batch_title)
        self.batch_list = QListWidget()
        self.batch_list.setObjectName("DashboardBatchList")
        batch_layout.addWidget(self.batch_list, 1)
        root.addWidget(batch_card, 1)

        actions_shell = QFrame()
        actions_shell.setObjectName("BatchActionBar")
        actions_shell.setFixedHeight(56)
        actions = QHBoxLayout(actions_shell)
        actions.setContentsMargins(10, 8, 10, 8)
        actions.setSpacing(10)
        self.new_batch_btn = QPushButton("New Batch")
        self.new_batch_btn.setObjectName("BatchSecondaryButton")
        self.edit_batch_btn = QPushButton("Edit Batch")
        self.edit_batch_btn.setObjectName("BatchSecondaryButton")
        self.clone_batch_btn = QPushButton("Clone Batch")
        self.clone_batch_btn.setObjectName("BatchSecondaryButton")
        actions.addWidget(self.new_batch_btn)
        actions.addWidget(self.edit_batch_btn)
        actions.addWidget(self.clone_batch_btn)
        actions.addStretch(1)
        root.addWidget(actions_shell)

        export_box = QFrame()
        export_box.setObjectName("ProjectSummaryPanel")
        export_root = QVBoxLayout(export_box)
        export_root.setContentsMargins(10, 8, 10, 10)
        export_root.setSpacing(8)
        export_title = QLabel("Export")
        export_title.setObjectName("SummaryTitle")
        export_root.addWidget(export_title)
        export_grid = QGridLayout()
        export_grid.setContentsMargins(0, 0, 0, 0)
        export_grid.setHorizontalSpacing(8)
        export_grid.setVerticalSpacing(8)
        self.export_btn = QPushButton("Open Export Dialog")
        self.export_btn.setObjectName("BatchPrimaryButton")
        self.manage_runs_btn = QPushButton("Runs verwalten...")
        self.manage_runs_btn.setObjectName("BatchSecondaryButton")
        self.cleanup_testdata_btn = QPushButton("Testdaten aufraeumen...")
        self.cleanup_testdata_btn.setObjectName("BatchSecondaryButton")
        export_grid.addWidget(self.export_btn, 0, 0)
        export_grid.addWidget(self.manage_runs_btn, 0, 1)
        export_grid.addWidget(self.cleanup_testdata_btn, 0, 2)
        export_root.addLayout(export_grid)
        root.addWidget(export_box)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setObjectName("BatchGhostButton")
        footer.addWidget(self.settings_btn)
        root.addLayout(footer)

        self.new_batch_btn.clicked.connect(self.request_new_batch.emit)
        self.edit_batch_btn.clicked.connect(self._emit_edit)
        self.clone_batch_btn.clicked.connect(self._emit_clone)
        self.export_btn.clicked.connect(self.request_open_export_dialog.emit)
        self.manage_runs_btn.clicked.connect(self.request_manage_runs.emit)
        self.cleanup_testdata_btn.clicked.connect(self.request_cleanup_testdata.emit)
        self.settings_btn.clicked.connect(self.request_settings.emit)

    def set_constraints_payload(self, payload: Optional[Dict[str, Any]]) -> None:
        self.constraints_summary.set_constraints_payload(payload)

    def _selected_batch_id(self) -> Optional[str]:
        item = self.batch_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.UserRole)
        return str(data) if data else None

    def _emit_edit(self) -> None:
        batch_id = self._selected_batch_id()
        if batch_id:
            self.request_edit_batch.emit(batch_id)

    def _emit_clone(self) -> None:
        batch_id = self._selected_batch_id()
        if batch_id:
            self.request_clone_batch.emit(batch_id)

class ProjectIssuesPanel(QFrame):
    issue_selected = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        popup: bool = False,
        show_header: bool = True,
    ) -> None:
        _ = popup
        super().__init__(parent)
        self.setObjectName("ProjectIssuesPanel")
        self.setMinimumWidth(0)
        self.setMinimumHeight(96)
        self._show_header = bool(show_header)
        self._compact_counts = "E0 W0 I0"
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        if self._show_header:
            header = QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(6)
            self.counts = QLabel("Errors: 0 · Warnings: 0 · Incomplete: 0")
            self.counts.setObjectName("IssuesPanelCounts")
            header.addWidget(self.counts, 0, Qt.AlignLeft | Qt.AlignVCenter)
            header.addStretch(1)
            root.addLayout(header)
        else:
            self.counts = QLabel("")
            self.counts.setVisible(False)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setMinimumHeight(66)
        self._container = QWidget()
        self._rows = QVBoxLayout(self._container)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(5)
        self._scroll.setWidget(self._container)
        root.addWidget(self._scroll)

    def _clear_rows(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_issues(self, issues: List[UiProjectIssue]) -> None:
        self._clear_rows()
        counts = issue_counts(issues)
        fatal_count = int(counts.get("error", 0))
        warn_count = int(counts.get("warn", 0))
        incomplete_count = int(counts.get("incomplete", 0))
        self._compact_counts = f"E{fatal_count} W{warn_count} I{incomplete_count}"
        if self._show_header:
            self.counts.setText(
                f"Errors: {fatal_count} · "
                f"Warnings: {warn_count} · "
                f"Incomplete: {incomplete_count}"
            )

        groups: Dict[str, List[UiProjectIssue]] = {"error": [], "warn": [], "incomplete": []}
        for issue in issues:
            groups.setdefault(issue.severity, []).append(issue)

        labels = {
            "error": "Errors",
            "warn": "Warnings",
            "incomplete": "Incomplete",
        }
        for severity in ("error", "warn", "incomplete"):
            rows = groups.get(severity, [])
            if not rows:
                continue
            section_label = QLabel(f"{labels[severity]} ({len(rows)})")
            section_label.setObjectName("IssuesPanelGroupTitle")
            section_label.setProperty("severity", severity)
            self._rows.addWidget(section_label)
            for issue in rows:
                badge = {"error": "[E]", "warn": "[W]", "incomplete": "[I]"}.get(severity, "[I]")
                button = IssueRowButton(
                    f"{badge}  {issue.field_label}: {issue.message}  [{issue.section}]"
                )
                button.setObjectName("IssueRowButton")
                button.setProperty("severity", severity)
                button.setCursor(Qt.PointingHandCursor)
                button.setFlat(True)
                button.setToolTip(f"{issue.field_label}: {issue.message}")
                button.clicked.connect(lambda _checked=False, key=issue.key: self.issue_selected.emit(str(key)))
                self._rows.addWidget(button)
        if self._rows.count() == 0:
            empty = QLabel("No open issues.")
            empty.setObjectName("IssuesPanelEmpty")
            self._rows.addWidget(empty)
        self._rows.addStretch(1)

    def show_for(self, anchor: QWidget) -> None:
        _ = anchor
        self.setVisible(True)

    def compact_counts(self) -> str:
        return self._compact_counts


class IssuesSubsectionHeader(QToolButton):
    toggled_request = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SummaryIssuesHeaderButton")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setProperty("severity", "ok")
        self.setMinimumHeight(30)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setArrowType(Qt.LeftArrow)
        self.setText("Issues")
        self.setEnabled(False)
        self.clicked.connect(lambda _checked=False: self.toggled_request.emit())
        self._issue_total = 0

    def set_issue_total(self, total: int) -> None:
        self._issue_total = max(int(total), 0)
        if self._issue_total > 0:
            self.setText(f"Issues ({self._issue_total})")
            self.setEnabled(True)
        else:
            self.setText("Issues")
            self.setEnabled(False)

    def set_expanded(self, expanded: bool) -> None:
        self.setArrowType(Qt.RightArrow if expanded else Qt.LeftArrow)
        self.setProperty("expanded", "true" if expanded else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_severity(self, level: str) -> None:
        self.setProperty("severity", str(level or "ok"))
        self.style().unpolish(self)
        self.style().polish(self)

class SummaryIssuesSection(QFrame):
    issue_selected = Signal(str)
    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SummaryIssuesSection")
        self._expanded = False
        self._target_body_width = 320
        self._target_body_height = 84
        self._collapsed_width = 96

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.body = QFrame(self)
        self.body.setObjectName("SummaryIssuesBody")
        self.body.installEventFilter(self)
        self.body.setMinimumHeight(self._target_body_height)
        self.body.setMaximumWidth(0)
        self.body.setMaximumHeight(0)
        self.body.setVisible(False)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.panel = ProjectIssuesPanel(self, popup=False, show_header=True)
        self.panel.setVisible(False)
        self.panel_effect = QGraphicsOpacityEffect(self.panel)
        self.panel_effect.setOpacity(0.0)
        self.panel.setGraphicsEffect(self.panel_effect)
        body_layout.addWidget(self.panel)
        root.addWidget(self.body, 1)

        self.header = IssuesSubsectionHeader(self)
        self.header.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        root.addWidget(self.header, 0)
        self._refresh_collapsed_width()

        self._width_anim = QPropertyAnimation(self.body, b"maximumWidth", self)
        self._width_anim.setDuration(200)
        self._width_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._opacity_anim = QPropertyAnimation(self.panel_effect, b"opacity", self)
        self._opacity_anim.setDuration(180)
        self._opacity_anim.setEasingCurve(QEasingCurve.InOutCubic)

        self.panel.issue_selected.connect(self.issue_selected.emit)

    def _refresh_collapsed_width(self) -> None:
        self._collapsed_width = max(int(self.header.sizeHint().width()) + 4, 78)
        self.header.setFixedWidth(self._collapsed_width)

    def collapsed_width(self) -> int:
        return self._collapsed_width

    def set_issues(self, issues: List[UiProjectIssue]) -> None:
        self.panel.set_issues(issues)
        counts = issue_counts(issues)
        fatal_count = int(counts.get("error", 0))
        warn_count = int(counts.get("warn", 0))
        incomplete_count = int(counts.get("incomplete", 0))
        self.header.set_issue_total(fatal_count + warn_count + incomplete_count)
        self._refresh_collapsed_width()
        if fatal_count > 0:
            self.header.set_severity("fatal")
        elif warn_count > 0:
            self.header.set_severity("warn")
        elif incomplete_count > 0:
            self.header.set_severity("incomplete")
        else:
            self.header.set_severity("ok")

    def set_body_target_size(self, width: int, height: int) -> None:
        self._target_body_width = max(int(width), 220)
        self._target_body_height = max(int(height), 36)
        self.body.setMinimumHeight(self._target_body_height)
        self.body.setMaximumHeight(self._target_body_height)
        if self._expanded:
            self.body.setMaximumWidth(self._target_body_width)
        else:
            self.body.setMaximumWidth(0)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded, animated=True)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool, *, animated: bool) -> None:
        target = bool(expanded)
        if target == self._expanded:
            return
        self._expanded = target
        self.header.set_expanded(target)
        self.toggled.emit(target)
        self._width_anim.stop()
        self._opacity_anim.stop()
        if target:
            self.body.setVisible(True)
            self.panel.setVisible(True)

        start_width = int(self.body.maximumWidth())
        end_width = self._target_body_width if target else 0
        start_opacity = float(self.panel_effect.opacity())
        end_opacity = 1.0 if target else 0.0

        if animated:
            self._width_anim.setStartValue(start_width)
            self._width_anim.setEndValue(end_width)
            self._opacity_anim.setStartValue(start_opacity)
            self._opacity_anim.setEndValue(end_opacity)
            self._width_anim.start()
            self._opacity_anim.start()
            if not target:
                QTimer.singleShot(200, lambda: self.panel.setVisible(False))
                QTimer.singleShot(200, lambda: self.body.setVisible(False))
        else:
            self.body.setMaximumWidth(end_width)
            self.panel_effect.setOpacity(end_opacity)
            self.panel.setVisible(target)
            self.body.setVisible(target)

    def eventFilter(self, watched: QObject, event) -> bool:  # type: ignore[override]
        if watched is self.body and self._expanded and event.type() == QEvent.MouseButtonPress:
            target = self.body.childAt(event.pos())
            if target is None or target.objectName() != "IssueRowButton":
                self.set_expanded(False, animated=True)
                return False
        return super().eventFilter(watched, event)

class ProjectPage(QWidget):
    submit_project = Signal(str, dict)
    draft_changed = Signal(dict)
    blocked_interaction = Signal(str, str, str)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 14)
        root.setSpacing(8)
        title = QLabel("PROJECT")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        root.addSpacing(4)

        form_column_width = (2 * FORM_METRICS.label_width) + (2 * FORM_METRICS.input_width) + FORM_METRICS.column_gap + 32
        self._form_column_width = form_column_width

        name_wrap = QWidget()
        name_row = QHBoxLayout(name_wrap)
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(10)
        left_col = QWidget()
        left_col.setMinimumWidth(form_column_width)
        left_col.setMaximumWidth(form_column_width)
        left_col_layout = QHBoxLayout(left_col)
        left_col_layout.setContentsMargins(0, 0, 0, 0)
        left_col_layout.setSpacing(0)
        self.project_name = QLineEdit()
        self.project_name.setPlaceholderText("Project Name")
        self.project_name.setToolTip("Project Name")
        self.project_name.setMinimumWidth(form_column_width)
        left_col_layout.addWidget(self.project_name)
        name_row.addWidget(left_col, 0, Qt.AlignTop)
        name_row.addStretch(1)
        root.addWidget(name_wrap)
        root.addSpacing(2)

        self.summary_panel = QFrame()
        self.summary_panel.setObjectName("ProjectSummaryPanel")
        self.summary_panel.setFixedHeight(108)
        summary_layout = QHBoxLayout(self.summary_panel)
        summary_layout.setContentsMargins(12, 6, 12, 6)
        summary_layout.setSpacing(10)
        self.summary_left = QWidget()
        summary_left_layout = QVBoxLayout(self.summary_left)
        summary_left_layout.setContentsMargins(0, 0, 0, 0)
        summary_left_layout.setSpacing(2)

        summary_head = QHBoxLayout()
        summary_head.setContentsMargins(0, 0, 0, 0)
        summary_head.setSpacing(6)
        summary_title = QLabel("Project constraints (locked after creation)")
        summary_title.setObjectName("SummaryTitle")
        summary_head.addWidget(summary_title)
        summary_head.addStretch(1)
        summary_left_layout.addLayout(summary_head)
        self.summary_line_1 = QLabel(
            "Everything you set here becomes fixed for the project and cannot be changed in Batch runs."
        )
        self.summary_line_1.setObjectName("SummaryText")
        self.summary_line_1.setWordWrap(False)
        summary_left_layout.addWidget(self.summary_line_1)
        summary_left_layout.addStretch(1)
        self.summary_chips_wrap = QWidget()
        self.summary_chips_layout = QHBoxLayout(self.summary_chips_wrap)
        self.summary_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.summary_chips_layout.setSpacing(6)
        summary_left_layout.addWidget(self.summary_chips_wrap)
        summary_layout.addWidget(self.summary_left, 1)

        self.summary_right = QWidget()
        self.summary_right.setObjectName("SummaryIssuesDock")
        summary_right_layout = QVBoxLayout(self.summary_right)
        summary_right_layout.setContentsMargins(10, 8, 10, 8)
        summary_right_layout.setSpacing(2)
        self.summary_issue_title = QLabel("Validation")
        self.summary_issue_title.setObjectName("SummaryTitle")
        summary_right_layout.addWidget(self.summary_issue_title)
        self.summary_issue_hint = QLabel("No validation issues.")
        self.summary_issue_hint.setObjectName("IssueHint")
        self.summary_issue_hint.setWordWrap(True)
        summary_right_layout.addWidget(self.summary_issue_hint)
        summary_right_layout.addStretch(1)
        self.summary_right.setMinimumWidth(320)
        self.summary_right.setMaximumWidth(420)
        summary_layout.addWidget(self.summary_right, 0)
        root.addWidget(self.summary_panel)
        root.addSpacing(2)

        self.constraints_form = ParameterForm(build_project_form_schema())
        root.addWidget(self.constraints_form, 1)

        self.action_bar = QFrame()
        self.action_bar.setObjectName("ProjectActionBar")
        self.action_bar.setFixedHeight(58)
        action_layout = QHBoxLayout(self.action_bar)
        action_layout.setContentsMargins(10, 8, 10, 8)
        action_layout.setSpacing(10)

        self.action_status_pill = QLabel("Ready to create project.")
        self.action_status_pill.setObjectName("ProjectStatusPill")
        self.action_status_pill.setProperty("severity", "ok")
        action_layout.addWidget(self.action_status_pill, 0, Qt.AlignVCenter)

        self.action_counts = QLabel("0 errors · 0 warnings · 0 incomplete")
        self.action_counts.setObjectName("ProjectStatusHint")
        action_layout.addWidget(self.action_counts, 0, Qt.AlignVCenter)

        self.action_status_hint = QLabel("")
        self.action_status_hint.setObjectName("ProjectStatusHint")
        action_layout.addWidget(self.action_status_hint, 0, Qt.AlignVCenter)

        action_layout.addStretch(1)

        self.create_btn = QPushButton("Create Project")
        self.create_btn.setObjectName("BatchPrimaryButton")
        action_layout.addWidget(self.create_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        root.addWidget(self.action_bar)

        self.create_btn.clicked.connect(self._submit)
        self.constraints_form.changed.connect(self._emit_draft_changed)
        self.constraints_form.blocked_interaction.connect(self.blocked_interaction.emit)

        self._compat_state: Dict[str, Any] = {"visible_keys": [], "locked_keys": [], "sweepable_keys": [], "issues": []}
        self._latest_field_issues: List[Dict[str, Any]] = []
        self._ui_issues: List[UiProjectIssue] = []
        self._validation_phase = "idle"
        self._creating_project = False
        self._constraints_locked = False
        self._update_action_state()
        self._update_summary_panel()
        self._update_issues_panel()

    def _emit_draft_changed(self, payload: Dict[str, Any] | None = None) -> None:
        self.draft_changed.emit(payload or self._raw_constraints_payload())

    def _raw_constraints_payload(self) -> Dict[str, Any]:
        return self.constraints_form.payload()

    def apply_compatibility(self, state: Dict[str, Any]) -> None:
        self._compat_state = dict(state)
        self.constraints_form.apply_compatibility(state)

    def apply_ui_risks(self, issues: List[Dict[str, Any]]) -> None:
        raw_issues = [item for item in issues if isinstance(item, dict)]
        field_is_set = self.constraints_form.field_is_set_map()
        field_labels = self.constraints_form.field_label_map()
        field_sections = self.constraints_form.field_section_map()

        mapped: List[Dict[str, Any]] = []
        for issue in raw_issues:
            key = str(issue.get("field_key") or issue.get("key") or "").strip()
            severity = classify_ui_severity(issue, field_is_set=bool(field_is_set.get(key, False)))
            normalized = dict(issue)
            if severity == "error":
                normalized["severity"] = "fatal"
            elif severity == "warn":
                normalized["severity"] = "warn"
            elif severity == "incomplete":
                normalized["severity"] = "incomplete"
            mapped.append(normalized)

        self._latest_field_issues = mapped
        self._ui_issues = normalize_project_issues(
            raw_issues,
            field_is_set=field_is_set,
            field_labels=field_labels,
            field_sections=field_sections,
        )
        self.constraints_form.apply_ui_risks(mapped)
        if self._validation_phase == "validating":
            self._validation_phase = "idle"
        self._update_action_state()
        self._update_summary_panel()
        self._update_issues_panel()

    def set_validation_phase(self, phase: str) -> None:
        self._validation_phase = str(phase or "idle").strip().lower()
        self._update_action_state()

    def set_creating(self, creating: bool) -> None:
        self._creating_project = bool(creating)
        if creating:
            self._constraints_locked = False
        self._update_action_state()

    def set_constraints_locked(self, locked: bool) -> None:
        self._constraints_locked = bool(locked)
        if locked:
            self._creating_project = False
            self._validation_phase = "idle"
        self._update_action_state()
        self._update_summary_panel()
        self._update_issues_panel()

    def _issue_counts(self) -> Dict[str, int]:
        raw = issue_counts(self._ui_issues)
        return {
            "fatal": int(raw.get("error", 0)),
            "warn": int(raw.get("warn", 0)),
            "incomplete": int(raw.get("incomplete", 0)),
        }

    @staticmethod
    def _mode_label(mapping: Dict[int, str], value: Any, *, fallback: str) -> str:
        try:
            key = int(value)
        except Exception:
            return fallback
        return mapping.get(key, fallback)

    def _mode_chips(self, payload: Dict[str, Any]) -> List[str]:
        state_by_key: Dict[str, Dict[str, Any]] = {}
        for row in list(payload.get("param_states", []) or []):
            if not isinstance(row, dict):
                continue
            key = str(row.get("param_name", "")).strip()
            if not key:
                continue
            state_by_key[key] = {"is_set": bool(row.get("is_set")), "value": row.get("value")}

        def get_value(key: str) -> Any:
            row = state_by_key.get(key, {})
            if bool(row.get("is_set")):
                return row.get("value")
            return None

        throat_value = get_value("Throat.Profile")
        gcurve_value = get_value("GCurve.Type")
        morph_value = get_value("Morph.TargetShape")
        enclosure_enabled = bool(state_by_key.get("Mesh.Enclosure", {}).get("is_set", False))
        chips = [
            f"Throat: {self._mode_label({1: 'OS-SE', 2: 'R-OSSE', 3: 'Circular Arc'}, throat_value, fallback='unset')}",
            f"Morph: {self._mode_label({0: 'no morph', 1: 'rectangle', 2: 'circle'}, morph_value if morph_value is not None else 0, fallback='no morph')}",
            f"GCurve: {self._mode_label({0: 'no GCurve', 1: 'Superellipse', 2: 'Superformula'}, gcurve_value if gcurve_value is not None else 0, fallback='no GCurve')}",
            f"Enclosure: {'enabled' if enclosure_enabled else 'disabled'}",
        ]
        return chips

    def _set_summary_chips(self, chips: List[str]) -> None:
        while self.summary_chips_layout.count():
            item = self.summary_chips_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        max_visible = 4
        visible = chips[:max_visible]
        for chip_text in visible:
            chip = QLabel(chip_text)
            chip.setObjectName("SummaryChip")
            self.summary_chips_layout.addWidget(chip, 0, Qt.AlignVCenter)
        remaining = max(0, len(chips) - max_visible)
        if remaining > 0:
            more_chip = QLabel(f"+{remaining}")
            more_chip.setObjectName("SummaryChip")
            self.summary_chips_layout.addWidget(more_chip, 0, Qt.AlignVCenter)
        self.summary_chips_layout.addStretch(1)

    def _update_summary_panel(self) -> None:
        payload = self._raw_constraints_payload()
        self._set_summary_chips(self._mode_chips(payload))

    def _update_issues_panel(self) -> None:
        counts = issue_counts(self._ui_issues)
        fatal = int(counts.get("error", 0))
        warn = int(counts.get("warn", 0))
        incomplete = int(counts.get("incomplete", 0))
        if self._ui_issues:
            top = self._ui_issues[0]
            teaser = str(top.message or "").strip()
            if len(teaser) > 132:
                teaser = f"{teaser[:129].rstrip()}..."
        else:
            teaser = "No validation issues."
        if fatal > 0:
            self.summary_issue_hint.setProperty("severity", "fatal")
            self.summary_issue_hint.setText(teaser or f"{fatal} fatal issue(s).")
        elif warn > 0:
            self.summary_issue_hint.setProperty("severity", "warn")
            self.summary_issue_hint.setText(teaser or f"{warn} warning(s).")
        elif incomplete > 0:
            self.summary_issue_hint.setProperty("severity", "")
            self.summary_issue_hint.setText("Configuration incomplete. Fill required values when ready.")
        else:
            self.summary_issue_hint.setProperty("severity", "")
            self.summary_issue_hint.setText("No validation issues.")
        self.summary_issue_hint.style().unpolish(self.summary_issue_hint)
        self.summary_issue_hint.style().polish(self.summary_issue_hint)

    def _update_action_state(self) -> None:
        counts = self._issue_counts()
        fatal = int(counts.get("fatal", 0))
        warn = int(counts.get("warn", 0))
        incomplete = int(counts.get("incomplete", 0))

        if self._creating_project:
            text = "Creating project..."
            severity = "progress"
            hint = ""
        elif self._constraints_locked:
            text = "Constraints locked for this project"
            severity = "ok"
            hint = ""
        elif self._validation_phase == "validating":
            text = "Checking constraints..."
            severity = "progress"
            hint = ""
        elif fatal > 0:
            text = "Resolve errors to continue."
            severity = "fatal"
            hint = "Resolve errors to proceed."
        elif incomplete > 0 and warn > 0:
            text = "Configuration incomplete. You can create the project, but review warnings."
            severity = "warn"
            hint = "Missing required values are shown as incomplete."
        elif incomplete > 0:
            text = "Configuration incomplete. You can create the project and complete values later."
            severity = "neutral"
            hint = ""
        elif warn > 0:
            text = "Warnings present — you can continue, but review them."
            severity = "warn"
            hint = "You can continue, but results may be unstable."
        else:
            text = "Ready to create project."
            severity = "ok"
            hint = ""

        self.action_status_pill.setText(text)
        self.action_status_pill.setProperty("severity", severity)
        self.action_status_pill.style().unpolish(self.action_status_pill)
        self.action_status_pill.style().polish(self.action_status_pill)
        self.action_status_hint.setText(hint)
        self.action_status_hint.setVisible(bool(hint))
        self.action_counts.setText(f"{fatal} errors · {warn} warnings · {incomplete} incomplete")

        enabled = (fatal == 0) and (not self._creating_project)
        self.create_btn.setEnabled(enabled)
        if not enabled and fatal > 0:
            self.create_btn.setToolTip("Resolve errors before creating the project.")
        else:
            self.create_btn.setToolTip("")

    def _toggle_issues_panel(self) -> None:
        return

    def _focus_issue_key(self, key: str) -> None:
        self.constraints_form.focus_issue_key(str(key))
        self._update_action_state()

    def _set_issues_open(self, open_state: bool, *, animated: bool) -> None:
        _ = (open_state, animated)
        return

    def _summary_issues_dimensions(self) -> tuple[int, int, int]:
        return (320, 420, 96)

    def _sync_summary_issues_geometry(self) -> None:
        return

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)

    def _submit(self) -> None:
        if not self.create_btn.isEnabled():
            return
        payload = self._raw_constraints_payload()
        self.submit_project.emit(self.project_name.text().strip(), payload)


class BatchPage(QWidget):
    save_batch = Signal(dict)
    run_batch = Signal(dict)
    back_to_dashboard = Signal()
    open_project_manager = Signal()
    draft_changed = Signal(dict)
    blocked_interaction = Signal(str, str, str)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 14)
        root.setSpacing(10)
        self._root_layout = root

        title = QLabel("BATCH")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        name_row = QWidget()
        name_layout = QHBoxLayout(name_row)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(10)
        self.batch_name = QLineEdit()
        self.batch_name.setPlaceholderText("Batch Name")
        name_layout.addWidget(self.batch_name, 0, Qt.AlignLeft)
        name_layout.addStretch(1)
        root.addWidget(name_row)

        summary_strip = QWidget()
        summary_strip_layout = QHBoxLayout(summary_strip)
        summary_strip_layout.setContentsMargins(0, 0, 0, 0)
        summary_strip_layout.setSpacing(10)

        self.summary_left_card = QFrame()
        self.summary_left_card.setObjectName("ProjectSummaryPanel")
        self.summary_left_card.setFixedHeight(126)
        left_card_layout = QVBoxLayout(self.summary_left_card)
        left_card_layout.setContentsMargins(10, 8, 10, 8)
        left_card_layout.setSpacing(2)
        summary_title = QLabel("Batch Draft")
        summary_title.setObjectName("SummaryTitle")
        left_card_layout.addWidget(summary_title)
        self.summary_line_1 = QLabel("Define base values, activate sweeps, and configure exports.")
        self.summary_line_1.setObjectName("SummaryText")
        self.summary_line_1.setWordWrap(True)
        left_card_layout.addWidget(self.summary_line_1)
        self.summary_line_2 = QLabel("Dynamic compatibility hiding prevents conflicting fatal combinations.")
        self.summary_line_2.setObjectName("SummaryText")
        self.summary_line_2.setWordWrap(True)
        left_card_layout.addWidget(self.summary_line_2)
        left_card_layout.addStretch(1)
        summary_strip_layout.addWidget(self.summary_left_card, 1)

        self.summary_center_card = QFrame()
        self.summary_center_card.setObjectName("ProjectSummaryPanel")
        self.summary_center_card.setFixedHeight(126)
        center_card_layout = QVBoxLayout(self.summary_center_card)
        center_card_layout.setContentsMargins(10, 8, 10, 8)
        center_card_layout.setSpacing(2)
        summary_center_title = QLabel("Estimate")
        summary_center_title.setObjectName("SummaryTitle")
        center_card_layout.addWidget(summary_center_title)
        self.summary_meta_versions = QLabel("Version preview: 0 · Export specs: 0 · Mode: single")
        self.summary_meta_versions.setObjectName("SummaryMeta")
        center_card_layout.addWidget(self.summary_meta_versions)
        self.summary_meta_counts = QLabel("Visible variable params: 0 · Active sweeps: 0")
        self.summary_meta_counts.setObjectName("SummaryMeta")
        center_card_layout.addWidget(self.summary_meta_counts)
        self.summary_defined_vars = QLabel("Defined variables: 0")
        self.summary_defined_vars.setObjectName("SummaryMeta")
        center_card_layout.addWidget(self.summary_defined_vars)
        self.summary_eta_label = QLabel("ETA: unknown")
        self.summary_eta_label.setObjectName("SummaryMeta")
        center_card_layout.addWidget(self.summary_eta_label)
        center_card_layout.addStretch(1)
        summary_strip_layout.addWidget(self.summary_center_card, 1)

        self.summary_right_card = QFrame()
        self.summary_right_card.setObjectName("ProjectSummaryPanel")
        self.summary_right_card.setFixedHeight(126)
        right_card_layout = QVBoxLayout(self.summary_right_card)
        right_card_layout.setContentsMargins(10, 8, 10, 8)
        right_card_layout.setSpacing(2)
        summary_issue_title = QLabel("Validation")
        summary_issue_title.setObjectName("SummaryTitle")
        right_card_layout.addWidget(summary_issue_title)
        self.summary_issue_hint = QLabel("No validation issues.")
        self.summary_issue_hint.setObjectName("BatchValidationHint")
        self.summary_issue_hint.setWordWrap(True)
        right_card_layout.addWidget(self.summary_issue_hint)
        right_card_layout.addStretch(1)
        summary_strip_layout.addWidget(self.summary_right_card, 1)
        root.addWidget(summary_strip)

        self.parameter_form = BatchParameterForm(build_project_form_schema())
        self.export_panel = BatchExportPanel()
        self.preview_panel = BatchPreviewPlaceholder()
        self.preview_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.export_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.export_panel.setMinimumHeight(240)
        self.export_panel.setMaximumHeight(260)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        left_panel = QFrame()
        left_panel.setObjectName("ProjectIssuesPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)
        left_layout.addWidget(self.parameter_form, 1)
        body.addWidget(left_panel, 3)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.addWidget(self.preview_panel, 3)
        right_layout.addWidget(self.export_panel, 1, Qt.AlignBottom)
        body.addWidget(right_panel, 2)
        root.addLayout(body, 1)

        self.compat_panel = CompatibilityPanel("Batch Compatibility")
        self.compat_panel.setVisible(False)

        self.action_bar = QFrame()
        self.action_bar.setObjectName("BatchActionBar")
        self.action_bar.setFixedHeight(52)
        action_layout = QHBoxLayout(self.action_bar)
        action_layout.setContentsMargins(10, 6, 10, 6)
        action_layout.setSpacing(10)
        self.project_manager_btn = QPushButton("Project Manager")
        self.project_manager_btn.setObjectName("StatusActionButton")
        self.project_manager_btn.setMinimumWidth(148)
        self.project_manager_btn.setMaximumWidth(186)
        self.project_manager_btn.setFixedHeight(30)
        action_layout.addWidget(self.project_manager_btn, 0, Qt.AlignLeft | Qt.AlignVCenter)
        action_layout.addStretch(1)
        self.back_btn = QPushButton("Back to Dashboard")
        self.back_btn.setObjectName("BatchGhostButton")
        self.save_btn = QPushButton("Save Batch")
        self.save_btn.setObjectName("BatchPrimaryButton")
        self.run_btn = QPushButton("Run Batch")
        self.run_btn.setObjectName("BatchRunButton")
        for button in (self.save_btn, self.run_btn, self.back_btn):
            button.setMinimumWidth(128)
        action_layout.addWidget(self.back_btn)
        action_layout.addWidget(self.save_btn)
        action_layout.addWidget(self.run_btn)
        root.addWidget(self.action_bar)

        self.save_btn.clicked.connect(lambda: self.save_batch.emit(self._payload()))
        self.run_btn.clicked.connect(lambda: self.run_batch.emit(self._payload()))
        self.back_btn.clicked.connect(self.back_to_dashboard.emit)
        self.project_manager_btn.clicked.connect(self.open_project_manager.emit)

        self.parameter_form.changed.connect(self._emit_draft_changed)
        self.parameter_form.blocked_interaction.connect(self.blocked_interaction.emit)
        self.export_panel.changed.connect(self._emit_draft_changed)
        self.export_panel.open_enclosure.connect(self.parameter_form.open_enclosure_dialog)
        self.batch_name.textChanged.connect(self._emit_draft_changed)

        self._summary_strip_layout = summary_strip_layout
        self._summary_strip = summary_strip
        self._body_layout = body
        self._right_panel = right_panel
        self._summary_cards = [self.summary_left_card, self.summary_center_card, self.summary_right_card]

        self._compat_state: Dict[str, Any] = {"visible_keys": [], "locked_keys": [], "sweepable_keys": [], "issues": []}
        self._latest_field_issues: List[Dict[str, Any]] = []
        self._project_fixed_keys: set[str] = set()
        self._eta_seconds: Optional[float] = None
        self._eta_sample_count: int = 0
        self._suspend_draft_events = False
        self._update_summary_widgets()
        QTimer.singleShot(0, self._apply_equal_widths)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_equal_widths()

    def _apply_equal_widths(self) -> None:
        margins = self._root_layout.contentsMargins()
        available_width = max(int(self.width() - margins.left() - margins.right()), 1)
        summary_reference = int(self._summary_strip.width()) if self._summary_strip is not None else 0
        summary_total = max(summary_reference or available_width, 1)
        summary_spacing = max(int(self._summary_strip_layout.spacing()), 0)
        summary_width = max((summary_total - (2 * summary_spacing)) // 3, 1)
        for card in self._summary_cards:
            card.setMinimumWidth(summary_width)
            card.setMaximumWidth(summary_width)

        body_total = max(available_width, 1)
        body_spacing = max(int(self._body_layout.spacing()), 0)
        right_width = max((body_total - body_spacing) // 3, 1)
        self._right_panel.setMinimumWidth(right_width)
        self._right_panel.setMaximumWidth(right_width)

        name_width = max(240, available_width // 3)
        name_width = max(int(summary_width), 240)
        self.batch_name.setMinimumWidth(name_width)
        self.batch_name.setMaximumWidth(name_width)

    def _emit_draft_changed(self) -> None:
        if self._suspend_draft_events:
            return
        self._update_summary_widgets()
        self.draft_changed.emit(self._payload(include_name=False))

    def set_preview_busy(self, busy: bool) -> None:
        self.preview_panel.set_busy(bool(busy))

    def set_preview_error(self, message: str) -> None:
        self.preview_panel.set_error_message(str(message or "Preview generation failed."))

    def set_preview_mesh(self, path: str) -> None:
        self.preview_panel.set_preview_mesh(str(path))

    def set_preview_parameters(self, parameters: Dict[str, Any]) -> None:
        self.preview_panel.set_preview_parameters(dict(parameters or {}))

    def set_project_fixed_keys(self, keys: List[str]) -> None:
        self._project_fixed_keys = {str(item) for item in list(keys or []) if str(item).strip()}
        self.parameter_form.set_project_fixed_keys(sorted(self._project_fixed_keys))
        self._update_summary_widgets()

    def highlight_policy_missing_keys(self, keys: List[str]) -> List[str]:
        return self.parameter_form.highlight_policy_missing_keys(list(keys or []))

    def clear_policy_missing_highlights(self) -> None:
        self.parameter_form.clear_manual_highlights()

    def apply_policy_defaults(self, defaults: Dict[str, Any]) -> None:
        self._suspend_draft_events = True
        try:
            self.parameter_form.apply_default_values(dict(defaults or {}))
        finally:
            self._suspend_draft_events = False
        self._emit_draft_changed()

    def set_policy_default_suggestions(self, defaults: Dict[str, Any]) -> None:
        self.parameter_form.set_policy_default_suggestions(dict(defaults or {}))

    def set_eta(self, eta_seconds: Optional[float], *, sample_count: int, median_seconds: Optional[float]) -> None:
        self._eta_seconds = eta_seconds
        self._eta_sample_count = max(int(sample_count), 0)
        if eta_seconds is None:
            self.summary_eta_label.setText("ETA: unknown")
            self.summary_eta_label.setToolTip("No historical duration data available yet.")
        else:
            total = max(float(eta_seconds), 0.0)
            minutes = int(total // 60)
            seconds = int(round(total - (minutes * 60)))
            if minutes > 0:
                text = f"ETA: ~{minutes}m {seconds:02d}s"
            else:
                text = f"ETA: ~{seconds}s"
            self.summary_eta_label.setText(text)
            median_hint = "unknown" if median_seconds is None else f"{float(median_seconds):.1f}s/version"
            self.summary_eta_label.setToolTip(
                f"Estimated from historical median ({median_hint}) across {self._eta_sample_count} successful versions."
            )

    def apply_compatibility(self, state: Dict[str, Any]) -> None:
        self._compat_state = dict(state)
        self.parameter_form.apply_compatibility(state)
        self.compat_panel.update_state(state)
        self._update_summary_widgets()

    def apply_ui_risks(self, issues: List[Dict[str, Any]]) -> None:
        self._latest_field_issues = [dict(item) for item in list(issues or []) if isinstance(item, dict)]
        self.parameter_form.apply_ui_risks(self._latest_field_issues)
        self._update_summary_widgets()

    def _payload(self, *, include_name: bool = True) -> Dict[str, object]:
        selected = self.parameter_form.selected_params_payload()
        sweeps = self.parameter_form.sweeps_payload()

        visible = set(str(item) for item in list(self._compat_state.get("visible_keys", []) or []))
        locked = set(str(item) for item in list(self._compat_state.get("locked_keys", []) or []))
        sweepable = set(str(item) for item in list(self._compat_state.get("sweepable_keys", []) or []))
        if visible or self._project_fixed_keys:
            selected = {
                key: value
                for key, value in selected.items()
                if (not visible or str(key) in visible)
                and str(key) not in locked
                and str(key) not in self._project_fixed_keys
            }
        if sweepable:
            sweeps = {
                key: value
                for key, value in sweeps.items()
                if str(key) in sweepable and str(key) not in locked and str(key) not in self._project_fixed_keys
            }

        payload: Dict[str, object] = {
            "sweep_mode": self.export_panel.sweep_mode_value(),
            "selected_params": selected,
            "sweeps": sweeps,
            "sim_export_params": self.export_panel.sim_export_params_payload(),
        }
        if include_name:
            payload["batch_name"] = self.batch_name.text().strip()
        return payload

    def reset_draft(self) -> None:
        self._suspend_draft_events = True
        try:
            self.batch_name.clear()
            self.export_panel.set_sweep_mode("single")
            self.parameter_form.set_selected_params({})
            self.parameter_form.set_sweeps({})
            self.parameter_form.set_policy_default_suggestions({})
            self.export_panel.set_from_payload({})
            self.set_eta(None, sample_count=0, median_seconds=None)
            self.preview_panel.set_busy(False)
            self.preview_panel.set_preview_parameters({})
            self.preview_panel.set_info_message("No preview mesh loaded.")
        finally:
            self._suspend_draft_events = False
        self._emit_draft_changed()

    def load_from_batch(self, batch: Batch, *, batch_name: Optional[str] = None) -> None:
        self._suspend_draft_events = True
        try:
            name = batch_name
            if not name:
                name = str(batch.extra.get("batch_name", batch.batch_id))
            self.batch_name.setText(name)
            mode = str(batch.sweep_mode or "single")
            self.export_panel.set_sweep_mode(mode if mode in {"single", "combined"} else "single")
            self.parameter_form.set_from_batch(batch)
            self.export_panel.set_from_batch(batch)
        finally:
            self._suspend_draft_events = False
        self._emit_draft_changed()

    def _update_summary_widgets(self) -> None:
        visible_count = len(self.parameter_form.visible_field_keys())
        active_sweeps = int(self.parameter_form.active_sweep_count())
        version_preview = int(self._compat_state.get("version_count_preview", 0) or 0)
        export_specs = int(self.export_panel.export_spec_count())
        selected = self.parameter_form.selected_params_payload()
        defined_vars = sum(1 for value in selected.values() if value is not None)
        mode = self.export_panel.sweep_mode_value()
        self.summary_meta_versions.setText(
            f"Version preview: {version_preview} · Export specs: {export_specs} · Mode: {mode}"
        )

        issues = list(self._latest_field_issues or self.compat_panel.issues())
        fatal_count = 0
        warn_count = 0
        incomplete_count = 0
        for issue in issues:
            severity = str(issue.get("severity", "")).lower()
            if severity == "warn":
                warn_count += 1
                continue
            if severity != "fatal":
                continue
            issue_key = str(issue.get("field_key") or issue.get("key") or "").strip()
            ui_severity = classify_ui_severity(issue, field_is_set=bool(selected.get(issue_key) is not None))
            if ui_severity == "incomplete":
                incomplete_count += 1
            else:
                fatal_count += 1
        def _issue_rank(raw: Dict[str, Any]) -> tuple[int, str]:
            sev = str(raw.get("severity", "")).strip().lower()
            if sev == "fatal":
                return (0, str(raw.get("message", "")))
            if sev == "warn":
                return (1, str(raw.get("message", "")))
            if sev == "incomplete":
                return (2, str(raw.get("message", "")))
            return (3, str(raw.get("message", "")))

        sorted_issues = sorted([dict(item) for item in issues], key=_issue_rank)
        self.summary_meta_counts.setText(
            f"Visible variable params: {visible_count} · Active sweeps: {active_sweeps}"
        )
        self.summary_defined_vars.setText(
            f"Defined variables: {defined_vars} · Errors: {fatal_count} · Incomplete: {incomplete_count}"
        )

        issue_lines: List[str] = []
        for issue in sorted_issues:
            msg = str(issue.get("message", "")).strip()
            if not msg:
                continue
            issue_lines.append(msg)
        tooltip_lines = [f"{index + 1}. {line}" for index, line in enumerate(issue_lines)]
        summary_tooltip = "\n".join(tooltip_lines[:12]).strip()
        if fatal_count > 0:
            self.summary_issue_hint.setText(
                "\n".join([line for line in issue_lines[:3] if line])
                or f"{fatal_count} fatal issue(s), {warn_count} warning(s)."
            )
            self.summary_issue_hint.setProperty("severity", "fatal")
        elif incomplete_count > 0:
            self.summary_issue_hint.setText("Define required values to run this batch.")
            self.summary_issue_hint.setProperty("severity", "")
        elif warn_count > 0:
            warning_lines = [
                str(issue.get("message", "")).strip()
                for issue in sorted_issues
                if str(issue.get("severity", "")).strip().lower() == "warn"
            ]
            warning_lines = [line for line in warning_lines if line]
            self.summary_issue_hint.setText(warning_lines[0] if warning_lines else f"{warn_count} warning(s) in current draft.")
            self.summary_issue_hint.setProperty("severity", "warn")
        else:
            self.summary_issue_hint.setText("No validation issues.")
            self.summary_issue_hint.setProperty("severity", "")
        self.summary_issue_hint.setToolTip(summary_tooltip)
        self.summary_issue_hint.style().unpolish(self.summary_issue_hint)
        self.summary_issue_hint.style().polish(self.summary_issue_hint)

        has_name = bool(self.batch_name.text().strip())
        can_save = has_name
        # Keep run button interactive once a name is present; run-time validation
        # dialog explains blockers/default options without silently disabling action.
        can_run = has_name
        self.save_btn.setEnabled(can_save)
        self.run_btn.setEnabled(can_run)
        if not has_name:
            self.save_btn.setToolTip("Provide a batch name first.")
        elif fatal_count > 0:
            self.save_btn.setToolTip("Resolve fatal validation issues before saving.")
        else:
            self.save_btn.setToolTip("")
        if not has_name:
            self.run_btn.setToolTip("Provide a batch name first.")
        elif incomplete_count > 0:
            self.run_btn.setToolTip("Undefined policy parameters will be offered with defaults on run.")
        elif fatal_count > 0:
            self.run_btn.setToolTip("Resolve fatal validation issues before running.")
        else:
            self.run_btn.setToolTip("")
        run_ready = bool(has_name and fatal_count == 0 and incomplete_count == 0)
        self.run_btn.setProperty("runReady", "true" if run_ready else "false")
        self.run_btn.style().unpolish(self.run_btn)
        self.run_btn.style().polish(self.run_btn)


class RunPage(QWidget):
    back_to_dashboard = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(0)
        root.addStretch(1)

        shell = QFrame()
        shell.setObjectName("RunScreenShell")
        shell.setMaximumWidth(860)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(24, 20, 24, 20)
        shell_layout.setSpacing(12)

        title = QLabel("RUN")
        title.setObjectName("PageTitle")
        shell_layout.addWidget(title, 0, Qt.AlignLeft | Qt.AlignVCenter)

        self.hint_label = QLabel(
            "AKABAK/VACS are driven via UI automation. This screen stays in front until the run finishes."
        )
        self.hint_label.setObjectName("SummaryText")
        self.hint_label.setWordWrap(True)
        shell_layout.addWidget(self.hint_label)

        self.progress = QProgressBar()
        self.progress.setObjectName("RunProgressBar")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(12)
        shell_layout.addWidget(self.progress)

        self.version_label = QLabel("Version 0/0")
        self.version_label.setObjectName("SummaryMeta")
        self.mode_label = QLabel("Mode: --")
        self.mode_label.setObjectName("SummaryMeta")
        self.eta_label = QLabel("ETA: --")
        self.eta_label.setObjectName("SummaryMeta")
        shell_layout.addWidget(self.version_label)
        shell_layout.addWidget(self.mode_label)
        shell_layout.addWidget(self.eta_label)
        shell_layout.addSpacing(6)

        self.back_btn = QPushButton("Back to Dashboard")
        self.back_btn.setObjectName("BatchSecondaryButton")
        self.back_btn.setEnabled(False)
        self.back_btn.clicked.connect(self.back_to_dashboard.emit)
        shell_layout.addWidget(self.back_btn, 0, Qt.AlignRight)

        root.addWidget(shell, 0, Qt.AlignHCenter | Qt.AlignVCenter)
        root.addStretch(1)

    def set_running_state(self) -> None:
        self.progress.setRange(0, 0)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.version_label.setText("Version 0/0")
        self.mode_label.setText("Mode: running...")
        self.eta_label.setText("ETA: calculating...")
        self.back_btn.setEnabled(False)

    def set_background_mode(self, enabled: bool) -> None:
        if enabled:
            self.hint_label.setText(
                "AKABAK/VACS are driven via UI automation. This screen stays in front until the run finishes."
            )
        else:
            self.hint_label.setText(
                "AKABAK/VACS are driven via UI automation. Background mode is disabled; tool windows may come to front."
            )

    def set_finished_state(self, *, version_count: int, dry_run: bool) -> None:
        count = max(int(version_count), 0)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setTextVisible(True)
        self.format_progress_label()
        self.version_label.setText(f"Version {count}/{count}")
        self.mode_label.setText("Mode: dry-run" if dry_run else "Mode: real")
        self.eta_label.setText("ETA: done")
        self.back_btn.setEnabled(True)

    def set_failed_state(self) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setTextVisible(True)
        self.progress.setFormat("Run failed")
        self.mode_label.setText("Mode: failed")
        self.eta_label.setText("ETA: --")
        self.back_btn.setEnabled(True)

    def format_progress_label(self) -> None:
        self.progress.setFormat("Run complete")


class ProjectManagerWindow(QMainWindow):
    open_project = Signal(str)
    create_project = Signal()

    def __init__(self, service: OrchestratorService) -> None:
        super().__init__()
        self.service = service
        self.setWindowTitle("WUT Batcher - Project Manager")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setProperty("framelessShell", True)
        self.setMinimumSize(760, 520)
        self.resize(920, 620)
        self._drag_offset: Optional[QPoint] = None

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)
        shell = QFrame()
        shell.setObjectName("FramelessShell")
        outer.addWidget(shell)
        root = QVBoxLayout(shell)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title_bar = QWidget()
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title = QLabel("Project Manager")
        title.setObjectName("SectionTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        close_btn = QPushButton("X")
        close_btn.setObjectName("WindowCloseButton")
        close_btn.setFixedSize(28, 24)
        close_btn.clicked.connect(self.close)
        title_row.addWidget(close_btn, alignment=Qt.AlignRight)
        root.addWidget(title_bar)
        title_bar.mousePressEvent = self._title_mouse_press  # type: ignore[assignment]
        title_bar.mouseMoveEvent = self._title_mouse_move  # type: ignore[assignment]
        title_bar.mouseReleaseEvent = self._title_mouse_release  # type: ignore[assignment]

        self.project_list = QListWidget()
        self.project_list.setObjectName("ProjectTileList")
        self.project_list.setViewMode(QListView.IconMode)
        self.project_list.setResizeMode(QListView.Adjust)
        self.project_list.setMovement(QListView.Static)
        self.project_list.setWrapping(True)
        self.project_list.setSpacing(12)
        self.project_list.setIconSize(QSize(170, 120))
        self.project_list.setGridSize(QSize(210, 170))
        self.project_list.setWordWrap(True)
        self.project_list.setSelectionRectVisible(False)
        list_palette = self.project_list.palette()
        list_palette.setColor(QPalette.Highlight, QColor(0, 0, 0, 0))
        list_palette.setColor(QPalette.HighlightedText, QColor("#F1F1F1"))
        self.project_list.setPalette(list_palette)
        root.addWidget(self.project_list, 1)

        buttons = QHBoxLayout()
        open_btn = QPushButton("Open Project")
        open_btn.setObjectName("ProjectManagerButton")
        new_btn = QPushButton("New Project")
        new_btn.setObjectName("ProjectManagerButton")
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("ProjectManagerButton")
        buttons.addWidget(open_btn)
        buttons.addWidget(new_btn)
        buttons.addWidget(refresh_btn)
        buttons.addStretch(1)
        root.addLayout(buttons)

        open_btn.clicked.connect(self._emit_open)
        new_btn.clicked.connect(self.create_project.emit)
        refresh_btn.clicked.connect(self.refresh)
        self.project_list.itemDoubleClicked.connect(lambda _item: self._emit_open())
        self.refresh()

    def refresh(self) -> None:
        self.project_list.clear()
        for project in self.service.list_projects():
            item = QListWidgetItem()
            item.setIcon(self._project_tile_icon(project.name, project.project_id))
            item.setText("")
            item.setToolTip(f"{project.project_id} | {project.name}")
            item.setData(Qt.UserRole, project.project_id)
            self.project_list.addItem(item)

    def _project_tile_icon(self, project_name: str, project_id: str) -> QIcon:
        pixmap = QPixmap(170, 120)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)

        frame = QPainterPath()
        frame.addRoundedRect(1, 1, 168, 118, 10, 10)
        painter.fillPath(frame, QColor("#13161A"))
        painter.setPen(QColor("#2C323A"))
        painter.drawPath(frame)

        painter.setPen(QColor("#F1F1F1"))
        title_font = QFont("Segoe UI", 9)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(8, 8, 154, 22, Qt.AlignCenter | Qt.TextWordWrap, str(project_name or "Project"))

        thumbnail_rect = (18, 36, 134, 72)
        image_path = self.service.project_preview_image_path(project_id)
        preview = QPixmap(str(image_path)) if image_path.exists() else QPixmap()
        if not preview.isNull():
            zoom_factor = 1.8
            crop_w = max(1, int(preview.width() / zoom_factor))
            crop_h = max(1, int(preview.height() / zoom_factor))
            crop_x = max(0, (preview.width() - crop_w) // 2)
            crop_y = max(0, (preview.height() - crop_h) // 2)
            cropped = preview.copy(crop_x, crop_y, crop_w, crop_h)
            clipped = cropped.scaled(
                thumbnail_rect[2],
                thumbnail_rect[3],
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            draw_x = thumbnail_rect[0] - max(0, (clipped.width() - thumbnail_rect[2]) // 2)
            draw_y = thumbnail_rect[1] - max(0, (clipped.height() - thumbnail_rect[3]) // 2)
            painter.setClipRect(*thumbnail_rect)
            painter.drawPixmap(draw_x, draw_y, clipped)
            painter.setClipping(False)
            painter.setPen(QColor("#323941"))
            painter.drawRoundedRect(*thumbnail_rect, 8, 8)
        else:
            painter.setPen(QColor("#252B33"))
            painter.setBrush(QColor("#1A1F25"))
            painter.drawRoundedRect(*thumbnail_rect, 8, 8)
            painter.setPen(QColor("#3A424D"))
            painter.drawLine(28, 95, 78, 58)
            painter.drawLine(78, 58, 112, 86)
            painter.drawLine(112, 86, 138, 65)
            painter.setBrush(QColor("#3A424D"))
            painter.drawEllipse(38, 54, 8, 8)
        painter.end()
        return QIcon(pixmap)

    def _emit_open(self) -> None:
        item = self.project_list.currentItem()
        if item is None:
            return
        project_id = item.data(Qt.UserRole)
        if project_id:
            self.open_project.emit(str(project_id))

    def _title_mouse_press(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.LeftButton:
            return
        self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def _title_mouse_move(self, event) -> None:  # type: ignore[override]
        if self._drag_offset is None:
            return
        if not (event.buttons() & Qt.LeftButton):
            return
        self.move(event.globalPosition().toPoint() - self._drag_offset)
        event.accept()

    def _title_mouse_release(self, event) -> None:  # type: ignore[override]
        self._drag_offset = None
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self, service: OrchestratorService) -> None:
        super().__init__()
        self.service = service
        self.compat_ui_adapter = CompatUiAdapter(build_project_form_schema())
        self.current_project: Optional[Project] = None
        self.last_status_detail = ""
        self.ui_validation = UiValidationEngine()
        self._project_validation_debounce_ms = 100
        self._pending_project_payload: Optional[Dict[str, object]] = None
        self._project_validation_timer = QTimer(self)
        self._project_validation_timer.setSingleShot(True)
        self._project_validation_timer.setInterval(self._project_validation_debounce_ms)
        self._project_validation_timer.timeout.connect(self._flush_project_draft_validation)
        self._project_reconcile_guard = False
        self._batch_validation_debounce_ms = 100
        self._pending_batch_payload: Optional[Dict[str, object]] = None
        self._batch_reconcile_guard = False
        self._batch_validation_timer = QTimer(self)
        self._batch_validation_timer.setSingleShot(True)
        self._batch_validation_timer.setInterval(self._batch_validation_debounce_ms)
        self._batch_validation_timer.timeout.connect(self._flush_batch_draft_validation)
        self._project_manager_handler: Optional[Callable[[], None]] = None
        self._preview_request_id = 0
        self._preview_thread: Optional[QThread] = None
        self._preview_worker: Optional[_BatchPreviewWorker] = None
        self._batch_run_thread: Optional[QThread] = None
        self._batch_run_worker: Optional[_BatchRunWorker] = None
        self._preview_update_debounce_ms = 280
        self._pending_preview_payload: Optional[Dict[str, object]] = None
        self._preview_update_timer = QTimer(self)
        self._preview_update_timer.setSingleShot(True)
        self._preview_update_timer.setInterval(self._preview_update_debounce_ms)
        self._preview_update_timer.timeout.connect(self._flush_batch_preview_update)
        self._run_foreground_timer = QTimer(self)
        self._run_foreground_timer.setSingleShot(False)
        self._run_foreground_timer.setInterval(850)
        self._run_foreground_timer.timeout.connect(self._enforce_run_foreground)
        self._run_fullscreen_active = False
        self._window_state_before_run = Qt.WindowNoState
        self._window_topmost_before_run = False

        self.setWindowTitle("WUT Batcher")
        self.setMinimumSize(1280, 800)
        self.resize(1280, 860)

        self.stack = QStackedWidget()
        self.dashboard_page = DashboardPage()
        self.project_page = ProjectPage()
        self.batch_page = BatchPage()
        self.run_page = RunPage()

        self.stack.addWidget(self.dashboard_page)
        self.stack.addWidget(self.project_page)
        self.stack.addWidget(self.batch_page)
        self.stack.addWidget(self.run_page)
        self.setCentralWidget(self.stack)

        self._build_statusbar()
        self._connect_page_signals()
        try:
            self.service.cleanup_preview_cache()
        except Exception as exc:
            # Non-critical startup maintenance; runtime preview requests handle errors explicitly.
            LOGGER.warning("Startup preview cache cleanup failed: %s", exc)
        self.show_dashboard()

    def _build_statusbar(self) -> None:
        bar = QStatusBar()
        bar.setSizeGripEnabled(False)
        self.setStatusBar(bar)

        self.status_message = ClickableLabel("Ready.")
        self.status_message.clicked.connect(self._show_status_detail)
        bar.addWidget(self.status_message, 1)

        self.brand = ClickableLabel("WUT BATCHER")
        self.brand.setObjectName("StatusBrand")
        self.brand.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.brand.clicked.connect(self._show_about)
        bar.addPermanentWidget(self.brand)

    def set_project_manager_handler(self, handler: Callable[[], None]) -> None:
        self._project_manager_handler = handler

    def _open_project_manager(self) -> None:
        if callable(self._project_manager_handler):
            self._project_manager_handler()

    def _connect_page_signals(self) -> None:
        self.dashboard_page.request_new_batch.connect(self.show_batch)
        self.dashboard_page.request_edit_batch.connect(self._edit_batch)
        self.dashboard_page.request_clone_batch.connect(self._clone_batch)
        self.dashboard_page.request_open_export_dialog.connect(self._open_export_dialog)
        self.dashboard_page.request_manage_runs.connect(self._open_run_manager)
        self.dashboard_page.request_cleanup_testdata.connect(self._open_cleanup_dialog)
        self.dashboard_page.request_settings.connect(self._open_settings)

        self.project_page.submit_project.connect(self._create_project)
        self.project_page.draft_changed.connect(self._queue_project_draft_changed)
        self.project_page.blocked_interaction.connect(self._on_project_blocked_interaction)

        self.batch_page.save_batch.connect(self._save_batch)
        self.batch_page.run_batch.connect(self._run_batch)
        self.batch_page.back_to_dashboard.connect(self.show_dashboard)
        self.batch_page.open_project_manager.connect(self._open_project_manager)
        self.batch_page.draft_changed.connect(self._queue_batch_draft_changed)
        self.batch_page.blocked_interaction.connect(self._on_batch_blocked_interaction)
        self.batch_page.compat_panel.request_show_details.connect(
            lambda: self._show_validation_details(self.batch_page.compat_panel.issues(), "Batch Validation Details")
        )
        self.run_page.back_to_dashboard.connect(self.show_dashboard)

    def _enter_run_presentation(self) -> None:
        if not self._background_automation_enabled():
            self._run_foreground_timer.stop()
            self._run_fullscreen_active = False
            if self.statusBar() is not None:
                self.statusBar().setVisible(True)
            self.show()
            return
        if not self._run_fullscreen_active:
            self._window_state_before_run = self.windowState()
            self._window_topmost_before_run = bool(self.windowFlags() & Qt.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.show()
        if self.statusBar() is not None:
            self.statusBar().setVisible(False)
        self._run_fullscreen_active = True
        _ensure_fullscreen_foreground(self)
        self._run_foreground_timer.start()

    def _exit_run_presentation(self) -> None:
        if not self._run_fullscreen_active:
            return
        self._run_foreground_timer.stop()
        self._run_fullscreen_active = False
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self._window_topmost_before_run)
        self.show()
        if self.statusBar() is not None:
            self.statusBar().setVisible(True)
        previous_state = self._window_state_before_run
        if bool(previous_state & Qt.WindowMaximized):
            _ensure_maximized_foreground(self)
        elif bool(previous_state & Qt.WindowFullScreen):
            _ensure_fullscreen_foreground(self)
        else:
            _ensure_normal_foreground(self)

    def _enforce_run_foreground(self) -> None:
        if not self._run_fullscreen_active:
            return
        if self.stack.currentWidget() is not self.run_page:
            return
        _ensure_fullscreen_foreground(self)

    def _background_automation_enabled(self) -> bool:
        return bool(getattr(self.service.settings, "background_automation_mode", True))

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._run_in_progress():
            QMessageBox.warning(
                self,
                "Run in progress",
                "A run is still in progress. Please wait until it finishes.",
            )
            event.ignore()
            return
        self._stop_preview_worker()
        super().closeEvent(event)

    def _stop_preview_worker(self) -> None:
        self._cancel_pending_preview_update()
        worker = self._preview_worker
        thread = self._preview_thread
        if worker is not None:
            worker.cancel()
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(1800)
        self._preview_worker = None
        self._preview_thread = None

    def _run_in_progress(self) -> bool:
        thread = self._batch_run_thread
        return bool(thread is not None and thread.isRunning())

    def _clear_batch_run_worker_refs(self, thread: Optional[QThread] = None) -> None:
        if thread is None:
            self._batch_run_thread = None
            self._batch_run_worker = None
            return
        if self._batch_run_thread is thread:
            self._batch_run_thread = None
            self._batch_run_worker = None

    def _start_batch_run_worker(self, *, project_id: str, batch_id: str, continue_on_error: bool) -> None:
        worker = _BatchRunWorker(
            service=self.service,
            project_id=project_id,
            batch_id=batch_id,
            continue_on_error=continue_on_error,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_batch_run_finished)
        worker.failed.connect(self._on_batch_run_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._clear_batch_run_worker_refs(thread))
        self._batch_run_worker = worker
        self._batch_run_thread = thread
        thread.start()

    def _on_batch_run_finished(self, batch_id: str, summary_payload: Dict[str, Any]) -> None:
        version_count = len(list(summary_payload.get("versions", []) or []))
        dry_run = bool(summary_payload.get("dry_run", False))
        self.run_page.set_finished_state(version_count=version_count, dry_run=dry_run)
        self.set_status(
            f"Run finished for {batch_id}",
            detail=json.dumps(summary_payload, indent=2, ensure_ascii=False),
        )
        self.refresh_dashboard()
        self._exit_run_presentation()

    def _on_batch_run_failed(self, batch_id: str, detail: str) -> None:
        self.run_page.set_failed_state()
        self.set_status(
            f"Run failed for {batch_id}",
            detail=str(detail or "unknown error"),
        )
        self._exit_run_presentation()

    def _cancel_pending_preview_update(self) -> None:
        self._pending_preview_payload = None
        self._preview_update_timer.stop()

    def _queue_batch_preview_update(self, payload: Dict[str, object]) -> None:
        self._pending_preview_payload = dict(payload)
        self._preview_update_timer.start()

    def _flush_batch_preview_update(self) -> None:
        payload = self._pending_preview_payload
        self._pending_preview_payload = None
        if payload is None:
            payload = self.batch_page._payload(include_name=False)
        self._request_batch_preview_update(payload)

    def _request_batch_preview_update(self, payload: Dict[str, object]) -> None:
        if self.current_project is None:
            self.batch_page.set_preview_busy(False)
            self.batch_page.set_preview_error("Open a project before generating preview.")
            self.batch_page.set_policy_default_suggestions({})
            return

        self._stop_preview_worker()

        selected_params = dict(payload.get("selected_params", {}) or {})
        sweep_mode = str(payload.get("sweep_mode", "single") or "single")

        self._preview_request_id += 1
        request_id = int(self._preview_request_id)
        worker = _BatchPreviewWorker(
            service=self.service,
            project_id=self.current_project.project_id,
            selected_params=selected_params,
            sweep_mode=sweep_mode,
            request_id=request_id,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_batch_preview_ready)
        worker.failed.connect(self._on_batch_preview_failed)
        worker.canceled.connect(self._on_batch_preview_canceled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.canceled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._preview_worker = worker
        self._preview_thread = thread
        self.batch_page.set_preview_busy(True)
        thread.start()

    def _on_batch_preview_ready(self, request_id: int, result: Dict[str, Any]) -> None:
        if int(request_id) != int(self._preview_request_id):
            return
        cache_path = str(result.get("cache_stl", "")).strip()
        if not cache_path:
            self.batch_page.set_preview_busy(False)
            self.batch_page.set_preview_error("Preview finished without cached STL path.")
            self._preview_worker = None
            self._preview_thread = None
            return
        try:
            self.batch_page.set_preview_mesh(cache_path)
        except Exception as exc:
            self.batch_page.set_preview_busy(False)
            self.batch_page.set_preview_error(f"Preview load failed: {exc}")
            self._preview_worker = None
            self._preview_thread = None
            return
        self.batch_page.set_preview_busy(False)
        self.batch_page.set_policy_default_suggestions(dict(result.get("policy_default_values", {}) or {}))
        self.set_status("Preview updated.", detail=json.dumps(result, indent=2, ensure_ascii=False))
        self._preview_worker = None
        self._preview_thread = None

    def _on_batch_preview_failed(self, request_id: int, message: str) -> None:
        if int(request_id) != int(self._preview_request_id):
            return
        self.batch_page.set_preview_busy(False)
        self.batch_page.set_preview_error(str(message or "Preview generation failed."))
        self.batch_page.set_policy_default_suggestions({})
        self.set_status("Preview generation failed.", detail=str(message or "unknown error"))
        self._preview_worker = None
        self._preview_thread = None

    def _on_batch_preview_canceled(self, request_id: int, message: str) -> None:
        if int(request_id) != int(self._preview_request_id):
            return
        self.batch_page.set_preview_busy(False)
        self.batch_page.preview_panel.set_info_message("Preview update canceled.")
        self.batch_page.set_policy_default_suggestions({})
        self._preview_worker = None
        self._preview_thread = None

    @staticmethod
    def _project_fixed_keys_from_constraints(constraints: ProjectConstraints) -> List[str]:
        keys = {
            *(str(key) for key in dict(getattr(constraints, "fixed_params", {}) or {}).keys()),
            *(str(key) for key in dict(getattr(constraints, "limits", {}) or {}).keys()),
        }
        for row in list(getattr(constraints, "param_states", []) or []):
            if not isinstance(row, dict):
                continue
            if not bool(row.get("is_set")):
                continue
            key = str(row.get("param_name", "")).strip()
            if key:
                keys.add(key)
        return sorted(keys)

    @staticmethod
    def _sanitize_batch_payload_for_project_constraints(
        payload: Dict[str, Any],
        constraints: ProjectConstraints,
        compat_state: Dict[str, Any],
    ) -> tuple[Dict[str, Any], bool]:
        visible_keys = {
            str(item)
            for item in list(compat_state.get("visible_keys", []) or [])
            if str(item).strip()
        }
        sweepable_keys = {
            str(item)
            for item in list(compat_state.get("sweepable_keys", []) or [])
            if str(item).strip()
        }
        fixed_keys = set(MainWindow._project_fixed_keys_from_constraints(constraints))

        selected_in = dict(payload.get("selected_params", {}) or {})
        sweeps_in = dict(payload.get("sweeps", {}) or {})

        selected_out: Dict[str, Any] = {}
        for key, value in selected_in.items():
            key_s = str(key).strip()
            if not key_s:
                continue
            if key_s in fixed_keys:
                continue
            if key_s not in visible_keys:
                continue
            selected_out[key_s] = value

        sweeps_out: Dict[str, Any] = {}
        for key, sweep in sweeps_in.items():
            key_s = str(key).strip()
            if not key_s:
                continue
            if key_s in fixed_keys:
                continue
            if key_s not in visible_keys:
                continue
            if key_s not in sweepable_keys:
                continue
            sweeps_out[key_s] = sweep

        changed = (selected_out != selected_in) or (sweeps_out != sweeps_in)
        sanitized = dict(payload)
        sanitized["selected_params"] = selected_out
        sanitized["sweeps"] = sweeps_out
        return sanitized, changed

    def set_status(self, text: str, detail: Optional[str] = None) -> None:
        self.status_message.setText(text)
        self.last_status_detail = detail or text

    def _on_project_blocked_interaction(self, _target_key: str, cause_key: str, message: str) -> None:
        if cause_key:
            self.project_page.constraints_form.flash_cause_key(cause_key)
        hint = str(message or "").strip()
        if hint:
            self.set_status(hint)

    def _on_batch_blocked_interaction(self, _target_key: str, cause_key: str, message: str) -> None:
        if cause_key:
            self.batch_page.parameter_form.flash_cause_key(cause_key)
        hint = str(message or "").strip()
        if hint:
            self.set_status(hint)

    def _show_status_detail(self) -> None:
        StatusDetailDialog(self.last_status_detail or "No details.", self).exec()

    def _show_about(self) -> None:
        AboutDialog(self).exec()

    def _show_validation_details(self, issues: List[Dict[str, Any]], title: str) -> None:
        if not issues:
            QMessageBox.information(self, title, "No validation issues.")
            return
        lines = []
        for issue in issues:
            severity = str(issue.get("severity", "info")).upper()
            rule_id = str(issue.get("rule_id", "unknown_rule"))
            evidence_type = str(issue.get("evidence_type", "hypothesis"))
            message = str(issue.get("message", ""))
            lines.append(f"[{severity}] {rule_id} ({evidence_type})\n{message}")
        QMessageBox.information(self, title, "\n\n".join(lines))

    def _present_validation_summary(
        self,
        *,
        title: str,
        issues: List[Dict[str, Any]],
        block_on_fatal: bool,
    ) -> bool:
        if not issues:
            return True
        fatal_count = sum(1 for issue in issues if str(issue.get("severity", "")).lower() == "fatal")
        top = issues[:5]
        lines = []
        for issue in top:
            severity = str(issue.get("severity", "info")).upper()
            rule_id = str(issue.get("rule_id", "unknown_rule"))
            evidence_type = str(issue.get("evidence_type", "hypothesis"))
            message = str(issue.get("message", ""))
            lines.append(f"[{severity}] {rule_id} ({evidence_type}) - {message}")
        detail_lines = [
            {
                "severity": issue.get("severity"),
                "rule_id": issue.get("rule_id"),
                "evidence_type": issue.get("evidence_type"),
                "message": issue.get("message"),
                "scope": issue.get("scope"),
            }
            for issue in issues
        ]
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setIcon(QMessageBox.Warning if fatal_count == 0 else QMessageBox.Critical)
        dialog.setText(f"Validation Summary ({len(issues)} issues)")
        dialog.setInformativeText("\n".join(lines) + "\n\nShow details for full list.")
        dialog.setDetailedText(json.dumps(detail_lines, indent=2, ensure_ascii=False))
        if fatal_count > 0 and block_on_fatal:
            dialog.setStandardButtons(QMessageBox.Ok)
            dialog.exec()
            return False
        dialog.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        return dialog.exec() == QMessageBox.Ok

    def _normalize_batch_issues_for_ui(
        self,
        issues: List[Dict[str, Any]],
        *,
        selected_params: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for issue in issues:
            entry = dict(issue)
            severity = str(entry.get("severity", "")).strip().lower()
            if severity != "fatal":
                normalized.append(entry)
                continue
            key = str(entry.get("field_key") or entry.get("key") or "").strip()
            ui_severity = classify_ui_severity(entry, field_is_set=bool(selected_params.get(key) is not None))
            if ui_severity == "incomplete":
                entry["severity"] = "incomplete"
            normalized.append(entry)
        return normalized

    @staticmethod
    def _batch_issue_counts(issues: List[Dict[str, Any]]) -> Dict[str, int]:
        counts = {"fatal": 0, "warn": 0, "incomplete": 0}
        for issue in issues:
            severity = str(issue.get("severity", "")).strip().lower()
            if severity in counts:
                counts[severity] += 1
        return counts

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.service, self)
        dialog.settings_saved.connect(lambda _: self.set_status("Settings saved."))
        dialog.exec()

    def load_project(self, project: Project) -> None:
        self.current_project = project
        self.project_page.set_constraints_locked(True)
        fixed_keys = self._project_fixed_keys_from_constraints(project.constraints)
        self.batch_page.set_project_fixed_keys(fixed_keys)
        self.refresh_dashboard()
        self._on_project_draft_changed(self.project_page._raw_constraints_payload())
        self._on_batch_draft_changed(self.batch_page._payload(include_name=False))
        self.show_dashboard()

    def refresh_dashboard(self) -> None:
        if self.current_project is None:
            self.dashboard_page.set_constraints_payload(None)
            self.dashboard_page.batch_list.clear()
            return

        self.dashboard_page.set_constraints_payload(self.current_project.constraints.to_dict())
        self.dashboard_page.batch_list.clear()
        for batch in self.service.repo.list_batches(self.current_project.project_id):
            label = f"{batch.batch_id} | {batch.extra.get('batch_name', batch.batch_id)}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, batch.batch_id)
            self.dashboard_page.batch_list.addItem(item)

    def show_dashboard(self) -> None:
        self._stop_preview_worker()
        self._exit_run_presentation()
        self.stack.setCurrentWidget(self.dashboard_page)

    def show_project(self) -> None:
        self._stop_preview_worker()
        self._exit_run_presentation()
        self._on_project_draft_changed(self.project_page._raw_constraints_payload())
        self.stack.setCurrentWidget(self.project_page)

    def show_batch(self) -> None:
        self._exit_run_presentation()
        self.batch_page.reset_draft()
        self._on_batch_draft_changed(self.batch_page._payload(include_name=False))
        self.stack.setCurrentWidget(self.batch_page)

    def show_run(self) -> None:
        self._stop_preview_worker()
        self.stack.setCurrentWidget(self.run_page)
        self.run_page.set_background_mode(self._background_automation_enabled())
        self._enter_run_presentation()

    def _create_project(self, project_name: str, constraints: Dict[str, object]) -> None:
        self.project_page.set_creating(True)
        try:
            validation = self.service.evaluate_project_constraints(dict(constraints))
            issues = [item for item in list(validation.get("issues", []) or []) if isinstance(item, dict)]
            project = self.service.create_project(project_name, constraints)
            self.load_project(project)
            self.project_page.set_constraints_locked(True)
            if issues:
                self.set_status(
                    f"Project created: {project.project_id} (draft issues: {len(issues)})",
                    detail=json.dumps(issues, indent=2, ensure_ascii=False),
                )
            else:
                self.set_status(f"Project created: {project.project_id}")
        finally:
            self.project_page.set_creating(False)

    def _save_batch(self, payload: Dict[str, object], *, for_run: bool = False) -> Optional[str]:
        if self.current_project is None:
            self.set_status("No project loaded.")
            return None
        raw_payload = dict(payload)
        raw_selected_params = dict(raw_payload.get("selected_params", {}) or {})
        raw_sweeps = dict(raw_payload.get("sweeps", {}) or {})
        raw_mode = str(raw_payload.get("sweep_mode", "single"))
        validation = self.service.evaluate_batch_definition(
            project_id=self.current_project.project_id,
            selected_params=raw_selected_params,
            sweeps=raw_sweeps,
            sweep_mode=raw_mode,
        )
        sanitized_payload, changed = self._sanitize_batch_payload_for_project_constraints(
            raw_payload,
            self.current_project.constraints,
            validation,
        )
        payload = sanitized_payload
        selected_params = dict(payload.get("selected_params", {}) or {})
        if changed:
            validation = self.service.evaluate_batch_definition(
                project_id=self.current_project.project_id,
                selected_params=selected_params,
                sweeps=dict(payload.get("sweeps", {}) or {}),
                sweep_mode=str(payload.get("sweep_mode", "single")),
            )
        raw_issues = [item for item in list(validation.get("issues", []) or []) if isinstance(item, dict)]
        issues = self._normalize_batch_issues_for_ui(raw_issues, selected_params=selected_params)
        issues.extend(self.batch_page.export_panel.validation_issues())
        counts = self._batch_issue_counts(issues)
        block_count = int(counts.get("fatal", 0))
        if for_run:
            block_count += int(counts.get("incomplete", 0))
        should_prompt = int(counts.get("fatal", 0)) > 0
        if should_prompt:
            if not self._present_validation_summary(
                title="Batch Validation Summary",
                issues=issues,
                block_on_fatal=(block_count > 0),
            ):
                self.set_status("Batch save blocked by validation.")
                return None
        summary = self.service.create_batch(
            project_id=self.current_project.project_id,
            batch_name=str(payload.get("batch_name", "")),
            selected_params=dict(payload.get("selected_params", {}) or {}),
            sweeps=dict(payload.get("sweeps", {}) or {}),
            sweep_mode=str(payload.get("sweep_mode", "single")),
            sim_export_params=dict(payload.get("sim_export_params", {}) or {}),
        )
        self.set_status(
            f"Batch saved: {summary.batch_id}, versions={summary.version_count}",
            detail=json.dumps(asdict(summary), indent=2, ensure_ascii=False),
        )
        self.refresh_dashboard()
        if not for_run:
            self.show_dashboard()
        return summary.batch_id

    @staticmethod
    def _merge_policy_defaults(
        selected_params: Dict[str, Any],
        default_values: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged = dict(selected_params or {})
        for raw_key, raw_value in dict(default_values or {}).items():
            key = str(raw_key).strip()
            if not key:
                continue
            if key.startswith("R-OSSE."):
                obj = dict(merged.get("R-OSSE", {}) or {})
                sub_key = key.split(".", 1)[1]
                if obj.get(sub_key) is None:
                    obj[sub_key] = raw_value
                merged["R-OSSE"] = obj
                continue
            if key == "R-OSSE" and isinstance(raw_value, Mapping):
                obj = dict(merged.get("R-OSSE", {}) or {})
                for sub_key, sub_value in dict(raw_value).items():
                    if obj.get(str(sub_key)) is None:
                        obj[str(sub_key)] = sub_value
                merged["R-OSSE"] = obj
                continue
            if merged.get(key) is None:
                merged[key] = raw_value
        return merged

    def _resolve_run_policy_defaults(self, payload: Dict[str, object]) -> Optional[Dict[str, object]]:
        if self.current_project is None:
            return payload
        selected_params = dict(payload.get("selected_params", {}) or {})
        policy = self.service.evaluate_batch_default_policy(
            project_id=self.current_project.project_id,
            selected_params=selected_params,
        )
        missing_keys = [str(item) for item in list(policy.get("missing_keys", []) or []) if str(item).strip()]
        default_values = dict(policy.get("default_values", {}) or {})
        self.batch_page.clear_policy_missing_highlights()
        if not missing_keys:
            return payload

        dialog = BatchRunDefaultsDialog(
            missing_keys=missing_keys,
            default_values=default_values,
            parent=self,
        )
        decision = "cancel"
        if dialog.exec() == QDialog.Accepted:
            decision = dialog.decision()
        if decision == "show":
            highlighted = self.batch_page.highlight_policy_missing_keys(missing_keys)
            if highlighted:
                self.set_status(f"Highlighted undefined parameters: {len(highlighted)}")
            else:
                self.set_status("No visible undefined parameters to highlight.")
            return None
        if decision != "use_defaults":
            self.set_status("Run canceled.")
            return None

        merged_selected = self._merge_policy_defaults(selected_params, default_values)
        next_payload = dict(payload)
        next_payload["selected_params"] = merged_selected
        self.batch_page.apply_policy_defaults(default_values)
        self.batch_page.clear_policy_missing_highlights()
        self.set_status("Applied policy defaults for run.")
        return next_payload

    def _run_batch(self, payload: Dict[str, object]) -> None:
        if self._run_in_progress():
            self.set_status("Run already in progress.")
            return
        self._stop_preview_worker()
        run_payload = self._resolve_run_policy_defaults(dict(payload))
        if run_payload is None:
            return
        batch_id = self._save_batch(run_payload, for_run=True)
        if self.current_project is None or not batch_id:
            return
        self._ensure_project_preview_thumbnail()
        self.show_run()
        self.run_page.set_running_state()
        self.set_status(f"Run started for {batch_id}")
        QApplication.processEvents()
        self._start_batch_run_worker(
            project_id=self.current_project.project_id,
            batch_id=batch_id,
            continue_on_error=True,
        )

    def _ensure_project_preview_thumbnail(self) -> None:
        if self.current_project is None:
            return
        target = self.service.project_preview_image_path(self.current_project.project_id)
        if target.exists():
            return
        ok = self.batch_page.preview_panel.capture_snapshot(target)
        if not ok:
            return
        self.refresh_dashboard()

    def _open_export_dialog(self) -> None:
        if self.current_project is None:
            self.set_status("No project loaded.")
            return
        rows = self.service.list_versions(self.current_project.project_id)
        versions_by_batch: Dict[str, List[str]] = {}
        for row in rows:
            batch_id = str(row["batch_id"])
            versions_by_batch.setdefault(batch_id, []).append(str(row["version_id"]))
        if not versions_by_batch:
            self.set_status("No versions available for export.")
            return
        dialog = ExportDialog(versions_by_batch, self)
        if dialog.exec() != QDialog.Accepted:
            return
        payload = dialog.payload()
        self._export_version(
            str(payload["batch_id"]),
            str(payload["version_id"]),
            bool(payload["export_stl"]),
            bool(payload["export_abec"]),
        )

    def _open_run_manager(self) -> None:
        if self.current_project is None:
            self.set_status("No project loaded.")
            return
        RunManagerDialog(self.service, self.current_project.project_id, self).exec()
        self.refresh_dashboard()

    def _open_cleanup_dialog(self) -> None:
        if self.current_project is None:
            self.set_status("No project loaded.")
            return
        dialog = CleanupTestDataDialog(self.service, self.current_project.project_id, self)
        if dialog.exec() == QDialog.Accepted:
            self.set_status("Cleanup finished.")
            self.refresh_dashboard()

    def _edit_batch(self, batch_id: str) -> None:
        if self.current_project is None:
            self.set_status("No project loaded.")
            return
        try:
            batch = self.service.repo.load_batch(self.current_project.project_id, batch_id)
        except Exception as exc:
            self.set_status(f"Edit Batch failed for {batch_id}", detail=str(exc))
            return
        self.batch_page.load_from_batch(batch)
        self._on_batch_draft_changed(self.batch_page._payload(include_name=False))
        self.stack.setCurrentWidget(self.batch_page)
        self.set_status(f"Batch loaded: {batch_id}")

    def _clone_batch(self, batch_id: str) -> None:
        if self.current_project is None:
            self.set_status("No project loaded.")
            return
        try:
            batch = self.service.repo.load_batch(self.current_project.project_id, batch_id)
        except Exception as exc:
            self.set_status(f"Clone Batch failed for {batch_id}", detail=str(exc))
            return
        source_name = str(batch.extra.get("batch_name", batch.batch_id)).strip() or batch.batch_id
        clone_name = f"{source_name} Clone"
        self.batch_page.load_from_batch(batch, batch_name=clone_name)
        self._on_batch_draft_changed(self.batch_page._payload(include_name=False))
        self.stack.setCurrentWidget(self.batch_page)
        self.set_status(f"Batch cloned into draft: {clone_name}")

    def _export_version(self, batch_id: str, version_id: str, export_stl: bool, export_abec: bool) -> None:
        if self.current_project is None:
            self.set_status("No project loaded.")
            return
        try:
            result = self.service.export_version(
                project_id=self.current_project.project_id,
                batch_id=batch_id,
                version_id=version_id,
                export_stl=export_stl,
                export_abec=export_abec,
            )
        except Exception as exc:
            self.set_status(f"Export failed for {version_id}", detail=str(exc))
            return
        self.set_status(f"Export finished for {version_id}", detail=json.dumps(result, indent=2, ensure_ascii=False))

    def _queue_project_draft_changed(self, payload: Dict[str, object]) -> None:
        self._pending_project_payload = dict(payload)
        self.project_page.set_validation_phase("validating")
        self._project_validation_timer.start()

    def _flush_project_draft_validation(self) -> None:
        payload = self._pending_project_payload
        self._pending_project_payload = None
        if payload is None:
            payload = self.project_page._raw_constraints_payload()
        self._on_project_draft_changed(payload)

    def _queue_batch_draft_changed(self, payload: Dict[str, object]) -> None:
        self._pending_batch_payload = dict(payload)
        self._batch_validation_timer.start()

    def _flush_batch_draft_validation(self) -> None:
        payload = self._pending_batch_payload
        self._pending_batch_payload = None
        if payload is None:
            payload = self.batch_page._payload(include_name=False)
        self._on_batch_draft_changed(payload)

    def _on_project_draft_changed(self, payload: Dict[str, object]) -> None:
        runner_mode = DEFAULT_RUNNER_MODE
        if self.current_project is not None:
            runner_mode = self.current_project.constraints.runner_mode
        constraints_payload = {
            "fixed_params": dict(payload.get("fixed_params", {}) or {}),
            "limits": dict(payload.get("limits", {}) or {}),
            "param_states": [item for item in list(payload.get("param_states", []) or []) if isinstance(item, dict)],
            "runner_mode": runner_mode,
        }
        state_raw = self.service.evaluate_project_constraints(constraints_payload)
        ui_state = self.compat_ui_adapter.compute_project_ui_state(
            draft_payload=constraints_payload,
            compat_state=state_raw,
            evaluate_constraints=self.service.evaluate_project_constraints,
            last_changed_key=self.project_page.constraints_form.last_changed_key(),
        )
        state = dict(state_raw)
        state["compat_ui_state"] = ui_state
        self.project_page.apply_compatibility(state)
        if not self._project_reconcile_guard:
            reconciled_payload = self.project_page._raw_constraints_payload()
            reconciled_constraints = {
                "fixed_params": dict(reconciled_payload.get("fixed_params", {}) or {}),
                "limits": dict(reconciled_payload.get("limits", {}) or {}),
                "param_states": [item for item in list(reconciled_payload.get("param_states", []) or []) if isinstance(item, dict)],
                "runner_mode": runner_mode,
            }
            if reconciled_constraints != constraints_payload:
                self._project_reconcile_guard = True
                try:
                    self._on_project_draft_changed(reconciled_payload)
                finally:
                    self._project_reconcile_guard = False
                return
        visible_keys = set(str(item) for item in list(state.get("visible_keys", []) or []))
        issues = [item for item in list(state.get("issues", []) or []) if isinstance(item, dict)]
        field_issues = self.ui_validation.evaluate(
            draft_payload=constraints_payload,
            validation_state=state,
            visible_keys=visible_keys,
        )
        self.project_page.apply_ui_risks(field_issues)
        _ = issues

    def _on_batch_draft_changed(self, payload: Dict[str, object]) -> None:
        if self.current_project is None:
            self._cancel_pending_preview_update()
            self._stop_preview_worker()
            self.batch_page.set_project_fixed_keys([])
            self.batch_page.apply_compatibility(
                {
                    "visible_keys": [],
                    "locked_keys": [],
                    "sweepable_keys": [],
                    "compat_ui_state": {},
                    "issues": [],
                }
            )
            self.batch_page.apply_ui_risks([])
            self.batch_page.set_eta(None, sample_count=0, median_seconds=None)
            self.batch_page.set_preview_busy(False)
            self.batch_page.set_preview_parameters({})
            self.batch_page.preview_panel.set_info_message("No preview mesh loaded.")
            return
        raw_payload = dict(payload)
        raw_sweep_mode = str(raw_payload.get("sweep_mode", "single"))
        raw_selected_params = dict(raw_payload.get("selected_params", {}) or {})
        raw_sweeps = dict(raw_payload.get("sweeps", {}) or {})
        state_raw = self.service.evaluate_batch_definition(
            project_id=self.current_project.project_id,
            selected_params=raw_selected_params,
            sweeps=raw_sweeps,
            sweep_mode=raw_sweep_mode,
        )
        sanitized_payload, _changed = self._sanitize_batch_payload_for_project_constraints(
            raw_payload,
            self.current_project.constraints,
            state_raw,
        )
        payload = sanitized_payload
        sweep_mode = str(payload.get("sweep_mode", "single"))
        selected_params = dict(payload.get("selected_params", {}) or {})
        sweeps = dict(payload.get("sweeps", {}) or {})
        if selected_params != raw_selected_params or sweeps != raw_sweeps or sweep_mode != raw_sweep_mode:
            state_raw = self.service.evaluate_batch_definition(
                project_id=self.current_project.project_id,
                selected_params=selected_params,
                sweeps=sweeps,
                sweep_mode=sweep_mode,
            )
        ui_state = self.compat_ui_adapter.compute_batch_ui_state(
            selected_params=selected_params,
            sweeps=sweeps,
            sweep_mode=sweep_mode,
            compat_state=state_raw,
            project_constraints=self.current_project.constraints.to_dict(),
            evaluate_batch=lambda sel, sw, mode: self.service.evaluate_batch_definition(
                project_id=self.current_project.project_id,
                selected_params=sel,
                sweeps=sw,
                sweep_mode=mode,
            ),
            last_changed_key=self.batch_page.parameter_form.last_changed_key(),
        )
        state = dict(state_raw)
        state["compat_ui_state"] = ui_state
        self.batch_page.apply_compatibility(state)
        if not self._batch_reconcile_guard:
            reconciled_payload = self.batch_page._payload(include_name=False)
            reconciled_selected = dict(reconciled_payload.get("selected_params", {}) or {})
            reconciled_sweeps = dict(reconciled_payload.get("sweeps", {}) or {})
            if reconciled_selected != selected_params or reconciled_sweeps != sweeps:
                self._batch_reconcile_guard = True
                try:
                    self._on_batch_draft_changed(reconciled_payload)
                finally:
                    self._batch_reconcile_guard = False
                return

        project_constraints = self.current_project.constraints.to_dict()
        draft_fixed = dict(project_constraints.get("fixed_params", {}) or {})
        draft_limits = dict(project_constraints.get("limits", {}) or {})
        draft_param_states = [
            dict(item)
            for item in list(project_constraints.get("param_states", []) or [])
            if isinstance(item, dict)
        ]
        for key, value in selected_params.items():
            key_s = str(key).strip()
            if not key_s:
                continue
            if value is not None:
                draft_fixed[key_s] = value
            draft_param_states.append(
                {
                    "param_name": key_s,
                    "is_set": 1 if value is not None else 0,
                    "value": value if value is not None else None,
                }
            )
        batch_draft_payload = {
            "fixed_params": draft_fixed,
            "limits": draft_limits,
            "param_states": draft_param_states,
            "runner_mode": project_constraints.get("runner_mode", DEFAULT_RUNNER_MODE),
        }
        visible_keys = set(str(item) for item in list(state.get("visible_keys", []) or []))
        batch_field_issues_raw = self.ui_validation.evaluate(
            draft_payload=batch_draft_payload,
            validation_state=state,
            visible_keys=visible_keys,
        )
        batch_field_issues = self._normalize_batch_issues_for_ui(
            [dict(item) for item in list(batch_field_issues_raw or []) if isinstance(item, dict)],
            selected_params=selected_params,
        )
        export_validation_issues = [dict(item) for item in list(self.batch_page.export_panel.validation_issues() or [])]
        self.batch_page.apply_ui_risks([*batch_field_issues, *export_validation_issues])

        estimate = self.service.estimate_batch_runtime(
            project_id=self.current_project.project_id,
            selected_params=selected_params,
            sweeps=sweeps,
            sweep_mode=sweep_mode,
            validation_state=state,
        )
        self.batch_page.set_eta(
            estimate.get("eta_seconds"),
            sample_count=int(estimate.get("sample_count", 0) or 0),
            median_seconds=estimate.get("median_seconds_per_version"),
        )
        if self.isVisible() and self.stack.currentWidget() is self.batch_page:
            self._queue_batch_preview_update(
                {
                    "selected_params": selected_params,
                    "sweep_mode": sweep_mode,
                }
            )
        self.batch_page.set_preview_parameters(selected_params)


class GuiController:
    def __init__(self, service: OrchestratorService) -> None:
        self.service = service
        self.project_manager = ProjectManagerWindow(service)
        self.main_window = MainWindow(service)
        self.project_manager.open_project.connect(self._open_project)
        self.project_manager.create_project.connect(self._new_project)
        self.main_window.set_project_manager_handler(self._open_project_manager_from_main)

    def show_project_manager(self) -> None:
        self.project_manager.refresh()
        self._show_window_normal_foreground(self.project_manager)

    def _show_main_window_maximized(self) -> None:
        self._show_window_maximized_foreground(self.main_window)

    @staticmethod
    def _show_window_normal_foreground(window: QMainWindow) -> None:
        _center_window(window)
        window.show()
        apply_windows_dark_titlebar(window)
        _ensure_normal_foreground(window)

    @staticmethod
    def _show_window_maximized_foreground(window: QMainWindow) -> None:
        window.show()
        apply_windows_dark_titlebar(window)
        _ensure_maximized_foreground(window)

    def _open_project(self, project_id: str) -> None:
        project = self.service.repo.load_project(project_id)
        self.main_window.load_project(project)
        self._show_main_window_maximized()
        self.project_manager.hide()

    def _new_project(self) -> None:
        self.main_window.current_project = None
        self.main_window.project_page.set_constraints_locked(False)
        self._show_main_window_maximized()
        self.main_window.show_project()
        self.project_manager.hide()

    def _open_project_manager_from_main(self) -> None:
        self.project_manager.refresh()
        self._show_window_normal_foreground(self.project_manager)
        self.main_window.hide()


def _make_splash(app: QApplication) -> QSplashScreen:
    width, height = 760, 360
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, float(width), float(height), 18.0, 18.0)
    painter.fillPath(path, QColor("#101010"))
    painter.setPen(QColor("#F1F1F1"))
    font = QFont("Condor", 58)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "WUT BATCHER")
    painter.end()
    splash = QSplashScreen(pixmap, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
    splash.setAttribute(Qt.WA_TranslucentBackground, True)
    splash.show()
    app.processEvents()
    return splash


def _run_doctor_for_splash(service: OrchestratorService) -> Dict[str, object]:
    settings = service.settings
    config = AppConfig(projects_root=settings.library_root)
    report = run_doctor_checks(
        config,
        config_path=None,
        fix=False,
        kill_zombies=False,
        report_path=None,
        tool_paths={
            "ath_exe": settings.ath_exe,
            "akabak_exe": settings.akabak_exe,
            "vacs_exe": settings.vacs_exe,
        },
    )
    tool_versions: Dict[str, str] = {}
    for key, exe_path in {
        "ath": settings.ath_exe,
    }.items():
        if not exe_path:
            continue
        try:
            result = subprocess.run(
                [exe_path, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4,
                check=False,
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0),
            )
            text = (result.stdout or result.stderr or "").strip().splitlines()
            if text:
                tool_versions[key] = text[0]
        except Exception:
            continue
    return {
        "overall_status": report.overall_status,
        "checks": [check.__dict__ for check in report.checks],
        "tool_versions": tool_versions,
    }


def launch_gui() -> int:
    configure_windows_qt_darkmode_env()
    app = QApplication.instance() or QApplication([])
    apply_theme(app)

    service = OrchestratorService()
    splash = _make_splash(app)
    doctor_payload = _run_doctor_for_splash(service)
    controller = GuiController(service)
    doctor_status = str(doctor_payload["overall_status"]).lower()
    if doctor_status in {"fail", "warn"}:
        controller.main_window.set_status(
            f"Doctor {doctor_status}: click for details",
            detail=json.dumps(doctor_payload, indent=2, ensure_ascii=False),
        )
    else:
        controller.main_window.set_status(
            "Doctor ok.",
            detail=json.dumps(doctor_payload, indent=2, ensure_ascii=False),
        )
    splash.finish(controller.project_manager)
    controller.show_project_manager()
    return app.exec()


def main() -> int:
    from app.audit_mode import enable_audit_mode

    enable_audit_mode(entrypoint="app.gui.main")
    return int(launch_gui())
