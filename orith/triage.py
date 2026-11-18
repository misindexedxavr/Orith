"""File triage: every fact the file itself carries, with no guessing.

JPEG: segment map (APPn identifiers, COM comments, SOF size), EXIF (all IFDs, read with our own
TIFF parser so nothing is hidden), GPS, capture times and offsets, camera and field of view,
the embedded IFD1 thumbnail compared with the main image, XMP packets, IPTC (Photoshop APP13),
ICC profile header and description, MPF, and any data after the end-of-image marker.
PNG: chunk list with CRC check, text chunks (tEXt/zTXt/iTXt), eXIf, tIME, iCCP, private chunks,
data after IEND. WebP: RIFF chunk list with EXIF/XMP. Other formats: type and size only.

Thresholds used for "notes" are fixed and printed; the numbers are always reported as well.
Tag names come from Pillow's EXIF tables; IPTC dataset names from the IPTC IIM 4.2 spec.
"""
from __future__ import annotations

import hashlib
import io
import math
import re
import struct
import warnings
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import ExifTags, Image

THUMB_MISMATCH_HAMMING = 12   # dHash bits (of 64) above which the thumbnail is reported as different
THUMB_MISMATCH_MAD = 25.0     # mean absolute grey difference (0-255) above which it is reported as different

MAGIC = [  # (prefix, type)
    (b"\xff\xd8\xff", "jpeg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF87a", "gif"), (b"GIF89a", "gif"),
    (b"II*\x00", "tiff"), (b"MM\x00*", "tiff"), (b"BM", "bmp"), (b"PK\x03\x04", "zip"), (b"Rar!\x1a\x07", "rar"),
    (b"7z\xbc\xaf\x27\x1c", "7z"), (b"%PDF", "pdf"), (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"), (b"OggS", "ogg"), (b"ID3", "mp3"), (b"\x00\x00\x01\x00", "ico"),
]
JPEG_MARKERS = {0xC0: "SOF0", 0xC1: "SOF1", 0xC2: "SOF2", 0xC3: "SOF3", 0xC4: "DHT", 0xC5: "SOF5", 0xC6: "SOF6",
                0xC7: "SOF7", 0xC9: "SOF9", 0xCA: "SOF10", 0xCB: "SOF11", 0xCC: "DAC", 0xCD: "SOF13", 0xCE: "SOF14",
                0xCF: "SOF15", 0xD8: "SOI", 0xD9: "EOI", 0xDA: "SOS", 0xDB: "DQT", 0xDD: "DRI", 0xFE: "COM"}
JPEG_MARKERS.update({0xE0 + i: f"APP{i}" for i in range(16)})
SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}

# IPTC IIM 4.2, record 2 (application record): dataset number -> name
IPTC_NAMES = {5: "ObjectName", 7: "EditStatus", 10: "Urgency", 15: "Category", 20: "SupplementalCategory",
              25: "Keywords", 40: "SpecialInstructions", 55: "DateCreated", 60: "TimeCreated",
              62: "DigitalCreationDate", 63: "DigitalCreationTime", 80: "By-line", 85: "By-lineTitle", 90: "City",
              92: "Sub-location", 95: "Province-State", 100: "Country-PrimaryLocationCode",
              101: "Country-PrimaryLocationName", 103: "OriginalTransmissionReference", 105: "Headline",
              110: "Credit", 115: "Source", 116: "CopyrightNotice", 118: "Contact", 120: "Caption-Abstract",
              122: "Writer-Editor"}

TIFF_TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 6: ("b", 1), 7: ("x", 1),
              8: ("h", 2), 9: ("i", 4), 10: ("ii", 8), 11: ("f", 4), 12: ("d", 8)}
SUB_IFDS = {0x8769: "Exif", 0x8825: "GPS", 0xA005: "Interop"}


def detect_type(data):
    for prefix, kind in MAGIC:
        if data.startswith(prefix):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp":
        brand = data[8:12].decode("latin-1", "replace")
        return "heif" if brand in ("heic", "heix", "mif1", "msf1", "heim", "heis", "avif") else f"iso-bmff({brand})"
    return "unknown"


def _text(b, limit=2000):
    s = b.decode("utf-8", errors="replace").replace("\x00", "")
    return s if len(s) <= limit else s[:limit] + f"... [{len(s)} chars]"


# ------------------------------------------------------------------------------------- TIFF / EXIF

