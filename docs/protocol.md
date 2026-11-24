# FS21 USB export protocol: verified evidence and boundaries

## V3 capture evidence and current support

The original capture below remains the bank-1 baseline. V3 additionally
analyzes these **actual files found in `/tmp`**; none was modified:

| Capture | SHA-256 | Packets | Observed exports |
| --- | --- | ---: | --- |
| `fs21-all-banks.pcapng` | `6d2f2fc35fe8b354d82a84b4bd6f1fde99204467567d0e2aedc032b554e5fe2d` | 401279 | Banks 1, 2, 3 only |
| `fs21-bank4.pcapng` | `e3be2126023df4f939f6dbab8eeb5b988ca0aa4ddaa695c01897d38ef1abe27c` | 247953 | Bank 4 |
| `fs21-bank8.pcapng` | `60a12b9a65d015375dbab43df5723fed2be04c5a977535b12b1e9bd0bdfbefa6` | 249502 | Bank 8 |

**The filename “all-banks” is not evidence of 40 captured exports.** The
file contains only three complete CA/CB/CC/CD/CE export sequences.

| Bank | CA argument | Integrity byte | Blocks | Last index | PCM bytes | Seconds at 44100 Hz |
| ---: | --- | --- | ---: | ---: | ---: | ---: |
| 1 | `00` | `8D` | 42 | 41 | 16403712 | 61.994376 |
| 2 | `01` | `9D` | 115 | 114 | 45153024 | 170.646349 |
| 3 | `02` | `AD` | 101 | 100 | 39394560 | 148.883447 |
| 4 | `03` | `BD` | 161 | 160 | 63106560 | 238.497959 |
| 8 | `07` | `FD` | 162 | 161 | 63505920 | 240.007256 |

All **581 CD requests** (258 + 161 + 162) match the V2/V3 XOR-basis
checksum algorithm. The bank-8 capture independently covers **162/162
indices, 0..161**, including final acknowledgment
`AA 55 04 00 00 00 CD 00 A1 00 00 54`.
There are **no captured requests above index 161**, no empty-bank exports,
and no captured failed-transfer recovery sequences.

Reproduce the compact, audio-free structural fixture:

```bash
python tools/collect_v3_evidence.py \
  /tmp/fs21-all-banks.pcapng /tmp/fs21-bank4.pcapng /tmp/fs21-bank8.pcapng \
  --output tests/fixtures/v3-evidence.json
```

The tool refuses to replace an existing output. The fixture records raw
requests, small response bodies, audio prefix/size/trailer/hash, source
hashes, and complete-export counts. Test replay substitutes zero audio;
it must not be described as original captured PCM.

## Source and reproducibility

This document is based on programmatic analysis of the actual
`/home/jiri/Downloads/looper.pcap`, not on the preliminary interpretation
or an online implementation.

| Property | Value |
| --- | --- |
| Source SHA-256 | `979f26efd625d80c3de09d30f3cca0eb1664efc6cd0bed942ebe291a41408910` |
| Format | Classic little-endian microsecond PCAP, linktype 220 |
| Total records | 65,588 |
| Capture duration | 95.269231 seconds |
| Target | bus 5, USB address 11 |
| Target records | 65,570 |
| Captured bulk OUT requests | 49, each 512 bytes |
| Successful bulk IN completions | 32,087, each 512 bytes |
| Cancelled idle IN completions | 646, `-ENOENT`, zero data |
| Complete captured exports | 1 |

Frame numbers below refer to the **original** capture. Removing unrelated
device records changes frame numbers in the derived fixture.

Run:

```bash
fs21 analyze /path/to/looper.pcap --json
fs21 --capture /path/to/looper.pcap export 1 recovered.wav
pytest
```

The independent `tshark` dissector confirms the descriptor and usbmon
header interpretation. No dissector is required at runtime.

## USB descriptors

- Normal mode VID/PID: `0483:1412`.
- USB strings identify the OEM as DONNER/CIRCLE LOOPER; the vendor
  identification response explicitly says `FLAMMA FS21`.
