"""orith: where was this photo taken? Offline, rule-based, honest (no AI, no OCR).

  orith IMAGE                    verdict, evidence with a confidence each, leads to check by hand
  orith IMAGE --text "SIGN TEXT" --drives right --place NAME     add what you see in the photo
  orith FOLDER                   every image in a folder: GPS, times, problems, duplicates
  orith scan --help              every option of the scan

Options for every command:
  --json         machine-readable JSON on stdout (or set ORITH_JSON=1)
  -v, --verbose  the full working: hypotheses, checks, sun, ledger; tracebacks on internal errors
  --no-online    never go online, even with --online (or set ORITH_NO_ONLINE=1)
  --no-color     plain text (also NO_COLOR=1);  --ascii  for consoles without Unicode

Exit codes: 0 done, 1 internal error, 2 bad input or usage, 130 interrupted.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

from . import ledger, paths


class _Lazy:
    """A module imported on first use: each command loads only what it needs (fast start)."""

    def __init__(self, name):
        self._name, self._mod = name, None

    def __getattr__(self, attr):
        if self._mod is None:
            import importlib
            self._mod = importlib.import_module(f"{__package__}.{self._name}")
        return getattr(self._mod, attr)


candidates, facts, geo, geocode, links, net, osm, packs, sun, text, triage, workflow = (
    _Lazy(n) for n in ("candidates", "facts", "geo", "geocode", "links", "net", "osm", "packs", "sun", "text",
                       "triage", "workflow"))

ROOT = Path(__file__).resolve().parent.parent


VISIBLE = ["scan", "case", "clue", "hyp", "link", "rank", "report", "tools", "doctor", "version"]
TOOLS = {  # orith tools NAME -> what for (each is also accepted as a top-level command, unlisted)
    "triage": "every fact the image file carries (EXIF, GPS, times, thumbnail, XMP, IPTC, ICC, hidden data)",
    "text": "scripts, phone numbers, domains, currencies in text you read in the photo",
    "facts": "countries consistent with clues (driving side, plugs, script, calling code ...), with sources",
    "sun": "sun position, shadow, elevation ring, day length, matching times",
    "tz": "UTC offset of a time zone, or every zone of a country",
    "geocode": "place name -> coordinates (offline packs first, then Nominatim)",
    "where": "offline reverse geocoding over the installed country packs",
    "osm": "OpenStreetMap feature search (Overpass) in a bounded area",
    "test": "check every hypothesis against a sun / bearing / distance / time-zone measurement",
    "convert": "any coordinate format -> all formats (DMS, UTM, MGRS, Plus Code, geohash)",
    "distance": "distance and bearings between two points",
    "destination": "point at a distance along a bearing",
    "intersect": "where two bearing lines meet",
    "resection": "camera position from bearings to known landmarks",
    "buildings": "buildings near a point from the optional offline layer (GlobalBuildingAtlas)",
    "ela": "error level analysis image of a JPEG: a visual aid to spot edited areas",
    "export": "a case as GeoJSON for QGIS or geojson.io (hypotheses, map evidence, file GPS)",
    "links": "map, street-level, imagery and reverse-image-search links (printed only)",
    "packs": "installed country packs with sources and licences",
}
STATE = {"json": True, "cmd": None, "style": None, "brief": False}  # main() sets these per call


def out(obj):
    if STATE["json"]:
        print(json.dumps(obj, ensure_ascii=False, indent=1))
        return
    from . import display
    st = STATE["style"] or display.make_style()
    print(display.render(STATE["cmd"], obj, st, brief=STATE["brief"]))


def tools_help():
    w = max(map(len, TOOLS))
    lines = [f"usage: {paths.APP} tools NAME [options]   ({paths.APP} tools NAME --help for its options)", ""]
    lines += [f"  {k:<{w}}  {v}" for k, v in TOOLS.items()]
    lines += ["", "Every tool prints text; add --json for JSON."]
    return "\n".join(lines)


def case_path(args):
    base = Path(args.case_dir or paths.cases_dir())
    if not args.case:
        raise ledger.LedgerError("--case ID is required")
    from .inputs import safe_case_id
    args.case = safe_case_id(args.case)  # one folder name: no path separators, no '..'
    return base / args.case / "case.json"


def floats(text, n, what):
    try:
        vals = [float(x) for x in str(text).split(",")]
    except ValueError:
        vals = []
    if len(vals) != n:
        raise ledger.LedgerError(f"{what} needs {n} comma-separated numbers, got {text!r}")
    return vals


def cmd_case_new(a):
    img = Path(a.image)
    if not img.is_file():
        raise ledger.LedgerError(f"image not found: {img}")
    a.case = a.case or img.stem
    path = case_path(a)
    if path.exists():
        return out({"case": a.case, "path": str(path), "note": "case already exists; continuing it"})
    sha = hashlib.sha256(img.read_bytes()).hexdigest()
    ledger.save(ledger.new_case(img, sha), path)
    out({"case": a.case, "path": str(path), "image": str(img)})


def cmd_case_show(a):
    out(ledger.load(case_path(a)))


def _record(a, results, source, query):
    """Store lookup results as evidence in the case (if one is given) and tag them with ids."""
    if not a.case:
        return results
    path = case_path(a)
    case = ledger.load(path)
    for r in results:
        extra = {k: r[k] for k in ("match", "matched_key") if r.get(k)}
        ev = ledger.add_evidence(case, source=r.get("source", source), lat=r["lat"], lon=r["lon"],
                                 name=r.get("name", ""), precision_m=r.get("precision_m", 0.0),
                                 ref=r.get("ref", ""), query=query, extra=extra or None)
        r["evidence"] = ev["id"]
    ledger.save(case, path)
    return results


def _safe_label(text, n=60):
    """A label that can be pasted into a command line (no quotes, no shell metacharacters)."""
    return re.sub(r"\s+", " ", re.sub(r"[\"'`;&|<>$\\\r\n]", " ", str(text))).strip()[:n] or "place"


def _evidence_hints(a, results):
    """What to do with recorded results: their evidence ids and the commands that use them."""
    if not a.case:
        return {"evidence": "not recorded: add --case ID to record these results as evidence ids (eN), "
                            "which hyp add --evidence and clue add --evidence accept"}
    ids = [r["evidence"] for r in results if r.get("evidence")]
    if not ids:
        return {"evidence_ids": []}
    first = results[0]
    return {"evidence_ids": ids,
            "next": [f"orith hyp add --case {a.case} --label \"{_safe_label(first.get('name', ''))}\" --evidence {ids[0]}",
                     f"orith clue add --case {a.case} --evidence {ids[0]} --text \"WHAT YOU SEE\" --kind SOFT "
                     f"--u 3 --p 4 --r 4 --group GROUP --supports hN"],
            "next_note": "Commands for the first result; use any id from evidence_ids instead. Replace the words in "
                         "capitals. Add --geometry to the clue only if a layout in the photo matches this map object."}


def cmd_geocode(a):
    viewbox = floats(a.bbox, 4, "--bbox") if a.bbox else None
    mode = "always" if a.online else "never" if a.offline else "fallback"
    results, note = geocode.search(a.query, limit=a.limit, countrycodes=a.country, viewbox=viewbox, use_online=mode)
    results = _record(a, results[: a.limit], "geocode", a.query)
    out({"query": a.query, "note": note, "results": results, **_evidence_hints(a, results),
         "fields": "match_score (offline gazetteer): 1.0 exact name, 0.9 all query words, else spelling similarity. "
                   "importance (Nominatim): how prominent the place is, NOT how well it matches the query; "
                   "small places are near 0.",
         "reminder": "precision_m is the uncertainty radius. An area's label point can rank a region; "
                     "it cannot count as a clue agreeing at one point."})


def cmd_where(a):
    hits = geocode.reverse(a.lat, a.lon)
    out({"lat": a.lat, "lon": a.lon, "packs_installed": [p.code for p in packs.installed()], "results": hits,
         "note": "" if hits else "no installed country pack covers this point; no offline answer"})


def cmd_triage(a):
    out(triage.triage(a.image, extract_dir=a.extract))


def _sun_time(a):
    t = sun.parse_time(a.time, a.utc_offset)
    return t


def cmd_sun_position(a):
    t = _sun_time(a)
    p = sun.position(a.lat, a.lon, t)
    sh = sun.shadow(a.lat, a.lon, t, object_height=a.height)
    out({"lat": a.lat, "lon": a.lon, "time_utc": t.astimezone(datetime.UTC).isoformat(),
         "sun_azimuth": round(p["azimuth"], 2), "sun_elevation_apparent": round(p["elevation"], 2),
         "shadow_bearing": round(sh["shadow_bearing"], 2),
         **({"shadow_length_per_unit_height": round(sh["shadow_length_per_unit_height"], 3)}
            if "shadow_length_per_unit_height" in sh else {"note": sh.get("note")}),
         **({"shadow_length": round(sh["shadow_length"], 2)} if "shadow_length" in sh else {}),
         "declination": round(p["declination"], 3), "equation_of_time_min": round(p["equation_of_time_min"], 2)})


def cmd_sun_ring(a):
    t = _sun_time(a)
    if a.elevation is not None:
        elev, how = a.elevation, "given"
    elif a.height and a.shadow:
        elev, how = sun.elevation_from_shadow(a.height, a.shadow), f"atan(height {a.height} / shadow {a.shadow})"
    else:
        raise ValueError("give --elevation, or --height and --shadow")
    fc = sun.elevation_ring(elev, t, tolerance=a.tolerance, time_error_min=a.time_error)
    fc["properties"]["elevation_source"] = how
    if a.out:
        Path(a.out).write_text(json.dumps(fc), encoding="utf-8")
        out({"written": a.out, "features": len(fc["features"]), **fc["properties"]})
    else:
        out(fc)


def cmd_sun_daylight(a):
    d = sun.daylight(a.lat, a.lon, datetime.date.fromisoformat(a.date))
    out({k: (v.isoformat() if hasattr(v, "isoformat") else (round(v, 3) if isinstance(v, float) else v))
         for k, v in d.items()} | {"lat": a.lat, "lon": a.lon, "date_utc": a.date})


def cmd_sun_latitude(a):
    out(sun.latitudes_for_day_length(a.day_length, datetime.date.fromisoformat(a.date), tolerance_h=a.tolerance))


def cmd_sun_times(a):
    runs = sun.times_matching(a.lat, a.lon, datetime.date.fromisoformat(a.date), shadow_bearing=a.shadow_bearing,
                              elevation=a.elevation, tolerance=a.tolerance)
    out({"lat": a.lat, "lon": a.lon, "local_solar_date": a.date, "tolerance_deg": a.tolerance,
         "shadow_bearing": a.shadow_bearing, "elevation": a.elevation, "matches": runs,
         "note": "" if runs else "no time that day matches within the tolerance"})


_MGRS_RE = r"^\s*\d{1,2}\s*[C-HJ-NP-X]\s*[A-HJ-NP-Z][A-HJ-NP-V]\s*(\d+\s*\d*)?\s*$"
_UTM_RE = r"^\s*(\d{1,2})\s*([C-HJ-NP-X]|[NS](?=\s))\s+(\d+(?:\.\d+)?)\s*m?\s*E?\s+(\d+(?:\.\d+)?)\s*m?\s*N?\s*$"


def _point(text):
    lat, lon = geo.parse_point(text)
    return lat, lon


def cmd_convert(a):
    import re as _re
    t = a.text.strip()
    if "+" in t and _re.search(r"[23456789CFGHJMPQRVWX]{2,8}\+", t.upper()):
        ref = _point(a.ref) if a.ref else (None, None)
        r = geo.from_pluscode(t, *ref)
        lat, lon, how = r["lat"], r["lon"], f"Plus Code {r['code']} (cell half-size {r['lat_error']:.6f} deg lat)"
    elif _re.match(_MGRS_RE, t.upper()):
        r = geo.from_mgrs(t)
        lat, lon, how = r["lat"], r["lon"], f"MGRS, {r['square_m']} m square, south-west corner"
    elif _re.match(_UTM_RE, t.upper()):
        m = _re.match(_UTM_RE, t.upper())
        zone, z2, e, n = int(m.group(1)), m.group(2), float(m.group(3)), float(m.group(4))
        hemi = "N" if z2 >= "N" else "S"
        lat, lon = geo.from_utm(zone, hemi, e, n)
        how = (f"UTM zone {zone}, letter {z2} read as a latitude band ({'northern' if hemi == 'N' else 'southern'} "
               f"hemisphere). If '{z2}' meant the hemisphere, the result is wrong: N and S are also band letters.")
    elif _re.fullmatch(r"[0-9bcdefghjkmnpqrstuvwxyz]{1,12}", t.lower()) and not t.isdigit() and not _re.search(r"[\s,]", t):
        r = geo.from_geohash(t)
        lat, lon, how = r["lat"], r["lon"], f"geohash (cell half-size {r['lat_error']:.6f} deg lat)"
    else:
        lat, lon = _point(t)
        how = "decimal or DMS"
    out({"input": a.text, "read_as": how, "lat": round(lat, 7), "lon": round(lon, 7),
         "formats": geo.all_formats(lat, lon), "packs": packs.locate(lat, lon)})


def cmd_distance(a):
    (la1, lo1), (la2, lo2) = _point(a.p1), _point(a.p2)
    out({"from": [la1, lo1], "to": [la2, lo2], "distance_km": round(geo.distance_km(la1, lo1, la2, lo2), 4),
         "bearing_from_to": round(geo.bearing(la1, lo1, la2, lo2), 2),
         "bearing_to_from": round(geo.bearing(la2, lo2, la1, lo1), 2), "model": "sphere R=6371.0088 km (about 0.5 %)"})


def cmd_destination(a):
    lat, lon = _point(a.point)
    la2, lo2 = geo.destination(lat, lon, a.bearing, a.km)
    res = {"from": [lat, lon], "bearing": a.bearing, "distance_km": a.km, "lat": round(la2, 7), "lon": round(lo2, 7)}
    if a.out:
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"bearing": a.bearing},
              "geometry": {"type": "LineString", "coordinates": geo.bearing_line(lat, lon, a.bearing, a.km)}}]}
        Path(a.out).write_text(json.dumps(fc), encoding="utf-8")
        res["written"] = a.out
    out(res)


def cmd_intersect(a):
    (la1, lo1), (la2, lo2) = _point(a.p1), _point(a.p2)
    p = geo.intersection(la1, lo1, a.b1, la2, lo2, a.b2)
    out({"lat": round(p[0], 7), "lon": round(p[1], 7)} if p else
        {"result": None, "note": "the two lines do not meet in front of both points"})


def cmd_resection(a):
    lms = []
    for spec in a.landmark:
        parts = [x.strip() for x in spec.split(",")]
        if len(parts) != 3:
            raise ValueError(f"--landmark needs LAT,LON,BEARING, got {spec!r}")
        lms.append(tuple(float(x) for x in parts))
    r = geo.resection_angles(lms) if a.relative else geo.resection_bearings(lms)
    r["bearings"] = "relative (unknown zero, solved)" if a.relative else "absolute (from true north)"
    out(r)


def _zone_table():
    """IANA zone.tab from the tzdata package: [(country_code, coordinates, zone, comment)]."""
    from importlib import resources

    import tzdata
    text = resources.files("tzdata").joinpath("zoneinfo", "zone.tab").read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        rows.append((parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else ""))
    return rows, getattr(tzdata, "IANA_VERSION", "unknown")


def cmd_tz(a):
    import zoneinfo
    if a.country:
        rows, ver = _zone_table()
        t = sun.parse_time(a.time, "+00:00") if a.time else datetime.datetime.now(datetime.UTC)
        zones = []
        for cc, coords, zone, comment in rows:
            if cc == a.country.upper():
                loc = t.astimezone(zoneinfo.ZoneInfo(zone))
                zones.append({"zone": zone, "comment": comment, "utc_offset": loc.strftime("%z"), "dst": bool(loc.dst()),
                              "local": loc.isoformat(), "reference_point_iso6709": coords})
        out({"country": a.country.upper(), "time_utc": t.astimezone(datetime.UTC).isoformat(),
             "zones": zones, "source": f"IANA tz database zone.tab (tzdata package, IANA version {ver})",
             "note": ("All zones listed for the country; pick from the scene. The toolkit does not guess a zone "
                      "from coordinates.") if zones else "no zone listed for this country code"})
        return
    if not a.zone:
        raise ValueError("give a zone name (e.g. Africa/Tunis) or --country CC")
    try:
        z = zoneinfo.ZoneInfo(a.zone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"unknown time zone {a.zone!r}: {e}") from e
    t = sun.parse_time(a.time, "+00:00") if a.time else datetime.datetime.now(datetime.UTC)
    loc = t.astimezone(z)
    out({"zone": a.zone, "utc": t.astimezone(datetime.UTC).isoformat(), "local": loc.isoformat(),
         "utc_offset": loc.strftime("%z"), "dst": bool(loc.dst()), "abbreviation": loc.tzname(),
         "note": "A time given without a zone is read as UTC."})


def cmd_links(a):
    if a.point:
        lat, lon = _point(a.point)
        out(links.all_links(lat, lon, zoom=a.zoom, utc_iso=a.time))
    else:
        out(links.all_links())




def _quick_summary(t):
    s = {"type": t.get("type"), "size": t.get("image", {}).get("size"), "notes": t.get("notes", [])}
    if t.get("gps"):
        s["gps"] = t["gps"]
    if t.get("time"):
        s["time"] = t["time"]
    if t.get("camera"):
        s["camera"] = {k: v for k, v in t["camera"].items() if k in ("Make", "Model", "Software", "LensModel",
                                                                      "FocalLengthIn35mmFilm", "fov_long_side_deg",
                                                                      "ImageDescription", "Artist", "UserComment")}
    if t.get("thumbnail"):
        s["thumbnail"] = {k: t["thumbnail"].get(k) for k in ("differs", "dhash_hamming", "mean_abs_diff")}
    if t.get("data_after_end"):
        s["data_after_end"] = {k: t["data_after_end"][k] for k in ("bytes", "type")}
    if (t.get("c2pa") or {}).get("present"):
        s["c2pa"] = t["c2pa"]
    loc_keys = ("city", "country", "location", "state", "sublocation", "province", "gps", "heading", "description",
                "headline", "caption", "subject", "keywords", "title")
    picks = [i for i in t.get("xmp", []) if any(k in i["name"].lower() for k in loc_keys)]
    picks += [{"name": f"IPTC {k}", "value": v} for k, v in t.get("iptc", [])]
    picks += [{"name": f"PNG {c['key']}", "value": c["value"]} for c in t.get("text_chunks", [])]
    picks += [{"name": "JPEG comment", "value": c} for c in t.get("comments", [])]
    if picks:
        s["text_fields"] = picks[:40]
    return s


def _can_ask(a):
    """Questions only when asked for (--ask): the default scan runs on its own, with no prompts."""
    return bool(a.interactive) and not (a.no_interactive or a.yes)


def _progress(a):
    if STATE["json"] or getattr(a, "quiet", False):
        return None
    from . import display
    st = display.make_style(stream=sys.stderr, no_color=STATE.get("no_color", False))
    marks = {"+": ("[+]", "green"), "-": ("[-]", "dim"), "!": ("[!]", "yellow"), "*": ("[*]", "cyan")}

    def log(mark, label, detail=""):
        sym, color = marks.get(mark, ("[*]", "cyan"))
        sys.stderr.write(f"{st.c(sym, 'bold', color)} {label} ... {st.t(detail)}\n")
        sys.stderr.flush()
    return log


def cmd_scan(a):
    from . import inputs, interview, scan
    if Path(a.image).is_dir():  # folder triage
        from . import batch
        STATE["cmd"] = "batch"
        res = batch.run(a.image, recursive=a.recursive, log=_progress(a))
        if a.geojson:
            Path(a.geojson).write_text(json.dumps(batch.geojson(res), ensure_ascii=False), encoding="utf-8")
            res["geojson"] = a.geojson
        out(res)
        return
    img = inputs.check_file(a.image)
    t = triage.triage(img)
    warning = inputs.check_triage(t, img)  # raises for a non-image; returns a warning for damaged data
    a.case = a.case or Path(a.image).stem
    path = case_path(a)
    log = _progress(a)
    if warning and log:
        log("!", "Image data", warning)
    if _can_ask(a):
        interview.run(a, t)
    res = scan.run(a, path, log=log, t=t)
    if warning:
        res["warning"] = warning
    if a.geojson:
        Path(a.geojson).write_text(json.dumps(case_geojson(ledger.load(path), path.parent), ensure_ascii=False),
                                   encoding="utf-8")
        res["geojson"] = a.geojson
    out(res)


def case_geojson(case, case_dir=None):
    """Hypotheses, map evidence and the file's GPS of a case as a GeoJSON FeatureCollection (QGIS, geojson.io)."""
    ev = ledger.evaluate(case)
    rank = {r["id"]: r for r in ev["ranking"]}
    feats = []
    for h in case["hypotheses"]:
        r = rank.get(h["id"], {})
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [h["lon"], h["lat"]]},
                      "properties": {"kind": "hypothesis", "id": h["id"], "label": h["label"],
                                     "status": r.get("status"), "relative_score": r.get("score"),
                                     "top": h["id"] == ev["top"], "verdict": ev["verdict"] if h["id"] == ev["top"] else None}})
    for e in case["evidence"]:
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]},
                      "properties": {"kind": "evidence", "id": e["id"], "name": e.get("name"), "source": e["source"],
                                     "precision_m": e["precision_m"]}})
    tri = Path(case_dir) / "triage.json" if case_dir else None
    if tri and tri.exists():
        g = (json.loads(tri.read_text(encoding="utf-8")).get("gps") or {})
        if g.get("lat") is not None:
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [g["lon"], g["lat"]]},
                          "properties": {"kind": "file GPS", "note": "can be edited or planted"}})
    return {"type": "FeatureCollection", "features": feats}


