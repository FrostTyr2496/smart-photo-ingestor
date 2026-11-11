# Getting iPhone and iCloud photos into PhotoVault

This guide is for you, the user. It covers how to get iPhone photos (and the RAWs and Live Photos
that sit in iCloud) out of Apple's storage and onto a card-like folder that PhotoVault can ingest.
Menu names differ a little between macOS and iOS versions; the wording below follows recent releases.

Write-up date: 2026-11-11. The ingest command is not built yet (fnd-6 and ing-9), so the last step
in each section describes the target workflow.

## 1. Where your iPhone photos actually live

| Place | What it holds | Usable by PhotoVault? |
|---|---|---|
| iPhone (camera roll) | Originals, or small "optimised" copies if *Optimise iPhone Storage* is on | Yes, over USB, if you choose *Keep Originals* |
| iCloud Photos | The master copies. Originals stay here; the phone and Mac keep copies only if you ask | Only through a Mac or the web (see below) |
| Mac Photos library (`~/Pictures/Photos Library.photoslibrary`) | A database plus `originals/` with UUID file names | Not directly. Export the files out first |
| iCloud.com | Web view with download, one photo or a zip at a time | Yes, for small sets |

Two rules follow from this:

- **Never point PhotoVault at the Photos library package.** Its file names are UUIDs, the real capture
  dates and names live in a database, and Photos may hold it open. Export the originals to a folder
  instead.
- **iCloud is not a card.** PhotoVault treats sources as read-only, so an export folder is safe to read,
  but the engine can't see iCloud directly.

## 2. Make the originals local first

Originals are downloaded to the Mac only when you turn on the setting. Without it you may get small
placeholders or nothing at all when you export.

1. On the Mac, open **Photos → Settings → iCloud**.
2. Tick **Download Originals to this Mac**. Wait until Photos finishes downloading (the library
   status shows *Updating…* until it's done).
3. Check the library has room. A full library of RAW and ProRAW files can be large.
4. If *Optimise Mac Storage* is on, the Mac keeps only small copies and fetches originals on demand.
   Export only when online, and check the file sizes in step 4 of section 3.

## 3. Export the originals (recommended route)

Export gives you the original bytes, with the original file names and no edits applied.

1. In Photos, select the photos (or press Cmd-A in the album you want).
2. Choose **File → Export → Export N Photos…**.
3. Set **Photo Kind** to **Original** (not *Edited*), and set **Subfolder format** to *None*.
   Alternatively use **File → Export → Export Unmodified Original for N Photos**.
4. Check the output: a RAW (`.DNG` for ProRAW, `.HEIC` for normal shots) should be several MB, not
   a few hundred KB. A Live Photo should give you a `.HEIC` (or `.JPG`) and a `.MOV` with the same stem.
5. Export into a **card-shaped folder** (see section 5).

Edits you made in Photos are not in the original file. They live in the Photos database, so an
export of the *Original* kind leaves them out. Keep that in mind when you compare results.

## 4. Get photos straight off the iPhone (USB)

Good for fresh shoots that have not reached the Mac yet.

1. On the iPhone: **Settings → Photos → Transfer to Mac or PC → Keep Originals**. (*Automatic* may
   convert HEIC or ProRAW to JPEG, which loses the RAW.)
2. Connect the iPhone to the Mac with a cable and unlock it. Tap **Trust** if asked.
3. Open **Image Capture** (or **Photos → Import**). Choose the destination folder and import.
4. Image Capture copies a `DCIM` folder structure, like a camera card. That is the easiest path for
   PhotoVault.

If you use Photos → Import instead, the files land in the library. Export them again, as in section 3,
if you want a plain folder.

## 5. Make the export look like a card (until `allow_non_card` exists)

The design (system-design §4) only ingests from a source that looks like a card: a `DCIM` folder at the
root. Until PhotoVault accepts any folder, stage exports like this:

```
<staging folder>/
  DCIM/
    100APPLE/
      IMG_0001.HEIC
      IMG_0001.MOV          (the Live Photo video, same stem)
      IMG_0002.DNG          (ProRAW)
```

- Keep the stem pairs intact (`.HEIC` with `.MOV`, `.DNG` with `.AAE` if you have one).
- Put the staging folder on the Mac's internal disk or a spare drive, **not** on the working SSD and not
  inside the Photos library.
- Don't rename the files. PhotoVault renames them into the vault using the EXIF time and the shutter
  count, so the originals must keep their metadata.

## 6. What PhotoVault will do with the files

When the ingest command is built, the flow will be:

```
uv run photovault --json ingest --source "<staging folder>" --target "/Volumes/X9 Pro" --name "<shoot name>" --dry-run
```

It reads the card-shaped folder, groups the sidecars (HEIC + MOV + AAE), copies each file with a verify
step, and records everything in the catalog. It never writes to the staging folder. Running the real
copy onto `/Volumes` is a step for you to run yourself.

Duplicates are found by content (SHA-256), not by name. A photo that is already in iCloud, on a card and
in the Photos library is copied once, and the other copies are reported.

## 7. Using iCloud.com (small sets only)

1. Sign in at icloud.com, open **Photos**, select the items.
2. Choose **Download**. Multiple items come as a `.zip`.
3. Unzip into a staging folder shaped as above. Check that `.HEIC` and `.MOV` pairs survived the zip.

The web route is slow and sometimes renames or converts files, so use it for a few photos, not for a
shoot.

## 8. macOS privacy: why the Photos library can't be read from this session

macOS protects the Photos library. A tool in Terminal (or in Claude Code) gets *Operation not permitted*
when it lists the package, even with a normal read. This is working as designed. If you ever want the
engine to read the library directly, that means granting **Full Disk Access** to the terminal app in
**System Settings → Privacy & Security**, and it is your decision. The export route above doesn't need it.

## 9. Privacy checklist

- Exports contain real photos and possibly locations (GPS). Keep them in `testdata/` or a private
  folder, never in git. This repo is public.
- Don't paste the Photos library path, the iCloud account name or the device name into committed docs.
- If you delete photos in iCloud, the Mac copy may go too (depending on the settings). Ingest first,
  then clean up.

## 10. Quick checklist

- [ ] Originals downloaded to the Mac (or the iPhone set to *Keep Originals*).
- [ ] Exported *Original* kind, with file names intact.
- [ ] HEIC/DNG and MOV pairs present and of the expected size.
- [ ] Staged as `DCIM/100APPLE/…` on a non-card, non-working disk.
- [ ] Nothing committed to git.
