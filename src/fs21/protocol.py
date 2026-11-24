"""Strict framing and a finite read-only command allowlist.

CD indices 0..161 and CA selectors for banks 1..4/8 are capture-validated.
Other CA selectors within the 40-bank range are explicitly inferred.
"""

from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass

from fs21.errors import ProtocolError, UnsupportedOperation

VID = 0x0483
PID = 0x1412
BOOT_PID = 0x5703
EP_OUT = 0x01
EP_IN = 0x82
PACKET_SIZE = 512
MAX_AUDIO_BYTES = 393216
MAX_BODY = MAX_AUDIO_BYTES + 6
MAGIC = b"\xaa\x55"
BANK_COUNT = 40
CAPTURED_BANKS = frozenset({1, 2, 3, 4, 8})
MAX_VALIDATED_BLOCK = 161
MAX_VALIDATED_BLOCKS = MAX_VALIDATED_BLOCK + 1


def _padded(hexadecimal: str) -> bytes:
    return bytes.fromhex(hexadecimal).ljust(PACKET_SIZE, b"\0")


IDENTIFY = _padded("aa 55 01 00 00 00 00 55 ae")


def select_bank(bank0: int) -> bytes:
    """Build only CA bank-selection reads (zero-based argument, 0..39)."""
    if not 0 <= bank0 < BANK_COUNT:
        raise UnsupportedOperation("Bank must be in the hardware's range 1..40.")
    body = struct.pack("<I", 2) + bytes((0xCA, bank0))
    # This matches every captured CA and the empirically validated CD CRC-high
    # model. The original additive CA extrapolation diverges at selector 8 (bank 9).
    # Selectors outside CAPTURED_BANKS remain inferred, not newly "validated".
    checksum = (binascii.crc_hqx(body, 0) >> 8) ^ 0xFF
    return (MAGIC + body + bytes((0, checksum))).ljust(PACKET_SIZE, b"\0")


START_SLOT_1 = select_bank(0)  # Backward-compatible name and exact original bytes.


def block_checksum(index: int) -> int:
    """Captured checksum algorithm, bounded to all 162 verified requests."""
    if not 0 <= index <= MAX_VALIDATED_BLOCK:
        raise UnsupportedOperation(
            f"Block acknowledgment {index} is unverified; validated indices are 0..161."
        )
    value = 0x7A
    for bit, difference in enumerate((0x33, 0x66, 0xCC, 0x89, 0x03, 0x06, 0x0D, 0x1B)):
        if index & (1 << bit):
            value ^= difference
    return value


ACK_INTEGRITY = bytes(block_checksum(index) for index in range(MAX_VALIDATED_BLOCKS))


def acknowledge(sequence: int) -> bytes:
    checksum = block_checksum(sequence)
    body = MAGIC + struct.pack("<IHH", 4, 0xCD, sequence)
    return (body + bytes((0, checksum))).ljust(PACKET_SIZE, b"\0")


ALLOWED_COMMANDS = frozenset(
    [IDENTIFY] + [select_bank(bank0) for bank0 in range(BANK_COUNT)]
    + [acknowledge(n) for n in range(MAX_VALIDATED_BLOCKS)]
)


@dataclass(frozen=True)
class Message:
    body: bytes
    trailer: bytes

    @property
    def command(self) -> int:
        return self.body[0]


def wire_size(header: bytes) -> int:
    if len(header) < 6 or header[:2] != MAGIC:
        raise ProtocolError("Missing AA 55 response header.")
    length = int.from_bytes(header[2:6], "little")
    if not 1 <= length <= MAX_BODY:
        raise ProtocolError(f"Invalid response body length {length}.")
    return ((length + 8 + PACKET_SIZE - 1) // PACKET_SIZE) * PACKET_SIZE


def decode_message(wire: bytes) -> Message:
    expected = wire_size(wire)
    if len(wire) != expected:
        raise ProtocolError(f"Expected {expected} padded response bytes, got {len(wire)}.")
    length = int.from_bytes(wire[2:6], "little")
    # Padding sometimes contains stale device RAM. Never include it in audio.
    return Message(wire[6 : 6 + length], wire[6 + length : 8 + length])


@dataclass(frozen=True)
class AudioBlock:
    sequence: int
    total_blocks: int
    pcm: bytes
    bank: int = 0


def decode_audio(message: Message, bank: int | None = None) -> AudioBlock:
    body = message.body
    if len(body) < 6 or body[0] != 0xCC:
        raise ProtocolError("Expected CC audio response with a six-byte prefix.")
    if body[1] >= BANK_COUNT or (bank is not None and body[1] != bank):
        raise ProtocolError(f"Unexpected audio bank selector {body[1]}; expected {bank}.")
    total, sequence = struct.unpack_from("<HH", body, 2)
    pcm = body[6:]
    empty = total == 0 and sequence == 0 and not pcm
    if not empty and (not total or sequence >= total or not pcm or len(pcm) % 6):
        raise ProtocolError("Invalid audio count, sequence, or stereo PCM frame alignment.")
    if len(pcm) > MAX_AUDIO_BYTES:
        raise ProtocolError("Audio block exceeds the captured maximum size.")
    if message.trailer != b"\0\0":
        raise ProtocolError("Unexpected audio trailer; integrity format is unverified.")
    return AudioBlock(sequence, total, pcm, body[1])


def decode_start(message: Message) -> int:
    if len(message.body) != 5 or message.body[:2] != b"\xcb\0":
        raise ProtocolError("Expected CB 00 export-start response.")
    if message.trailer != b"\x02\0":
        raise ProtocolError("Unexpected export-start trailer.")
    return int.from_bytes(message.body[2:5], "little")


def check_end(message: Message, bank: int = 0) -> None:
    if message.body != bytes((0xCE, bank)) or message.trailer != b"\x02\0":
        raise ProtocolError(f"Missing CE end acknowledgment for bank selector {bank}.")


def decode_identity(message: Message) -> dict[str, str]:
    if len(message.body) != 56 or message.body[0] != 1 or message.trailer != b"\x02\0":
        raise ProtocolError("Unrecognized identification response.")
    body = message.body
    try:
        return {
            "model": body[1:33].split(b"\0", 1)[0].decode("ascii"),
            "version_1": body[33:40].split(b"\0", 1)[0].decode("ascii"),
            "version_2": body[40:47].split(b"\0", 1)[0].decode("ascii"),
            "version_3": body[47:54].split(b"\0", 1)[0].decode("ascii"),
            "unknown_tail_hex": body[54:56].hex(),
        }
    except UnicodeDecodeError as exc:
        raise ProtocolError("Identification contains non-ASCII strings.") from exc