def cmd_export(a):
    path = case_path(a)
    data = case_geojson(ledger.load(path), path.parent)
    dst = Path(a.out or f"{a.case}.geojson")
    dst.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out({"written": str(dst), "features": len(data["features"]),
         "open_with": "QGIS (drag the file in) or https://geojson.io (open the file by hand)"})


def cmd_ela(a):
    from . import ela
    dst = a.out or str(Path(a.image).with_name(Path(a.image).stem + "_ela.png"))
    out(ela.run(a.image, out=dst, quality=a.quality))


def cmd_buildings(a):
    from . import buildings
    if a.index:
        out(buildings.build_index(a.index, source={"file": str(a.index)}))
        return
    installed = buildings.tiles()
    if a.list or not a.point:
        out({"buildings_dir": str(paths.buildings_dir()),
             "tiles": [{"tile": m["tile"], "bbox": m["bbox"], "located": m["counts"]["located"],
                        "no_position_in_id": m["counts"]["no_position_in_id"], "licence": m["licence"]}
                       for m, _ in installed],
             "note": "install tiles with: python tools/fetch_gba.py --bbox S,W,N,E" if not installed else
             buildings.CITATION})
        return
    lat, lon = _point(a.point)
    r = buildings.near(lat, lon, a.radius)
    if r.get("covered"):
        r["setting_by_rule"] = next((k for k in buildings.SETTINGS if buildings.setting_matches(k, r["count"])), None) \
            if a.radius == 500 else None
        r["rule"] = buildings.SETTING_RULE
        r["meaning"] = ("ML-derived building positions (Google Open Buildings ids) and heights in metres with a global "
                        "RMSE of about 5.5 m: a hint to check on imagery, not proof")
    out(r)


