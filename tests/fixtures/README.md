# Capture provenance (private recording)

Raw PCAP files and WAV recordings are ignored by Git and are not published.
If the private fixture below is absent, only tests that require it are
skipped. The public structural fixture remains available and testable.

`fs21-export.pcap.gz` contains unmodified USB records selected from
`/home/jiri/Downloads/looper.pcap`, retaining only bus 5, device 11.
The original input is untouched. Gzip has deterministic metadata.

- Original SHA-256: `979f26efd625d80c3de09d30f3cca0eb1664efc6cd0bed942ebe291a41408910`
- Original records: 65,588; selected records: 65,570.
- Fixture SHA-256: `543b628e4bb4a05e7101991c355309964f7d9f76fe53faecb6603836d5cd1b0a`
- Reconstructed PCM SHA-256: `aec8b1bcf99435a263bbae93cc962fb9b7a9aec2377f6353f763b9ed055b0987`
- PCM: 16,403,712 bytes, 2,733,952 stereo frames, 42 blocks,
  61.99437641723356 seconds at 44.1 kHz / 24 bits / stereo.

Reproduce with:

```bash
python tools/make_fixture.py /path/to/looper.pcap tests/fixtures/fs21-export.pcap.gz
```

The fixture contains actual audio. It is not MIT-licensed or authorized
for redistribution merely because the source code is. Obtain the recording
owner’s permission before publishing. Automated tests use no physical USB.

## Structural fixture

`protocol-evidence.json` was generated with `tools/collect_evidence.py` from
the three PCAPNG files found in `/tmp`:

| File | Source SHA-256 |
| --- | --- |
| `fs21-all-banks.pcapng` | `6d2f2fc35fe8b354d82a84b4bd6f1fde99204467567d0e2aedc032b554e5fe2d` |
| `fs21-bank4.pcapng` | `e3be2126023df4f939f6dbab8eeb5b988ca0aa4ddaa695c01897d38ef1abe27c` |
| `fs21-bank8.pcapng` | `60a12b9a65d015375dbab43df5723fed2be04c5a977535b12b1e9bd0bdfbefa6` |

It preserves the exact prefixes of all 581 CD requests (512-byte zero
padding is restored on replay) and CA requests for banks 1, 2, 3, 4 and 8.
Response bank/count/sequence prefixes, PCM lengths, trailers, and PCM
SHA-256 hashes are retained; the new recordings' audio bytes are **omitted**.
Tests reconstruct their structure with zero-filled PCM, not original audio.
No empty or error/recovery fixture is claimed to come from a capture:
those cases are explicitly synthetic in `tests/replay.py`.

Source capture names alone are not proof of coverage: the “all-banks” file
contains only three completed exports, for banks 1–3. No source contains
an acknowledgment above index 161 or an empty-bank export.
