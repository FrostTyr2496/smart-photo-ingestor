"""PhotoVault CLI (fnd-6, ing-9). Human output by default; `--json` gives JSON lines (system-design §10)."""

from __future__ import annotations

import argparse
import shutil
import signal
import sqlite3
import sys
import uuid
from pathlib import Path

from photovault import volumes
from photovault.catalog import db, repo
from photovault.config import ConfigError, load_config
from photovault.index import index_volume
from photovault.ingest import plan_card_ingest, run_job
from photovault.protocol import EXIT_ERROR, EXIT_OK, EXIT_PAUSED, EXIT_RED, EXIT_USAGE, Emitter, human_bytes
from photovault.status import card_status

VOLUMES_ROOT = Path("/Volumes")
SKIP_MOUNTS = {"Recovery", "Macintosh HD"}


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
    rows = []
    root = Path(args.root) if args.root else VOLUMES_ROOT
    for mount in sorted(root.iterdir()):
        if mount.is_symlink() or not mount.is_dir() or mount.name in SKIP_MOUNTS:
            continue
        try:
            p = volumes.probe(mount)
        except (RuntimeError, OSError, ValueError) as exc:
            emit.emit("warning", code="probe", message=f"{mount}: {exc}")
            continue
        row = repo.find_volume(conn, marker_id=p.marker_id, da_volume_uuid=p.da_volume_uuid)
        try:
            free = human_bytes(shutil.disk_usage(mount).free)
        except OSError:
            free = "?"
        role = row["role"] if row else ("CARD (new)" if p.looks_like_card else "unassigned")
        rows.append({"mount": str(mount), "role": role, "label": row["label"] if row else "", "fs": p.fs_type,
                     "size": human_bytes(p.capacity_bytes or 0), "free": free})
    emit.emit("result", drives=rows)
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


def cmd_index(args, cfg, emit) -> int:
    conn = _open(cfg)
    stop = _Stop()
    stop.install(emit)
    job_id = index_volume(conn, cfg, Path(args.mount), emit=emit, should_stop=stop)
    return EXIT_PAUSED if repo.get_job(conn, job_id)["state"] == "paused" else EXIT_OK


def cmd_jobs(args, cfg, emit) -> int:
    conn = _open(cfg)
    rows = []
    for r in repo.list_jobs(conn, args.limit):
        rows.append({"job": r["job_id"], "state": r["state"], "created": r["created_at"], "items": r["items"],
                     "committed": r["committed"] or 0, "dup": r["skipped_dup"] or 0, "failed": r["failed"] or 0,
                     "pending": r["pending"] or 0})
    emit.emit("result", jobs=rows)
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

    ix = sub.add_parser("index", help="record what is on a registered drive (read-only; Ctrl-C pauses, re-run resumes)")
    ix.add_argument("mount")
    ix.set_defaults(func=cmd_index)

    j = sub.add_parser("jobs", help="recent jobs")
    j.add_argument("--limit", type=int, default=20)
    j.set_defaults(func=cmd_jobs)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    emit = Emitter(sys.stdout, json_mode=args.json)
    try:
        cfg = load_config()
        return args.func(args, cfg, emit)
    except (ValueError, ConfigError, FileExistsError, RuntimeError, OSError, volumes.CardWriteRefused,
            volumes.UnknownVolume) as exc:
        emit.emit("error", code=type(exc).__name__, message=str(exc))
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