class TiffReader:
    """Minimal TIFF/EXIF IFD reader over a bytes object that starts at the TIFF header."""

    def __init__(self, data):
        self.d = data
        bo = data[:2]
        if bo == b"II":
            self.e = "<"
        elif bo == b"MM":
            self.e = ">"
        else:
            raise ValueError("not a TIFF header")
        if struct.unpack(self.e + "H", data[2:4])[0] != 42:
            raise ValueError("bad TIFF magic")
        self.ifd0 = struct.unpack(self.e + "I", data[4:8])[0]

    def _value(self, typ, count, raw_or_off, inline):
        fmt, size = TIFF_TYPES.get(typ, (None, 1))
        total = size * count
        if fmt is None:
            return None
        buf = inline[:total] if total <= 4 else self.d[raw_or_off:raw_or_off + total]
        if len(buf) < total:
            return None
        if typ == 2:
            return buf.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
        if typ == 7:
            return bytes(buf)
        if typ in (5, 10):
            vals = struct.unpack(self.e + fmt[0] * (2 * count), buf)
            out = [(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]
            return out if count > 1 else out[0]
        vals = struct.unpack(self.e + fmt * count, buf)
        return list(vals) if count > 1 else vals[0]

    def read_ifd(self, offset):
        """(entries {tag: value}, next_ifd_offset)."""
        if offset <= 0 or offset + 2 > len(self.d):
            return {}, 0
        n = struct.unpack(self.e + "H", self.d[offset:offset + 2])[0]
        entries = {}
        for i in range(n):
            p = offset + 2 + 12 * i
            if p + 12 > len(self.d):
                break
            tag, typ, count = struct.unpack(self.e + "HHI", self.d[p:p + 8])
            inline = self.d[p + 8:p + 12]
            off = struct.unpack(self.e + "I", inline)[0]
            try:
                entries[tag] = self._value(typ, count, off, inline)
            except struct.error:
                entries[tag] = None
        p = offset + 2 + 12 * n
        nxt = struct.unpack(self.e + "I", self.d[p:p + 4])[0] if p + 4 <= len(self.d) else 0
        return entries, nxt


def _ratio(v):
    if isinstance(v, tuple) and len(v) == 2:
        return v[0] / v[1] if v[1] else None
    return v


def _jsonable(v):
    if isinstance(v, bytes):
        return {"bytes": len(v), "hex": v[:32].hex()} if len(v) > 32 or not v.isascii() else _text(v)
    if isinstance(v, tuple) and len(v) == 2:
        r = _ratio(v)
        return round(r, 8) if r is not None else f"{v[0]}/{v[1]}"
    if isinstance(v, list):
        return [_jsonable(x) for x in v[:64]] + ([f"... {len(v)} values"] if len(v) > 64 else [])
    return v


def parse_exif(tiff_bytes):
    """All IFDs: {"IFD0": {...}, "Exif": {...}, "GPS": {...}, "Interop": {...}, "IFD1": {...}}, raw dicts and the thumbnail."""
    r = TiffReader(tiff_bytes)
    raw, named = {}, {}
    ifd0, nxt = r.read_ifd(r.ifd0)
    raw["IFD0"] = ifd0
    for tag, name in SUB_IFDS.items():
        holder = ifd0 if tag in ifd0 else raw.get("Exif", {})
        off = holder.get(tag) if isinstance(holder.get(tag), int) else None
        if off:
            raw[name], _ = r.read_ifd(off)
    if "Exif" in raw and 0xA005 in raw["Exif"] and "Interop" not in raw:
        raw["Interop"], _ = r.read_ifd(raw["Exif"][0xA005])
    if nxt:
        raw["IFD1"], _ = r.read_ifd(nxt)
    thumb = None
    ifd1 = raw.get("IFD1", {})
    if ifd1.get(0x0201) and ifd1.get(0x0202):
        start, length = ifd1[0x0201], ifd1[0x0202]
        thumb = tiff_bytes[start:start + length] if start + length <= len(tiff_bytes) else None
    for ifd, entries in raw.items():
        table = ExifTags.GPSTAGS if ifd == "GPS" else ExifTags.TAGS
        out = {}
        for tag, val in entries.items():
            name = table.get(tag, f"0x{tag:04x}")
            if tag == 0x927C:  # MakerNote: vendor-specific binary, report size only
                out[name] = {"bytes": len(val) if isinstance(val, bytes) else None, "note": "vendor binary, not decoded"}
            elif tag in SUB_IFDS or tag in (0x0201,):
                out[name] = val
            else:
                out[name] = _jsonable(val)
        named[ifd] = out
    return named, raw, thumb, r.e


def _dms(vals, ref):
    if not vals or not isinstance(vals, list) or len(vals) != 3:
        return None
    d, m, s = (_ratio(v) for v in vals)
    if None in (d, m, s) or d < 0 or not 0 <= m < 60 or not 0 <= s < 60:
        return None
    deg = d + m / 60 + s / 3600
    return -deg if ref in ("S", "W") else deg


def _set_exif(out, named, raw):
    out["exif"], out["gps"] = named, gps_facts(raw.get("GPS"))
    out["time"], out["camera"] = time_facts(named), camera_facts(named)


def gps_facts(raw_gps):
    """GPS IFD -> decimal degrees with sanity checks. Impossible values are dropped and reported in 'problems'."""
    if not raw_gps:
        return None
    g = raw_gps
    lat = _dms(g.get(2), g.get(1))
    lon = _dms(g.get(4), g.get(3))
    problems = []
    if g.get(2) is not None and lat is None:
        problems.append("latitude is not a valid degrees/minutes/seconds value")
    if g.get(4) is not None and lon is None:
        problems.append("longitude is not a valid degrees/minutes/seconds value")
    if lat is not None and not -90 <= lat <= 90:
        problems.append(f"latitude {lat:.4f} is outside -90..90")
        lat = None
    if lon is not None and not -180 <= lon <= 180:
        problems.append(f"longitude {lon:.4f} is outside -180..180")
        lon = None
    if lat is not None and g.get(1) not in ("N", "S"):
        problems.append("no N/S reference: north assumed")
    if lon is not None and g.get(3) not in ("E", "W"):
        problems.append("no E/W reference: east assumed")
    if lat is not None and lon is not None and abs(lat) < 1e-6 and abs(lon) < 1e-6:
        problems.append("0, 0 (Null Island): an empty GPS fix, not a position")
        lat = lon = None
    if lat is None or lon is None:
        lat = lon = None
    out = {"lat": round(lat, 7) if lat is not None else None, "lon": round(lon, 7) if lon is not None else None}
    if problems:
        out["problems"] = problems
    if g.get(6) is not None:
        alt = _ratio(g[6])
        out["altitude_m"] = round(-alt if g.get(5) == 1 else alt, 2) if alt is not None else None
    if g.get(17) is not None:
        out["image_direction_deg"] = round(_ratio(g[17]), 2)
        out["image_direction_ref"] = {"T": "true north", "M": "magnetic north"}.get(g.get(16), g.get(16))
    if g.get(24) is not None:
        out["dest_bearing_deg"] = round(_ratio(g[24]), 2)
    if g.get(29) and isinstance(g.get(7), list) and len(g[7]) == 3:
        h, m, s = (_ratio(v) for v in g[7])
        ok_date = re.fullmatch(r"\d{4}:\d\d:\d\d", str(g[29]).strip())
        if None not in (h, m, s) and ok_date and 0 <= h < 24 and 0 <= m < 60 and 0 <= s < 61:
            out["gps_utc"] = f"{str(g[29]).strip().replace(':', '-')}T{int(h):02d}:{int(m):02d}:{s:06.3f}Z"
        else:
            out.setdefault("problems", []).append("GPS date/time stamp is not valid")
    if g.get(18):
        out["map_datum"] = g[18]
    if g.get(27) is not None:
        out["processing_method"] = _jsonable(g[27])
    if g.get(31) is not None:
        out["h_positioning_error_m"] = _ratio(g[31])
    return out


def time_facts(named):
    e = named.get("Exif", {})
    i0 = named.get("IFD0", {})
    out = {k: v for k, v in {
        "DateTimeOriginal": e.get("DateTimeOriginal"), "SubSecTimeOriginal": e.get("SubSecTimeOriginal"),
        "OffsetTimeOriginal": e.get("OffsetTimeOriginal"), "DateTimeDigitized": e.get("DateTimeDigitized"),
        "OffsetTimeDigitized": e.get("OffsetTimeDigitized"), "DateTime (file change)": i0.get("DateTime"),
        "OffsetTime": e.get("OffsetTime")}.items() if v not in (None, "")}
    dto, off = e.get("DateTimeOriginal"), e.get("OffsetTimeOriginal")
    m = re.match(r"^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})$", str(dto or ""))
    mo = re.match(r"^([+-])(\d{2}):(\d{2})$", str(off or ""))
    if m and mo:
        import datetime as dt
        local = dt.datetime(*map(int, m.groups()))
        delta = dt.timedelta(hours=int(mo.group(2)), minutes=int(mo.group(3)))
        utc = local - delta if mo.group(1) == "+" else local + delta
        out["utc_from_offset"] = utc.isoformat() + "Z"
    elif m:
        out["utc_from_offset"] = None
        # EXIF 2.31: each offset belongs to its own timestamp. Show the others, never pick one silently.
        others = [{"tag": tag, "value": e.get(tag), "belongs_to": belongs}
                  for tag, belongs in (("OffsetTimeDigitized", "DateTimeDigitized"), ("OffsetTime", "DateTime (file change)"))
                  if re.match(r"^[+-]\d{2}:\d{2}$", str(e.get(tag) or ""))]
        if others:
            out["other_offsets"] = others
            out["offset_conflict"] = len({o["value"] for o in others}) > 1
            out["note"] = ("OffsetTimeOriginal (the capture-time offset) is missing. The file has other offset tags, "
                           "listed in other_offsets with the timestamp each belongs to; they may not apply to the capture "
                           "time and none was used." + (" They disagree with each other." if out["offset_conflict"] else "")
                           + " The time zone of the capture time is unknown until you confirm one.")
        else:
            out["note"] = "local capture time with no UTC offset in the file: the time zone is unknown"
    return out


def camera_facts(named):
    i0, e = named.get("IFD0", {}), named.get("Exif", {})
    out = {k: v for k, v in {"Make": i0.get("Make"), "Model": i0.get("Model"), "Software": i0.get("Software"),
                             "LensModel": e.get("LensModel"), "LensMake": e.get("LensMake"),
                             "BodySerialNumber": e.get("BodySerialNumber"), "Artist": i0.get("Artist"),
                             "Copyright": i0.get("Copyright"), "ImageDescription": i0.get("ImageDescription"),
                             "Orientation": i0.get("Orientation"), "FocalLength_mm": e.get("FocalLength"),
                             "FocalLengthIn35mmFilm": e.get("FocalLengthIn35mmFilm"),
                             "UserComment": e.get("UserComment")}.items() if v not in (None, "")}
    f35 = e.get("FocalLengthIn35mmFilm")
    if isinstance(f35, (int, float)) and f35 > 0:
        # 35 mm frame is 36 x 24 mm: field of view across the long and short sides
        out["fov_long_side_deg"] = round(math.degrees(2 * math.atan(36 / (2 * f35))), 1)
        out["fov_short_side_deg"] = round(math.degrees(2 * math.atan(24 / (2 * f35))), 1)
    return out


# ------------------------------------------------------------------------------------- XMP / IPTC / ICC

def parse_xmp(packet):
    """Flat list of (qualified name, value) from an XMP packet. Values are text exactly as stored."""
    text = packet.decode("utf-8", errors="replace")
    prefixes = {uri: p for p, uri in re.findall(r'xmlns:([\w-]+)="([^"]+)"', text)}

    def q(tag):
        m = re.match(r"\{(.+)\}(.+)", tag)
        return f"{prefixes.get(m.group(1), m.group(1))}:{m.group(2)}" if m else tag
    items = []
    try:
        root = ET.fromstring(text[text.index("<"):text.rindex(">") + 1] if "<" in text else text)
    except (ET.ParseError, ValueError) as e:
        return [("parse_error", str(e))]
    parent = {c: p for p in root.iter() for c in p}
    for el in root.iter():
        for k, v in el.attrib.items():
            name = q(k)
            if not name.startswith(("rdf:", "xml:", "x:")) and v.strip():
                items.append((name, v.strip()))
        if el.text and el.text.strip() and not list(el):
            name = q(el.tag)
            if name == "rdf:li":  # list item: report it under the property that holds the list
                bag = parent.get(el)
                owner = parent.get(bag) if bag is not None else None
                name = q(owner.tag) if owner is not None else name
            items.append((name, el.text.strip()))
    return items


def parse_iptc(app13_payload):
    """IPTC IIM record-2 datasets inside a Photoshop 8BIM 0x0404 resource."""
    out = []
    data = app13_payload
    i = data.find(b"8BIM")
    while i != -1 and i + 12 <= len(data):
        rid = struct.unpack(">H", data[i + 4:i + 6])[0]
        nlen = data[i + 6]
        p = i + 7 + nlen
        if (nlen + 1) % 2:
            p += 1
        if p + 4 > len(data):
            break
        size = struct.unpack(">I", data[p:p + 4])[0]
        body = data[p + 4:p + 4 + size]
        if rid == 0x0404:
            j = 0
            while j + 5 <= len(body) and body[j] == 0x1C:
                rec, ds, ln = body[j + 1], body[j + 2], struct.unpack(">H", body[j + 3:j + 5])[0]
                val = body[j + 5:j + 5 + ln]
                if rec == 2 and ds != 0:
                    out.append((IPTC_NAMES.get(ds, f"2:{ds}"), _text(val, 500)))
                j += 5 + ln
        i = data.find(b"8BIM", p + 4 + size + (size % 2))
    return out


def parse_icc(icc):
    if len(icc) < 132:
        return {"bytes": len(icc), "error": "too short for an ICC header"}
    h = icc
    out = {"bytes": len(icc), "declared_size": struct.unpack(">I", h[0:4])[0],
           "cmm": h[4:8].decode("latin-1").strip("\x00 "), "version": f"{h[8]}.{h[9] >> 4}.{h[9] & 15}",
           "device_class": h[12:16].decode("latin-1"), "colour_space": h[16:20].decode("latin-1").strip(),
           "pcs": h[20:24].decode("latin-1").strip(), "platform": h[40:44].decode("latin-1").strip("\x00 "),
           "manufacturer": h[48:52].decode("latin-1").strip("\x00 "), "model": h[52:56].hex(),
           "creator": h[80:84].decode("latin-1").strip("\x00 ")}
    y, mo, d, hh, mi, s = struct.unpack(">6H", h[24:36])
    if y:
        out["created"] = f"{y:04d}-{mo:02d}-{d:02d}T{hh:02d}:{mi:02d}:{s:02d}"
    n = struct.unpack(">I", h[128:132])[0]
    tags = {}
    for k in range(min(n, 200)):
        p = 132 + 12 * k
        if p + 12 > len(h):
            break
        sig, off, size = h[p:p + 4].decode("latin-1"), *struct.unpack(">II", h[p + 4:p + 12])
        tags[sig] = (off, size)
    out["tags"] = sorted(tags)
    for key, label in (("desc", "description"), ("cprt", "copyright")):
        if key in tags:
            off, size = tags[key]
            out[label] = _icc_text(h[off:off + size])
    return out


def _icc_text(b):
    kind = b[:4]
    if kind == b"desc" and len(b) >= 12:
        n = struct.unpack(">I", b[8:12])[0]
        return b[12:12 + n].split(b"\x00", 1)[0].decode("latin-1", "replace")
    if kind == b"text":
        return b[8:].split(b"\x00", 1)[0].decode("latin-1", "replace")
    if kind == b"mluc" and len(b) >= 16:
        count = struct.unpack(">I", b[8:12])[0]
        if count:
            ln, off = struct.unpack(">II", b[20:28])
            return b[off:off + ln].decode("utf-16-be", "replace")
    return f"<{kind.decode('latin-1', 'replace')}>"


# ------------------------------------------------------------------------------------- thumbnail check

def _dhash(img):
    g = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(g.getdata()) if not hasattr(g, "get_flattened_data") else list(g.get_flattened_data())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def _trim_borders(img, level=12):
    """Remove uniform dark letterbox bars that cameras add when the thumbnail aspect differs."""
    g = img.convert("L")
    w, h = g.size
    px = g.load()

    def row_dark(y):
        return sum(px[x, y] for x in range(w)) / w < level

    def col_dark(x):
        return sum(px[x, y] for y in range(h)) / h < level
    top, bottom, left, right = 0, h - 1, 0, w - 1
    while top < bottom and row_dark(top):
        top += 1
    while bottom > top and row_dark(bottom):
        bottom -= 1
    while left < right and col_dark(left):
        left += 1
    while right > left and col_dark(right):
        right -= 1
    return img.crop((left, top, right + 1, bottom + 1))


def compare_thumbnail(main_img, thumb_bytes):
    try:
        th = Image.open(io.BytesIO(thumb_bytes))
        th.load()
    except Exception as e:  # noqa: BLE001
        return {"decodable": False, "error": f"{type(e).__name__}: {e}"}
    t = _trim_borders(th.convert("RGB"))
    m = main_img.convert("RGB")
    out = {"decodable": True, "thumb_size": list(th.size), "thumb_size_after_trimming_bars": list(t.size),
           "main_size": list(m.size),
           "aspect_main": round(m.size[0] / m.size[1], 3), "aspect_thumb": round(t.size[0] / t.size[1], 3)}
    a = m.resize((64, 64), Image.Resampling.LANCZOS).convert("L")
    b = t.resize((64, 64), Image.Resampling.LANCZOS).convert("L")
    pa = a.get_flattened_data() if hasattr(a, "get_flattened_data") else a.getdata()
    pb = b.get_flattened_data() if hasattr(b, "get_flattened_data") else b.getdata()
    mad = sum(abs(x - y) for x, y in zip(pa, pb)) / (64 * 64)
    ham = bin(_dhash(m) ^ _dhash(t)).count("1")
    out.update(mean_abs_diff=round(mad, 1), dhash_hamming=ham,
               thresholds={"dhash_hamming": THUMB_MISMATCH_HAMMING, "mean_abs_diff": THUMB_MISMATCH_MAD})
    out["differs"] = ham > THUMB_MISMATCH_HAMMING or mad > THUMB_MISMATCH_MAD
    return out


# ------------------------------------------------------------------------------------- containers

def _scan_jpeg(data):
    segs, i, end = [], 2, None
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            segs.append({"offset": i, "name": "garbage", "note": "expected a marker"})
            break
        while i < len(data) and data[i] == 0xFF:
            i += 1
        if i >= len(data):
            break
        m = data[i]
        i += 1
        name = JPEG_MARKERS.get(m, f"0x{m:02X}")
        if m == 0xD9:
            end = i
            segs.append({"offset": i - 2, "name": "EOI"})
            break
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            continue
        length = struct.unpack(">H", data[i:i + 2])[0]
        payload = data[i + 2:i + length]
        seg = {"offset": i - 2, "name": name, "length": length, "payload": payload}
        segs.append(seg)
        i += length
        if m == 0xDA:  # entropy-coded data until the next real marker
            while i + 1 < len(data):
                if data[i] == 0xFF and data[i + 1] != 0x00 and not (0xD0 <= data[i + 1] <= 0xD7):
                    break
                i += 1
    return segs, end


def _app_id(payload):
    for ident in (b"Exif\x00", b"http://ns.adobe.com/xap/1.0/\x00", b"http://ns.adobe.com/xmp/extension/\x00",
                  b"ICC_PROFILE\x00", b"Photoshop 3.0\x00", b"JFIF\x00", b"JFXX\x00", b"MPF\x00", b"Ducky",
                  b"Adobe"):
        if payload.startswith(ident):
            return ident.rstrip(b"\x00").decode("latin-1")
    return _text(payload[:24], 24)


def triage_jpeg(data, out):
    segs, end = _scan_jpeg(data)
    icc_chunks, xmp, ext_xmp = {}, [], []
    out["segments"] = []
    app11 = []
    for s in segs:
        entry = {k: s[k] for k in ("offset", "name", "length") if k in s}
        p = s.get("payload", b"")
        if s["name"].startswith("APP"):
            entry["id"] = _app_id(p)
            if p.startswith(b"Exif\x00"):
                named, raw, thumb, endian = parse_exif(p[6:])
                out["exif"] = named
                out["gps"] = gps_facts(raw.get("GPS"))
                out["time"] = time_facts(named)
                out["camera"] = camera_facts(named)
                out["exif_byte_order"] = "little-endian (II)" if endian == "<" else "big-endian (MM)"
                if thumb:
                    out["_thumb"] = thumb
            elif p.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
                xmp.append(p[29:])
            elif p.startswith(b"http://ns.adobe.com/xmp/extension/\x00"):
                ext_xmp.append(p[35 + 32 + 8:])
            elif p.startswith(b"ICC_PROFILE\x00") and len(p) > 14:
                icc_chunks[p[12]] = p[14:]
            elif p.startswith(b"Photoshop 3.0\x00"):
                out["iptc"] = parse_iptc(p[14:])
            elif p.startswith(b"MPF\x00"):
                out["mpf"] = {"present": True, "bytes": len(p),
                              "note": "Multi-Picture Format: the file holds extra images (often a large preview)"}
            elif s["name"] == "APP11" and p.startswith(b"JP"):
                app11.append(bytes(p))
        elif s["name"] == "COM":
            entry["text"] = _text(p, 500)
            out.setdefault("comments", []).append(_text(p))
        elif s["name"] in JPEG_MARKERS.values() and s["name"].startswith("SOF"):
            if len(p) >= 5:
                h, w = struct.unpack(">HH", p[1:5])
                entry["size"] = [w, h]
                out["coding"] = {"process": s["name"], "progressive": s["name"] in ("SOF2", "SOF6", "SOF10", "SOF14")}
        out["segments"].append(entry)
    if xmp or ext_xmp:
        out["xmp"] = [{"name": k, "value": v} for pkt in xmp + ([b"".join(ext_xmp)] if ext_xmp else [])
                      for k, v in parse_xmp(pkt)]
    if icc_chunks:
        out["icc"] = parse_icc(b"".join(icc_chunks[k] for k in sorted(icc_chunks)))
    if app11:
        from . import c2pa
        found = c2pa.parse(c2pa.jpeg_app11_payloads_to_jumbf(app11))
        if found.get("present"):
            out["c2pa"] = found
    out["end_of_image_at"] = end
    return end


def triage_png(data, out):
    i, chunks, end = 8, [], None
    out["text_chunks"] = []
    while i + 12 <= len(data):
        ln = struct.unpack(">I", data[i:i + 4])[0]
        ctype = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + ln]
        crc = struct.unpack(">I", data[i + 8 + ln:i + 12 + ln])[0] if i + 12 + ln <= len(data) else None
        name = ctype.decode("latin-1", "replace")
        entry = {"offset": i, "type": name, "length": ln, "crc_ok": crc == (zlib.crc32(ctype + body) & 0xFFFFFFFF)}
        if name[:1].islower() and name not in ("tEXt", "zTXt", "iTXt", "tIME", "pHYs", "gAMA", "cHRM", "sRGB", "iCCP",
                                                "eXIf", "bKGD", "sBIT", "sPLT", "hIST", "tRNS"):
            entry["note"] = "non-standard ancillary chunk"
        if name == "tEXt":
            k, _, v = body.partition(b"\x00")
            out["text_chunks"].append({"type": name, "key": _text(k), "value": _text(v)})
        elif name == "zTXt":
            k, _, rest = body.partition(b"\x00")
            try:
                v = zlib.decompress(rest[1:])
            except zlib.error as e:
                v = f"<zlib error {e}>".encode()
            out["text_chunks"].append({"type": name, "key": _text(k), "value": _text(v)})
        elif name == "iTXt":
            k, _, rest = body.partition(b"\x00")
            comp, rest = rest[0], rest[2:]
            lang, _, rest = rest.partition(b"\x00")
            tkey, _, txt = rest.partition(b"\x00")
            if comp == 1:
                try:
                    txt = zlib.decompress(txt)
                except zlib.error:
                    pass
            item = {"type": name, "key": _text(k), "language": _text(lang), "translated_key": _text(tkey),
                    "value": _text(txt)}
            out["text_chunks"].append(item)
            if k == b"XML:com.adobe.xmp":
                out["xmp"] = [{"name": a, "value": b} for a, b in parse_xmp(txt)]
        elif name == "eXIf":
            named, raw, thumb, endian = parse_exif(body)
            _set_exif(out, named, raw)
            if thumb:
                out["_thumb"] = thumb
        elif name == "tIME" and ln == 7:
            y, mo, d, h, mi, s = struct.unpack(">HBBBBB", body)
            entry["value"] = f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}Z (last modification, UTC)"
        elif name == "iCCP":
            pname, _, rest = body.partition(b"\x00")
            entry["profile_name"] = _text(pname)
            try:
                out["icc"] = parse_icc(zlib.decompress(rest[1:]))
            except zlib.error as e:
                out["icc"] = {"error": str(e)}
        elif name == "IHDR" and ln >= 8:
            entry["size"] = list(struct.unpack(">II", body[:8]))
        elif name == "caBX":  # C2PA content credentials (JUMBF)
            from . import c2pa
            out["c2pa"] = c2pa.parse(body)
            entry["note"] = "C2PA content credentials (provenance manifest)"
        chunks.append(entry)
        i += 12 + ln
        if name == "IEND":
            end = i
            break
    out["chunks"] = chunks
    if not out["text_chunks"]:
        del out["text_chunks"]
    out["end_of_image_at"] = end
    return end


