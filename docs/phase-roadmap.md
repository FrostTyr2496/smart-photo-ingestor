# Phase roadmap

This is the active plan. Each item points to a task in a spec's `tasks.md`; the spec holds the task text,
files, `Verify:` line and design pointers. This file adds the phase grouping, the dependency order, the [user]
gates and the parallel lanes.

Last written: 2026-11-11. Rules:
- `docs/steering/task-execution.md` applies to every task.
- One commit per task, with the task ID in the scope.
- Don't start an item until its "Blocks on" tasks are ticked and its gate is clear.

How to read the lanes:
- Items in the same phase with the same lane letter must run in order.
- Different letters can run at the same time in separate sessions or worktrees.
- Two items that edit the same file still have to be serialized (see "Shared files").
- Executor: unmarked = Haiku; **[stronger]** = Opus or Sonnet; **[user]** = you.

## Phase 1: foundation (spec `foundation`)

Goal: config, catalog, volume identity, the metadata reader and the CLI skeleton, with no real drives involved.

| Item | Task | Blocks on | Lane | Gate |
|---|---|---|---|---|
| P1.0 | fnd-0 scaffold, hooks, process (**done 2026-11-11**) | — | — | — |
| P1.1 | fnd-1 config (**done 2026-11-11**) | — | A | — |
| P1.2 | fnd-2 catalog db + migrations (**done 2026-11-11**) | — | B | — |
| P1.3 | fnd-3 repository (**done 2026-11-11**) | fnd-2 | B | — |
| P1.4 | fnd-4 volumes (**done 2026-11-11**) | fnd-3 | B | — |
| P1.5 | fnd-5 metadata reader (adds PyExifTool) (**done 2026-11-11**) | — | C | — |
| P1.6 | fnd-6 protocol + CLI skeleton (**done 2026-11-11**) | fnd-1, fnd-4 | A | — |
| P1.7 | fnd-7 sample corpus (**done 2026-11-11, partial copy**; gaps in `START_HERE.md`) | — (agent part after fnd-5) | D | **[user]** copy the sample files |

Notes:
- Lanes A, B and C can start at once.
- Shared files: `pyproject.toml` (fnd-5 adds a dependency, fnd-6 adds the script entry) and `cli.py` (fnd-6
  onward). Serialize those edits.

## Phase 2: verified card ingest (spec `ingest`)

Goal: plug in a card, copy it with each byte read once and checked, and get a trustworthy card-status.
Policy: one verified copy on WORKING (D-003).

| Item | Task | Blocks on | Lane | Gate |
|---|---|---|---|---|
| P2.1 | ing-1 hashing (**done 2026-11-11**) | — | E | — |
| P2.2 | ing-2 copy engine (**done 2026-11-11**) | ing-1 | E | — |
| P2.3 | ing-3 scanner (**done 2026-11-11**) | — | F | — |
| P2.4 | ing-4 groups (**done 2026-11-11**) | ing-3, fnd-5 | F | — |
| P2.5 | ing-5 naming (**done 2026-11-11**) | ing-4, fnd-1 | F | — |
| P2.6 | ing-6 plan (**done 2026-11-11**) | fnd-3, fnd-4, fnd-5, fnd-6, ing-3, ing-4, ing-5 | G | — |
| P2.7 | ing-7 run/resume **[stronger]** (**done 2026-11-11**) | ing-2, ing-6 | G | — |
| P2.8 | ing-8 card status (**done 2026-11-11**) | ing-7 | G | — |
| P2.9 | ing-9 CLI wiring (**done 2026-11-11**) | ing-8 | G | — |
| P2.10 | ing-10 fault suite **[stronger]** (**done 2026-11-11**) | ing-9 | G | — |
| P2.11 | ing-11 acceptance | ing-10, ing-12 | G | **[user]** real card → X9 Pro, after ing-12 |
| P2.12 | ing-12 legacy-known frames get the naming-spec stem **[stronger]** (D-042) (**done 2026-11-17**) | ing-10 | G | — |
| P2.13 | ing-13 hash a card file once when only a legacy copy matches **[stronger]** (D-042) | ing-12 | G | — |

Notes:
- ing-1, ing-2 and ing-3 to ing-5 can run during Phase 1. They only need fnd-1 and fnd-5 where noted.
- Lane G is the critical path and edits `ingest.py` and `cli.py` throughout: one agent at a time.

## Phase 3: legacy drives (spec `index`)

