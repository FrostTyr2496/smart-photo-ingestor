# Task execution guide

Applies to every task in `docs/specs/*/tasks.md`. This was adapted from mimir's guide. It covers process only:
the hard rules are in `vault-rules.md` and the system map is in `system-design.md`.

## 1. Before you start
1. **Pre-flight.** The SessionStart hook prints the branch, `git status --short`, `git log -5`, the "In progress"
   block of `START_HERE.md` and the mounted volumes. A previous session was interrupted if "In progress" names
   a task, or if the tree has changes that no doc explains. In that case:
   - Review those files against the task's spec before building on them.
   - Finish or fix them; never discard them.
   - Say in the Result line that you resumed.
2. Take the **next unticked task** whose `Blocks on:` tasks are ticked (`docs/phase-roadmap.md`). The checkbox
   in `tasks.md` is the source of truth for status.
3. Stop and tell the user if any of these apply:
   - the task is **[user]**;
   - it is **[stronger]** and you are a fast model (Haiku);
   - it needs an unapproved dependency;
   - its inputs aren't done.
4. Read only what the task cites (`_Design:_` lines → grep the section, read that range). Use the read-only
   Explore agent for unfamiliar code. To port code from the old CLI, use
   `git show archive/v0-cli-2026-11-11:photo_ingest/<file>`.
5. Before your first edit, write the "In progress" block in `START_HERE.md` (format given there).

## 2. While implementing
- Create or edit only the files the task names. If another file is needed, add it to the task text and say
  why in the Result line.
- Make the smallest change that meets the acceptance criteria. Use the signatures in `system-design.md` §3
  exactly; later tasks depend on them.
- Write Python 3.12 with type hints on public functions. Handle errors at boundaries (disk, SQLite,
  subprocess) with a specific message. Don't swallow exceptions.
- Tests:
  - Make no network calls and never touch `/Volumes`.
  - Use `tmp_path` and set `PHOTOVAULT_HOME` to it.
  - Use `monkeypatch` for `diskutil` and `exiftool` unless the test is `@pytest.mark.realfiles`.
- Follow the privacy rule (vault-rules 10) in code, tests, fixtures, docs and commit messages.
- After each Verify run, update "Last verified" in the In-progress block.

## 3. Verifying
- Run exactly the task's `Verify:` command. Read the output. A task isn't done until the command passes and
  tests actually ran (count > 0).
- For **[user]** steps:
  - Give the user the exact commands and what to paste back.
  - Never invent, assume or backfill results.
  - Results that contain real-drive facts go in `docs/private/`; the task's Result line gets a redacted
    summary.

## 4. Finishing: commit and close the loop
1. Update in the same commit:
   - `tasks.md`: tick the box, then add `Result (YYYY-MM-DD): <command> → <outcome>` and anything left out of
     scope. Don't copy commit hashes there.
   - `system-design.md` if a name, threshold or signature differed from reality (§6 rules apply).
   - `docs/decisions.md`: one row per decision (`user` or `agent`).
   - `START_HERE.md`: set "In progress" to `none`, fix "Now", update "Next" and "Waiting on user", copy the
     newest 5 decisions, and respect the caps.
   - `docs/phase-roadmap.md`: the item's status.
2. Run `/close-the-loop`.
3. One commit per task: `feat(<task-id>): …  [<task-id>]` (`docs(…)` or `chore(…)` where it fits).
   - Stage specific paths with `git add <paths>`.
   - No attribution trailers (D-009).
   - Never use `--no-verify`, never amend after a hook failure, and don't push unless asked.
4. End with an empty `git status --short`, or describe what is left under "In progress".

## 5. When to stop and ask
- Two documents conflict, or a document conflicts with `vault-rules.md`.
- A signature, the schema, the naming spec or the protocol has to change. Propose the edit, get approval, then
  change the doc and the code together.
- A dependency, system package or real-drive run is needed.
- An acceptance criterion is ambiguous or can't be met as written.
- You'd have to touch a file outside the task's scope.

## 6. Choosing the executor
- Tasks without a marker are written for a **fast executor (Haiku)**: complete spec text, one Verify command.
- **[stronger]** tasks integrate modules or carry data-safety judgment. Use Opus or Sonnet, and check the diff
  against `vault-rules.md` before committing.
- **[user]** tasks need the user's hands, hardware or decision.

## 7. Writing and editing tasks
- Keep each spec to about 5–12 tasks. Each task names exact files and functions, has one `Verify:` command and
  a `_Design:_` pointer.
- Write tasks so a cheaper model can execute them without further decisions. If a decision is missing, add
  it to the text.
- Split any task that needs more than one verification command.
- IDs are stable: never renumber or reuse one.