- Configuration 1, interface 0, alternate setting 0.
- Descriptor interface class is **0x00 in this capture**, not 0xff.
  Its bulk protocol is vendor-defined despite that descriptor value.
- Bulk OUT: `0x01`; bulk IN: `0x82`.
- Endpoint maximum packet size: **512 bytes** at high speed.
- The application checks the active descriptors rather than assuming an
  interface class. It does not issue SET_CONFIGURATION or reset.
- `0483:5703` is the user-reported firmware/HID identity, **not captured
  here**. It is detected only to refuse communication; no firmware-mode
  command is implemented.

The PCAP has a 24-byte global header and 16-byte record headers. Linux
usbmon linktype 220 uses a 64-byte pseudoheader, including a 48-byte
base header and 16-byte extension. Status and captured data length are at
offsets 28 and 36. Payload starts at 64. OUT data comes from `S`ubmit
records, IN data from successful `C`ompletions; OUT completion payloads
must not be counted again. URB IDs pair submissions with completions,
and can be reused after completion. Initial orphan idle cancellations
are unrelated to application transactions.

## Framing

Both directions start with:

| Offset | Length | Meaning |
| --- | --- | --- |
| 0 | 2 | Magic `AA 55` |
| 2 | 4 | Little-endian body length `L` |
| 6 | `L` | Command-specific body |
| `6+L` | 2 | Trailer/integrity field; semantics not fully established |
| `8+L` | variable | Padding through the next 512-byte boundary |

Observed padded size is `ceil((L + 8) / 512) * 512`.
**Do not search PCM for magic bytes** or treat each IN URB as a new
protocol message: a single audio message spans hundreds of transfers.
Read the declared length and consume the full padded message.

The first body byte is a command. It is **not universally a 16-bit
command**: identification has body `00`, and the reply has `01` followed
immediately by text. Several audio commands do have an observed zero
byte after the command, documented below without assuming its meaning.

Padding is **not guaranteed zero**. Small replies and the last audio
block include stale-looking RAM/data after their declared body/trailer.
Only the declared audio bytes belong in the WAV. Padding cannot be used
to infer recording metadata.

### Outbound integrity: validated range versus generalization

All 49 outgoing requests were checked byte for byte. Except the one-byte
identification request, captured requests have an observed zero byte
followed by a variable integrity byte after their body.

There is a partial mathematical correlation: for every captured
even-length request, the final integrity byte equals the complemented
high byte of CRC-16/XMODEM over `packet[2:6+L]`. However, this does **not**
explain the full two-byte field: the low byte is zero rather than the
corresponding CRC byte. For the odd-length identify request the trailer
is `55 AE`. No general integrity algorithm is claimed.

V3 validates the original block checksum algorithm across all 581 newer
requests:

```python
value = 0x7A
for bit, difference in enumerate((0x33, 0x66, 0xCC, 0x89, 0x03, 0x06, 0x0D, 0x1B)):
    if index & (1 << bit):
        value ^= difference
```

The `0x40` basis is `0x0D`, **not `0x0C`**. Boundary checksums:

| Index | 0 | 114 | 115 | 127 | 128 | 160 | 161 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Byte | `7A` | `14` | `27` | `62` | `61` | `67` | `54` |

The live generator is explicitly guarded to **0..161**. The byte-exact
original requests 0..41 are unchanged. A 162-block recording uses every
validated index; total counts come from CC, not from a bank-specific table.

**Inference, not observed support:** the same XOR model mathematically
predicts index 162 → `01`, 189 → `12`, and 255 → `79`. It agrees with the
complemented CRC-16/XMODEM high-byte hypothesis through index 255, but the
eight-bit-basis V3 function ignores the high index byte: at 256 it would
repeat `7A`, whereas the CRC-derived candidate predicts `6A`.
Neither candidate has captured evidence above 161. Therefore no such
request is sent, and increasing the limit to the two-byte field's maximum
would be unjustified.

