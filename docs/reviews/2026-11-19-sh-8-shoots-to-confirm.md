# sh-8: "Shoots to confirm": stacks of date · place · subject, with confidence-ranked members

Status: **approved 2026-11-19 (D-127)**; tasks in `docs/specs/shoots/tasks.md` (sp-9, pv-1, sh-8..sh-10, ob-1, sh-7).
Answers to §9:
1. Pre-tick at score ≥ 0.5, untick below, hide below 0.1. Beta: everything ≥ 0.1 is shown; both cut-offs are settings
   to tune later.
2. Import the phone analysis, with a confidence that falls with the time distance. Each attribute falls at its own
   rate: a phone location 6–12 h away can still hint at the area, while labels, captions, people and activities from
   that far away are junk (an astro night with no phone shots). Every detail chip shows its confidence.
3. Review thumbs: yes, **opt-in**, and the user picks where they are stored (which device).
4. Tiers 1/2: local and API, both behind a setting.
5. Schema approved, with **separate `place` and `subject` columns**.
6. Phone photos stay **context only**. Importing them as photos is a separate, later option; the two libraries are not
   merged yet.

The sections below are the proposal as reviewed; where they differ, the answers above win.

It builds on sh-0..sh-4 (D-080): the planner, the review mode,
and Apply/Undo all exist. This review changes three things. Shoot membership gets **confidence**, a shoot gets a
**subject**, and review becomes a **queue of stacks** that a new user works through once their library is built.
This file is public, so it holds no names or places.

## 1. What other tools do, and what we take from them

| Tool | Pattern | What we take |
|---|---|---|
| Apple Photos (People, Memories, Trips) | "Is this X?", answered yes/no per suggestion; trips and moments proposed automatically, titled place + date | Confirm-or-reject on *suggestions*, never on a blank form. Titles in the form place · date. |
| Google Photos (face groups, album suggestions) | "Add 14 more photos to this album?": a core set plus suggested additions | The **core + edge candidates** split. Additions are offered, not forced. |
| Lightroom Classic (People view, Map tracklogs) | Confirmed vs suggested faces in two zones; a tracklog with a time offset assigns GPS | Two visual zones (in / maybe). A clock offset is one per-body decision, not a per-frame one (we have it already: `body_clock`). |
| Photo Mechanic (stationery pad, code replacement) | One metadata template applied to a whole batch; very fast keyboard flow | Details are confirmed once per stack and inherited by every member. One key per action. |
| PhotoPrism / Immich (labels, review queue, CLIP search) | Labels with a confidence; low-confidence items go to a Review queue | Show the *reason* and the confidence; let low confidence wait in a queue. |
| Peakto, Mylio (multi-catalog / multi-device organizers) | One index over many sources, browsing offline | Our premise: the catalog is the library, and drives can be unplugged. |
| Narrative Select / Aftershoot (culling) | Bursts collapsed into one "scene" tile | Members shown as **session tiles** (a burst or timelapse is one tile with a count), never 900 thumbnails. |

## 2. Can this run from the catalog alone? Yes, with two caveats

Everything that decides a shoot is already in the catalog: the instant and its provenance (sh-0b), body, sessions
and sequences, camera GPS, phone points with place names and offsets (sp-2), human folder names, and drives. The
planner reads only `catalog.sqlite` today (2.3 s for 65k frames). No drive needs to be connected.

The two caveats:
1. **Previews.** A member grid needs thumbnails. They live in `<home>/thumbs/<group_id>.jpg` (act-4) and are filled
   only while a drive is connected. Measured today: 8,778 thumbs = 968 MB, so about 110 KB each, because they are the
   camera's embedded preview stored as is. All 65k frames would be about **7 GB**. Proposal (pv-1): during `hash`,
   which already reads every byte, also extract the embedded preview and store a **384 px review thumb** (Pillow,
   already approved for seq-3) at about 20 KB. That is about 1.3 GB for the library, and every shoot can then be reviewed
   with every drive unplugged. Shoots with no thumbs still show "plug in X to see 312 frames", and their details can
   be confirmed while their members wait.
2. **Subject.** Time and place come from metadata, but "what was this?" needs either the phone's own analysis (next
   section) or pixels.

### The phone export already holds a subject source we throw away

The osxphotos export the user ran on 2026-11-18 (19,845 rows) carries Apple's on-device analysis. `phone.py`
reads only time, GPS and place from it. Fill counts (fields, not values):

