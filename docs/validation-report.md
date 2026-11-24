# Change and validation report

## Changes

- Kept a single current entry point, `scripts/fs21_direct_export.py`,
  backed by the existing modular package.
- Integrated the validated upper block index **161** into the main
  protocol/device path. Counts still come from each device CC response.
  The earlier 42-block and 161-block limits are superseded.
- Kept block checksums and the original bank-1 packets byte-identical.
  All **581 recorded CD requests** match, including all 162 bank-8 requests.
- Added bank echo validation for CC and CE and bounded bank selection 1..40.
  Banks 1–4 and 8 are captured; other selectors remain inferred reads.
- Found an additional inference limitation: the original additive bank checksum
  formula diverges from the CRC-derived model at bank 9. Uncaptured
  selectors now use that consistent CRC-high model, with explicit warnings;
  they are not presented as independently verified.
- Added resumable, non-aborting export-all with per-bank progress, protected
  existing files, explicit `--force`, JSON/text summaries, and separate
  exported/empty/failed/unsupported/skipped statuses.
- Added bounded stale-input draining plus fresh identification after a
  failed/empty/unsupported attempt. No reset, speculative abort, CF/D2,
  configuration change, or blind OUT retry is used.
- Preserved the script's `--bank`, `--all`, `--out`, `--live`, and default
  dry-run behavior; it delegates to the tested package instead of
  duplicating USB/WAV code. The checksum/selection helpers are re-exported.
- Added dependency-free Linux-usbmon PCAPNG reading; the old PCAP reader,
  atomic WAV writer and resource cleanup remain functional.
- Replaced the bank-1 dropdown GUI with a scrollable 40-slot table, an export
  button on every row, and a working export-all action sharing the CLI's
  batch coordinator. Added per-slot results/errors, recording and batch
  progress, summary counts, keyboard controls, and safe retry recovery.
- Simplified the list to just slot numbers and export buttons. Results,
  progress and errors remain below the list; failed/unsupported slot
  numbers are listed explicitly without diagnostic or status columns.
- Isolated automatic connection checks from foreground transfer state so
  polling does not disable the export-folder field or steal keyboard focus.
  Unchanged connections leave controls/results untouched; detection errors
  do not replace export outcomes, and closing waits safely for a pending check.

## Evidence and tests

Found and analyzed the three new captures in `/tmp` and the original exporter
scripts. Contrary to its filename, `fs21-all-banks.pcapng` contains
**only banks 1–3**, not all 40.

Complete **real-capture** reconstructions passed for banks 1, 2, 3, 4 and 8.
Their observed block counts are 42, 115, 101, 161 and 162 respectively.
Bank 8 was also exported by the actual offline CLI:

```bash
.venv/bin/fs21 --capture /tmp/fs21-bank8.pcapng \
  export 8 artifacts/bank-08-from-capture.wav --json
```

Result: **63,505,920 PCM bytes**, 162 blocks, 240.007256 seconds,
44.1 kHz / 24-bit stereo. PCM SHA-256:
`301e939a1700bf7fe0e3fd0fdccffdf0c47ec34a8c41248447635e7d9fa5985b`.

Automated regression coverage includes:

- Byte-exact requests and structural replay of banks 1–4 and 8.
- Indices 0, 114, 115, 127, 128, 160, 161; rejection of 162 and higher.
- Bank/sequence/count mismatches, malformed/truncated USB and PCAPNG data.
- Explicit zero-block replies, timeouts, bounded recovery, and continuing
  a batch after empty/failed/unsupported banks.
- Atomic WAV publication, overwrite protection, resume/force, CLI JSON
  summaries and legacy script flags.
- Offscreen GUI interaction: all 40 row buttons, bank-8 capture replay through
  a worker, batches continuing after empty/failed/unsupported slots, preserved
  files, retry recovery, native Space on focused buttons, and close protection.
- Repeated connection checks preserve folder focus, text, cursor and selection.
  Hotplug, probe errors, concurrent export initiation and deferred close are
  covered without physical USB access.

Final checks: **215 pytest tests passed**, Ruff passed, strict mypy passed,
and `dist/fs21-0.3.0-py3-none-any.whl` built successfully. The local editable
installation was updated to match the built package.

The compact structural fixture contains recorded request bytes and response
structure/hashes but **omits PCM**; replay uses zero-filled audio. The
original audio-bearing fixture continues to verify unchanged WAV bytes.
An autouse pytest guard forbids physical USB discovery.

## Windows preview v0.3.0

The [portable x64 release](https://github.com/jardous/fs21-edit/releases/tag/v0.3.0)
includes separate windowed GUI and console CLI executables, Python, Qt/PySide6,
and libusb. Linux locking is retained; Windows uses byte-range device locks and
non-overwriting atomic rename for WAV publication. Drivers are never installed
automatically, and the read-only USB command allowlist is unchanged.

- Local Linux regression: **224 passed, 3 Windows-only tests skipped**; Ruff and
  strict mypy passed, including mypy's Windows-platform check.
- [Native build](https://github.com/jardous/fs21-edit/actions/runs/45460393872):
  Windows Server 2022, Python 3.12.10 x64; **167 passed, 60 skipped**. Skips cover
  the unavailable private audio fixture and the POSIX kernel-driver query.
  Ruff and strict mypy passed.
- The extracted GUI and CLI started outside the checkout. The frozen GUI
  loaded its bundled libusb DLL, displayed 40 slots and 40 Export buttons,
  and retained the two-column layout.
- [Standalone verification](https://github.com/jardous/fs21-edit/actions/runs/45461613056)
  downloaded the **published ZIP**, checked its SHA-256, and launched both
  executables without installing Python dependencies. A screenshot from the
  normal Windows display backend was inspected: text renders correctly.
  The offscreen Windows backend's missing fonts were a test-environment issue,
  not observed with the normal backend.
- The downloaded ZIP and build manifest matched their published checksums;
  all **461 bundled files** matched the manifest. Both executables have AMD64
  PE headers and the intended GUI/console subsystems. No recordings or raw
  captures are included. License notices and corresponding Qt/libusb sources
  accompany the release; the unused Qt virtual keyboard is excluded.

**No physical Windows USB export was tested.** Windows 10/11 x64 is the target,
not a claim that those desktop OS versions or a live pedal were tested.
The manual WinUSB setup and all protocol limitations remain documented.

## Remaining limitations

- **No multi-bank live-hardware testing is claimed.** Earlier live bank-1
  tests remain documented separately; the new validation is offline and mocked.
- No captured block request exceeds 161. A count above 162 is unsupported,
  even when a mathematical checksum candidate can be computed.
- The block count and sequence fields are 16-bit, but field width does not
  establish a working checksum range or actual firmware capacity.
- No empty-bank response or interrupted-transfer recovery is captured.
  Only explicit zero-block/no-audio replies count as empty. Other empty
  encodings, if any, are errors rather than guessed successes.
- Read-to-quiet plus identification is a bounded host recovery strategy,
  not proof every firmware state recovers. Manual reconnect may be needed;
  the batch continues reporting individual outcomes without automatic reset.
- Other bank selectors, recording metadata/occupancy, complete response
  integrity semantics, and the bank-9 checksum hypothesis need more captures.
- The redesigned GUI and CLI both support the complete 40-bank workflow,
  subject to the same capture-evidence and recording-length limits above.
