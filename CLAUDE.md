# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This repo builds PhotoVault: a Python engine (`photovault/`, a CLI that speaks JSON-lines) plus, later, a
SwiftUI menu-bar app. Together they import camera cards with verified copies and track every file across
drives in a SQLite catalog. The process is ported from the user's mimir project: a handoff file, specs with
task IDs, a decisions log, and `/close-the-loop`.

@START_HERE.md
@docs/steering/vault-rules.md

## When starting a task
- The SessionStart hook prints the pre-flight: branch, git status, In progress, mounted volumes. Follow
  `docs/steering/task-execution.md`.
- Pick the next unticked task from `docs/phase-roadmap.md`. The `tasks.md` checkbox is the source of truth.
  - **[stronger]** tasks need Opus or Sonnet: if you are Haiku, stop and say so.
  - **[user]** tasks need the user.
- Read only the parts of `docs/steering/system-design.md` the task cites. Use the Explore agent for read-only
  search.
- Port reference code from the old CLI with `git show archive/v0-cli-2026-11-11:photo_ingest/<file>`. Never
  restore it wholesale.

## Commands
- Setup: `uv sync`. Add an approved dependency with `uv add <pkg>`, only in the task that approves it.
- Tests: `uv run pytest <file> -q` for the task's Verify line, then `uv run pytest -q` once before committing.
  Real-photo tests run with `uv run pytest -m realfiles` and skip when `testdata/corpus` is absent.
- CLI (from fnd-6): `uv run photovault --json <command>`. Against real drives, it is the user who runs it.
- Commits: `feat(<task-id>): …  [<task-id>]`, one per task, staged with `git add <paths>`.
  - Don't push unless asked.
  - No attribution trailers (D-009). `.claude/settings.json` turns them off.
- Run `/close-the-loop` before each task commit.

## Never
- Write, move or delete anything under `/Volumes`, or run the engine against `/Volumes`. The guard hook
  blocks it: give the user the command instead.
- Commit camera serials, volume UUIDs, GPS, real trip or location names, or real photos. This repo is public.
  Use `docs/private/` and `testdata/`, which are gitignored.
- Add a dependency, change the catalog schema, the naming spec, the protocol or a signature in
  `system-design.md` §3, or edit `vault-rules.md`, without the user's approval. Propose the change instead.
