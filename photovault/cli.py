"""PhotoVault CLI (fnd-6, ing-9). Human output by default; `--json` gives JSON lines (system-design §10)."""

from __future__ import annotations

import argparse
import json
import re
import signal
import sqlite3
import sys
import uuid
import webbrowser
from pathlib import Path

from photovault import cards, meta, metrics, phone, report, shoots, volumes
from photovault import activity
from photovault.activity import ActivityEmitter
from photovault.drives import CLI_DRIVE_KEYS, VOLUMES_ROOT, scan_mounts
from photovault.web import server as web_server
from photovault.catalog import db, repo
from photovault.config import ConfigError, load_config
from photovault.index import baseline_hash, index_volume, registered_volume
from photovault.ingest import plan_card_ingest, run_job
from photovault.lrcat import CatalogInUse, LrcatSchemaError, keys_on_volume, mark_lr_referenced, referenced_paths
from photovault.protocol import EXIT_ERROR, EXIT_OK, EXIT_PAUSED, EXIT_RED, EXIT_USAGE, Emitter, human_bytes
from photovault.status import card_status



def _open(cfg) -> sqlite3.Connection:
    cfg.home.mkdir(parents=True, exist_ok=True)
    conn = db.connect(cfg.catalog_path)
    db.migrate(conn)
    return conn


def _probe_dict(p: volumes.VolumeProbe) -> dict:
    return {"mount": str(p.mount), "name": p.name, "fs_type": p.fs_type, "capacity": human_bytes(p.capacity_bytes or 0),
            "internal": p.internal, "card": p.looks_like_card, "has_marker": p.marker_id is not None}


def cmd_catalog_init(args, cfg, emit) -> int:
    conn = _open(cfg)
    emit.emit("result", catalog=str(cfg.catalog_path), version=conn.execute("PRAGMA user_version").fetchone()[0])
    return EXIT_OK


def cmd_drives(args, cfg, emit) -> int:
    conn = _open(cfg)
    rows = scan_mounts(conn, Path(args.root) if args.root else VOLUMES_ROOT, emit)
    emit.emit("result", drives=[{k: r[k] for k in CLI_DRIVE_KEYS} for r in rows])
    return EXIT_OK


def cmd_volumes_probe(args, cfg, emit) -> int:
    emit.emit("result", **_probe_dict(volumes.probe(Path(args.mount))))
    return EXIT_OK


def cmd_volumes_list(args, cfg, emit) -> int:
    conn = _open(cfg)
    emit.emit("result", volumes=[{"label": r["label"], "role": r["role"], "fs": r["fs_type"],
                                  "last_mount": r["last_mount_path"], "last_seen": r["last_seen_at"]}
                                 for r in repo.list_volumes(conn)])
    return EXIT_OK


def cmd_volumes_assign(args, cfg, emit) -> int:
    conn = _open(cfg)
    mount = Path(args.mount)
    p = volumes.probe(mount)
    if p.looks_like_card:
        emit.emit("error", code="card", message=f"{mount} is a camera card; cards are registered automatically by ingest")
        return EXIT_ERROR
    if args.role in ("WORKING", "MASTER") and p.marker_id is None:
        volumes.write_marker(mount, str(uuid.uuid4()))
        p = volumes.probe(mount)
    with repo.transaction(conn):
        vid = volumes.register_volume(conn, p, role=args.role, label=args.label)
    emit.emit("result", volume_id=vid, role=args.role, label=args.label or p.name, mount=str(mount))
    return EXIT_OK


class _Stop:
    def __init__(self) -> None:
        self.flag = False

    def __call__(self) -> bool:
        return self.flag

    def install(self, emit: Emitter) -> None:
        def handler(signum, frame):  # noqa: ANN001
            if self.flag:
                raise KeyboardInterrupt
            self.flag = True
            emit.emit("warning", code="stopping", message="stopping after the current file (Ctrl-C again to abort)")
        signal.signal(signal.SIGINT, handler)


def _run(conn, cfg, job_id, emit) -> int:
    stop = _Stop()
    stop.install(emit)
    summary = run_job(conn, cfg, job_id, emit=emit, should_stop=stop)
    if summary.paused:
        return EXIT_PAUSED
    return EXIT_ERROR if summary.failed else EXIT_OK


