# Spec: import — card import from the web, two-drive green, small batches, a travel working drive (D-088..D-091)

Goal: pick a card in the Cards view (all known cards are listed; the ones not plugged in are greyed out and their
buttons disabled with the reason), see a dry run of the import as a plan in the browser, then apply it. With two
writable drives plugged in (two WORKING, or WORKING + MASTER) the import copies the card onto the first and a
trailing job copies each verified file onto the second, so the card's files turn **green** and the card can be
formatted in camera. With one drive the files turn **yellow**: one verified copy, safe to format only if you must
(D-003). Testing goes in small batches first (`--limit`), then a small dry run and apply in the web UI, then a
full card.

Rules for every task:
- vault-rules apply. Cards stay read-only except for the card clear (cc-1, D-096): it deletes only files that are
  green when checked just before deletion, after a dry run and a typed confirmation.
- No new dependency. The only schema change is cc-1's job kind (D-096). The second copy is the backup job of spec `backup` (bk-3), which already
  allows a second WORKING drive as its target.
- Tests use `tmp_path` and fakes for `volumes.probe` and `metadata.read_metadata`; never `/Volumes`.
- Real runs (the role change of Backup Plus, every real import, every real card clear) are **[user]** steps.

## Shared definitions

**Tiers (D-089, replaces the green/yellow part of D-046).** "Writable copy" = a `verified` instance on a WORKING or
MASTER volume (the Mac folder counts as one volume). A card file is:
- `backed_up` (green): verified on any MASTER, or on two or more different WORKING/MASTER volumes;
- `needs_backup` (yellow): any other verified non-CARD copy (one WORKING volume, or LEGACY only);
- `only_on_card` and `unchecked`: unchanged.
LEGACY copies never count toward green: they are drives the engine does not manage.

**Batch (`--limit N`, D-090).** The plan keeps the first N capture groups in capture-time order (a RAW+JPEG pair or
a photo and its sidecars stay together; groups with no time sort last, by card path). The job's items are only
those groups. Running the same import again without `--limit` plans the whole card; files already verified on the
target are skipped (`skipped_dup`, D-036), so a batch is never copied twice.

## Tasks

- [x] 1. Two writable copies are green
  - ID: im-1 (D-089)
  - `photovault/cards.py` `_classify`: green when a MASTER copy exists or copies sit on ≥ 2 distinct WORKING/MASTER
    volumes; the reason names the drives ("On X9 Pro and Backup Plus"). Yellow's reason says "one copy". Module
    docstring and the Cards view legend text follow.
  - Verify: `uv run pytest tests/test_cards.py -q`
  - Result (2026-11-18): `uv run pytest tests/test_cards.py -q` → 13 passed; web 109 passed. Yellow is labelled
    "One copy" in the UI (`web/src/lib/tiers.ts`, `labels.ts`); `tests/test_web_api.py` reason string updated.

- [x] 2. **[stronger]** A LEGACY drive becomes WORKING (or MASTER)
  - ID: im-2 (D-088)
  - `volumes.change_role(conn, probe, role) -> str` (does not commit): only LEGACY → WORKING or MASTER. The volume
    keeps its `volume_id`, so its indexed files keep their rows; the marker written to the drive holds that id and
    `volumes.marker_id` is set to it. Refused for CARD, for the Mac folder, while a job on the volume is `running`,
    `planned` or `paused`, and when a marker with another id already sits on the drive.
  - CLI: `volumes assign <mount> --role WORKING --change-role` (without `--change-role` the old error stays, now
    naming the flag). Web `assign` accepts `change_role`. Nothing on the drive moves or is renamed (rule 7); new
    imports land under `working_subdir`.
  - Verify: `uv run pytest tests/test_volumes.py tests/test_cli.py -q`
  - Result (2026-11-18): `uv run pytest -q` → 697 passed, 9 skipped. Also fixed: plain `volumes assign` on an
    already-registered drive of another role now errors before writing a marker (it used to write one first).

- [x] 3. Import in small batches
  - ID: im-3 (D-090)
  - `plan_card_ingest(..., limit: int | None = None)` per the batch definition; the result event adds `limit`,
    `groups_total` and `files_total` (whole card). CLI `ingest --limit N` (N ≥ 1); web `ingest` accepts `limit`.
  - Verify: `uv run pytest tests/test_cli.py tests/test_web_api.py -q` (ingest tests live in `test_cli.py`)
  - Result (2026-11-18): `uv run pytest tests/test_cli.py tests/test_web_api.py tests/test_ingest_faults.py -q` → 97
    passed. Groups outside the batch still reserve their names (no catalog writes), so a batch names files exactly
    as a full import would; a `batch` warning states "N of M frames".

