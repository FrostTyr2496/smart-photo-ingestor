# PhotoVault architecture

PhotoVault imports camera cards onto external drives with byte-verified copies. It tracks every file on every
drive in one SQLite catalog on the Mac, and it tells you, file by file, when a card is safe to clear. This page
describes how the system is built. For the exact signatures and schema, see
[`steering/system-design.md`](steering/system-design.md). For why it is built this way, see
[`ARCHITECTURE_AUDIT_AND_ROADMAP.md`](ARCHITECTURE_AUDIT_AND_ROADMAP.md) and [`decisions.md`](decisions.md).

## 1. The system at a glance

```mermaid
flowchart LR
    subgraph Mac["Mac (everything runs locally)"]
        UI["React UI<br/>(browser, 127.0.0.1:8765)"]
        Server["photovault serve<br/>JSON API + static files"]
        Engine["photovault CLI<br/>(engine, one process per job)"]
        Catalog[("catalog.sqlite<br/>volumes · assets · instances · jobs")]
        Metrics[("metrics.sqlite<br/>I/O per drive")]
        Activity["activity/*.json<br/>live progress"]
        Previews["preview cache"]
    end

    subgraph Drives["External drives, by role"]
        CARD["CARD<br/>camera media<br/>read-only"]
        WORKING["WORKING<br/>SSDs, receives copies"]
        MASTER["MASTER<br/>offline archive"]
        LEGACY["LEGACY<br/>old drives, indexed in place<br/>read-only"]
    end

    Tools["exiftool · Lightroom .lrcat (read-only)<br/>Photos library export (phone GPS)"]

    UI <-->|HTTP JSON| Server
    Server -->|spawns| Engine
    Engine -->|JSON lines on stdout| Server
    Engine <--> Catalog
    Engine --> Metrics
    Engine --> Activity
    Server <-->|"reads; writes edits & settings"| Catalog
    Server -->|reads| Activity
    Server --> Previews
    Engine -->|read| CARD
    Engine -->|read| LEGACY
    Engine <-->|verified write| WORKING
    Engine <-->|verified write| MASTER
    Engine --> Tools
```

Three ideas carry the design:

1. **A headless engine does all the work.** Every operation that reads a drive or changes the catalog
   (import, backup, index, hash, card check, card clear) is a command of the `photovault` CLI. The command
   prints its progress as JSON lines. The web server never touches a drive directly. It starts the CLI as a
   subprocess and relays the events. So a job started in a terminal and a job started from the UI are the same
   thing, and the UI also shows runs that a terminal started.
