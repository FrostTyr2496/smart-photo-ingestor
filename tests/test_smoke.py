"""Scaffold checks: the package imports, and the committed EXIF fixture stays redacted (public repo)."""
import gzip
import json
import re
from pathlib import Path

import photovault

FIXTURE = Path(__file__).parent / "fixtures" / "meta" / "cards.jsonl.gz"


def test_package_imports():
    assert photovault.__version__


def test_card_fixture_is_redacted():
    with gzip.open(FIXTURE, "rt") as fh:
        rows = [json.loads(line) for line in fh]
    assert len(rows) > 8000
    assert {r["body"] for r in rows} <= {"SN-A", "SN-B"}  # serials replaced by aliases
    assert all(r["card"] in ("CARD1", "CARD2") for r in rows)  # no volume names or paths
    assert all(r["dto"].startswith(("2000", "2001", "2002")) for r in rows)  # dates shifted
    assert not any(re.search(r"/Volumes|serial|gps", json.dumps(r), re.I) for r in rows[:500])
