"""Reconstruct exchanges and complete exports from captured USB traffic."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from fs21.errors import CaptureError, EmptyBank, ProtocolError
from fs21.pcap import URB, read_urbs
from fs21.protocol import (
    BANK_COUNT,
    EP_IN,
    EP_OUT,
    IDENTIFY,
    PID,
    VID,
    AudioBlock,
    Message,
    acknowledge,
    check_end,
    decode_audio,
    decode_message,
    decode_start,
    select_bank,
    wire_size,
)


@dataclass(frozen=True)
class Exchange:
    request: URB
    responses: tuple[URB, ...]

    def messages(self) -> Iterator[Message]:
        stream = b"".join(urb.payload for urb in self.responses)
        while stream:
            size = wire_size(stream)
            if len(stream) < size:
                raise CaptureError(f"Incomplete response after frame {self.request.frame}.")
            yield decode_message(stream[:size])
            stream = stream[size:]


def detect_address(path: Path) -> tuple[int, int]:
    candidates: set[tuple[int, int]] = set()
    for urb in read_urbs(path):
        p = urb.payload
        if urb.transfer == 2 and urb.event == "C" and urb.status == 0:
            if len(p) >= 18 and p[:2] == b"\x12\x01":
                if int.from_bytes(p[8:10], "little") == VID:
                    if int.from_bytes(p[10:12], "little") == PID:
                        candidates.add(urb.address)
        if urb.is_bulk_payload and urb.endpoint == EP_OUT and (
            p == IDENTIFY or _selected_bank(p) is not None
        ):
            candidates.add(urb.address)
    if len(candidates) != 1:
        raise CaptureError(
            f"Found {len(candidates)} candidate devices; specify --usb-address BUS:DEVICE."
        )
    return candidates.pop()


def _selected_bank(request: bytes) -> int | None:
    if len(request) == 512 and request[6] == 0xCA and request[7] < BANK_COUNT:
        if request == select_bank(request[7]):
            return request[7]
    return None


def exchanges(path: Path, address: tuple[int, int] | None = None) -> Iterator[Exchange]:
    address = address or detect_address(path)
    request: URB | None = None
    responses: list[URB] = []
    completed = False
    pending_reads: set[int] = set()
    for urb in read_urbs(path):
        if urb.address != address or urb.transfer != 3:
            continue
        if urb.endpoint == EP_OUT and urb.event == "S" and urb.payload:
            if request is not None:
                if not completed:
                    raise CaptureError(f"Missing OUT completion for frame {request.frame}.")
                yield Exchange(request, tuple(responses))
            request, responses, completed = urb, [], False
        elif urb.endpoint == EP_OUT and urb.event in ("C", "E") and request is not None:
            if urb.urb_id != request.urb_id or urb.status != 0:
                raise CaptureError(f"Failed or mismatched OUT completion at frame {urb.frame}.")
            completed = True
        elif urb.endpoint == EP_IN:
            if urb.event == "S":
                if urb.urb_id in pending_reads:
                    raise CaptureError(f"Duplicate IN submission at frame {urb.frame}.")
                pending_reads.add(urb.urb_id)
            elif urb.event in ("C", "E"):
                if urb.urb_id not in pending_reads:
                    if request is not None:
                        raise CaptureError(f"Unpaired IN completion at frame {urb.frame}.")
                else:
                    pending_reads.remove(urb.urb_id)
                if request is not None:
                    # The Windows editor cancels idle reads between exchanges.
                    # Missing audio is still caught by framing/count checks.
                    if urb.status in (-2, -104) and not urb.payload:
                        continue
                    if urb.status != 0:
                        raise CaptureError(f"Failed IN completion at frame {urb.frame}.")
                    if urb.payload:
                        responses.append(urb)
    if request is not None:
        if not completed:
            raise CaptureError(f"Missing final OUT completion for frame {request.frame}.")
        yield Exchange(request, tuple(responses))


def captured_blocks(
    path: Path, address: tuple[int, int] | None = None, session: int = 1,
    bank: int | None = None,
) -> Iterator[AudioBlock]:
    """Require a complete CA/CB, CC/CD..., CE transaction, including block zero."""
    if session < 1:
        raise CaptureError("Capture session numbers start at 1.")
    if bank is not None and not 1 <= bank <= BANK_COUNT:
        raise CaptureError("Bank must be 1..40.")
    seen = 0
    active = False
    expected = 0
    total: int | None = None
    expected_next_request: bytes | None = None
    selected: int | None = None
    for exchange in exchanges(path, address):
        request = exchange.request.payload
        candidate = _selected_bank(request)
        if candidate is not None:
            if active:
                raise CaptureError("A new export began before the selected session completed.")
            if bank is not None and candidate != bank - 1:
                continue
            seen += 1
            if seen != session:
                continue
            active = True
            selected = candidate
            messages = iter(exchange.messages())
            try:
                decode_start(next(messages))
            except StopIteration as exc:
                raise CaptureError("Missing export-start response.") from exc
        elif active:
            if request != expected_next_request:
                raise CaptureError(f"Unexpected acknowledgment at frame {exchange.request.frame}.")
            messages = iter(exchange.messages())
        else:
            continue
        for message in messages:
            if message.command == 0xCE:
                assert selected is not None
                check_end(message, selected)
                if total is None or expected != total:
                    raise CaptureError("Transfer ended before all advertised audio blocks arrived.")
                if next(messages, None) is not None:
                    raise CaptureError("Unexpected response after end of export.")
                return
            block = decode_audio(message, selected)
            if block.total_blocks == 0:
                assert selected is not None
                raise EmptyBank(selected + 1)
            if block.sequence != expected or (total is not None and total != block.total_blocks):
                raise CaptureError(f"Missing, duplicate, or reordered audio block {expected}.")
            total = block.total_blocks
            expected_next_request = acknowledge(block.sequence)
            expected += 1
            yield block
    raise CaptureError("No complete export found; missing session or CE end acknowledgment.")


def capture_summary(path: Path, address: tuple[int, int] | None = None) -> dict[str, object]:
    address = address or detect_address(path)
    records = []
    for exchange in exchanges(path, address):
        messages = []
        try:
            for message in exchange.messages():
                entry: dict[str, object] = {
                    "command": f"0x{message.command:02x}",
                    "body_length": len(message.body),
                    "trailer_hex": message.trailer.hex(),
                }
                if message.command == 0xCC:
                    block = decode_audio(message)
                    entry.update(sequence=block.sequence, total_blocks=block.total_blocks,
                                 audio_bytes=len(block.pcm), bank=block.bank + 1)
                messages.append(entry)
        except ProtocolError as exc:
            raise CaptureError(str(exc)) from exc
        records.append({
            "frame": exchange.request.frame,
            "request_hex": exchange.request.payload.rstrip(b"\0").hex(),
            "in_transfers": len(exchange.responses),
            "messages": messages,
        })
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bus": address[0], "device": address[1],
            "exchanges": records}