2. **The catalog is the contract.** One SQLite file records which drives exist, which files they hold, and
   which copies were verified, and when. Every answer the UI gives ("is this card safe?", "where else does
   this photo live?") is a query over it. Its schema changes only through numbered migrations (now at V8).
3. **Identity is the SHA-256 of the bytes.** File names, sizes, dates and partial hashes only narrow the
   candidates. Two files are the same photo only when their full hashes match.

## 2. Process model

```mermaid
sequenceDiagram
    participant B as Browser (React)
    participant S as photovault serve
    participant E as photovault CLI (subprocess)
    participant C as catalog.sqlite
    participant D as Drives

    B->>S: POST /api/... (e.g. Import, Make green)
    S->>E: spawn `python -m photovault --json <command>`
    loop for each file
        E->>D: read / verified write
        E->>C: one transaction per item
        E-->>S: {"event": "progress", ...} (JSON line)
        S-->>B: task state on poll
    end
    E-->>S: {"event": "done", ...}
    Note over E: Also writes activity/<pid>.json,<br/>so runs started in a terminal show up in Tasks
```

- The server binds to `127.0.0.1` only. It writes small things to the catalog itself (settings, drive names,
  photo edits such as location and keywords), but it never reads or writes a drive's media. That is the engine's job.
- Each item of a job commits in its own transaction. A killed or unplugged run leaves the job `paused`, and
  rerunning it resumes from the first unfinished item. Leftover `.pvpartial` files are swept first.
- Metrics are recorded alongside the work and never in the catalog. They are buffered and flushed every few
  seconds, so measuring never slows a copy.

## 3. The verified copy

`copy_engine.copy_verified` is the only code that writes photo bytes. Imports, backups and the follow copy to
a second drive all go through it.

```mermaid
flowchart LR
    A["Read source,<br/>hash while copying"] --> B["Write<br/>.name.pvpartial"]
    B --> C["F_FULLFSYNC<br/>(falls back to fsync)"]
    C --> D["Re-read from disk,<br/>bypassing the cache"]
    D --> E{"SHA-256<br/>matches?"}
    E -->|yes| F["Rename to final name<br/>(never overwrites)"]
    E -->|no| G["Delete partial,<br/>record incident, fail item"]
    F --> H["Catalog: instance = verified"]
```

When a backup copies from a drive whose file was already hashed, the bytes read are also checked against the
catalog's checksum, so a copy made from a silently corrupted source fails instead of spreading the corruption.
The re-read bypasses the OS cache on purpose: a hash of cached pages proves only that memory holds the right
bytes, not the disk. Names are Windows-safe and unique without regard to case, because the working drives are
exFAT.

## 4. A card's life

Each file on a card gets a colour from the catalog: how many verified copies it has, and where.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Uncopied: card check (index + hash)
    Uncopied --> OneCopy: Import to a WORKING drive
    OneCopy --> Green: Second drive or Make green
    Uncopied --> Green: already on a MASTER or two drives
    Green --> Cleared: Clear card (dry run, typed confirm, re-check)
    Cleared --> [*]
```

- **Yellow (one copy):** a verified copy exists on one drive.
- **Green:** a verified copy exists on a MASTER, or on two different WORKING/MASTER drives.
- **Clear** deletes only green files, and it checks each one against the catalog again right before deleting
  it. Optionally it re-reads every copy first. It is the one place where the engine deletes anything, and it
  never formats a card.

## 5. Data model

```mermaid
erDiagram
    volumes ||--o{ instances : holds
    assets ||--o{ instances : "copies of"
    capture_groups ||--o{ assets : "files of one shot"
    bodies ||--o{ capture_groups : "shot by"
    shoots ||--o{ capture_groups : contains
    trips ||--o{ shoots : groups
    sequences ||--o{ capture_groups : "burst / run"
    capture_groups ||--o| group_meta : "GPS, keywords, credit"
    capture_groups ||--o{ sidecars : "XMP on drives"
    jobs ||--o{ job_items : "one per file"
    volumes ||--o{ jobs : "source / target"
    bodies ||--o{ body_clock : "clock corrections"

    volumes {
        text volume_id PK
        text role
        text label
    }
    assets {
        int asset_id PK
        text sha256 UK
        int size
    }
    instances {
        int instance_id PK
        text rel_path
        text state
        text verified_at
    }
    capture_groups {
        int group_id PK
        int shutter_count
        text canonical_stem
        text capture_time
    }
    jobs {
        int job_id PK
        text kind
        text state
    }
    job_items {
        int seq
        text src_rel
        text dst_rel
        text state
    }
```

- An **asset** is a unique byte sequence (one SHA-256). An **instance** is that asset at a path on a volume,
  in the state `indexed`, `verified`, `mismatch` or `missing`.
- A **capture group** is one press of the shutter: RAW + JPEG + sidecars + derivatives, keyed by the camera
  body and its shutter count. Its `canonical_stem` gives every member the same deterministic name, so camera
  file-number rollover can never collide.
- **Jobs** are `card_ingest`, `replicate` (backup), `index_volume`, `baseline_hash` and `card_clear`. Their
  items are the unit of resume.
- User edits (location, keywords, credit, clock shift) live in `group_meta` and are written out as XMP
  sidecars next to the copies, never into the original files.

## 6. Code map

| Layer | Modules |
|---|---|
| Entry points | `cli.py` (argparse, JSON-lines `protocol.py`), `web/server.py` (JSON API, task runner) |
| Jobs | `ingest.py` (plan + run + resume), `backup.py` (replicate, Make green), `index.py` (index + baseline hash), `card_clear.py`, `cards.py` + `status.py` (is it safe?), `targets.py` (which drives to copy to) |
| Core I/O | `copy_engine.py` (the only writer), `hashing.py`, `scan.py`, `volumes.py` (probe, markers, roles), `drives.py` |
| Meaning | `metadata.py` (exiftool), `groups.py` (capture groups), `naming.py`, `timemodel.py`, `sequences.py`, `shoots.py` |
| Enrichment | `meta.py`, `geo.py`, `phone.py`, `clock.py`, `xmp.py`, `previews.py`, `lrcat.py` (read-only Lightroom) |
| Reporting | `report.py` (duplicates, coverage), `usage.py`, `actions.py` (what to do next), `metrics.py`, `activity.py` |
| Catalog | `catalog/db.py`, `catalog/migrations.py`, `catalog/repo.py` (all catalog SQL lives here) |
| UI | `web/` (React + TypeScript, Vite). The build is committed under `photovault/web/static/next/`, so running needs no Node |

## 7. Safety rules and how they are enforced

| Rule | Enforcement |
|---|---|
| Source drives (CARD, LEGACY) are never written | Roles in the catalog; markers only on WORKING/MASTER; the engine opens source files `"rb"` only |
| One writer for photo bytes, always verified | `copy_engine.py` is the only module that writes media; tests inject faults and verify mismatches |
| Nothing is deleted, except a user-confirmed card clear of green files | `card_clear.py` is the only delete path; a dry run, typed count and per-file re-check |
| A run can stop at any moment | Per-item transactions, `.pvpartial` temp names, resume re-checks committed files |
| The development agent cannot touch real drives | A pre-tool hook blocks writes and engine runs on `/Volumes`; real-drive commands are run by a person |
| No private data in the public repo | Drive facts and sample photos live in gitignored `docs/private/` and `testdata/` |

The full list is [`steering/vault-rules.md`](steering/vault-rules.md).

## 8. Stack

Python 3.12 (stdlib, PyExifTool, Pillow), SQLite, ExifTool, React + TypeScript + Vite, Leaflet. No cloud
services, no daemon: the engine runs only while a job runs.
