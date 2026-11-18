"""Consistency checks between the facts a file carries. A mismatch means the file was edited, re-dated,
renamed, converted or its metadata planted; it never says where the photo was taken. Deterministic.

Each check: {"check", "result": ok | mismatch | warning | info | unknown, "detail"}.
"""
from __future__ import annotations

import datetime as dt
import re

EDITORS = ("photoshop", "lightroom", "gimp", "snapseed", "picsart", "canva", "pixelmator", "affinity", "paint.net",
           "facetune", "luminar", "capture one", "darktable", "rawtherapee", "remini", "faceapp")


def _exif_local(tm):
    s = (tm or {}).get("DateTimeOriginal")
    m = re.match(r"^(\d{4}):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)", str(s or ""))
    if not m:
        return None
    try:
        return dt.datetime(*map(int, m.groups()))
    except ValueError:
        return None


def _parse_utc(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(dt.UTC)
    except ValueError:
        return None


def _fmt_offset(minutes):
    sign = "-" if minutes < 0 else "+"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def checks(t, name_time=None, provenance=None, now=None):
    """Checks on a triage result `t`. Returns (checks, implied) where implied may hold a UTC offset worked out
    from GPS time vs local capture time (usable for the sun)."""
    out, implied = [], {}
    tm, gps, cam = t.get("time") or {}, t.get("gps") or {}, t.get("camera") or {}
    local = _exif_local(tm)
    gps_utc = _parse_utc(gps["gps_utc"]) if gps.get("gps_utc") else None
    exif_utc = _parse_utc(tm["utc_from_offset"]) if tm.get("utc_from_offset") else None

    # 1. GPS clock vs camera clock
    if gps_utc and exif_utc:
        diff = abs((gps_utc - exif_utc).total_seconds())
        out.append({"check": "GPS time vs capture time", "result": "ok" if diff <= 120 else "mismatch",
                    "detail": f"{diff / 60:.1f} min apart" + ("" if diff <= 120 else
                                                               ": one clock is wrong, or the metadata was changed")})
    elif gps_utc and local:
        mins = round((local - gps_utc.replace(tzinfo=None)).total_seconds() / 60 / 15) * 15
        if -12 * 60 <= mins <= 14 * 60:
            implied = {"utc_offset": _fmt_offset(mins), "from": "GPS time vs local capture time"}
            out.append({"check": "UTC offset from the GPS clock", "result": "info",
                        "detail": f"local capture time minus GPS UTC = {_fmt_offset(mins)}: the camera's time zone"})
        else:
            out.append({"check": "GPS time vs capture time", "result": "mismatch",
                        "detail": f"{mins / 60:+.1f} h apart: not a real time zone"})

    # 2. time zone at the GPS point vs the offset in the file
    offset = tm.get("OffsetTimeOriginal") or implied.get("utc_offset")
    when = exif_utc or gps_utc
    if gps.get("lat") is not None and offset and when:
        from .candidates import tz_test
        r = tz_test(gps["lat"], gps["lon"], when, offset)
        res = {"pass": "ok", "fail": "mismatch"}.get(r["result"], "unknown")
        det = (f"{offset} is used in {', '.join(r.get('countries', []))} at that date" if res == "ok" else
               f"{offset} is not used in {', '.join(r.get('countries', []))} at that date (offsets there: "
               + ", ".join(_fmt_offset(m) for m in r.get("zone_offsets_min", [])) + "): GPS or clock edited"
               if res == "mismatch" else r.get("why", ""))
        out.append({"check": "time zone at the GPS point", "result": res, "detail": det})

    # 3. the sun at the GPS point and time
    if gps.get("lat") is not None and when:
        from . import sun
        p = sun.position(gps["lat"], gps["lon"], when)
        if p["elevation"] < -6:
            out.append({"check": "daylight at the GPS point", "result": "warning",
                        "detail": f"the sun was {p['elevation']:.0f} deg below the horizon (night): a daylight photo "
                                  "contradicts the GPS or the time"})
        else:
            out.append({"check": "sun at the GPS point", "result": "info",
                        "detail": f"azimuth {p['azimuth']:.0f} deg, elevation {p['elevation']:.0f} deg; shadows "
                                  f"should point to {(p['azimuth'] + 180) % 360:.0f} deg"})

    # 4. dates: plausible, and the file name agrees
    if local:
        now = now or dt.datetime.now()
        if local > now + dt.timedelta(days=1):
            out.append({"check": "capture date", "result": "mismatch", "detail": f"{local:%Y-%m-%d} is in the future"})
        elif local.year < 1995:
            out.append({"check": "capture date", "result": "warning",
                        "detail": f"{local:%Y-%m-%d}: before digital cameras were common; often a reset clock"})
    if name_time and local:
        nd = name_time["local"][:10]
        same = nd == f"{local:%Y-%m-%d}"
        out.append({"check": "date in the file name vs capture date", "result": "ok" if same else "mismatch",
                    "detail": f"name {nd}, EXIF {local:%Y-%m-%d}" + ("" if same else ": the file was renamed, copied "
                                                                       "or saved again later")})

    # 5. traces of editing
    sw = " ".join(str(cam.get(k, "")) for k in ("Software",)) + " " + " ".join(
        str(i["value"]) for i in t.get("xmp", []) if i["name"].lower().endswith(("creatortool", "historysoftwareagent")))
    hits = sorted({e for e in EDITORS if e in sw.lower()})
    if hits:
        out.append({"check": "editing software", "result": "warning",
                    "detail": "names " + ", ".join(hits) + f" (field value: {sw.strip()[:80]})"})
    if (t.get("thumbnail") or {}).get("differs"):
        out.append({"check": "embedded thumbnail", "result": "mismatch",
                    "detail": "differs from the image: edited or cropped after capture"})
    if provenance and provenance.get("ai_generated"):
        out.append({"check": "declared provenance", "result": "mismatch",
                    "detail": "the file declares generative AI (C2PA / IPTC): not a photograph of a real place"})
    for p in gps.get("problems", []):
        out.append({"check": "GPS fields", "result": "mismatch" if "Null Island" in p or "outside" in p else "warning",
                    "detail": p})
    return out, implied
