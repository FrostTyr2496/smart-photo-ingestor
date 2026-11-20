"""Phone locations (sp-2): `photovault meta phone` and `photovault.phone`, on a synthetic osxphotos export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from photovault import phone
from photovault.catalog import db
from photovault.config import load_config
from tests.test_cli import result, run


def item(uuid: str, *, make: str | None = "Apple", model: str | None = "iPhone 14 Pro", **kw) -> dict:
    """One osxphotos row at an invented spot in open water."""
    row = {
        "uuid": uuid, "date": "2026-01-02T03:04:05-05:00", "tzoffset": -18000, "latitude": 12.5, "longitude": -40.25,
        "screenshot": False, "screen_recording": False, "shared": False, "syndicated": False, "intrash": False,
        "ismovie": False, "exif_info": {"camera_make": make, "camera_model": model},
        "place": {"name": "Harbourton Quay",
                  "address": {"city": "Harbourton", "state_province": "Westshire", "country": "Freedonia",
                              "street": "1 Quay Rd"},
                  "names": {"city": ["Ignored"], "country": ["Ignored"]}},
    }
    row.update(kw)
    return row


def rows() -> list[dict]:
    names_only = {"names": {"city": ["Westport"], "state_province": ["Eastshire"], "country": ["Freedonia"]}}
    return [
        item("u-iphone", ai_caption="A boat at a quay", labels=["boat", "harbour"],
             persons=["Ada Example", "_UNKNOWN_"], albums=["Quay Trip"],
             search_info={"activities": ["sailing"], "venues": ["Quay Cafe"], "venue_types": ["cafe"],
                          "landmarks": [], "bodies_of_water": ["Example Bay"], "areas_of_interest": [],
                          "holidays": ["Founders Day"], "events": []}),
        item("u-iphone-nomake", make=None, model="Apple iPhone 14 Pro 77mm"),
        item("u-ipad", model="iPad Pro"),
        item("u-glasses", make="Meta AI", model="Ray-Ban Meta"),
        item("u-glasses-nomake", make=None, model="Ray-Ban Meta Smart Glasses"),
        item("u-video", ismovie=True, date="2026-01-02T03:04:05.123456-05:00"),
        item("u-noloc", latitude=None, longitude=None),
        item("u-shot", screenshot=True),
        item("u-rec", screen_recording=True),
        item("u-shared", shared=True),
        item("u-syn", syndicated=True),
        item("u-trash", intrash=True),
        item("u-dji", make="DJI", model="FC3582"),
        item("u-vision", make="Apple", model="Apple Vision Pro"),
        item("u-nomodel", make=None, model=None),
        item("u-baddate", date="not a date"),
        item("u-naive", date="2026-01-02T03:04:05", tzoffset=3600, place=names_only),
    ]


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    c = db.connect(load_config().catalog_path)
    db.migrate(c)
    return c


def write_array(tmp_path: Path, data: list[dict]) -> Path:
    path = tmp_path / "photos.json"
    path.write_text(json.dumps(data))
    return path


def point(conn, ref: str):
    return conn.execute("SELECT * FROM geo_points WHERE source_ref = ?", (ref,)).fetchone()


def test_filters_sources_and_counts(conn, tmp_path) -> None:
    counts = phone.import_phone(conn, write_array(tmp_path, rows()))
    assert counts["seen"] == 17 and counts["kept"] == 7
    assert counts["inserted"] == 7 and counts["updated"] == 0
    assert counts["skipped"] == {"no_location": 1, "screenshot": 2, "shared": 2, "trash": 1, "other_device": 3,
                                 "bad_date": 1}
    assert counts["kept_by_source"] == {"iphone": 4, "ipad": 1, "glasses": 2}
    sources = {r["source_ref"]: r["source"] for r in conn.execute("SELECT source, source_ref FROM geo_points")}
    assert sources == {"u-iphone": "iphone", "u-iphone-nomake": "iphone", "u-ipad": "ipad", "u-glasses": "glasses",
                       "u-glasses-nomake": "glasses", "u-video": "iphone", "u-naive": "iphone"}


def test_fields_dates_and_places(conn, tmp_path) -> None:
    phone.import_phone(conn, write_array(tmp_path, rows()))
    p = point(conn, "u-iphone")
    assert (p["utc_time"], p["local_offset"], p["kind"]) == ("2026-01-02T08:04:05+00:00", "-05:00", "photo")
    assert (p["lat"], p["lon"], p["alt"], p["accuracy_m"]) == (12.5, -40.25, None, None)
    assert (p["place_name"], p["city"], p["state"], p["country"]) == ("Harbourton Quay", "Harbourton", "Westshire",
                                                                      "Freedonia")
    v = point(conn, "u-video")  # microseconds are dropped, kind is video
    assert (v["utc_time"], v["kind"]) == ("2026-01-02T08:04:05+00:00", "video")
    n = point(conn, "u-naive")  # no offset in the date: tzoffset applies; places fall back to place.names
    assert (n["utc_time"], n["local_offset"]) == ("2026-01-02T02:04:05+00:00", "+01:00")
    assert (n["place_name"], n["city"], n["state"], n["country"]) == (None, "Westport", "Eastshire", "Freedonia")


def test_rerun_changes_nothing_and_edits_update(conn, tmp_path) -> None:
    path = write_array(tmp_path, rows())
    phone.import_phone(conn, path)
    before = [tuple(r) for r in conn.execute("SELECT * FROM geo_points ORDER BY point_id")]
    again = phone.import_phone(conn, path)
    assert (again["inserted"], again["updated"], again["unchanged"]) == (0, 0, 7)
    assert [tuple(r) for r in conn.execute("SELECT * FROM geo_points ORDER BY point_id")] == before
    data = rows()
    data[0]["latitude"] = 13.0
    changed = phone.import_phone(conn, write_array(tmp_path, data))
    assert (changed["inserted"], changed["updated"], changed["unchanged"]) == (0, 1, 6)
    assert point(conn, "u-iphone")["lat"] == 13.0


def test_json_lines_input(conn, tmp_path) -> None:
    path = tmp_path / "photos.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows()[:3]) + "\n\n")
    counts = phone.import_phone(conn, path)
    assert counts["seen"] == 3 and counts["kept"] == 3


@pytest.mark.parametrize("text", ["", "   \n", "hello", '{"a": 1}', "[1, 2]", '[{"a": 1}]', '[{"uuid": '])
def test_bad_input_names_the_export_command(conn, tmp_path, text) -> None:
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises(phone.NotOsxphotosJson, match="osxphotos query"):
        phone.import_phone(conn, path)
    assert conn.execute("SELECT COUNT(*) FROM geo_points").fetchone()[0] == 0


def test_cli_result_and_errors(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    path = write_array(tmp_path, rows())
    code, lines = run(capsys, "meta", "phone", str(path))
    assert code == 0, lines
    res = result(lines)
    assert res["kept"] == 7 and res["kept_by_source"]["glasses"] == 2 and res["skipped"]["screenshot"] == 2
    bad = tmp_path / "bad.json"
    bad.write_text("nope")
    code, lines = run(capsys, "meta", "phone", str(bad))
    assert code == 2 and any(e["event"] == "error" and "osxphotos" in e["message"] for e in lines)
    assert run(capsys, "meta", "phone", str(tmp_path / "missing.json"))[0] == 2


def test_phone_analysis_filled_and_idempotent(conn, tmp_path) -> None:
    path = write_array(tmp_path, rows())
    res = phone.import_phone(conn, path)
    assert (res["with_caption"], res["with_labels"], res["with_people"]) == (1, 1, 1)
    phone.import_phone(conn, path)
    assert conn.execute("SELECT COUNT(*) FROM phone_analysis").fetchone()[0] == res["kept"]
    q = ("SELECT a.* FROM phone_analysis a JOIN geo_points g USING (point_id) WHERE g.source_ref = ?")
    a = conn.execute(q, ("u-iphone",)).fetchone()
    assert a["caption"] == "A boat at a quay" and json.loads(a["labels_json"]) == ["boat", "harbour"]
    assert json.loads(a["persons_json"]) == ["Ada Example"]
    hints = json.loads(a["hints_json"])
    assert hints["activities"] == ["sailing"] and hints["albums"] == ["Quay Trip"] and hints["landmarks"] == []
    other = conn.execute(q, ("u-ipad",)).fetchone()
    assert other["caption"] is None and other["persons_json"] == "[]"
    data = rows()
    data[0]["ai_caption"] = "Changed"
    phone.import_phone(conn, write_array(tmp_path, data))
    assert conn.execute(q, ("u-iphone",)).fetchone()["caption"] == "Changed"
