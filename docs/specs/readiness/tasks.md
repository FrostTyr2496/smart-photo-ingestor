# Spec: readiness (sequencing evaluation, live-device tests, master gap analysis)

Three open tasks, all **[stronger]** (Opus or Sonnet). They judge sequencing approaches, data-safety gaps and
test design, so a fast model should not run them. Nothing here changes a signature in `system-design.md` §3,
the schema, the naming spec or `vault-rules.md` without the user's approval first.

Rules:
- `docs/steering/task-execution.md` applies. Commit as `feat(<id>): …  [<id>]`.
- Real-drive facts go in `docs/private/` (gitignored). Real photos stay in `testdata/` (gitignored).
- Agents only READ `/Volumes`. Real-drive runs are **[user]** steps: print the command, don't run it.

- [ ] 1. **[stronger]** Evaluate sequencing approaches (bracket and burst cases)
  - ID: eval-1
  - Blocks on: seq-1
  - Context: the flat 150 s gap floor (D-023) is the baseline. Real shooting breaks it in two ways:
    - **Brackets.** Some sets are 3 frames (±1 EV), others 5 (±2 EV), and they can sit inside a longer run.
    - **Bursts.** Some high-speed bursts are long strings that the user wants kept as one sequence, and they
      may run into the next sequence with no gap.
  - Compare at least these approaches on the same synthetic cases plus the real survey data:
    1. Flat floor (seq-1, the baseline).
    2. Exposure-aware segmentation: split where the exposure pattern repeats or steps (EV steps of 1 or 2).
    3. Burst-aware chaining: join frames that share a `dto` second or a burst signature, then cut between them.
    4. Search-based segmentation: dynamic programming or beam search over split points, minimising a cost
       (gap, exposure step, same-second, folder hint). This is the adversarial option: it asks which split
       of the sequence the data least contradicts.
  - Create `tests/test_sequence_eval.py` with synthetic cases for: 3-frame and 5-frame brackets, a long burst
    string with no gap, and a bracket followed directly by a burst. Keep the real-survey numbers in
    `docs/private/SEQUENCE_EVAL.md` (local only), with only a redacted summary in the task Result.
  - Dependencies: no new library. If a search method needs one, stop and ask (vault-rules 11).
  - Do not change the default of `detect_runs` or any §3 signature. Output is a recommendation with a
    decision proposal, for the user to approve.
  - Verify: `uv run pytest tests/test_sequence_eval.py -q`
  - _Design:_ audit §5.4; seq-1; D-023

- [x] 2. **[stronger]** Live-device test harness (read-only, no local copies needed)
  - ID: live-1
  - Blocks on: ing-3, fnd-4, seq-1
  - Goal: tests can read the live files of plugged-in cards and drives in place, so the local corpus stays
    small. Big files are pulled into `testdata/milestones/` only for milestone checks.
  - Create:
    - `photovault/live_harness.py`: reads the source registry and yields `scan.walk` entries for each source.
      Opens files `"rb"` only.
    - `docs/live_sources.example.toml` (committed, placeholder names only; it cannot live in `docs/private/`,
      which is gitignored) and the real `docs/private/live_sources.toml` (gitignored): `name → mount path`,
      with a `role`.
    - `@pytest.mark.live` (registered in `pyproject.toml`) and `tests/conftest.py`: live tests run only with
      `-m live`, and the `live_sources`/`live_source` fixtures skip when no registry is present or the mount
      path is absent.
    - `photovault/live_survey.py` and `tests/test_live_survey.py` (added 2026-11-11, D-029): a read-only survey
      (scan, metadata, runs, bursts, a three-file hash throughput probe) with a `live` test that writes
      `docs/private/LIVE_SURVEY.md`; its unit tests use `tmp_path`.
    - `tests/test_live_harness.py`: uses only `tmp_path` and a fake registry. It checks the skip paths and that
      the harness never opens a file for writing.
  - **Blocker:** `vault-rules` 2 says tests never touch `/Volumes`, and the guard hook blocks engine runs there.
    A live test needs that rule relaxed to "read-only on `/Volumes`, `live` marker only". Stop and propose
    that change to the user before writing any test that touches a real mount. Until then, build the harness
    and its `tmp_path` tests only.
  - Milestone pulls (`testdata/milestones/`) are copies. Make them a **[user]** command, not an agent step.
  - Verify: `uv run pytest tests/test_live_harness.py -q` (tmp_path only).
  - Result (2026-11-11): `uv run pytest tests/test_live_harness.py -q` → 25 passed; `uv run pytest -q` → 220 passed.
    Built the harness and its `tmp_path` tests only. Left out of scope until the user approves the vault-rules 2
    change: any `@pytest.mark.live` test that reads a real mount, and the real `live_sources.toml`.
    Milestone pulls stay a [user] command (none written).
  - Result, live run (2026-11-11, after the user approved the vault-rules 2 exception, D-028):
    `uv run pytest -m live -q -s tests/test_live_survey.py` → 1 passed in 178 s on 4 mounted drives (2 cards, the
    working SSD, Flats 1). Redacted summary in `docs/UTILITY_ASSESSMENT.md`; exact numbers in
    `docs/private/LIVE_SURVEY.md`. Nothing was written to any drive.
  - [user] after approval: `uv run pytest -m live -q` with the drives plugged in.
  - _Design:_ vault-rules 2, 10; system-design §8 (scan)

- [ ] 3. **[stronger]** Gap analysis before merging devices into the 12 TB master
  - ID: gap-1
  - Blocks on: idx-4, live-1
  - Feeds: rep-0 (master design). This task answers "is it safe to merge yet?"
  - Produce `docs/private/MASTER_GAP.md` (local) with one section per device and a verdict, ready or not ready,
    with reasons. Check each device for:
    - every asset has a `verified` instance on a non-CARD volume (vault-rules 5);
    - no unique assets left unindexed on LEGACY (Flats 1, the 4 TB);
    - free space on the 12 TB against each device's bytes, after the dedupe report;
    - filename collisions under the naming rules, case-insensitive (vault-rules 8);
    - Lightroom catalog references that a move would break (vault-rules 7);
    - the dual-copy policy (D-003) and what is still missing.
  - End with a list of **[user]** steps in order, and nothing run on `/Volumes`.
  - Create `tests/test_gap_report.py` with synthetic catalogs in `tmp_path`, covering a ready device, a device
    with an unverified asset and a device with a name collision.
  - Verify: `uv run pytest tests/test_gap_report.py -q`
  - _Design:_ system-design §5, §9 (policy); vault-rules 5, 7, 8; audit (replicate)
