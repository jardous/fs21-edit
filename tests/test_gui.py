import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QEventLoop, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from fs21.exporter import BankResult, BatchReport
from fs21.gui import Task, Window
from tests.replay import CaptureReplay, SyntheticBank


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance
    instance.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def window(app, monkeypatch):
    # Avoid starting even a descriptor enumeration in GUI unit tests.
    monkeypatch.setattr(Window, "refresh", lambda self: None)
    result = Window()
    result.timer.stop()
    yield result
    result.close()
    result.deleteLater()


def finish_window_task(window: Window, app: QApplication) -> None:
    task = window.task
    if task is None:
        app.processEvents()
        return
    loop = QEventLoop()
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(loop.quit)
    task.finished.connect(loop.quit)
    if not task.isFinished():
        watchdog.start(10000)
        loop.exec()
    watchdog.stop()
    assert task.wait(1000), "GUI worker did not finish"
    app.processEvents()
    assert window.task is None


def test_no_device_disables_export(window: Window) -> None:
    window.task_result("probe", [])
    window.update_buttons()
    assert all(not button.isEnabled() for button in window.export_buttons.values())
    assert not window.export_all_button.isEnabled()
    assert window.slots.rowCount() == 40
    assert len(window.export_buttons) == 40


def test_connection_and_path_enable_export(window: Window, tmp_path: Path) -> None:
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(tmp_path))
    assert all(button.isEnabled() for button in window.export_buttons.values())
    assert window.export_all_button.isEnabled()


def test_existing_file_is_protected(window: Window, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "bank-01.wav"
    path.write_bytes(b"original")
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(tmp_path))
    calls = []
    monkeypatch.setattr(window, "start_task", lambda *args: calls.append(args))
    window.export()
    assert not calls
    assert path.read_bytes() == b"original"
    assert "already exists" in window.error.text()


def test_progress_and_help(window: Window) -> None:
    window.transfer_progress(8, 21, 42)
    assert window.progress.value() == 50
    assert "21/42" in window.slots.item(7, 2).text()
    window.toggle_help()
    assert not window.help.isHidden()


def test_probe_runs_in_worker(app, monkeypatch) -> None:
    monkeypatch.setattr("fs21.gui.connected_addresses", lambda: iter([(5, 11)]))
    task = Task("probe", 3000, 2, None)
    loop = QEventLoop()
    results = []
    task.result.connect(results.append)
    task.finished.connect(loop.quit)
    task.start()
    loop.exec()
    assert task.wait(1000)
    assert results == [[(5, 11)]]


def test_export_worker_uses_capture_replay(
    app, monkeypatch, captured_exchanges, tmp_path: Path,
) -> None:
    from unittest.mock import MagicMock

    transport = MagicMock()
    transport.__enter__.return_value = transport
    messages = [next(captured_exchanges[1].messages())]
    for exchange in captured_exchanges[4:47]:
        messages.extend(exchange.messages())
    transport.receive.side_effect = messages
    monkeypatch.setattr("fs21.gui.USBTransport", lambda *_: transport)
    task = Task("export", 3000, 2, None, tmp_path / "gui-export.wav")
    loop = QEventLoop()
    results, errors, progress = [], [], []
    task.result.connect(results.append)
    task.failed.connect(errors.append)
    task.progress.connect(lambda done, total: progress.append((done, total)))
    task.finished.connect(loop.quit)
    task.start()
    loop.exec()
    assert task.wait(1000)
    assert not errors
    assert len(results) == 1 and results[0].audio_bytes == 16403712
    assert progress[-1] == (42, 42)


def test_probe_does_not_clear_transfer_error(window: Window) -> None:
    window.error.setText("USB access denied")
    window.task_result("probe", [(5, 11)])
    window.task_result("probe", [(5, 11)])
    assert window.error.text() == "USB access denied"


def test_typing_path_disables_single_character_shortcuts(window: Window) -> None:
    window.focus_changed(None, window.output)
    assert all(not shortcut.isEnabled() for shortcut in window.text_shortcuts)
    window.focus_changed(window.output, window.slots)
    assert all(shortcut.isEnabled() for shortcut in window.text_shortcuts)


