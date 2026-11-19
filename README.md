# smart-photo-ingestor → PhotoVault

PhotoVault is a personal photo vault for macOS. Plug in a camera card and it copies everything to a working
SSD, checking every byte. It records each file in a local SQLite catalog, which knows which drives hold
verified copies. It tells you when a card is safe to format, and it later reconciles everything onto an
offline 12 TB master drive.

**Status (November 2026):** in daily use on real cards and drives. Import with verified copies, a second copy
to another drive, per-file card status, safe card clear, drive indexing and duplicate reports, and a local web
UI all work. The SwiftUI menu-bar app is still to come. The previous CLI is preserved on branch
`archive/v0-cli-2026-11-11`.

**How it is built:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (diagrams of the processes, the verified
copy, a card's life and the data model).

## Design in one paragraph

A headless Python engine (`photovault`, driven over JSON-lines) does all the file and catalog work. A thin
SwiftUI menu-bar app (Phase 5) watches for mounted volumes and shows progress. The engine:
- never writes to source media;
- copies through a single verified writer: temp file → sync → uncached re-read → SHA-256 compare → rename;
- identifies files only by their SHA-256;
- groups RAW, JPEG, sidecar and derivative files into captures;
- names files deterministically so camera number rollover can't collide.

Details:
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): the overview, with diagrams.
- [`docs/ARCHITECTURE_AUDIT_AND_ROADMAP.md`](docs/ARCHITECTURE_AUDIT_AND_ROADMAP.md): the reasoning.
- [`docs/steering/system-design.md`](docs/steering/system-design.md): the contract.
- [`docs/phase-roadmap.md`](docs/phase-roadmap.md): the plan.

## Web UI

```bash
uv run photovault serve   # then open http://127.0.0.1:8765
```

Drives, tasks, cards and settings, bound to 127.0.0.1 only. The page is a React build of [`web/`](web/) that is
committed under `photovault/web/static/next/`, so serving it needs no Node. To change it: `cd web && npm install`,
edit, then `npm test && npm run build`.

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