def cmd_version(a):
    from . import __version__
    out({"name": paths.APP, "version": __version__})


def cmd_tools(a):
    print(tools_help())


def cmd_quick(a):
    img = Path(a.image)
    if not img.is_file():
        raise ledger.LedgerError(f"image not found: {img}")
    a.case = a.case or img.stem
    path = case_path(a)
    created = not path.exists()
    if created:
        ledger.save(ledger.new_case(img, hashlib.sha256(img.read_bytes()).hexdigest()), path)
    t = triage.triage(img, extract_dir=path.parent / "extracted")
    (path.parent / "triage.json").write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
    tim = workflow.timer(path.parent, "show")
    if not tim["running"]:
        tim = workflow.timer(path.parent, "start", "quick")
    gps = t.get("gps") or {}
    utc = gps.get("gps_utc") or (t.get("time") or {}).get("utc_from_offset")
    if gps.get("lat") is not None:
        lk = links.all_links(gps["lat"], gps["lon"], utc_iso=utc)
        lk["from"] = "EXIF GPS (can be edited or planted: verify against the scene)"
    else:
        lk = links.all_links()
        lk["from"] = "no position in the file: reverse image search pages only"
    nxt = []
    nxt.append(f"orith triage {img}   (full detail, also saved to {path.parent / 'triage.json'})")
    if gps.get("image_direction_deg") is not None:
        nxt.append(f"orith destination \"{gps['lat']},{gps['lon']}\" --bearing {gps['image_direction_deg']} --km 2 "
                   "--out view.geojson   (camera direction line)")
    if utc:
        nxt.append(f"orith sun ring --time {utc} --elevation E   (after measuring a shadow)")
    nxt.append(f"orith report --case {a.case}")
    out({"case": a.case, "case_created": created, "case_dir": str(path.parent), "timer": tim,
         "facts": _quick_summary(t), "extracted": t.get("extracted", []), "links": lk,
         "checklist": [s for s, _ in workflow.STEPS], "next": nxt})


