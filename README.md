# smart-photo-ingestor → PhotoVault

PhotoVault is a personal photo vault for macOS. Plug in a camera card and it copies everything to a working
SSD, checking every byte. It records each file in a local SQLite catalog, which knows which drives hold
verified copies. It tells you when a card is safe to format, and it later reconciles everything onto an
offline 12 TB master drive.

**Status:** being rebuilt (November 2026). `main` holds the new design and a clean scaffold. The previous CLI
(`photo-ingest analyze/ingest`) is preserved on branch `archive/v0-cli-2026-11-11`.

## Design in one paragraph

A headless Python engine (`photovault`, driven over JSON-lines) does all the file and catalog work. A thin
SwiftUI menu-bar app (Phase 5) watches for mounted volumes and shows progress. The engine:
- never writes to source media;
- copies through a single verified writer: temp file → sync → uncached re-read → SHA-256 compare → rename;
- identifies files only by their SHA-256;
- groups RAW, JPEG, sidecar and derivative files into captures;
- names files deterministically so camera number rollover can't collide.

Details:
- [`docs/ARCHITECTURE_AUDIT_AND_ROADMAP.md`](docs/ARCHITECTURE_AUDIT_AND_ROADMAP.md): the reasoning.
- [`docs/steering/system-design.md`](docs/steering/system-design.md): the contract.
- [`docs/phase-roadmap.md`](docs/phase-roadmap.md): the plan.

## Development

```bash
uv sync
uv run pytest -q
```

Needs Python ≥ 3.12, [uv](https://docs.astral.sh/uv/), and `exiftool` (`brew install exiftool`).
Contributors (human or agent): start with [`START_HERE.md`](START_HERE.md) and
[`docs/steering/task-execution.md`](docs/steering/task-execution.md).

## License

MIT
