# Spec: ingest (Phase 2) — verified card import

Goal: plug in a card, copy it to the working SSD with each byte read once and checked, record everything in
the catalog, and get a trustworthy green or red **card-status**. Policy today is one verified copy on WORKING
(D-003).

Rules:
- `docs/steering/task-execution.md` applies.
- Commit as `feat(ing-N): …  [ing-N]`.
- Tests use `tmp_path` dirs as "cards" and "drives", never `/Volumes`.

- [x] 1. Hashing
  - ID: ing-1
  - Create `photovault/hashing.py`:
    - `CHUNK = 8 * 1024 * 1024`.
    - `sha256_file(path)` streams the file in CHUNK reads.
    - `partial_sha256(path)` = sha256 over `b"pv1"` + the size as 8-byte big-endian + the first 64 KiB + the
      64 KiB starting at `size // 2 - 32 KiB` (clamped to 0) + the last 64 KiB.
    - Files ≤ 192 KiB use the whole content instead of the three windows. Keep the `b"pv1"` + size prefix.
  - Create `tests/test_hashing.py` covering:
    - known vectors for an empty file and the bytes `b"abc"` (compare with `hashlib`);
    - large random files: the partial changes when one byte inside any window changes, and stays the same when
      a byte outside every window changes;
    - identical files → identical hashes.
  - Verify: `uv run pytest tests/test_hashing.py -q`
  - Result (2026-11-11): `uv run pytest tests/test_hashing.py -q` → 12 passed; `uv run pytest -q` → 168 passed.
  - _Design:_ system-design §3 (hashing); audit §2.2 tiers

- [ ] 2. Verified copy engine
  - ID: ing-2
  - Blocks on: ing-1
  - Create `photovault/copy_engine.py` with `copy_verified(src, dst, *, mtime_ns=None) -> CopyResult` and
    `CopyError`. Steps:
    1. Raise `FileExistsError` if `dst` exists. Create the parent dirs.
    2. `tmp = dst.parent / f".{dst.name}.pvpartial"`. Delete a stale tmp if one exists.
    3. Open `src` for reading and `tmp` with `O_CREAT|O_EXCL|O_WRONLY`. Loop over CHUNK reads, updating the
       SHA-256 and writing.
    4. `flush`, then `fcntl.fcntl(fd, fcntl.F_FULLFSYNC)`. If that raises `OSError`, call `os.fsync(fd)`.
       Record `sync_method` as `"F_FULLFSYNC"` or `"fsync"`.
    5. Re-open `tmp` with `F_NOCACHE` (`getattr(fcntl, "F_NOCACHE", 48)`) and re-hash it. On a mismatch,
       unlink tmp and raise `CopyError("verify mismatch …")`.
    6. `os.utime(tmp, ns=(atime, mtime_ns or src mtime))`.
    7. Raise `FileExistsError` if `dst` appeared meanwhile; otherwise `os.rename(tmp, dst)`.
    8. Best-effort fsync of the parent directory (ignore `OSError`).
    9. `partial_sha256(dst)`.
  - Any exception after creating tmp unlinks it before re-raising. Never use `shutil.copy2` or `copystat`
    (they write xattrs, which become AppleDouble files on exFAT).
  - Create `tests/test_copy_engine.py` covering:
    - a round trip (hash and bytes equal; mtime preserved);
    - refusing an existing dst;
    - a failure mid-write (monkeypatch `os.write` to raise after the first chunk) → no dst and no tmp left;
    - a readback mismatch (monkeypatch the readback hash) → `CopyError` with no dst;
    - no extended attributes on dst when src has one (`os.setxattr` on src, `os.listxattr(dst) == []`);
    - `sync_method` is one of the two values.
  - Verify: `uv run pytest tests/test_copy_engine.py -q`
  - _Design:_ system-design §3; vault-rules 3, 8

