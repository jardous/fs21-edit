#!/usr/bin/env python3
"""Render the real GUI offscreen using fabricated display data and no USB calls."""

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from fs21.exporter import BankResult, BatchReport
from fs21.gui import Window


def main() -> None:
    app = QApplication([])
    app.setStyle("Fusion")
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#202124",
        QPalette.ColorRole.WindowText: "#eeeeee",
        QPalette.ColorRole.Base: "#17181a",
        QPalette.ColorRole.AlternateBase: "#292a2d",
        QPalette.ColorRole.Text: "#eeeeee",
        QPalette.ColorRole.Button: "#303134",
        QPalette.ColorRole.ButtonText: "#eeeeee",
        QPalette.ColorRole.Highlight: "#ffd60a",
        QPalette.ColorRole.HighlightedText: "#17181a",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    Window.refresh = lambda self: None  # type: ignore[method-assign]
    window = Window()
    window.timer.stop()
    window.task_result("probe", [(1, 1)])
    window.connection_status.setText("Preview — example data only; no USB access")
    window.metadata.setText("44.1 kHz · 24-bit stereo PCM · read-only USB export")
    directory = Path("/recordings")
    window.output.setText(str(directory))
    results = []
    for bank in range(1, 41):
        path = directory / f"bank-{bank:02d}.wav"
        if bank == 1:
            result = BankResult(bank, "skipped", path)
        elif bank in (3, 6):
            result = BankResult(bank, "empty", path, 0)
        elif bank == 9:
            result = BankResult(
                bank, "unsupported", path, 189,
                error="Example: recording exceeds the 162-block verified range.",
            )
        elif bank == 12:
            result = BankResult(bank, "failed", path, error="Example: USB read timed out.")
        elif bank == 8:
            result = BankResult(bank, "exported", path, 162, 63505920)
        else:
            blocks = 12 + bank
            result = BankResult(bank, "exported", path, blocks, blocks * 393216)
        results.append(result)
    window.task_result("export-all", BatchReport(tuple(results)))
    window.slots.selectRow(7)
    window.slots.scrollToTop()
    window.resize(980, 1000)
    window.show()
    app.processEvents()
    output = Path(__file__).resolve().parents[1] / "docs" / "screenshot.png"
    if not window.grab().save(str(output)):
        raise RuntimeError("Could not save GUI preview.")
    window.close()
    print(output)


if __name__ == "__main__":
    main()