def test_all_rows_have_independent_export_actions(
    window: Window, tmp_path: Path, monkeypatch,
) -> None:
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(tmp_path))
    calls = []
    monkeypatch.setattr(
        window, "start_task", lambda action, path, **kwargs: calls.append((action, path, kwargs))
    )
    for bank in range(1, 41):
        window.export_buttons[bank].click()
    assert calls == [
        ("export", tmp_path / f"bank-{bank:02d}.wav", {"bank": bank}) for bank in range(1, 41)
    ]


def test_keyboard_export_uses_selected_row(window: Window, tmp_path: Path, monkeypatch) -> None:
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(tmp_path))
    window.slots.selectRow(7)
    calls = []
    monkeypatch.setattr(window, "start_task", lambda *args, **kwargs: calls.append((args, kwargs)))
    window.export()
    assert calls == [(("export", tmp_path / "bank-08.wav"), {"bank": 8})]


def test_empty_folder_disables_all_export_buttons(window: Window) -> None:
    window.task_result("probe", [(5, 11)])
    window.output.clear()
    assert not window.export_all_button.isEnabled()
    assert all(not button.isEnabled() for button in window.export_buttons.values())


def test_export_folder_must_not_be_a_file(window: Window, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "not-a-directory"
    path.write_bytes(b"keep")
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(path))
    calls = []
    monkeypatch.setattr(window, "start_task", lambda *args, **kwargs: calls.append(args))
    window.export_all()
    window.export_bank(8)
    assert not calls and path.read_bytes() == b"keep"
    assert "directory" in window.error.text()


def test_busy_disables_actions_but_not_slot_navigation(window: Window) -> None:
    from unittest.mock import MagicMock

    window.task_result("probe", [(5, 11)])
    window.task = MagicMock()
    try:
        window.update_buttons()
        assert all(not button.isEnabled() for button in window.export_buttons.values())
        assert not window.export_all_button.isEnabled()
        assert not window.output.isEnabled()
        assert window.slots.isEnabled()
    finally:
        window.task = None


def test_captured_and_inferred_slots_are_marked(window: Window) -> None:
    assert window.slots.item(7, 1).text() == "Captured"
    assert window.slots.item(8, 1).text() == "Inferred"
    assert all(window.slots.item(row, 2).text() == "Not checked" for row in range(40))


def test_row_click_exports_bank8_through_worker(
    app, window: Window, tmp_path: Path, monkeypatch,
) -> None:
    from contextlib import nullcontext

    replay = CaptureReplay(8)
    monkeypatch.setattr("fs21.gui.USBTransport", lambda *_: nullcontext(replay))
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(tmp_path))
    window.show()
    window.slots.scrollToItem(window.slots.item(7, 0))
    app.processEvents()
    QTest.mouseClick(window.export_buttons[8], Qt.MouseButton.LeftButton)
    finish_window_task(window, app)
    assert window.bank_results[8].status == "exported"
    assert window.bank_results[8].blocks == 162
    assert (tmp_path / "bank-08.wav").stat().st_size == 63505964
    assert not replay.expected and not replay.pending
    assert "162 blocks" in window.slots.item(7, 2).text()
    assert window.export_all_button.isEnabled()


def test_export_all_button_reports_every_outcome_and_continues(
    app, window: Window, tmp_path: Path, monkeypatch,
) -> None:
    (tmp_path / "bank-01.wav").write_bytes(b"existing successful export")
    devices = {
        bank: SyntheticBank(
            bank, total=0 if bank == 3 else 189 if bank == 9 else 162 if bank == 8 else 1,
            failure="timeout" if bank == 4 else None,
        ) for bank in range(2, 41)
    }
    attempts = iter(devices.values())
    monkeypatch.setattr("fs21.gui.USBTransport", lambda *_: next(attempts))
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(tmp_path))
    window.export_all_button.click()
    finish_window_task(window, app)
    assert len(window.bank_results) == 40
    assert window.bank_results[1].status == "skipped"
    assert window.bank_results[3].status == "empty"
    assert window.bank_results[4].status == "failed"
    assert window.bank_results[8].blocks == 162
    assert window.bank_results[9].status == "unsupported"
    assert window.bank_results[40].status == "exported"
    assert window.batch_progress.value() == 40
    assert window.summary.text() == (
        "Exported: 36 · Empty: 1 · Failed: 1 · Unsupported: 1 · Skipped: 1"
    )
    assert "189 blocks" in window.slots.item(8, 2).text()
    assert (tmp_path / "bank-01.wav").read_bytes() == b"existing successful export"
    assert not (tmp_path / "bank-04.wav").exists()
    assert all(device.closed for device in devices.values())


