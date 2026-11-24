"""Replay capture structure without bundling the new recordings' audio."""

from __future__ import annotations

import json
import struct
from collections import deque
from pathlib import Path
from typing import Any, Self

from fs21.errors import TransportError
from fs21.protocol import ALLOWED_COMMANDS, IDENTIFY, Message, acknowledge, select_bank

EVIDENCE_PATH = Path(__file__).parent / "fixtures" / "protocol-evidence.json"


def evidence() -> dict[str, Any]:
    return json.loads(EVIDENCE_PATH.read_text())


def packet(record: dict[str, Any]) -> bytes:
    return bytes.fromhex(record["request_hex"]).ljust(512, b"\0")


def identity() -> Message:
    entry = evidence()["sources"][0]["exchanges"][0]["messages"][0]
    return Message(bytes.fromhex(entry["body_hex"]), bytes.fromhex(entry["trailer_hex"]))


def session(bank: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    for source in evidence()["sources"]:
        if not any(s["bank"] == bank for s in source["sessions"]):
            continue
        selected = False
        records = []
        for record in source["exchanges"]:
            request = packet(record)
            if request[6] == 0xCA:
                selected = request[7] + 1 == bank
            if selected and request[6] in (0xCA, 0xCD):
                records.append(record)
        metadata = next(s for s in source["sessions"] if s["bank"] == bank)
        return metadata, records
    raise AssertionError(f"No captured bank {bank}")


class CaptureReplay:
    def __init__(self, bank: int) -> None:
        _, records = session(bank)
        identity_record = {
            "request_hex": IDENTIFY.rstrip(b"\0").hex(),
            "messages": [{"body_hex": identity().body.hex(), "trailer_hex": "0200"}],
        }
        self.expected = deque([identity_record, *records])
        self.pending: deque[dict[str, Any]] = deque()
        self.sent: list[bytes] = []

    def send(self, command: bytes) -> None:
        assert not self.pending, "Host sent another request before reading the response"
        record = self.expected.popleft()
        assert command == packet(record), "Generated command differs from recorded USB bytes"
        self.sent.append(command)
        self.pending.extend(record["messages"])

    def receive(self) -> Message:
        entry = self.pending.popleft()
        if "prefix_hex" in entry:
            body = bytes.fromhex(entry["prefix_hex"]) + bytes(entry["audio_bytes"])
        else:
            body = bytes.fromhex(entry["body_hex"])
        return Message(body, bytes.fromhex(entry["trailer_hex"]))


class SyntheticBank:
    """Explicitly synthetic edge cases, not evidence of real empty/error behavior."""

    def __init__(self, bank: int, total: int = 1, failure: str | None = None) -> None:
        self.bank, self.total = bank, total
        self.sent: list[bytes] = []
        self.closed = False
        self.drained = False
        bank0 = bank - 1
        self.responses: deque[Message | Exception] = deque([
            identity(), Message(b"\xcb\0\x01\0\0", b"\x02\0"),
        ])
        if failure == "timeout":
            self.responses.append(TransportError("transfer timeout"))
        elif total == 0:
            self.responses.append(Message(bytes((0xCC, bank0)) + bytes(4), bytes(2)))
        else:
            # Oversized cases need only the first block; no unsupported ACK is built.
            sequences = range(total) if total <= 162 else range(1)
            for sequence in sequences:
                body = bytes((0xCC, bank0)) + struct.pack("<HH", total, sequence) + bytes(6)
                if failure == "short_audio":
                    body = body[:-1]
                self.responses.append(Message(body, bytes(2)))
            terminal_bank = (bank0 + 1) % 40 if failure == "wrong_end" else bank0
            self.responses.append(Message(bytes((0xCE, terminal_bank)), b"\x02\0"))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def send(self, command: bytes) -> None:
        assert command in ALLOWED_COMMANDS
        if len(self.sent) == 0:
            assert command == IDENTIFY
        elif len(self.sent) == 1:
            assert command == select_bank(self.bank - 1)
        else:
            assert command == acknowledge(len(self.sent) - 2)
        self.sent.append(command)

    def receive(self) -> Message:
        result = self.responses.popleft()
        if isinstance(result, Exception):
            raise result
        return result

    def drain_input(self) -> int:
        self.drained = True
        return 0
