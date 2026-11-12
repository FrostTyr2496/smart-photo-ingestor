# eval-1: how to cut frames into sequences (recommendation, needs approval)

Redacted: no paths, trip names or serials. Real-data detail is in `docs/private/SEQUENCE_EVAL_FINDINGS.md`.

## Recommendation

1. **Keep `detect_runs` as it is** (D-023). It is the right rule for long-exposure night runs and should not
   become the general "sequence" rule.
2. **Use the camera's own flag for brackets.** Nikon files record `ShootingMode` (including "Exposure
   Bracketing") and `ExposureBracketValue`. Add both to the metadata reader (a small change to `metadata.py`
   and the fields in `system-design.md` §3; **needs your approval**). Fall back to the search segmenter below
   for files without the flag (other makers, or a body that does not write it).
3. **Use approach 4 (search-based segmentation) for everything else**: it cuts a body's frames into
   bracket / burst / run / single / other segments by dynamic programming over an explicit cost. It is in
   `photovault/sequence_eval.py` today as an evaluation harness. Turning it into the production segmenter is a
   new task (seq-2 builds on it, so seq-2 should wait for your answer).
4. **Reject approach 3 (burst chaining)**, and do not use approach 1 (flat floor) for non-run frames.

## What was compared

Four approaches, on 9 synthetic cases (3- and 5-frame brackets, a long burst with a buffer hiccup, a burst into
a burst with new settings, a bracket inside a night run, a bracket into a burst, whole-second timestamps) and on
14,864 real raw frames from the four registered drives:

| Approach | Synthetic cases exact (of 9) | Real: segments | Real: bracket recall vs camera flag |
|---|---:|---:|---:|
| 1 flat floor (baseline) | 1 | 275 (largest 1,368 frames) | 0.2 % |
| 2 exposure-aware | 7 | 2,619 | 83.6 % |
| 3 burst chaining | 6 | 5,606 (3,517 single frames) | 14.4 % |
| 4 search (DP) | **9** | 4,338 (largest 298) | **83.9 %** |

Bracket precision is 99.9 % for approaches 2 and 4. Half of all frames (51 %) are exposure-bracketed, which is
why brackets matter more than the original spec guessed. The misses are almost all single frames whose
sibling frames are gone (culled or never copied); the flag still identifies those, the heuristic cannot.

## Findings worth knowing

- The baseline cannot separate a bracket from a run, and chains whole days of daytime shooting (segments of
  over 1,000 frames). Its 150 s floor stays a night-run rule only.
- Burst chaining flips between "fast" and "slow" on real days, because frame intervals sit near its 1 s
  threshold, and shreds the data into single frames.
- "Continuous" drive mode is not a burst: a bracket fired in continuous mode carries the same flag. No metadata
  gives burst boundaries, so burst cuts are the least certain part. Approach 4 measures tempo by
  start-to-start interval (an earlier version used idle time and cut a real night run into four pieces).
- Approach 2's greedy bracket carve can start a bracket one frame early when the previous frame happens to fit
  the exposure ladder. Approach 4 does not, because it weighs the tempo break.
- Approach 4's weights are hand-set, not learned. Bracket precision and recall did not move across a wide range
  of weights, and the synthetic results held except at the extremes.
- Default bracket steps are 1 and 2 EV. The data also has 2/3-EV and 1/3-EV sets and a few 3- and 6-EV sets;
  widen `ev_steps` if you want those grouped.

## Decisions for you

1. Add `ShootingMode` and `ExposureBracketValue` to the metadata reader (approve a §3 field change)?
2. Make approach 4 the production segmenter (a new task between seq-1 and seq-2), with the flag taking priority
   for brackets?
3. Which bracket steps count: 1 and 2 EV only, or also 1/3 and 2/3?
4. Check run and burst boundaries against what you remember: the agent can list 10 to 20 real boundaries from
   `docs/private/SEQUENCE_EVAL.md` for you to confirm or reject.

## Reproduce

`uv run pytest tests/test_sequence_eval.py -q` (synthetic and redacted-fixture tests).
`uv run pytest -m live tests/test_sequence_eval.py` reads the registered drives read-only (about 8 minutes) and
writes local-only caches and `docs/private/SEQUENCE_EVAL.md`.
