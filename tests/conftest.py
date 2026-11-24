from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fs21.capture import Exchange, exchanges

FIXTURE = Path(__file__).parent / "fixtures" / "fs21-export.pcap.gz"


@pytest.fixture(autouse=True)
def forbid_physical_usb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "usb.core.find",
        MagicMock(side_effect=AssertionError("Automated tests must not access physical USB.")),
    )


@pytest.fixture(scope="session")
def capture_path() -> Path:
    if not FIXTURE.is_file():
        pytest.skip(
            "Private audio-bearing fixture is not distributed; "
            "see tests/fixtures/README.md to recreate it locally."
        )
    return FIXTURE


@pytest.fixture(scope="session")
def captured_exchanges(capture_path: Path) -> tuple[Exchange, ...]:
    return tuple(exchanges(capture_path))
