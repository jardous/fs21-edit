from pathlib import Path
from unittest.mock import MagicMock

from fs21.exporter import export_all
from tests.replay import SyntheticBank


def test_export_all_continues_after_individual_failures(tmp_path: Path) -> None:
    existing = tmp_path / "bank-01.wav"
    existing.write_bytes(b"previous successful export")
    transports = {
        bank: SyntheticBank(
            bank, total=0 if bank == 3 else 189 if bank == 9 else 162 if bank == 8 else 1,
            failure="timeout" if bank == 4 else "wrong_end" if bank == 11 else None,
        ) for bank in range(2, 41)
    }
    attempts = iter(transports.values())
    progress, completed = [], []
    report = export_all(
        tmp_path, lambda: next(attempts),
        progress=lambda bank, n, total: progress.append((bank, n, total)),
        on_result=completed.append,
    )
    assert report.counts == {
        "exported": 35, "empty": 1, "failed": 2, "unsupported": 1, "skipped": 1,
    }
    assert report.exit_code == 1
    assert len(completed) == len(report.results) == 40
    assert report.results[7].blocks == 162
    assert report.results[8].blocks == 189
    assert report.results[39].status == "exported"
    assert (8, 162, 162) in progress
    assert existing.read_bytes() == b"previous successful export"
    assert all(transport.closed for transport in transports.values())
    assert {bank for bank, transport in transports.items() if transport.drained} == {4, 5, 10, 12}
    assert not list(tmp_path.glob("*.part"))
    assert len(list(tmp_path.glob("*.wav"))) == 36


def test_resume_skips_all_existing_files_without_usb(tmp_path: Path) -> None:
    for bank in range(1, 41):
        (tmp_path / f"bank-{bank:02d}.wav").write_bytes(b"keep")
    factory = MagicMock()
    report = export_all(tmp_path, factory)
    assert report.counts["skipped"] == 40 and report.exit_code == 0
    factory.assert_not_called()
    assert all(path.read_bytes() == b"keep" for path in tmp_path.iterdir())


def test_force_explicitly_replaces_existing_local_files(tmp_path: Path) -> None:
    for bank in range(1, 41):
        (tmp_path / f"bank-{bank:02d}.wav").write_bytes(b"old")
    transports = iter(SyntheticBank(bank) for bank in range(1, 41))
    report = export_all(tmp_path, lambda: next(transports), overwrite=True)
    assert report.counts["exported"] == 40
    assert all(path.read_bytes().startswith(b"RIFF") for path in tmp_path.iterdir())


def test_unsupported_only_has_distinct_exit_status(tmp_path: Path) -> None:
    transports = iter(SyntheticBank(bank, 189) for bank in range(1, 41))
    report = export_all(tmp_path, lambda: next(transports))
    assert report.counts["unsupported"] == 40 and report.exit_code == 2
    assert not list(tmp_path.iterdir())


def test_open_failures_do_not_abort_remaining_banks(tmp_path: Path) -> None:
    from fs21.errors import TransportError
    factory = MagicMock(side_effect=TransportError("device disconnected"))
    report = export_all(tmp_path, factory)
    assert factory.call_count == 40
    assert report.counts["failed"] == 40


def test_timeout_is_never_classified_as_empty(tmp_path: Path) -> None:
    transports = iter(SyntheticBank(bank, failure="timeout") for bank in range(1, 41))
    report = export_all(tmp_path, lambda: next(transports))
    assert report.counts["failed"] == 40
    assert report.counts["empty"] == 0


def test_corrupt_audio_preserves_old_outputs_even_with_force(tmp_path: Path) -> None:
    for bank in range(1, 41):
        (tmp_path / f"bank-{bank:02d}.wav").write_bytes(b"old")
    transports = iter(SyntheticBank(bank, failure="short_audio") for bank in range(1, 41))
    report = export_all(tmp_path, lambda: next(transports), overwrite=True)
    assert report.counts["failed"] == 40
    assert all(path.read_bytes() == b"old" for path in tmp_path.iterdir())
