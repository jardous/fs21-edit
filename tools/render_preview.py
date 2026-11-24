#!/usr/bin/env python3
"""Render the real GUI offscreen using fabricated display data and no USB calls."""

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

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
    window.status.setText("Connected — example preview (no USB access)")
    window.metadata.setText("44.1 kHz · 24-bit stereo PCM · read-only USB export")
    window.output.setText("/recordings/slot-1.wav")
    window.resize(700, 470)
    window.show()
    app.processEvents()
    output = Path(__file__).resolve().parents[1] / "docs" / "screenshot.png"
    if not window.grab().save(str(output)):
        raise RuntimeError("Could not save GUI preview.")
    window.close()
    print(output)


if __name__ == "__main__":
    main()
