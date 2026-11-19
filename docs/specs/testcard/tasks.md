# Spec: testcard: try a real card safely (D-111)

Goal: the user marks one card as a "test card" so its files show red (only on the card) although the catalog holds
verified copies on chosen drives. Importing then moves them to yellow and green. Copies on the hidden drives are
treated as nonexistent, for that card's files only, wherever backup state is judged. It only errs toward caution.

Rules: no schema change (state lives in the `settings` row `test_card`); marking writes the catalog only, never a
drive; a CARD cannot be hidden; vault-rules apply.

- [x] 1. **[stronger]** Test card mode
  - ID: tc-1 (D-111)
  - `photovault/testcard.py`: `load`, `mark`, `clear`, `resolve`, `hidden_for_card`, `hidden_for_asset`, `sql_hide`.
    Applied in: `cards._catalog_rows` (tiers), `card_clear._check_file` (pre-delete recheck; plan uses tiers),
    `ingest` (run-time skip via the new optional `repo.verified_counts_by_role(..., ignore_volumes=)`, plan-time
    `on_target`), `targets.recommend` / `_already_there` / `card_unbacked_bytes` (a hidden drive is never a target, so
    make-green and the Import dialog skip it), `actions.card_unbacked`. CLI `test-card mark <card> --hide <drive>...`,
    `test-card show`, `test-card clear`. The cards payload carries `test_card`; Cards shows a "Test" badge and a
    line naming the hidden drives.
  - Verify: `uv run pytest tests/test_testcard.py -q`, then `uv run pytest -q`; web typecheck, test, build.
  - Result (2026-11-19): tests/test_testcard.py -> 11 passed; `uv run pytest -q` -> 785 passed, 4 skipped; web
    typecheck, 171 tests, build done. Not seen in a browser. Per-file hint in the inspector left out (the card
    page line and badge only). Only the card-level hidden list is shown; an unhashed card file hides every
    candidate on the hidden drives.
