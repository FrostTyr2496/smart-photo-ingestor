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

## Now (2026-11-18)
- **Card milestone built:** the CLI imports a card onto WORKING with verified copies and answers `card-status`
  (`drives`, `volumes assign`, `ingest [--dry-run]`, `resume`, `card-status`, `jobs`). Guides:
  `docs/user/CARD_WORKFLOW.md`, `docs/user/INDEX_WORKFLOW.md`. `uv run pytest -q` → 532 passed, 4 skipped (`live`).
- Index done: idx-1..4, idx-11 (D-044), idx-13 (D-063: `index` counts non-photo files per drive, schema V3; the drive
  page shows photography / not / unaccounted / free). Flats 1 is indexed and hashed; its duplicate report found 113.5 GB
  reclaimable (`docs/private/IDX5_FLATS1_RESULTS.md`). `lrcat mark` not run on a real catalog yet (idx-3 unverified).
- ing-12 done (legacy-seen frames get the D-005 name at import). eval-1 done; its recommendation awaits the user (`docs/reviews/2026-11-11-eval-1-sequencing.md`).
- A live dry run planned both real cards (D-031); no real import has run yet (ing-11 is the user's).
- Drives: cards exFAT; X9 Pro (1 TB exFAT) WORKING; Flats 1 (1 TB APFS, 98 % full) LEGACY, hashed; PP 1 (4 TB
  APFS HDD, LEGACY) indexed, its hash paused on I/O errors (resume with idx-11); the 12 TB is offline.
- **Metrics (met-1, D-054):** every CLI command except `serve` records bytes, MB/s and read errors per drive to
  `<home>/metrics.sqlite`; `photovault metrics [--days N]` reports, `metrics backfill` estimates past work.
- Policy: one verified copy on WORKING = safe to format (D-003). The Cards view's colours are stricter (D-089):
  green = on a MASTER or on two different WORKING/MASTER drives; yellow ("One copy") = one drive.
- **Web UI (D-045, specs `docs/specs/ui`, `docs/specs/cards`):** `uv run photovault serve` → 127.0.0.1:8765.
  Drives (with Ignore, ui-9), tasks, a metadata view, a drive summary (ui-7) with copies across drives (ui-8), a
  Cards view (card-3; `cards check <mount>` = index + hash, read-only), and one filter bar for drive and card file
  lists (ui-10: Show · Copies · Camera, "x / N files", drives always named). Tasks also shows runs started in a
  terminal, plus jobs left `running` with no process (ui-11, D-053). ui-7..11 and card-3 not seen in a browser yet.
  The Mac folder (`mac_folder`, default `~/Sandbox`) is a drive (D-047).
- **Web UI (D-059, D-062, spec `docs/specs/revamp`):** `uv run photovault serve` → `127.0.0.1:8765` is the React UI
  (rv-1..rv-6): tasks, drives with file lists, cards, new-drive setup, settings, dev palette picker. Build committed
  under `photovault/web/static/next/`; the classic UI is deleted (rv-8 cleaned up leftovers). **Photos (rv-7, D-064):** a
  timeline by day with an inspector (map, GPS suggestions, place search, credit, keywords, clock shift); schema V4
  adds the `assets(group_id)` index that the old grid lacked. Leaflet added to `web/`. rv-9: edits apply to related
  photos (the selection's session). Spike sp-1 (spec `phone-gps`): phone GPS + place names from the Photos library.
- Hooks: `guard_volumes.py` blocks agent writes and engine runs on `/Volumes`; live tests read mounts only under
  `-m live` (D-028, D-031). Old v0 code: local branch `archive/v0-cli-2026-11-11`. The repo is public: real-drive
  facts and photos stay in `docs/private/` and `testdata/` (gitignored).

## In progress
none

When you start a task, replace `none` with this block before your first edit, and set it back to `none` in the
task's commit:
```
- Task: <id> (started YYYY-MM-DD, <model>)
- Files touched: <paths>
- Last verified: `<command>` → <result>, or "not run"
- Notes: what is half-done, and what to check first
```

## Next
0. **[user] visual check of the new UI (rv-1..rv-7, rv-12..15)**: restart `serve` (it picks up drive rename and the
   faster drive list). In Photos, connect a drive, check that thumbnails load, then save a location and keywords on a few photos. Also the older
   **[user] visual check (ui-5..15, card-3):** restart `uv run photovault serve` (it picks up ui-11..15), try the
   Tasks home page, the sidebar groups, drives, cards, a step's Run button (e.g. Hash) and the editor (Photos).
1. **[user] re-index Big Boy Old** (`uv run photovault index "/Volumes/Big Boy Old"`), then open its drive page.
   Then:
2. **Import (spec `docs/specs/import`, D-088..D-091), replaces ing-11's run:** im-1 done; next im-3 (`--limit`), im-2
   (LEGACY → WORKING for Backup Plus), im-4 (Cards → dry-run plan → apply); then [user] im-5, im-6 with X9 Pro
   (small batches → web dry run → full card). bk-3 adds the second drive (green in one pass).
3. **[user] idx-6:** resume the PP 1 hash with `--skip-prefix` on the damaged folder, then run `report duplicates`
   ("If a drive shows I/O errors" in `docs/user/INDEX_WORKFLOW.md`). idx-5 is done except `lrcat mark`.
4. **Curation (spec `docs/specs/curation`, D-069):** wave 1 is approved and ready: ing-13, idx-9, idx-10, idx-14,
   seq-1b (then seq-2), rv-11; then lr-0, set-1; cu-0 / dd-0 / ro-0 are proposals first. **Phone + related
   (D-070..D-079):** rv-16, sp-2, sp-3, sp-7, sp-7b, sp-8, rv-10, rv-17..rv-24 done. **Shoots (spec
   `docs/specs/shoots`, D-080, V7 landed):** sh-0b, sh-1 (plan: 182 shoots, 35 trips), sh-2 (`shoots plan`, decisions in
   the catalog), sh-3, sh-3b, sh-4 (Apply/Undo, shoot filter in Photos) done; next sh-5. **Actions** (`docs/specs/actions`): act-1,
   act-2 done (23 actions on the real catalog); act-4 (previews cached from a connected drive) done; next act-3.
5. **Card backup (spec `docs/specs/backup`, D-050..D-052):** bk-1 drive health, bk-3 backup job (after ing-13),
   vn-1 volume name on hover, then bk-2, bk-4, vn-2, vn-3. bk-5 [user]: plug in and assign the 12 TB. Metrics:
   met-2 (web view); met-4 after bk-1.

## Waiting on user
- **Index proposal (D-080):** add `CREATE INDEX capture_groups_time ON capture_groups (capture_time)` so session
  lookups don't range-scan? Not part of the V7 approval.
- **[user] browser check (phone import done 2026-11-18):** restart `serve` and check in Photos: the "Applies to" block (rv-16), phone place names and "Near this time"
  (sp-3), Copy name (rv-18), Maps links (rv-17), the farther suggestions (sp-8), Show in All Photos (rv-19), the
  full-screen viewer (rv-20), the condensed headers and filter chips (rv-21), hiding the sidebar with `[` (rv-22),
  full size vs "plug in PP 1" plus the grid's unplugged icon (rv-23), the expandable map / "Refine on map" (rv-24).
  No clock banners should show now (sp-7b). Then run `uv run photovault --json meta scan --rescan "<mount>"` on each
  connected drive so `subsec` fills in (sh-0b), and try the review mode on test data:
  `PHOTOVAULT_DEV_PLAN=tests/fixtures/plans/shoots-fixture.json uv run photovault serve` → `#/review/shoots` (sh-3),
  or on the real catalog: `uv run photovault --json shoots plan`, then `#/review/shoots` (sh-2 saves each decision;
  sh-4 Apply changes the catalog only, and Undo is in the review's side panel).
- **Push the archive branch?** Run `git push -u origin archive/v0-cli-2026-11-11` if you want it on GitHub.
  (`main` is pushed.)
- **Corpus gaps (fnd-7):** the wish list still lacks a Z6II pair, a night run, a GoPro set and an iPhone
  HEIC+AAE (`docs/private/CORPUS.md`). Copy them when you can.
- **X9 sample:** should `testdata/x9_sample/` move into `testdata/corpus/x9/` (D-017)?
- The exFAT answer looked cut off ("honestly considering…"). Tell us if you are weighing APFS after all.
- **eval-1, last question:** the boundary check from memory (the other three are answered, D-069). See the review file.
- **Curation proposals (D-069):** cu-0 (now schema V8), dd-0 (duplicate quarantine), ro-0 (shoot folder layout) each
  come back as a review for your approval before any code.
- **lrcat mark:** run it on a copy of one catalog (guide step 3) or say you are skipping it for now. Keep Lightroom
  closed while you do.

## Recent decisions
- D-094 (agent): act-4 previews cached by a user-started task while a drive is connected; served unplugged.
- D-093 (agent): sh-4 Apply/Undo as tasks with `apply-<time>` ids; refuses a stale review; offsets only when proposed.
- D-092 (agent): sh-2 decisions saved in the catalog as made; plans record `decisions_through`; planner ids for pieces.
- D-091 (user): import flow: every card listed (unplugged greyed), dry-run plan in the browser, then apply; X9 Pro first.
- D-090 (user): `ingest --limit N` imports in small batches (first N frames by capture time).

## Execution rules
Follow `docs/steering/task-execution.md`. Run `/close-the-loop` before each commit.

Hard stops:
- Never write to `/Volumes`; never put serials, UUIDs, GPS or locations in git.
- Never add a dependency or change the schema, naming spec or a signature without the user's approval.
