FS21 for Windows - portable x64 preview
=====================================

Requirements: Windows 10/11, 64-bit x86 (not Windows ARM or 32-bit Windows).
Python, Wine, QEMU, and the manufacturer's editor are NOT required.

Start
-----
1. Extract the ENTIRE FS21-Windows-x64.zip archive into a writable folder.
2. Keep FS21.exe, fs21-cli.exe, and the _internal directory together.
3. Double-click FS21.exe. Choose a writable export folder.
4. Connect the pedal in normal USB mode. Export any slot, or all 40.

The application is unsigned. Windows SmartScreen may display a warning.
Download only from https://github.com/jardous/fs21-edit/releases and verify
the SHA-256 checksum when in doubt. Do not run the application as administrator.
Startup/runtime errors are logged to %LOCALAPPDATA%\FS21\fs21.log.

USB driver
----------
The release bundles libusb, but cannot silently install a Windows USB driver.
Normal mode must use a compatible WinUSB driver on interface 0.

If the device cannot be opened:
- Close the manufacturer's editor, virtual machines, and other FS21 programs.
- Check Device Manager. The pedal may be called DONNER AUDIO / CIRCLE LOOPER.
- If necessary, use Zadig from https://zadig.akeo.ie/ to install WinUSB for
  ONLY the normal-mode device with USB VID 0483, PID 1412, interface 0.
- Confirm the VID/PID and interface before making any driver change.
- NEVER replace the driver for firmware/HID mode 0483:5703 or another device.

Installing WinUSB changes the host driver binding and may prevent the official
editor from working until its original driver is restored. This is a manual,
administrator-approved step, not a firmware update. To return to the original
editor, restore/roll back the device driver in Device Manager or reinstall its
original driver. FS21 does not install drivers or reset the USB device.

CLI examples (PowerShell)
-------------------------
.\fs21-cli.exe info
.\fs21-cli.exe export 8 .\bank-08.wav
.\fs21-cli.exe export-all --output .\recordings
.\fs21-cli.exe export-all --dry-run

The GUI keeps existing files. CLI replacement requires explicit --force.
An interrupted export never publishes a partial WAV.

Protocol and validation limits
------------------------------
- Read-only export: no import, erase, format, or firmware-update commands.
- 40 bank selectors; banks 1-4 and 8 are independently captured.
- Other bank selectors use an inferred checksum model.
- Maximum verified recording size: 162 blocks, indices 0-161.
- Longer recordings are reported as unsupported, not guessed.
- Only explicit zero-block replies are classified as empty.
- Empty-bank forms and recovery after interruption are not capture-confirmed.

This Windows preview is built and smoke-tested on Windows without a pedal.
No live Windows USB export is claimed. Historical live export validation
was on Linux; see the repository documentation for details.

Licenses and source
-------------------
FS21 is MIT-licensed. See LICENSE, THIRD-PARTY-NOTICES.txt, and
third-party-licenses for the bundled components' separate licenses.
The dynamically linked Qt/libusb DLLs remain replaceable in _internal.
Corresponding third-party source archives are supplied as a separate release
asset; exact source links and package versions are in the notices/build manifest.
No private recordings or raw captures are included.
