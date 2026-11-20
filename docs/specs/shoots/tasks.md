# Spec: shoots — trips and shoots proposed by time and place, reviewed as a dry run

Source: `docs/reviews/2026-11-18-sh-0-shoot-tagging.md`, approved by the user on 2026-11-18 (D-080) with these
answers:
- 0: the two-time model.
- 1A: trips above shoots.
- 2: a time zone per shoot.
- 3: names from a dated folder, then the phone's place, then other folders (with a stoplist), then a fallback. An LLM title
  from the images is a later step (sh-7).
- 4: schema V7 (landed: `fa052a2`, `system-design.md` §5g).
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
  "decisions_through": 0,
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
     "volumes": [{"volume_id": "…", "label": "X9 Pro", "frames": 312}],
     "counts": {"frames": 312, "by_body": {"Z6": 300, "Z6II": 12}, "located": 40},
     "sample_group_ids": [101, 140, 180],
     "centroid": {"lat": 0.0, "lon": 0.0}, "points": [[0.0, 0.0]],
     "gaps": [{"after_group_id": 150, "gap_s": 5400}],
     "flags": ["multi_place", "tz_change", "body_clock_mismatch", "huge_timelapse", "weak_name"],
     "locked": false}
  ]
}
```

- `decisions_through` is the newest `shoot_plan_decisions.decision_id` already folded into the plan (sh-2). Review
  replays only the decisions after it, so none is applied twice.
- `sample_group_ids` holds at most 12 ids: first, last, and evenly spaced in between.
- `points` holds at most 50.
- `volumes` lists the drives holding the shoot's frames (primary files, indexed or verified), most frames first, so review
  can put shoots on connected drives first.
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

- [x] 3. `shoots plan` CLI and the plan API
  - ID: sh-2 (after sh-1).
  - `photovault shoots plan [--from] [--to] [--gap-h]` writes the plan file and prints the summary (JSON lines).
  - `GET /api/shoots/plan` returns the latest plan; `POST /api/shoots/plan` runs a new one as a task.
  - `POST /api/shoots/decisions {proposal_id, kind, payload}` stores a decision. `GET /api/shoots/decisions` lists them.
  - Verify: `uv run pytest tests/test_shoots_api.py -q`.
  - Result (2026-11-18): `photovault shoots plan` (writes the plan, prints `plan_id`, `path`, the summary); `POST /api/shoots/plan`
    and `/api/run` `shoots_plan` start it as a task. `GET/POST /api/shoots/decisions` (one or `{"decisions": [...]}`, all or
    none) and, beyond the spec, `POST /api/shoots/decisions/delete {decision_ids}` for undo. The plan records
    `decisions_through`; review loads only the decisions after it and saves each action as it is made, with a Re-plan button.
    "Accept trip" and "Accept all unflagged" are stored as `accept` rows sharing a `batch` (no new kinds). Split and merge
    pieces carry the planner's ids, so later decisions on them replay. A `PHOTOVAULT_DEV_PLAN` fixture keeps its decisions
    in server memory. `tests/test_shoots_api.py` 18 passed; full suite 682 passed; web 112.

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

- [x] 4b. Review sorted by connected drives
  - ID: sh-3b (after sh-3). Shoots whose frames sit on a connected drive can show previews, so review lists them first.
    - Trips with any frame on a connected drive come first, then the rest; inside a trip shoots stay in time order.
      Shoots without a trip: connected first, then time order.
    - Each shoot card shows its drives as chips (connected or not, like rv-21), from the plan's `volumes`.
    - A toggle "Connected first" (on by default, kept per browser) restores plain time order.
    - Split keeps the drives; merge adds their frame counts.
  - Verify: `cd web && npm run typecheck && npm test`.
  - Result (2026-11-18): `reviewSections` and `connectedFrames` in `web/src/lib/review.ts`; drive chips on each card; the
    toggle sits above the decision list. The fixture plan gained invented `volumes`. Web 113 tests. Browser check [user].

- [x] 5. Apply and undo
  - ID: sh-4 (after sh-2, sh-3).
  - `POST /api/shoots/apply` runs as a job, in one transaction:
    - creates trips and shoots (name, shoot_date, `local_offset`, `name_source`, `trip_id`);
    - sets `capture_groups.shoot_id`;
    - writes `shoot_apply_log`.
  - `POST /api/shoots/undo {job_id}` restores the log. Applied shoots become locked in later plans.
  - Photos shows the shoot name on day headers, plus a shoot filter.
  - Verify: `uv run pytest tests/test_shoots_apply.py -q`.
  - Result (2026-11-18): `shoots.apply` / `undo` / `list_applies`; CLI `shoots apply --plan <id> [--expect N]` and `shoots undo
    <apply_id>`; `POST /api/shoots/apply {plan_id, expect}` and `POST /api/shoots/undo {job_id}` start them as tasks (the `jobs`
    kind CHECK has no shoots kind, so the apply id is `apply-<UTC time>` in `shoot_apply_log.job_id`); `GET /api/shoots/applies`.
    Apply re-plans with the reviewed plan's params and every stored decision, and refuses when the accepted count differs from
    what the review showed. Later plans show applied shoots and trips locked. Review: Apply N (confirm, toast with Undo, then
    re-plan) and an Applied list with Undo. Photos: shoot chips on day headers and a shoot filter (`/api/photo-days?shoot_id`).
    `tests/test_shoots_apply.py` 11 passed; full suite 692 passed; web 112. Browser check [user].

- [ ] 6. The wall clock through shoots
  - ID: sh-5 (after sh-4). Applied shoots' `local_offset` drives wall time, wall date and day grouping everywhere. The
    inspector shows "−07:00 (shoot)" next to the time.
  - Verify: `uv run pytest tests/test_timemodel.py -q -k wall`; **[user]** a trip's late-night frames sit on the right day.

- [ ] 7. XMP from reviewed data (later; proposal first)
  - ID: sh-6. In review mode, choose shoot fields to write to sidecars (shoot name as a keyword or headline, place,
    corrected `DateTimeOriginal`?). The review names the exact tags first; legacy drives are never written (rule 1).

## Shoots to confirm (D-127, review `docs/reviews/2026-11-19-sh-8-shoots-to-confirm.md`)

Order: sp-9 and pv-1 (independent) → sh-8 → sh-9 → sh-10; ob-1 any time; sh-7 last. Rules for these tasks:
- Phone photos are **context only** (D-127 answer 6). They are never members of a shoot and never shown in its grid as
  frames; they appear only as evidence ("Phone: 6 photos at … 40 min before").
- Phone evidence carries a **confidence that decays with the time distance**, and each attribute decays at its own rate
  (answer 2). Δt is the time from a phone point to the shoot's core span (0 inside it). Starting values, kept as
  settings and calibrated in sh-8:

  | Attribute | Full weight within | Half-life | Dropped beyond |
  |---|---|---|---|
  | Area (city, state, locality) | ±1 h | 3 h | 12 h |
  | Spot (`place_name`, venue, landmark, area of interest) | ±30 min | 1 h | 3 h |
  | Subject (caption, labels, activities, people, events) | ±15 min | 30 min | 2 h |
  | Holiday | same wall date | — | other dates |

  Beyond 1 h, a location hints at the **area only**, never at a spot. A detail's confidence = the weighted evidence for
  the value × its share of all weighted evidence. Every detail chip shows it.
- Membership bands (answer 1): pre-ticked at score ≥ 0.5, unticked from 0.1 to 0.5, hidden below 0.1. Both cut-offs
  are settings (`shoot_pretick_min`, `shoot_show_min`).
- **Beta mode (D-128), on by default:** a setting `shoot_review_beta = true`. Nothing is hidden or dropped: candidates
  below `shoot_show_min`, phone evidence beyond the decay table's "dropped beyond" limits, and detail alternatives a
  full run would discard are all kept in the plan, flagged `"beyond_cutoff": true` with their computed confidence. The
  UI shows them with **opacity that falls with confidence** (for example `0.25 + 0.75 × confidence`, so 1.0 at full
  confidence and 0.25 at zero), and each one shows its number on hover. Everything a full run would exclude sits below a
  dashed "beyond cut-off (beta)" divider. This lets the user see what the iOS data contributes and check the confidence
  ranges. With beta off, the cut-offs apply as written above.

- [ ] 8. **[stronger]** Schema V9 and the phone analysis import
  - ID: sp-9. _Design:_ review §2, §8 (approved, D-127).
  - Migration V9 (the next free version; cu-0 moves after it), with `system-design.md` §5:
    - `shoots`: `place`, `subject`, `subject_source` (`user|phone|folder|model`), `confirmed_at`;
    - `shoot_plan_decisions.kind` CHECK adds `include`, `exclude` and `detail` (a table rebuild, as V8 did for `jobs`);
    - `phone_analysis (point_id PK → geo_points, caption, labels_json, persons_json, hints_json)`. `hints_json` holds
      `activities`, `venues`, `venue_types`, `landmarks`, `bodies_of_water`, `areas_of_interest`, `holidays`, `events`
      and `albums`.
  - `photovault meta phone <json>` also fills `phone_analysis` for the rows it keeps (the same filters). It is
    idempotent on re-import, and its result counts the rows with a caption, labels and people. Stdlib only.
  - People names live only in the catalog: never in logs, reports in the repo, or fixtures (fixtures use invented
    names).
  - Verify: `uv run pytest tests/test_phone.py tests/test_catalog_db.py -q`; then **[user]** re-run
    `uv run photovault --json meta phone "<home>/phone/photos-20261118.json"` (no new export needed).

- [x] 9. Review thumbs: opt-in, stored in a Mac folder the user picks
  - ID: pv-1. _Design:_ review §2 (answer 3, narrowed by D-128).
  - A setting `review_thumbs = {"enabled": false, "folder": "~/Sandbox/PhotoVault Thumbs"}`, plus a Settings section
    (off by default) with the size estimate (frames × ~20 KB), the folder (editable, default
    `~/Sandbox/PhotoVault Thumbs`) and Show in Finder. **Mac only (D-128)**: the server refuses a folder on a mounted
    drive (the mounts folder), so no vault-rules exception is needed.
  - When enabled, `hash` (and `cache_volume`) extracts the embedded preview of each primary file it reads, scales it to
    384 px on the long edge with Pillow (approved, rule 11), and writes `<folder>/<group_id>.jpg` atomically. It never
    slows or fails a hash: thumb errors are counted and reported. Drives are only read.
  - `/api/groups/<id>/thumb` looks in the review folder after the act-4 cache.
  - Verify: `uv run pytest tests/test_previews.py tests/test_hashing.py -q` (`tmp_path` stores only).
  - Result (2026-11-19): setting `review_thumbs` (JSON in the `settings` table, default off, folder `~/Sandbox/PhotoVault Thumbs`); `previews.ThumbMaker` (Pillow, 384 px, JPEG quality stepped down toward 20 KB, temp + rename, existing thumbs skipped, errors counted never raised) is used by `index.baseline_hash` (primary files only; the job result and `result` event carry `thumbs`, plus a `thumb_errors` warning) and `previews.cache_volume` (`review_added`, `review_errors`). `/api/groups/<id>/thumb` reads the review folder after the act-4 cache; `POST /api/settings` validates the folder (refuses the mounts folder); `POST /api/settings/review-thumbs/reveal` is Show in Finder. Settings has a Review thumbs section (switch, estimate, folder, count). Measured on 5 real NEFs (168 MB, warm cache): about 54 ms per file for the thumb against about 24 ms to hash, including one ExifTool start; 5.9 KB average on the test frames (the 20 KB figure is the ceiling).

- [ ] 10. **[stronger]** Planner: membership confidence and details
  - ID: sh-8 (after sp-9). _Design:_ review §6, plus the decay table above.
  - `shoots.plan` adds `members.core_sessions`, `candidates` (score, band, 1–3 reasons, `home_proposal`) and `details`
    (`place`, `subject` tier 0, `people`, each with value, source, confidence, evidence count and alternatives) to
    every shoot, as in review §6. It is additive; the sh-2 format stays readable.
  - Score terms: time, place, continuity (body, sequence, human folder, phone album), clock trust. Candidates come from
    within `max(3 × largest internal gap, 6 h)` of the core.
  - Subject tier 0: phone evidence weighted by the decay table. Labels go through a stoplist of generic ones (a
    setting, learned from rejected chips the way names are). Folder names are a source too.
  - `include`, `exclude` and `detail` decisions replay on re-plan. An `exclude` is never offered again for that
    shoot.
  - Calibration: weights scored against the dated folders (as sh-1); report to `docs/private/`.
  - Verify: `uv run pytest tests/test_shoots.py -q`, with these cases: an astro night with phone frames only 8 h
    before (area kept, subject dropped); a second body offset by 1 h; a sequence across the boundary; a replayed
    exclude.

- [ ] 11. The queue: Shoots to confirm
  - ID: sh-9 (after sh-8). _Design:_ review §4.
  - `#/review/shoots` becomes the queue:
    - progress ("N of M confirmed · K frames unassigned");
    - sort: Easiest first (the default), Biggest, Newest, Connected first;
    - filter chips: Needs subject, Frames in question, Time zone change, Bodies disagree, No previews;
    - collapsed stacks read `date · place · subject` with a confidence marker;
    - "Accept all high-confidence" replaces "Accept all unflagged", and accepts core members and details only.
  - Verify: `cd web && npm run typecheck && npm test`; **[user]** browser check.

