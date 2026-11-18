"""XMP sidecars: render the user's fixes as XMP and write them next to the copies on WORKING.

Sidecars are the only thing the UI writes next to media, and only on WORKING volumes (vault-rules 1, 3). The
writer refuses to overwrite a sidecar it did not write last (Lightroom may own it), so the caller passes the
SHA-256 it expects to find. Never writes xattrs (vault-rules 8).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from xml.sax.saxutils import escape

from photovault.hashing import sha256_file

__all__ = [
    "SidecarConflict",
    "gps_to_xmp",
    "render_xmp",
    "sidecar_path",
    "write_sidecar",
]

_MINUTE_UNITS = 10**6  # minutes are kept as integer millionths to avoid float carry bugs


class SidecarConflict(Exception):
    """A sidecar exists whose SHA-256 is not the expected one (or it must not exist and does)."""


def gps_to_xmp(value: float, is_lat: bool) -> str:
    """Format a signed decimal degree as XMP GPSCoordinate ``DDD,MM.mmmmmmR``."""
    ref = ("N" if value >= 0 else "S") if is_lat else ("E" if value >= 0 else "W")
    absolute = abs(value)
    degrees = int(absolute)
    minutes_units = round((absolute - degrees) * 60 * _MINUTE_UNITS)
    if minutes_units >= 60 * _MINUTE_UNITS:  # carry, e.g. 33.99999999 -> 34,00.000000
        degrees += 1
        minutes_units -= 60 * _MINUTE_UNITS
    whole, frac = divmod(minutes_units, _MINUTE_UNITS)
    return f"{degrees},{whole:02d}.{frac:06d}{ref}"


def _attr(name: str, value: str) -> str:
    return f'{name}="{escape(value, {chr(34): "&quot;"})}"'  # attribute values also escape quotes


def _alt_block(tag: str, text: str) -> list[str]:
    return [
        f"<{tag}>",
        '<rdf:Alt><rdf:li xml:lang="x-default">' + escape(text) + "</rdf:li></rdf:Alt>",
        f"</{tag}>",
    ]


def render_xmp(meta: dict) -> str:
    """Render an XMP packet for the given fixes. Absent (None / empty) fields are omitted."""
    attrs: list[str] = []
    lat, lon, alt = meta.get("lat"), meta.get("lon"), meta.get("alt")
    if lat is not None and lon is not None:
        attrs.append(_attr("exif:GPSVersionID", "2.3.0.0"))
        attrs.append(_attr("exif:GPSLatitude", gps_to_xmp(float(lat), True)))
        attrs.append(_attr("exif:GPSLongitude", gps_to_xmp(float(lon), False)))
    if alt is not None:
        alt_value = float(alt)
        rational = f"{round(abs(alt_value) * 1000)}/1000"
        attrs.append(_attr("exif:GPSAltitudeRef", "0" if alt_value >= 0 else "1"))
        attrs.append(_attr("exif:GPSAltitude", rational))
    if meta.get("place_name"):
        attrs.append(_attr("Iptc4xmpCore:Location", str(meta["place_name"])))
    for tag, key in (("photoshop:City", "city"), ("photoshop:State", "state"), ("photoshop:Country", "country")):
        if meta.get(key):
            attrs.append(_attr(tag, str(meta[key])))
    date_original = meta.get("date_original")
    if date_original:
        attrs.append(_attr("exif:DateTimeOriginal", str(date_original)))
        attrs.append(_attr("photoshop:DateCreated", str(date_original)))

    body: list[str] = []
    if meta.get("creator"):
        body += [
            "<dc:creator><rdf:Seq>",
            f"<rdf:li>{escape(str(meta['creator']))}</rdf:li>",
            "</rdf:Seq></dc:creator>",
        ]
    if meta.get("copyright"):
        body += _alt_block("dc:rights", str(meta["copyright"]))
    if meta.get("title"):
        body += _alt_block("dc:title", str(meta["title"]))
    keywords = [k for k in (meta.get("keywords") or []) if k]
    if keywords:
        body.append("<dc:subject><rdf:Bag>")
        body += [f"<rdf:li>{escape(str(k))}</rdf:li>" for k in keywords]
        body.append("</rdf:Bag></dc:subject>")

    namespaces = [
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"',
        'xmlns:exif="http://ns.adobe.com/exif/1.0/"',
        'xmlns:dc="http://purl.org/dc/elements/1.1/"',
        'xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"',
        'xmlns:xmp="http://ns.adobe.com/xap/1.0/"',
        'xmlns:Iptc4xmpCore="http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/"',
    ]
    description_open = "<rdf:Description " + " ".join(['rdf:about=""', *attrs]) + ">"
    lines = [
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>',
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="PhotoVault">',
        "<rdf:RDF " + " ".join(namespaces) + ">",
        description_open,
        *body,
        "</rdf:Description>",
        "</rdf:RDF>",
        "</x:xmpmeta>",
        '<?xpacket end="w"?>',
    ]
    return "\n".join(lines) + "\n"


def sidecar_path(media_path: Path) -> Path:
    """Lightroom convention: ``DSC_1.NEF`` -> ``DSC_1.xmp`` in the same folder."""
    return media_path.with_suffix(".xmp")


def write_sidecar(path: Path, text: str, *, expected_sha256: str | None) -> str:
    """Write ``text`` to ``path`` atomically and return its SHA-256.

    Raises ``SidecarConflict`` if ``path`` exists and its SHA-256 differs from ``expected_sha256``
    (``None`` means the file must not exist). Existing files are never overwritten unless the caller proves
    they are the one it wrote last.
    """
    if path.exists():
        current = sha256_file(path)
        if expected_sha256 is None or current != expected_sha256:
            raise SidecarConflict(f"{path} changed outside PhotoVault (sha256 {current})")
    data = text.encode("utf-8")
    tmp = path.with_name(f".{path.name}.pvtmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return hashlib.sha256(data).hexdigest()