### Bank selector integrity: captured and inferred

For the five captured banks, `CA + zero-based bank` is byte-exact and
verified. The old standalone scripts extrapolated
`(0x8D + 0x10 * selector) & 0xFF`. That agrees for selectors 0..7 but
**diverges from the CRC model at selector 8 / bank 9**:

| Bank | Old additive prediction | CRC-high prediction | Independently captured? |
| ---: | --- | --- | --- |
| 8 | `FD` | `FD` | Yes |
| 9 | `0D` | `0C` | No |
| 17 | `8D` | `9F` | No |
| 40 | `FD` | `D9` | No |

V3 uses the consistent **inferred CRC-high model** for uncaptured CA
selectors: complement the high byte of `crc_hqx(packet[2:8], 0)`, retaining
the observed preceding zero trailer byte. This matches every captured CA
and the independently validated CD model. It is not independent evidence
for the other 35 bank selectors, and their firmware behavior is unverified.
CLI output/JSON distinguishes captured selectors from inferred ones.

The finite transport allowlist contains only identification, CA selectors
0..39, and CD acknowledgments 0..161: **203 exact packets**, not an
arbitrary command interface. The user explicitly authorizes inferred
bank-selection reads; no unknown write/erase command is introduced.

Inbound small replies have observed trailer `02 00`; audio replies
have `00 00`. These are checked, but are **not established audio
checksums**. There is no independently verified end-to-end PCM checksum.

## Identification (implemented)

Original request frame **150**:

```text
AA 55 01 00 00 00 00 55 AE [zero padding to 512]
```

Reply frame **153** has `L=56`:

| Body offset | Length | Observed value |
| --- | --- | --- |
| 0 | 1 | `01` |
| 1 | 32 | NUL-padded `FLAMMA FS21` |
| 33 | 7 | `V1.0.0\0` |
| 40 | 7 | `V1.0.1\0` |
| 47 | 7 | `V1.0.0\0` |
| 54 | 2 | `41 00`, meaning unknown |

The version strings' individual roles are not proven; expose them as
`version_1`, `version_2`, `version_3`, not guessed firmware/hardware
versions. Frame **154** sends another identification variant with
`L=2`, body `00 00`, trailer `00 74`; frame 157 replies identically.
Only the one-byte request is enabled.

## Export state machine (original bank-1 example)

### Start request and metadata

Frame **749**:

```text
AA 55 02 00 00 00 CA 00 00 8D [zero padding to 512]
```

Body `CA 00` starts bank 1 in this baseline. The newer captures establish
the same one-based bank / zero-based selector convention for banks 2, 3,
4 and 8 (`CA 07`). Other selectors 0..39 are bounded inferred reads,
not independently verified bank mappings.

Frame **751** replies with `L=5`, body `CB 00 4D FA 00`, trailer `02 00`.
The last three body bytes decode little-endian to 64077. Their meaning
and unit are unknown; they are **not** treated as a byte length or duration.

### Crucial first block

Without another OUT request, frame **753** begins:

```text
AA 55 06 00 06 00 CC 00 2A 00 00 00 [PCM ...]
```

This is **block zero**, total count 42. It is sent as part of the
`CA` exchange, after `CB`. The provided `fs21_pcap_extract.py` only
started collecting after `CD` requests and therefore **discarded
393,216 bytes / 65,536 stereo frames / 1.486077 seconds** at the start.
That reference script was inspected but not modified.

### Audio body layout

| Body offset | Length | Meaning |
| --- | --- | --- |
| 0 | 1 | `CC` |
| 1 | 1 | Zero-based bank selector (`00` for bank 1, `07` for bank 8) |
| 2 | 2 | Total block count, LE (`2A 00` = 42 here; `A2 00` = 162 in bank 8) |
| 4 | 2 | Zero-based block sequence, LE (0..41) |
| 6 | `L-6` | Packed 24-bit stereo PCM |

