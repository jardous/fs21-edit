## Portable Windows x64 preview

Download **FS21-Windows-x64.zip**, extract the entire folder, and run **FS21.exe**.
The zip includes a console CLI (`fs21-cli.exe`), Python, Qt/PySide6, and libusb.
No Python installation is needed. Do not separate the executables from `_internal`.

The normal-mode pedal (`0483:1412`, interface 0) may require a **WinUSB** driver.
The application does not install drivers. Read `README-Windows.txt` before using
Zadig: changing the driver may affect the manufacturer's editor. Never change
the firmware/HID device (`0483:5703`).

- GUI: all 40 slots, individual Export buttons, and Export all.
- Existing-file protection, atomic WAV output, progress, and batch summaries.
- Read-only commands; no import, erase, firmware update, or automatic USB reset.
- Maximum verified length: **162 blocks**. Selectors outside banks 1–4 and 8
  remain inferred; empty-bank/recovery behavior is not capture-confirmed.
- Native Windows tests and packaged GUI/CLI smoke checks run without a pedal.
  **No live Windows USB export is claimed.**

This build is unsigned and may trigger SmartScreen. SHA-256 checksums are
provided. Third-party source archives and license notices accompany the release.