def cmd_ingest(args, cfg, emit) -> int:
    conn = _open(cfg)
    conn.execute("BEGIN IMMEDIATE")
    try:
        job_id = plan_card_ingest(conn, cfg, source=Path(args.source), target=Path(args.target),
                                  shoot_name=args.name, emit=emit)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    if args.dry_run:
        conn.execute("ROLLBACK")
        emit.emit("warning", code="dry_run", message="dry run: nothing was written and the plan was discarded")
        return EXIT_OK
    conn.execute("COMMIT")
    return _run(conn, cfg, job_id, emit)


def cmd_resume(args, cfg, emit) -> int:
    conn = _open(cfg)
    return _run(conn, cfg, args.job_id, emit)


def cmd_card_status(args, cfg, emit) -> int:
    conn = _open(cfg)
    s = card_status(conn, cfg, Path(args.mount))
    verdict = "GREEN: every file is verified on WORKING; safe to format in camera" if s.state == "green" else \
        "RED: do NOT format this card"
    emit.emit("result", state=s.state, verdict=verdict, total=s.total, safe=s.safe, unknown=len(s.unknown),
              changed=len(s.changed), short=len(s.short),
              examples=(s.unknown + s.changed + [r for r, _ in s.short])[: args.show])
    return EXIT_OK if s.state == "green" else EXIT_RED


def cmd_cards_check(args, cfg, emit) -> int:
    """Index and hash a card (read-only) and rank its files by removability (D-046, D-048)."""
    conn = _open(cfg)
    stop = _Stop()
    stop.install(emit)
    try:
        out = cards.check_card(conn, cfg, Path(args.mount), emit=emit, should_stop=stop)
    except (ValueError, volumes.UnknownVolume) as exc:
        emit.emit("error", code="card", message=str(exc))
        return EXIT_USAGE
    emit.emit("result", **out)
    return EXIT_PAUSED if out["paused"] else EXIT_OK


def cmd_cards_list(args, cfg, emit) -> int:
    conn = _open(cfg)
    listing = cards.list_cards(conn, scan_mounts(conn, Path(args.root) if args.root else VOLUMES_ROOT, emit))
    emit.emit("result", cards=[{k: c[k] for k in ("label", "mounted", "tier", "files", "counts", "checked_at")}
                               | {"cameras": [{k: m[k] for k in ("name", "tier", "files")} for m in c["cameras"]]}
                               for c in listing["cards"]],
              cameras=[{k: m[k] for k in ("name", "tier", "files", "counts")} for m in listing["cameras"]])
    return EXIT_OK


def cmd_index(args, cfg, emit) -> int:
    conn = _open(cfg)
    stop = _Stop()
    stop.install(emit)
    job_id = index_volume(conn, cfg, Path(args.mount), emit=emit, should_stop=stop)
    return EXIT_PAUSED if repo.get_job(conn, job_id)["state"] == "paused" else EXIT_OK


def _relative_prefix(mount: Path, prefix: str) -> str | None:
    """A `--skip-prefix` relative to the mount; an absolute path is accepted only when it lies under the mount."""
    if not prefix.startswith("/"):
        return prefix
    try:
        return str(Path(prefix).relative_to(mount))
    except ValueError:
        return None


def cmd_hash(args, cfg, emit) -> int:
    mount = Path(args.mount)
    prefixes = []
    for p in args.skip_prefix or []:
        rel = _relative_prefix(mount, p)
        if rel is None or rel in ("", "."):
            emit.emit("error", code="usage", message=f"--skip-prefix must be a folder inside {mount}: {p}")
            return EXIT_USAGE
        prefixes.append(rel)
    conn = _open(cfg)
    stop = _Stop()
    stop.install(emit)
    job_id = baseline_hash(conn, cfg, mount, emit=emit, max_mbps=args.max_mbps, should_stop=stop,
                           skip_prefixes=prefixes, retry_unreadable=args.retry_unreadable)
    return EXIT_PAUSED if repo.get_job(conn, job_id)["state"] == "paused" else EXIT_OK