| Item | Task | Blocks on | Lane | Gate |
|---|---|---|---|---|
| P3.1 | idx-1 index volume (**done 2026-11-11**) | ing-9 | H | — |
| P3.2 | idx-2 baseline hash (**done 2026-11-11**) | idx-1 | H | — |
| P3.3 | idx-3 lrcat reader (**done 2026-11-11**) | idx-1 | I | schema unverified until idx-5 |
| P3.4 | idx-4 duplicate report (**done 2026-11-11**) | idx-2 | H | — |
| P3.5 | idx-5 Flats 1 run | idx-4 | H | **[user]** close Lightroom, run overnight hash |
| P3.6 | idx-6 PP 1 (the 4 TB) survey + index | idx-5 | H | **[user]** the 4 TB is plugged in (D-041); the user may run it alongside idx-5 |
| P3.7 | idx-7 consolidation plan **[stronger]** | idx-6 | H | **[user]** approve |
| P3.8 | idx-8 `--force-exit` quits Lightroom on command **[stronger]** | idx-3 | I | first real use is the user's |
| P3.9 | idx-10 `lrcat mark --was-mounted-as` for renamed drives **[stronger]** (D-042) | idx-3 | I | **[user]** approve the CLI change |
| P3.10 | idx-11 `hash` survives a failing drive **[stronger]** (D-044) (**done 2026-11-12**) | idx-2 | H | the PP 1 resume is the user's |
| P3.11 | idx-12 `hash` bounds one stuck read and stops on Ctrl-C at once **[stronger]** | idx-11 | H | **[user]** approve the timeout option |
| P3.12 | idx-13 drive usage: photography vs non-photo (schema V3 `volume_usage`) **[stronger]** (D-063) (**done 2026-11-18**) | idx-1 | H | approved 2026-11-18; the Big Boy Old re-index is the user's |

## Phase 4: sequences (spec `sequences`)

| Item | Task | Blocks on | Lane | Gate |
|---|---|---|---|---|
| P4.1 | seq-1 runs and bursts (**done 2026-11-11**, flat 150 s floor, D-023) | — | J | — |
| P4.2 | seq-2 classification | seq-1 | J | — |
| P4.3 | seq-3 preview luminance (adds Pillow) (**done 2026-11-11**) | — | K | — |
| P4.4 | seq-4 persist + astro layout **[stronger]** | seq-2, seq-3, ing-10 | J | **[user]** approve the naming-spec change |
| P4.5 | seq-5 relabel | seq-4 | J | — |

Note: seq-1 to seq-3 are pure and fixture-driven, so they make good parallel Haiku work during Phase 2.

## Phase 5a: local web UI and metadata completion (spec `ui`, D-045)

| Item | Task | Blocks on | Gate |
|---|---|---|---|
| P5a.1 | ui-1 catalog migration 2 + repo | — | approved in D-045 |
| P5a.2 | ui-2 XMP sidecar module | — | — |
| P5a.3 | ui-3 geo suggestions + `meta` CLI | ui-1, ui-2 | — |
| P5a.4 | ui-4 local server + JSON API | ui-3 | — |
| P5a.5 | ui-5 front end | ui-4 | **[user]** visual check |
| P5a.6 | card-1 Mac folder as a WORKING drive (D-047) (**done 2026-11-17**) | — | — |
| P5a.7 | card-2 card check and removability tiers (D-046, D-048) (**done 2026-11-17**) | ing-12 | — |
| P5a.8 | card-3 Cards and Cameras views (**done 2026-11-17**) | card-2 | **[user]** visual check |
| P5a.9 | ui-7 drive summary (**done 2026-11-17**) | ui-6 | **[user]** visual check |
| P5a.10 | ui-8 copies across drives (**done 2026-11-17**) | ui-7 | **[user]** visual check |
| P5a.11 | ui-9 ignore drives (**done 2026-11-17**) | ui-6 | **[user]** visual check |
| P5a.12 | ui-10 one filter bar for drives and cards (**done 2026-11-17**) | ui-8, card-3 | **[user]** visual check |
| P5a.13 | ui-11 progress of runs started outside the UI (**done 2026-11-18**) | ui-4 | **[user]** visual check |
| P5a.14 | ui-12 run one step, keep the Mac awake (**done 2026-11-18**) | ui-11 | **[user]** visual check |
| P5a.15 | ui-13 hash is not "done" while files wait (**done 2026-11-18**) | ui-12 | **[user]** visual check |
| P5a.16 | ui-14 home page: tasks by drive; sidebar grouped by connection (**done 2026-11-18**) | ui-11 | **[user]** visual check |
| P5a.17 | ui-15 NTFS and Synology drives: walk them, set them up read-only (**done 2026-11-18**) | ui-14 | **[user]** visual check |

