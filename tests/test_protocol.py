from dataclasses import replace

import pytest

from fs21.capture import Exchange
from fs21.errors import ProtocolError, UnsupportedOperation
from fs21.protocol import (
    ACK_INTEGRITY,
    ALLOWED_COMMANDS,
    IDENTIFY,
    START_SLOT_1,
    Message,
    acknowledge,
    check_end,
    decode_audio,
    decode_identity,
    decode_message,
    decode_start,
    wire_size,
)


def test_allowlist_matches_actual_capture(captured_exchanges: tuple[Exchange, ...]) -> None:
    observed = {e.request.payload for e in captured_exchanges}
    # Preserve byte-for-byte compatibility with all originally enabled commands.
    original_allowlist = {IDENTIFY, START_SLOT_1, *(acknowledge(n) for n in range(42))}
    assert original_allowlist <= observed
    assert original_allowlist <= ALLOWED_COMMANDS
    assert len(ALLOWED_COMMANDS) == 203  # Identity + 40 banks + 162 acknowledgments.
    assert all(len(command) == 512 for command in ALLOWED_COMMANDS)


@pytest.mark.parametrize("sequence", range(42))
def test_ack_is_byte_exact(sequence: int, captured_exchanges: tuple[Exchange, ...]) -> None:
    assert acknowledge(sequence) == captured_exchanges[5 + sequence].request.payload


@pytest.mark.parametrize("sequence", [-1, 162, 65535])
def test_unknown_ack_refused(sequence: int) -> None:
    with pytest.raises(UnsupportedOperation):
        acknowledge(sequence)


def test_identity(captured_exchanges: tuple[Exchange, ...]) -> None:
    exchange = captured_exchanges[1]
    assert exchange.request.payload == IDENTIFY
    identity = decode_identity(next(exchange.messages()))
    assert identity["model"] == "FLAMMA FS21"
    assert identity["version_2"] == "V1.0.1"


def test_first_audio_is_before_any_cd(captured_exchanges: tuple[Exchange, ...]) -> None:
    exchange = captured_exchanges[4]
    assert exchange.request.payload == START_SLOT_1
    messages = list(exchange.messages())
    assert len(messages) == 2
    assert decode_start(messages[0]) == 64077
    block = decode_audio(messages[1])
    assert (block.sequence, block.total_blocks, len(block.pcm)) == (0, 42, 393216)
    assert block.pcm[:6] == bytes.fromhex("30 00 00 50 00 00")


def test_last_block_strips_stale_padding(captured_exchanges: tuple[Exchange, ...]) -> None:
    exchange = captured_exchanges[45]
    message = next(exchange.messages())
    block = decode_audio(message)
    assert (block.sequence, len(block.pcm)) == (41, 281856)
    assert len(b"".join(u.payload for u in exchange.responses)) == 282112
    assert block.pcm[-6:] == bytes.fromhex("a9 ff ff c6 ff ff")
    check_end(next(captured_exchanges[46].messages()))


@pytest.mark.parametrize("raw", [b"", b"xx" + b"\0" * 510,
                                      b"\xaa\x55" + b"\0" * 510,
                                      b"\xaa\x55\xff\xff\xff\xff" + b"\0" * 506])
def test_bad_headers(raw: bytes) -> None:
    with pytest.raises(ProtocolError):
        wire_size(raw)


def test_short_response() -> None:
    with pytest.raises(ProtocolError):
        decode_message(b"\xaa\x55\x02\0\0\0\xce\0\x02\0")


@pytest.mark.parametrize("message", [
    Message(b"\xcc\0\x01\0\0\0" + b"\0" * 5, b"\0\0"),
    Message(b"\xcc\0\0\0\0\0" + b"\0" * 6, b"\0\0"),
    Message(b"\xcc\0\x01\0\x01\0" + b"\0" * 6, b"\0\0"),
    Message(b"\xcc\0\x01\0\0\0" + b"\0" * 6, b"\x02\0"),
])
def test_bad_audio(message: Message) -> None:
    with pytest.raises(ProtocolError):
        decode_audio(message)


def test_observed_checks_are_finite() -> None:
    assert len(ACK_INTEGRITY) == 162
    with pytest.raises(ProtocolError):
        check_end(Message(b"\xce\0", b"\0\0"))


def test_missing_packet_rejected(captured_exchanges: tuple[Exchange, ...]) -> None:
    exchange = captured_exchanges[5]
    shortened = replace(exchange, responses=exchange.responses[:-1])
    with pytest.raises(Exception, match="Incomplete"):
        list(shortened.messages())
