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
| P1.1 | fnd-1 config | — | A | — |
| P1.2 | fnd-2 catalog db + migrations | — | B | — |
| P1.3 | fnd-3 repository | fnd-2 | B | — |
| P1.4 | fnd-4 volumes | fnd-3 | B | — |
| P1.5 | fnd-5 metadata reader (adds PyExifTool) | — | C | — |
| P1.6 | fnd-6 protocol + CLI skeleton | fnd-1, fnd-4 | A | — |
| P1.7 | fnd-7 sample corpus | — (agent part after fnd-5) | D | **[user]** copy the sample files |

Notes:
- Lanes A, B and C can start at once.
- Shared files: `pyproject.toml` (fnd-5 adds a dependency, fnd-6 adds the script entry) and `cli.py` (fnd-6
  onward). Serialize those edits.

## Phase 2: verified card ingest (spec `ingest`)

Goal: plug in a card, copy it with each byte read once and checked, and get a trustworthy card-status.
Policy: one verified copy on WORKING (D-003).

| Item | Task | Blocks on | Lane | Gate |
|---|---|---|---|---|
| P2.1 | ing-1 hashing | — | E | — |
| P2.2 | ing-2 copy engine | ing-1 | E | — |
| P2.3 | ing-3 scanner | — | F | — |
| P2.4 | ing-4 groups | ing-3, fnd-5 | F | — |
| P2.5 | ing-5 naming | ing-4, fnd-1 | F | — |
| P2.6 | ing-6 plan | fnd-3, fnd-4, fnd-5, fnd-6, ing-3, ing-4, ing-5 | G | — |
| P2.7 | ing-7 run/resume **[stronger]** | ing-2, ing-6 | G | — |
| P2.8 | ing-8 card status | ing-7 | G | — |
| P2.9 | ing-9 CLI wiring | ing-8 | G | — |
| P2.10 | ing-10 fault suite **[stronger]** | ing-9 | G | — |
| P2.11 | ing-11 acceptance | ing-10 | G | **[user]** real card → X9 Pro |

Notes:
- ing-1, ing-2 and ing-3 to ing-5 can run during Phase 1. They only need fnd-1 and fnd-5 where noted.
- Lane G is the critical path and edits `ingest.py` and `cli.py` throughout: one agent at a time.

## Phase 3: legacy drives (spec `index`)

| Item | Task | Blocks on | Lane | Gate |
|---|---|---|---|---|
| P3.1 | idx-1 index volume | ing-9 | H | — |
| P3.2 | idx-2 baseline hash | idx-1 | H | — |
| P3.3 | idx-3 lrcat reader | idx-1 | I | schema unverified until idx-5 |
| P3.4 | idx-4 duplicate report | idx-2 | H | — |
| P3.5 | idx-5 Flats 1 run | idx-4 | H | **[user]** close Lightroom, run overnight hash |
| P3.6 | idx-6 4 TB survey + index | idx-5 | H | **[user]** plug in the 4 TB (D-006) |
| P3.7 | idx-7 consolidation plan **[stronger]** | idx-6 | H | **[user]** approve |

## Phase 4: sequences (spec `sequences`)

| Item | Task | Blocks on | Lane | Gate |
|---|---|---|---|---|
| P4.1 | seq-1 runs and bursts | — (can start any time) | J | — |
| P4.2 | seq-2 classification | seq-1 | J | — |
| P4.3 | seq-3 preview luminance (adds Pillow) | — | K | — |
| P4.4 | seq-4 persist + astro layout **[stronger]** | seq-2, seq-3, ing-10 | J | **[user]** approve the naming-spec change |
| P4.5 | seq-5 relabel | seq-4 | J | — |

Note: seq-1 to seq-3 are pure and fixture-driven, so they make good parallel Haiku work during Phase 2.

## Phase 5: app (spec `app`)

| Item | Task | Blocks on | Gate |
|---|---|---|---|
| P5.0 | app-0 design study **[stronger]** | ing-11 | **[user]** approve; Path B re-evaluation |
| P5.x | app-1 to app-5 | app-0 | written by app-0 |

## Phase 6: master and dual copy (spec `replicate`)

| Item | Task | Blocks on | Gate |
|---|---|---|---|
| P6.0 | rep-0 design **[stronger]** | idx-7 | **[user]** approve |
| P6.1 | rep-1 assign 12 TB | rep-0 | **[user]** |
| P6.x | rep-2 to rep-4 | rep-1 | written by rep-0 |
| P6.5 | rep-5 dual-copy policy | dedupe done | **[user]** (D-003) |

## Phase 7: backlog

`docs/specs/backlog.md`: re-ordered numbering, LLM enrichment, offline geocoding, phone imports, a Windows
companion.