- [x] 3. Scanner and file kinds
  - ID: ing-3
  - Create `photovault/scan.py` with `FileEntry`, `classify(name)` and
    `walk(root, *, include_ignored=False)`. Kinds, skips and the derivative-stem rule are as in system-design §8.
    `rel_path` uses POSIX separators relative to `root`. Yield in sorted order (deterministic).
  - Create `tests/test_scan.py`. Build a tmp tree that includes:
    - `DCIM/101NZ6_2/DSC_0001.NEF`/`.JPG`, plus `._DSC_0001.NEF`;
    - `.Trashes/x.JPG`, `System Volume Information/x`;
    - a zero-byte `DSC_0002.NEF`;
    - `MISC/THM/100/DJI_0001.THM`, `NIKON/Z_6_2/SENDLST1.BIN`;
    - `DSC_0850-Edit.tif`, `IMG_0001.HEIC` + `.AAE` + `.MOV`, `GX010020.MP4` + `.LRV`.
    Assert the exact yielded `rel_path`s and kinds, and that `include_ignored` adds only the ignored ones.
  - Verify: `uv run pytest tests/test_scan.py -q`
  - Result (2026-11-11): `uv run pytest tests/test_scan.py -q` → 25 passed; `uv run pytest -q` → 168 passed.
    Choices the spec left open are in D-021 (sidecar beats derivative, dot-directories, non-regular files).
  - _Design:_ system-design §8

- [ ] 4. Capture groups and sidecars
  - ID: ing-4
  - Blocks on: ing-3, fnd-5 (for the metadata key names only; tests use dicts)
  - Create `photovault/groups.py` with `Member`, `GroupPlan` and `build_groups(entries, meta)`. Rules:
    - **Base key:** `(parent dir, stem.upper())`. A derivative's stem has its suffix
      (`-Edit|-HDR|-Pano|-Enhanced-NR`, case-insensitive) stripped first, then it joins its base.
    - **Primary:** raw > heic > jpeg > video. Other members get the roles from §8. A `.MOV` next to a
      HEIC/JPG is `live_video`.
    - **Orphans:** a sidecar with no base becomes its own group (primary = the sidecar, role
      `orphan_sidecar`).
    - **Identity:** take `model`, `serial`, `shutter_count`, `capture_time`, `subsec`, `offset` and
      `time_source` from the primary's metadata, falling back to any member that has them. With no time at
      all, use the primary's mtime with `time_source="mtime"`.
    - **Original stem:** `original_stem` = the primary's stem. `original_num` = its trailing digits, last 4,
      or None.
    - **Folder hint:** a path containing `/PANORAMA/` or `/HYPERLAPSE/` → the hint is that folder's
      `rel_path`.
  - Create `tests/test_groups.py`:
    - Synthetic cases: NEF+JPG pair; NEF+JPG+XMP; `-Edit.tif` attaches; orphan XMP; HEIC+AAE+MOV; DJI
      MP4+SRT; GoPro MP4+LRV; PANORAMA DNGs get the hint.
    - Fixture case: build `FileEntry`s and metadata dicts from `tests/fixtures/meta/cards.jsonl.gz` (fake
      sizes) and assert:
      - the group count equals the number of distinct `(card, folder, stem)` triples;
      - no group has two raw members;
      - CARD2's stem `DSC_0001` yields 12 groups (rollover across 12 folders; checked against the fixture
        on 2026-11-11).
  - Verify: `uv run pytest tests/test_groups.py -q`
  - _Design:_ system-design §3 (groups), §8; audit §2.3

- [ ] 5. Naming, shoot day and Windows safety
  - ID: ing-5
  - Blocks on: ing-4, fnd-1
  - Create `photovault/naming.py` with `shoot_date`, `windows_safe`, `canonical_stem`, `dest_dir`,
    `check_unique` and `NameCollision`, exactly as in system-design §6.
  - `canonical_stem`:
    - If the group's primary is a derivative, or for derivative members, append the original suffix.
    - Callers pass `body` = the nickname looked up from `cfg.bodies` by Model, or else
      `windows_safe(model)` with spaces removed.
  - Create `tests/test_naming.py` covering:
    - the midnight session: 23:57 and 01:28 the next day give the same `shoot_date`; 12:00 exactly belongs to
      that day; 11:59 belongs to the day before;
    - Nikon example → `M0781_20261027-181442-81_Z6_0084022`; iPhone → `M4821_…_IP14P_IMG4821`;
      missing subsec → `00`; `-Edit` suffix kept;
    - `windows_safe` on `C:2023 A3/Comet*`, on `CON`, and on a trailing dot or space;
    - a 200-char component is truncated to 120;
    - `dest_dir` → `Photography/Imports/2026/20261027 - Lighthouse Moonrise/Z6`;
    - `check_unique` raises on `a/X.NEF` + `a/x.nef`.
  - Verify: `uv run pytest tests/test_naming.py -q`
  - _Design:_ system-design §6; D-002, D-004, D-005