def triage_webp(data, out):
    size = struct.unpack("<I", data[4:8])[0] + 8
    i, chunks = 12, []
    while i + 8 <= min(len(data), size):
        cid = data[i:i + 4].decode("latin-1", "replace")
        ln = struct.unpack("<I", data[i + 4:i + 8])[0]
        body = data[i + 8:i + 8 + ln]
        chunks.append({"offset": i, "type": cid, "length": ln})
        if cid == "EXIF":
            tb = body[6:] if body.startswith(b"Exif\x00\x00") else body
            named, raw, thumb, _ = parse_exif(tb)
            _set_exif(out, named, raw)
        elif cid == "XMP ":
            out["xmp"] = [{"name": a, "value": b} for a, b in parse_xmp(body)]
        elif cid == "ICCP":
            out["icc"] = parse_icc(body)
        elif cid == "C2PA":
            from . import c2pa
            out["c2pa"] = c2pa.parse(body)
        i += 8 + ln + (ln % 2)
    out["chunks"] = chunks
    out["end_of_image_at"] = size
    return size


def _trailing(data, end, out):
    if end is None or end >= len(data):
        return
    tail = data[end:]
    info = {"offset": end, "bytes": len(tail), "type": detect_type(tail), "sha256": hashlib.sha256(tail).hexdigest(),
            "head_hex": tail[:32].hex(), "printable_preview": re.sub(r"[^\x20-\x7e]", ".", tail[:120].decode("latin-1"))}
    embedded = []
    for prefix, kind in MAGIC[:2] + [(b"PK\x03\x04", "zip"), (b"%PDF", "pdf"), (b"Rar!", "rar"), (b"7z\xbc\xaf", "7z")]:
        for mt in re.finditer(re.escape(prefix), tail):
            embedded.append({"type": kind, "offset_in_file": end + mt.start()})
    if embedded:
        info["signatures_found"] = embedded[:20]
    if tail.strip(b"\x00") == b"":
        info["note"] = "only zero padding"
    out["data_after_end"] = info


