# Spec: phone-gps — locations and shoot suggestions from the user's phone photos (spike)

Goal: photos from the cameras get location suggestions (and place names in the inspector's search box) from the
phone photos taken around the same time, and the catalog suggests shoots from the camera and phone timelines
together. The phone photos live in iCloud Photos; the Mac has a Photos library with iCloud Photos on.

Known (2026-11-18): `~/Pictures/Photos Library.photoslibrary` exists. Its `database/` folder is blocked by macOS
privacy controls ("Operation not permitted") for the terminal, so any reader needs Full Disk Access or Photos
permission granted by the user. The engine already has `geo_points` (time-indexed GPS fixes), `meta_refs` (a GPS
reference folder read into `geo_points`) and `geo.suggest` (time-matched suggestions with interpolation).

## Tasks

- [x] 1. Spike: how to read time + location (+ place names) from the Photos library, once, without downloading originals
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
  - User's answers (D-066, amended by D-070): iTerm gets Full Disk Access; one import now (continuous sync is sp-5);
    place names stored in the catalog and written to XMP (schema V5, committed); Photos is quit during the export;
    only iPhone-camera photos and videos with a location (no screenshots, shared albums or "Shared with You").
    **osxphotos runs as an external tool** (`uv tool run`), never as an engine dependency (D-070: it pulls in about
    80 packages). It writes a JSON file; `photovault` reads that file with the stdlib.

- [ ] 2. **[stronger]** Import iPhone locations from an osxphotos JSON export into `geo_points`
  - ID: sp-2 (after sp-1, and after the V6 `geo_points` columns; see below). _Design:_ D-066, D-070, `system-design.md` §5e.
  - Step 1, **[user]**, in iTerm with Photos quit (the output stays in the PhotoVault home, outside the repo). Paste as is:
    ```sh
    mkdir -p "$HOME/Library/Application Support/PhotoVault/phone"
    uv tool run --from 'osxphotos==0.78.1' osxphotos query \
      --not-screenshot --not-shared --not-syndicated --location --json \
      > "$HOME/Library/Application Support/PhotoVault/phone/photos-$(date +%Y%m%d).json"
    ```
    To limit the range, add `--from-date 2026-01-01 --to-date 2026-03-31` before the `>`. With `$PHOTOVAULT_HOME`
    set, use that folder instead (`config.default_home()`).
  - Step 2: `photovault meta phone <json>` in `photovault/phone.py` (stdlib `json` only). It accepts a JSON array or JSON lines of
    osxphotos `PhotoInfo.asdict()` rows. It keeps a row when `exif_info.camera_make == "Apple"`, `exif_info.camera_model`
    starts with `"iPhone"`, `latitude`/`longitude` are set, and `screenshot`, `shared`, `syndicated` and `intrash`
    are all false. It keeps photos and movies.
  - Each kept row is upserted into `geo_points` on `(source, source_ref)`: `source = 'iphone'`, `source_ref = uuid`,
    `utc_time` = `geo.utc_text(date)` (`date` is ISO with an offset; if it has none, apply `tzoffset` seconds),
    `lat`, `lon`, `place_name = place.name`, `city` / `state` / `country` from `place.address`
    (`city`, `state_province`, `country`), falling back to the first entry of `place.names.city` /
    `state_province` / `country`, `accuracy_m = NULL` (not in the JSON), `local_offset` = `tzoffset` as `+HH:MM`, and
    `kind` = `'video'` if `ismovie` else `'photo'`. Re-running the same file changes nothing.
  - Result: rows seen, kept, inserted, updated, and skipped per reason (`not_iphone`, `no_location`, `screenshot`,
    `shared`, `trash`, `bad_date`). A file that is not osxphotos JSON fails with a message naming the export command.
  - Schema: `geo_points.local_offset TEXT` and `geo_points.kind TEXT` (approved, D-070) belong to the cu-0 V6
    migration. If cu-0 is not approved when sp-2 starts, these two columns land alone as V6 (with `system-design.md` §5f),
    and cu-0 becomes V7.
  - Also from the shelved draft: `group_meta.city/state/country` in `GET /api/groups`, `POST /api/groups/meta`, `meta set`
    and the XMP (`photoshop:City/State/Country`); `geo.suggest` returns each point's `place_name`, `city`, `state`,
    `country`.
  - Verify: `uv run pytest tests/test_phone.py tests/test_geo.py tests/test_xmp.py -q`. The fixture is a synthetic
    osxphotos JSON with invented coordinates. **[user]** then runs it on a short date range.

- [ ] 3. Place names from the phone in the inspector
  - ID: sp-3 (after sp-2). _Design:_ D-066.
  - Suggestions show the place ("Harbourton, Westshire · +3 min"); Use sets the location, place name, city, state and
    country. With the search box focused and empty, a list "Near this time, from your iPhone" offers the distinct
    places of the phone points within ±1 h of the photo. Choosing one fills the same fields with no Nominatim call.
    Nominatim results fill city/state/country from `addressdetails`. City, state and country are editable under Place
    name.
  - Verify: `cd web && npm test`; **[user]** visual check.

- [ ] 4. **[stronger]** Suggested shoots across cameras and the phone
  - ID: sp-4 (after sp-2). The `shoots` table is empty today; seq-2 (sequences inside a shoot) is unaffected.
  - `photovault/shoots.py` `suggest_shoots(conn, gap_h=4, split_km=50)`: camera frames (UTC after clock shift, as
    `geo.group_utc`) and iPhone points on one timeline. A new shoot starts after a gap longer than `gap_h`, or after a
    move of more than `split_km` between consecutive located items. Clusters without camera frames are dropped;
    frames already in a shoot are left out. Each suggestion has a name from the most common city (else the country,
    else none) plus the dates ("Harbourton · 12–14 Jul"), first and last local time, frame and phone counts, cameras,
    group ids and the centroid. Check first whether Photos' Moment titles (osxphotos `{moment}` template) are worth
    adding to the export; that would be an extra `--field`, not a new tool.
  - API: `GET /api/shoots/suggestions?gap_h=&split_km=`; `POST /api/shoots` (name, shoot_date, group_ids) creates a shoot
    and assigns the frames; `POST /api/shoots/<id>` renames; `POST /api/shoots/<id>/release` unassigns its frames and
    removes the empty shoot (catalog rows only). `/api/photo-days` gains `shoot_id`.
  - UI `#/shoots`: gap and distance controls, suggestion cards (editable name, dates, counts, cameras, place, a
    thumbnail strip, Accept, Open in Photos), "Accept all", and the accepted shoots (rename, release). Photos gets a
    shoot filter and the shoot name on day headers.
  - Verify: `uv run pytest tests/test_shoots.py tests/test_web_api.py -q`; `npm test`; a run on a copy of the real
    catalog (counts only, into `docs/private/`).

- [ ] 5. Continuous sync of phone locations (later, as its own feature; likely the Mac app via PhotoKit)
  - ID: sp-5. The app writes the same JSON shape that sp-2 reads, so the engine side doesn't change (D-070).

- [ ] 6. **[stronger]** Sessions that use the phone track, and Follow the track
  - ID: sp-6 (after sp-2 and rv-16). _Design:_ D-070.
  - `GET /api/geo-points?after=&before=&source=iphone` returns points (UTC, lat, lon, place fields) for the related
    window. `web/src/lib/photos.ts` `relatedSession` chains over camera frames **and** phone points on one UTC timeline:
    a phone point can bridge a camera gap (each consecutive item is still within the threshold). A move of more than
    `split_km` (default 2 km) between consecutive located items (camera GPS or phone) breaks the chain, so a drive
    between two spots splits the session even when the gap is short. Phone points are never returned as related photos.
  - Follow the track (the default when phone points exist; D-070): saving Place to related photos calls
    `POST /api/groups/geo-follow {group_ids, fallback: {lat, lon, alt, place_name, city, state, country}}`. For each group
    without camera GPS, the server takes the first `geo.suggest` result with confidence `high` or `medium` (gps_source
    `match`, place fields from that point); otherwise it uses the fallback (gps_source `neighbour`). The selected photos
    get the user's pin. "Same point for all" (one click next to it) keeps the rv-9 behaviour. Each related row shows where its
    location came from: "phone · +2 min" or "your pin".
  - Verify: `uv run pytest tests/test_web_api.py -q -k geo_follow`; `cd web && npm test` (bridge, split and
    fallback cases); **[user]** visual check.

- [ ] 7. **[stronger]** Time-zone suggestions per camera from the phone timeline
  - ID: sp-7 (after sp-2). _Design:_ D-070. Metadata only (rule 9).
  - `photovault/clock.py` `suggest_offsets(conn, *, min_matches=20)`: split the phone timeline into runs where
    `local_offset` stays the same (trips). For each camera body and each run, try shifts from −14 h to +14 h in 15-min
    steps on the frames' current corrected UTC, and count frames within ±10 min of some iPhone point. When the best
    shift is not 0, has at least `min_matches` matches, and has at least twice the matches of the runner-up, suggest it:
    body, date range, frame count, current shift, suggested `time_shift_s`, matches. Shifts of a few minutes can't be
    told apart this way; those stay with the manual clock shift.
  - API `GET /api/clock-suggestions`; Photos shows a dismissible banner ("Z 6II, 12–19 Jul, looks 8 h off: shift
    1,240 photos"). Apply sets `time_shift_s` on that body's frames in the range through the existing meta save.
  - Verify: `uv run pytest tests/test_clock.py -q` (synthetic camera frames 8 h off against a phone track; a no-signal
    case gives no suggestion).
