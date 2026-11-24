# Build on Windows with: pyinstaller --clean --noconfirm packaging/windows/fs21.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

root = Path(SPECPATH).resolve().parents[1]
paths = [str(root / "src"), str(root / "packaging" / "windows")]
usb_libraries = collect_dynamic_libs("libusb_package")
if not any(source.lower().endswith(".dll") for source, _ in usb_libraries):
    raise RuntimeError("The Windows libusb wheel must supply a DLL.")


def analyze(entry):
    analysis = Analysis(
        [str(root / "packaging" / "windows" / entry)],
        pathex=paths,
        binaries=usb_libraries,
        hiddenimports=["usb.backend.libusb1", "libusb_package"],
        excludes=["tkinter", "PyQt5", "PyQt6", "PySide2"],
    )
    # Widgets and PNG screenshots do not need PDF/SVG/image-format plugins.
    # The optional virtual-keyboard plugin also pulls a GPL-only Qt module;
    # ordinary desktop keyboard/IME input is provided by the platform plugin.
    def needed(item):
        name = item[0].replace("\\", "/").lower()
        if "/imageformats/" in name or "/iconengines/" in name:
            return False
        return not name.endswith((
            "/qt6pdf.dll", "/qt6svg.dll",
            "/qt6virtualkeyboard.dll", "/qtvirtualkeyboardplugin.dll",
        ))
    analysis.binaries = [item for item in analysis.binaries if needed(item)]
    return analysis


gui = analyze("gui_entry.py")
cli = analyze("cli_entry.py")
gui_exe = EXE(
    PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
    name="FS21", console=False, upx=False,
)
cli_exe = EXE(
    PYZ(cli.pure), cli.scripts, [], exclude_binaries=True,
    name="fs21-cli", console=True, upx=False,
)
COLLECT(
    gui_exe, cli_exe, gui.binaries, gui.datas, cli.binaries, cli.datas,
    name="FS21", upx=False,
)
