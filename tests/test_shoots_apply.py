"""sh-4: Apply creates the accepted shoots and trips in the catalog, and Undo puts every frame back. Names are invented."""

from __future__ import annotations

import json

import pytest

from photovault import cli, shoots
from photovault.catalog import db, repo
from photovault.config import load_config
from photovault.web import server as web
from tests.test_cli import result, run
from tests.test_shoots import Builder, d
from tests.test_web_api import Client, srv  # noqa: F401  (the server fixture)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "c.sqlite")
    db.migrate(c)
    c.execute("INSERT INTO volumes (volume_id, role, label, created_at, last_seen_at) "
              "VALUES ('v1', 'WORKING', 'W', 'x', 'x')")
    yield c
    c.close()


def three(conn) -> tuple[Builder, dict]:
    """Two nights in one place (a trip) and a lone shoot two weeks later."""
    b = Builder(conn)
    b.frames(d(9, 20), 5, path="Photos/Dunes", lat=1.0, lon=1.0)
    b.frames(d(10, 20), 5, path="Photos/Dunes", lat=1.0, lon=1.0)
    b.frames(d(25, 20), 5)
    return b, shoots.plan(conn)


def decide(conn, *rows) -> None:
    with repo.transaction(conn):
        shoots.add_decisions(conn, [{"proposal_id": pid, "kind": kind, "payload": payload} for pid, kind, payload in rows])


def applied(conn, **kw) -> dict:
    with repo.transaction(conn):
        return shoots.apply(conn, **kw)


def test_apply_creates_accepted_shoots_and_their_trip(conn) -> None:
    _, p = three(conn)
    s0, s1, s2 = p["shoots"]
    assert s0["trip_id"] == s1["trip_id"] and s0["trip_id"] is not None
    decide(conn, (s0["proposal_id"], "accept", {}), (s1["proposal_id"], "rename", {"value": "Harbour Moonrise"}),
           (s1["proposal_id"], "accept", {}))
    out = applied(conn, expect=2)
    assert (out["shoots"], out["trips"], out["frames"]) == (2, 1, 10)
    rows = conn.execute("SELECT name, shoot_date, trip_id, name_source, local_offset FROM shoots ORDER BY shoot_date").fetchall()
    assert [r["name"] for r in rows][1] == "Harbour Moonrise" and rows[1]["name_source"] == "user"
    assert rows[0]["trip_id"] == rows[1]["trip_id"] is not None and rows[0]["local_offset"] is None
    assert conn.execute("SELECT COUNT(*) FROM capture_groups WHERE shoot_id IS NOT NULL").fetchone()[0] == 10
    assert conn.execute("SELECT COUNT(*) FROM shoot_apply_log WHERE job_id = ?", (out["apply_id"],)).fetchone()[0] == 10
    assert conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 1
    # later plans show them locked, inside their applied trip; the lone shoot is still open
    p2 = shoots.plan(conn)
    locked = [s for s in p2["shoots"] if s["locked"]]
    assert len(locked) == 2 and {s["name"]["value"] for s in locked} >= {"Harbour Moonrise"}
    trip = next(t for t in p2["trips"] if t.get("locked"))
    assert trip["shoot_ids"] == [s["proposal_id"] for s in locked] and all(s["trip_id"] == trip["proposal_id"] for s in locked)
    assert [s["proposal_id"] for s in p2["shoots"] if not s["locked"]] == [s2["proposal_id"]]


def test_apply_refuses_when_the_review_is_out_of_date_or_empty(conn) -> None:
    _, p = three(conn)
    with pytest.raises(shoots.ApplyError, match="no accepted"):
        applied(conn)
    decide(conn, (p["shoots"][2]["proposal_id"], "accept", {}))
    with pytest.raises(shoots.ApplyError, match="re-plan"):
        applied(conn, expect=2)
    assert conn.execute("SELECT COUNT(*) FROM shoots").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM shoot_apply_log").fetchone()[0] == 0


def test_apply_keeps_names_unique_per_day(conn) -> None:
    _, p = three(conn)
    s2 = p["shoots"][2]
    conn.execute("INSERT INTO shoots (name, shoot_date, created_at) VALUES ('Dock at Dawn', ?, 'x')", (s2["shoot_date"],))
    decide(conn, (s2["proposal_id"], "rename", {"value": "Dock at Dawn"}), (s2["proposal_id"], "accept", {}))
    out = applied(conn)
    assert out["renamed"] == 1 and out["created"][0]["name"] == "Dock at Dawn 2"


def test_apply_stores_a_proposed_time_zone(conn, monkeypatch) -> None:
    _, p = three(conn)
    real = shoots.plan

    def with_tz(c, **kw):
        q = real(c, **kw)
        for s in q["shoots"]:
            s["local_offset"] = {"value": "-07:00", "camera": "+00:00", "source": "phone", "changes": True}
        return q

    monkeypatch.setattr(shoots, "plan", with_tz)
    decide(conn, (p["shoots"][2]["proposal_id"], "accept", {}))
    applied(conn)
    assert conn.execute("SELECT local_offset FROM shoots").fetchone()[0] == "-07:00"


