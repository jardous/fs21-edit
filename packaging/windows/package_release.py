"""Assemble portable binaries, licenses, corresponding sources and checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import posixpath
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from importlib import metadata
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, expected_hash: str | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)
    if expected_hash is not None and sha256(destination) != expected_hash:
        raise RuntimeError(f"Source archive checksum mismatch: {destination.name}")


def source_notices(archive: Path, destination: Path) -> None:
    """Copy license texts/attributions without extracting arbitrary tar paths."""
    with tarfile.open(archive) as source:
        files = {item.name: item for item in source.getmembers() if item.isfile()}
        selected: set[str] = set()
        for name, member in files.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError("Unexpected unsafe path in upstream source archive.")
            if (
                path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
                or "LICENSES" in path.parts or path.name == "qt_attribution.json"
            ):
                selected.add(name)
            if path.name == "qt_attribution.json":
                stream = source.extractfile(member)
                if stream is None:
                    continue
                # Qt's attribution format permits literal newlines in strings.
                attribution = json.load(stream, strict=False)
                records = attribution if isinstance(attribution, list) else [attribution]
                for record in records:
                    for reference in record.get("LicenseFile", "").split():
                        candidate = posixpath.normpath(str(path.parent / reference))
                        if candidate in files:
                            selected.add(candidate)
        for name in sorted(selected):
            member = files[name]
            if member.size > 2 * 1024 * 1024:
                raise RuntimeError(f"Unexpected large license file: {name}")
            parts = PurePosixPath(name).parts[1:]
            if not parts or ".." in parts:
                raise RuntimeError("Unsafe license destination.")
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = source.extractfile(member)
            if stream is not None:
                with target.open("wb") as output:
                    shutil.copyfileobj(stream, output)
        if not selected:
            raise RuntimeError(f"No license notices found in {archive.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "dist" / "FS21")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "windows-release")
    args = parser.parse_args()
    bundle: Path = args.bundle
    output: Path = args.output
    if sys.platform != "win32":
        raise RuntimeError("Build the Windows release on a native Windows runner.")
    if not (bundle / "FS21.exe").is_file() or not (bundle / "fs21-cli.exe").is_file():
        raise RuntimeError("Build both executables before packaging.")
    # All redistributed Qt libraries must be covered by the supplied source.
    qtbase_dlls = {
        "qt6core.dll", "qt6gui.dll", "qt6widgets.dll", "qt6network.dll",
        "qt6opengl.dll", "qt6openglwidgets.dll", "qt6printsupport.dll", "qt6dbus.dll",
    }
    qtdeclarative_dlls = {
        "qt6qml.dll", "qt6qmlmodels.dll", "qt6qmlworkerscript.dll", "qt6qmlmeta.dll",
        "qt6qmlcompiler.dll", "qt6qmlcore.dll", "qt6quick.dll", "qt6quicktemplates2.dll",
        "qt6quickcontrols2.dll",
    }
    print("Bundled Qt libraries:", ", ".join(sorted(p.name for p in bundle.rglob("Qt6*.dll"))))
    for path in bundle.rglob("Qt6*.dll"):
        if path.name.lower() not in qtbase_dlls | qtdeclarative_dlls:
            raise RuntimeError(f"Add corresponding source and notices for {path.name}.")
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
    shutil.copy2(Path(__file__).with_name("README-Windows.txt"), bundle / "README-Windows.txt")
    notices = bundle / "third-party-licenses"
    notices.mkdir(exist_ok=True)
    versions = {}
    distributions = (
        "pyusb", "PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6",
        "libusb-package", "importlib-resources", "pyinstaller",
    )
    for name in distributions:
        distribution = metadata.distribution(name)
        versions[name] = distribution.version
        for item in distribution.files or []:
            if item.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                destination = notices / name / item.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(distribution.locate_file(item), destination)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("The Python runtime license must be included.")
    shutil.copy2(python_license, notices / "Python-LICENSE.txt")

    from PySide6.QtCore import qVersion

    qt_version = qVersion()
    source_cache = ROOT / "artifacts" / "windows-sources"
    if versions["libusb-package"] != "1.0.30.1":
        raise RuntimeError("Update the pinned libusb source commits for this package version.")
    urls = [
        ("qtbase", f"https://github.com/qt/qtbase/archive/refs/tags/v{qt_version}.tar.gz", None),
        ("qtdeclarative",
         f"https://github.com/qt/qtdeclarative/archive/refs/tags/v{qt_version}.tar.gz", None),
        ("pyside-setup",
         f"https://github.com/pyside/pyside-setup/archive/refs/tags/v{versions['PySide6']}.tar.gz",
         None),
        # This wheel-only release has no PyPI sdist. Include both the package
        # source and its exact libusb submodule commit (GitHub omits submodules).
        ("libusb-package",
         "https://github.com/pyocd/libusb-package/archive/"
         "09711f4056efd55f5591ecfad1826c83b411c7c2.tar.gz", None),
        ("libusb",
         "https://github.com/libusb/libusb/archive/"
         "87a55632db62c9bdc58cd31d3ccfa673f1bb017f.tar.gz", None),
    ]
    sources = []
    for name, url, expected in urls:
        archive = source_cache / f"{name}-source.tar.gz"
        download(url, archive, expected)
        source_notices(archive, notices / name)
        sources.append({"name": name, "url": url, "sha256": sha256(archive)})
    source_zip = output / "FS21-third-party-sources.zip"
    with zipfile.ZipFile(source_zip, "w", compression=zipfile.ZIP_STORED) as archive:
        for source in sources:
            name = f"{source['name']}-source.tar.gz"
            archive.write(source_cache / name, name)
    text = [
        "FS21 third-party components",
        "==========================",
        "FS21: MIT; https://github.com/jardous/fs21-edit",
        "Python: PSF license; https://www.python.org/downloads/source/",
        "PyUSB: BSD-3-Clause; https://github.com/pyusb/pyusb",
        "Qt/PySide6/shiboken6: LGPL-3.0; dynamically linked and replaceable.",
        "libusb-package: Apache-2.0; bundled libusb: LGPL-2.1.",
        "PyInstaller bootloader: GPL-2.0-or-later with its distribution exception.",
        "",
        "Component license texts and attributions: third-party-licenses/",
        "Corresponding source archives: FS21-third-party-sources.zip in this release.",
        "The binaries are unmodified upstream components; no source changes were made.",
        "Replacement Qt/libusb DLLs can be placed in their original _internal locations.",
        "These notices do not impose any restriction on reverse engineering for",
        "debugging modifications to LGPL-covered libraries.",
        "",
        "Installed build versions:", json.dumps(versions, indent=2),
        "", "Exact corresponding source URLs:", json.dumps(sources, indent=2),
    ]
    (bundle / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    manifest = {
        "python": platform.python_version(), "platform": platform.platform(),
        "machine": platform.machine(), "packages": versions, "sources": sources,
        "files": {str(path.relative_to(bundle)): sha256(path)
                  for path in sorted(bundle.rglob("*")) if path.is_file()},
    }
    (output / "build-info.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    binary_zip = output / "FS21-Windows-x64.zip"
    with zipfile.ZipFile(binary_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, Path("FS21") / path.relative_to(bundle))
    files = [binary_zip, source_zip, output / "build-info.json"]
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="ascii"
    )
    for path in files:
        print(f"{path.name}: {path.stat().st_size} bytes, SHA-256 {sha256(path)}")


if __name__ == "__main__":
    main()