| Field | Rows filled | Use |
|---|---|---|
| `ai_caption` (Apple's on-device sentence) | 19,691 (99 %) | the best free subject hint |
| `labels` (scene classes, 1,405 distinct) | 19,099 (96 %) | subject words ("mountain", "waterfall") |
| `persons` (8 named) | 6,320 (32 %) | "with …" people chip |
| `search_info.activities` / `venues` / `venue_types` / `landmarks` / `bodies_of_water` / `areas_of_interest` | 4,501 / 1,002 / 970 / 94 / 696 / 2,230 | place and subject precision ("hiking", a venue name) |
| `search_info.holidays`, `events` | 1,611 / 146 | subject hints ("New Year's Eve") |
| `albums` | 1,878 | the user's own grouping, a name source like folders |

This is text, computed on the Mac, and needs no re-export. A shoot with phone frames in its window (49 % of
clusters, sh-0 §1) can get a subject suggestion **with no LLM and no pixels leaving the Mac**. The LLM matters most
for the other half (the 2021 frames have almost no phone data).

## 3. The new user's path

```
connect drive ─► index ─► hash (+ review thumbs, pv-1) ─► repeat per drive / card / old folder
                                                              │
import iPhone library (sp-2 + labels, sp-9) ─────────────────┤
                                                              ▼
                                     meta scan ─► shoots plan (with confidence, sh-8)
                                                              ▼
                       Home: "182 shoots to confirm · 41 need a subject · 12 have frames in question"
```

The library checklist on the home/Tasks page shows each step's state, so the user knows when "shoots to confirm"
is complete enough to start. The plan is re-run after each new drive, and confirmed shoots stay locked (sh-1).

## 4. The queue: "Shoots to confirm"

It is the existing `#/review/shoots` mode (dry run, nothing written until Apply), reorganized as an inbox:

- Header: progress ("34 of 182 confirmed · 1,920 frames unassigned"), Apply N, and the pending decisions panel (exists).
- **Sort**: Easiest first (the default; high confidence, a few keystrokes each, to build momentum), Biggest first,
  Newest first, Connected first (sh-3b).
- **Filter chips**: Needs subject · Frames in question · Time zone change · Bodies disagree · No previews.
- Trips stay as a grouping row ("Northland · 9–12 Sep · 4 shoots"). A trip is confirmed after its shoots, and
  its name is proposed from them.
- **Accept all high-confidence** replaces "Accept all unflagged". It accepts the core members and the details as
  proposed, and leaves the edges and the subject open.

Each collapsed stack reads as one line, with its source shown on hover:

```
▸ Sat 9 Sep 2023 · Harbourton lookout · Moonrise over the harbour        312 frames  ●●●○ 3 in question
```

## 5. One stack, three steps (keyboard-first)

Opening a stack shows one focused pane. `Enter` confirms and moves on, `Tab` switches between the alternatives,
`J/K` moves between tiles, `X` toggles in/out, `S` skips.

**Step 1: Details.** Each field is a chip row: the proposal, alternatives with their source, and type-to-edit.
- **Date**: wall-clock date and range, and the time-zone change chip (exists).
- **Place**: phone `place_name` → venue / landmark / area of interest → city → camera-GPS reverse geocode → folder.
- **Subject**: see §6. Shown with its source ("from 23 phone captions", "from the folder name", "suggested by model").
- **People** (optional): `persons` on phone frames in the window.
- The **name** is composed: `place · subject` by default, editable. That keeps `UNIQUE (shoot_date, name)` honest
  without the "· evening" suffix in most cases.

**Step 2: Members.** A grid of session tiles in time order. A burst, bracket (seq-2) or timelapse is one tile with
a count, scrubbable on hover. All core tiles are in. Unticking one sends its frames back to the pool, where they
become unassigned or part of the neighbouring proposal on the next plan. Thin markers on the time strip show gaps
(Split here, exists).

**Step 3: In question.** Tiles from outside the core, below a divider, each with its reason in plain words:

```
  +2 h 10 min after the last frame · same body · 3 km away            [Add] [Not this shoot]
  Z6II frames at the same time · other body, clock −1 h unaligned     [Add] [Not this shoot]
  Phone: 6 photos at "Harbourton lookout" 40 min before                [shown as context only]
```

Unanswered edges stay with the next plan's neighbour. "Not this shoot" is remembered, so a re-plan does not offer it
again.

## 6. Membership confidence (planner change, sh-8)

Today the planner draws hard boundaries. Proposal: keep those boundaries as the **core**, and score the frames
around them.

Candidates for a shoot are the sessions within `max(3 × the shoot's largest internal gap, 6 h)` of its core, in
neighbouring shoots or unassigned. Each candidate session gets a score from 0 to 1, from these terms (weights to be
calibrated, as in sh-1):

| Term | Pulls in | Pushes out |
|---|---|---|
| Time | gap to the core, against the gaps inside the shoot | longer than the gap to another core |
| Place | located within `split_km` of the shoot's points | located far away, or near another shoot |
| Continuity | same body, same sequence, same human folder, same phone album | different folder with a human name |
| Clock trust | provenance `gps` / `aligned` | `assumed_offset` with a body disagreement |

Bands: **core** (in the plan's boundary, shown as in), **likely** (≥ 0.6, shown in "In question" and pre-ticked),
**maybe** (0.3–0.6, unticked), and below 0.3 nothing is shown. Each candidate carries the 1–3 terms that decided it, which
is what the UI prints as its reason.

Plan file (additive; the sh-2 contract stays readable):

```json
"members":    {"core_sessions": [[101, 140], [141, 160]]},
"candidates": [{"session": [161, 175], "score": 0.72, "band": "likely",
                "reasons": [{"term": "time", "text": "+2 h 10 min after"}, {"term": "body", "text": "same body"}],
                "home_proposal": "s:…"}],
"details":    {"place": {"value": "…", "source": "phone_venue", "alternatives": []},
               "subject": {"value": "…", "source": "phone_caption", "evidence": 23, "alternatives": []},
               "people": []}
```

Sessions are given as `[first_group_id, last_group_id]`, so the file stays small.

## 7. Subject: tiers, cheapest and most private first

| Tier | Input | Output | Leaves the Mac | Covers |
|---|---|---|---|---|
| 0. Rules | phone `labels`, `ai_caption`, `activities`, `landmarks`, `holidays`, phone `albums`, human folder names in the window | top 3 terms after a stoplist of generic labels ("sky", "outdoor"); a caption picked as an example | nothing | about half the shoots (phone-covered) |
| 1. Text LLM | the tier-0 words + place + date + time of day + body/lens ("night, 14 mm, 30 s exposures" says astro) | a 2–5 word subject and 2 alternatives | only text you can read in the UI before sending | phone-covered shoots, better titles; EXIF-only hints for the rest |
| 2. Vision LLM | 4–6 review thumbs (384 px) per shoot from the cache, with the tier-1 text | subject + keyword suggestions | thumbnails | every shoot with cached thumbs |
| 2-local | the same, on a local model | the same | nothing | when it is good enough on this Mac |

The size of the work: 182 shoots × 6 thumbs at 384 px is about 1,100 images. That is a few hundred thousand input
tokens in all, a one-off for the whole library. Model and price are chosen in sh-7.

Guardrails (needs a rule 9 exception, D-012):
- **Never in ingest or dedupe.** It runs only from review, when the user presses "Suggest subjects", on a scope they
  choose ("these 41 shoots").
- **A preview of the payload**: the exact text, and the thumbnails, that will be sent. GPS coordinates are never sent;
  the place name is sent only if the user ticks it.
- The output is a **suggestion chip** with source `model`. It is never applied without the user confirming it in
  step 1.
- Results are cached in the plan's details, keyed by proposal id plus a hash of the input, so a re-plan does not
  pay twice.

## 8. Catalog changes (needs approval, rule 6; after cu-0's V9 → V10)

```sql
ALTER TABLE shoots ADD COLUMN place TEXT;
ALTER TABLE shoots ADD COLUMN subject TEXT;
ALTER TABLE shoots ADD COLUMN subject_source TEXT;    -- user | phone | folder | model
ALTER TABLE shoots ADD COLUMN confirmed_at TEXT;      -- NULL = applied but not confirmed through a stack
-- decision kinds for step 2/3 and step 1
--   include / exclude (payload: session [first,last]), detail (payload: field, value, source)
-- shoot_plan_decisions.kind CHECK widens (a table rebuild, as V8 did for jobs)
CREATE TABLE phone_analysis (                         -- sp-9: Apple's on-device analysis, text only
  point_id INTEGER PRIMARY KEY REFERENCES geo_points, caption TEXT, labels_json TEXT, persons_json TEXT,
  hints_json TEXT                                     -- activities, venues, landmarks, holidays, albums
);
```

People are kept local, like everything else in the catalog. They never go in git, and they are sent to a model only if
the user ticks them.

## 9. Decisions for the user

1. **Bands**: core in, likely pre-ticked, maybe unticked, below 0.3 hidden (recommended)? Or should likely also
   start unticked?
2. **Phone analysis (sp-9)**: import `ai_caption`, `labels`, `persons` and the `search_info` hints from the export
   you already have, into the `phone_analysis` table? The people names stay local.
3. **Review thumbs (pv-1)**: a 384 px thumb for every frame, written during `hash` (about 1.3 GB), so review works
   with every drive unplugged?
4. **Subject tiers**: tier 0 now (recommended). For tiers 1 and 2, local first, the API first, or both behind a
   setting? That is the rule 9 exception, and sh-7 picks the model and the price.
5. **Schema (§8)**: approve, or change? In particular, should `place` and `subject` be columns, or only parts of the
   name?
6. **Phone photos as members**: should they show inside a shoot's grid, as real catalog assets, or stay context only
   (recommended for now: they are not on our drives)?

## 10. Build order after approval

| ID | Task | Needs |
|---|---|---|
| sp-9 | Import the phone analysis into `phone_analysis` (stdlib JSON, the existing export) | decision 2 + schema |
| pv-1 | Review thumbs during `hash` (and `cache_volume`), 384 px via Pillow | decision 3 |
| sh-8 | Planner: core sessions, candidates with scores, bands and reasons; details (place, subject tier 0, people); calibrate the weights on the dated folders | sp-9 for subject |
| sh-9 | Queue UI: sort, filters, collapsed stacks, Accept all high-confidence | sh-8 |
| sh-10 | Stack UI: the three steps, session tiles, keyboard, the include/exclude/detail decisions; Apply writes place, subject, `confirmed_at` | sh-9 + schema |
| ob-1 | Library checklist on the home page (drives indexed, hashed, thumbs, phone, plan) | — |
| sh-7 | Proposal, then build: tiers 1/2, payload preview, cache | decision 4 |
