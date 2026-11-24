"""Check the frozen GUI and bundled backend without accessing a physical pedal."""

from __future__ import annotations

import json
import logging
import platform
import sys
from pathlib import Path
from unittest.mock import patch

import libusb_package
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from fs21 import __version__
from fs21.gui import Window
from fs21.usb_backend import get_usb_backend


def smoke_test(output: Path) -> int:
    image = output.with_suffix(".png")
    if output.exists() or image.exists():
        raise FileExistsError("Choose a new smoke-test output filename.")
    output.parent.mkdir(parents=True, exist_ok=True)
    library = libusb_package.get_library_path()
    bundle = Path(getattr(sys, "_MEIPASS", ""))
    if library is None or not library.is_file() or bundle not in library.parents:
        raise RuntimeError("libusb was not loaded from this executable's bundled files.")
    if get_usb_backend() is None:
        raise RuntimeError("The native Windows libusb backend did not initialize.")
    report: dict[str, object] = {
        "version": __version__, "platform": sys.platform, "machine": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "bundled_libusb": library.name, "libusb_backend_loaded": True,
        "physical_usb_access": False,
    }
    exit_code = 0
    app = QApplication(["FS21 smoke test"])
    app.setApplicationName("fs21")
    with (
        patch("fs21.gui.connected_addresses", return_value=iter(())),
        patch(
            "usb.core.find", side_effect=AssertionError("Smoke tests must not access USB devices")
        ),
    ):
        window = Window()
        window.timer.stop()
        window.output.setText("C:/Recordings")
        window.show()

        def finish() -> None:
            nonlocal exit_code
            try:
                assert window.slots.rowCount() == 40
                assert window.slots.columnCount() == 2
                assert len(window.export_buttons) == 40
                assert window.output.isEnabled()
                if not window.grab().save(str(image)):
                    raise RuntimeError("Could not save the native GUI smoke-test image.")
                report.update(slots=40, columns=2, export_buttons=40, gui_started=True)
            except Exception as exc:
                logging.exception("Packaged GUI smoke test failed")
                report["error"] = str(exc)
                exit_code = 1
            finally:
                try:
                    with output.open("x", encoding="utf-8") as stream:
                        json.dump(report, stream, indent=2)
                except OSError:
                    logging.exception("Could not save the smoke-test report")
                    exit_code = 1
                window.close()

        QTimer.singleShot(300, finish)
        app.exec()
    return exit_code
