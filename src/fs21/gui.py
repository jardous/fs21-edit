"""Forty-slot Qt exporter; USB and file work run outside the UI thread."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fs21.audio import ExportResult, write_wav
from fs21.device import FS21
from fs21.errors import EmptyBank, UnsupportedOperation, UnsupportedRecording
from fs21.exporter import BankResult, BatchReport, export_all
from fs21.protocol import BANK_COUNT, CAPTURED_BANKS, MAX_VALIDATED_BLOCKS
from fs21.transport import USBTransport, connected_addresses

log = logging.getLogger(__name__)


class Task(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)
    bank_progress = Signal(int, int, int)
    bank_started = Signal(int)
    bank_result = Signal(object)

    def __init__(
        self, action: str, timeout: int, retries: int, address: tuple[int, int] | None,
        path: Path | None = None, parent: QWidget | None = None,
        *, bank: int = 1, recover: bool = False,
    ) -> None:
        super().__init__(parent)
        self.action, self.timeout, self.retries = action, timeout, retries
        self.address, self.path = address, path
        self.bank, self.recover = bank, recover

    def factory(self) -> USBTransport:
        return USBTransport(self.timeout, self.retries, self.address)

    def notify(self, done: int, total: int) -> None:
        self.progress.emit(done, total)
        self.bank_progress.emit(self.bank, done, total)

    def bank_outcome(
        self, status: str, error: str, blocks: int | None = None,
    ) -> None:
        if self.path is None:
            self.failed.emit(error)
            return
        # Status values are kept explicit so an empty/unsupported bank is not
        # reported as a successful WAV or confused with a transport failure.
        if status == "empty":
            result = BankResult(self.bank, "empty", self.path, 0, error=error)
        elif status == "unsupported":
            result = BankResult(self.bank, "unsupported", self.path, blocks, error=error)
        else:
            result = BankResult(self.bank, "failed", self.path, error=error)
        self.bank_result.emit(result)
        self.result.emit(result)

    def run(self) -> None:
        try:
            if self.action == "probe":
                addresses = list(connected_addresses())
                if self.address:
                    addresses = [a for a in addresses if a == self.address]
                self.result.emit(addresses)
                return
            if self.action == "export-all":
                if self.path is None:
                    raise ValueError("An export folder is required.")
                report = export_all(
                    self.path, self.factory,
                    progress=self.bank_progress.emit, on_result=self.bank_result.emit,
                    on_start=self.bank_started.emit, recover_first=self.recover,
                )
                self.result.emit(report)
                return
            if self.action == "export":
                if self.path is None:
                    raise ValueError("An output WAV path is required.")
                self.bank_started.emit(self.bank)
            with self.factory() as transport:
                if self.recover:
                    transport.drain_input()
                device = FS21(transport)
                if self.action == "info":
                    self.result.emit(device.info())
                elif self.action == "export" and self.path is not None:
                    result = write_wav(device.blocks(self.bank), self.path, self.notify)
                else:
                    raise ValueError("Unknown GUI action.")
            if self.action == "export":
                self.bank_result.emit(BankResult(
                    self.bank, "exported", result.path, result.blocks, result.audio_bytes
                ))
                self.result.emit(result)
        except EmptyBank as exc:
            self.bank_outcome("empty", str(exc))
        except UnsupportedRecording as exc:
            self.bank_outcome("unsupported", str(exc), exc.blocks)
        except UnsupportedOperation as exc:
            if self.action == "export":
                self.bank_outcome("unsupported", str(exc))
            else:
                self.failed.emit(str(exc))
        except Exception as exc:
            log.exception("GUI %s failed", self.action)
            if self.action == "export":
                self.bank_outcome("failed", str(exc))
            self.failed.emit(str(exc))


class Window(QMainWindow):
    def __init__(
        self, timeout: int = 3000, retries: int = 2,
        address: tuple[int, int] | None = None,
    ) -> None:
        super().__init__()
        self.timeout, self.retries, self.address = timeout, retries, address
        self.task: Task | None = None
        self.probe_task: Task | None = None
        self._close_pending = False
        self.connected = False
        self._addresses: list[tuple[int, int]] | None = None
        self.bank_results: dict[int, BankResult] = {}
        self.export_buttons: dict[int, QPushButton] = {}
        self.active_bank: int | None = None
        self._completed_banks: set[int] = set()
        self._batch_active = False
        self._needs_recovery = False
        self._operation_failed = False
        self.setWindowTitle(self.tr("FS21 — Read-only exporter"))
        self.resize(900, 760)
        self.setMinimumSize(700, 480)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel(self.tr("FLAMMA FS21"))
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()
        self.refresh_button = QPushButton(self.tr("Refresh connection"))
        self.info_button = QPushButton(self.tr("Read device info"))
        header.addWidget(self.refresh_button)
        header.addWidget(self.info_button)
        layout.addLayout(header)
        self.connection_status = QLabel(self.tr("Checking USB connection…"))
        self.connection_status.setWordWrap(True)
        layout.addWidget(self.connection_status)
        self.metadata = QLabel(self.tr("44.1 kHz · 24-bit stereo PCM · read-only USB export"))
        self.metadata.setWordWrap(True)
        layout.addWidget(self.metadata)
        form = QFormLayout()
        self.output = QLineEdit()
        self.output.setText(str(Path.cwd() / "recordings"))
        self.output.setAccessibleName(self.tr("Export folder"))
        self.output.setToolTip(self.tr("Exports are named bank-01.wav through bank-40.wav."))
        form.addRow(self.tr("Export folder"), self.output)
        layout.addLayout(form)
        note = QLabel(self.tr(
            "Slots 1–4 and 8 have captured selectors; the others use inferred read commands. "
            "Up to %1 blocks per recording. Existing files are kept."
        ).replace("%1", str(MAX_VALIDATED_BLOCKS)))
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        actions.addWidget(QLabel(self.tr("Recording slots")))
        actions.addStretch()
        self.export_all_button = QPushButton(self.tr("Export all 40 slots"))
        self.export_all_button.setToolTip(
            self.tr("Continue after errors and skip files that already exist.")
        )
        actions.addWidget(self.export_all_button)
        layout.addLayout(actions)
        self.slots = QTableWidget(BANK_COUNT, 4)
        self.slots.setAccessibleName(self.tr("Recording slots and export actions"))
        self.slots.setHorizontalHeaderLabels([
            self.tr("Slot"), self.tr("Read command"), self.tr("Status"), self.tr("Export"),
        ])
        self.slots.verticalHeader().hide()
        self.slots.verticalHeader().setDefaultSectionSize(36)
        self.slots.setAlternatingRowColors(True)
        self.slots.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.slots.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.slots.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.slots.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.slots.setMinimumHeight(150)
        columns = self.slots.horizontalHeader()
        for column, width in ((0, 75), (1, 130), (3, 110)):
            columns.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.slots.setColumnWidth(column, width)
        columns.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for bank in range(1, BANK_COUNT + 1):
            row = bank - 1
            label = QTableWidgetItem(f"{bank:02d}")
            label.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.slots.setItem(row, 0, label)
            captured = bank in CAPTURED_BANKS
            evidence = QTableWidgetItem(self.tr("Captured") if captured else self.tr("Inferred"))
            evidence.setToolTip(self.tr(
                "Read selector 0x%1. This is protocol evidence, not recording occupancy."
            ).replace("%1", f"{row:02X}"))
            self.slots.setItem(row, 1, evidence)
            self.slots.setItem(row, 2, QTableWidgetItem(self.tr("Not checked")))
            button = QPushButton(self.tr("Export"))
            button.setObjectName(f"export_bank_{bank:02d}")
            button.setAccessibleName(self.tr("Export slot %1").replace("%1", str(bank)))
            button.clicked.connect(lambda checked=False, bank=bank: self.export_bank(bank))
            self.export_buttons[bank] = button
            self.slots.setCellWidget(row, 3, button)
        self.slots.selectRow(0)
        layout.addWidget(self.slots, 1)
        self.details = QLabel(self.tr(
            "Select a slot for details. Recording occupancy is not read in advance."
        ))
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)
        self.status = QLabel(self.tr("Choose an export folder, then export a slot or all 40."))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat(self.tr("No active transfer"))
        layout.addWidget(self.progress)
        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, BANK_COUNT)
        self.batch_progress.setFormat(self.tr("%v / %m slots processed"))
        self.batch_progress.hide()
        layout.addWidget(self.batch_progress)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.hide()
        layout.addWidget(self.summary)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.error)
        self.help = QLabel(self.tr(
            "Space / Ctrl+E — export selected slot · Ctrl+Shift+E — export all · "
            "Ctrl+R — refresh · ? — help · Q / Ctrl+Q — quit"
        ))
        self.help.setWordWrap(True)
        self.help.hide()
        layout.addWidget(self.help)
        self.setCentralWidget(content)
        self.refresh_button.clicked.connect(self.refresh)
        self.info_button.clicked.connect(self.read_info)
        self.export_all_button.clicked.connect(self.export_all)
        self.output.textChanged.connect(self.update_buttons)
        self.slots.currentCellChanged.connect(self.show_selected_details)
        for key, callback in [
            ("Ctrl+R", self.refresh), ("Ctrl+E", self.export),
            ("Ctrl+Shift+E", self.export_all),
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
            # Let Space activate a focused row's own button rather than the
            # table's previously selected slot (or hijack Export all).
            native_button_space = (
                isinstance(current, QPushButton) and shortcut.key() == QKeySequence("Space")
            )
            shortcut.setEnabled(current is not self.output and not native_button_space)

    def update_buttons(self) -> None:
        busy = self.task is not None or self._close_pending
        can_export = self.connected and not busy and bool(self.output.text().strip())
        self.refresh_button.setEnabled(not busy)
        self.info_button.setEnabled(self.connected and not busy)
        self.export_all_button.setEnabled(can_export)
        for button in self.export_buttons.values():
            button.setEnabled(can_export)
        self.output.setEnabled(not busy)

    def refresh(self) -> None:
        if self.task is not None or self.probe_task is not None or self._close_pending:
            return
        # Descriptor-only detection must not enter the foreground busy state:
        # disabling an editor, even briefly, makes Qt move its keyboard focus.
        task = Task("probe", self.timeout, self.retries, self.address, parent=self)
        self.probe_task = task
        task.result.connect(self.probe_result)
        task.failed.connect(self.probe_failed)
        task.finished.connect(self.probe_finished)
        task.start()

    @Slot(object)
    def probe_result(self, addresses: list[tuple[int, int]]) -> None:
        self.task_result("probe", addresses)

    @Slot(str)
    def probe_failed(self, message: str) -> None:
        was_connected = self.connected
        self.connected = False
        # A later successful probe must restore the label even if the device
        # address is the same as before this error.
        self._addresses = None
        self.connection_status.setText(
            self.tr("Could not check the USB connection: %1").replace("%1", message)
        )
        if was_connected:
            self.update_buttons()

    @Slot()
    def probe_finished(self) -> None:
        if self.probe_task is not None:
            self.probe_task.deleteLater()
            self.probe_task = None
        if self._close_pending:
            self.close()

    def read_info(self) -> None:
        if self.task is None and self.connected:
            self.start_task("info")

    def export(self) -> None:
        row = self.slots.currentRow()
        if row >= 0:
            self.export_bank(row + 1)

    def export_directory(self) -> Path | None:
        text = self.output.text().strip()
        if not text:
            self.error.setText(self.tr("Enter an export folder."))
            return None
        try:
            directory = Path(text).expanduser()
            if directory.exists() and not directory.is_dir():
                self.error.setText(self.tr("The export folder is a file. Choose a directory."))
                return None
        except OSError as exc:
            self.error.setText(str(exc))
            return None
        return directory

    def export_bank(self, bank: int) -> None:
        if bank not in self.export_buttons or not self.export_buttons[bank].isEnabled():
            return
        directory = self.export_directory()
        if directory is None:
            return
        path = directory / f"bank-{bank:02d}.wav"
        self.slots.selectRow(bank - 1)
        if path.exists():
            self.apply_bank_result(BankResult(bank, "skipped", path))
            self.status.setText(self.tr("Slot %1 · existing file kept.")
                                .replace("%1", f"{bank:02d}"))
            self.progress.setValue(0)
            self.progress.setFormat(self.tr("No active transfer"))
            self.error.setText(self.tr(
                "That file already exists and was kept. Choose another export folder: %1"
            ).replace("%1", str(path)))
            return
        self._batch_active = False
        self.batch_progress.hide()
        self.summary.clear()
        self.summary.hide()
        self.bank_results.pop(bank, None)
        self.active_bank = bank
        self.progress.setValue(0)
        self.start_task("export", path, bank=bank)

    def export_all(self) -> None:
        if not self.export_all_button.isEnabled():
            return
        directory = self.export_directory()
        if directory is None:
            return
        self.bank_results.clear()
        self._completed_banks.clear()
        self._batch_active = True
        self.summary.clear()
        self.summary.hide()
        self.progress.setValue(0)
        self.batch_progress.setValue(0)
        self.batch_progress.show()
        for bank in range(1, BANK_COUNT + 1):
            self.set_slot_status(bank, self.tr("Queued"))
        self.status.setText(self.tr("Exporting all 40 slots — keep the pedal idle."))
        self.start_task("export-all", directory)

    def start_task(self, action: str, path: Path | None = None, *, bank: int = 1) -> None:
        if action == "probe":
            self.refresh()
            return
        if self.task is not None or self._close_pending:
            return
        self._operation_failed = False
        self.error.clear()
        task = Task(
            action, self.timeout, self.retries, self.address, path, self,
            bank=bank, recover=self._needs_recovery,
        )
        self.task = task
        task.result.connect(self.receive_task_result)
        task.failed.connect(self.task_failed)
        task.bank_started.connect(self.mark_bank_started)
        task.bank_result.connect(self.apply_bank_result)
        task.bank_progress.connect(self.transfer_progress)
        task.finished.connect(self.task_finished)
        self.update_buttons()
        task.start()

    @Slot(object)
    def receive_task_result(self, result: Any) -> None:
        if self.task is not None:
            self.task_result(self.task.action, result)

    @Slot(int)
    def mark_bank_started(self, bank: int) -> None:
        self.active_bank = bank
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.set_slot_status(bank, self.tr("Processing…"))
        self.status.setText(self.tr("Exporting slot %1 — keep the pedal idle.")
                            .replace("%1", f"{bank:02d}"))
        self.slots.selectRow(bank - 1)
        item = self.slots.item(bank - 1, 0)
        if item is not None:
            self.slots.scrollToItem(item)

    @Slot(int, int, int)
    def transfer_progress(self, bank: int, done: int, total: int) -> None:
        if not 1 <= bank <= BANK_COUNT or total <= 0:
            return
        self.active_bank = bank
        self.progress.setValue(done * 100 // total)
        self.progress.setFormat("%p%")
        block_status = self.tr("Receiving block %1/%2").replace("%1", str(done))
        block_status = block_status.replace("%2", str(total))
        self.set_slot_status(bank, block_status)
        self.status.setText(self.tr("Slot %1 · %2").replace("%1", f"{bank:02d}")
                            .replace("%2", block_status))

    def set_slot_status(self, bank: int, text: str, detail: str = "") -> None:
        item = self.slots.item(bank - 1, 2)
        if item is not None:
            item.setText(text)
            item.setToolTip(detail)

    def outcome_text(self, result: BankResult) -> str:
        if result.status == "exported":
            return self.tr("Exported · %1 blocks · %2 s").replace("%1", str(result.blocks)).replace(
                "%2", f"{result.audio_bytes / 6 / 44100:.3f}"
            )
        if result.status == "empty":
            return self.tr("Empty")
        if result.status == "skipped":
            return self.tr("Existing file kept")
        if result.status == "unsupported":
            if result.blocks is not None:
                return self.tr("Unsupported (%1 blocks)").replace("%1", str(result.blocks))
            return self.tr("Unsupported")
        return self.tr("Failed")

    @Slot(object)
    def apply_bank_result(self, result: BankResult) -> None:
        self.bank_results[result.bank] = result
        self.set_slot_status(
            result.bank, self.outcome_text(result), result.error or str(result.path)
        )
        if result.status != "skipped":
            self._needs_recovery = result.status != "exported"
        if self._batch_active:
            self._completed_banks.add(result.bank)
            self.batch_progress.setValue(len(self._completed_banks))
        self.show_selected_details()

    @Slot()
    def show_selected_details(self) -> None:
        bank = self.slots.currentRow() + 1
        if bank < 1:
            return
        result = self.bank_results.get(bank)
        if result is None:
            self.details.setText(self.tr(
                "Slot %1 · occupancy and recording metadata have not been checked."
            ).replace("%1", f"{bank:02d}"))
        else:
            self.details.setText(
                self.tr("Slot %1 · %2\n%3").replace("%1", f"{bank:02d}")
                .replace("%2", self.outcome_text(result))
                .replace("%3", result.error or str(result.path))
            )

    @Slot(str)
    def task_failed(self, message: str) -> None:
        self._operation_failed = True
        self.error.setText(message)
        self._needs_recovery = True

    def task_result(self, action: str, result: Any) -> None:
        if action == "probe":
            addresses: list[tuple[int, int]] = result
            was_connected = self.connected
            self.connected = len(addresses) == 1
            if addresses != self._addresses:
                self._addresses = addresses
                if self.connected:
                    bus, address = addresses[0]
                    self.connection_status.setText(
                        self.tr("USB detected at %1:%2 — access checked on export.")
                        .replace("%1", str(bus)).replace("%2", str(address))
                    )
                elif addresses:
                    self.connection_status.setText(
                        self.tr("Multiple pedals found; launch with --usb-address.")
                    )
                else:
                    self.connection_status.setText(
                        self.tr("FS21 not connected in normal USB mode.")
                    )
            if self.connected != was_connected:
                self.update_buttons()
            return
        elif action == "info":
            self.metadata.setText(
                f"{result['model']} · {result['version_1']} / {result['version_2']} / "
                f"{result['version_3']}"
            )
            self.status.setText(self.tr("Connected — identification verified."))
            self._needs_recovery = False
        elif isinstance(result, BatchReport):
            for outcome in result.results:
                self.apply_bank_result(outcome)
            self.batch_progress.setValue(len(result.results))
            self.batch_progress.show()
            self.progress.setValue(0)
            self.progress.setFormat(self.tr("No active transfer"))
            counts = result.counts
            text = self.tr(
                "Exported: %1 · Empty: %2 · Failed: %3 · Unsupported: %4 · Skipped: %5"
            )
            statuses = ("exported", "empty", "failed", "unsupported", "skipped")
            for index, key in enumerate(statuses, 1):
                text = text.replace(f"%{index}", str(counts[key]))
            self.summary.setText(text)
            self.summary.show()
            self._batch_active = False
            self.status.setText(
                self.tr("Export all finished — some slots need attention.")
                if result.exit_code else self.tr("Export all finished.")
            )
            if result.exit_code:
                self.error.setText(
                    self.tr("Select a failed or unsupported slot to see its error.")
                )
        elif isinstance(result, ExportResult):
            self.status.setText(self.tr("Slot %1 exported — %2 seconds.")
                                .replace("%1", f"{self.active_bank or 1:02d}")
                                .replace("%2", f"{result.duration:.3f}"))
        elif isinstance(result, BankResult):
            self.status.setText(self.tr("Slot %1 · %2").replace("%1", f"{result.bank:02d}")
                                .replace("%2", self.outcome_text(result)))
            if result.status in ("failed", "unsupported"):
                self.error.setText(result.error or self.outcome_text(result))
            self.progress.setValue(0)
            self.progress.setFormat(self.tr("No audio exported"))
        self.update_buttons()

    @Slot()
    def task_finished(self) -> None:
        if self.task:
            if self._operation_failed:
                self.status.setText(self.tr("Operation failed — see the error below."))
                self.progress.setValue(0)
                self.progress.setFormat(self.tr("Transfer failed"))
                if self._batch_active:
                    for bank in range(1, BANK_COUNT + 1):
                        if bank not in self._completed_banks:
                            self.set_slot_status(bank, self.tr("Not completed"))
            self._batch_active = False
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
            if self.probe_task is not None:
                # Do not destroy a running QThread. Unlike a pedal transfer,
                # this read-only check can simply finish and then close us.
                self._close_pending = True
                self.update_buttons()
                event.ignore()
            else:
                event.accept()


def run(timeout: int, retries: int, address: tuple[int, int] | None) -> int:
    app = QApplication([sys.argv[0]])
    app.setApplicationName("fs21")
    app.setDesktopFileName("fs21")
    window = Window(timeout, retries, address)
    window.show()
    return app.exec()