def cmd_facts(a):
    if a.country:
        out(facts.country_facts(a.country))
        return
    clues = [(k, v) for k in facts.CLUES for v in (getattr(a, k) or [])]
    point = _point(a.point) if a.point else None
    if not clues and not point:
        raise ValueError("give at least one clue (e.g. --drives left --plug G) or --point, or --country CC")
    r = facts.filter_countries(clues, point=point)
    if not a.full:
        for c in r["consistent"]:
            for cl in c["clues"]:
                cl["facts"] = [{k: f[k] for k in ("value", "status", "source", "retrieved") if k in f} | (
                    {"note": f["note"][:120]} if "note" in f and cl["result"] != "match" else {}) for f in cl["facts"]]
        r["excluded"] = [{"iso2": e["iso2"], "country": e["country"],
                          "excluded_by": "; ".join(f"{w['clue']}: " + ", ".join(sorted({f['value'] for f in w['facts']}))
                                                   + f" ({w['facts'][0]['source'].split(',')[0]}, "
                                                     f"{w['facts'][0].get('retrieved', '')})"
                                                   for w in e["excluded_by"])} for e in r["excluded"]]
    out(r)


def cmd_text(a):
    out(text.analyse(a.text, region=a.region.upper() if a.region else None, all_regions=a.all_regions))


def cmd_test(a):
    params = {"tolerance": a.tolerance} if a.tolerance is not None else {}
    if a.test == "sun":
        if not a.time_source:
            raise ValueError("sun test needs --time-source gps|exif-with-offset|exif-no-offset|manual")
        params["time_source"] = a.time_source
        if a.time_source == "exif-no-offset":
            if a.tz:
                import zoneinfo
                naive = datetime.datetime.fromisoformat(a.time.replace("Z", "+00:00")).replace(tzinfo=None)
                params["time"] = naive.replace(tzinfo=zoneinfo.ZoneInfo(a.tz))
                params["time_source"] = f"exif-no-offset, read as local time in {a.tz}"
            else:
                params["why_no_time"] = "EXIF time has no UTC offset and no --tz was given: the instant is unknown"
        elif a.time:
            params["time"] = sun.parse_time(a.time, a.utc_offset)
    elif a.time:
        params["time"] = sun.parse_time(a.time, None)
    if a.test == "sun":
        params.update(shadow_bearing=a.shadow_bearing, elevation=a.elevation, time_error=a.time_error)
        if a.height and a.shadow:
            params["elevation"] = sun.elevation_from_shadow(a.height, a.shadow)
    elif a.test in ("bearing", "distance"):
        if not a.landmark:
            raise ValueError("--landmark LAT,LON is required")
        params["landmark"] = a.landmark.strip() if a.landmark.strip().startswith("e") else _point(a.landmark)
        if a.test == "bearing":
            if a.bearing is None:
                raise ValueError("--bearing is required")
            params["bearing"] = a.bearing
        else:
            params.update(min_km=a.min_km or 0.0, max_km=a.max_km)
    elif a.test == "tz":
        if not a.utc_offset:
            raise ValueError("--utc-offset is required (e.g. +01:00)")
        params["utc_offset"] = a.utc_offset
    r = candidates.run(case_path(a), a.test, params, hyps=a.hyp, record=not a.dry_run, kind=a.kind,
                       u=a.u, p=a.p, r=a.r, group=a.group)
    r["effect_on_verdict"] = candidates.EFFECT_ON_VERDICT
    out(r)


def cmd_packs(a):
    out({"packs_dir": str(packs.packs_dir()), "packs": [p.info() for p in packs.installed()]})




def cmd_doctor(a):
    from . import doctor
    report = doctor.check()
    out(report)
    if not report["ok"]:
        raise SystemExit(1)




def cmd_osm(a):
    spec = {"limit": a.limit}
    if a.feature:
        spec["feature"] = a.feature
    if a.name:
        spec["name"] = a.name
    if a.translit:
        spec["translit"] = True
    if a.tag:
        spec["tags"] = dict(t.split("=", 1) if "=" in t else (t, "*") for t in a.tag)
    if a.near_feature:
        spec["near"] = {"feature": a.near_feature, "within_m": a.within}
    if a.around:
        spec["area"] = {"around": floats(a.around, 3, "--around")}
    elif a.bbox:
        spec["area"] = {"bbox": floats(a.bbox, 4, "--bbox")}
    elif a.area:
        hits = packs.find_area(a.area, geocode.norm)
        if not hits:
            raise osm.SpecError(f"no admin unit named '{a.area}' in the installed packs; use --around or --bbox")
        if len({(h["pack"], h["id"]) for h in hits}) > 1:
            raise osm.SpecError("ambiguous --area: " + "; ".join(f"{h['pack']} {h['label']} {h['name']} ({h['id']})"
                                                                  for h in hits[:8]) + ". Use an id from this list.")
        w, s_, e, n = hits[0]["bbox"]
        spec["area"] = {"bbox": [s_, w, n, e]}
    else:
        raise osm.SpecError("give the search area: --around LAT,LON,RADIUS_M or --bbox S,W,N,E or --area NAME")
    query = osm.build_query(spec)
    if a.dry_run:
        return out({"spec": spec, "query": query})
    try:
        elements, query, cached = osm.run(spec, log=lambda st, sec, d: _log_overpass(a, st, sec, d))
    except net.NetError as e:
        area = spec["area"]
        if "around" in area:
            clat, clon = area["around"][0], area["around"][1]
        else:
            s_, w, n, e_ = area["bbox"]
            clat, clon = (s_ + n) / 2, (w + e_) / 2
        import urllib.parse as _up
        fb = links.place_links(clat, clon, zoom=16)
        out({"error": f"Overpass did not answer: {e}",
             "overpass_unavailable": True,
             "meaning": "Nothing was concluded: an unreachable map service says nothing about the place.",
             "do_by_hand": "Open the query in Overpass turbo, or check the area on the map links below.",
             "query": query,
             "overpass_turbo_with_query": "https://overpass-turbo.eu/?Q=" + _up.quote(query) + f"&C={clat:.5f};{clon:.5f};16",
             "map_links": fb["maps"], "street_level_links": fb["street_level"],
             "endpoints_tried": osm.endpoints(), "log": "cases/overpass.jsonl"})
        a.exit_code = 2
        return
    part, pages = osm.page(elements, a.page, a.show)
    shown = _record(a, part, "osm", query)
    first = (a.page - 1) * a.show + 1
    msg = (f"{len(elements)} match(es)" + (" (cached)" if cached else "") +
           (f", showing {first}-{first + len(shown) - 1} (page {a.page} of {pages})" if pages > 1 else ""))
    if len(elements) >= spec["limit"]:
        msg += (f". The limit of {spec['limit']} was reached: more may exist. Narrow the area, or raise --limit "
                f"(hard cap {osm.MAX_LIMIT})")
    if a.page < pages:
        msg += f". Next page: same command with --page {a.page + 1} (served from the cache, no new request)"
    if not elements:
        msg += ". An empty result does NOT rule the area out: OpenStreetMap is incomplete."
    fuzzy = sum(e.get("match") == "fuzzy" for e in elements)
    if fuzzy:
        msg += (f". {fuzzy} match(es) are FUZZY: only the loose --translit pattern matched, not the name you "
                "typed; read all_names before using one.")
    out({"spec": spec, "query": query, "note": msg, "results": shown, **_evidence_hints(a, shown)})


