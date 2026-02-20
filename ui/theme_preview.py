"""Theme preview window for visual validation."""

from __future__ import annotations

import sys

from ui.theme import apply_theme, apply_windows_dark_titlebar, configure_windows_qt_darkmode_env

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ThemePreviewWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WUT Batcher Theme Preview")
        self.resize(1100, 760)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        title = QLabel("Theme Preview")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._build_controls_tab(), "Controls")
        tabs.addTab(self._build_data_tab(), "Data")
        root.addWidget(tabs, 1)

    def _build_controls_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)

        group = QGroupBox("Buttons + Inputs")
        layout = QVBoxLayout(group)

        row = QHBoxLayout()
        primary = QPushButton("Primary Action")
        secondary = QPushButton("Secondary")
        disabled = QPushButton("Disabled")
        disabled.setEnabled(False)
        row.addWidget(primary)
        row.addWidget(secondary)
        row.addWidget(disabled)
        row.addStretch(1)
        layout.addLayout(row)

        input_row = QHBoxLayout()
        line = QLineEdit()
        line.setPlaceholderText("Type something ...")
        check = QCheckBox("Enable option")
        input_row.addWidget(line, 2)
        input_row.addWidget(check, 1)
        layout.addLayout(input_row)

        text = QTextEdit()
        text.setPlaceholderText("Long-form text area")
        layout.addWidget(text)

        root.addWidget(group)
        root.addStretch(1)
        return page

    def _build_data_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)

        group = QGroupBox("Table Preview")
        layout = QVBoxLayout(group)

        table = QTableView()
        model = QStandardItemModel(6, 5)
        model.setHorizontalHeaderLabels(["Project", "Batch", "Version", "Status", "Duration"])
        for row in range(6):
            model.setItem(row, 0, QStandardItem("P001"))
            model.setItem(row, 1, QStandardItem(f"B{row+1:03d}"))
            model.setItem(row, 2, QStandardItem(f"V{row+1:03d}"))
            model.setItem(row, 3, QStandardItem("completed" if row % 2 == 0 else "running"))
            model.setItem(row, 4, QStandardItem(f"{2.3 + row:.1f}s"))
        table.setModel(model)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        root.addWidget(group)
        return page


def launch_preview() -> int:
    configure_windows_qt_darkmode_env()
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    window = ThemePreviewWindow()
    window.show()
    apply_windows_dark_titlebar(window)
    return int(app.exec())


def main() -> int:
    from app.audit_mode import enable_audit_mode

    enable_audit_mode(entrypoint="ui.theme_preview.main")
    return launch_preview()


if __name__ == "__main__":
    raise SystemExit(main())

