"""Test card mode (tc-1, D-111): a marked card's files ignore verified copies on the hidden drives."""

from __future__ import annotations

from pathlib import Path

import pytest

from photovault import card_clear, cards, targets, testcard
from photovault.catalog import repo
from tests.test_card_clear import build, clear, conn_  # noqa: F401  (build via the env fixture)
from tests.test_card_clear import env  # noqa: F401
from tests.test_cli import result, run

CARD = "DCIM/100NZ6_2/DSC_{:04d}.NEF"


def tiers(conn) -> dict[int, str]:
    cid = next(v["volume_id"] for v in repo.list_volumes(conn) if v["role"] == "CARD")
    files = {f["rel_path"]: f["tier"] for f in cards.card_files(conn, cid)}
    return {n: files[CARD.format(n)] for n in (1, 3, 4, 5)}


def mark(capsys, env, *hide: str):
    args = ["test-card", "mark", str(env["card"])]
    for h in hide:
        args += ["--hide", h]
    return run(capsys, *args)


def test_baseline_without_a_test_card(env) -> None:
    assert tiers(conn_()) == {1: "backed_up", 3: "needs_backup", 4: "only_on_card", 5: "needs_backup"}


def test_hidden_copies_turn_files_red_or_yellow(env, capsys) -> None:
    code, lines = mark(capsys, env, "WORK2")
    assert code == 0, lines
    assert result(lines)["test_card"]["hidden"][0]["label"] == "WORK2"
    # frame 1 had WORK + WORK2: now one copy (yellow); frame 3 is on WORK only: unchanged
    assert tiers(conn_()) == {1: "needs_backup", 3: "needs_backup", 4: "only_on_card", 5: "needs_backup"}
    assert mark(capsys, env, "WORK", "label-that-does-not-exist")[0] == 2  # nothing changed by a refused mark
    assert tiers(conn_())[1] == "needs_backup"
    assert mark(capsys, env, "WORK", "WORK2", "OLD")[0] == 0
    assert set(tiers(conn_()).values()) == {"only_on_card"}  # every copy hidden: red; LEGACY never counted
    c = conn_()
    assert targets.card_unbacked_bytes(c, c.execute("SELECT volume_id FROM volumes WHERE role='CARD'").fetchone()[0]) > 0


def test_cannot_hide_a_card_and_show_and_clear(env, capsys) -> None:
    code, lines = run(capsys, "test-card", "mark", str(env["card"]), "--hide", "card:CARD1")
    assert code == 2 and "card" in lines[-1]["message"]
    assert testcard.load(conn_()) is None
    assert mark(capsys, env, "WORK", "WORK2")[0] == 0
    code, lines = run(capsys, "test-card", "show")
    assert result(lines)["test_card"]["card"] == "card:CARD1"
    code, lines = run(capsys, "test-card", "clear")
    assert code == 0 and result(lines)["test_card"] is None
    assert tiers(conn_()) == {1: "backed_up", 3: "needs_backup", 4: "only_on_card", 5: "needs_backup"}


def test_card_listing_flags_the_test_card(env, capsys) -> None:
    assert mark(capsys, env, "WORK2")[0] == 0
    c = conn_()
    listing = cards.list_cards(c, [])
    row = next(r for r in listing["cards"] if r["label"] == "card:CARD1")
    assert row["test_card"] == {"hidden": ["WORK2"]}


def test_clear_plans_nothing_for_hidden_only_files(env, capsys) -> None:
    assert result(clear(capsys, env, "--dry-run")[1])["deletable_files"] == 3
    assert mark(capsys, env, "WORK", "WORK2")[0] == 0
    plan = result(clear(capsys, env, "--dry-run")[1])
    assert plan["deletable_files"] == 0
    code, lines = clear(capsys, env, "--confirm", "0")
    assert code == 2


