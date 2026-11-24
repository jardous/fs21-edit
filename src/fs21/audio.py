"""Atomic, lossless 24-bit stereo WAV export."""

from __future__ import annotations

import os
import tempfile
import wave
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from fs21.errors import FS21Error, ProtocolError
from fs21.protocol import AudioBlock

Progress = Callable[[int, int], None]


@dataclass(frozen=True)
class ExportResult:
    path: Path
    blocks: int
    audio_bytes: int

    @property
    def duration(self) -> float:
        return self.audio_bytes / 6 / 44100


def write_wav(
    blocks: Iterable[AudioBlock],
    destination: Path,
    progress: Progress | None = None,
    overwrite: bool = False,
) -> ExportResult:
    """Publish only after generator exhaustion validates the terminal CE response."""
    if destination.exists() and not overwrite:
        raise FS21Error(f"Output already exists: {destination}. Use --force to replace it.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".part",
                                dir=destination.parent)
    temporary = Path(name)
    count = size = 0
    total: int | None = None
    try:
        with os.fdopen(fd, "w+b") as output:
            with wave.open(output, "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(3)
                wav.setframerate(44100)
                for block in blocks:
                    if block.sequence != count or (
                        total is not None and total != block.total_blocks
                    ):
                        raise ProtocolError("Audio blocks are missing, duplicated, or reordered.")
                    if not block.pcm or len(block.pcm) % 6:
                        raise ProtocolError("Incomplete stereo PCM frame.")
                    total = block.total_blocks
                    if size + len(block.pcm) > 0xFFFFFFFF - 36:
                        raise FS21Error("Recording exceeds classic WAV's 4 GiB limit.")
                    wav.writeframesraw(block.pcm)
                    count += 1
                    size += len(block.pcm)
                    if progress:
                        progress(count, total)
                if not count or count != total:
                    raise ProtocolError("Recording is empty or incomplete.")
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            os.replace(temporary, destination)
        elif os.name == "nt":
            # Windows rename is non-overwriting, unlike POSIX rename, and also
            # works on removable filesystems that do not support hard links.
            os.rename(temporary, destination)
        else:
            # Hard linking publishes atomically without clobbering a raced-in output.
            os.link(temporary, destination)
            temporary.unlink()
        return ExportResult(destination, count, size)
    finally:
        temporary.unlink(missing_ok=True)
