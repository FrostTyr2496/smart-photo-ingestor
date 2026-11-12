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

## Now (2026-11-11)
- **Card milestone built:** the CLI imports a card onto WORKING with verified copies and answers
  `card-status`. Commands: `drives`, `volumes assign`, `ingest [--dry-run]`, `resume`, `card-status`, `jobs`.
  User guides: `docs/user/CARD_WORKFLOW.md` (cards) and `docs/user/INDEX_WORKFLOW.md` (old drives). `uv run pytest -q` → 286 passed, 2 skipped (`live`).
- Index modules done: idx-1 index, idx-2 baseline hash, idx-3 Lightroom reader (`lrcat mark`, schema unverified until idx-5),
  idx-4 duplicate and coverage report (`report duplicates`, writes Markdown to `<home>/reports/`).
- Fault suite done (ing-10): resume retries failed items, re-queues files gone from the target, sweeps partials (D-034).
- Built modules: config, catalog (db, repo), volumes, metadata, hashing, scan, groups, naming, copy engine,
  ingest (plan/run), status, index, report, protocol, cli, sequences, preview, live harness and survey.
- A live dry run planned both real cards (D-031); no real import has run yet (ing-11 is the user's).
- The old v0 code is on local branch `archive/v0-cli-2026-11-11` (port with `git show`).
- Hooks: `guard_volumes.py` blocks agent writes and engine runs on `/Volumes`; `preflight.sh` prints the
  pre-flight. Live tests read mounts only under `-m live` (vault-rules 2, D-028, D-031).
- The repo is public. Real-drive facts and photos stay in `docs/private/` and `testdata/` (gitignored).
- Drives: cards exFAT; X9 Pro (1 TB exFAT) is WORKING; Flats 1 (1 TB APFS, 98 % full) is LEGACY (indexing and hashing now, user-run); the 4 TB
  (PP 1, 4.0 TB APFS, LEGACY) is plugged in and not yet registered; the 12 TB is offline.
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
1. **ing-12, then ing-13 [stronger]** (D-042): a card frame already indexed from Flats 1 would be named by its camera
   stem; fix first, and ing-13 stops a double card read. Then:
2. **[user] ing-11:** the first real import (commands in `docs/user/CARD_WORKFLOW.md`), after ing-12, then paste back the
   timings, `sync_method` and counts.
3. **[user] idx-5** (idx-1 to idx-4 done): the Flats 1 index, hash, `lrcat mark` and `report duplicates` run
   (pulled forward per `docs/UTILITY_ASSESSMENT.md`; guide: `docs/user/INDEX_WORKFLOW.md`). Running now. Paste back the
   report summary; the agent compares it with the ~113 GB survey estimate. idx-6 (PP 1) follows, and may run in parallel.
4. **eval-1 [stronger]:** sequencing approaches; seq-2 (classification) as fixture-only work.
5. Backlog items from this milestone: rental bodies and variants, shoot names from geo or a local LLM, video
   body inference (`docs/specs/backlog.md`).
6. **idx-8 [stronger]:** `lrcat mark --force-exit`, which asks Lightroom to quit first (not on the critical path).

## Waiting on user
- **Push the archive branch?** Run `git push -u origin archive/v0-cli-2026-11-11` if you want it on GitHub.
  (`main` is pushed up to live-1's harness commit; later commits are local.)
- **Corpus gaps (fnd-7):** the wish list still lacks a Z6II pair, a night run, a GoPro set and an iPhone
  HEIC+AAE (`docs/private/CORPUS.md`). Copy them when you can.
- **X9 sample:** should `testdata/x9_sample/` move into `testdata/corpus/x9/` (D-017)?
- The exFAT answer looked cut off ("honestly considering…"). Tell us if you are weighing APFS after all.
- **idx-10 (D-042):** approve `lrcat mark --was-mounted-as` (new CLI flag)? Until then, run `lrcat mark` for PP 1
  before renaming the drive: a rename breaks Lightroom's stored paths.
- Phase 3: keep Lightroom closed while Flats 1 is hashed. Then register, index and hash PP 1 (idx-6; commands in
  `docs/user/INDEX_WORKFLOW.md`), and paste back the `report duplicates` summary.

## Recent decisions
- D-042 (agent): outside review of 4 suspected bugs: stem reuse (ing-12) and double card read (ing-13) confirmed,
  lrcat renamed-mount gap (idx-10, needs your OK), dry-run partial hashes declined (backlog).
- D-041 (user): the 4 TB is PP 1 (LEGACY); Flats 1 runs as is, no cleanup first; the two may run in parallel.
- D-040 (agent): duplicate report (idx-4): verified non-CARD copies only; unhashed files listed; output never under `/Volumes`.
- D-039 (user): `--force-exit` (idx-8): gracefully quit Lightroom on command; no kill, no lock deletion.
- D-038 (agent): lrcat reader (idx-3): refuses on a lock file or running Lightroom; marking is additive.

## Execution rules
Follow `docs/steering/task-execution.md`. Run `/close-the-loop` before each commit.

Hard stops:
- Never write to `/Volumes`; never put serials, UUIDs, GPS or locations in git.
- Never add a dependency or change the schema, naming spec or a signature without the user's approval.
