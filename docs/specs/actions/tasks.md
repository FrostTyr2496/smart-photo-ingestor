# Spec: actions — a ranked list of what to do next, separate from Tasks

Source: the user's ask of 2026-11-18 (D-084). An example action: "photos on certain cards are not backed up at all:
find those cards, plug them in and back them up". **Tasks** shows jobs that run or ran. **Actions** says what *should*
happen next, ranked by risk to the photos.

Rules for every task:
- The engine reads the catalog only. It never runs anything, never touches a drive, and needs no new schema. Dismiss
  and snooze state lives in the `settings` table (key `actions_state`, JSON).
- An action that would touch a drive (back up a card, resume a hash, write sidecars) is a button that starts the
  existing job or flow through the existing API, and only when the user presses it. Commands are shown for the CLI too.
- Wording is plain and specific: names of drives, counts and sizes, never "some files".
- Example names in tests are invented.

## Contract: `GET /api/actions`

```json
{
  "generated_at": "2026-11-18T22:00:00+00:00",
  "counts": {"critical": 1, "high": 2, "normal": 4, "low": 3, "snoozed": 1, "dismissed": 0},
  "actions": [
    {"id": "card-unbacked:<volume_id>", "kind": "card_unbacked", "severity": "critical",
     "title": "Back up card A: 412 photos (18.2 GB) exist only on this card",
     "why": ["412 of 1,030 files on card A have no verified copy on a working or master drive",
             "Last seen 3 days ago"],
     "needs": [{"volume_id": "…", "label": "Card A", "role": "CARD", "mounted": false}],
     "steps": ["Plug in Card A", "Back it up to SMALL 1TB"],
     "do": {"label": "Back up", "kind": "ui", "href": "#/cards/<volume_id>"},
     "cli": "uv run photovault --json ingest \"/Volumes/<card>\" --dry-run",
     "evidence": {"files": 412, "bytes": 19541000000},
     "snoozed_until": null}
  ]
}
```

- `id` is stable for the same subject, so dismiss and snooze survive.
- `severity` is one of critical | high | normal | low. Sort by severity, then by bytes at risk, then by age.
- `do.kind` is one of `ui` (a link to the page that does it), `run` (`{endpoint, body}`: a POST to an existing
  task-starting endpoint, shown with a confirm), or `none` (a manual step).
- `POST /api/actions/<id>/dismiss`, `POST /api/actions/<id>/snooze {days}`, `POST /api/actions/<id>/restore`.

## Rules (the first set)

Data at risk (critical / high):
1. **card_unbacked** (critical): a CARD volume with files whose asset has no `verified` instance on a WORKING or MASTER
   volume (D-003). Give counts and bytes, and the working drive to back up to.
2. **card_unindexed** (high): a CARD volume seen but never indexed, or indexed but not hashed, so its backup state is
   unknown. "Plug in and check".
3. **single_copy** (high): assets with exactly one verified instance overall, grouped per volume. Raise it to critical
   when that volume has read errors (metrics) or is a LEGACY drive with I/O errors recorded.
4. **drive_errors** (high): a drive whose last job hit read errors (metrics, or job events), with the affected
   folder prefix. "Copy what's readable before it gets worse" points at idx-6.

Unfinished work (normal):
5. **job_paused**: paused, interrupted or failed jobs, with the drive and progress. `do` = Resume (`run`).
6. **hash_incomplete**: a drive whose instances are indexed but not hashed (share and bytes). Duplicates and copy
   checks need hashes.
7. **drive_stale**: a drive holding single-copy assets that hasn't been seen for more than 30 days. "Plug it in so its
   files can be checked."
8. **sidecars_dirty**: `group_meta.dirty` rows whose files sit on a WORKING drive (sidecars to write), with the
   count. `do` = the existing write-sidecars flow.

Housekeeping (low):
9. **metadata_stale**: groups with no `subsec` or no metadata row, per drive. "Run `meta scan --rescan`."
10. **locations_suggested**: groups with no location but a `high` or `medium` suggestion (sp-8), counted per day.
    `do` = Photos filtered to "Missing location" on the biggest day.
11. **shoots_review**: frames not in a shoot while a plan exists or can be made (sh-2). `do` = `#/review/shoots`.
12. **duplicates_reclaimable**: from the last duplicate report, if present (bytes reclaimable). It informs only;
    dd-0 is still a proposal.

## Tasks

- [ ] 1. **[stronger]** The actions engine and API
  - ID: act-1. `photovault/actions.py`: `compute(conn, *, now, mounted) -> list[dict]`, one function per rule, each
    cheap (aggregate SQL, no per-file loops in Python over 65k rows). Rules that need data the catalog lacks are
    skipped quietly. The API is as in the contract; results are cached for 30 s and refreshed when a job ends or a drive
    mounts.
  - Verify: `uv run pytest tests/test_actions.py tests/test_web_api.py -q -k actions`. Each rule gets a synthetic
    catalog that triggers it and one that doesn't, plus ordering and the dismiss/snooze round trip. Then a
    read-only run on the real catalog: counts per rule into `docs/private/`.

- [ ] 2. The Actions pane
  - ID: act-2. A nav item "Actions" with a badge (the count of critical + high), and a route `#/actions`, separate
    from Tasks.
    - Groups: "Photos at risk", "Unfinished", "Housekeeping".
    - Each card shows the title, the `why` lines, needed drives as chips (mounted or not, like rv-21), steps, the `do`
      button, the CLI line (with a copy button), and Dismiss / Snooze 7 days.
    - A card whose needed drive becomes mounted is highlighted ("Card A is connected — back it up now").
    - The Tasks page shows a one-line link "N actions need attention".
    - Built against fixture JSON in the contract format until act-1 lands.
  - Verify: `cd web && npm run typecheck && npm test`; **[user]** browser check.

- [ ] 3. Actions from the shoots and phone work
  - ID: act-3 (after sh-2). Rules 10 and 11 use the live plan and suggestion APIs. The time-zone banner's (sp-7b)
    suggestions also appear as actions ("Align Z6 for 2–7 Apr").