- [ ] 6. Plan a card ingest (persisted plan, dry-run capable)
  - ID: ing-6
  - Blocks on: fnd-3, fnd-4, fnd-5, ing-3, ing-4, ing-5, fnd-6
  - Create `photovault/ingest.py` with `plan_card_ingest(...) -> int`, following system-design §4 steps 1–4.
    - Refuse with `ValueError` when:
      - the source is not a card;
      - the target has no marker or its role is not WORKING;
      - the target path is inside the source, or the reverse.
    - Store `params_json`: `{"shoot_name", "source_mount", "target_mount", "config_template", "day_boundary_hour"}`.
    - Compute `partial_sha256` for each source file and record it on the source CARD instance
      (`state=indexed`).
    - Reuse the stored `canonical_stem` for existing capture groups.
    - Emit a `result` with counts per kind, groups, mtime-dated files and `planned_bytes`.
  - The function does **not** commit. The caller commits for a real run, or rolls back for `--dry-run`
    (ing-9).
  - Create `tests/test_ingest_plan.py`:
    - Setup: a `tmp_path` "card" with DCIM files, a "target" dir with a marker, `volumes.probe` monkeypatched
      to report each as card or not, and `metadata.read_metadata` monkeypatched from fixture-like dicts.
    - Assert:
      - the items and `dst_rel`s;
      - a re-plan of the same card reuses the stems;
      - a target without a marker is refused;
      - rolling back leaves the catalog empty.
  - Verify: `uv run pytest tests/test_ingest_plan.py -q`
  - _Design:_ system-design §4, §5

- [ ] 7. **[stronger]** Execute and resume a job
  - ID: ing-7
  - Blocks on: ing-2, ing-6
  - Add `run_job(...)` and `JobSummary(copied, skipped_dup, failed, bytes_copied, elapsed_s, paused)` to
    `photovault/ingest.py`, implementing system-design §4 step 5.
    - Each item runs in its own `with conn:`. Set `state=copying` first and commit, so a crash leaves
      evidence.
    - Items found in `copying` at start are retried after their stale `.pvpartial` is deleted.
    - The destination already exists:
      - Hash it uncached. Equal → mark the target instance verified and the item `committed`.
      - Different → the item is `failed` with "name collision". Never overwrite.
    - The target root disappears (`not target.is_dir()`) or `should_stop()` returns True → set the job
      `paused`, emit a warning, return with `paused=True`.
    - `CopyError` → the item fails; continue.
    - Finishing sets the job to `done`, or `failed` if any item failed.
    - Emit an `item` event per item and a `progress` event at least every 25 items.
  - Guards: refuse when the target role is CARD or the source equals the target. Never open a source file for
    writing.
  - Create `tests/test_ingest_run.py` covering:
    - a clean run copies everything and the instances are verified;
    - a second plan+run of the same card → all items `skipped_dup` and no new files;
    - a duplicate inside one card (same bytes, two names) → one copy and one `skipped_dup`;
    - `should_stop` after 3 items → paused, then resume completes;
    - the files on the "card" are unchanged afterwards (same sha256 and mtime).
  - Verify: `uv run pytest tests/test_ingest_run.py -q`
  - _Design:_ system-design §4; vault-rules 1, 3, 5

