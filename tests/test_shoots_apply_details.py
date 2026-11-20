"""sh-8: Apply writes the details the user set."""

from __future__ import annotations

from photovault import shoots
from photovault.catalog import repo
from tests.test_shoots_apply import applied, conn, decide, three  # noqa: F401  (fixtures and helpers)


def test_apply_writes_user_details(conn) -> None:  # noqa: F811
    _, p = three(conn)
    s0 = p["shoots"][0]
    decide(conn, (s0["proposal_id"], "accept", {}),
           (s0["proposal_id"], "detail", {"field": "subject", "value": "Dune astro"}),
           (s0["proposal_id"], "detail", {"field": "place", "value": "Dune Lookout"}))
    applied(conn, expect=1)
    r = conn.execute("SELECT place, subject, subject_source, confirmed_at FROM shoots").fetchone()
    assert (r["place"], r["subject"], r["subject_source"], r["confirmed_at"]) == (
        "Dune Lookout", "Dune astro", "user", None)


def test_confirmed_accept_sets_confirmed_at_and_removal_follows_undo(conn) -> None:  # noqa: F811
    _, p = three(conn)
    s0, s1 = p["shoots"][0], p["shoots"][2]
    decide(conn, (s0["proposal_id"], "accept", {"confirmed": True}), (s1["proposal_id"], "accept", {}))
    assert shoots.plan(conn)["params"]["pretick_min"] == 0.5
    r = applied(conn, expect=2)
    rows = [x["confirmed_at"] for x in conn.execute("SELECT confirmed_at FROM shoots")]
    assert sum(v is not None for v in rows) == 1
    with repo.transaction(conn):
        shoots.undo(conn, r["apply_id"])
    assert conn.execute("SELECT COUNT(*) FROM shoots").fetchone()[0] == 0
