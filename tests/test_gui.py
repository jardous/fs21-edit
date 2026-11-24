import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QEventLoop, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

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
    if window.task is not None:
        assert window.task.wait(1000), "GUI worker did not finish"
    app.processEvents()
    assert window.task is None


def finish_connection_check(window: Window, app: QApplication) -> None:
    task = window.probe_task
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
    if window.probe_task is not None:
        assert window.probe_task.wait(1000), "Connection check did not finish"
    app.processEvents()
    assert window.probe_task is None


@pytest.fixture
def polling_window(app, monkeypatch):
    # Exercise the real refresh timer, but never enumerate physical USB.
    monkeypatch.setattr("fs21.gui.connected_addresses", lambda: iter([(5, 11)]))
    result = Window()
    result.timer.stop()
    result.show()
    app.processEvents()
    QTest.qWait(20)
    finish_connection_check(result, app)
    yield result
    result.timer.stop()
    finish_connection_check(result, app)
    result.close()
    result.deleteLater()


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
    assert "21/42" in window.status.text()
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


def test_list_contains_only_slots_and_export_buttons(window: Window) -> None:
    assert not any("captured selectors" in label.text() for label in window.findChildren(QLabel))
    assert window.slots.columnCount() == 2
    assert [window.slots.horizontalHeaderItem(column).text() for column in range(2)] == [
        "Slot", "Export",
    ]
    for bank in range(1, 41):
        assert window.slots.item(bank - 1, 0).text() == f"Slot {bank:02d}"
        assert window.slots.cellWidget(bank - 1, 1) is window.export_buttons[bank]
        assert window.slot_statuses[bank] == "Not checked"


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
    assert "162 blocks" in window.details.text()
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
    assert "04, 09" in window.error.text()
    window.slots.selectRow(8)
    assert "189 blocks" in window.details.text()
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
    assert window.bank_results[3].status == "empty"
    assert "Empty" in window.details.text()
    window.export_bank(9)
    finish_window_task(window, app)
    assert window.bank_results[9].status == "unsupported"
    assert "Unsupported (189 blocks)" in window.details.text()
    assert not list(tmp_path.iterdir())


def test_failed_slot_details_remain_available(window: Window, tmp_path: Path) -> None:
    outcome = BankResult(4, "failed", tmp_path / "bank-04.wav", error="USB transfer timed out")
    window.apply_bank_result(outcome)
    window.slots.selectRow(3)
    assert "USB transfer timed out" in window.details.text()
    assert "USB transfer timed out" in window.slots.item(3, 0).toolTip()
    assert "USB transfer timed out" in window.export_buttons[4].toolTip()


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


def test_automatic_connection_checks_preserve_folder_editing(
    app, polling_window: Window, tmp_path: Path,
) -> None:
    window = polling_window
    window.output.setText(str(tmp_path / "my recordings"))
    window.slots.selectRow(7)
    window.output.setFocus()
    window.output.setSelection(3, 7)
    app.processEvents()
    before = (
        window.output.text(), window.output.cursorPosition(),
        window.output.selectionStart(), window.output.selectedText(),
    )
    assert window.output.hasFocus()
    window.timer.setInterval(10)
    window.timer.start()
    QTest.qWait(80)
    window.timer.stop()
    finish_connection_check(window, app)
    assert window.output.isEnabled()
    assert window.output.hasFocus(), "Background USB detection stole the folder field's focus"
    assert (
        window.output.text(), window.output.cursorPosition(),
        window.output.selectionStart(), window.output.selectedText(),
    ) == before
    assert window.slots.currentRow() == 7
    QTest.keyClicks(window.output, "edited")
    assert "edited" in window.output.text()


def test_unchanged_probe_does_not_refresh_controls(
    app, polling_window: Window, monkeypatch,
) -> None:
    from unittest.mock import MagicMock

    window = polling_window
    update = MagicMock(wraps=window.update_buttons)
    monkeypatch.setattr(window, "update_buttons", update)
    window.refresh()
    finish_connection_check(window, app)
    update.assert_not_called()
    assert window.task is None


