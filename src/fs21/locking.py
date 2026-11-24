"""Non-blocking per-device locks on POSIX and Windows, released on file close."""

from __future__ import annotations

import errno
import os
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO

from fs21.errors import TransportError


def acquire_device_lock(bus: int, address: int) -> BinaryIO:
    # Windows TEMP is per-user. Keep the existing numeric user key on POSIX.
    getuid = getattr(os, "getuid", None)
    user = str(getuid()) if getuid is not None else "windows"
    path = Path(tempfile.gettempdir()) / f"fs21-{user}-{bus}-{address}.lock"
    flags = os.O_CREAT | os.O_RDWR
    for name in ("O_NOFOLLOW", "O_CLOEXEC", "O_BINARY"):
        flags |= getattr(os, name, 0)
    stream = os.fdopen(os.open(path, flags, 0o600), "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt

            # msvcrt locks a byte range starting at the current file position.
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException as exc:
        stream.close()
        if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            raise TransportError("Another fs21 process is already using this pedal.") from exc
        raise
    return stream
