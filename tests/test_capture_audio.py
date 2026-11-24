import hashlib
import wave
from dataclasses import replace
from pathlib import Path

import pytest

from fs21 import capture
from fs21.audio import write_wav
from fs21.capture import Exchange, captured_blocks
from fs21.errors import CaptureError, FS21Error, ProtocolError
from fs21.protocol import AudioBlock

PCM_SHA256 = "aec8b1bcf99435a263bbae93cc962fb9b7a9aec2377f6353f763b9ed055b0987"


def test_full_capture_wav(capture_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "recording.wav"
    progress = []
    result = write_wav(captured_blocks(capture_path), output,
                       lambda n, total: progress.append((n, total)))
    assert (result.blocks, result.audio_bytes) == (42, 16403712)
    assert result.duration == pytest.approx(61.99437641723356)
    assert progress == [(n, 42) for n in range(1, 43)]
    with wave.open(str(output)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (2, 3, 44100)
        assert wav.getnframes() == 2733952
        assert hashlib.sha256(wav.readframes(wav.getnframes())).hexdigest() == PCM_SHA256


def test_existing_file_untouched(tmp_path: Path) -> None:
    output = tmp_path / "original.wav"
    output.write_bytes(b"keep")
    with pytest.raises(FS21Error, match="exists"):
        write_wav([], output)
    assert output.read_bytes() == b"keep"


def test_failed_generator_never_publishes(tmp_path: Path) -> None:
    def broken():
        yield AudioBlock(0, 1, b"\0" * 6)
        raise ProtocolError("Missing CE")
    output = tmp_path / "old.wav"
    output.write_bytes(b"original")
    with pytest.raises(ProtocolError):
        write_wav(broken(), output, overwrite=True)
    assert output.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.part"))
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("blocks", [
    [],
    [AudioBlock(0, 2, b"\0" * 6)],
    [AudioBlock(1, 1, b"\0" * 6)],
    [AudioBlock(0, 1, b"\0" * 5)],
    [AudioBlock(0, 2, b"\0" * 6), AudioBlock(0, 2, b"\0" * 6)],
])
def test_incomplete_wav_refused(blocks: list[AudioBlock], tmp_path: Path) -> None:
    with pytest.raises(ProtocolError):
        write_wav(blocks, tmp_path / "bad.wav")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mutation", ["missing_zero", "missing_end", "swapped", "duplicate"])
def test_broken_capture_refused(
    mutation: str, captured_exchanges: tuple[Exchange, ...],
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    selected = list(captured_exchanges)
    if mutation == "missing_zero":
        selected[4] = replace(selected[4], responses=selected[4].responses[:1])
    elif mutation == "missing_end":
        selected = selected[:46]
    elif mutation == "swapped":
        selected[5], selected[6] = selected[6], selected[5]
    else:
        selected.insert(6, selected[5])
    monkeypatch.setattr(capture, "exchanges", lambda *_: iter(selected))
    with pytest.raises((CaptureError, ProtocolError)):
        write_wav(captured_blocks(Path("unused")), tmp_path / "bad.wav")
    assert not list(tmp_path.iterdir())


def test_force_local_replace(tmp_path: Path) -> None:
    output = tmp_path / "old.wav"
    output.write_bytes(b"old")
    write_wav([AudioBlock(0, 1, b"\0" * 6)], output, overwrite=True)
    assert output.read_bytes().startswith(b"RIFF")