def test_hotplug_changes_preserve_folder_focus(app, polling_window: Window) -> None:
    window = polling_window
    window.output.setFocus()
    window.output.setCursorPosition(3)
    app.processEvents()
    assert window.output.hasFocus()
    for addresses in ([], [(5, 11)], []):
        window.task_result("probe", addresses)
        app.processEvents()
        assert window.output.isEnabled() and window.output.hasFocus()
        assert window.output.cursorPosition() == 3
        assert window.export_all_button.isEnabled() == bool(addresses)


def test_probe_error_preserves_export_state_and_recovers_same_address(
    app, polling_window: Window, tmp_path: Path, monkeypatch,
) -> None:
    window = polling_window
    outcome = BankResult(8, "exported", tmp_path / "bank-08.wav", 162, 63505920)
    window.apply_bank_result(outcome)
    window.status.setText("Previous export complete")
    window.summary.setText("Previous summary")
    window.error.setText("Earlier export detail")
    window.output.setFocus()
    app.processEvents()

    def failed_probe():
        raise OSError("Enumeration failed")

    monkeypatch.setattr("fs21.gui.connected_addresses", failed_probe)
    window.refresh()
    finish_connection_check(window, app)
    assert not window.connected
    assert "Enumeration failed" in window.connection_status.text()
    assert window.output.hasFocus()
    assert window.bank_results[8] == outcome
    assert window.status.text() == "Previous export complete"
    assert window.summary.text() == "Previous summary"
    assert window.error.text() == "Earlier export detail"
    assert not window._operation_failed and not window._needs_recovery
    monkeypatch.setattr("fs21.gui.connected_addresses", lambda: iter([(5, 11)]))
    window.refresh()
    finish_connection_check(window, app)
    assert window.connected and window.export_all_button.isEnabled()
    assert "USB detected at 5:11" in window.connection_status.text()
    assert window.output.hasFocus()


def test_export_starts_while_one_background_check_is_pending(
    app, polling_window: Window, tmp_path: Path, monkeypatch,
) -> None:
    from threading import Event
    from unittest.mock import MagicMock

    window = polling_window
    started, release = Event(), Event()

    def blocked_probe():
        started.set()
        if not release.wait(5):
            raise TimeoutError("Test did not release the connection check")
        return iter([(5, 11)])

    probe = MagicMock(side_effect=blocked_probe)
    monkeypatch.setattr("fs21.gui.connected_addresses", probe)
    device = SyntheticBank(8)
    monkeypatch.setattr("fs21.gui.USBTransport", lambda *_: device)
    window.output.setText(str(tmp_path))
    window.refresh()
    try:
        assert started.wait(1)
        assert window.probe_task is not None and window.task is None
        for _ in range(3):
            window.refresh()
        assert probe.call_count == 1
        assert window.output.isEnabled() and window.export_buttons[8].isEnabled()
        window.export_buttons[8].click()
        finish_window_task(window, app)
        assert window.bank_results[8].status == "exported"
        assert (tmp_path / "bank-08.wav").exists()
        assert window.probe_task is not None
    finally:
        release.set()
        finish_connection_check(window, app)


def test_close_waits_for_background_check_without_transfer_abort_warning(
    app, polling_window: Window, monkeypatch,
) -> None:
    from threading import Event

    window = polling_window
    started, release = Event(), Event()

    def blocked_probe():
        started.set()
        if not release.wait(5):
            raise TimeoutError("Test did not release the connection check")
        return iter([(5, 11)])

    monkeypatch.setattr("fs21.gui.connected_addresses", blocked_probe)
    window.refresh()
    try:
        assert started.wait(1)
        window.close()
        assert window._close_pending and window.isVisible()
        assert not window.timer.isActive()
        assert not window.export_all_button.isEnabled()
        assert not window.error.text()
    finally:
        release.set()
        finish_connection_check(window, app)
    assert not window.isVisible()


def test_polling_does_not_start_during_foreground_operations(
    polling_window: Window, monkeypatch,
) -> None:
    from unittest.mock import MagicMock

    window = polling_window
    probe = MagicMock()
    monkeypatch.setattr("fs21.gui.connected_addresses", probe)
    window.task = MagicMock()
    try:
        window.refresh()
        assert window.probe_task is None
        probe.assert_not_called()
    finally:
        window.task = None
