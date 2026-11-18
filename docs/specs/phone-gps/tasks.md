# Spec: phone-gps — locations and shoot suggestions from the user's phone photos (spike)

Goal: photos from the cameras get location suggestions (and place names in the inspector's search box) from the
phone photos taken around the same time, and the catalog suggests shoots from the camera and phone timelines
together. The phone photos live in iCloud Photos; the Mac has a Photos library with iCloud Photos on.

Known (2026-11-18): `~/Pictures/Photos Library.photoslibrary` exists. Its `database/` folder is blocked by macOS
privacy controls ("Operation not permitted") for the terminal, so any reader needs Full Disk Access or Photos
permission granted by the user. The engine already has `geo_points` (time-indexed GPS fixes), `meta_refs` (a GPS
reference folder read into `geo_points`) and `geo.suggest` (time-matched suggestions with interpolation).

## Tasks

- [ ] 1. Spike: how to read time + location (+ place names) from the Photos library, once, without downloading originals
  - ID: sp-1 [stronger]. Research by an agent; decision by the user.
  - Compare: (a) read `Photos.sqlite` read-only from a copy (needs Full Disk Access for the process; Core Data
    schema varies by macOS version; place names may sit in reverse-geocode data); (b) `osxphotos` (Python
    library and CLI over the same database; a new dependency); (c) a small Swift helper using PhotoKit
    (`PHAsset.location`, `creationDate`; asks for Photos permission; no originals downloaded; fits the planned
    SwiftUI app); (d) export from Photos.app into a folder for `meta_refs` (downloads originals, slow).
  - For each: permission needed, what it returns (UTC time, lat/lon, accuracy, place names), cost on a library of
    tens of thousands of photos, privacy (all local; nothing to git), new dependencies (need the user's approval).
  - Output: findings and a recommendation in this entry (no real data); real counts or samples in `docs/private/`.
  - Findings (2026-11-18):
    - Local checks: macOS 15.2, Xcode app present (Swift 6.1, `xcrun swift` works), `osxphotos` not installed
      (PyPI latest 0.78.1), `uv` not on this shell's PATH. Nothing installed, no permissions touched, library not read.
    - Sources: osxphotos https://github.com/RhetTbull/osxphotos (README, MIT, lists macOS 15 as supported) and API
      https://rhettbull.github.io/osxphotos/API_README.html; schema notes https://github.com/RhetTbull/osxphotos/wiki/Understanding-the-Photos-database
      and https://engineered.at/articles/extracting-travel-data-from-macos-photos-library ; PhotoKit on macOS
      https://developer.apple.com/forums/thread/749286. Not verified from sources (my background knowledge, check on a copy):
      the lat/lon and accuracy column names.
    - Key fact for all options: the Photos database holds metadata (time, GPS, place names) for every asset, including
      iCloud-only ones. Optimize Mac Storage affects originals, not rows. (osxphotos marks such photos `ismissing`,
      but still returns date/location/place.) So no option except (d) needs a download.
    - (a) Direct SQLite. Tables: `ZASSET` (`ZDATECREATED` = seconds since 2001-01-01 UTC, so +978307200; `ZLATITUDE`,
      `ZLONGITUDE`, -180 when none) joined to `ZADDITIONALASSETATTRIBUTES` (`ZASSET` key; horizontal accuracy, time zone
      offset/name, and `ZREVERSELOCATIONDATA`, an NSKeyedArchiver binary plist with place names by `placeType`).
      Names come from Apple's reverse geocoding done by Photos and cached in the DB: offline for us. Schema: undocumented,
      reverse-engineered, renamed `ZGENERICASSET`->`ZASSET` in Photos 6, changes between macOS releases; we would own the
      breakage. Permission: Full Disk Access for the process that reads the file (Terminal, or the app running
      `photovault`); the library folder is TCC-protected. Safe while Photos is open only if the .sqlite, -wal and -shm
      files are copied together (or Photos is quit); open the copy read-only. Cost: no dependency (stdlib sqlite3 +
      plistlib for the blob, which needs the `$objects` UID walk). Fast (seconds for 50k rows). Privacy: all local.
    - (b) osxphotos (Python). Does (a) for us and absorbs schema changes per macOS release. `PhotoInfo.date` (tz-aware),
      `.location` / `.latitude` / `.longitude` (None when unset), `.place` (name, plus `names` by area: country, state,
      city...). Reads a working copy of the DB; no originals needed. Same Full Disk Access need (README does not state it).
      MIT. New dependency with a large tree (pyobjc, click, rich etc.; install size not measured, the spike did not
      install it); needs the user's approval (vault-rule 11). Accuracy field: not in the documented API, would need raw SQL.
      Could be run as a throwaway one-off in a temp venv outside the repo (`uv tool install osxphotos`, which does not touch
      `pyproject.toml`) writing a JSON/CSV that `photovault` imports: no repo dependency.
    - (c) Swift + PhotoKit. `PHAsset.location` (coordinate, horizontalAccuracy, timestamp) and `creationDate`; fetch
      without downloading originals; 50k assets enumerate in seconds to tens of seconds. No place names: `CLGeocoder` is
      network (Apple servers) and rate limited, so it is no good for 50k. A bare CLI binary is a poor fit: macOS needs
      `NSPhotoLibraryUsageDescription` (embedded via `-sectcreate __TEXT __info_plist`) and signing; people report silent
      `.denied` with no prompt for tools outside an app bundle. A small signed `.app` bundle is the dependable route and
      is what the planned SwiftUI app would be. Permission: Photos "Read and Write" in Privacy & Security, no Full Disk
      Access. Official API, stable across macOS. No new Python dependency (Swift code in the repo, needs user approval as
      new scope). Cost: building and signing a helper just for a one-off.
    - (d) Export. Downloads full originals (tens to hundreds of GB for tens of thousands of phone photos, hours, needs
      disk space and Photos open), then `meta_refs` reads GPS from the files. Fails for photos with no embedded GPS and
      returns no place names. Not recommended; the other options avoid this.
    - Comparison (permission | data | cost | dependency): (a) FDA | time, lat/lon, accuracy, names (blob) | low, fragile
      | none. (b) FDA | time, lat/lon, names (+accuracy via SQL) | low, robust | osxphotos. (c) Photos permission via .app
      | time, lat/lon, accuracy; no names | medium build | none (Swift). (d) Photos.app export | time, lat/lon | very high | none.
    - Offline place names: yes, via (a) or (b) (`ZREVERSELOCATIONDATA` / `.place`). sp-3 can offer them without
      Nominatim. Caveat: only for photos Photos has geocoded, and names follow Photos' language and version.
    - Recommendation: (a) in a one-off import script that fails loudly on unknown schema (check column names first,
      print which are missing), validated on a copy. If the schema check fails or the blob walk is painful, fall back to
      (b) run as an external one-off tool, with the user's approval. Do not build (c) now; revisit when the SwiftUI app
      exists, since it could then read PhotoKit for ongoing sync. Skip (d).
    - First step for the user: System Settings > Privacy & Security > Full Disk Access > add the terminal app you run
      `photovault` from (Terminal.app or iTerm, whichever you use), then restart it. Revoke afterwards if you like. Nothing
      is read until sp-2 is written and you run it; the agent never reads the library (copy step is a user command).
    - Open questions for the user: (1) Which terminal app should get Full Disk Access? (2) Is a one-off enough, or
      should phone photos sync again later (that favours PhotoKit in the app)? (3) OK to try osxphotos as an external
      throwaway tool if (a) is too brittle? (4) Do you want place names stored too (needs a catalog change:
      a `place_name` column or table; needs your approval, vault-rule 6)? (5) Is it OK to quit Photos while the DB is copied?
      (6) Are other people's shared-album photos or screenshots in the library to be excluded?
- [ ] 2. Import the phone timeline into `geo_points` (shape set by sp-1; needs the user's approval for any dependency)
  - ID: sp-2 (after sp-1)
- [ ] 3. Place names as suggestions in the inspector's search box (from the phone's places near the photo's time)
  - ID: sp-3 (after sp-2)
- [ ] 4. Suggested shoots across devices: cluster camera and phone captures by time gaps and location into proposed
  shoots the user accepts, renames or splits (the `shoots` table is empty today; relates to seq-2 and eval-1)
  - ID: sp-4 (after sp-2; check with seq-2 before building)