def triage_heif(data, out):
    """HEIC/HEIF (iPhone photos): EXIF and XMP through the optional pillow-heif decoder (orith[heic])."""
    try:
        import pillow_heif
    except ImportError:
        out["note"] = 'HEIC/HEIF metadata needs the optional decoder: pip install "orith[heic]"'
        return
    pillow_heif.register_heif_opener()
    with Image.open(io.BytesIO(data)) as im:
        exif = im.info.get("exif")
        xmp = im.info.get("xmp")
    if exif:
        tb = exif[6:] if exif.startswith(b"Exif\x00\x00") else exif
        named, raw, thumb, _ = parse_exif(tb)
        _set_exif(out, named, raw)
    if xmp:
        out["xmp"] = [{"name": a, "value": b} for a, b in parse_xmp(xmp if isinstance(xmp, bytes) else str(xmp).encode())]


def triage(path, extract_dir=None):
    path = Path(path)
    data = path.read_bytes()
    kind = detect_type(data)
    out = {"file": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "type": kind,
           "extension": path.suffix.lower()}
    ext_ok = {"jpeg": (".jpg", ".jpeg", ".jpe", ".jfif"), "png": (".png",), "webp": (".webp",), "gif": (".gif",),
              "tiff": (".tif", ".tiff"), "bmp": (".bmp",)}.get(kind)
    if ext_ok is not None and out["extension"] not in ext_ok:
        out["extension_mismatch"] = f"content is {kind} but the extension is {out['extension'] or '(none)'}"
    end = None
    try:
        if kind == "jpeg":
            end = triage_jpeg(data, out)
        elif kind == "png":
            end = triage_png(data, out)
        elif kind == "webp":
            end = triage_webp(data, out)
        elif kind == "tiff":  # a TIFF file is itself an EXIF-style structure
            named, raw, thumb, _ = parse_exif(data)
            _set_exif(out, named, raw)
        elif kind == "heif":
            triage_heif(data, out)
        else:
            out["note"] = f"container '{kind}' is not parsed beyond its type; use exiftool if available"
    except Exception as e:  # noqa: BLE001 - a malformed file is itself a finding
        out["parse_error"] = f"{type(e).__name__}: {e}"
    _trailing(data, end, out)
    main = None
    try:
        with warnings.catch_warnings():  # damaged EXIF is reported by our own parser; Pillow's warning is noise
            warnings.simplefilter("ignore")
            main = Image.open(io.BytesIO(data))
            main.load()
        out["image"] = {"format": main.format, "mode": main.mode, "size": list(main.size)}
    except Exception as e:  # noqa: BLE001
        out["image"] = {"error": f"{type(e).__name__}: {e}"}
        main = None
    thumb = out.pop("_thumb", None)
    if thumb:
        out["thumbnail"] = {"bytes": len(thumb)}
        if main is not None:
            out["thumbnail"].update(compare_thumbnail(main, thumb))
    if extract_dir:
        d = Path(extract_dir)
        d.mkdir(parents=True, exist_ok=True)
        saved = []
        if thumb:
            (d / "embedded_thumbnail.jpg").write_bytes(thumb)
            saved.append(str(d / "embedded_thumbnail.jpg"))
        if end is not None and end < len(data):
            (d / "data_after_end.bin").write_bytes(data[end:])
            saved.append(str(d / "data_after_end.bin"))
        out["extracted"] = saved
    out["notes"] = _notes(out)
    return out


