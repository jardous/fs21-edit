"""Streaming PCAP/PCAPNG Linux usbmon reader, with no external dissector."""

from __future__ import annotations

import gzip
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from fs21.errors import CaptureError

MAX_RECORD = 16 * 1024 * 1024


@dataclass(frozen=True)
class URB:
    frame: int
    timestamp: float
    urb_id: int
    event: str
    transfer: int
    endpoint: int
    device: int
    bus: int
    status: int
    requested_length: int
    payload: bytes
    setup: bytes

    @property
    def address(self) -> tuple[int, int]:
        return self.bus, self.device

    @property
    def is_bulk_payload(self) -> bool:
        return self.transfer == 3 and bool(self.payload) and (
            (self.event == "S" and not self.endpoint & 0x80)
            or (self.event == "C" and bool(self.endpoint & 0x80))
        )


def read_urbs(path: Path) -> Iterator[URB]:
    """Read DLT_USB_LINUX (189) or DLT_USB_LINUX_MMAPPED (220), optionally gzip."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        header = stream.read(24)
        if header[:4] == b"\x0a\x0d\x0d\x0a":
            stream.seek(0)
            yield from _read_pcapng(cast(BinaryIO, stream))
            return
        if len(header) != 24:
            raise CaptureError("Truncated PCAP header.")
        formats = {
            b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
            b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
            b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
            b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
        }
        if header[:4] not in formats:
            raise CaptureError("Expected PCAP or PCAPNG with Linux usbmon packets.")
        endian, ticks = formats[header[:4]]
        major, minor, _, _, snaplen, linktype = struct.unpack(endian + "HHiIII", header[4:])
        if (major, minor) != (2, 4) or linktype not in (189, 220):
            raise CaptureError(f"Unsupported PCAP version or link type {linktype}.")
        usb_header = 64 if linktype == 220 else 48
        frame = 0
        while record := stream.read(16):
            frame += 1
            if len(record) != 16:
                raise CaptureError(f"Truncated record header at frame {frame}.")
            sec, subsec, included, original = struct.unpack(endian + "IIII", record)
            if included > MAX_RECORD or included > snaplen or included > original:
                raise CaptureError(f"Invalid record length at frame {frame}.")
            raw = stream.read(included)
            if len(raw) != included or included < usb_header:
                raise CaptureError(f"Truncated USB record at frame {frame}.")
            yield _decode_urb(raw, frame, sec + subsec / ticks, endian, linktype)


def _decode_urb(raw: bytes, frame: int, timestamp: float, endian: str, linktype: int) -> URB:
    if linktype not in (189, 220):
        raise CaptureError(f"Unsupported USB link type {linktype}.")
    usb_header = 64 if linktype == 220 else 48
    if len(raw) < usb_header:
        raise CaptureError(f"Truncated USB record at frame {frame}.")
    fields = struct.unpack_from(endian + "QBBBBHBBqiiII", raw)
    uid, event, transfer, ep, dev, bus, _, data_flag, _, _, status, length, dlen = fields
    if dlen > len(raw) - usb_header:
        raise CaptureError(f"USB payload truncated at frame {frame}.")
    payload = raw[usb_header : usb_header + dlen] if data_flag == 0 else b""
    return URB(frame, timestamp, uid, chr(event), transfer, ep, dev, bus,
               status, length, payload, raw[40:48])


def _options(data: bytes, endian: str) -> Iterator[tuple[int, bytes]]:
    while data:
        if len(data) < 4:
            raise CaptureError("Truncated PCAPNG option.")
        code, length = struct.unpack_from(endian + "HH", data)
        if code == 0:
            return
        padded = (length + 3) & ~3
        if 4 + padded > len(data):
            raise CaptureError("Truncated PCAPNG option value.")
        yield code, data[4 : 4 + length]
        data = data[4 + padded :]


def _read_pcapng(stream: BinaryIO) -> Iterator[URB]:
    """Read section/interface/enhanced-packet blocks, including multiple sections."""
    endian: str | None = None
    interfaces: list[tuple[int, int, float, int]] = []
    frame = 0
    while header := stream.read(8):
        if len(header) != 8:
            raise CaptureError("Truncated PCAPNG block header.")
        prefix = b""
        section = header[:4] == b"\x0a\x0d\x0d\x0a"
        if section:
            prefix = stream.read(4)
            orders = {b"\x4d\x3c\x2b\x1a": "<", b"\x1a\x2b\x3c\x4d": ">"}
            endian = orders.get(prefix)
            if endian is None:
                raise CaptureError("Invalid PCAPNG byte-order magic.")
            interfaces = []
        if endian is None:
            raise CaptureError("PCAPNG packet without a section header.")
        block_type, length = struct.unpack(endian + "II", header)
        if length < (28 if section else 12) or length % 4 or length > MAX_RECORD:
            raise CaptureError("Invalid PCAPNG block size.")
        rest = stream.read(length - 8 - len(prefix))
        if len(rest) != length - 8 - len(prefix):
            raise CaptureError("Truncated PCAPNG block.")
        if struct.unpack(endian + "I", rest[-4:])[0] != length:
            raise CaptureError("Mismatched PCAPNG block lengths.")
        body = prefix + rest[:-4]
        if section:
            if struct.unpack_from(endian + "HH", body, 4)[0] != 1:
                raise CaptureError("Unsupported PCAPNG section version.")
        elif block_type == 1:
            if len(body) < 8:
                raise CaptureError("Truncated PCAPNG interface.")
            linktype, _, snaplen = struct.unpack_from(endian + "HHI", body)
            resolution, offset = 1e-6, 0
            for code, value in _options(body[8:], endian):
                if code == 9 and len(value) == 1:
                    exponent = value[0]
                    resolution = (2.0 ** -(exponent & 127) if exponent & 128
                                  else 10.0 ** -exponent)
                elif code == 14 and len(value) == 8:
                    offset = struct.unpack(endian + "q", value)[0]
            interfaces.append((linktype, snaplen, resolution, offset))
        elif block_type == 6:
            frame += 1
            if len(body) < 20:
                raise CaptureError("Truncated PCAPNG enhanced packet.")
            interface, high, low, captured, original = struct.unpack_from(endian + "IIIII", body)
            if interface >= len(interfaces):
                raise CaptureError("Unknown PCAPNG interface.")
            linktype, snaplen, resolution, offset = interfaces[interface]
            if (captured > original or captured > MAX_RECORD
                    or (snaplen and captured > snaplen) or 20 + captured > len(body)):
                raise CaptureError(f"Truncated PCAPNG packet at frame {frame}.")
            yield _decode_urb(body[20 : 20 + captured], frame,
                              ((high << 32) | low) * resolution + offset, endian, linktype)
        elif block_type in (2, 3):
            raise CaptureError(
                "Legacy/simple PCAPNG packets are unsupported; convert with editcap."
            )
