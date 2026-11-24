#!/usr/bin/env python3
"""Select only the FS21's records without rewriting any captured USB bytes."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import struct
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--bus", type=int, default=5)
    parser.add_argument("--device", type=int, default=11)
    args = parser.parse_args()
    digest = hashlib.sha256(args.capture.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    selected = 0
    with args.capture.open("rb") as source, args.output.open("xb") as target:
        with gzip.GzipFile(filename="", fileobj=target, mode="wb", mtime=0) as out:
            header = source.read(24)
            if header[:4] != b"\xd4\xc3\xb2\xa1" or header[20:24] != struct.pack("<I", 220):
                raise ValueError("This fixture tool requires little-endian linktype 220 PCAP.")
            out.write(header)
            while record := source.read(16):
                if len(record) != 16:
                    raise ValueError("Truncated PCAP record.")
                _, _, size, _ = struct.unpack("<IIII", record)
                raw = source.read(size)
                if len(raw) != size or size < 64:
                    raise ValueError("Truncated USB packet.")
                if raw[11] == args.device and struct.unpack_from("<H", raw, 12)[0] == args.bus:
                    out.write(record)
                    out.write(raw)
                    selected += 1
    print(json.dumps({"source_sha256": digest, "selected_records": selected,
                      "fixture_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()},
                     indent=2))


if __name__ == "__main__":
    main()