def _id_list(text):
    return [x.strip() for x in str(text or "").split(",") if x.strip()]


def _links_from(a, pos_flag, neg_flag):
    """[(other_id, relation)] from two comma lists; an id in both lists is an error."""
    pos, neg = _id_list(getattr(a, pos_flag)), _id_list(getattr(a, neg_flag))
    both = sorted(set(pos) & set(neg))
    if both:
        raise ledger.LedgerError(f"{', '.join(both)} cannot both support and contradict")
    return [(x, "supports") for x in pos] + [(x, "contradicts") for x in neg]


def cmd_clue_add(a):
    """Add a clue and, in the same call, link it to hypotheses. Nothing is saved if any id is wrong."""
    path = case_path(a)
    case = ledger.load(path)
    pairs = _links_from(a, "supports", "contradicts")
    clue = ledger.add_clue(case, a.text, a.kind, a.u, a.p, a.r, a.group, geometry=a.geometry, evidence=a.evidence)
    links = [ledger.add_link(case, clue["id"], h, rel, a.note or "") for h, rel in pairs]
    ledger.save(case, path)
    out({"clue": clue, "weight": round(ledger.weight(clue), 3), "links": links})


def cmd_hyp_add(a):
    """Add a hypothesis and, in the same call, link existing clues to it. Nothing is saved if any id is wrong."""
    path = case_path(a)
    case = ledger.load(path)
    pairs = _links_from(a, "supported_by", "contradicted_by")
    hyp = ledger.add_hypothesis(case, a.label, a.lat, a.lon, evidence=a.evidence)
    links = [ledger.add_link(case, c, hyp["id"], rel, a.note or "") for c, rel in pairs]
    ledger.save(case, path)
    out({"hypothesis": hyp, "links": links})


def cmd_link(a):
    path = case_path(a)
    case = ledger.load(path)
    link = ledger.add_link(case, a.clue, a.hyp, a.rel, a.note or "")
    ledger.save(case, path)
    out({"link": link})


SCORE_NOTE = ("relative_score = sum of the weights (u*p*r/125) of supporting clues minus those of contradicting SOFT "
              "clues. It only orders the hypotheses of this case: it is not a probability or a confidence. "
              "map_anchored_clues = supporting clues that cite recorded map evidence (eN); only those can count "
              "toward IDENTIFIED (agreeing_clues: anchored AND within agree_m).")


def _rank_view(case, ev):
    """The ledger's evaluation, presented for reading: score renamed and the anchored-clue count beside it."""
    anchored = {c["id"] for c in case["clues"] if c.get("evidence")}
    rows = []
    for r in ev["ranking"]:
        row = {k: r[k] for k in ("id", "label", "lat", "lon", "source", "status", "eliminated_by")}
        row["relative_score"] = r["score"]
        row["map_anchored_clues"] = sum(c in anchored for c in r["supports"])
        row.update({k: v for k, v in r.items() if k not in row and k != "score"})
        rows.append(row)
    return {**ev, "ranking": rows, "score_note": SCORE_NOTE}


def cmd_rank(a):
    case = ledger.load(case_path(a))
    out(_rank_view(case, ledger.evaluate(case, agree_m=a.agree_m)))


NAME_MATCH_NOTE = "name matched to map object (not geometry; does not count toward IDENTIFIED)"


def _norm_name(text):
    t = unicodedata.normalize("NFKD", str(text)).casefold()
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", " ", t).strip()


def name_matches(case, agree_m=ledger.AGREE_M_DEFAULT):
    """Informational only: (hypothesis, clue, evidence, distance_m) where a supporting clue's text contains the
    name of a recorded map object that lies within agree_m of the hypothesis. The verdict never uses this."""
    out_, hyps = [], {h["id"]: h for h in case["hypotheses"]}
    clues = {c["id"]: c for c in case["clues"]}
    for l in case["links"]:
        if l["relation"] != "supports" or l["hypothesis"] not in hyps or l["clue"] not in clues:
            continue
        h, text = hyps[l["hypothesis"]], _norm_name(clues[l["clue"]]["text"])
        for e in case["evidence"]:
            first = str(e.get("name", "")).split(",")[0]
            names = {_norm_name(first), _norm_name(re.sub(r"\([^)]*\)", " ", first))}
            if not any(len(n) >= 4 and n in text for n in names):
                continue
            d = ledger.hav_m((h["lat"], h["lon"]), (e["lat"], e["lon"]))
            if d <= agree_m:
                out_.append((h["id"], l["clue"], e["id"], first.strip(), d))
    return out_


def cmd_report(a):
    case = ledger.load(case_path(a))
    ev = ledger.evaluate(case, agree_m=a.agree_m)
    lines = [f"CASE {a.case}   image: {case['image']}", f"VERDICT: {ev['verdict']}"]
    rows = {r["id"]: r for r in ev["ranking"]}
    if ev["top"]:
        t = rows[ev["top"]]
        lines.append(f"Best hypothesis: {t['id']} {t['label']}  ({t['lat']:.5f}, {t['lon']:.5f})  "
                     f"coordinates from {t['source']}")
    if ev["verdict"] == "CANDIDATE":
        lines.append("This is a candidate, not an identification. Unmet conditions:")
        lines += [f"  - {u}" for u in ev["unmet"]]
    lines.append("Ranking:")
    for r in _rank_view(case, ev)["ranking"]:
        state = "ELIMINATED by " + ",".join(r["eliminated_by"]) if r["status"] == "eliminated" else "alive"
        lines.append(f"  {r['id']:<4} relative score {r['relative_score']:+.3f} (map-anchored clues "
                     f"{r['map_anchored_clues']})  {state:<22} agreeing {r['independent_agreeing']} "
                     f"(geometry {len(r['geometry_agreeing'])})  contradictions {len(r['contradicts'])}  {r['label']}")
    lines.append("Relative score orders the hypotheses of this case only; it is not a confidence.")
    for hid, cid, eid, name, d in name_matches(case, a.agree_m):
        lines.append(f"  {hid}: clue {cid} names '{name}' = evidence {eid} at {d:.0f} m: {NAME_MATCH_NOTE}")
    if ev["warnings"]:
        lines.append("Warnings:")
        lines += [f"  - {w}" for w in ev["warnings"]]
    out({"verdict": ev["verdict"], "top": ev["top"], "unmet": ev["unmet"], "text": "\n".join(lines)})


