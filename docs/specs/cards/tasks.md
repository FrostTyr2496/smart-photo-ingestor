# Spec: cards — removability of card files, cameras, the Mac as a working drive (D-046, D-047, D-048)

Goal: plug in a card that holds recent photos and see, file by file, whether each one is safe to remove:
green when a backup (MASTER) drive holds it, yellow when only a working drive (or the Mac folder, or a legacy
drive) holds it, red when the card is its only copy. A card, and each camera whose files are on cards, takes
the worst colour of its files. The Mac counts as a working drive, but only inside one configurable folder.

Rules for every task:
- vault-rules apply. Cards stay read-only: checking a card only reads it (index + hash), like a legacy drive.
- No schema change, no new dependency. `card-status` (D-003) is unchanged; the ranking is a separate answer.
- Tests use `tmp_path` and fakes for `volumes.probe` and `metadata.read_metadata`; never `/Volumes` or `~`.

## Shared definitions (D-046)

Tier of one file on a card (identity is the full hash, vault-rules 5; a CARD copy never counts):
- `backed_up` (green): a `verified` instance of its asset on a MASTER volume.
  **Superseded by D-089 (spec `import`, im-1):** also green when verified on two or more different WORKING/MASTER
  volumes; LEGACY copies never count toward green.
- `needs_backup` (yellow): a `verified` instance on WORKING (the Mac folder included) or LEGACY, none on MASTER.
- `only_on_card` (red): no `verified` non-CARD instance; or no hashed asset elsewhere has its size and partial
  hash (then no full match is possible, so no full read is needed); or the card could not read it.
- `unchecked` (grey): not checked yet: not in the catalog, changed since the check, or its size and partial hash
  match a hashed file elsewhere but its own full hash is not known yet.

Worst first: `only_on_card` > `unchecked` > `needs_backup` > `backed_up`. A card's or a camera's colour is
the worst tier among its files; no files gives `empty`.

## Tasks

- [x] 1. The Mac folder as a working drive
  - ID: card-1 (D-047)
  - `volumes.probe` on a folder that is not a mount point: no `diskutil` UUID (`da_volume_uuid=None`), filesystem
    and capacity from its containing mount, `looks_like_card=False`. It can be WORKING or MASTER (marker), never
    LEGACY (no UUID) or CARD.
  - Setting `mac_folder` (catalog `settings`, default `~/Sandbox`, editable in the web Settings dialog).
    `drives.scan_mounts` lists it as a drive (`local: true`) when the folder exists.
  - Verify: `uv run pytest tests/test_cards.py tests/test_web_api.py -q`
  - Result (2026-11-17): `uv run pytest tests/test_cards.py -q` → 12 passed (Mac folder tests included).

- [x] 2. Card check and removability
  - ID: card-2 (D-046, D-048)
  - `photovault/cards.py`: `check_card(conn, cfg, mount, emit, should_stop)` registers the card (CARD), then runs
    `index_volume` and `baseline_hash` (read-only). `card_files`, `card_summary` and `cameras` classify files by
    the tiers above; a mounted card is walked (names and sizes only) so new or changed files show as `unchecked`.
    Camera per file: the capture group from the card's index or ingest job items, else the most common camera
    in the same folder (camera videos), else "Unknown camera".
  - CLI: `cards check <mount>` and `cards list`. Relies on ing-12: a group made by the card check is renamed to
    the D-005 stem when the card is imported.
  - Verify: `uv run pytest tests/test_cards.py -q`
  - Result (2026-11-17): `uv run pytest tests/test_cards.py -q` → 12 passed.

- [x] 3. Cards and cameras in the web UI (visual check pending, user)
  - ID: card-3
  - `GET /api/cards` (cards with tier counts, worst colour, cameras; cameras aggregated over connected cards),
    `GET /api/cards/<volume_id>/files?tier=&camera=&limit=&offset=` (most urgent first), `POST /api/run`
    `card_check` (mount). A Cards view (header button) lists connected cards first, each with a stacked colour
    bar; a card opens its ranked file list; a Cameras table colours each camera worst first.
  - Verify: `uv run pytest tests/test_web_api.py -q`; the user does the visual check.
  - Result (2026-11-17): `uv run pytest tests/test_web_api.py -q` → 50 passed (with ui-7..ui-10). Visual check open.
