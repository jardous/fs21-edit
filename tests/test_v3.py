from __future__ import annotations

import runpy
import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import usb.core

from fs21.audio import write_wav
from fs21.device import FS21
from fs21.errors import EmptyBank, ProtocolError, UnsupportedOperation, UnsupportedRecording
from fs21.protocol import (
    ALLOWED_COMMANDS,
    CAPTURED_BANKS,
    MAX_VALIDATED_BLOCK,
    Message,
    acknowledge,
    block_checksum,
    decode_audio,
    select_bank,
)
from tests.v3_replay import CaptureReplay, SyntheticBank, evidence, identity, packet, session


def test_every_recorded_acknowledgment_matches() -> None:
    observed = set()
    requests = 0
    for source in evidence()["sources"]:
        assert not source["checksum_mismatches"]
        for record in source["exchanges"]:
            request = packet(record)
            if request[6] == 0xCD:
                index = int.from_bytes(request[8:10], "little")
                assert acknowledge(index) == request
                observed.add(index)
                requests += 1
    assert requests == 581
    assert observed == set(range(162))


@pytest.mark.parametrize("bank, checksum", [(1, 0x8D), (2, 0x9D), (3, 0xAD),
                                          (4, 0xBD), (8, 0xFD)])
def test_captured_bank_selection(bank: int, checksum: int) -> None:
    _, records = session(bank)
    assert select_bank(bank - 1) == packet(records[0])
    assert select_bank(bank - 1)[7] == bank - 1
    assert select_bank(bank - 1)[9] == checksum
    assert bank in CAPTURED_BANKS


@pytest.mark.parametrize("index, checksum", [
    (0, 0x7A), (114, 0x14), (115, 0x27), (127, 0x62),
    (128, 0x61), (160, 0x67), (161, 0x54),
])
def test_checksum_regression_edges(index: int, checksum: int) -> None:
    assert block_checksum(index) == checksum
    assert acknowledge(index)[11] == checksum


@pytest.mark.parametrize("index", [-1, 162, 189, 255, 256, 65535, 65536])
def test_unverified_indices_are_never_generated(index: int) -> None:
    with pytest.raises(UnsupportedOperation):
        acknowledge(index)


@pytest.mark.parametrize("bank0", [-1, 40, 255])
def test_invalid_bank_selector(bank0: int) -> None:
    with pytest.raises(UnsupportedOperation):
        select_bank(bank0)


def test_allowlist_is_finite_and_contains_only_read_commands() -> None:
    assert len(ALLOWED_COMMANDS) == 203
    for request in ALLOWED_COMMANDS:
        assert request[6] in (0, 0xCA, 0xCD)
        if request[6] == 0xCA:
            assert request[7] < 40
        elif request[6] == 0xCD:
            assert int.from_bytes(request[8:10], "little") <= 161


@pytest.mark.parametrize("bank, checksum", [(9, 0x0C), (17, 0x9F), (40, 0xD9)])
def test_inferred_selectors_use_crc_model_not_additive_wrap(bank: int, checksum: int) -> None:
    assert bank not in CAPTURED_BANKS
    assert select_bank(bank - 1)[9] == checksum
    assert checksum != (0x8D + 0x10 * (bank - 1)) & 0xFF


@pytest.mark.parametrize("bank, blocks", [(1, 42), (2, 115), (3, 101), (4, 161), (8, 162)])
def test_device_replays_captured_banks(bank: int, blocks: int) -> None:
    replay = CaptureReplay(bank)
    metadata, _ = session(bank)
    count = size = 0
    for block in FS21(replay).blocks(bank):
        assert block.bank == bank - 1
        assert block.sequence == count
        assert block.total_blocks == blocks
        count += 1
        size += len(block.pcm)
    assert count == blocks
    assert size == metadata["audio_bytes"]
    assert not replay.pending and not replay.expected


def test_bank8_atomic_wav_output(tmp_path: Path) -> None:
    replay = CaptureReplay(8)
    progress = []
    result = write_wav(FS21(replay).blocks(8), tmp_path / "bank-08.wav",
                       lambda n, total: progress.append((n, total)))
    assert result.blocks == 162
    assert result.audio_bytes == 63505920
    assert progress == [(n, 162) for n in range(1, 163)]
    with wave.open(str(result.path)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (2, 3, 44100)
        assert wav.getnframes() == 10584320
    assert list(tmp_path.iterdir()) == [result.path]


@pytest.mark.parametrize("total", [163, 189, 65535])
def test_report_unsupported_recording_without_any_ack(total: int, tmp_path: Path) -> None:
    transport = SyntheticBank(8, total)
    with pytest.raises(UnsupportedRecording) as caught:
        write_wav(FS21(transport).blocks(8), tmp_path / "unsupported.wav")
    assert caught.value.blocks == total
    assert len(transport.sent) == 2  # Only identify and CA; no guessed acknowledgment.
    assert not list(tmp_path.iterdir())


def test_explicit_zero_block_response_is_empty(tmp_path: Path) -> None:
    transport = SyntheticBank(3, 0)
    with pytest.raises(EmptyBank):
        write_wav(FS21(transport).blocks(3), tmp_path / "empty.wav")
    assert len(transport.sent) == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("body", [
    b"\xcc\x07\0\0\0\0" + bytes(6),  # Zero count plus audio is inconsistent.
    b"\xcc\x28\x01\0\0\0" + bytes(6),  # Bank 40 zero-based is out of range.
    b"\xcc\x07\x01\0\0",  # Truncated prefix.
])
def test_malformed_audio_is_not_empty(body: bytes) -> None:
    with pytest.raises(ProtocolError):
        decode_audio(Message(body, bytes(2)), 7)


def test_wrong_audio_bank_is_rejected_before_ack() -> None:
    transport = MagicMock()
    transport.receive.side_effect = [
        identity(), Message(b"\xcb\0\x01\0\0", b"\x02\0"),
        Message(b"\xcc\0\x01\0\0\0" + bytes(6), bytes(2)),
    ]
    with pytest.raises(ProtocolError, match="bank selector"):
        list(FS21(transport).blocks(8))
    assert transport.send.call_count == 2


def test_v3_entry_point_preserves_dry_run_and_helpers(capsys, monkeypatch) -> None:
    namespace = runpy.run_path(str(Path(__file__).parents[1] / "scripts/fs21_direct_export_v3.py"))
    assert namespace["MAX_VALIDATED_BLOCK"] == MAX_VALIDATED_BLOCK == 161
    assert namespace["block_checksum"](161) == 0x54
    assert namespace["select_bank"](7) == select_bank(7)
    assert namespace["cli"](["--bank", "8"]) == 0
    assert "DRY RUN: no USB" in capsys.readouterr().out
    # The suite's autouse fixture rejects physical USB, including in dry runs.
    usb.core.find.assert_not_called()


def test_v3_legacy_live_arguments_delegate_to_main(monkeypatch) -> None:
    main = MagicMock(return_value=0)
    monkeypatch.setattr("fs21.cli.main", main)
    namespace = runpy.run_path(str(Path(__file__).parents[1] / "scripts/fs21_direct_export_v3.py"))
    assert namespace["cli"](["--all", "--live", "--out", "recordings", "--force"]) == 0
    assert main.call_args.args[0] == [
        "--timeout", "15000", "--retries", "2",
        "export-all", "--output", "recordings", "--force",
    ]