For full blocks `L=393222` (`06 00 06 00`), the PCM is 393216 bytes,
exactly 65536 stereo frames. The logical message is 393230 bytes;
wire padding makes it 393728 bytes, **769 IN completions**.

After receiving a block, the host acknowledges **that block's sequence**.
Frame **2290**, after complete block zero:

```text
AA 55 04 00 00 00 CD 00 00 00 00 7A [zero padding to 512]
```

Body `CD 00` + little-endian sequence zero acknowledges block zero and
advances to block one. This is not simply an indexed request for
the block number named by the command. The next reply is:

```text
AA 55 06 00 06 00 CC 00 2A 00 01 00 [PCM ...]
```

The same stop-and-wait sequence repeats: receive block N, acknowledge N,
then receive N+1. Request integrity bytes for acknowledgments 0..41:

```text
7A 49 1C 2F B6 85 D0 E3 F3 C0 95 A6 3F 0C 59 6A
79 4A 1F 2C B5 86 D3 E0 F0 C3 96 A5 3C 0F 5A 69
7C 4F 1A 29 B0 83 D6 E5 F5 C6
```

Each request is exactly 512 bytes, including zero padding.

CC and CE both echo the bank selector; CB's second byte remains `00`
even for banks 2, 3, 4 and 8 and must not be used as a bank echo.
Bank, sequence, total count and frame alignment are checked on every block.

The block count is an **unsigned little-endian 16-bit field**: the encoding
can represent 0..65535. Sequences are also unsigned 16-bit. That field width
does not establish a hardware recording limit or a valid checksum range,
and no special encoding for 65536 blocks is observed. A zero count is
handled as empty only with sequence zero and no PCM bytes; this empty form
is inferred and synthetically tested, not present in the captures.
Timeouts, malformed data, and unexpected early CE are failures, not empty.

### Final block and termination

- Frame **63958** acknowledges block 40 (`28 00`).
- Frame **63961** starts block 41 (`29 00`), `L=281862`
  (`06 4D 04 00`), with **281856 PCM bytes**.
- Its padded wire length is 282112 bytes, 551 IN completions.
- Frame **65062** acknowledges block 41:
  `AA 55 04 00 00 00 CD 00 29 00 00 C6`.
- Frame **65065** replies with `L=2`, body `CE 00`, trailer `02 00`.
  This is the observed terminal acknowledgment.

There are **42 audio blocks but 42 `CD` requests**. The final `CD` receives
`CE`, not audio. The earlier estimate of approximately 41 blocks reflects
missing block zero in the reference extractor.

After termination the editor sends `CF 00` (frame 65066, no response),
then `D2 00` (65068), receiving `D3` (65071). Their independent semantics
are unknown, and neither command is enabled. They are not needed to
reconstruct the captured recording. Whether `CF` is needed for returning
every firmware version to an idle editor state remains unverified.
No guessed cleanup/abort command is sent, even after an error.

## Other captured traffic (not enabled)

| OUT frame | Body | Reply | What is actually known |
| --- | --- | --- | --- |
| 147 | `D0 00` | `D1 00`, `L=2` | Startup exchange; semantics unknown |
| 154 | `00 00` | `01`, `L=56` | Alternate identification request |
| 158 | `C0 2D` | `C1`, `L=266` | Metadata-like reply, not decoded |
| 65066 | `CF 00` | none | Post-export command; meaning unknown |
| 65068 | `D2 00` | `D3`, `L=33` | Post-export metadata-like reply |

`C1` begins `C1 28 FF FF FF FF FF 00 00 00`, then a sequence of
little-endian-looking 32-bit values. A 40-slot count/bitmap interpretation
is plausible, but one capture does not verify value meanings, units,
occupancy, or slot mapping. `C0 2D` must not be relabeled as a proven
slot-list command. `fs21 list` therefore still exits 2 before USB access.
Export-all does not guess occupancy from C1: it attempts each of the
40 read-only CA selectors and reports the actual result.

