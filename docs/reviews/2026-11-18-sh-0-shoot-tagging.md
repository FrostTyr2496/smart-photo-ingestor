# sh-0: automatic shoot tagging, reviewed as a dry run

Status: **proposal, waiting for the user's decisions (section 7)**. No code until then. This replaces sp-4's shape
(`docs/specs/phone-gps/tasks.md`) and takes in cu-2 (manual shoot controls). Real numbers come from a read-only
analysis of the user's catalog (`docs/private/2026-11-18-shoot-analysis.md`). This file is public, so it holds no
names or places.

## 0. Time model: every comparison is time-zone independent (user, D-075)

A frame has two different times, and the design keeps them apart:

- **The instant** (absolute time on one UTC timeline). It is computed in three steps:
  `capture_time` − the camera's offset (EXIF `OffsetTime*`; `default_offset` only when the camera wrote none)
  \+ `time_shift_s` (the user's manual fix) + the **body clock correction**. That correction is estimated per body and
  per phone run by aligning its frames with the phone timeline (sp-7, reviewed by the user) and is 0 when unknown. Gaps,
  sessions, shoot boundaries, phone matching, interpolation and the joins between bodies use only the instant.
- **The wall clock** (what a clock on the wall said where the user stood). It is derived from the instant plus the
  offset of the **place**: the shoot's `local_offset`, else the phone's `local_offset` nearest in time, else the
  camera's offset. Display, shoot dates (noon boundary), "Same day" (rv-16 now uses the camera's local date and moves
  to this) and names use only the wall clock.

Each instant carries its provenance, and the UI shows it on the frame. Ranked from strongest:
1. `gps`: from camera GPS time.
2. `aligned`: corrected against the phone.
3. `exif_offset`: the camera wrote an offset.
4. `assumed_offset`: `default_offset` was used.
5. `user_shift`.

Matching and planning trust a frame's instant in that order. When a body's provenance is weak, the planner does not
guess per frame. It uses the body's estimated correction for that run, or else matches at the coarser tiers (the
far/weak tiers of sp-8) and flags the shoot for review.

Done this way, a camera left on home time during a trip is already handled: the instant is right, and the wall clock
comes from the phone. A camera whose clock was moved to local time without its zone is caught by the body
correction. Two bodies disagreeing by whole hours are reconciled through their corrections instead of by guessing.

Storage: the instant is computed, never stored. Per-row computation is cheap, and storing it would go stale whenever
a shift or correction changes. The body corrections (one row per body per run, `body_clock (body_id, start_utc,
end_utc, correction_s, source, accepted)`) and the per-shoot `local_offset` are stored, in V7.

## 1. What the library looks like

| Fact | Number | Consequence |
|---|---|---|
| Frames (capture groups) | 65,194, all dated | — |
| Gaps under 1 min | 97 % | Bursts and timelapses dominate; sessions are tight. |
| Clusters at gap 4 h / 8 h / 12 h / noon-boundary day | 203 / 174 / 157 / 171 | The boundary barely depends on the threshold between 8 and 12 h. |
| Runs of consecutive shoot dates | 104 (35 of 2+ days, 10 of 4+, longest 7) | Trips are real and need a level above the shoot. |
| Clusters with a phone point within ±2 h | 100 of 203 (49 %) | 2021 (44 % of frames) has almost no phone data. |
| Covered clusters with 2+ cities | 43 of 100 | One outing often moves; the name needs a "main place". |
| Human-named folders | 55, only 15 date-prefixed (`YYYYMMDD - Name`) | Folder names are hints, not boundaries. |
| Frames near home | 2.5k near, 32.3k away, 30.4k unknown | "Away from home" can't decide a trip. |
| Camera offset ≠ phone offset | about 1 in 3 samples | The camera's time zone is often wrong (a trip on home time). |
| Clusters with 2 bodies | 19; about half differ by whole hours | Body clocks disagree; flag, don't auto-fix. |
| Night frames (00–03 h) | about 4.4k | Day boundaries must sit at noon, not midnight. |

