from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fs21.cli import main
from tests.replay import SyntheticBank


@pytest.mark.parametrize("argv", [["list"]])
def test_unsupported_commands_never_open_usb(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    transport = MagicMock()
    monkeypatch.setattr("fs21.cli.USBTransport", transport)
    assert main(argv) == 2
    transport.assert_not_called()
    assert "verified" in capsys.readouterr().err


def test_offline_info(capture_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--capture", str(capture_path), "info", "--json"]) == 0
    assert '"model": "FLAMMA FS21"' in capsys.readouterr().out


@pytest.mark.parametrize("bank", [1, 8, 40])
def test_export_default_filename(
    bank: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("fs21.cli.USBTransport", lambda *_: SyntheticBank(bank))
    assert main(["export", str(bank)]) == 0
    assert (tmp_path / f"bank-{bank:02d}.wav").read_bytes().startswith(b"RIFF")


def test_export_all_json_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    devices = iter(SyntheticBank(bank) for bank in range(1, 41))
    monkeypatch.setattr("fs21.cli.USBTransport", lambda *_: next(devices))
    assert main(["export-all", "--output", str(tmp_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["exported"] == 40
    assert len(report["banks"]) == 40
    assert report["banks"][7]["selector_verified"] is True
    assert report["banks"][39]["selector_verified"] is False


def test_export_all_legacy_positional_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    devices = iter(SyntheticBank(bank) for bank in range(1, 41))
    monkeypatch.setattr("fs21.cli.USBTransport", lambda *_: next(devices))
    assert main(["export-all", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("bank-*.wav"))) == 40


def test_conflicting_output_folders_do_not_open_usb(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = MagicMock()
    monkeypatch.setattr("fs21.cli.USBTransport", factory)
    assert main(["export-all", "one", "--output", "two"]) == 1
    factory.assert_not_called()


@pytest.mark.parametrize("argv", [["export", "8", "--dry-run"],
                                 ["export-all", "--dry-run"]])
def test_dry_run_never_opens_usb(argv, monkeypatch) -> None:
    factory = MagicMock()
    monkeypatch.setattr("fs21.cli.USBTransport", factory)
    assert main(argv) == 0
    factory.assert_not_called()


def test_capture_export_all_never_falls_back_to_live_usb(monkeypatch) -> None:
    factory = MagicMock()
    monkeypatch.setattr("fs21.cli.USBTransport", factory)
    assert main(["--capture", "capture.pcapng", "export-all"]) == 2
    factory.assert_not_called()


@pytest.mark.parametrize("bank", ["0", "41", "-1"])
def test_invalid_cli_banks(bank: str) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["export", bank])
    assert caught.value.code == 2


def test_single_empty_bank_is_not_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fs21.cli.USBTransport", lambda *_: SyntheticBank(3, 0))
    output = tmp_path / "empty.wav"
    assert main(["export", "3", str(output)]) == 0
    assert not output.exists()
