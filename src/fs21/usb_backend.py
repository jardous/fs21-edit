"""Use the bundled libusb on Windows without changing Linux backend discovery."""

from __future__ import annotations

import sys
from typing import Any

from fs21.errors import TransportError

WINDOWS = sys.platform == "win32"


def get_usb_backend() -> Any:
    """Loading a backend does not enumerate devices or send USB commands."""
    if not WINDOWS:
        return None
    try:
        import libusb_package
    except ImportError as exc:
        raise TransportError(
            "Windows libusb support is missing. Re-extract the complete Windows release "
            "or reinstall the fs21 package with its Windows dependencies."
        ) from exc
    backend = libusb_package.get_libusb1_backend()
    if backend is None:
        raise TransportError(
            "The bundled libusb DLL could not be loaded. Re-extract the complete release "
            "including its _internal directory."
        )
    return backend