def test_failed_single_export_recovers_before_another_slot(
    app, window: Window, tmp_path: Path, monkeypatch,
) -> None:
    failed = SyntheticBank(4, failure="timeout")
    next_slot = SyntheticBank(5)
    attempts = iter([failed, next_slot])
    monkeypatch.setattr("fs21.gui.USBTransport", lambda *_: next(attempts))
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(tmp_path))
    window.export_bank(4)
    finish_window_task(window, app)
    assert window.bank_results[4].status == "failed"
    assert "timeout" in window.error.text()
    window.export_bank(5)
    finish_window_task(window, app)
    assert next_slot.drained
    assert window.bank_results[5].status == "exported"
    assert window.bank_results[4].status == "failed"
    assert not window.error.text()


def test_single_empty_and_unsupported_slots_have_distinct_status(
    app, window: Window, tmp_path: Path, monkeypatch,
) -> None:
    attempts = iter([SyntheticBank(3, 0), SyntheticBank(9, 189)])
    monkeypatch.setattr("fs21.gui.USBTransport", lambda *_: next(attempts))
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(tmp_path))
    window.export_bank(3)
    finish_window_task(window, app)
    assert window.slots.item(2, 2).text() == "Empty"
    window.export_bank(9)
    finish_window_task(window, app)
    assert window.slots.item(8, 2).text() == "Unsupported (189 blocks)"
    assert not list(tmp_path.iterdir())


def test_failed_slot_details_remain_available(window: Window, tmp_path: Path) -> None:
    outcome = BankResult(4, "failed", tmp_path / "bank-04.wav", error="USB transfer timed out")
    window.apply_bank_result(outcome)
    window.slots.selectRow(3)
    assert "USB transfer timed out" in window.details.text()
    assert "USB transfer timed out" in window.slots.item(3, 2).toolTip()


def test_batch_summary_is_not_overwritten_by_connection_probes(
    window: Window, tmp_path: Path,
) -> None:
    window.task_result("export-all", BatchReport((BankResult(1, "skipped", tmp_path / "one.wav"),)))
    summary = window.summary.text()
    status = window.status.text()
    window.task_result("probe", [(5, 11)])
    assert window.summary.text() == summary and window.status.text() == status


def test_cannot_close_during_usb_operation(window: Window) -> None:
    from unittest.mock import MagicMock

    window.task = MagicMock()
    try:
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert "still running" in window.error.text()
    finally:
        window.task = None


def test_space_activates_focused_export_button_not_another_selected_slot(
    app, window: Window, tmp_path: Path, monkeypatch,
) -> None:
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(tmp_path))
    calls = []
    monkeypatch.setattr(
        window, "start_task", lambda action, path, **kwargs: calls.append((action, path, kwargs))
    )
    window.show()
    window.slots.selectRow(0)
    window.export_buttons[8].setFocus()
    app.processEvents()
    QTest.keyClick(window.export_buttons[8], Qt.Key.Key_Space)
    assert calls == [("export", tmp_path / "bank-08.wav", {"bank": 8})]


def test_failed_single_export_recovers_before_export_all(
    app, window: Window, tmp_path: Path, monkeypatch,
) -> None:
    failed = SyntheticBank(4, failure="timeout")
    banks = {bank: SyntheticBank(bank) for bank in range(1, 41)}
    attempts = iter([failed, *banks.values()])
    monkeypatch.setattr("fs21.gui.USBTransport", lambda *_: next(attempts))
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(tmp_path))
    window.export_bank(4)
    finish_window_task(window, app)
    window.export_all()
    finish_window_task(window, app)
    assert banks[1].drained
    assert window.summary.text() == (
        "Exported: 40 · Empty: 0 · Failed: 0 · Unsupported: 0 · Skipped: 0"
    )