- [x] 4. **[stronger]** Import from the Cards view: dry-run plan, then apply
  - ID: im-4 (D-091)
  - Cards view: every known card is listed; one not plugged in is greyed with a "Not connected" badge, and its
    Import and Check buttons are disabled with a tooltip ("Plug in <card> to import it").
  - Import dialog: target (WORKING drives; unplugged ones listed disabled), optional second drive (another WORKING
    or a MASTER, needs bk-3; until then shown disabled "coming with bk-3"), batch size (10 / 100 / all / custom),
    shoot name. "Dry run" runs the plan and shows it: files, groups, size, target free space, folders, sample
    names, warnings, and the tier the files will reach (yellow with one drive, green with two). "Apply" starts the
    same import without `--dry-run` and is enabled only after a dry run with the same inputs.
  - Verify: `uv run pytest tests/test_web_api.py -q`; `cd web && npm run typecheck && npm test`; the user does the
    visual check.
  - Result (2026-11-18): `uv run pytest -q` → 697 passed, 9 skipped; web typecheck clean, `npm test` 120 passed, build
    committed. The ingest result now also carries `target_free_bytes`. The dialog polls the dry-run task itself for its
    events. Visual check is the user's.

- [ ] 5. **[user]** Make Backup Plus a working drive
  - ID: im-5 (after im-2)
  - `uv run photovault --json volumes assign "/Volumes/Backup Plus" --role WORKING --change-role`, then paste back the
    result line.

- [ ] 6. **[user]** Test imports, small to full
  - ID: im-6 (after im-1, im-3, im-4; X9 Pro plugged in; ing-11 is this task's first part)
  - CLI, batch of 10: `ingest --source "/Volumes/NIKON Z 6_2" --target "/Volumes/X9 Pro" --limit 10 --dry-run`, then
    without `--dry-run`; `shasum -a 256` three pairs; check the Cards view shows 10 files yellow.
  - Web, batch of 100: dry run in the Import dialog, then Apply. With bk-3 in: the same with Backup Plus ticked as
    the second drive → those files green.
  - Full card: dry run, then apply. Paste back timings, counts, `sync_method` and the card's tiers.

- [x] 7. **[stronger]** Safe card clear
  - ID: cc-1 (D-096; vault-rules 1 and 4 exceptions)
  - Migration: job kind `card_clear` (items: `src_rel` = the card file, `state` `committed` once deleted,
    `failed` with the reason). `photovault/card_clear.py`: `plan_clear(conn, cfg, mount) -> dict` (read-only:
    groups whose every card file is green, the kept files by reason) and `run_clear(conn, cfg, mount, *,
    confirm: int, emit, should_stop)`. Per group, just before deleting: re-hash each card file (must equal the
    asset's SHA-256), re-check the qualifying verified copies in the catalog, re-hash one copy on a connected
    drive. Any surprise stops the run. Deleted card instances → `missing`. Only files under `DCIM/`, only on
    CARD volumes; never formats.
  - CLI: `cards clear <mount> [--dry-run] --confirm N` (N must equal the planned file count). Web: `card_clear`
    with a typed-count confirm; the card shows "Cleared by PhotoVault on <date>".
  - Verify: `uv run pytest tests/test_card_clear.py -q`
  - Result (2026-11-18): `uv run pytest tests/test_card_clear.py -q` → 13 passed; `uv run pytest -q` full suite green;
    web typecheck clean, `npm test` 123 passed, build committed. Schema V8 (cu-0 moves to V9). See D-097.

- [ ] 8. Dry-run folder tree and Show in Finder
  - ID: im-7 (D-102)
  - The ingest dry-run result (or a task-detail fetch) gives every planned `src_rel -> dst_rel`; the Import dialog
    shows them as a collapsible folder tree on the target (rv-26 component), with counts and sizes per folder.
    After Apply, each target folder has Show in Finder (existing `/reveal` endpoint with `rel_path`).
  - Verify: `uv run pytest tests/test_web_api.py -q`; `cd web && npm run typecheck && npm test && npm run build`.

- [x] 9. **[stronger]** Copy-target recommendations
  - ID: rec-1 (D-103; after bk-3)
  - `photovault/targets.py`: `recommend(conn, home, mounted, *, planned_bytes, source_volume_id) -> list[dict]`
    ranking connected WORKING/MASTER drives by the D-103 factors, each with plain reasons ("SSD, 410 MB/s
    measured", "NTFS: not written to"), plus a proposed pair for green. The Import dialog pre-selects the pair;
    the Actions engine's card and single-copy actions name it.
  - Verify: `uv run pytest tests/test_targets.py tests/test_actions.py -q`
  - Result (2026-11-18): `uv run pytest tests/test_targets.py tests/test_actions.py -q` -> 39 passed; web 140 passed, build done.
    `recommend` returns `{planned_bytes, candidates, pair}` (not a bare list) and takes an extra `exclude`; `GET
    /api/cards/<id>/targets[?planned_bytes=N]` serves it; the Import dialog pre-selects the pair and lists the reasons;
    `card_unbacked` and `single_copy` name the pair / best drive. D-105.

## Order

im-1, im-3 (no drives needed) → im-2 → im-4 → im-5 [user] → im-6 [user] small batches. The two-drive green flow
also needs ing-13 → bk-1 → bk-3 (backup job, second WORKING allowed) → bk-2 (1 GB health gate) → bk-4 (merged into
im-4's dialog: the second-drive tick becomes live).

Build order (2026-11-18): bk-3 (done) → rec-1 (done) → im-7. bk-1/bk-2 (health gate,
D-051) are still open: until they land, big copies run without the health check.
