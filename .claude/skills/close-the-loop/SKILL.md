---
name: close-the-loop
description: PhotoVault end-of-task state check. Confirms the handoff docs match the work before a task commit, so the next (often Haiku) session can pick it up cold. Ported from mimir's close-the-loop.
---

End-of-task state check for PhotoVault. If you changed no files this session, say so and stop. Otherwise confirm each item and fix whatever is missing:

1. `START_HERE.md`: "In progress" is `none` if the work is committed, or describes exactly what is half-done. "Now", "Next", "Waiting on user" and "Recent decisions" are true and within the caps at the top of the file.
2. The task's `docs/specs/<spec>/tasks.md` entry is ticked and has a `Result (YYYY-MM-DD): <command> → <outcome>` line, plus anything deliberately left out of scope.
3. `docs/decisions.md` has a row for every decision the user made this session (`user`) and every choice you made that the docs did not cover (`agent`).
4. `docs/phase-roadmap.md` and `docs/steering/system-design.md` still match reality (task status, module map, schema, signatures) if this session changed them.
5. Privacy (this repo is PUBLIC): `git diff --cached --stat` adds nothing under `testdata/` or `docs/private/`, and no staged text contains camera serial numbers, volume UUIDs, GPS coordinates, real trip/location names, or real photos. `git check-ignore -v testdata docs/private` reports both ignored.
6. `uv run pytest -q` passes (the count is above zero), and `git status --short` is empty after the commit or every remaining change is named under "In progress".
7. The commit message matches `docs/steering/task-execution.md` §4.3 (D-019): subject `type(<task-id>): summary  [<task-id>]`; a 1–4 line body that points to the spec path and decision IDs, names anything still open, and has no file lists, no copied `Result` line and no attribution trailer.

Report one line per item (ok / fixed / not applicable). Then stop. Don't start new work.
