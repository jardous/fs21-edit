#!/usr/bin/env python3
"""V3 compatibility entry point, using the existing fs21 package's safe exporter.

Original flags are retained: --bank/--all, --out, and opt-in --live.
Without --live this script still performs only a dry run. V2 is kept
unchanged as a historical reference; USB and WAV logic now has one owner.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permit invocation directly from a checkout as well as from an installed venv.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fs21.cli import bank_number, main
from fs21.protocol import (
    EP_IN,
    EP_OUT,
    MAX_VALIDATED_BLOCK,
    PID,
    VID,
    block_checksum,
    select_bank,
)
from fs21.protocol import (
    acknowledge as block_request,
)

__all__ = [
    "EP_IN", "EP_OUT", "MAX_VALIDATED_BLOCK", "PID", "VID",
    "block_checksum", "block_request", "select_bank", "cli",
]


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bank", type=bank_number, help="Bank number (1..40).")
    group.add_argument("--all", action="store_true", help="Attempt all 40 banks.")
    parser.add_argument("--out", "--output", type=Path, default=Path("./fs21-export"))
    parser.add_argument("--live", action="store_true", help="Actually access USB.")
    parser.add_argument("--force", action="store_true", help="Replace existing local WAVs.")
    parser.add_argument("--timeout", type=int, default=15000, help="USB timeout in ms.")
    parser.add_argument("--retries", type=int, choices=range(11), default=2)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive.")
    translated = ["--timeout", str(args.timeout), "--retries", str(args.retries)]
    if args.all:
        translated += ["export-all", "--output", str(args.out)]
    else:
        translated += ["export", str(args.bank), str(args.out / f"bank-{args.bank:02d}.wav")]
    if not args.live:
        translated += ["--dry-run"]
    if args.force:
        translated += ["--force"]
    return main(translated)


if __name__ == "__main__":
    sys.exit(cli())
