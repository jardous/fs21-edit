"""Cross-platform regression tests; OS-specific cases also run natively in CI."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fs21 import usb_backend
from fs21.audio import write_wav
from fs21.errors import TransportError
from fs21.locking import acquire_device_lock
from fs21.protocol import AudioBlock
from fs21.transport import USBTransport
from tests.test_device_transport import mock_device


def test_device_lock_is_exclusive_and_released_on_close(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fs21.locking.tempfile.gettempdir", lambda: str(tmp_path))
    first = acquire_device_lock(5, 11)
    try:
        with pytest.raises(TransportError, match="already using"):
            acquire_device_lock(5, 11)
    finally:
        first.close()
    second = acquire_device_lock(5, 11)
    second.close()


def test_different_devices_have_independent_locks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fs21.locking.tempfile.gettempdir", lambda: str(tmp_path))
    first = acquire_device_lock(5, 11)
    second = acquire_device_lock(5, 12)
    first.close()
    second.close()


def test_linux_backend_discovery_remains_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(usb_backend, "WINDOWS", False)
    assert usb_backend.get_usb_backend() is None


def test_windows_backend_uses_bundled_library(monkeypatch) -> None:
    backend = object()
    loader = MagicMock(return_value=backend)
    monkeypatch.setattr(usb_backend, "WINDOWS", True)
    monkeypatch.setitem(
        sys.modules, "libusb_package", SimpleNamespace(get_libusb1_backend=loader)
    )
    assert usb_backend.get_usb_backend() is backend
    loader.assert_called_once_with()


def test_windows_missing_dll_is_an_actionable_error(monkeypatch) -> None:
    monkeypatch.setattr(usb_backend, "WINDOWS", True)
    monkeypatch.setitem(
        sys.modules, "libusb_package",
        SimpleNamespace(get_libusb1_backend=lambda: None),
    )
    with pytest.raises(TransportError, match="DLL could not be loaded"):
        usb_backend.get_usb_backend()


def test_windows_missing_package_is_an_actionable_error(monkeypatch) -> None:
    monkeypatch.setattr(usb_backend, "WINDOWS", True)
    monkeypatch.setitem(sys.modules, "libusb_package", None)
    with pytest.raises(TransportError, match="Windows libusb support is missing"):
        usb_backend.get_usb_backend()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows backend behavior.")
def test_windows_does_not_query_or_detach_kernel_driver(monkeypatch) -> None:
    device = mock_device(monkeypatch)
    device.is_kernel_driver_active.side_effect = NotImplementedError
    with USBTransport():
        pass
    device.is_kernel_driver_active.assert_not_called()
    device.detach_kernel_driver.assert_not_called()
    device.set_configuration.assert_not_called()
    device.reset.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows non-overwriting rename semantics.")
def test_windows_wav_output_does_not_require_hardlinks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fs21.audio.os.link", MagicMock(side_effect=AssertionError("No hardlinks")))
    output = tmp_path / "recording.wav"
    write_wav([AudioBlock(0, 1, bytes(6))], output)
    assert output.read_bytes().startswith(b"RIFF")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows non-overwriting rename semantics.")
def test_windows_atomic_publish_protects_raced_in_file(tmp_path: Path, monkeypatch) -> None:
    import os

    original = os.rename
    output = tmp_path / "recording.wav"

    def race(source, destination):
        output.write_bytes(b"raced-in original")
        return original(source, destination)

    monkeypatch.setattr("fs21.audio.os.rename", race)
    with pytest.raises(FileExistsError):
        write_wav([AudioBlock(0, 1, bytes(6))], output)
    assert output.read_bytes() == b"raced-in original"
    assert list(tmp_path.iterdir()) == [output]
