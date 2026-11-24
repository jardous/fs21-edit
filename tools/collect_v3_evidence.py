#!/usr/bin/env python3
"""Record raw command bytes and response structure, omitting captured PCM audio.

The resulting fixture preserves every request and each response's bank/count/
sequence, declared PCM size, trailer, and PCM hash. Tests can substitute zeros
for audio without redistributing the user's recordings. This is explicitly a
structural fixture, not a byte-identical reproduction of the original PCAPs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from fs21.capture import exchanges
from fs21.pcap import read_urbs
from fs21.protocol import acknowledge, select_bank


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def collect(path: Path) -> dict[str, Any]:
    records = []
    sessions: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    audio_hash = hashlib.sha256()
    ack_indices: list[int] = []
    mismatches: list[int] = []
    for exchange in exchanges(path):
        packet = exchange.request.payload
        record: dict[str, Any] = {
            "frame": exchange.request.frame,
            "request_hex": packet.rstrip(b"\0").hex(),
            "messages": [],
        }
        if packet[6] == 0xCA:
            bank = packet[7] + 1
            active = {"bank": bank, "start_frame": exchange.request.frame, "audio_bytes": 0,
                      "blocks": 0, "complete": False,
                      "selector_matches": packet == select_bank(bank - 1)}
            sessions.append(active)
            audio_hash = hashlib.sha256()
        if packet[6] == 0xCD:
            index = int.from_bytes(packet[8:10], "little")
            ack_indices.append(index)
            if index <= 161 and packet != acknowledge(index):
                mismatches.append(index)
        for message in exchange.messages():
            entry: dict[str, Any] = {"trailer_hex": message.trailer.hex()}
            if message.command == 0xCC:
                entry.update(
                    prefix_hex=message.body[:6].hex(), audio_bytes=len(message.body) - 6,
                    pcm_sha256=hashlib.sha256(message.body[6:]).hexdigest(),
                )
                if active is None:
                    raise ValueError("Audio before a bank-selection request.")
                bank0 = message.body[1]
                total = int.from_bytes(message.body[2:4], "little")
                sequence = int.from_bytes(message.body[4:6], "little")
                if bank0 != active["bank"] - 1 or sequence != active["blocks"]:
                    raise ValueError("Captured bank/sequence mismatch.")
                if "advertised_blocks" in active and total != active["advertised_blocks"]:
                    raise ValueError("Captured block count changed.")
                active["advertised_blocks"] = total
                active["blocks"] += 1
                active["audio_bytes"] += len(message.body) - 6
                audio_hash.update(message.body[6:])
            else:
                entry["body_hex"] = message.body.hex()
                if message.command == 0xCE and active is not None:
                    if (message.body[1] != active["bank"] - 1
                            or active["blocks"] != active["advertised_blocks"]):
                        raise ValueError("Incomplete captured export.")
                    active.update(complete=True, pcm_sha256=audio_hash.hexdigest())
            record["messages"].append(entry)
        records.append(record)
    return {
        "filename": path.name, "sha256": file_hash(path),
        "packet_count": sum(1 for _ in read_urbs(path)),
        "acknowledgments": len(ack_indices), "highest_ack": max(ack_indices, default=None),
        "checksum_mismatches": mismatches, "sessions": sessions, "exchanges": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = {
        "format": 1,
        "audio_policy": "PCM omitted; prefixes/lengths/trailers/hashes are capture-derived.",
        "sources": [collect(path) for path in args.captures],
    }
    with args.output.open("x") as output:
        json.dump(evidence, output, indent=2)
        output.write("\n")
    for source in evidence["sources"]:
        print(json.dumps({k: v for k, v in source.items() if k != "exchanges"}, indent=2))


if __name__ == "__main__":
    main()
