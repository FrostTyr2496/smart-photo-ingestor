# Spec: sequences (Phase 4) — astro, bursts, brackets, pano/hyperlapse

Goal: group frames into sequences and shoots from metadata, so night sessions stay together and stacking prep
is easy. Labels never decide whether a byte is kept.

Calibrate to the real data (from the survey; see the redacted `tests/fixtures/meta/cards.jsonl.gz`):
- night runs are short (5–19 frames), with irregular gaps (11–45 s) and mixed exposures;
- framing shots interrupt them;
- no calibration frames exist yet;
- DJI pano/hyperlapse frames arrive in their own folders.

Rules:
- `docs/steering/task-execution.md` applies.
- Commit as `feat(seq-N): …  [seq-N]`.

- [ ] 1. Run and burst detection (pure functions)
  - ID: seq-1
  - Create `photovault/sequences.py`:
    - `FrameMeta(body, shutter, dto: datetime, subsec, exposure_s, iso, fnumber, focal_mm, file, folder)`.
    - `load_fixture(path) -> list[FrameMeta]` (NEF rows only).
    - `detect_runs(frames, *, min_frames=5, min_exposure_s=1.0, max_outliers=1) -> list[Run]`.
    - `detect_bursts(frames, *, min_frames=3) -> list[Burst]`.
  - A run is a stretch of frames from one body, sorted by shutter count, with consecutive counts.
    - Each frame has `exposure ≥ min_exposure_s`.
    - The gap between frames is ≤ `exposure + max(60 s, exposure)`.
    - A single out-of-rule frame (for example a 0.5 s framing shot) is tolerated when the run resumes on the
      next frame. It is excluded from the run and listed in `Run.interruptions`.
  - A burst is ≥ `min_frames` frames from one body sharing a `dto` second.
  - Create `tests/test_sequences.py` using the fixture:
    - A run on SN-A contains shutter counts 17396–17414, with the 17415 framing shot as an interruption.
    - Every survey run of ≥ 5 frames is found (CARD1: 14052–14056, 17396–17414, 17423–17427; CARD2:
      17372–17377, 17384–17393).
    - NEF same-second groups of ≥ 3: **54 on CARD1 and 494 on CARD2**. These numbers come from the survey; if
      yours differ, investigate before changing the expectation.
  - Verify: `uv run pytest tests/test_sequences.py -q`
  - _Design:_ audit §5.4

- [ ] 2. Metadata-only classification
  - ID: seq-2
  - Blocks on: seq-1
  - Add `classify_run(run, neighbours) -> tuple[str, float]` (kind, confidence), following the audit §5.4
    table, metadata columns only.
    - Kinds: `light`, `foreground`, `bracket`, `burst`, `timelapse`, `dark_candidate`, `bias_candidate`,
      `flat_candidate`, `unknown`.
    - `*_candidate` kinds need seq-3 confirmation.
    - Also add `folder_kind(folder_hint) -> str | None`: `PANORAMA` → `pano`, `HYPERLAPSE` → `timelapse`.
  - Tests:
    - synthetic runs for each kind;
    - on the fixture, the 2026-10 Z6II night runs classify as `light`;
    - the 37-frame 1/4 s ISO 6400 block on CARD1 is not `light` (exposure < 1 s) and comes out as
      `burst`/`unknown`, not a calibration kind without seq-3.
  - Verify: `uv run pytest tests/test_sequences.py -q`

- [ ] 3. Preview luminance (adds Pillow)
  - ID: seq-3
  - Approves the dependency: run `uv add Pillow`.
  - Create `photovault/preview.py`:
    - `luma_of_jpeg(data: bytes) -> float`: mean L of a 64-px thumbnail, from 0 to 255.
    - `extract_preview(path) -> bytes | None`: `exiftool -b -PreviewImage`, then `-JpgFromRaw` as fallback,
      read-only.
    - `preview_luma(path) -> float | None`.
  - A candidate is confirmed as dark/bias when luma < 8, and as flat when luma > 100 with low variance.
  - Tests: synthetic black, white and gradient JPEGs made with Pillow; a `realfiles` test on corpus NEFs.
  - Verify: `uv run pytest tests/test_preview.py -q`

- [ ] 4. **[stronger]** Persist sequences and shoots; astro layout at plan time
  - ID: seq-4
  - Blocks on: seq-2, seq-3, ing-10
  - During `plan_card_ingest`:
    - detect sessions (> 90 min gap per body → a new session);
    - write `shoots` and `sequences`, and set `capture_groups.sequence_id` and `shoot_id`;
    - with `--astro-layout`, put the members of light, dark, flat, bias and foreground sequences under
      `<body>/{lights,darks,flats,bias,foreground}/`.
  - Labels and layout apply only before execution (planned jobs). Copied files are never moved.
  - Update system-design §4 and §6 with the layout rule (needs the user's approval: it changes the naming spec).
  - Verify: `uv run pytest tests/test_ingest_plan.py tests/test_sequences.py -q`

- [ ] 5. Relabel before execution
  - ID: seq-5
  - Blocks on: seq-4
  - Add `photovault sequence list <job_id>` and `photovault sequence relabel <sequence_id> --kind K`.
    - Sets `kind_source=user`. The label is sticky: auto-detection never overrides it.
    - While the job is still `planned`, recompute the affected `job_items.dst_rel`.
    - Refuse when the job is not `planned`.
  - Verify: `uv run pytest tests/test_cli.py -q`
