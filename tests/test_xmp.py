"""XMP sidecars: coordinate format, packet structure, escaping and the write-conflict guard."""

import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from photovault.xmp import SidecarConflict, gps_to_xmp, render_xmp, sidecar_path, write_sidecar

RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
EXIF = "{http://ns.adobe.com/exif/1.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
XPACKET = re.compile(r"<\?xpacket[^?]*\?>")


def parse_packet(text: str) -> ET.Element:
    return ET.fromstring(XPACKET.sub("", text))


def description(root: ET.Element) -> ET.Element:
    return root.find(f"{RDF}RDF/{RDF}Description")


def test_gps_examples():
    assert gps_to_xmp(-33.8568, True) == "33,51.408000S"
    assert gps_to_xmp(151.2153, False) == "151,12.918000E"


def test_gps_refs_and_zero():
    assert gps_to_xmp(10.5, True) == "10,30.000000N"
    assert gps_to_xmp(-10.5, False) == "10,30.000000W"
    assert gps_to_xmp(0.0, True) == "0,00.000000N"


def test_gps_minute_carry_does_not_produce_60_minutes():
    # 0.9999999999 degrees is 59.99999999940 minutes, which must round up to 1 degree, 00 minutes.
    assert gps_to_xmp(33.9999999999, True) == "34,00.000000N"
    assert gps_to_xmp(-0.9999999999, False) == "1,00.000000W"
    # 0.99999999 degrees is 59.9999994 minutes: rounds to 59.999999, no carry.
    assert gps_to_xmp(33.99999999, True) == "33,59.999999N"


def test_round_trip_contains_keywords_creator_and_gps():
    meta = {
        "lat": -33.8568,
        "lon": 151.2153,
        "alt": 12.3456,
        "place_name": "Harbour",
        "creator": "Jane Doe",
        "copyright": "© Jane Doe",
        "title": "Morning",
        "keywords": ["beach", "Sydney"],
        "date_original": "2026-03-01T09:00:00+10:00",
    }
    root = parse_packet(render_xmp(meta))
    desc = description(root)
    assert desc.get(f"{EXIF}GPSLatitude") == "33,51.408000S"
    assert desc.get(f"{EXIF}GPSLongitude") == "151,12.918000E"
    assert desc.get(f"{EXIF}GPSAltitude") == "12346/1000"
    assert desc.get(f"{EXIF}GPSAltitudeRef") == "0"
    assert desc.get(f"{EXIF}DateTimeOriginal") == "2026-03-01T09:00:00+10:00"
    creators = [li.text for li in desc.findall(f"{DC}creator/{RDF}Seq/{RDF}li")]
    assert creators == ["Jane Doe"]
    keywords = [li.text for li in desc.findall(f"{DC}subject/{RDF}Bag/{RDF}li")]
    assert keywords == ["beach", "Sydney"]
    rights = desc.find(f"{DC}rights/{RDF}Alt/{RDF}li")
    assert rights.text == "© Jane Doe"
    assert rights.get("{http://www.w3.org/XML/1998/namespace}lang") == "x-default"
    assert desc.find(f"{DC}title/{RDF}Alt/{RDF}li").text == "Morning"


def test_negative_altitude_uses_below_sea_level_ref():
    desc = description(parse_packet(render_xmp({"alt": -2.5})))
    assert desc.get(f"{EXIF}GPSAltitudeRef") == "1"
    assert desc.get(f"{EXIF}GPSAltitude") == "2500/1000"


def test_absent_fields_are_omitted():
    root = parse_packet(render_xmp({"creator": "Jane"}))
    desc = description(root)
    assert desc.get(f"{EXIF}GPSLatitude") is None
    assert desc.find(f"{DC}subject") is None
    assert desc.find(f"{DC}rights") is None
    assert desc.find(f"{DC}title") is None


def test_packet_has_xpacket_wrappers():
    text = render_xmp({"creator": "Jane"})
    assert text.startswith("<?xpacket begin=")
    assert text.rstrip().endswith('<?xpacket end="w"?>')


def test_keyword_and_text_escaping():
    meta = {"keywords": ["Tom & Jerry", "<tag>"], "creator": 'A "B" & <C>', "place_name": "Q&A <place>"}
    text = render_xmp(meta)
    root = parse_packet(text)
    desc = description(root)
    keywords = [li.text for li in desc.findall(f"{DC}subject/{RDF}Bag/{RDF}li")]
    assert keywords == ["Tom & Jerry", "<tag>"]
    assert [li.text for li in desc.findall(f"{DC}creator/{RDF}Seq/{RDF}li")] == ['A "B" & <C>']
    assert desc.get("{http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/}Location") == "Q&A <place>"
    assert "&amp;" in text and "&lt;tag&gt;" in text


def test_sidecar_path_uses_lightroom_convention(tmp_path: Path):
    assert sidecar_path(tmp_path / "DSC_1.NEF") == tmp_path / "DSC_1.xmp"
    assert sidecar_path(tmp_path / "a.b.CR3") == tmp_path / "a.b.xmp"


def test_write_new_sidecar_returns_hash_and_leaves_no_temp(tmp_path: Path):
    target = tmp_path / "DSC_1.xmp"
    text = render_xmp({"creator": "Jane"})
    digest = write_sidecar(target, text, expected_sha256=None)
    assert target.read_text(encoding="utf-8") == text
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert not (tmp_path / ".DSC_1.xmp.pvtmp").exists()


def test_conflict_raised_on_foreign_file(tmp_path: Path):
    target = tmp_path / "DSC_1.xmp"
    target.write_text("<lightroom/>", encoding="utf-8")
    with pytest.raises(SidecarConflict):
        write_sidecar(target, render_xmp({"creator": "Jane"}), expected_sha256=None)
    with pytest.raises(SidecarConflict):
        write_sidecar(target, render_xmp({"creator": "Jane"}), expected_sha256="0" * 64)
    assert target.read_text(encoding="utf-8") == "<lightroom/>"


def test_second_write_with_expected_hash_succeeds(tmp_path: Path):
    target = tmp_path / "DSC_1.xmp"
    first = write_sidecar(target, render_xmp({"creator": "Jane"}), expected_sha256=None)
    second_text = render_xmp({"creator": "Jane", "keywords": ["beach"]})
    second = write_sidecar(target, second_text, expected_sha256=first)
    assert second == hashlib.sha256(second_text.encode("utf-8")).hexdigest()
    assert target.read_text(encoding="utf-8") == second_text


def test_write_refuses_when_file_appeared_after_expected_none(tmp_path: Path):
    target = tmp_path / "DSC_1.xmp"
    write_sidecar(target, "one", expected_sha256=None)
    with pytest.raises(SidecarConflict):
        write_sidecar(target, "two", expected_sha256=None)
    assert target.read_text(encoding="utf-8") == "one"
