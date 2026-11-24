"""Optional minimal Qt UI; all USB and file work runs outside the UI thread."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fs21.audio import ExportResult, write_wav
from fs21.device import FS21
from fs21.transport import USBTransport, connected_addresses

log = logging.getLogger(__name__)


class Task(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(
        self, action: str, timeout: int, retries: int, address: tuple[int, int] | None,
        path: Path | None = None, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.action, self.timeout, self.retries = action, timeout, retries
        self.address, self.path = address, path

    def run(self) -> None:
        try:
            if self.action == "probe":
                addresses = list(connected_addresses())
                if self.address:
                    addresses = [a for a in addresses if a == self.address]
                self.result.emit(addresses)
                return
            with USBTransport(self.timeout, self.retries, self.address) as transport:
                device = FS21(transport)
                if self.action == "info":
                    self.result.emit(device.info())
                elif self.action == "export" and self.path is not None:
                    result = write_wav(device.blocks(1), self.path, self.progress.emit)
                    self.result.emit(result)
                else:
                    raise ValueError("Unknown GUI action.")
        except Exception as exc:
            log.exception("GUI %s failed", self.action)
            self.failed.emit(str(exc))


class Window(QMainWindow):
    def __init__(
        self, timeout: int = 3000, retries: int = 2,
        address: tuple[int, int] | None = None,
    ) -> None:
        super().__init__()
        self.timeout, self.retries, self.address = timeout, retries, address
        self.task: Task | None = None
        self.connected = False
        self._addresses: list[tuple[int, int]] | None = None
        self.setWindowTitle(self.tr("FS21 — Read-only exporter"))
        self.resize(640, 430)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        title = QLabel(self.tr("FLAMMA FS21"))
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(title)
        self.status = QLabel(self.tr("Checking USB connection…"))
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton(self.tr("Refresh connection"))
        self.info_button = QPushButton(self.tr("Read device info"))
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.info_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.metadata = QLabel(
            self.tr("Recording name and duration are not verified by the protocol.")
        )
        self.metadata.setWordWrap(True)
        layout.addWidget(self.metadata)
        form = QFormLayout()
        self.slots = QComboBox()
        self.slots.addItem(self.tr("Slot 1 — captured wire selector 0x00"), 1)
        form.addRow(self.tr("Recording"), self.slots)
        self.output = QLineEdit()
        self.output.setPlaceholderText(str(Path.cwd() / "recordings" / "slot-1.wav"))
        self.output.setAccessibleName(self.tr("Output WAV path"))
        form.addRow(self.tr("Save WAV to"), self.output)
        layout.addLayout(form)
        note = QLabel(self.tr(
            "Up to 162 blocks are supported. This minimal GUI exports bank 1; "
            "use the CLI for other banks or export-all. Existing files are never replaced."
        ))
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.export_button = QPushButton(self.tr("Export WAV"))
        self.export_all_button = QPushButton(self.tr("Export all (use CLI)"))
        self.export_all_button.setEnabled(False)
        self.export_all_button.setToolTip("fs21 export-all --output ./fs21-export")
        actions.addWidget(self.export_button)
        actions.addWidget(self.export_all_button)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.help = QLabel(self.tr(
            "Shortcuts: Space — export · Ctrl+R — refresh · ? — help · Q / Ctrl+Q — quit"
        ))
        self.help.setWordWrap(True)
        self.help.hide()
        layout.addWidget(self.help)
        layout.addStretch()
        self.setCentralWidget(content)
        self.refresh_button.clicked.connect(self.refresh)
        self.info_button.clicked.connect(self.read_info)
        self.export_button.clicked.connect(self.export)
        self.output.textChanged.connect(self.update_buttons)
        for key, callback in [
            ("Ctrl+R", self.refresh), ("Ctrl+E", self.export),
            ("Ctrl+Q", self.close),
        ]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
        self.text_shortcuts = []
        for key, callback in [
            ("Space", self.export), ("Q", self.close), ("?", self.toggle_help),
        ]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self.text_shortcuts.append(shortcut)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.focusChanged.connect(self.focus_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.update_buttons()
        QTimer.singleShot(0, self.refresh)

    def toggle_help(self) -> None:
        self.help.setVisible(self.help.isHidden())

    def focus_changed(self, previous: QWidget | None, current: QWidget | None) -> None:
        for shortcut in self.text_shortcuts:
            shortcut.setEnabled(current is not self.output)

    def update_buttons(self) -> None:
        busy = self.task is not None
        self.refresh_button.setEnabled(not busy)
        self.info_button.setEnabled(self.connected and not busy)
        self.export_button.setEnabled(
            self.connected and not busy and bool(self.output.text().strip())
        )
        self.output.setEnabled(not busy)
        self.slots.setEnabled(not busy)

    def refresh(self) -> None:
        if self.task is None:
            self.start_task("probe")

    def read_info(self) -> None:
        if self.task is None and self.connected:
            self.start_task("info")

    def export(self) -> None:
        if not self.export_button.isEnabled():
            return
        path = Path(self.output.text().strip()).expanduser()
        if path.exists():
            self.error.setText(self.tr("That file already exists. Choose a new output path."))
            return
        self.progress.setValue(0)
        self.status.setText(self.tr("Exporting — keep the pedal idle."))
        self.start_task("export", path)

    def start_task(self, action: str, path: Path | None = None) -> None:
        if action != "probe":
            self.error.clear()
        task = Task(action, self.timeout, self.retries, self.address, path, self)
        self.task = task
        task.result.connect(lambda result: self.task_result(action, result))
        task.failed.connect(self.error.setText)
        task.progress.connect(self.transfer_progress)
        task.finished.connect(self.task_finished)
        self.update_buttons()
        task.start()

    def transfer_progress(self, done: int, total: int) -> None:
        self.progress.setValue(done * 100 // total)
        self.status.setText(self.tr("Receiving block %1/%2").replace("%1", str(done))
                            .replace("%2", str(total)))

    def task_result(self, action: str, result: Any) -> None:
        if action == "probe":
            addresses: list[tuple[int, int]] = result
            self.connected = len(addresses) == 1
            if addresses == self._addresses:
                return
            self._addresses = addresses
            if self.connected:
                bus, address = addresses[0]
                self.status.setText(self.tr("USB detected at %1:%2 — access checked on export.")
                                    .replace("%1", str(bus)).replace("%2", str(address)))
            elif addresses:
                self.status.setText(self.tr("Multiple pedals found; launch with --usb-address."))
            else:
                self.status.setText(self.tr("FS21 not connected in normal USB mode."))
        elif action == "info":
            self.metadata.setText(
                f"{result['model']} · {result['version_1']} / {result['version_2']} / "
                f"{result['version_3']}"
            )
            self.status.setText(self.tr("Connected — identification verified."))
        elif isinstance(result, ExportResult):
            self.status.setText(self.tr("Export complete — %1 seconds.")
                                .replace("%1", f"{result.duration:.3f}"))
            self.metadata.setText(str(result.path))

    def task_finished(self) -> None:
        if self.task:
            if self.error.text():
                self.status.setText(self.tr("Operation failed — see the error below."))
            self.task.deleteLater()
            self.task = None
        self.update_buttons()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.task is not None:
            self.error.setText(self.tr(
                "A USB operation is still running. Wait for it to finish before closing; "
                "no safe abort command has been verified."
            ))
            event.ignore()
        else:
            self.timer.stop()
            event.accept()


def run(timeout: int, retries: int, address: tuple[int, int] | None) -> int:
    app = QApplication([sys.argv[0]])
    app.setApplicationName("fs21")
    app.setDesktopFileName("fs21")
    window = Window(timeout, retries, address)
    window.show()
    return app.exec()