def test_clear_recheck_refuses_when_copies_get_hidden_after_planning(env, capsys) -> None:
    c = conn_()
    card = next(v for v in repo.list_volumes(c) if v["role"] == "CARD")
    inst = repo.get_instance(c, card["volume_id"], CARD.format(1))
    item = {"rel_path": inst["rel_path"], "asset_id": inst["asset_id"]}
    wk = {v["label"]: v["volume_id"] for v in repo.list_volumes(c)}
    connected = {wk["WORK"]: env["vols"] / "WORK"}
    card_clear._check_file(c, card, env["card"], item, connected, verify_all=False)  # passes: two verified copies, WORK re-read
    assert mark(capsys, env, "WORK2")[0] == 0
    c = conn_()
    with pytest.raises(card_clear.Surprise, match="two verified copies"):
        card_clear._check_file(c, card, env["card"], item, connected, verify_all=False)


def test_ingest_copies_instead_of_skipping_when_copies_are_hidden(env, capsys, tmp_path) -> None:
    root = env["vols"] / "TGT"
    (root / "Photos").mkdir(parents=True)
    assert run(capsys, "volumes", "assign", str(root), "--role", "WORKING", "--label", "TGT")[0] == 0
    assert mark(capsys, env, "WORK", "WORK2")[0] == 0
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(root))
    assert code == 0, lines
    states = [r["state"] for r in conn_().execute("SELECT state FROM job_items")]
    assert "skipped_dup" not in states and "committed" in states, states


def test_ingest_skips_without_a_test_card(env, capsys) -> None:
    root = env["vols"] / "TGT"
    (root / "Photos").mkdir(parents=True)
    assert run(capsys, "volumes", "assign", str(root), "--role", "WORKING", "--label", "TGT")[0] == 0
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(root))
    assert code == 0, lines
    assert "skipped_dup" in [r["state"] for r in conn_().execute("SELECT state FROM job_items")]


def test_recommend_never_picks_a_hidden_drive_for_the_test_card(env, capsys) -> None:
    c = conn_()
    mounted = {v["volume_id"]: str(env["vols"] / v["label"]) for v in repo.list_volumes(c) if v["role"] == "WORKING"}
    card = next(v["volume_id"] for v in repo.list_volumes(c) if v["role"] == "CARD")
    labels = lambda rec: {x["label"] for x in rec["candidates"]}  # noqa: E731
    assert labels(targets.recommend(c, None, mounted, planned_bytes=1, source_volume_id=card)) == {"WORK", "WORK2"}
    assert mark(capsys, env, "WORK2")[0] == 0
    c = conn_()
    assert labels(targets.recommend(c, None, mounted, planned_bytes=1, source_volume_id=card)) == {"WORK"}
    assert labels(targets.recommend(c, None, mounted, planned_bytes=1, source_volume_id=None)) == {"WORK", "WORK2"}


def test_make_green_ignores_hidden_only_files(env, capsys) -> None:
    from photovault import backup
    c = conn_()
    mounted = {v["volume_id"]: str(env["vols"] / v["label"]) for v in repo.list_volumes(c) if v["role"] == "WORKING"}
    card = next(v["volume_id"] for v in repo.list_volumes(c) if v["role"] == "CARD")
    before = backup.plan_make_green(c, None, card_volume_id=card, mounted=mounted, home=None)["files"]
    assert before > 0
    assert mark(capsys, env, "WORK", "WORK2", "OLD")[0] == 0  # all red: no yellow file, nothing to make green
    plan = backup.plan_make_green(conn_(), None, card_volume_id=card, mounted=mounted, home=None)
    assert plan["files"] == 0 and not plan["possible"]


def test_ingest_hides_copies_for_card_files_not_hashed_yet(env, capsys) -> None:
    """A test-card file the catalog has not linked to its asset yet must not be skipped as a duplicate either."""
    root = env["vols"] / "TGT"
    (root / "Photos").mkdir(parents=True)
    assert run(capsys, "volumes", "assign", str(root), "--role", "WORKING", "--label", "TGT")[0] == 0
    assert mark(capsys, env, "WORK", "WORK2")[0] == 0
    c = conn_()
    cid = next(v["volume_id"] for v in repo.list_volumes(c) if v["role"] == "CARD")
    with repo.transaction(c):
        c.execute("UPDATE instances SET asset_id = NULL, state = 'indexed' WHERE volume_id = ?", (cid,))
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(root))
    assert code == 0, lines
    states = [r["state"] for r in conn_().execute("SELECT state FROM job_items")]
    assert "skipped_dup" not in states and "committed" in states, states
