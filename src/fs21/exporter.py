"""Independent per-bank exports and a non-aborting 40-bank batch coordinator."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal

from fs21.audio import ExportResult, write_wav
from fs21.device import FS21
from fs21.errors import EmptyBank, FS21Error, UnsupportedOperation, UnsupportedRecording
from fs21.protocol import BANK_COUNT, CAPTURED_BANKS
from fs21.transport import USBTransport

log = logging.getLogger(__name__)
Status = Literal["exported", "empty", "failed", "unsupported", "skipped"]
BankProgress = Callable[[int, int, int], None]


@dataclass(frozen=True)
class BankResult:
    bank: int
    status: Status
    path: Path
    blocks: int | None = None
    audio_bytes: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "bank": self.bank, "status": self.status, "path": str(self.path),
            "blocks": self.blocks, "audio_bytes": self.audio_bytes, "error": self.error,
            "selector_verified": self.bank in CAPTURED_BANKS,
        }


@dataclass(frozen=True)
class BatchReport:
    results: tuple[BankResult, ...]

    @property
    def counts(self) -> dict[str, int]:
        counts = Counter(result.status for result in self.results)
        return {status: counts[status]
                for status in ("exported", "empty", "failed", "unsupported", "skipped")}

    @property
    def exit_code(self) -> int:
        return 1 if self.counts["failed"] else 2 if self.counts["unsupported"] else 0


def export_all(
    directory: Path,
    transport_factory: Callable[[], USBTransport],
    *,
    overwrite: bool = False,
    progress: BankProgress | None = None,
    on_result: Callable[[BankResult], None] | None = None,
) -> BatchReport:
    results: list[BankResult] = []
    recover = False
    for bank in range(1, BANK_COUNT + 1):
        destination = directory / f"bank-{bank:02d}.wav"
        if destination.exists() and not overwrite:
            result = BankResult(bank, "skipped", destination)
        else:
            try:
                with transport_factory() as transport:
                    if recover:
                        transport.drain_input()
                    # Each bank validates model identity anew. No CF/D2/reset
                    # is needed on the working path; no guessed abort is sent.
                    notify = partial(progress, bank) if progress is not None else None
                    exported: ExportResult = write_wav(
                        FS21(transport).blocks(bank), destination, notify, overwrite
                    )
                result = BankResult(
                    bank, "exported", destination, exported.blocks, exported.audio_bytes
                )
                recover = False
            except EmptyBank as exc:
                result = BankResult(bank, "empty", destination, 0, error=str(exc))
                recover = True
            except UnsupportedRecording as exc:
                result = BankResult(bank, "unsupported", destination, exc.blocks, error=str(exc))
                recover = True
            except UnsupportedOperation as exc:
                result = BankResult(bank, "unsupported", destination, error=str(exc))
                recover = True
            except (FS21Error, OSError, ValueError) as exc:
                result = BankResult(bank, "failed", destination, error=str(exc))
                recover = True
        results.append(result)
        log.info("bank_export_result", extra=result.as_dict())
        if on_result is not None:
            on_result(result)
    return BatchReport(tuple(results))