## 2. Model: trip → shoot → session

- **Session**: frames chained by short gaps (rv-16; transient, never stored).
- **Shoot**: one outing, such as an evening at a viewpoint or a night of astro. It is the unit a name, a place, a
  local time zone and (later) a folder hang on. A shoot has one **shoot date** with the noon boundary
  (`naming.shoot_date`), so a night shoot belongs to the evening it started.
- **Trip**: consecutive shoots that belong together ("a week in the desert"). Optional, since a single outing has
  none.

Why two stored levels: the user's own folders name shoots (a single night or a single place), while the day runs
show trips of up to 7 days. Folding trips into shoots would make the folder layout (ro-0) and day names coarse;
dropping trips loses the most natural browse level.

## 3. The planner: boundaries

`photovault/shoots.py` `plan(conn, *, gap_h=8, split_km=50, merge_h=36, region_km=300) -> Plan` is pure: it reads the
catalog and writes nothing.

1. **Timeline**: all camera frames on corrected UTC (`geo.group_utc`), plus phone points (sp-2) as context only.
2. **Shoot boundaries**: a gap of `gap_h` or more between consecutive frames (any body). Inside a candidate, split
   where consecutive *located* items (camera GPS, phone point, or a frame located by the user) are more than
   `split_km` apart *and* the frames on either side are more than 1 h apart. That catches a drive between two places
   without cutting a moving timelapse. Never split inside a sequence (seq-2), or between frames less than 15 min
   apart.
3. **Trips**: merge consecutive shoots when the gap between them is at most `merge_h`, and at least one of these holds:
   their centroids are within `region_km`; the phone's offset differs from home on both; or they share a human
   folder. For 2021, where phone data is missing, the frames' own `capture_offset` and folder names stand in.
4. **Locked**: frames already in an accepted shoot are never re-planned. The user's earlier splits, merges and
   rejected names are stored as constraints, so a re-plan does not undo them.

Calibration (the first build step, sh-1): run the planner on the real catalog and score its boundaries against the
15 date-prefixed folders, which act as ground truth. Report agreement for `gap_h` 4, 6, 8 and 12; the default follows
the result.

## 4. The planner: names and time zones

**Name** for each shoot, with alternatives shown and their source:
1. A date-prefixed human folder that covers most of the shoot ("Harbour Moonrise").
2. Phone place: Photos' own `place_name` (often a point of interest, better than a town), else the most common city.
3. Another human folder name that is not on the stoplist. The stoplist is learned: names the user rejects ("selected",
   "240gb") are never offered again.
4. A fallback of the date and the first body ("2023-09-09 · Z6").

A **trip** is named from its most common region or state plus the date range ("Northland · 9–12 Sep"). The user
can type over any name.

**Time zone per shoot** (the DSC_0340 case: a camera left on home time during a trip). When the shoot's phone points
agree on one `local_offset`, that offset differs from the frames' `capture_offset`, and phone matching already works
at Δ ≈ 0 (so UTC is right), the plan proposes "show this shoot in −07:00". That changes the displayed local time,
the shoot date and "Same day", and never the UTC or the clock shift. The camera's offset is wrong a third of the time,
so this is a review item, never applied silently. Whole-hour body disagreements inside a shoot are flagged and link to
sp-7.

## 5. Review: the dry run the user confirms

- `photovault shoots plan [--from DATE] [--to DATE] [--gap-h 8]` writes the plan to `<home>/plans/shoots-<time>.json`
  and prints a summary. Each proposal has a stable id (hash of its first and last group ids), so decisions survive a
  re-plan.
- UI `#/shoots/review`, one card per trip, its shoots inside. Each card shows:
  - the editable name with alternatives and their sources, dates and local time zone;
  - counts per body and the share located;
  - a thumbnail strip (first, last and evenly spaced frames), with a small map of its points;
  - flags: several places, a time-zone change proposed, bodies disagreeing, a very large timelapse, a name from a
    weak source.
