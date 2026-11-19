# Spec: shoots — trips and shoots proposed by time and place, reviewed as a dry run

Source: `docs/reviews/2026-11-18-sh-0-shoot-tagging.md`, approved by the user on 2026-11-18 (D-080) with these
answers:
- 0: the two-time model.
- 1A: trips above shoots.
- 2: a time zone per shoot.
- 3: names from a dated folder, then the phone's place, then other folders (with a stoplist), then a fallback. An LLM title
  from the images is a later step (sh-7).
- 4: schema V7 (landed: `fd1ce3e`, `system-design.md` §5g).
- 5: Apply changes the catalog only; review happens in its own dry-run mode, and XMP writes of reviewed data come later
  from there (sh-6).
- 6: subsec stored.

Rules for every task:
- Vault rules hold. Planning reads the catalog and writes only the plan file. Only Apply (sh-4) writes catalog rows,
  and no task touches a file on a drive.
- Example names in code, tests and docs are invented ("Harbour Moonrise", "Northland").
- Real-catalog runs are read-only (`?mode=ro`), and their reports go to `docs/private/`.
- UI work runs `cd web && npm run typecheck && npm test && npm run build`, and the build is committed.

## Plan file format (the contract between the planner and the review mode)

`<home>/plans/shoots-<YYYYMMDD-HHMMSS>.json`, UTF-8:

```json
{
  "plan_id": "shoots-20261118-210000",
  "created_at": "2026-11-18T21:00:00+00:00",
  "params": {"gap_h": 8, "split_km": 50, "merge_h": 36, "region_km": 300, "from": null, "to": null},
  "summary": {"frames": 65194, "shoots": 171, "trips": 35, "unassigned": 0, "locked": 0, "flagged": 40},
  "trips": [
    {"proposal_id": "t:<sha1 of first and last group id>",
     "name": {"value": "Northland · 9–12 Sep", "source": "phone",
              "alternatives": [{"value": "2023-09-09 trip", "source": "fallback"}]},
     "start_date": "2023-09-07", "end_date": "2023-09-10", "shoot_ids": ["s:…", "s:…"], "flags": []}
  ],
  "shoots": [
    {"proposal_id": "s:<sha1 of first and last group id>", "trip_id": "t:…",
     "name": {"value": "Harbour Moonrise", "source": "folder", "alternatives": [{"value": "Harbourton", "source": "phone"}]},
     "shoot_date": "2023-09-09", "start_instant": "2023-09-10T03:05:00+00:00", "end_instant": "2023-09-10T04:30:00+00:00",
     "wall": {"start": "2023-09-09T20:05:00-07:00", "end": "2023-09-09T21:30:00-07:00"},
     "local_offset": {"value": "-07:00", "camera": "-04:00", "source": "phone", "changes": true},
     "group_ids": [101, 102],
     "counts": {"frames": 312, "by_body": {"Z6": 300, "Z6II": 12}, "located": 40},
     "sample_group_ids": [101, 140, 180],
     "centroid": {"lat": 0.0, "lon": 0.0}, "points": [[0.0, 0.0]],
     "gaps": [{"after_group_id": 150, "gap_s": 5400}],
     "flags": ["multi_place", "tz_change", "body_clock_mismatch", "huge_timelapse", "weak_name"],
     "locked": false}
  ]
}
```

- `sample_group_ids` holds at most 12 ids: first, last, and evenly spaced in between.
- `points` holds at most 50.
- `gaps` holds the 20 largest gaps inside the shoot, which is where the review UI offers a split.
- Proposal ids are stable for the same first and last frame, so decisions in `shoot_plan_decisions` apply again
  after a re-plan.
- Locked shoots are existing accepted shoots, shown read-only.

## Tasks