- [ ] 12. The stack: details, members, in question
  - ID: sh-10 (after sh-9). _Design:_ review §5.
  - A focused pane in three steps:
    1. Details: chips with source and confidence; the name is composed from place · subject and editable.
    2. Members: session tiles; a burst, sequence or timelapse is one tile with a count.
    3. In question: candidates with reasons, pre-ticked per band, plus phone evidence as context rows.
  - Keyboard: `Enter`, `Tab`, `J`/`K`, `X`, `S`.
  - Each action stores an `include`, `exclude` or `detail` decision. Apply writes `place`, `subject`,
    `subject_source` and `confirmed_at`, and Undo restores them.
  - Verify: `uv run pytest tests/test_shoots_apply.py -q`; `cd web && npm run typecheck && npm test`; **[user]** browser
    check.

- [ ] 13. Library checklist
  - ID: ob-1. _Design:_ review §3.
  - A card on the home (Tasks) page lists, per drive: indexed, hashed, thumbs. Then the phone import (with analysis),
    meta scan, and the latest plan, with a link to "N shoots to confirm".
  - Verify: `cd web && npm test`; **[user]** browser check.

- [ ] 14. Subject from a model: local or API, behind a setting
  - ID: sh-7 (after sh-10). D-080 idea, shaped by D-127 answer 4.
  - The first step is a short proposal: the models (local and API), the price per library, and the exact rule 9
    exception text (the guardrails of review §7). It is built after the user approves.
  - A setting `subject_model = off | local | api` (default off), with the endpoint and model for each.
  - Tier 1 (text) and tier 2 (thumbs from the review store) run only from review ("Suggest subjects" on a chosen set of
    shoots), after a preview of the exact payload. No GPS is sent, and the place name only if ticked.
  - Results are suggestion chips with source `model`, cached by proposal id and a hash of the input.
  - Verify: unit tests with a fake model; **[user]** a run on 5 shoots.
