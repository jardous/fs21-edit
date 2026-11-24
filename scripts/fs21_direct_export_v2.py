#!/usr/bin/env python3
"""Experimental, read-only FLAMMA FS21 USB audio exporter.

Reverse engineered from FS21 USB captures containing exports of banks 1, 2, 3, 4.
No import, erase or firmware functionality. Defaults to dry-run.

Dependencies for --live: python-pyusb (and libusb).
Examples:
  python fs21_direct_export.py --bank 4         # dry run only
  python fs21_direct_export.py --bank 4 --live   # EXPERIMENTAL, actual USB
  python fs21_direct_export.py --all --live

WARNING: bank>=5 commands are inferred rather than verified by capture.
Block checksum is empirically validated for block indices 0..160 only.
"""
from __future__ import annotations
import argparse
import sys
import wave
from pathlib import Path

VID, PID = 0x0483, 0x1412
EP_IN, EP_OUT = 0x82, 0x01
MAX_VALIDATED_BLOCK = 160


def frame(cmd: int, subcmd: int, index: int, checksum: int) -> bytes:
    if not 0 <= index <= 65535:
        raise ValueError("index out of range")
    header = bytearray(12)
    header[:2] = b'\xaa\x55'
    header[2] = cmd
    header[6] = subcmd
    header[8:10] = index.to_bytes(2, 'little')
    header[11 if cmd == 4 else 9] = checksum
    return bytes(header).ljust(512, b'\x00')


def simple_cmd(subcmd: int, index: int, checksum: int) -> bytes:
    # For CMD=0x02 the argument is at offset 7 (not offsets 8:9).
    if not 0 <= index <= 255:
        raise ValueError('simple command argument out of range')
    result = bytearray(frame(2, subcmd, 0, checksum))
    result[7] = index
    return bytes(result)


def select_bank(bank0: int) -> bytes:
    # Indices 0,1,2,3 have observed checksums 0x8d, 0x9d, 0xad, 0xbd.
    # For higher banks this checksum extrapolation IS NOT validated.
    return simple_cmd(0xca, bank0, (0x8d + 0x10 * bank0) & 0xff)


def block_checksum(index: int) -> int:
    # Reproduces every CD checksum in captures of 4 banks, indices 0..160.
    # 0x40 basis is 0x0d (not 0x0c).
    if not 0 <= index <= MAX_VALIDATED_BLOCK:
        raise ValueError(f'Block {index}: checksum not validated beyond {MAX_VALIDATED_BLOCK}')
    value = 0x7a
    for bit, diff in enumerate((0x33, 0x66, 0xcc, 0x89, 0x03, 0x06, 0x0d, 0x1b)):
        if index & (1 << bit):
            value ^= diff
    return value


def block_request(index: int) -> bytes:
    return frame(4, 0xcd, index, block_checksum(index))


