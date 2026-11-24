import struct
from pathlib import Path

import pytest

from fs21.errors import CaptureError
from fs21.pcap import read_urbs
from fs21.protocol import select_bank


def block(kind: int, body: bytes, endian: str = "<") -> bytes:
    padded = body.ljust((len(body) + 3) & ~3, b"\0")
    size = len(padded) + 12
    return struct.pack(endian + "II", kind, size) + padded + struct.pack(endian + "I", size)


def sample(endian: str = "<", nano: bool = False) -> bytes:
    section = block(0x0A0D0D0A, struct.pack(endian + "IHHq", 0x1A2B3C4D, 1, 0, -1), endian)
    options = struct.pack(endian + "HH", 9, 1) + bytes((9 if nano else 6, 0, 0, 0))
    options += bytes(4)
    interface = block(1, struct.pack(endian + "HHI", 220, 0, 65535) + options, endian)
    command = select_bank(7)
    raw = struct.pack(endian + "QBBBBHBBqiiII", 42, 83, 3, 1, 12, 5, 45, 0,
                      1, 0, -115, 512, 512) + bytes(24) + command
    packet = block(6, struct.pack(endian + "IIIII", 0, 0, 123456, len(raw), len(raw))
                   + raw, endian)
    return section + interface + packet


@pytest.mark.parametrize("endian", ["<", ">"])
@pytest.mark.parametrize("nano", [False, True])
def test_pcapng_formats(endian: str, nano: bool, tmp_path: Path) -> None:
    path = tmp_path / "capture.pcapng"
    path.write_bytes(sample(endian, nano))
    urb, = read_urbs(path)
    assert urb.payload == select_bank(7)
    assert urb.address == (5, 12)
    assert urb.timestamp == pytest.approx(123456 / (1e9 if nano else 1e6))


def test_mixed_endian_sections(tmp_path: Path) -> None:
    path = tmp_path / "sections.pcapng"
    path.write_bytes(sample("<") + sample(">"))
    first, second = read_urbs(path)
    assert (first.frame, second.frame) == (1, 2)
    assert first.payload == second.payload == select_bank(7)


@pytest.mark.parametrize("cut", [4, 15, 27, 35, 90, 200])
def test_truncated_pcapng(cut: int, tmp_path: Path) -> None:
    path = tmp_path / "truncated.pcapng"
    path.write_bytes(sample()[:cut])
    with pytest.raises(CaptureError):
        list(read_urbs(path))


def test_inconsistent_block_lengths(tmp_path: Path) -> None:
    path = tmp_path / "lengths.pcapng"
    path.write_bytes(sample()[:-4] + struct.pack("<I", 12))
    with pytest.raises(CaptureError, match="block lengths"):
        list(read_urbs(path))
