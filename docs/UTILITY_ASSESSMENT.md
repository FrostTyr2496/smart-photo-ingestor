# Does PhotoVault have real utility yet? (assessment, 2026-11-11)

Written for future agents and for the user, to help map out tasks. It is a snapshot: re-check the "Status"
section against `docs/phase-roadmap.md` before relying on it. Exact numbers from the user's drives are in
`docs/private/LIVE_SURVEY.md` (local only); this file keeps rounded figures and no identifiers (vault-rules 10).

## Verdict

**Not yet, for the user's real workflow.** Everything built so far is a set of verified parts. None of them can
be run by the user to import a card, verify a copy or answer "is this card safe to format?". The parts do work
on real data, which is the useful news: the first live run (live-1, read-only, 4 drives, about 3 minutes)
read every file without an error that points at our code.

## Status: what exists and what is missing

| Capability | State | Why it matters |
|---|---|---|
| Config, catalog DB, repository, volume probe | built, fixture-tested | foundation |
| Scanner, hashing, metadata reader | built; **now proven on real drives** | the inputs to everything |
| Run and burst detection (flat 150 s floor) | built; runs on real data, **not validated against ground truth** | sequencing (eval-1 decides) |
| Preview luminance | built, fixture-tested | Phase 4 flats/darks |
| Live harness and survey (live-1) | built; first real run done | lets agents test on live drives read-only |
| **CLI (`fnd-6`)** | **missing** | nothing is user-runnable without it |
| **Copy engine (`ing-2`)** | **missing** | the only media writer; no import without it |
| Groups, naming, plan, run/resume, card status (`ing-4` to `ing-8`) | missing | the ingest pipeline and the "safe to format" answer |
| Indexing, baseline hash, duplicate report (`idx-1` to `idx-4`) | missing | the "what do I have, and where" answer |
| macOS app | not started (Phase 5) | |

The critical path to the **first thing the user can use** is `fnd-6` → `ing-2` → `ing-4` → `ing-5` → `ing-6` →
`ing-7` → `ing-8` → `ing-9`, then `ing-11` (real card to the working SSD). The first useful read-only command
could come much earlier: `idx-1` (index a volume) plus `idx-4` (duplicate report) would already answer
"which of my drives hold the same files" without any copy engine. Consider pulling those forward.

## What the first live run showed (rounded)

Sources: two exFAT cards (about 3,000 and 1,200 raw files), a nearly empty exFAT working SSD, and a 98 %-full
APFS legacy drive (about 17,000 raw files, 650 GB of raw, 290 GB of video).

- **The scanner is fast.** About 27,000 files in under a second per drive. No path collisions under
  case-insensitive comparison on any drive.
- **The metadata reader holds up.** Zero errors on the cards and the working SSD, one unreadable file out of
  17,000 on the legacy drive. Throughput ranged from about 45 files/s (a slow card reader) to about 195 files/s.
  Reading the whole legacy drive took about two minutes.
- **Field coverage is high.** Model, serial, shutter count, capture time and exposure were present on every
  readable camera file except drone DNGs, which have no shutter count (and carry GPS; cameras on the other
  files did not). Capture times came from EXIF almost everywhere. Anything that has no shutter count can only
  be identified by hash: plan for that in dedupe and sequencing.
- **One card was used in two bodies**, so a card can hold files from more than one camera. Anything that
  assumes one body per card is wrong.
- **Duplicates are real.** On the legacy drive about one raw frame in six is a second copy of another frame
  (same body, shutter count and capture time). The dedupe report (`idx-4`) is not a nice-to-have.
- **Raw+JPEG pairing by stem is only about half of the legacy drive's stems**, versus nearly all on cards.
  `ing-4` (groups) must not assume every raw has a JPEG, or that pairs share a folder.
- **Run detection finds plausible runs on real data.** The cards gave 2 to 3 runs each, the legacy drive 24
  (the longest near 200 frames, with in-run gaps up to about 195 s for long exposures). Bursts of three or more
  frames in one second are common (hundreds on a card, about 1,900 on the legacy drive, up to 9 frames). Nobody
  has checked the run boundaries against what the user remembers shooting. That check is the real test.
- **Read speed:** whole-file hashing measured roughly 30 to 400 MB/s depending on the device (a three-file
  probe, so rough). At those rates, hashing the 650 GB of raw on the legacy drive is on the order of an hour
  or more, and one slow card could take about 20 minutes. This sizes `idx-5` and `ing-2`.
- **Cards have no marker and a volume UUID**, as designed (`system-design` §7). The probe read all four drives.

## What is still unproven

1. Hashing a whole drive, and the catalog holding 27,000 assets. No real data has gone into a catalog yet.
2. Cross-drive matching: whether a card's frames are already on the legacy drive. The `(serial, shutter count)`
   pair would narrow candidates cheaply from the metadata we already read, but only a full hash decides
   (vault-rules 5).
3. Run boundaries against the user's memory (eval-1), and bracket cases (3 or 5 frames).
4. The copy engine on exFAT, including `F_FULLFSYNC` behaviour and the uncached re-read.
5. Lightroom coexistence: a stale `.lrcat.lock` was present on the legacy drive while no Lightroom process was
   running. A lock file is therefore not proof that a catalog is open. `idx-3` should check for a live process
   as well as the file (vault-rules 7).

## Candidate tasks for the planner (proposals, not yet in any spec)

Each needs the user's approval before it goes into a `tasks.md`.

- **Pull `idx-1` + `idx-4` forward as the first user-runnable milestone.** A read-only "what is where" report
  on real drives, before the copy engine exists.
- **live-2, overlap estimate:** from the metadata a survey already reads, estimate how many frames of each card
  also appear on the working SSD or legacy drive by `(body, shutter count, capture time)`, as a cheap preview of
  the dedupe report. Output is an estimate, never a skip decision.
- **Handle frames with no shutter count** (drone DNGs, some video) in `ing-4` and in sequencing.
- **Multi-body cards:** a test and a rule in `ing-4`/`ing-5` for one card holding several bodies.
- **Resolve the Z 8 note:** `D-018` says no Z 8 is owned, but the legacy drive holds about 1,000 raw files from
  a Z 8. Ask the user whether to restore Z 8 to the corpus wish list.
- **Slow-reader awareness:** the copy engine's progress and time estimates should use measured throughput.
- **eval-1 inputs:** the survey's run lists are real data for eval-1. A follow-up could export per-run frame
  timings (redacted) so eval-1 can compare approaches on real runs without re-reading the drives.

## How to re-run the survey

`uv run pytest -m live -q -s tests/test_live_survey.py` with the drives plugged in. It reads the registry at
`docs/private/live_sources.toml` (copy `docs/live_sources.example.toml`), writes only
`docs/private/LIVE_SURVEY.md`, and never writes to a source (vault-rules 2, D-028).