## WAV reconstruction and integrity

Audio samples are copied unchanged as signed, little-endian, packed
24-bit stereo PCM, using the **user-provided** 44100 Hz format.
The capture confirms frame/block alignment but does not independently
prove the sample rate or left/right channel assignment; no recognizable
WAV header or self-describing rate field is present in the audio replies.

Complete reconstruction:

- 41 full blocks plus one partial block.
- 16,403,712 PCM bytes.
- 2,733,952 stereo sample frames.
- 61.99437641723356 seconds at 44100 Hz.
- PCM SHA-256:
  `aec8b1bcf99435a263bbae93cc962fb9b7a9aec2377f6353f763b9ed055b0987`.

The exporter validates URB success/pairing, message size, order, total
counts, trailer constants, stereo frame alignment, and terminal `CE`.
Missing/duplicate/reordered blocks, short captures, unknown messages,
and inconsistent totals are fatal. The WAV is published atomically only
after the complete transfer validates; a failed export leaves existing
outputs intact.

## Safety boundary and future evidence needed

- No raw command interface, upload, erase, format, firmware operation,
  USB reset, automatic driver detachment, or halt clearing.
- No blind OUT retries, even for apparently transient write errors.
  Read timeouts/EINTR only are retried within a bounded count.
- A per-user advisory device lock excludes concurrent fs21 processes.
  It cannot exclude an unrelated manufacturer editor; close other editors.
- Live reads are limited to selectors 0..39 and **162 blocks**, because
  no acknowledgment for block 162 or later exists in the evidence.
- A recording advertising more blocks is rejected after its first block
  is read; no unverified acknowledgment/abort follows. No partial output
  is published and existing successful WAVs are protected.

Export-all opens/releases the interface per bank and continues after a
bank failure. After failed/unsupported/zero-block transfers it drains stale
bulk IN data to a quiet timeout of at most 200 ms, with a budget derived
from one maximum padded response plus two small packets. It then requires
a fresh valid identification response before sending the next CA.
No OUT is blindly retried; no USB reset, CF, D2 or guessed abort is sent.
This is bounded read-only host resynchronization, **not captured proof
that every firmware state recovers**. If recovery/identification fails,
that bank is failed and later banks are still attempted.

Remaining useful captures: bank 9 (distinguishing selector checksum
hypotheses), other uncaptured selectors, known empty banks, and indices
162+ including 255/256. A safe recovery sequence after interruption also
needs independent observation. Do not erase or overwrite recordings to
obtain these.

## Historical V1 live Linux validation, 2026-11-24

After the user granted a temporary device-node ACL, the application:

1. Claimed interface 0 without configuration changes or driver detachment.
2. Read identification successfully via the captured one-byte request.
3. Sent the exact captured `CA 00` request.
4. Received `CB`, then block zero, then all remaining ordered blocks.
5. Sent the 42 exact captured acknowledgments and received terminal `CE`.
6. Released the interface and published the WAV atomically.
7. Successfully read identification again after export, without `CF`, `D2`,
   reset, or reconnect.

The complete live WAV is byte-for-byte identical to the corrected capture
reconstruction:
`f81780389a4bd0013c51343164c3f402a21465de7d8a700c8566d557424dfe11`
(SHA-256 including WAV header). This validates the implemented native
export path on this pedal and firmware combination; it does not establish
other selectors, longer recordings, or undocumented metadata semantics.

A second complete native export through the real PySide6 window/controller
and its background worker also received 42 blocks and produced the same WAV
hash, with final UI status `Export complete — 61.994 seconds.` This repeat
completed without `CF`, `D2`, reset, or reconnect. The window was driven
offscreen to avoid interfering with the user's desktop.

Final development checks: 113 automated tests passed; Ruff and strict mypy
passed; a pure-Python wheel built successfully. The automated suite uses
only recorded traffic and mocks; these two live transfers were explicit
manual development checks, not automatic hardware tests.
