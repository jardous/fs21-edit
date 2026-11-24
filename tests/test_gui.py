import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QEventLoop
from PySide6.QtWidgets import QApplication

from fs21.gui import Task, Window


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


def test_no_device_disables_export(window: Window) -> None:
    window.task_result("probe", [])
    window.update_buttons()
    assert not window.export_button.isEnabled()
    assert not window.export_all_button.isEnabled()
    assert window.slots.count() == 1


def test_connection_and_path_enable_export(window: Window, tmp_path: Path) -> None:
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(tmp_path / "new.wav"))
    assert window.export_button.isEnabled()
    assert not window.export_all_button.isEnabled()


def test_existing_file_is_protected(window: Window, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "existing.wav"
    path.write_bytes(b"original")
    window.task_result("probe", [(5, 11)])
    window.output.setText(str(path))
    calls = []
    monkeypatch.setattr(window, "start_task", lambda *args: calls.append(args))
    window.export()
    assert not calls
    assert path.read_bytes() == b"original"
    assert "already exists" in window.error.text()


def test_progress_and_help(window: Window) -> None:
    window.transfer_progress(21, 42)
    assert window.progress.value() == 50
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
    window.focus_changed(window.output, window.export_button)
    assert all(shortcut.isEnabled() for shortcut in window.text_shortcuts)