def _notes(o):
    n = []
    cp = o.get("c2pa") or {}
    if cp.get("present"):
        n.append("C2PA content credentials present: " + (cp.get("meaning") or
                 f"generator {cp.get('generator') or 'unknown'} (signature not verified)."))
    if o.get("gps") and o["gps"].get("lat") is not None:
        n.append("GPS position present. It can be edited or planted: confirm it against the scene.")
    if o.get("gps") and o["gps"].get("image_direction_deg") is not None:
        n.append("Camera direction recorded (GPSImgDirection): usable as a bearing line.")
    if o.get("time", {}).get("note"):
        n.append(o["time"]["note"])
    if o.get("thumbnail", {}).get("differs"):
        n.append("Embedded thumbnail differs from the main image: the image may have been edited after capture. "
                 "Look at the extracted thumbnail.")
    if o.get("data_after_end"):
        n.append(f"{o['data_after_end']['bytes']} bytes after the end of the image (type: {o['data_after_end']['type']}).")
    if o.get("comments"):
        n.append("JPEG comment segment(s) present.")
    if o.get("text_chunks"):
        n.append("PNG text chunk(s) present.")
    if any(c.get("note") == "non-standard ancillary chunk" for c in o.get("chunks", [])):
        n.append("Non-standard PNG chunk(s) present: " + ", ".join(
            c["type"] for c in o["chunks"] if c.get("note") == "non-standard ancillary chunk") + ".")
    if any(not c.get("crc_ok", True) for c in o.get("chunks", [])):
        n.append("PNG chunk with a bad CRC: the file was modified by hand or is damaged.")
    if o.get("extension_mismatch"):
        n.append(o["extension_mismatch"])
    if o.get("iptc"):
        n.append("IPTC metadata present (may name a city or location).")
    if o.get("xmp"):
        n.append("XMP metadata present.")
    if not any(k in o for k in ("exif", "xmp", "iptc", "comments", "text_chunks", "c2pa")):
        n.append("No EXIF, XMP, IPTC, C2PA, comment or text metadata found.")
    return n
