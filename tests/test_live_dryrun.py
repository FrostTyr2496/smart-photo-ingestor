"""Live dry run (D-031): plan an ingest of each registered CARD into a tmp_path target and discard the plan.

Read-only on the card. The catalog and the target are in tmp_path. Writes a summary to docs/private/.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from photovault import cli, volumes
from photovault.live_harness import LiveSource

OUT = Path(__file__).resolve().parent.parent / "docs" / "private" / "LIVE_DRYRUN.json"


@pytest.mark.live
def test_live_dry_run_on_cards(live_sources: list[LiveSource], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cards = [s for s in live_sources if s.role == "CARD" and s.available]
    if not cards:
        pytest.skip("no card mounted")
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    real_probe = volumes.probe

    def probe(mount: Path, **kw: object) -> volumes.VolumeProbe:
        mount = Path(mount)
        if mount.resolve().is_relative_to(tmp_path.resolve()):  # the tmp target is a folder, not a mount
            return volumes.VolumeProbe(mount, "TMPWORK", "apfs", None, None, True,
                                       volumes.read_marker(mount), False)
        return real_probe(mount, **kw)

    monkeypatch.setattr(volumes, "probe", probe)
    assert cli.main(["volumes", "assign", str(work), "--role", "WORKING", "--label", "TMPWORK"]) == 0
    summary = {}
    for card in cards:
        before = sorted(p.name for p in work.rglob("*"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--json", "ingest", "--source", str(card.path), "--target", str(work),
                             "--name", "Dry Run", "--dry-run"])
        events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
        assert code == 0, events[-3:]
        assert sorted(p.name for p in work.rglob("*")) == before  # nothing written to the target
        result = [e for e in events if e["event"] == "result"][-1]
        warnings = [e for e in events if e["event"] == "warning"]
        summary[card.name] = {"result": result, "warnings": warnings[:20], "warning_count": len(warnings)}
        print(card.name, {k: result[k] for k in ("files", "groups", "kinds", "planned", "mtime_dated")}, flush=True)
    OUT.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