def test_undo_puts_every_frame_back_and_removes_what_apply_made(conn) -> None:
    _, p = three(conn)
    before = conn.execute("SELECT group_id, shoot_id FROM capture_groups ORDER BY group_id").fetchall()
    decide(conn, *[(s["proposal_id"], "accept", {}) for s in p["shoots"]])
    first = applied(conn)
    assert shoots.list_applies(conn) == [{"apply_id": first["apply_id"], "frames": 15, "shoots": 3}]
    with repo.transaction(conn):
        out = shoots.undo(conn, first["apply_id"])
    assert (out["frames"], out["shoots"], out["trips"]) == (15, 3, 1)
    assert conn.execute("SELECT group_id, shoot_id FROM capture_groups ORDER BY group_id").fetchall() == before
    for table in ("shoots", "trips", "shoot_apply_log"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    with pytest.raises(shoots.ApplyError):
        shoots.undo(conn, first["apply_id"])
    # the decisions are still there, so the same review applies again
    assert applied(conn)["shoots"] == 3


def test_photo_days_name_the_shoots_and_filter_by_one(conn) -> None:
    _, p = three(conn)
    decide(conn, (p["shoots"][0]["proposal_id"], "rename", {"value": "Harbour Moonrise"}),
           (p["shoots"][0]["proposal_id"], "accept", {}))
    sid = applied(conn)["created"][0]["shoot_id"]
    days = {x["day"]: x for x in repo.list_photo_days(conn)}
    assert days["2023-09-09"]["shoots"] == [{"shoot_id": sid, "name": "Harbour Moonrise", "groups": 5}]
    assert days["2023-09-10"]["shoots"] == []
    only = repo.list_photo_days(conn, shoot_id=sid)
    assert [x["day"] for x in only] == ["2023-09-09"] and only[0]["groups"] == 5


# --------------------------------------------------------------------------- CLI and API

def test_cli_apply_and_undo(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    cfg = load_config()
    c = db.connect(cfg.catalog_path)
    db.migrate(c)
    c.execute("INSERT INTO volumes (volume_id, role, label, created_at, last_seen_at) VALUES ('v1','WORKING','W','x','x')")
    _, p = three(c)
    decide(c, (p["shoots"][2]["proposal_id"], "accept", {}))
    c.close()
    code, lines = run(capsys, "shoots", "plan")
    plan_id = result(lines)["plan_id"]
    code, lines = run(capsys, "shoots", "apply", "--plan", plan_id, "--expect", "2")
    assert code != 0 and "re-plan" in lines[-1]["message"]
    code, lines = run(capsys, "shoots", "apply", "--plan", plan_id, "--expect", "1")
    r = result(lines)
    assert code == 0 and (r["shoots"], r["frames"]) == (1, 5) and "created" not in r
    code, lines = run(capsys, "shoots", "undo", r["apply_id"])
    assert code == 0 and result(lines)["frames"] == 5
    assert run(capsys, "shoots", "apply", "--plan", "nope")[0] != 0


def test_build_argv_for_apply_and_undo() -> None:
    argv = web.build_argv("shoots_apply", {"plan_id": "shoots-20261118-210000", "expect": 3})
    assert argv == ["shoots", "apply", "--plan", "shoots-20261118-210000", "--expect", "3"]
    cli.build_parser().parse_args(["--json", *argv])
    assert web.build_argv("shoots_undo", {"apply_id": "apply-20261118-210500-2"}) == ["shoots", "undo", "apply-20261118-210500-2"]
    for cmd, bad in (("shoots_apply", {"plan_id": "../x"}), ("shoots_apply", {"plan_id": "shoots-20261118-210000", "expect": 0}),
                     ("shoots_undo", {"apply_id": "-rf"}), ("shoots_undo", {})):
        with pytest.raises(web.BadRequest):
            web.build_argv(cmd, bad)


def test_api_apply_undo_and_list(srv: Client, monkeypatch) -> None:  # noqa: F811
    monkeypatch.delenv("PHOTOVAULT_DEV_PLAN", raising=False)
    seen: list[list[str]] = []
    monkeypatch.setattr(srv.app.runner, "start", lambda argv: seen.append(argv) or 9)
    assert srv.call("POST", "/api/shoots/apply", {"plan_id": "shoots-20261118-210000", "expect": 2}) == (200, {"task_id": 9})
    assert srv.call("POST", "/api/shoots/undo", {"job_id": "apply-20261118-210500"}) == (200, {"task_id": 9})
    assert seen == [["shoots", "apply", "--plan", "shoots-20261118-210000", "--expect", "2"],
                    ["shoots", "undo", "apply-20261118-210500"]]
    assert srv.call("GET", "/api/shoots/applies") == (200, {"applies": []})
    monkeypatch.setenv("PHOTOVAULT_DEV_PLAN", "x.json")
    assert srv.call("POST", "/api/shoots/apply", {"plan_id": "shoots-20261118-210000"})[0] == 400
    assert len(seen) == 2


def test_api_photo_days_take_a_shoot(srv: Client) -> None:  # noqa: F811
    status, body = srv.call("GET", "/api/photo-days?shoot_id=1")
    assert status == 200 and all("shoots" in x for x in body["days"])
    assert srv.call("GET", "/api/photo-days?shoot_id=x")[0] == 400
