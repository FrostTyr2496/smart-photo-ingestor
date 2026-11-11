# Spec: app (Phase 5) — SwiftUI menu-bar shell

Goal: a menu-bar app that notices cards and drives, runs engine jobs, shows progress and card status, and
prompts to reconcile when the 12 TB appears. It is a thin shell: all file and catalog logic stays in the engine
(Path A, D-001).

Rules:
- `docs/steering/task-execution.md` applies.
- app-0 writes the remaining tasks; the ones below are placeholders.

- [ ] 0. **[stronger]** App design study (user approval required)
  - ID: app-0
  - Blocks on: ing-11
  - Write `docs/reviews/<date>-app-design.md` covering:
    - the Xcode project layout under `app/`;
    - the minimum macOS version;
    - DiskArbitration appear/disappear callbacks;
    - volume classification (DCIM → card; marker → known; else ask once);
    - how the engine is located and launched (a uv-managed environment path vs a bundled Python; personal
      use, no notarisation);
    - JSON-lines parsing and cancellation (SIGINT → paused job);
    - read-only catalog access (GRDB, WAL);
    - notifications;
    - the card badge (green/red with copy counts);
    - "Eject" enabled only when no job holds the volume;
    - TCC prompts for removable volumes.
  - Then replace the placeholders below with 5–10 executable tasks.
  - Also record the Path B re-evaluation gate (audit §3): if Python packaging is the main pain once the schema
    is stable, port the engine against the same schema and fixtures.
  - Verify: the user approves; a decision row is recorded.

- [ ] 1. Placeholder: Xcode project and menu bar skeleton (ID app-1)
- [ ] 2. Placeholder: DiskArbitration volume watcher (ID app-2)
- [ ] 3. Placeholder: engine runner and JSON-lines progress (ID app-3)
- [ ] 4. Placeholder: catalog read model and card badge (ID app-4)
- [ ] 5. Placeholder: notifications and the reconcile prompt (ID app-5)