def build_parser():
    ap = argparse.ArgumentParser(prog=paths.APP, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--case", help="case id (the image file name without extension)")
    common.add_argument("--case-dir", help="folder holding cases (default: ./cases)")
    ap.add_argument("--version", action="version", version=f"{paths.APP} " + __import__(__package__).__version__)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    s = sub.add_parser("scan", parents=[common], help="look at an image with every automatic step and record a case "
                                                      "(same as: orith IMAGE)",
                       description="Read the file, check it against itself, combine every clue into one best guess "
                                   "with a confidence level. Offline unless --online.",
                       epilog="Also: --json, -v/--verbose, --no-online, --no-color (see: orith --help).")
    s.add_argument("image", metavar="IMAGE|FOLDER", help="JPEG, PNG, WebP, TIFF or HEIC file, or a folder of them")
    g = s.add_argument_group("what you see in the photo (orith does not read pixels)")
    g.add_argument("--text", action="append", help="text you read: signs, shop names, phone numbers, web addresses "
                                                   "(repeat for several)")
    g.add_argument("--drives", action="append", choices=["left", "right"], help="the side cars drive on")
    g.add_argument("--plug", action="append", help="plug or socket type letter seen (A-N)")
    g.add_argument("--place", action="append", help="a place name or landmark you can read")
    g.add_argument("--note", action="append", help="anything else; recorded, not used to narrow countries")
    g.add_argument("--setting", choices=["city", "town", "village", "open"],
                   help="how built-up the place looks; tested on building counts when the building layer is installed")
    g.add_argument("--lat", type=float, help="a point you want checked as a hypothesis (with --lon)")
    g.add_argument("--lon", type=float)
    g.add_argument("--ask", "--interactive", dest="interactive", action="store_true",
                   help="answer short questions instead of typing options")
    g = s.add_argument_group("time and sun")
    g.add_argument("--time", help="capture time as ISO with zone, if the file has none or it is wrong")
    g.add_argument("--utc-offset", help="zone of the EXIF capture time when the file has none, e.g. +01:00")
    g.add_argument("--shadow-bearing", type=float, help="direction a shadow points to (degrees from north)")
    g.add_argument("--elevation", type=float, help="measured sun elevation (degrees)")
    g.add_argument("--height", type=float, help="height of an object (with --shadow: its shadow length)")
    g.add_argument("--shadow", type=float, help="shadow length of that object (same unit as --height)")
    g = s.add_argument_group("online and output")
    g.add_argument("--online", action="store_true",
                   help="street address of the GPS point and places not in the offline data, from OpenStreetMap "
                        "Nominatim (needs a contact: see orith doctor)")
    g.add_argument("--no-record", action="store_true", help="show the results without writing to the case")
    g.add_argument("--quiet", action="store_true", help="no [+] progress lines")
    g.add_argument("--brief", action="store_true", help=argparse.SUPPRESS)  # handled globally; listed for --help
    g.add_argument("--no-ela", action="store_true", help="skip error level analysis of JPEG files")
    g.add_argument("--geojson", help="also write the result as GeoJSON (hypotheses, evidence, GPS) to this file")
    g.add_argument("--recursive", action="store_true", help="folder: include subfolders")
    s.add_argument("--no-interactive", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("export", parents=[common], help="a case as GeoJSON (hypotheses, map evidence, file GPS)")
    s.add_argument("--out", help="file to write (default: <case>.geojson)")
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("ela", help="error level analysis image of a JPEG (edit check, visual aid)")
    s.add_argument("image")
    s.add_argument("--out", help="PNG to write (default: <image>_ela.png)")
    s.add_argument("--quality", type=int, default=90)
    s.set_defaults(fn=cmd_ela)
    s = sub.add_parser("buildings", help="buildings near a point (optional offline layer)")
    s.add_argument("point", nargs="?", help="LAT,LON (any coordinate format)")
    s.add_argument("--radius", type=float, default=500, help="metres (default 500)")
    s.add_argument("--list", action="store_true", help="list the installed building tiles")
    s.add_argument("--index", help="build the index from a downloaded GBA.LoD1 tile (.json)")
    s.set_defaults(fn=cmd_buildings)
    sub.add_parser("tools", help="helpers: sun, time zones, facts, text, maps, coordinates, links "
                                 "(orith tools --help)").set_defaults(fn=cmd_tools)
    sub.add_parser("version", help="print the version").set_defaults(fn=cmd_version)

    p = sub.add_parser("case", help="start or show a case").add_subparsers(dest="sub", required=True)
    s = p.add_parser("new", parents=[common])
    s.add_argument("image")
    s.set_defaults(fn=cmd_case_new)
    p.add_parser("show", parents=[common]).set_defaults(fn=cmd_case_show)

    s = sub.add_parser("geocode", parents=[common], help="place name -> coordinates")
    s.add_argument("query")
    s.add_argument("--country", help="ISO country code to restrict Nominatim, e.g. tn")
    s.add_argument("--bbox", help="S,W,N,E to restrict Nominatim")
    s.add_argument("--limit", type=int, default=8)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--online", action="store_true", help="also ask Nominatim even if the offline gazetteer matched")
    g.add_argument("--offline", action="store_true", help="never use the network")
    s.set_defaults(fn=cmd_geocode)

    s = sub.add_parser("where", help="offline reverse geocoding over the installed country packs")
    s.add_argument("lat", type=float)
    s.add_argument("lon", type=float)
    s.set_defaults(fn=cmd_where)

    s = sub.add_parser("osm", parents=[common], help="clue -> OpenStreetMap query")
    s.add_argument("--feature", help="feature word, e.g. mosque, water_tower, roundabout (see --list)")
    s.add_argument("--name", help="case-insensitive regex, searched in name, name:ar/fr/en, alt_name, old_name, "
                                  "official_name, int_name (one filter); Arabic alef/taa marbuta/yaa forms are merged")
    s.add_argument("--translit", action="store_true",
                   help="treat --name as a plain Latin name and search a loose transliteration pattern "
                        "(needs --bbox and 3+ consonants); such hits are labelled match=fuzzy")
    s.add_argument("--tag", action="append", help="raw tag k=v (repeatable)")
    s.add_argument("--near-feature", help="second feature that must be close to the first")
    s.add_argument("--within", type=float, default=150, help="metres for --near-feature (default 150)")
    s.add_argument("--around", help="LAT,LON,RADIUS_M (max 100000)")
    s.add_argument("--bbox", help="S,W,N,E (max 3 degrees per side)")
    s.add_argument("--area", help="name or id of an admin unit in an installed country pack: uses its bounding box")
    s.add_argument("--limit", type=int, default=osm.DEFAULT_LIMIT,
                   help=f"max elements asked from Overpass in one request (default {osm.DEFAULT_LIMIT}, "
                        f"hard cap {osm.MAX_LIMIT})")
    s.add_argument("--show", type=int, default=20, help="results per page, printed and recorded as evidence")
    s.add_argument("--page", type=int, default=1, help="page of the fetched result (later pages come from the cache)")
    s.add_argument("--dry-run", action="store_true", help="print the query without sending it")
    s.set_defaults(fn=cmd_osm)

    p = sub.add_parser("clue", help="record a clue").add_subparsers(dest="sub", required=True)
    s = p.add_parser("add", parents=[common])
    s.add_argument("--text", required=True)
    s.add_argument("--kind", required=True, choices=["HARD", "SOFT", "hard", "soft"])
    s.add_argument("--u", type=int, required=True, help="uniqueness 1-5")
    s.add_argument("--p", type=int, required=True, help="permanence 1-5")
    s.add_argument("--r", type=int, required=True, help="reliability 1-5")
    s.add_argument("--group", required=True, help="independence group: clues that are not independent share a group")
    s.add_argument("--geometry", action="store_true", help="fixed geometry matched against map evidence")
    s.add_argument("--evidence", help="evidence id (eN) this clue is anchored to; geocode/osm with --case print these ids")
    s.add_argument("--supports", help="hypothesis ids this clue supports, comma-separated (h1,h2): links in the same call")
    s.add_argument("--contradicts", help="hypothesis ids this clue contradicts, comma-separated")
    s.add_argument("--note", help="note stored on the links made by --supports/--contradicts")
    s.set_defaults(fn=cmd_clue_add)

    p = sub.add_parser("hyp", help="record a hypothesis").add_subparsers(dest="sub", required=True)
    s = p.add_parser("add", parents=[common])
    s.add_argument("--label", required=True)
    s.add_argument("--lat", type=float)
    s.add_argument("--lon", type=float)
    s.add_argument("--evidence", help="take the coordinates from this evidence id (geocode/osm with --case print them)")
    s.add_argument("--supported-by", help="existing clue ids that support this hypothesis, comma-separated (c1,c2)")
    s.add_argument("--contradicted-by", help="existing clue ids that contradict it, comma-separated")
    s.add_argument("--note", help="note stored on the links made by --supported-by/--contradicted-by")
    s.set_defaults(fn=cmd_hyp_add)

    s = sub.add_parser("link", parents=[common], help="say that a clue supports or contradicts a hypothesis")
    s.add_argument("--clue", required=True)
    s.add_argument("--hyp", required=True)
    s.add_argument("--rel", required=True, choices=list(ledger.RELATIONS))
    s.add_argument("--note")
    s.set_defaults(fn=cmd_link)

    p = sub.add_parser("sun", help="sun position, shadow, elevation ring, day length").add_subparsers(dest="sub", required=True)
    tz = dict(help="ISO time with zone (2024-06-21T14:30:00+02:00 or ...Z)")
    s = p.add_parser("position", help="sun azimuth/elevation and shadow bearing at a place and time")
    s.add_argument("lat", type=float)
    s.add_argument("lon", type=float)
    s.add_argument("--time", required=True, **tz)
    s.add_argument("--utc-offset", help="zone for a time written without one, e.g. +01:00")
    s.add_argument("--height", type=float, help="object height, to get its shadow length")
    s.set_defaults(fn=cmd_sun_position)
    s = p.add_parser("ring", help="every place where the sun had this elevation at this time (GeoJSON band)")
    s.add_argument("--time", required=True, **tz)
    s.add_argument("--utc-offset")
    s.add_argument("--elevation", type=float, help="apparent sun elevation in degrees")
    s.add_argument("--height", type=float, help="object height (any unit), with --shadow")
    s.add_argument("--shadow", type=float, help="shadow length (same unit as --height)")
    s.add_argument("--tolerance", type=float, default=2.0, help="elevation error in degrees (default 2)")
    s.add_argument("--time-error", type=float, default=0.0, help="time error in minutes (default 0)")
    s.add_argument("--out", help="write the GeoJSON to this file")
    s.set_defaults(fn=cmd_sun_ring)
    s = p.add_parser("daylight", help="sunrise, solar noon, sunset (UTC) and day length")
    s.add_argument("lat", type=float)
    s.add_argument("lon", type=float)
    s.add_argument("--date", required=True, help="YYYY-MM-DD (UTC date)")
    s.set_defaults(fn=cmd_sun_daylight)
    s = p.add_parser("latitude", help="latitude bands with this day length on this date")
    s.add_argument("--day-length", type=float, required=True, help="hours")
    s.add_argument("--date", required=True)
    s.add_argument("--tolerance", type=float, default=0.25, help="hours (default 0.25)")
    s.set_defaults(fn=cmd_sun_latitude)
    s = p.add_parser("times", help="times of day at a known place matching a shadow bearing and/or elevation")
    s.add_argument("lat", type=float)
    s.add_argument("lon", type=float)
    s.add_argument("--date", required=True, help="local date YYYY-MM-DD")
    s.add_argument("--shadow-bearing", type=float)
    s.add_argument("--elevation", type=float)
    s.add_argument("--tolerance", type=float, default=2.0, help="degrees (default 2)")
    s.set_defaults(fn=cmd_sun_times)

    s = sub.add_parser("convert", help="read any coordinate (decimal, DMS, UTM, MGRS, Plus Code, geohash) and print all formats")
    s.add_argument("text")
    s.add_argument("--ref", help="LAT,LON near the place, needed for a short Plus Code")
    s.set_defaults(fn=cmd_convert)
    s = sub.add_parser("distance", help="distance and bearings between two points")
    s.add_argument("p1")
    s.add_argument("p2")
    s.set_defaults(fn=cmd_distance)
    s = sub.add_parser("destination", help="point at a distance along a bearing (optionally a GeoJSON line)")
    s.add_argument("point")
    s.add_argument("--bearing", type=float, required=True)
    s.add_argument("--km", type=float, required=True)
    s.add_argument("--out", help="write the bearing line as GeoJSON")
    s.set_defaults(fn=cmd_destination)
    s = sub.add_parser("intersect", help="where two bearing lines from two known points meet")
    s.add_argument("p1")
    s.add_argument("b1", type=float)
    s.add_argument("p2")
    s.add_argument("b2", type=float)
    s.set_defaults(fn=cmd_intersect)
    s = sub.add_parser("resection", help="camera position from bearings to 2+ known landmarks")
    s.add_argument("--landmark", action="append", required=True, help="LAT,LON,BEARING (repeat)")
    s.add_argument("--relative", action="store_true", help="bearings share an unknown zero (no compass); needs 3+")
    s.set_defaults(fn=cmd_resection)
    s = sub.add_parser("tz", help="UTC offset of a named time zone, or all zones of a country (offline, IANA)")
    s.add_argument("zone", nargs="?", help="IANA name, e.g. Africa/Tunis")
    s.add_argument("--country", help="ISO 3166 code: list every zone of that country")
    s.add_argument("--time", help="ISO time (default: now); without a zone it is read as UTC")
    s.set_defaults(fn=cmd_tz)

    s = sub.add_parser("quick", parents=[common], help="first look: triage, new case, timer, links, checklist")
    s.add_argument("image")
    s.set_defaults(fn=cmd_quick)
    s = sub.add_parser("links", help="print map, street-level, imagery and reverse-image-search links (never opens them)")
    s.add_argument("point", nargs="?", help="coordinates in any format; omit for reverse image search pages only")
    s.add_argument("--zoom", type=int, default=18)
    s.add_argument("--time", help="UTC ISO time for the SunCalc link")
    s.set_defaults(fn=cmd_links)

    s = sub.add_parser("facts", help="filter countries by clues; every fact printed with its source and date")
    s.add_argument("--drives", action="append", choices=["left", "right"])
    s.add_argument("--plug", action="append", help="plug type letter A-N (repeat for several plugs seen)")
    s.add_argument("--language", action="append", help="ISO 639 code, e.g. ar, fr, sw (GeoNames list)")
    s.add_argument("--currency", action="append", help="ISO 4217 code, e.g. TND")
    s.add_argument("--tld", action="append", help="country domain, e.g. .tn")
    s.add_argument("--calling-code", dest="calling_code", action="append", help="e.g. 216 or +216")
    s.add_argument("--continent", action="append", help="GeoNames code: AF AS EU NA OC SA AN")
    s.add_argument("--frequency", action="append", choices=["50", "60"], help="mains frequency in Hz")
    s.add_argument("--script", action="append", help="writing system seen (Arab, Latn, Cyrl, Hani, or Arabic...): "
                                                     "SOFT, match or unknown only, never excludes (Unicode CLDR)")
    s.add_argument("--point", help="restrict to the country containing this point (Natural Earth outlines)")
    s.add_argument("--country", help="show every fact of one country (ISO code)")
    s.add_argument("--full", action="store_true", help="full detail for excluded countries and every note")
    s.set_defaults(fn=cmd_facts)

    s = sub.add_parser("text", help="scripts, phone numbers, domains and currencies in text typed from the photo")
    s.add_argument("text")
    s.add_argument("--region", help="read numbers written without + as numbers of this region (ISO code)")
    s.add_argument("--all-regions", action="store_true", help="list every region where a national number is valid (local only)")
    s.set_defaults(fn=cmd_text)

    s = sub.add_parser("test", parents=[common], help="check hypothesis points against a measured clue (pass/fail/unknown)")
    s.add_argument("test", choices=["sun", "bearing", "distance", "tz"])
    s.add_argument("--hyp", action="append", help="hypothesis id (repeat); default: all")
    s.add_argument("--time", help="ISO time with zone (for tz: the UTC time of the observation)")
    s.add_argument("--utc-offset", help="sun: zone for a time without one; tz: the observed offset, e.g. +01:00")
    s.add_argument("--time-error", type=float, default=0.0, help="sun: minutes")
    s.add_argument("--shadow-bearing", type=float)
    s.add_argument("--elevation", type=float)
    s.add_argument("--height", type=float)
    s.add_argument("--shadow", type=float)
    s.add_argument("--landmark", help="LAT,LON of the landmark, or an evidence id (eN) from geocode/osm in this case")
    s.add_argument("--time-source", choices=["gps", "exif-with-offset", "exif-no-offset", "manual"],
                   help="sun: where the time comes from (required)")
    s.add_argument("--tz", help="sun with exif-no-offset: IANA zone to read the local time in")
    s.add_argument("--bearing", type=float, help="measured bearing camera -> landmark")
    s.add_argument("--min-km", type=float)
    s.add_argument("--max-km", type=float)
    s.add_argument("--tolerance", type=float, help="degrees (sun default 2, bearing default 5)")
    s.add_argument("--kind", default="SOFT", choices=["SOFT", "HARD"])
    s.add_argument("--u", type=int, default=3)
    s.add_argument("--p", type=int, default=5)
    s.add_argument("--r", type=int, default=3)
    s.add_argument("--group")
    s.add_argument("--dry-run", action="store_true", help="print results without recording them")
    s.set_defaults(fn=cmd_test)

    sub.add_parser("packs", help="list installed country packs").set_defaults(fn=cmd_packs)
    s = sub.add_parser("triage", help="every fact the image file carries: EXIF/GPS/time, thumbnail, XMP, IPTC, ICC, "
                                      "chunks, trailing data")
    s.add_argument("image")
    s.add_argument("--extract", help="folder to save the embedded thumbnail and any data after the end of the image")
    s.set_defaults(fn=cmd_triage)
    s = sub.add_parser("doctor", help="Python, platform, data and packs; confirm no AI/OCR is used and no AI "
                                      "service is called")
    s.set_defaults(fn=cmd_doctor)

    for name, fn in (("rank", cmd_rank), ("report", cmd_report)):
        s = sub.add_parser(name, parents=[common], help="rank hypotheses and compute the verdict" if name == "rank"
                           else "the ranking and verdict as a short report")
        s.add_argument("--agree-m", type=float, default=ledger.AGREE_M_DEFAULT,
                       help="how close evidence must be to a hypothesis to agree with it (metres)")
        s.set_defaults(fn=fn)
    # the main help lists only the main commands; every other command still works (orith tools ...)
    sub._choices_actions = sorted([c for c in sub._choices_actions if c.dest in VISIBLE],
                                  key=lambda c: VISIBLE.index(c.dest))
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr, sys.stdin):  # stdin too: answers to the guided questions
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    import warnings
    warnings.filterwarnings("ignore", module=r"PIL(\..*)?$")  # Pillow's own warnings never clutter the output
    raw = list(argv if argv is not None else sys.argv[1:])
    # output options work anywhere on the line, for every command
    flags = {"--json", "--no-color", "--ascii", "--brief", "-v", "--verbose", "--no-online"}
    given = {x for x in raw if x in flags}
    raw = [x for x in raw if x not in flags]
    STATE.update(json="--json" in given or paths.env("JSON") == "1", brief="--brief" in given, cmd=None, style=None,
                 no_color="--no-color" in given, verbose=bool(given & {"-v", "--verbose"}),
                 online="--no-online" not in given and paths.env("NO_ONLINE") != "1")
    if not STATE["json"]:
        from . import display
        STATE["style"] = display.make_style(no_color="--no-color" in given, force_ascii="--ascii" in given)
    if raw and raw[0] == "tools":  # `orith tools NAME ...` runs NAME
        if len(raw) == 1 or raw[1] in ("-h", "--help"):
            print(tools_help())
            return 0
        raw = raw[1:]
    parser = build_parser()
    commands = next(a.choices for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    if raw and raw[0] not in commands and not raw[0].startswith("-"):
        raw = ["scan"] + raw  # `<app> IMAGE|FOLDER` is `<app> scan IMAGE|FOLDER` (a missing file is reported as such)
    argv = raw
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:  # --help, or a usage error: argparse exits before any command runs

        def opt(name):
            return raw[raw.index(name) + 1] if name in raw and raw.index(name) + 1 < len(raw) else None
        stub = argparse.Namespace(case_dir=opt("--case-dir"), case=opt("--case"),
                                  cmd="help" if ("-h" in raw or "--help" in raw) else "usage-error")
        _log_call(stub, raw, e.code)
        raise
    code = 0
    STATE["cmd"] = args.cmd
    from .inputs import InputError
    try:
        args.fn(args)
        code = getattr(args, "exit_code", 0)
    except InputError as e:
        code = _fail(str(e), 2)
    except (ledger.LedgerError, osm.SpecError, net.NetError, packs.PackError, facts.FactsError, text.TextError,
            FileNotFoundError, ValueError) as e:
        code = _fail(f"{type(e).__name__}: {e}", 2)
    except KeyboardInterrupt:
        code = _fail("interrupted", 130)
    except BrokenPipeError:
        code = 1
    except Exception as e:  # noqa: BLE001 - never a traceback for a user: one line, details with -v
        if STATE.get("verbose"):
            import traceback
            traceback.print_exc()
        code = _fail(f"unexpected {type(e).__name__}: {e} (run again with -v for details)", 1)
    _log_call(args, argv if argv is not None else sys.argv[1:], code)
    return code


def _fail(message, code):
    """One-line error: JSON {"error": ...} on stdout with --json, else 'error: ...' on stderr."""
    if STATE["json"]:
        out({"error": message})
    else:
        from . import display
        st = display.make_style(stream=sys.stderr, no_color=STATE.get("no_color", False))
        sys.stderr.write(st.c("error: ", "bold", "red") + st.t(message) + "\n")
    return code


def _log_overpass(args, status, seconds, detail):
    """One line per Overpass request in <case dir>/overpass.jsonl: success, success_cached, timeout or error."""
    try:
        base = Path(getattr(args, "case_dir", None) or paths.cases_dir())
        base.mkdir(parents=True, exist_ok=True)
        with (base / "overpass.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "case": getattr(args, "case", None),
                                "status": status, "seconds": round(seconds, 1), "detail": detail[:200]}) + "\n")
    except OSError:
        pass


def _log_call(args, argv, code):
    """Append one line per invocation to <case dir>/calls.jsonl, so tool use can be counted afterwards."""
    try:
        base = Path(getattr(args, "case_dir", None) or paths.cases_dir())
        base.mkdir(parents=True, exist_ok=True)
        with (base / "calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "case": getattr(args, "case", None),
                                "cmd": args.cmd, "argv": list(argv), "exit": code}, ensure_ascii=False) + "\n")
    except OSError:
        pass  # logging must never break a command


if __name__ == "__main__":
    sys.exit(main())
