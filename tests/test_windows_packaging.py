import io
import runpy
import tarfile
from pathlib import Path

import pytest

SOURCE_NOTICES = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "packaging/windows/package_release.py")
)["source_notices"]


def make_archive(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


def test_qt_attributions_allow_multiline_strings_and_referenced_licenses(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    attribution = (
        b'{"Copyright": "First line\nSecond line", "LicenseFile": "SPECIAL-LICENSE.txt"}'
    )
    make_archive(archive, {
        "source/LICENSES/LGPL-3.0.txt": b"library license",
        "source/src/vendor/qt_attribution.json": attribution,
        "source/src/vendor/SPECIAL-LICENSE.txt": b"vendor license",
        "source/src/vendor/code.c": b"not a notice",
    })
    output = tmp_path / "notices"
    SOURCE_NOTICES(archive, output)
    assert (output / "LICENSES/LGPL-3.0.txt").read_bytes() == b"library license"
    assert (output / "src/vendor/SPECIAL-LICENSE.txt").read_bytes() == b"vendor license"
    assert (output / "src/vendor/qt_attribution.json").read_bytes() == attribution
    assert not (output / "src/vendor/code.c").exists()


def test_source_notices_reject_unsafe_archive_paths(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    make_archive(archive, {"source/../../LICENSE": b"unsafe"})
    with pytest.raises(RuntimeError, match="unsafe path"):
        SOURCE_NOTICES(archive, tmp_path / "notices")
    assert not (tmp_path / "LICENSE").exists()


def test_source_notices_require_license_evidence(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    make_archive(archive, {"source/main.c": b"no license"})
    with pytest.raises(RuntimeError, match="No license notices"):
        SOURCE_NOTICES(archive, tmp_path / "notices")