def cmd_lrcat_mark(args, cfg, emit) -> int:
    """Flag the instances on a registered volume that a Lightroom catalog references (reads the .lrcat only)."""
    conn = _open(cfg)
    mount = Path(args.volume)
    row = registered_volume(conn, mount)
    paths = referenced_paths(Path(args.lrcat))
    on_volume = len(keys_on_volume(mount, paths))
    marked = mark_lr_referenced(conn, row["volume_id"], mount, paths)
    emit.emit("result", lrcat=Path(args.lrcat).name, referenced=len(paths), on_volume=on_volume, marked=marked,
              volume=row["label"])
    if on_volume == 0:
        emit.emit("warning", code="no_paths_on_volume",
                  message=f"the catalog references no paths under {mount}; is this the right volume or catalog?")
    return EXIT_OK


def cmd_report_duplicates(args, cfg, emit) -> int:
    """Write the duplicate and coverage report. Reads the catalog only; the output may not be on a mounted drive."""
    out = report.check_output_path(Path(args.out) if args.out else report.default_report_path(cfg.home))
    conn = _open(cfg)
    volume_id = registered_volume(conn, Path(args.volume))["volume_id"] if args.volume else None
    clusters = report.duplicates(conn, volume_id=volume_id)
    summaries = report.coverage(conn)
    bad = report.unreadable(conn)
    report.write_markdown(clusters, out, summaries=summaries, unreadable=bad)
    reclaimable = sum(c.reclaimable for c in clusters)
    emit.emit("result", report=str(out), clusters=len(clusters), files=sum(len(c.instances) for c in clusters),
              reclaimable=human_bytes(reclaimable), reclaimable_bytes=reclaimable, unreadable=len(bad),
              unreadable_no_copy=sum(1 for b in bad if b.rescue == report.NO_COPY),
              volumes=[{"volume": s.label, "role": s.role, "files": s.files, "unhashed": s.unhashed,
                        "only_here": s.only_here_files, "only_here_size": human_bytes(s.only_here_bytes)}
                       for s in summaries])
    unhashed = sum(s.unhashed for s in summaries)
    if unhashed:
        emit.emit("warning", code="unhashed_files",
                  message=f"{unhashed} indexed files are not hashed yet and cannot appear in a cluster; "
                          "run 'photovault hash <mount>' first")
    return EXIT_OK


def cmd_jobs(args, cfg, emit) -> int:
    conn = _open(cfg)
    rows = []
    for r in repo.list_jobs(conn, args.limit):
        rows.append({"job": r["job_id"], "state": r["state"], "created": r["created_at"], "items": r["items"],
                     "committed": r["committed"] or 0, "dup": r["skipped_dup"] or 0, "failed": r["failed"] or 0,
                     "pending": r["pending"] or 0})
    emit.emit("result", jobs=rows)
    return EXIT_OK


def cmd_meta_scan(args, cfg, emit) -> int:
    conn = _open(cfg)
    row = registered_volume(conn, Path(args.volume))
    counts = meta.scan_volume(conn, cfg, Path(args.volume), row["volume_id"], emit, rescan=args.rescan)
    emit.emit("result", volume=row["label"], **counts)
    return EXIT_OK


def cmd_meta_refs(args, cfg, emit) -> int:
    conn = _open(cfg)
    counts = meta.scan_refs(conn, cfg, Path(args.folder), args.label, emit)
    emit.emit("result", label=args.label, **counts)
    return EXIT_OK


def cmd_meta_phone(args, cfg, emit) -> int:
    path = Path(args.json)
    if not path.is_file():
        emit.emit("error", code="usage", message=f"{path}: no such file")
        return EXIT_USAGE
    conn = _open(cfg)
    try:
        counts = phone.import_phone(conn, path, emit)
    except phone.NotOsxphotosJson as exc:
        emit.emit("error", code="bad_input", message=str(exc))
        return EXIT_USAGE
    emit.emit("result", **counts)
    return EXIT_OK


def cmd_meta_sidecars(args, cfg, emit) -> int:
    conn = _open(cfg)
    row = registered_volume(conn, Path(args.volume))
    counts = meta.write_sidecars(conn, cfg, Path(args.volume), row["volume_id"], emit, dry_run=args.dry_run)
    emit.emit("result", volume=row["label"], dry_run=args.dry_run, **counts)
    return EXIT_OK


def cmd_meta_missing(args, cfg, emit) -> int:
    conn = _open(cfg)
    volume_id = registered_volume(conn, Path(args.volume))["volume_id"] if args.volume else None
    emit.emit("result", **repo.missing_counts(conn, volume_id=volume_id))
    return EXIT_OK