def read_frame(dev, timeout_ms=15000) -> bytes:
    # All observed responses are padded to 512-byte boundaries.
    first = bytes(dev.read(EP_IN, 512, timeout=timeout_ms))
    if len(first) != 512 or first[:2] != b'\xaa\x55':
        raise RuntimeError(f'Unexpected frame header: {first[:24].hex(" ")}')
    length_field = int.from_bytes(first[2:6], 'little')
    # For CC audio data length=payload+6; packet header is 12 bytes.
    frame_size = length_field + 6
    if frame_size < 8 or frame_size > 2_000_000:
        raise RuntimeError(f'Invalid response length {frame_size}')
    packet_size = ((frame_size + 511) // 512) * 512
    chunks = [first]
    received = 512
    while received < packet_size:
        chunk = bytes(dev.read(EP_IN, 512, timeout=timeout_ms))
        if len(chunk) != 512:
            raise RuntimeError(f'Partial 512-byte USB transfer: {len(chunk)}')
        chunks.append(chunk)
        received += len(chunk)
    return b''.join(chunks)[:frame_size]


def expect_subcmd(dev, command: int, *, bank=None, sequence=None):
    response = read_frame(dev)
    if response[6] != command:
        raise RuntimeError(f'Expected response 0x{command:02x}, got 0x{response[6]:02x}')
    if bank is not None and response[7] != bank:
        raise RuntimeError(f'Unexpected bank: {response[7]} != {bank}')
    if sequence is not None and int.from_bytes(response[10:12], 'little') != sequence:
        raise RuntimeError(f'Unexpected block sequence {response[10:12].hex()} != {sequence}')
    return response


def send(dev, packet: bytes):
    n = dev.write(EP_OUT, packet, timeout=15000)
    if n != len(packet):
        raise RuntimeError(f'USB short write: {n}/{len(packet)}')


def setup(dev):
    # Captured Windows editor initialization before selecting bank 0.
    packet = bytearray(512)
    packet[:9] = bytes.fromhex('aa 55 01 00 00 00 00 55 ae')
    send(dev, bytes(packet))
    read_frame(dev)
    send(dev, simple_cmd(0x00, 0, 0x74))
    read_frame(dev)
    send(dev, simple_cmd(0xc0, 0x2d, 0x97))
    read_frame(dev)


def export_bank(dev, bank0: int, destination: Path):
    send(dev, select_bank(bank0))
    expect_subcmd(dev, 0xcb)       # metadata header
    first = expect_subcmd(dev, 0xcc, bank=bank0, sequence=0)
    total = int.from_bytes(first[8:10], 'little')
    if not 1 <= total <= MAX_VALIDATED_BLOCK + 1:
        raise RuntimeError(f'Unsupported/unvalidated block count {total}; no further transfers sent')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destination), 'wb') as w:
        w.setnchannels(2); w.setsampwidth(3); w.setframerate(44100)
        written = 0
        def append_audio(resp):
            nonlocal written
            length = int.from_bytes(resp[2:6], 'little') - 6
            audio = resp[12:12+length]
            if len(audio) != length or length % 6:
                raise RuntimeError(f'Invalid stereo 24-bit PCM length {length}')
            w.writeframesraw(audio)
            written += len(audio)
        append_audio(first)
        for index in range(total - 1):
            send(dev, block_request(index))
            append_audio(expect_subcmd(dev, 0xcc, bank=bank0, sequence=index+1))
        send(dev, block_request(total-1))
        expect_subcmd(dev, 0xce, bank=bank0)
    print(f'Bank {bank0+1:02d}: {total} blocks, {written} bytes, {written/6/44100:.2f} sec -> {destination}', flush=True)


def cli():
    p=argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--bank', type=int, help='Bank number (1..40)')
    g.add_argument('--all', action='store_true', help='Export banks 1..40')
    p.add_argument('--out', type=Path, default=Path('./fs21-export'))
    p.add_argument('--live', action='store_true', help='Actually communicate with pedal; otherwise dry-run')
    args=p.parse_args()
    banks=list(range(40)) if args.all else [args.bank-1]
    if any(not 0 <= b < 40 for b in banks):
        p.error('bank must be 1..40')
    for b in banks:
        print(f'Bank {b+1:02d}: CA {b:02x}, checksum {(0x8d+0x10*b)&0xff:02x}'+ (' [inferred]' if b >= 4 else ' [captured]'))
    if not args.live:
        print('DRY RUN: no USB transfers made. Add --live to test experimentally.')
        return
    import usb.core
    import usb.util
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit('FS21 not detected. Disconnect it from the Windows VM first.')
    try:
        if dev.is_kernel_driver_active(0):
            sys.exit('USB interface owned by kernel driver; aborting')
        dev.set_configuration()
        usb.util.claim_interface(dev, 0)
        setup(dev)
        for pos,b in enumerate(banks):
            if pos:
                send(dev, simple_cmd(0xcf, 0, 0x72))
                send(dev, simple_cmd(0xd2, b-1, (0x07+0x10*(b-1))&0xff))
                expect_subcmd(dev, 0xd3)
            export_bank(dev, b, args.out / f'bank-{b+1:02d}.wav')
    finally:
        usb.util.dispose_resources(dev)

if __name__=='__main__':
    cli()
