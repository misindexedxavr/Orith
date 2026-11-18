"""Folder triage: every image in a folder at once. For each file: GPS, capture time, camera, software,
declared provenance (C2PA / IPTC), consistency checks, JPEG fingerprint and perceptual hash; then
near-duplicates (dHash Hamming distance) and a timeline. Nothing is recorded in cases unless asked.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".jfif"}
DUP_BITS = 10


def _one(p):
    from . import facts, fingerprint, inputs, scan, triage, verify
    try:
        inputs.check_file(p)
        t = triage.triage(p)
        damaged = inputs.check_triage(t, p)
    except inputs.InputError as e:  # not an image, empty, too large: reported, the folder goes on
        short = str(e).split(" (")[0].replace(f": {p}", "").replace(f": {p.name}", "").replace(f"{p.name}: ", "")
        return {"file": p.name, "error": short[:160]}
    except Exception as e:  # noqa: BLE001 - one bad file must not stop the folder
        return {"file": p.name, "error": f"{type(e).__name__}: {e}"[:160]}
    gps, tm, cam = t.get("gps") or {}, t.get("time") or {}, t.get("camera") or {}
    name_time = fingerprint.file_name_time(p.name)
    prov = scan._provenance(t)
    chk, implied = verify.checks(t, name_time, prov)
    try:
        hsh = fingerprint.hashes(p)["dhash"]
    except Exception:  # noqa: BLE001
        hsh = None
    try:
        jp = fingerprint.jpeg(p)
    except Exception:  # noqa: BLE001
        jp = None
    country = None
    if gps.get("lat") is not None:
        country = ",".join(c["iso2"] for c in facts.country_at(gps["lat"], gps["lon"]) if c["iso2"]) or "-"
    flags = []
    if prov and prov["ai_generated"]:
        flags.append("AI-declared")
    elif prov:
        flags.append("C2PA")
    flags += [c["check"] for c in chk if c["result"] in ("mismatch", "warning")]
    if damaged:
        flags.append("HEIC needs orith[heic]" if "heic" in damaged else "damaged image data")
    return {"file": p.name, "path": str(p), "type": t.get("type"), "size": (t.get("image") or {}).get("size"),
            "gps": [gps["lat"], gps["lon"]] if gps.get("lat") is not None else None, "country": country,
            "time_local": (tm.get("DateTimeOriginal") or (name_time or {}).get("local")),
            "time_utc": tm.get("utc_from_offset") or gps.get("gps_utc"),
            "time_from": "EXIF" if tm.get("DateTimeOriginal") else ("file name" if name_time else None),
            "camera": " ".join(str(cam.get(k)) for k in ("Make", "Model") if cam.get(k)) or None,
            "software": cam.get("Software"), "jpeg_quality": (jp or {}).get("quality"),
            "standard_jpeg_table": (jp or {}).get("standard_table"), "dhash": hsh, "flags": flags,
            "checks": chk, "implied_utc_offset": implied.get("utc_offset")}


def run(folder, recursive=False, workers=8, log=None):
    log = log or (lambda *x: None)
    folder = Path(folder)
    files = sorted(p for p in (folder.rglob("*") if recursive else folder.iterdir())
                   if p.is_file() and p.suffix.lower() in EXTS)
    log("+", f"Scanning {folder}", f"{len(files)} image(s)")
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(files) or 1))) as ex:
        rows = list(ex.map(_one, files))
    for r in rows:
        log("-" if r.get("error") else "+", r["file"], r.get("error") or ", ".join(
            x for x in ("GPS " + r["country"] if r.get("gps") else "", r.get("time_local") or "",
                        r.get("camera") or "", ", ".join(r["flags"])) if x) or "nothing in the file")
    # near-duplicates: union of pairs within DUP_BITS. A flat image (few or almost all bits set) has a hash that
    # matches any other flat image, so it is left out of the comparison and marked.
    ok = []
    for r in rows:
        if r.get("dhash"):
            ones = bin(int(r["dhash"], 16)).count("1")
            if 6 <= ones <= 58:
                ok.append(r)
            else:
                r["dhash_note"] = "low-detail image: hash not used for duplicates"
    parent = {r["file"]: r["file"] for r in ok}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, a in enumerate(ok):
        ha = int(a["dhash"], 16)
        for b in ok[i + 1:]:
            if bin(ha ^ int(b["dhash"], 16)).count("1") <= DUP_BITS:
                parent[find(a["file"])] = find(b["file"])
    groups = {}
    for r in ok:
        groups.setdefault(find(r["file"]), []).append(r["file"])
    dups = [sorted(g) for g in groups.values() if len(g) > 1]
    for n, g in enumerate(dups, 1):
        for r in rows:
            if r["file"] in g:
                r["duplicate_group"] = n
    timeline = sorted((r for r in rows if r.get("time_local")), key=lambda r: str(r["time_local"]).replace(":", "-"))
    counts = {"images": sum(1 for r in rows if not r.get("error")), "with_gps": sum(1 for r in rows if r.get("gps")),
              "with_time": len(timeline), "ai_declared": sum("AI-declared" in r.get("flags", []) for r in rows),
              "with_problems": sum(1 for r in rows if any(f != "C2PA" for f in r.get("flags", []))),
              "duplicate_groups": len(dups), "errors": sum(1 for r in rows if r.get("error"))}
    return {"folder": str(folder), "counts": counts, "images": rows, "duplicates": dups,
            "timeline": [{"file": r["file"], "time": r["time_local"], "from": r["time_from"]} for r in timeline],
            "note": f"near-duplicates: dHash within {DUP_BITS} of 64 bits (same picture resized, recompressed or "
                    "lightly edited); flags are consistency problems or declared provenance"}


def geojson(result):
    """GPS positions of a folder scan as a GeoJSON FeatureCollection."""
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["gps"][1], r["gps"][0]]},
              "properties": {"file": r["file"], "time": r.get("time_local"), "camera": r.get("camera"),
                             "flags": ", ".join(r.get("flags", []))}}
             for r in result["images"] if r.get("gps")]
    return {"type": "FeatureCollection", "features": feats}