def cmd_meta_set(args, cfg, emit) -> int:
    fields: dict = {}
    if (args.lat is None) != (args.lon is None):
        emit.emit("error", code="usage", message="--lat and --lon must be given together")
        return EXIT_USAGE
    if args.lat is not None:
        fields.update(lat=args.lat, lon=args.lon, alt=None, gps_source="map")
    for key, value in (("place_name", args.place), ("city", args.city), ("state", args.state),
                       ("country", args.country), ("creator", args.creator), ("copyright", args.copyright),
                       ("title", args.title), ("time_shift_s", args.time_shift)):
        if value is not None:
            fields[key] = value
    if args.keyword_add:
        fields["keywords_add"] = args.keyword_add
    if not fields:
        emit.emit("error", code="usage", message="nothing to set; pass at least one field option")
        return EXIT_USAGE
    conn = _open(cfg)
    with repo.transaction(conn):
        updated = repo.update_group_meta(conn, args.group, fields=fields)
    emit.emit("result", updated=updated, fields=sorted(fields))
    return EXIT_OK


def cmd_serve(args, cfg, emit) -> int:
    """Run the local web UI on 127.0.0.1 until Ctrl-C. Engine commands it starts are subprocesses of this CLI."""
    _open(cfg)  # create and migrate the catalog once before the first request
    app = web_server.WebApp(catalog_path=cfg.catalog_path, home=cfg.home, volumes_root=VOLUMES_ROOT,
                            runner=web_server.TaskRunner())
    httpd = web_server.make_server(app, port=args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    emit.emit("progress", phase="serve", message=f"PhotoVault at {url} (Ctrl-C stops it)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return EXIT_OK


def _metrics_human(rep: dict) -> dict:
    """Readable sizes and rates for the human report; JSON keeps the raw numbers."""
    def size(n: int) -> str:
        return human_bytes(n or 0)

    def rate(v: float | None) -> str:
        return f"{v:.1f} MB/s" if v is not None else "-"

    out: dict = {"store": rep["store"], "live_since": rep["live_since"] or "never (no I/O recorded yet)",
                 "totals": {"read": f"{size(rep['totals']['bytes_read'])} ({rep['totals']['files_read']} file reads)",
                            "written": f"{size(rep['totals']['bytes_written'])} ({rep['totals']['files_written']} files)",
                            "errors": rep["totals"]["errors"]},
                 "drives": [{"drive": d["label"], "read": size(d["bytes_read"]), "read_rate": rate(d["read_mbps"]),
                             "written": size(d["bytes_written"]), "write_rate": rate(d["write_mbps"]),
                             "errors": d["errors"], "last_day": d["last_day"]} for d in rep["drives"]],
                 "incidents": [{"ts": i["ts"], "drive": i["label"], "op": i["op"], "kind": i["kind"], "path": i["path"]}
                               for i in rep["incidents"]]}
    if "days" in rep:
        out["days"] = [{"day": d["day"], "drive": d["label"], "read": size(d["bytes_read"]),
                        "written": size(d["bytes_written"]), "errors": d["errors"]} for d in rep["days"]]
    return out


def cmd_metrics(args, cfg, emit) -> int:
    """Lifetime I/O per drive from <home>/metrics.sqlite (met-1, D-054). Read-only on the catalog."""
    rep = metrics.report(cfg.home, days=args.days)
    emit.emit("result", **(rep if emit.json_mode else _metrics_human(rep)))
    return EXIT_OK


def cmd_metrics_backfill(args, cfg, emit) -> int:
    """Rebuild the backfill rows from the catalog (work done before live counting began)."""
    if not cfg.catalog_path.exists():
        emit.emit("error", code="no_catalog", message=f"no catalog at {cfg.catalog_path}")
        return EXIT_ERROR
    emit.emit("result", **metrics.backfill(cfg.home, cfg.catalog_path))
    return EXIT_OK


def _day(text: str) -> str:
    """argparse type for --from/--to: a YYYY-MM-DD date."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise argparse.ArgumentTypeError("use YYYY-MM-DD")
    return text


def cmd_shoots_plan(args, cfg, emit) -> int:
    """Propose trips and shoots and write the plan file (sh-2). Reads the catalog; writes only <home>/plans/."""
    if args.gap_h <= 0:
        emit.emit("error", code="usage", message="--gap-h must be above 0")
        return EXIT_USAGE
    conn = _open(cfg)
    emit.emit("progress", phase="plan", message="planning shoots")
    path = shoots.write_plan(conn, cfg.home, gap_h=args.gap_h, frm=args.frm, to=args.to)
    result = json.loads(path.read_text(encoding="utf-8"))
    emit.emit("result", plan_id=result["plan_id"], path=str(path), decisions_through=result["decisions_through"],
              **result["summary"])
    return EXIT_OK


def cmd_shoots_apply(args, cfg, emit) -> int:
    """sh-4: create the accepted shoots and trips of a reviewed plan in the catalog. No file is touched."""
    if not re.fullmatch(r"shoots-\d{8}-\d{6}", args.plan):
        emit.emit("error", code="usage", message="--plan takes a plan id like shoots-20261118-210000")
        return EXIT_USAGE
    path = cfg.home / "plans" / f"{args.plan}.json"
    try:
        params = json.loads(path.read_text(encoding="utf-8")).get("params", {})
    except (OSError, ValueError) as exc:
        emit.emit("error", code="no_plan", message=f"{path}: {exc}")
        return EXIT_USAGE
    conn = _open(cfg)
    emit.emit("progress", phase="apply", message=f"applying {args.plan}")
    try:
        with repo.transaction(conn):
            out = shoots.apply(conn, params=params, expect=args.expect)
    except shoots.ApplyError as exc:
        emit.emit("error", code="apply_refused", message=str(exc))
        return EXIT_ERROR
    emit.emit("result", plan_id=args.plan, **{k: v for k, v in out.items() if k != "created"})
    return EXIT_OK


def cmd_shoots_undo(args, cfg, emit) -> int:
    """sh-4: undo one apply: frames get their previous shoot back, and the shoots and trips it made go."""
    conn = _open(cfg)
    try:
        with repo.transaction(conn):
            out = shoots.undo(conn, args.apply_id)
    except shoots.ApplyError as exc:
        emit.emit("error", code="undo_refused", message=str(exc))
        return EXIT_ERROR
    emit.emit("result", **out)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="photovault", description="Verified card imports and a multi-drive catalog.")
    p.add_argument("--json", action="store_true", help="emit JSON lines (protocol v1)")
    sub = p.add_subparsers(dest="cmd", required=True)

    cat = sub.add_parser("catalog").add_subparsers(dest="sub", required=True)
    cat.add_parser("init", help="create or migrate the catalog").set_defaults(func=cmd_catalog_init)

    d = sub.add_parser("drives", help="list mounted drives and their roles")
    d.add_argument("--root", help=argparse.SUPPRESS)
    d.set_defaults(func=cmd_drives)

    vol = sub.add_parser("volumes").add_subparsers(dest="sub", required=True)
    vp = vol.add_parser("probe", help="describe a mounted volume (read-only)")
    vp.add_argument("mount")
    vp.set_defaults(func=cmd_volumes_probe)
    vol.add_parser("list", help="volumes in the catalog").set_defaults(func=cmd_volumes_list)
    va = vol.add_parser("assign", help="give a drive a role (WORKING/MASTER write a small marker file)")
    va.add_argument("mount")
    va.add_argument("--role", required=True, choices=["WORKING", "MASTER", "LEGACY"])
    va.add_argument("--label")
    va.set_defaults(func=cmd_volumes_assign)

    ing = sub.add_parser("ingest", help="copy a card onto a WORKING drive with verified copies")
    ing.add_argument("--source", required=True, help="card mount, e.g. /Volumes/NIKON Z 6_2")
    ing.add_argument("--target", required=True, help="WORKING drive mount")
    ing.add_argument("--name", default=None, help="optional shoot name for the folder")
    ing.add_argument("--dry-run", action="store_true", help="plan and show, write nothing")
    ing.set_defaults(func=cmd_ingest)

    r = sub.add_parser("resume", help="resume a paused or failed job")
    r.add_argument("job_id", type=int)
    r.set_defaults(func=cmd_resume)

    cs = sub.add_parser("card-status", help="is this card safe to format? (exit 3 when red)")
    cs.add_argument("mount")
    cs.add_argument("--show", type=int, default=10, help="how many problem files to list")
    cs.set_defaults(func=cmd_card_status)

    crd = sub.add_parser("cards", help="camera cards: which files are safe to remove").add_subparsers(
        dest="sub", required=True)
    cc = crd.add_parser("check", help="index and hash a card (read-only; Ctrl-C pauses, re-run resumes)")
    cc.add_argument("mount")
    cc.set_defaults(func=cmd_cards_check)
    cl = crd.add_parser("list", help="every card, ranked green/yellow/red, and the cameras on them")
    cl.add_argument("--root", help=argparse.SUPPRESS)
    cl.set_defaults(func=cmd_cards_list)

    ix = sub.add_parser("index", help="record what is on a registered drive (read-only; Ctrl-C pauses, re-run resumes)")
    ix.add_argument("mount")
    ix.set_defaults(func=cmd_index)

    hs = sub.add_parser("hash", help="full SHA-256 of indexed files (read-only; Ctrl-C pauses, re-run resumes)")
    hs.add_argument("mount")
    hs.add_argument("--max-mbps", type=float, default=None, help="cap the average read rate in MB/s")
    hs.add_argument("--skip-prefix", action="append", metavar="FOLDER",
                    help="do not read files under this folder (relative to the drive; repeatable)")
    hs.add_argument("--retry-unreadable", action="store_true",
                    help="also retry files an earlier run marked unreadable (I/O error)")
    hs.set_defaults(func=cmd_hash)

    lr = sub.add_parser("lrcat").add_subparsers(dest="sub", required=True)
    lm = lr.add_parser("mark", help="flag files a Lightroom catalog references (reads the .lrcat read-only)")
    lm.add_argument("lrcat", help="path to the .lrcat (use a copy; refused while Lightroom is open)")
    lm.add_argument("--volume", required=True, help="registered drive the catalog's paths live on")
    lm.set_defaults(func=cmd_lrcat_mark)

    rp = sub.add_parser("report").add_subparsers(dest="sub", required=True)
    rd = rp.add_parser("duplicates", help="identical files across drives and what is only on one drive (read-only)")
    rd.add_argument("--volume", help="only clusters with a copy on this registered drive")
    rd.add_argument("--out", help="Markdown file to write (default: <home>/reports/duplicates-<time>.md)")
    rd.set_defaults(func=cmd_report_duplicates)

    mt = sub.add_parser("meta", help="geotags, creator and keywords for frames (catalog; sidecars on WORKING)")
    ms = mt.add_subparsers(dest="sub", required=True)
    m_scan = ms.add_parser("scan", help="read camera GPS and capture time of each frame on a drive (read-only)")
    m_scan.add_argument("--volume", required=True, metavar="MOUNT")
    m_scan.add_argument("--rescan", action="store_true", help="also re-read frames that already have metadata rows")
    m_scan.set_defaults(func=cmd_meta_scan)
    m_refs = ms.add_parser("refs", help="store GPS fixes of a reference folder (phone export, drone card)")
    m_refs.add_argument("--folder", required=True)
    m_refs.add_argument("--label", required=True, help="source name for these fixes, e.g. phone or drone")
    m_refs.set_defaults(func=cmd_meta_refs)
    m_phone = ms.add_parser("phone", help="import iPhone/iPad/glasses locations from an osxphotos JSON export")
    m_phone.add_argument("json", metavar="JSON", help="file written by `osxphotos query --json` (array or JSON lines)")
    m_phone.set_defaults(func=cmd_meta_phone)
    m_side = ms.add_parser("sidecars", help="write XMP sidecars for changed frames on a WORKING drive")
    m_side.add_argument("--volume", required=True, metavar="MOUNT")
    m_side.add_argument("--dry-run", action="store_true", help="count what would be written, write nothing")
    m_side.set_defaults(func=cmd_meta_sidecars)
    m_miss = ms.add_parser("missing", help="frames lacking a geotag, creator or keywords")
    m_miss.add_argument("--volume", metavar="MOUNT", help="only frames with a copy on this registered drive")
    m_miss.set_defaults(func=cmd_meta_missing)
    m_set = ms.add_parser("set", help="edit frames by group id (for scripting)")
    m_set.add_argument("--group", required=True, type=int, nargs="+", metavar="ID")
    m_set.add_argument("--lat", type=float, help="manual geotag latitude (needs --lon; source 'map')")
    m_set.add_argument("--lon", type=float)
    m_set.add_argument("--place", help="place name")
    m_set.add_argument("--city")
    m_set.add_argument("--state")
    m_set.add_argument("--country")
    m_set.add_argument("--creator")
    m_set.add_argument("--copyright")
    m_set.add_argument("--title")
    m_set.add_argument("--keyword-add", action="append", metavar="WORD", help="repeatable")
    m_set.add_argument("--time-shift", type=int, metavar="SECONDS", help="correct a body's clock by this many seconds")
    m_set.set_defaults(func=cmd_meta_set)

    sv = sub.add_parser("serve", help="local web UI on 127.0.0.1 (Ctrl-C stops it)")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--no-open", action="store_true", help="do not open the browser")
    sv.set_defaults(func=cmd_serve)

    mx = sub.add_parser("metrics", help="lifetime bytes read and written per drive, speeds and read errors")
    mx.add_argument("--days", type=int, default=0, metavar="N", help="also show a per-day table for the last N days")
    mx.set_defaults(func=cmd_metrics)
    mxs = mx.add_subparsers(dest="sub")
    mxs.add_parser("backfill", help="estimate past work from the catalog (before live counting began)").set_defaults(
        func=cmd_metrics_backfill)

    sh = sub.add_parser("shoots", help="trips and shoots proposed by time and place").add_subparsers(
        dest="sub", required=True)
    shp = sh.add_parser("plan", help="propose trips and shoots for review (reads the catalog; writes a plan file)")
    shp.add_argument("--from", dest="frm", type=_day, metavar="YYYY-MM-DD", help="first capture day to plan")
    shp.add_argument("--to", type=_day, metavar="YYYY-MM-DD", help="last capture day to plan")
    shp.add_argument("--gap-h", type=float, default=8, help="hours without photos that start a new shoot (8)")
    shp.set_defaults(func=cmd_shoots_plan)
    sha = sh.add_parser("apply", help="create the accepted shoots of a reviewed plan (catalog only; undo with `shoots undo`)")
    sha.add_argument("--plan", required=True, metavar="PLAN_ID", help="the reviewed plan, e.g. shoots-20261118-210000")
    sha.add_argument("--expect", type=int, help="refuse unless this many shoots are accepted (what the review showed)")
    sha.set_defaults(func=cmd_shoots_apply)
    shu = sh.add_parser("undo", help="undo one apply (catalog only)")
    shu.add_argument("apply_id", help="the apply id, e.g. apply-20261118-210500")
    shu.set_defaults(func=cmd_shoots_undo)

    j = sub.add_parser("jobs", help="recent jobs")
    j.add_argument("--limit", type=int, default=20)
    j.set_defaults(func=cmd_jobs)
    return p


JOB_COMMANDS = frozenset({"ingest", "resume", "index", "hash", "cards"})


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    # ActivityEmitter also keeps <home>/activity/<pid>.json current, so the web UI can show this run (ui-11).
    emit = ActivityEmitter(sys.stdout, json_mode=args.json)
    recorder: metrics.Recorder | None = None
    code: int | None = None
    try:
        cfg = load_config()
        argv_list = list(sys.argv[1:] if argv is None else argv)
        emit.track(cfg.home, argv_list)
        if args.cmd in JOB_COMMANDS and cfg.catalog_path.exists():
            # idx-9: a job left `running` by a run that died is set to paused before this one starts.
            conn = _open(cfg)
            try:
                activity.pause_ownerless(conn, cfg.home, emit=emit)
            finally:
                conn.close()
        if args.cmd not in ("serve", "metrics"):
            # Lifetime I/O metrics (met-1, D-054): a no-op for commands that read and write nothing.
            recorder = metrics.Recorder(cfg.home, cfg.catalog_path, command=" ".join(argv_list),
                                        volumes_root=VOLUMES_ROOT)
            metrics.install(recorder)
        code = args.func(args, cfg, emit)
        return code
    except (ValueError, ConfigError, FileExistsError, RuntimeError, OSError, volumes.CardWriteRefused,
            volumes.UnknownVolume, CatalogInUse, LrcatSchemaError) as exc:
        emit.emit("error", code=type(exc).__name__, message=str(exc))
        code = EXIT_ERROR
        return code
    finally:
        if recorder is not None:
            metrics.install(None)
            recorder.close(code)
        emit.close()


if __name__ == "__main__":
    sys.exit(main())