- Actions on a card: Accept; Rename; **Split here** (a timeline strip with the gaps marked by size, so the user can cut
  at any gap); Merge with the previous or next; Move to another trip; Skip. The page also has **Accept all unflagged**.
- **Apply** writes catalog rows only, in one transaction recorded as a job:
  - shoots and trips;
  - `capture_groups.shoot_id`;
  - the shoot's time zone;
  - an apply log of each group's previous `shoot_id`.
  **Undo last apply** restores from that log. No file is touched. Writing shoot names into XMP and moving folders
  (ro-0) are later, separate approvals.

## 6. Schema (V7, needs approval; cu-0 moves to V8)

```sql
CREATE TABLE trips (trip_id INTEGER PRIMARY KEY, name TEXT NOT NULL, start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL, created_at TEXT NOT NULL);
ALTER TABLE shoots ADD COLUMN trip_id INTEGER REFERENCES trips;
ALTER TABLE shoots ADD COLUMN local_offset TEXT;        -- display time zone for the shoot, '+HH:MM' or NULL
ALTER TABLE shoots ADD COLUMN name_source TEXT;         -- folder | phone | user | fallback
CREATE TABLE shoot_plan_decisions (proposal_id TEXT NOT NULL, kind TEXT NOT NULL,   -- accept|split|merge|skip|reject_name
                                   payload_json TEXT NOT NULL, decided_at TEXT NOT NULL);
CREATE TABLE shoot_apply_log (job_id TEXT NOT NULL, group_id INTEGER NOT NULL, prev_shoot_id INTEGER);
CREATE TABLE body_clock (body_id INTEGER NOT NULL REFERENCES bodies, start_utc TEXT NOT NULL, end_utc TEXT NOT NULL,
                         correction_s INTEGER NOT NULL, source TEXT NOT NULL,   -- phone | gps | user
                         accepted INTEGER NOT NULL DEFAULT 0);
```

`shoots.UNIQUE (shoot_date, name)` stays. Two shoots on one date need different names, so the planner suffixes them
("· evening").

## 7. Decisions for the user

0. **Time model** (section 0): instant vs wall clock, provenance ranking, and stored body clock corrections. sp-7's
   banner would then save a `body_clock` row instead of a per-frame `time_shift_s`. OK?
1. **Levels**: trips above shoots (recommended), or shoot = trip (no `trips` table; days are only a display)?
2. **Time zone per shoot**: store a display offset per shoot, reviewed in the dry run (recommended), or leave local
   times as the camera wrote them?
3. **Name priority**: dated folder → phone place → other folder → date (recommended)? Should a learned stoplist of
   rejected folder names be kept?
4. **Schema V7** as in section 6: approve, or change?
5. **Scope of Apply**: catalog only with undo now (recommended); XMP keywords and folder moves later and separately?

## 8. Build order after approval (each an agent-sized task)

| ID | Task | Needs |
|---|---|---|
| sh-0b | Time model: `geo.instant()` and `geo.wall_clock()` with provenance; every caller (suggest, sessions, rv-16, sp-7, the planner) moves to them; `body_clock` read path | approval of section 0 |
| sh-1 | Planner + calibration on the real catalog (counts and agreement into `docs/private/`) | — |
| sh-2 | V7 migration + `shoots plan` CLI writing the plan file | approval of section 6 |
| sh-3 | Review UI (`#/shoots/review`): cards, alternatives, flags, split/merge, decisions stored | sh-2 |
| sh-4 | Apply + undo (job, apply log); Photos shows shoot names on day headers and a shoot filter | sh-3 |
| sh-5 | Time zone per shoot in display, shoot date and "Same day" (rv-16) | sh-4 |

dv-0 (derivatives without EXIF), cu-1 (names from legacy folders) and seq-2 (sequences) plug in as extra signals
later and need no new review screen.