- [ ] 8. Card status
  - ID: ing-8
  - Blocks on: ing-7
  - Create `photovault/status.py` with `card_status(conn, cfg, mount) -> CardStatus`. Fields: `volume_id`,
    `state` (`green`/`red`), `total`, `safe`, `unknown: list[str]`, `changed: list[str]`,
    `short: list[tuple[str, dict]]`.
  - It is read-only: match the volume by `da_volume_uuid` via `volumes.probe`, with no registration and no
    writes.
  - For each imported-kind file from `walk(mount)`:
    - no instance → `unknown`;
    - size differs, or mtime differs by more than 2 s → `changed`;
    - `asset_id` NULL → `unknown`;
    - `verified_counts_by_role` doesn't meet `cfg.policy.required` → `short`;
    - otherwise safe.
  - Green iff `total > 0` and `safe == total`.
  - Create `tests/test_status.py` covering:
    - green after a run (reuse the ing-7 helpers);
    - red after adding a new file to the card;
    - red when policy requires MASTER=1;
    - `changed` after rewriting a card file;
    - an unknown card → red with everything unknown.
  - Verify: `uv run pytest tests/test_status.py -q`
  - _Design:_ system-design §9

- [ ] 9. CLI: assign, ingest, resume, card-status, jobs
  - ID: ing-9
  - Blocks on: ing-8
  - Extend `photovault/cli.py` with these commands:

    | Command | Behaviour |
    |---|---|
    | `volumes assign <mount> --role WORKING\|MASTER\|LEGACY --label L` | WORKING/MASTER: `write_marker`, then register. LEGACY: register by UUID with no write. |
    | `ingest --source S --target T --name N [--dry-run]` | `--dry-run` rolls back after the plan and emits it. |
    | `resume <job_id>` | Resumes a paused job. |
    | `card-status <mount>` | Exit 3 when red. |
    | `jobs [--limit 20]` | Lists jobs. |

    `SIGINT` sets `should_stop`, so Ctrl-C leaves a resumable paused job (exit 4).
  - Extend `tests/test_cli.py` with these subcommands, against tmp dirs with `probe` monkeypatched.
  - Verify: `uv run pytest tests/test_cli.py -q`
  - _Design:_ system-design §10

- [ ] 10. **[stronger]** Fault-injection suite
  - ID: ing-10
  - Blocks on: ing-9
  - Create `tests/test_ingest_faults.py`, with synthetic "card" files (random bytes, DCIM names) and
    monkeypatched metadata. Scenarios:
    1. An exception during copy of item 5 of 10 → the job fails or pauses → resume → all 10 committed,
       exactly 10 files on the target, no `*.pvpartial`, and the catalog instance count is 10.
    2. The target directory is removed mid-job → `paused`; recreate it with its marker → resume completes.
    3. A flipped byte on readback → that item `failed`, no file at its dst; the other items committed;
       `card-status` red.
    4. Run twice → the second run copies 0.
    5. A pre-existing different file at a dst → the item failed with "name collision", and the existing file
       is unchanged.
    6. The source tree's hashes and mtimes are identical before and after every scenario.
  - Verify: `uv run pytest tests/test_ingest_faults.py -q`
  - _Design:_ audit §2.4; vault-rules 1, 3, 4

- [ ] 11. **[user]** Acceptance on real hardware
  - ID: ing-11
  - Blocks on: ing-10
  - The user runs these, in a terminal:
    1. `uv run photovault volumes assign "/Volumes/X9 Pro" --role WORKING --label 1TB_WORK`
    2. `uv run photovault ingest --source "/Volumes/NIKON Z 6_2" --target "/Volumes/X9 Pro" --name "Test import" --dry-run`
    3. The same command without `--dry-run`. Time it.
    4. Run it again → expect 0 copied.
    5. `uv run photovault card-status "/Volumes/NIKON Z 6_2"` → expect green.
    6. Spot check: `shasum -a 256` on 3 source files and their copies.
    7. Optional: repeat with Card A, which has two bodies, folder rollover and a full card.
  - Paste back: the timings, `sync_method` (does exFAT via FSKit honour `F_FULLFSYNC`?), the counts, and any
    errors.
  - The agent then records the details in `docs/private/acceptance.md` and a redacted Result line here.
  - Verify: `uv run photovault card-status "/Volumes/NIKON Z 6_2"` exits 0
  - _Design:_ audit §6 Phase 2 gate
