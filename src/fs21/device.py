"""Read-only application state machine, separate from USB and GUI."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from fs21.errors import EmptyBank, ProtocolError, UnsupportedOperation, UnsupportedRecording
from fs21.protocol import (
    BANK_COUNT,
    IDENTIFY,
    MAX_VALIDATED_BLOCKS,
    AudioBlock,
    Message,
    acknowledge,
    check_end,
    decode_audio,
    decode_identity,
    decode_start,
    select_bank,
)

ENUMERATION_UNSUPPORTED = (
    "Recording metadata enumeration remains unverified. Export supports banks 1..40 "
    "with at most 162 blocks; selectors for banks 1..4 and 8 are captured, "
    "the other bank selectors are inferred read operations."
)


class Transport(Protocol):
    def send(self, command: bytes) -> None: ...
    def receive(self) -> Message: ...


class FS21:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def info(self) -> dict[str, str]:
        self.transport.send(IDENTIFY)
        return decode_identity(self.transport.receive())

    def blocks(self, slot: int) -> Iterator[AudioBlock]:
        if not 1 <= slot <= BANK_COUNT:
            raise UnsupportedOperation("Bank must be 1..40; no command was sent.")
        bank0 = slot - 1
        identity = self.info()
        if identity["model"] != "FLAMMA FS21":
            raise UnsupportedOperation(
                f"Unverified device model {identity['model']!r}; export refused."
            )
        self.transport.send(select_bank(bank0))
        # This is observed metadata, but its unit/meaning is not established.
        decode_start(self.transport.receive())
        total: int | None = None
        expected = 0
        while True:
            message = self.transport.receive()
            if message.command == 0xCE:
                check_end(message, bank0)
                if total is None or expected != total:
                    raise ProtocolError("Unexpected early end of export.")
                return
            block = decode_audio(message, bank0)
            if block.total_blocks == 0:
                raise EmptyBank(slot)
            if block.sequence != expected or (total is not None and total != block.total_blocks):
                raise ProtocolError(f"Missing, duplicate, or reordered audio block {expected}.")
            total = block.total_blocks
            if total > MAX_VALIDATED_BLOCKS:
                raise UnsupportedRecording(slot, total, MAX_VALIDATED_BLOCKS)
            yield block
            self.transport.send(acknowledge(block.sequence))
            if expected + 1 == total:
                check_end(self.transport.receive(), bank0)
                return
            expected += 1
