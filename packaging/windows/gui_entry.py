"""Windowed executable entry point; never require a terminal or install a driver."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    directory = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "FS21"
    directory.mkdir(parents=True, exist_ok=True)
    logfile = directory / "fs21.log"
    logging.basicConfig(
        filename=logfile, encoding="utf-8", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    smoke = sys.argv[1:2] == ["--smoke-test"]
    try:
        if smoke:
            if len(sys.argv) != 3:
                raise ValueError("--smoke-test requires a report filename.")
            from windows_smoke import smoke_test
            return smoke_test(Path(sys.argv[2]))
        if len(sys.argv) != 1:
            raise ValueError("Start FS21.exe without arguments. Use fs21-cli.exe for CLI commands.")
        from fs21.gui import run
        return run(3000, 2, None)
    except Exception as exc:
        logging.exception("FS21 startup failed")
        if not smoke and sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(
                None, f"{exc}\n\nLog: {logfile}", "FS21 could not start", 0x10
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
