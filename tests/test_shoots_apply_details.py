"""sh-8: Apply writes the details the user set."""

from __future__ import annotations

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
