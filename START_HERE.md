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
- `docs/UTILITY_ASSESSMENT.md`: does the app have real utility yet, the live-run findings, candidate tasks.

## Now (2026-11-17)
- **Card milestone built:** the CLI imports a card onto WORKING with verified copies and answers `card-status`
  (`drives`, `volumes assign`, `ingest [--dry-run]`, `resume`, `card-status`, `jobs`). Guides:
  `docs/user/CARD_WORKFLOW.md`, `docs/user/INDEX_WORKFLOW.md`. `uv run pytest -q` → 442 passed, 4 skipped (`live`).
- Index done: idx-1..4, idx-11 (D-044). Flats 1 is indexed and hashed; its duplicate report found 113.5 GB
  reclaimable (`docs/private/IDX5_FLATS1_RESULTS.md`). `lrcat mark` not run on a real catalog yet (idx-3 unverified).
- ing-12 done: a frame first seen on a legacy drive or a card check gets the D-005 name at import.
- eval-1 done; its recommendation awaits the user (`docs/reviews/2026-11-11-eval-1-sequencing.md`).
- A live dry run planned both real cards (D-031); no real import has run yet (ing-11 is the user's).
- Drives: cards exFAT; X9 Pro (1 TB exFAT) WORKING; Flats 1 (1 TB APFS, 98 % full) LEGACY, hashed; PP 1 (4 TB
  APFS HDD, LEGACY) indexed, its hash paused on I/O errors (resume with idx-11); the 12 TB is offline.
- Policy: one verified copy on WORKING = safe to format (D-003). The Cards view's colours are a separate, stricter
  answer (D-046): green only when a MASTER holds the file.
- **Web UI (D-045, specs `docs/specs/ui`, `docs/specs/cards`):** `uv run photovault serve` → 127.0.0.1:8765.
  Drives (with Ignore, ui-9), tasks, a metadata view, a drive summary (ui-7) with copies across drives (ui-8), a
  Cards view (card-3; `cards check <mount>` = index + hash, read-only), and one filter bar for drive and card file
  lists (ui-10: Show · Copies · Camera, "x / N files", drives always named). Tasks also shows runs started in a
  terminal, plus jobs left `running` with no process (ui-11, D-053). ui-7..11 and card-3 not seen in a browser yet.
  The Mac folder (`mac_folder`, default `~/Sandbox`) is a drive (D-047).
- Hooks: `guard_volumes.py` blocks agent writes and engine runs on `/Volumes`; live tests read mounts only under
  `-m live` (D-028, D-031). Old v0 code: local branch `archive/v0-cli-2026-11-11`.
- The repo is public. Real-drive facts and photos stay in `docs/private/` and `testdata/` (gitignored).

## In progress
- Task: untracked work, not ui-11 (ui-11 is committed); found uncommitted on 2026-11-18, no task ID
- Files touched: photovault/scan.py, tests/test_scan.py (NTFS/Synology walk), photovault/web/static/app.js,
  app.css, index.html (new-drive setup view, read-only NTFS roles)
- Last verified: `uv run pytest -q` → 467 passed, 4 skipped (with this work in the tree)
- Notes: from another session; it needs a task ID and its own commit. ui-11's hunks in those files are committed.
- Task: met-1 (started 2026-11-18, claude-opus-5-5), in parallel with the work above (no shared files)
- Files touched: docs/specs/metrics/tasks.md, photovault/metrics.py, photovault/hashing.py,
  photovault/copy_engine.py, photovault/cli.py, tests/test_metrics.py
- Last verified: not run
- Notes: metrics go to `<home>/metrics.sqlite`, not the catalog (D-054).

When you start a task, replace `none` with this block before your first edit, and set it back to `none` in the
task's commit:
```
- Task: <id> (started YYYY-MM-DD, <model>)
- Files touched: <paths>
- Last verified: `<command>` → <result>, or "not run"
- Notes: what is half-done, and what to check first
```

## Next
0. **[user] visual check (ui-5..13, card-3):** restart `uv run photovault serve` (it picks up ui-11/12), try drives,
   cards, a step's Run button (e.g. Hash) and the editor.
1. **ing-13 [stronger]** (D-042): stops a double card read at import (ing-12 is done). Then:
2. **[user] ing-11:** the first real import (commands in `docs/user/CARD_WORKFLOW.md`), after ing-12, then paste back the
   timings, `sync_method` and counts.
3. **[user] idx-6:** resume the PP 1 hash with `--skip-prefix` on the damaged folder, then run `report duplicates`
   ("If a drive shows I/O errors" in `docs/user/INDEX_WORKFLOW.md`). idx-5 is done except `lrcat mark`.
4. **seq-2 [stronger]** waits for the user's answers on eval-1 (`docs/reviews/2026-11-11-eval-1-sequencing.md`).
5. **Card backup (spec `docs/specs/backup`, D-050..D-052):** bk-1 drive health, bk-3 backup job (after ing-13),
   vn-1 volume name on hover, then bk-2, bk-4, vn-2, vn-3. bk-5 [user]: plug in and assign the 12 TB.

## Waiting on user
- **Push the archive branch?** Run `git push -u origin archive/v0-cli-2026-11-11` if you want it on GitHub.
  (`main` is pushed.)
- **Corpus gaps (fnd-7):** the wish list still lacks a Z6II pair, a night run, a GoPro set and an iPhone
  HEIC+AAE (`docs/private/CORPUS.md`). Copy them when you can.
- **X9 sample:** should `testdata/x9_sample/` move into `testdata/corpus/x9/` (D-017)?
- The exFAT answer looked cut off ("honestly considering…"). Tell us if you are weighing APFS after all.
- **idx-10 (D-042):** approve `lrcat mark --was-mounted-as` (new CLI flag)? Until then, run `lrcat mark` for PP 1
  before renaming the drive: a rename breaks Lightroom's stored paths.
- **eval-1 (4 questions):** read the camera's bracket flag (a metadata field change), make search segmentation the
  production segmenter, which bracket steps count, and a boundary check from memory. See the review file.
- **Stale job 3:** `jobs` shows an index job on PP 1 as `running` with no process (job 4 is the live one). Propose
  idx-9 (mark ownerless `running` jobs `paused` at the next start)? Details: `docs/private/IDX5_FLATS1_RESULTS.md`.
- **lrcat mark:** run it on a copy of one catalog (guide step 3) or say you are skipping it for now. Keep Lightroom
  closed while you do.

## Recent decisions
- D-056 (agent, ui-13): a finished hash with indexed files still waiting shows as incomplete, never done.
- D-055 (user ask, ui-12): each timeline step has Run; UI tasks keep the Mac awake (`caffeinate -ims -w <pid>`).
- D-053 (agent): the UI shows runs started outside it (`ps` + `<home>/activity/<pid>.json`; catalog counts for older runs).
- D-052 (user): show a drive's volume name on hover; rename to match on any role, with a Lightroom warning
  (rule 1 exception); relink method is a spike (vn-3).
- D-051 (user): copies over 1 GB need a fresh drive health check; `stop` refuses, `warn` needs an accept.

## Execution rules
Follow `docs/steering/task-execution.md`. Run `/close-the-loop` before each commit.

Hard stops:
- Never write to `/Volumes`; never put serials, UUIDs, GPS or locations in git.
- Never add a dependency or change the schema, naming spec or a signature without the user's approval.
