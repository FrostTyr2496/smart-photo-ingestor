# Card workflow: import a card, check it, format it

The everyday commands for the PhotoVault CLI. Run them from the repo folder in a terminal. Add `--json` before the
command for machine-readable output (`uv run photovault --json drives`).

## Once: give the working SSD its role

```sh
uv run photovault drives
uv run photovault volumes assign "/Volumes/X9 Pro" --role WORKING --label X9_WORK
```

`assign` writes one small hidden file, `/.photovault-id`, to the SSD's root, so PhotoVault recognises the drive
even if you rename it. Nothing is ever written to a card.

## Each card

1. **See the plan (writes nothing):**
   ```sh
   uv run photovault ingest --source "/Volumes/NIKON Z 6_2" --target "/Volumes/X9 Pro" --name "Shoot name" --dry-run
   ```
   Check the folders, the sample names, the size against the free space, and any warnings. `--name` is optional;
   without it the folders are just dates. A card that spans several days gets one folder per shoot day
   (a day runs noon to noon).
2. **Import:** run the same command without `--dry-run`. Every file is copied, re-read from the SSD and checked
   by SHA-256 before it counts. Ctrl-C stops cleanly after the current file. Resume with
   `uv run photovault resume <job>`, and see jobs with `uv run photovault jobs`.
3. **Is it safe to format?**
   ```sh
   uv run photovault card-status "/Volumes/NIKON Z 6_2"
   ```
   GREEN (exit 0) means every file on the card has a verified copy on the WORKING drive. RED (exit 3) lists
   what is missing. **Format in camera only when GREEN.** PhotoVault never deletes anything.
4. **Running it again is safe.** A second import of the same card copies nothing (every file shows up as a
   duplicate). An existing file at a destination is never overwritten: it is reported as a "name collision".

## Where files land

`Photography/Imports/<YYYY>/<YYYYMMDD>[ - Shoot name]/<body>/M<num>_<date>-<time>-<subsec>_<body>_<shutter>.<EXT>`

Example: `Photography/Imports/2026/20261027 - Night Test/Z6II/M3371_20261027-205115-51_Z6II_0013861.NEF`.
The NEF and JPEG of one shot share a name. Bodies come from `[bodies]` in the config. An unknown body, such as
a rented one, gets its model name without spaces (`NIKONZ9`) and a warning in the plan.

## The catalog

The catalog lives at `~/Library/Application Support/PhotoVault/catalog.sqlite`. Set `PHOTOVAULT_HOME` to keep a
separate one, for example for a trial run.

## Old drives and duplicates

To index an existing drive and find duplicates (`index`, `hash`, `report duplicates`), see `INDEX_WORKFLOW.md`.

## Clearing a card: re-read every copy

Cards -> Clear (or `cards clear <mount> --dry-run`) shows what is safe to delete. By default it re-reads every copy that
makes a file green (the backup copies, or both working copies) from their drives, so a copy that went bad since it was
verified keeps the file on the card. Those drives must be plugged in, and a hard drive makes it slower. You can untick
"Re-read every copy before deleting" (`--no-verify-all-copies`) to bypass it knowingly: then only one copy is re-read
and the other is trusted from the catalog. Everything else (the card file's own checksum, DCIM only) is always checked.

## Test cards

To rehearse a real import with a card the catalog already has copies of, mark it as a test card:
`uv run photovault --json test-card mark "<card mount or card:LABEL>" --hide "<drive>" [--hide "<drive>" ...]`.
For that card's files, copies on the hidden drives count as nonexistent: the files show red, import copies them
again, and they turn yellow then green as new copies land on drives that are not hidden. Card clear and make-green
follow the same view. Nothing on any drive changes; only the catalog's settings row. `test-card show` prints the
state and `test-card clear` restores the normal view. Cards shows a "Test" badge on the card.