- [x] 1. **[stronger]** The two-time model in the engine and the UI
  - ID: sh-0b. _Design:_ review §0, `system-design.md` §5g.
  - `photovault/timemodel.py`:
    - `instant(row, *, default_offset, clocks) -> (datetime UTC, provenance)`. Provenance is `gps` | `aligned` |
      `exif_offset` | `assumed_offset`, plus a `user_shift` flag.
    - `BodyClocks.load(conn)` holds the accepted `body_clock` rows for lookup by (body_id, instant).
    - `wall_clock(conn or row, instant)`: the shoot's `local_offset` if the group is in such a shoot, else
      `capture_time` as written.
  - Callers move to the instant: `geo.group_utc` delegates to it (keep its name and signature), so `suggest`,
    `session_of`, `places_near`, `clock.suggest_offsets` and the `/api/groups` `utc_time` all follow.
  - `/api/groups` rows gain `time_provenance`, `wall_time` and `wall_date`. `/api/photo-days` groups by wall date.
    rv-16's "Same day" uses `wall_date`.
  - sp-7's banner Apply writes an accepted `body_clock` row (source `phone`, the run's UTC range) instead of per-frame
    `time_shift_s`. `GET /api/body-clock` lists the rows; `POST /api/body-clock/<id>/delete` removes one (undo).
  - Inspector: the time line shows provenance as a small tag ("camera offset", "aligned to phone", "assumed offset").
  - `meta scan` reads `SubSecTimeOriginal` into `capture_groups.subsec`, and `naming.vault_name` uses it (rv-18).
  - Verify: `uv run pytest tests/test_timemodel.py tests/test_geo.py tests/test_clock.py tests/test_web_api.py -q`;
    `cd web && npm test`.
  - Result (2026-11-18): `photovault/timemodel.py` (`instant`, `instant_info`, `BodyClocks`, `wall_clock`); `geo.group_utc`, `suggest`, `session_of`, `clock.suggest_offsets` and `meta scan` follow the instant. `/api/groups` rows carry `time_provenance`, `time_correction_s`, `time_user_shift`, `wall_time`, `wall_date`; `/api/photo-days` and the `day` filter use the wall date. `GET/POST /api/body-clock` and `POST /api/body-clock/<id>/delete`; the banner's Apply writes a row (suggestions carry `start_utc`/`end_utc`). `subsec` is stored by ingest, index and `meta scan`, and `vault_name` uses it. 594 passed, 9 skipped; web 71 tests.

