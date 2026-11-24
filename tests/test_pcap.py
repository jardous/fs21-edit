import struct
from pathlib import Path

import pytest

from fs21.errors import CaptureError
from fs21.pcap import read_urbs


def sample(endian: str, linktype: int, nano: bool) -> bytes:
    magic = 0xA1B23C4D if nano else 0xA1B2C3D4
    global_header = struct.pack(endian + "IHHiIII", magic, 2, 4, 0, 0, 65535, linktype)
    raw = struct.pack(endian + "QBBBBHBBqiiII", 123, 67, 3, 130, 11, 5, 45, 0,
                      1, 0, 0, 3, 3) + b"\0" * (24 if linktype == 220 else 8) + b"abc"
    record = struct.pack(endian + "IIII", 1, 123, len(raw), len(raw))
    return global_header + record + raw


@pytest.mark.parametrize("endian", ["<", ">"])
@pytest.mark.parametrize("linktype", [189, 220])
@pytest.mark.parametrize("nano", [False, True])
def test_formats(endian: str, linktype: int, nano: bool, tmp_path: Path) -> None:
    path = tmp_path / "sample.pcap"
    path.write_bytes(sample(endian, linktype, nano))
    urb, = read_urbs(path)
    assert urb.payload == b"abc"
    assert urb.address == (5, 11)
    assert urb.timestamp == 1 + 123 / (1_000_000_000 if nano else 1_000_000)


@pytest.mark.parametrize("cut", [0, 12, 25, 39, 45, 100])
def test_truncated(cut: int, tmp_path: Path) -> None:
    path = tmp_path / "truncated.pcap"
    path.write_bytes(sample("<", 220, False)[:cut])
    with pytest.raises(CaptureError):
        list(read_urbs(path))


def test_bad_linktype(tmp_path: Path) -> None:
    path = tmp_path / "other.pcap"
    path.write_bytes(sample("<", 1, False))
    with pytest.raises(CaptureError):
        list(read_urbs(path))
