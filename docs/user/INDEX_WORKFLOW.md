# Index workflow: find out what is on an old drive and where the duplicates are

The commands that teach PhotoVault what is already on a drive, without changing the drive. Run them from the repo
folder in a terminal. Add `--json` before the command for machine-readable output. For importing cards, see
`CARD_WORKFLOW.md`.

Nothing here writes to, moves or deletes anything on a drive. The only files created are the catalog and the
report, both in your home folder. PhotoVault never deletes anything.

## Once per drive: register it as LEGACY

```sh
uv run photovault drives
uv run photovault volumes assign "/Volumes/Flats 1" --role LEGACY --label FLATS1
```

LEGACY means an existing drive that is indexed in place and treated as read-only. It gets no marker file. The
drive is recognised by its hardware volume ID, so renaming it in Finder does not lose it, but its mount path
changes, so use the new path afterwards. Do not rename a drive while a job is running on it.

## 1. Index: what is there

```sh
uv run photovault index "/Volumes/Flats 1"
```

Walks the drive and records every file's path, size and date, a small fingerprint of its first and last bytes,
and (for photos and videos) the camera metadata used to pair RAW and JPEG files. It prints the file count after
the walk, then progress. Unreadable files give a `read_error` warning and the run carries on.

- Re-running is quick: files whose size and date have not changed are skipped.
- Files that have disappeared since the last run are marked `missing`.
- **Ctrl-C pauses; run the same command again to resume.**
- Lightroom catalog files (`.lrcat`) are never opened by `index`.

## 2. Hash: what is identical

```sh
uv run photovault hash "/Volumes/Flats 1"
```

Reads every indexed file in full and computes its SHA-256. Two files with the same SHA-256 are the same bytes,
and that is how duplicates are found. **Run `index` first:** `hash` only handles files that are indexed, so on an
unindexed drive it finishes at once with `hashed=0`.

**How long will it take?** The first line says `N files, X GB to hash`. Every 100 files it prints
`[hash] n/N  X GB hashed, Y MB/s`. The time left is the bytes still to hash divided by that rate. Small files
(JPEGs, sidecars) go slower per byte than RAW or video, so the rate moves around.

- Files go in path order and each file is saved as it finishes, so a stop loses at most one file.
- **Ctrl-C pauses; run the same command again to resume.**
- `--max-mbps N` caps the read rate (1 MB = 10^6 bytes) if you need the Mac or the drive for something else.
- A file that changed after it was indexed is skipped with a warning. Run `index` again to pick it up.

## 3. Optional: mark what Lightroom uses

Only if a Lightroom catalog points at files on this drive. **Quit Lightroom, and work on a copy of the catalog**:

```sh
cp "/path/to/Catalog.lrcat" ~/Desktop/catalog-copy.lrcat
uv run photovault lrcat mark ~/Desktop/catalog-copy.lrcat --volume "/Volumes/Flats 1"
```

This only reads the copy. It flags files the catalog references so the report can show which copies are safe to
consider. It refuses a catalog that has a lock file or while Lightroom is running. If it reports a schema error,
stop and tell the agent: the Lightroom table names are not yet confirmed against a real catalog.

## 4. Report: duplicates and coverage

```sh
uv run photovault report duplicates
uv run photovault report duplicates --volume "/Volumes/Flats 1" --out ~/Desktop/flats1.md
```

Writes a Markdown file (by default `~/Library/Application Support/PhotoVault/reports/duplicates-<time>.md`; never
under `/Volumes`). It holds:

- a summary: duplicate groups, files, and the bytes you could reclaim;
- coverage per drive: files, not hashed yet, mismatches, and files that exist only on that drive;
- each duplicate group with every copy's drive, path and Lightroom flag.

Only hashed files can appear in a group. If some are not hashed yet, the command warns `unhashed_files`. You can run
the report while a hash is still going to see an early picture. **The report names real paths, so keep it out of
the repository** (use `docs/private/`).

## 5. Jobs: is everything finished?

```sh
uv run photovault jobs
```

`index_volume` and `baseline_hash` should be `done`. A job that shows `paused` was stopped: run its command
again. If a run crashed, a job can show `running`; running the command again simply carries on from the catalog,
which is always up to date.

## Two drives at the same time

Index and hash on two different drives can run in separate terminals. Each file is saved in its own short
transaction, so the catalog is safe. Things to know:

- They share the Mac's CPU and, if the drives hang off the same hub or port, the same bandwidth.
- If a command stops with `database is locked`, run it again: no work is lost.
- Use `caffeinate -i` in front of a long run so the Mac does not sleep and drop the drives:
  `caffeinate -i uv run photovault hash "/Volumes/Flats 1"`.

## Before you clean up

Do not move, rename or delete anything on a legacy drive until the baseline is finished and you have a verified
copy on your working or master drive. A Lightroom catalog stores file paths, so moving a file it uses breaks the
link. The consolidation plan (idx-7) covers how to tidy things safely.