- [x] 2. **[stronger]** The planner, calibrated on the real catalog
  - ID: sh-1 (after sh-0b). _Design:_ review §3–4.
  - `photovault/shoots.py` `plan(conn, *, gap_h=8, split_km=50, merge_h=36, region_km=300, frm=None, to=None) -> dict`
    in the format above. It is pure and writes nothing.
    - Boundaries on the instant; the location split; trips; names.
    - Names: date-prefixed folder → phone `place_name`/city → other folder minus the stoplist (setting
      `shoot_name_stoplist`, a JSON list) → fallback.
    - Flags and the per-shoot `local_offset` proposal.
    - Decisions from `shoot_plan_decisions` applied on top.
  - Calibration: `photovault/shoots.py` `calibrate(conn)` scores the boundaries against the date-prefixed folders for
    `gap_h` in 4, 6, 8 and 12 (precision and recall of shoot edges, and the folders split or merged). Run it read-only on
    the real catalog, with the report in `docs/private/`. The default `gap_h` follows the result.
  - Verify: `uv run pytest tests/test_shoots.py -q` on synthetic timelines (a night crossing midnight, a trip, a move of
    more than 50 km, two bodies, a locked shoot, a decision replayed after a re-plan).
  - Result (2026-11-18): `photovault/shoots.py` (`plan`, `calibrate`, `write_plan`); 27 tests in `tests/test_shoots.py`. Calibration on the real catalog (65,194 frames, 17 date-prefixed folders as truth; recall counts a folder's start and end edges): gap_h 4 / 6 / 8 / 12 gave 207 / 188 / 182 / 169 shoots, precision 0.54 / 0.55 / 0.58 / 0.59, recall 0.79 / 0.72 / 0.72 / 0.72, folders kept whole 7 / 8 / 8 / 8 of 17 (the rest are multi-day folders, i.e. trips). Default `gap_h` stays 8. A real plan: 182 shoots, 36 trips, 135 flagged, 30 `local_offset` proposals, 2.3 s, 115 MB.

- [ ] 3. `shoots plan` CLI and the plan API
  - ID: sh-2 (after sh-1).
  - `photovault shoots plan [--from] [--to] [--gap-h]` writes the plan file and prints the summary (JSON lines).
  - `GET /api/shoots/plan` returns the latest plan; `POST /api/shoots/plan` runs a new one as a task.
  - `POST /api/shoots/decisions {proposal_id, kind, payload}` stores a decision. `GET /api/shoots/decisions` lists them.
  - Verify: `uv run pytest tests/test_shoots_api.py -q`.

- [x] 4. Review mode: a separate dry-run pane
  - ID: sh-3. Built first against fixture plans in this format; wired to the live API when sh-2 lands.
  - A route `#/review/shoots`, reached from Photos ("Review shoots") and the nav. It must be **visibly another mode**:
    - a persistent mode bar ("Dry run — nothing changes until you Apply · N decisions pending");
    - a distinct accent (the warning hue);
    - a hatched edge on the page frame;
    - the normal Photos chrome hidden.
  - One card per trip with its shoots inside, then the shoots without a trip. Each shoot card shows:
    - the editable name, with alternatives as chips showing their source;
    - the dates as wall clock, and a time-zone change chip ("−04:00 → −07:00");
    - counts by body and the located share;
    - a thumbnail strip from `sample_group_ids` (thumbs only);
    - a mini map of `points` (lazy Leaflet);
    - flags as chips.
  - Actions:
    - on a shoot: Accept, Rename, Reject name (adds it to the stoplist), Split here (a timeline strip of `gaps`, sized by
      gap), Merge with previous/next, Move to trip, Skip;
    - on a trip: Rename, Accept trip;
    - for the whole page: Accept all unflagged.
    Each action records a decision and updates the card at once. A side panel lists the pending decisions, each with
    undo.
  - Apply is disabled until sh-4 lands.
  - Verify: `cd web && npm run typecheck && npm test` (decision reducer, split/merge preview); **[user]** browser check.
  - Result (2026-11-18): built against `tests/fixtures/plans/shoots-fixture.json` until sh-2 (`GET /api/shoots/plan` serves the
    newest plan, or `PHOTOVAULT_DEV_PLAN=<path>`); decisions are kept in the browser per `plan_id` (`TODO(sh-2)`); Apply is
    disabled until sh-4. Reducer in `web/src/lib/review.ts`, views in `web/src/views/review/`. Not seen in a browser yet.

- [ ] 5. Apply and undo
  - ID: sh-4 (after sh-2, sh-3).
  - `POST /api/shoots/apply` runs as a job, in one transaction:
    - creates trips and shoots (name, shoot_date, `local_offset`, `name_source`, `trip_id`);
    - sets `capture_groups.shoot_id`;
    - writes `shoot_apply_log`.
  - `POST /api/shoots/undo {job_id}` restores the log. Applied shoots become locked in later plans.
  - Photos shows the shoot name on day headers, plus a shoot filter.
  - Verify: `uv run pytest tests/test_shoots_apply.py -q`.

- [ ] 6. The wall clock through shoots
  - ID: sh-5 (after sh-4). Applied shoots' `local_offset` drives wall time, wall date and day grouping everywhere. The
    inspector shows "−07:00 (shoot)" next to the time.
  - Verify: `uv run pytest tests/test_timemodel.py -q -k wall`; **[user]** a trip's late-night frames sit on the right day.

- [ ] 7. XMP from reviewed data (later; proposal first)
  - ID: sh-6. In review mode, choose shoot fields to write to sidecars (shoot name as a keyword or headline, place,
    corrected `DateTimeOriginal`?). The review names the exact tags first; legacy drives are never written (rule 1).

- [ ] 8. Titles from the images by an LLM (backlog; proposal first)
  - ID: sh-7. The user's idea (D-080): when a shoot only has a fallback name, ask a model to suggest a title from a
    few sample previews. This needs a vault rule 9 exception, a model and cost choice, and a privacy decision (images
    leave the Mac), so it is a proposal, not code.
