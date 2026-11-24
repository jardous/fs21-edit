"""Headless CLI; importing it does not load Qt."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from fs21 import __version__
from fs21.audio import write_wav
from fs21.capture import capture_summary, captured_blocks, exchanges
from fs21.device import ENUMERATION_UNSUPPORTED, FS21
from fs21.errors import EmptyBank, FS21Error, UnsupportedOperation
from fs21.exporter import BankResult, export_all
from fs21.protocol import (
    BANK_COUNT,
    CAPTURED_BANKS,
    MAX_VALIDATED_BLOCKS,
    decode_identity,
    select_bank,
)
from fs21.transport import USBTransport


def usb_address(value: str) -> tuple[int, int]:
    try:
        bus, address = (int(part) for part in value.split(":"))
        if bus < 1 or not 1 <= address <= 127:
            raise ValueError
        return bus, address
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected BUS:DEVICE, e.g. 5:11.") from exc


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer.")
    return number


def bank_number(value: str) -> int:
    bank = positive(value)
    if bank > BANK_COUNT:
        raise argparse.ArgumentTypeError("Bank must be 1..40.")
    return bank


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="fs21", description="Capture-verified, read-only FLAMMA FS21 exporter."
    )
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--timeout", type=positive, default=3000, help="USB timeout in ms (3000).")
    root.add_argument("--retries", type=int, choices=range(11), default=2,
                      help="Read timeout retries; ambiguous writes are never retried (2).")
    root.add_argument("--usb-address", type=usb_address)
    root.add_argument("--capture", type=Path, help="Use a PCAP instead of hardware (info/export).")
    root.add_argument("--session", type=positive, default=1, help="Matching capture session (1).")
    root.add_argument("--verbose", "-v", action="store_true")
    sub = root.add_subparsers(dest="command", required=True)
    info = sub.add_parser("info", help="Read USB descriptors and verified identification.")
    info.add_argument("--json", action="store_true")
    listing = sub.add_parser("list", help="Explain the unverified slot-enumeration limitation.")
    listing.add_argument("--json", action="store_true")
    export = sub.add_parser("export", help="Export a bank (1..40), at most 162 validated blocks.")
    export.add_argument("slot", type=bank_number)
    export.add_argument("output", type=Path, nargs="?", help="Default: bank-NN.wav.")
    export.add_argument("--force", action="store_true", help="Replace an existing local WAV.")
    export.add_argument("--json", action="store_true")
    export.add_argument(
        "--dry-run", action="store_true", help="Show read commands without USB access."
    )
    export_all = sub.add_parser(
        "export-all", help="Export all 40 banks, continuing after individual failures."
    )
    export_all.add_argument(
        "directory", type=Path, nargs="?", help="Legacy positional output folder."
    )
    export_all.add_argument("--output", type=Path, dest="output_directory")
    export_all.add_argument("--force", action="store_true", help="Replace existing local WAVs.")
    export_all.add_argument("--json", action="store_true")
    export_all.add_argument("--dry-run", action="store_true")
    analyze = sub.add_parser("analyze", help="Analyze captured exchanges without USB access.")
    analyze.add_argument("pcap", type=Path)
    analyze.add_argument("--json", action="store_true")
    sub.add_parser("gui", help="Launch the optional PySide6 UI.")
    return root


def _output(data: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        for key, value in data.items():
            print(f"{key}: {value}")


def _bank_line(result: BankResult) -> str:
    detail = f" ({result.blocks} blocks)" if result.blocks is not None and result.blocks else ""
    error = f": {result.error}" if result.status == "failed" else ""
    inferred = " [inferred selector]" if result.bank not in CAPTURED_BANKS else ""
    return f"Bank {result.bank:02d}: {result.status}{detail}{error}{inferred}"


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "list":
            raise UnsupportedOperation(ENUMERATION_UNSUPPORTED)
        if args.command in ("export", "export-all") and args.dry_run:
            banks = range(1, BANK_COUNT + 1) if args.command == "export-all" else [args.slot]
            preview = {
                "dry_run": True, "max_validated_blocks": MAX_VALIDATED_BLOCKS,
                "banks": [
                    {"bank": bank, "request_hex": select_bank(bank - 1)[:10].hex(),
                     "selector_verified": bank in CAPTURED_BANKS}
                    for bank in banks
                ],
            }
            if args.json:
                _output(preview, True)
            else:
                for bank in banks:
                    request = select_bank(bank - 1)
                    label = "captured" if bank in CAPTURED_BANKS else "inferred"
                    print(f"Bank {bank:02d}: CA {request[7]:02X}, "
                          f"checksum {request[9]:02X} [{label}]")
                print("DRY RUN: no USB transfers made. Validated block indices: 0..161.")
            return 0
        if args.command == "gui":
            if args.capture:
                raise UnsupportedOperation("GUI uses live USB; use the CLI for capture export.")
            from fs21.gui import run
            return run(args.timeout, args.retries, args.usb_address)
        if args.command == "analyze":
            summary = capture_summary(args.pcap, args.usb_address)
            _output(summary, args.json)
            return 0
        if args.command == "export-all":
            if args.capture:
                raise UnsupportedOperation(
                    "Offline export-all is not implemented; use --capture FILE export BANK."
                )
            if args.directory is not None and args.output_directory is not None:
                raise FS21Error("Use either a positional folder or --output, not both.")
            directory = args.output_directory or args.directory or Path("./fs21-export")
            print(
                "Banks 1–4 and 8 have captured selectors; other bank selectors are inferred reads.",
                file=sys.stderr,
            )
            def factory() -> USBTransport:
                return USBTransport(args.timeout, args.retries, args.usb_address)
            def batch_progress(bank: int, done: int, total: int) -> None:
                print(f"\rBank {bank:02d}: block {done}/{total} ({done * 100 // total}%)",
                      end="", file=sys.stderr, flush=True)
            def completed(result: BankResult) -> None:
                print(file=sys.stderr)
                if not args.json:
                    print(_bank_line(result), flush=True)
            report = export_all(directory, factory, overwrite=args.force,
                                progress=batch_progress, on_result=completed)
            if args.json:
                _output({"summary": report.counts,
                         "banks": [result.as_dict() for result in report.results]}, True)
            else:
                print()
                for status, count in report.counts.items():
                    print(f"{status.capitalize()}: {count}")
            return report.exit_code
        if args.command == "export":
            if args.output is None:
                args.output = Path(f"bank-{args.slot:02d}.wav")
            if args.slot not in CAPTURED_BANKS:
                print(f"Bank {args.slot:02d}: using an inferred read-only selector.",
                      file=sys.stderr)
            if args.output.exists() and not args.force:
                raise FS21Error(f"Output already exists: {args.output}. Use --force to replace it.")
            def progress(done: int, total: int) -> None:
                print(f"\rBank {args.slot:02d}: block {done}/{total} ({done * 100 // total}%)",
                      end="", file=sys.stderr, flush=True)
            if args.capture:
                result = write_wav(
                    captured_blocks(args.capture, args.usb_address, args.session, bank=args.slot),
                    args.output, progress, args.force,
                )
            else:
                with USBTransport(args.timeout, args.retries, args.usb_address) as transport:
                    result = write_wav(FS21(transport).blocks(args.slot), args.output,
                                       progress, args.force)
            print(file=sys.stderr)
            _output({"bank": args.slot, "status": "exported",
                     "selector_verified": args.slot in CAPTURED_BANKS,
                     "path": str(result.path), "blocks": result.blocks,
                     "audio_bytes": result.audio_bytes, "duration_seconds": result.duration,
                     "sample_rate": 44100, "channels": 2, "bits_per_sample": 24}, args.json)
        elif args.command == "info":
            if args.capture:
                for exchange in exchanges(args.capture, args.usb_address):
                    for message in exchange.messages():
                        if message.command == 1:
                            _output(decode_identity(message), args.json)
                            return 0
                raise FS21Error("Capture contains no identification response.")
            with USBTransport(args.timeout, args.retries, args.usb_address) as transport:
                data = transport.descriptor_info()
                data.update(FS21(transport).info())
                data["limitations"] = ENUMERATION_UNSUPPORTED
                _output(data, args.json)
        return 0
    except EmptyBank as exc:
        _output({"bank": exc.bank, "status": "empty", "blocks": 0},
                getattr(args, "json", False))
        return 0
    except (FS21Error, OSError, ValueError, ImportError) as exc:
        if args.verbose:
            logging.exception("Operation failed")
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc),
                              "unsupported": isinstance(exc, UnsupportedOperation)}),
                  file=sys.stderr)
        else:
            print(f"fs21: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, UnsupportedOperation) else 1
    except KeyboardInterrupt:
        print("\nfs21: cancelled; no incomplete WAV published.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
