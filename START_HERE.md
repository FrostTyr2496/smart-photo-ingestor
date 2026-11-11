# Start Here

PhotoVault (repo `smart-photo-ingestor`) imports camera cards onto a working SSD with verified copies. It
tracks every file across drives in a SQLite catalog on the Mac, and later reconciles to an offline 12 TB
master. Today `main` is a clean scaffold. The old CLI is on branch `archive/v0-cli-2026-11-11`.

**Rules for this file.** It is the handoff between sessions, so keep it short and true.
- Keep it under about 80 lines.
- "Now" holds at most 10 facts that are true right now.
- "In progress" is one block, or `none`.
- "Next" holds at most 6 items, "Waiting on user" at most 8.
- "Recent decisions" holds the newest 5 rows of `docs/decisions.md`.
- History goes to `docs/decisions.md` or git.

## Read at the start of every session
1. The pre-flight printed by the SessionStart hook, and this file.
2. `docs/phase-roadmap.md` (the active plan) and your task in `docs/specs/<spec>/tasks.md`.
3. Only the sections of `docs/steering/system-design.md` your task cites. The hard rules (`vault-rules.md`)
   are already loaded through `CLAUDE.md`.

Reference docs:
- `docs/ARCHITECTURE_AUDIT_AND_ROADMAP.md`: why the design is what it is.
- `docs/private/STORAGE_LAYOUT.md`: the real drives. Local only, never committed.
- `docs/legacy/handoff-v0.md`: v0 findings, including the ExifTool benchmarks.

## Now (2026-11-11)
- `main` was reset (D-007). The scaffold, hooks, docs, a redacted EXIF fixture, the config loader (fnd-1) and
  the ExifTool metadata reader (fnd-5, `photovault/metadata.py`) exist; `uv run pytest -q` → 89 passed, 1 skipped.
- The old v0 code is on local branch `archive/v0-cli-2026-11-11`. Port from it with
  `git show archive/v0-cli-2026-11-11:photo_ingest/<file>` (fnd-5 ports `analyzer.py`).
- Hooks are live:
  - `.claude/hooks/guard_volumes.py` blocks agent writes and engine runs on `/Volumes`; real-drive runs are
    [user] steps.
  - `.claude/hooks/preflight.sh` prints the pre-flight at session start.
- The repo is public. Real-drive facts and photos stay in `docs/private/` and `testdata/` (gitignored).
- Drives:
  - Cards are exFAT.
  - X9 Pro (1 TB exFAT) is the WORKING SSD.
  - Flats 1 (1 TB APFS, 98 % full, holds the Lightroom catalogs) is LEGACY.
  - The 4 TB is not available yet; the 12 TB is offline.
- Policy: one verified copy on WORKING = safe to format (D-003).

## In progress
- Task: none.

When you start a task, replace `none` with this block before your first edit, and set it back to `none` in the
task's commit:
```
- Task: <id> (started YYYY-MM-DD, <model>)
- Files touched: <paths>
- Last verified: `<command>` → <result>, or "not run"
- Notes: what is half-done, and what to check first
```

## Next
1. **Phase 1, lane B left:** fnd-2 → fnd-3 → fnd-4. fnd-6 waits for fnd-4. fnd-7's agent part follows the user's copy.
2. **Phase 2 early lanes**, which can run alongside Phase 1: ing-1 → ing-2 (hashing, copy engine); ing-3
   scanner.
3. **Fixture-only work** that can start any time: seq-1 (runs and bursts).
4. After that, fnd-6 then ing-4 → ing-5 → ing-6. ing-7 and ing-10 are **[stronger]**.

## Waiting on user
- **Push the archive branch?** Run `git push -u origin archive/v0-cli-2026-11-11` if you want it on GitHub;
  push `main` when you're happy with the reset.
- **fnd-7:** copy the sample shoot from the X9 Pro into `testdata/corpus/x9/Raw/` (the commands are in the chat;
  the agent then writes the corpus inventory to `docs/private/`).
- The exFAT answer looked cut off ("honestly considering…"). Tell us if you are weighing APFS after all.
- Phase 3: close Lightroom before indexing Flats 1, and plug in the 4 TB (D-006).

## Recent decisions
- D-016 (agent): metadata reader details (capture time as `DateTimeOriginal` + `CaptureTimeSource`).
- D-015 (user): PyExifTool approved for fnd-5.
- D-014 (agent): config details the spec left open (unknown keys rejected; policy table replaces default).
- D-013 (agent): LEGACY volumes get no marker; they are identified by UUID only.
- D-012 (agent): no perceptual hashes or LLM in ingest; LLM enrichment is backlog.

## Execution rules
Follow `docs/steering/task-execution.md`. Run `/close-the-loop` before each commit.

Hard stops:
- Never write to `/Volumes`; never put serials, UUIDs, GPS or locations in git.
- Never add a dependency or change the schema, naming spec or a signature without the user's approval.
