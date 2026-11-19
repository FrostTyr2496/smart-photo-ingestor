"""sh-2: `shoots plan` on the CLI, the plan API and the review decisions stored in the catalog. Names are invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from photovault import cli, shoots
from photovault.catalog import db
from photovault.config import load_config
from photovault.web import server as web
from tests.test_cli import run, result
from tests.test_shoots import Builder, d
from tests.test_web_api import Client, srv  # noqa: F401  (the server fixture)


def seed(path: Path) -> list[int]:
    conn = db.connect(path)
    db.migrate(conn)
    conn.execute("INSERT INTO volumes (volume_id, role, label, created_at, last_seen_at) "
                 "VALUES ('v1', 'WORKING', 'W', 'x', 'x')")
    b = Builder(conn)
    ids = b.frames(d(9, 20), 30, path="Photos/Dunes")
    b.frames(d(12, 20), 10)
    conn.close()
    return ids


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("PHOTOVAULT_HOME", str(h))
    monkeypatch.delenv("PHOTOVAULT_DEV_PLAN", raising=False)
    return h


# --------------------------------------------------------------------------- the CLI

def test_cli_writes_the_plan_file_and_prints_the_summary(home, capsys) -> None:
    seed(load_config().catalog_path)
    code, lines = run(capsys, "shoots", "plan", "--gap-h", "6")
    assert code == 0
    r = result(lines)
    path = Path(r["path"])
    assert path.parent == home / "plans" and path.name == f"{r['plan_id']}.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    assert plan["params"]["gap_h"] == 6 and plan["decisions_through"] == 0
    assert r["shoots"] == plan["summary"]["shoots"] == 2 and r["decisions_through"] == 0


def test_cli_rejects_bad_dates_and_gaps(home, capsys) -> None:
    assert cli.main(["--json", "shoots", "plan", "--from", "9 Sep"]) == 2
    capsys.readouterr()
    code, lines = run(capsys, "shoots", "plan", "--gap-h", "0")
    assert code != 0 and lines[-1]["event"] == "error"


def test_build_argv_for_shoots_plan() -> None:
    assert web.build_argv("shoots_plan", {}) == ["shoots", "plan"]
    argv = web.build_argv("shoots_plan", {"from": "2023-09-01", "to": "2023-09-30", "gap_h": 6})
    assert argv == ["shoots", "plan", "--from", "2023-09-01", "--to", "2023-09-30", "--gap-h", "6"]
    cli.build_parser().parse_args(["--json", *argv])
    for bad in ({"from": "-x"}, {"from": "Sept"}, {"gap_h": 0}, {"gap_h": True}, {"other": 1}):
        with pytest.raises(web.BadRequest):
            web.build_argv("shoots_plan", bad)


# --------------------------------------------------------------------------- decisions in the catalog

@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "c.sqlite")
    db.migrate(c)
    c.execute("INSERT INTO volumes (volume_id, role, label, created_at, last_seen_at) "
              "VALUES ('v1', 'WORKING', 'W', 'x', 'x')")
    yield c
    c.close()


def test_decisions_round_trip_and_the_plan_records_what_it_folded_in(conn) -> None:
    b = Builder(conn)
    ids = b.frames(d(9, 20), 30, path="Photos/Dunes")
    b.frames(d(12, 20), 10)
    first = shoots.plan(conn)["shoots"][0]["proposal_id"]
    rows = shoots.add_decisions(conn, [
        {"proposal_id": first, "kind": "split", "payload": {"after_group_id": ids[9]}},
        {"proposal_id": first, "kind": "rename", "payload": {"value": "Harbour Moonrise"}},
    ])
    assert [r["kind"] for r in rows] == ["split", "rename"]
    p = shoots.plan(conn)
    assert p["decisions_through"] == rows[-1]["decision_id"] and len(p["shoots"]) == 3
    assert shoots.list_decisions(conn, after=rows[0]["decision_id"]) == [rows[1]]
    assert shoots.delete_decisions(conn, [rows[0]["decision_id"]]) == 1
    assert len(shoots.plan(conn)["shoots"]) == 2


@pytest.mark.parametrize("bad", [
    {"proposal_id": "x", "kind": "accept"},
    {"proposal_id": "s:abcdef12", "kind": "accept_trip"},
    {"proposal_id": "s:abcdef12", "kind": "split", "payload": {}},
    {"proposal_id": "s:abcdef12", "kind": "split", "payload": {"after_group_id": True}},
    {"proposal_id": "s:abcdef12", "kind": "merge", "payload": {"with": "left"}},
    {"proposal_id": "s:abcdef12", "kind": "rename", "payload": {"value": "  "}},
    {"proposal_id": "s:abcdef12", "kind": "move", "payload": {"trip_id": "s:abcdef12"}},
    {"proposal_id": "s:abcdef12", "kind": "accept", "payload": {"sneaky": 1}},
    {"proposal_id": "t:abcdef12", "kind": "accept"},
    {"proposal_id": "s:abcdef12", "kind": "accept", "payload": []},
])
def test_bad_decisions_are_refused(conn, bad) -> None:
    with pytest.raises(ValueError):
        shoots.check_decision(bad)


# --------------------------------------------------------------------------- the API

def test_api_stores_lists_and_undoes_decisions(srv: Client, monkeypatch) -> None:  # noqa: F811
    monkeypatch.delenv("PHOTOVAULT_DEV_PLAN", raising=False)
    one = {"proposal_id": "s:abcdef1234567890", "kind": "accept", "payload": {}}
    status, body = srv.call("POST", "/api/shoots/decisions", one)
    assert status == 200 and body["decisions"][0]["kind"] == "accept"
    first = body["decisions"][0]["decision_id"]
    batch = [{"proposal_id": f"s:{c * 16}", "kind": "accept", "payload": {"batch": "b1", "ui": "accept_trip",
                                                                         "trip": "t:1234567890abcdef"}}
             for c in "ab"]
    status, body = srv.call("POST", "/api/shoots/decisions", {"decisions": batch})
    assert status == 200 and [x["payload"]["batch"] for x in body["decisions"]] == ["b1", "b1"]
    listed = srv.call("GET", "/api/shoots/decisions")[1]["decisions"]
    assert len(listed) == 3
    assert len(srv.call("GET", f"/api/shoots/decisions?after={first}")[1]["decisions"]) == 2
    # all or none: one bad row refuses the batch
    status, _ = srv.call("POST", "/api/shoots/decisions", {"decisions": [one, {"proposal_id": "s:1", "kind": "x"}]})
    assert status == 400 and len(srv.call("GET", "/api/shoots/decisions")[1]["decisions"]) == 3
    ids = [x["decision_id"] for x in listed[1:]]
    assert srv.call("POST", "/api/shoots/decisions/delete", {"decision_ids": ids}) == (200, {"deleted": 2})
    assert srv.call("POST", "/api/shoots/decisions/delete", {"decision_ids": ["1"]})[0] == 400
    assert [x["decision_id"] for x in srv.call("GET", "/api/shoots/decisions")[1]["decisions"]] == [first]


def test_api_dev_plan_keeps_decisions_out_of_the_catalog(srv: Client, monkeypatch, tmp_path) -> None:  # noqa: F811
    dev = tmp_path / "dev.json"
    dev.write_text(json.dumps({"plan_id": "dev"}), encoding="utf-8")
    monkeypatch.setenv("PHOTOVAULT_DEV_PLAN", str(dev))
    status, body = srv.call("POST", "/api/shoots/decisions",
                            {"proposal_id": "s:abcdef12", "kind": "reject_name", "payload": {"value": "Dunes"}})
    assert status == 200
    listed = srv.call("GET", "/api/shoots/decisions")[1]
    assert listed["dev"] is True and len(listed["decisions"]) == 1
    conn = db.connect(srv.app.catalog_path)
    assert conn.execute("SELECT COUNT(*) FROM shoot_plan_decisions").fetchone()[0] == 0
    conn.close()


def test_api_plan_starts_a_task(srv: Client, monkeypatch) -> None:  # noqa: F811
    seen: list[list[str]] = []
    monkeypatch.setattr(srv.app.runner, "start", lambda argv: seen.append(argv) or 7)
    assert srv.call("POST", "/api/shoots/plan", {"gap_h": 6}) == (200, {"task_id": 7})
    assert seen == [["shoots", "plan", "--gap-h", "6"]]
    assert srv.call("POST", "/api/shoots/plan", {"gap_h": -1})[0] == 400
    assert srv.call("POST", "/api/run", {"cmd": "shoots_plan", "args": {}}) == (200, {"task_id": 7})