## Phase 5b: card backup, drive health, volume names (spec `backup`, D-050..D-052)

| Item | Task | Blocks on | Gate |
|---|---|---|---|
| P5b.1 | bk-1 drive health check **[stronger]** | — | — |
| P5b.2 | bk-2 the 1 GB gate **[stronger]** | bk-1 | — |
| P5b.3 | bk-3 backup job, trailing an import **[stronger]** | ing-13 | — |
| P5b.4 | bk-4 card workflow in the web UI **[stronger]** | bk-2, bk-3 | **[user]** visual check |
| P5b.5 | bk-5 plug in and assign the 12 TB as MASTER | bk-1 | **[user]** |
| P5b.6 | bk-6 first card backup | ing-11, bk-4, bk-5 | **[user]** |
| P5b.7 | vn-1 volume name on hover | — | — |
| P5b.8 | vn-2 rename a volume **[stronger]** | vn-1 | — |
| P5b.9 | vn-3 Lightroom relink spike **[stronger]** | — | **[user]** approve |
| P5b.10 | vn-4 real rename + relink test | vn-2, vn-3 | **[user]**, fully backed-up drive only |

Notes: bk-3 and `ingest.py` share the job and resume code, so bk-3 waits for ing-13. vn-* is independent of bk-*.
rep-1 is replaced by bk-5; rep-0 still covers legacy content on the 12 TB.

## Phase 5c: lifetime metrics (spec `metrics`, D-054)

| Item | Task | Blocks on | Gate |
|---|---|---|---|
| P5c.1 | met-1 metrics store, recording, `metrics` CLI **[stronger]** (**done 2026-11-18**) | — | — |
| P5c.2 | met-2 metrics in the web UI | met-1, the uncommitted UI work committed | **[user]** visual check |
| P5c.3 | met-3 off-Mac copy (`metrics export`) | met-1 | **[user]** decision |
| P5c.4 | met-4 metrics feed drive health | met-1, bk-1 | — |

## Phase 5d: UI revamp (spec `revamp`, D-059)

| Item | Task | Blocks on | Gate |
|---|---|---|---|
| P5d.1 | rv-1 scaffold, shell, tasks and drive view at `/next` (**done 2026-11-18**) | ui-15 | **[user]** visual check |
| P5d.2 | rv-2..rv-5 cards, file lists, setup and ignore, settings (**done 2026-11-18**) | rv-1 | **[user]** visual check |
| P5d.3 | rv-6 the new UI becomes the default at `/`; classic UI removed (**done 2026-11-18**) | rv-2..rv-5 | **[user]** visual check |
| P5d.4 | rv-7 Photos timeline and editor, rebuilt and made to work (**done 2026-11-18**) | rv-6 | **[user]** visual check |
| P5d.5 | rv-8 clean up what the classic UI left behind (**done 2026-11-18**) | rv-6 | none |

## Phase 5: app (spec `app`)

| Item | Task | Blocks on | Gate |
|---|---|---|---|
| P5.0 | app-0 design study **[stronger]** | ing-11 | **[user]** approve; Path B re-evaluation |
| P5.x | app-1 to app-5 | app-0 | written by app-0 |

## Phase 6: master and dual copy (spec `replicate`)

| Item | Task | Blocks on | Gate |
|---|---|---|---|
| P6.0 | rep-0 design **[stronger]** | idx-7 | **[user]** approve |
| P6.1 | rep-1 assign 12 TB (moved to bk-5, D-050) | — | — |
| P6.x | rep-2 to rep-4 | rep-1 | written by rep-0 |
| P6.5 | rep-5 dual-copy policy | dedupe done | **[user]** (D-003) |

## Phase 4b: readiness (spec `readiness`, all [stronger])

| Item | Task | Blocks on | Gate |
|---|---|---|---|
| P4.6 | eval-1 sequencing approaches **[stronger]** (**done 2026-11-11**; recommendation in `docs/reviews/2026-11-11-eval-1-sequencing.md`) | seq-1 | **[user]** approve the chosen approach |
| P4.7 | live-1 live-device harness **[stronger]** (**done 2026-11-11**; first live survey run, D-028) | ing-3, fnd-4, seq-1 | **[user]** approve the vault-rules 2 change first |
| P6.G | gap-1 master gap analysis **[stronger]** | idx-4, live-1 | **[user]** review the verdict before any merge |

## Phase 7: backlog

`docs/specs/backlog.md`: re-ordered numbering, LLM enrichment, offline geocoding, phone imports, a Windows
companion.
