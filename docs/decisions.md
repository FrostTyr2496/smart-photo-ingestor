# Decision log

Every decision that changes what gets built, or how, gets one row here. That includes decisions made only in
chat: if it isn't in this file, the next session won't know about it.

Rules for agents:
- Append rows at the bottom. Never edit or delete an old row. To reverse a decision, add a new row that
  names the old ID.
- **By** is `user` only when the user said it in a session. A choice an agent made because the docs didn't
  cover it is `agent`, even if the user later saw it without objecting. Never record an approval the user
  didn't give.
- **Applied to** lists the docs or code that carry the decision.
- Copy the newest 5 rows into "Recent decisions" in `START_HERE.md`.
- This repo is public: no serials, UUIDs, locations or other personal details in rows.

| ID | Date | Decision | By | Applied to |
|---|---|---|---|---|
| D-001 | 2026-11-11 | Path A: a headless Python engine (`photovault` CLI, JSON-lines) with a SQLite catalog as the contract, and a thin SwiftUI menu-bar shell. Pure Swift (Path B) is re-evaluated at app-0. The user asked for the task list on this basis; Path A itself is the audit's recommendation. | agent | audit §3, `system-design.md`, `phase-roadmap.md` |
| D-002 | 2026-11-11 | The working SSD and the 12 TB master stay exFAT so they can be reused on Windows. Consequences: Windows-safe names, case-insensitive uniqueness, no xattrs, no trust in mtime equality or `F_FULLFSYNC`. | user | `vault-rules.md` 8, system-design §6, ing-2, ing-5 |
| D-003 | 2026-11-11 | Safe-to-format = one verified copy on WORKING for now. Dual copy (WORKING + MASTER) comes after the full data dedupe. Policy is config, not code. | user | system-design §9, fnd-1, ing-8, rep-5 |
| D-004 | 2026-11-11 | Renaming files is OK. Keep the camera's original number visible as an `M`-prefixed token (it is useful in the Lightroom grid). A re-ordered per-shoot sequence for that number is a later feature. | user | system-design §6, backlog |
| D-005 | 2026-11-11 | Default stem template `M{num:04d}_{date}-{time}-{subsec}_{body}_{ident}` (e.g. `M0781_20261027-181442-81_Z6_0084022.NEF`); `ident` = 7-digit shutter count, or the original stem when none exists. This is the agent's reading of D-004; to be confirmed by the user. | agent | system-design §6, fnd-1, ing-5 |
| D-006 | 2026-11-11 | The 4 TB SSD is not available now; it is plugged in and indexed in Phase 3. | user | idx-6 |
| D-007 | 2026-11-11 | The v0 CLI is archived on branch `archive/v0-cli-2026-11-11` (local, not pushed); `main` was reset to a clean scaffold for the new direction. The mimir process was ported: `START_HERE.md`, decisions log, phase roadmap, specs, `/close-the-loop`. | user | `START_HERE.md`, `docs/steering/task-execution.md` |
| D-008 | 2026-11-11 | Engine package `photovault`, managed by uv on Python ≥ 3.12; TOML config via stdlib `tomllib` (no pydantic). Approved deps: PyExifTool (fnd-5), Pillow (seq-3), pytest. | agent | `pyproject.toml`, `vault-rules.md` 11 |
| D-009 | 2026-11-11 | No attribution trailers on commits or PRs, as in mimir (D-078 there); enforced with `attribution` in `.claude/settings.json`. | agent | `.claude/settings.json`, task-execution §4 |
| D-010 | 2026-11-11 | Hooks: a PreToolUse guard blocks agent writes, moves, deletes and engine runs on `/Volumes` (real-drive runs are [user] steps), and a SessionStart hook prints the pre-flight. | agent | `.claude/hooks/`, `tests/test_guard_volumes.py` |
| D-011 | 2026-11-11 | The repo is public, so real-drive facts live in gitignored `docs/private/` (including `STORAGE_LAYOUT.md`) and sample photos in gitignored `testdata/`. Committed fixtures are redacted: serials → aliases, dates shifted, no paths. | agent | `.gitignore`, `vault-rules.md` 10, `tests/fixtures/meta/` |
| D-012 | 2026-11-11 | Perceptual hashing and LLM calls are removed from the ingest and dedupe path. LLM enrichment is backlog; pixel access is limited to preview luminance (seq-3). | agent | `vault-rules.md` 9, backlog |
| D-013 | 2026-11-11 | LEGACY volumes (Flats 1, the 4 TB) are identified by DiskArbitration UUID and never get a marker file, so nothing is ever written to them. | agent | system-design §7, idx-5 |
