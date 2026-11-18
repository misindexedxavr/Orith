"""Optional offline building layer from GlobalBuildingAtlas (GBA.LoD1), queried around a point.

The data is NOT shipped: tools/fetch_gba.py downloads the tiles you choose and builds one SQLite index per
tile (stdlib sqlite3) in the buildings folder (paths.buildings_dir()). Each GBA.LoD1 record is
"<source><id><ISO3>": {"height", "var"}; for Google-sourced buildings the id is a Plus Code of the building,
which gives its position (decoded here to 10 digits, about 14 m). Records without a position in the id
(Microsoft, OSM, GBA's own) are counted in the index metadata and never guessed.

What the numbers mean, from the dataset's paper (Zhu et al., ESSD 17, 6647-6668, 2025): heights are in
metres with a global RMSE of about 5.5 m, from a machine-learning model; buildings come from satellite
detection. Treat every count and height as an approximate, ML-derived hint. Licence of GBA.LoD1: CC BY-NC 4.0
(non-commercial); it stays on your machine.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path

from . import paths

ALPHA = "23456789CFGHJMPQRVWX"
VAL = {c: i for i, c in enumerate(ALPHA)}
RES = (20.0, 1.0, 0.05, 0.0025, 0.000125)
CELL = 100  # index cells of 0.01 degree
REC = re.compile(r'"([^"]+)": \{"height": ([-\d.eE]+), "var": ([-\d.eE]+)\}')
PLUS = re.compile(r"^([a-z]+)([23456789CFGHJMPQRVWX]{8})\+([23456789CFGHJMPQRVWX]{2})[23456789CFGHJMPQRVWX]*([A-Z]{3})$")
LICENCE = "CC BY-NC 4.0 (GlobalBuildingAtlas GBA.LoD1; non-commercial use only)"
CITATION = "Zhu, X. X. et al.: GlobalBuildingAtlas, Earth Syst. Sci. Data 17, 6647-6668, 2025, doi:10.5194/essd-17-6647-2025"


def decode10(code8, code2):
    """Centre of a 10-digit Plus Code (8 before '+', 2 after): (lat, lon)."""
    digits = code8 + code2
    lat, lon = -90.0, -180.0
    for i in range(5):
        lat += VAL[digits[2 * i]] * RES[i]
        lon += VAL[digits[2 * i + 1]] * RES[i]
    return lat + RES[4] / 2, lon + RES[4] / 2


def _cell(lat, lon):
    return int(math.floor((lat + 90) * CELL)) * 100000 + int(math.floor((lon + 180) * CELL))


def tile_bbox(name):
    """(s, w, n, e) from a GBA tile name like e005_n35_e010_n30."""
    m = re.fullmatch(r"([ew])(\d{3})_([ns])(\d{2})_([ew])(\d{3})_([ns])(\d{2})", name)
    if not m:
        raise ValueError(f"not a GBA tile name: {name}")
    sg = {"e": 1, "w": -1, "n": 1, "s": -1}
    w, n = sg[m[1]] * int(m[2]), sg[m[3]] * int(m[4])
    e, s = sg[m[5]] * int(m[6]), sg[m[7]] * int(m[8])
    return s, w, n, e


def build_index(json_path, out_dir=None, source=None, log=print):
    """Stream a GBA.LoD1 tile (.json) into <out_dir>/<tile>.sqlite. Returns the metadata dict."""
    json_path = Path(json_path)
    out_dir = Path(out_dir or paths.buildings_dir())
    out_dir.mkdir(parents=True, exist_ok=True)
    tile = json_path.stem
    s, w, n, e = tile_bbox(tile)
    db_path = out_dir / f"{tile}.sqlite"
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;"
                      "CREATE TABLE b (cell INTEGER, lat INTEGER, lon INTEGER, dm INTEGER, cc TEXT);"
                      "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);")
    counts = {"records": 0, "located": 0, "no_position_in_id": 0, "outside_tile": 0, "with_height": 0}
    rows, tail = [], ""
    with json_path.open(encoding="utf-8") as f:
        while True:
            chunk = f.read(8 << 20)
            buf = tail + chunk
            last = buf.rfind("}, ") if chunk else len(buf)
            body, tail = (buf[:last + 1], buf[last + 1:]) if chunk else (buf, "")
            for key, h, _var in REC.findall(body):
                counts["records"] += 1
                m = PLUS.match(key)
                if not m:
                    counts["no_position_in_id"] += 1
                    continue
                lat, lon = decode10(m[2], m[3])
                if not (s - 0.01 <= lat <= n + 0.01 and w - 0.01 <= lon <= e + 0.01):
                    counts["outside_tile"] += 1
                    continue
                hv = float(h)
                counts["located"] += 1
                counts["with_height"] += hv > 0
                rows.append((_cell(lat, lon), round(lat * 1e5), round(lon * 1e5),
                             round(hv * 10) if hv > 0 else None, m[4]))
                if len(rows) >= 200000:
                    con.executemany("INSERT INTO b VALUES (?,?,?,?,?)", rows)
                    rows.clear()
            if not chunk:
                break
            if counts["records"] and counts["records"] % 500000 < 100000:
                log(f"[+] indexing {tile}: {counts['records']:,} records read")
    if rows:
        con.executemany("INSERT INTO b VALUES (?,?,?,?,?)", rows)
    con.execute("CREATE INDEX b_cell ON b(cell)")
    meta = {"tile": tile, "bbox": [s, w, n, e], "licence": LICENCE, "citation": CITATION, "counts": counts,
            "source": source or {}, "note": "positions from Plus Code ids (10 digits, about 14 m); heights in metres "
                                           "from an ML model, global RMSE about 5.5 m (paper)"}
    con.execute("INSERT INTO meta VALUES ('meta', ?)", (json.dumps(meta),))
    con.commit()
    con.close()
    tmp.replace(db_path)
    return meta


def tiles(folder=None):
    """[(meta, db_path)] for every built index."""
    out = []
    for p in sorted(Path(folder or paths.buildings_dir()).glob("*.sqlite")):
        try:
            with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as con:
                out.append((json.loads(con.execute("SELECT value FROM meta WHERE key='meta'").fetchone()[0]), p))
        except (sqlite3.Error, TypeError, ValueError):
            continue
    return out


def near(lat, lon, radius_m=500.0, folder=None):
    """Buildings within radius_m of a point: count, density, nearest distance, height summary.
    {'covered': False} when no installed tile covers the point."""
    hits = [(m, p) for m, p in tiles(folder) if m["bbox"][0] <= lat <= m["bbox"][2] and m["bbox"][1] <= lon <= m["bbox"][3]]
    if not hits:
        return {"covered": False, "radius_m": radius_m}
    dlat = radius_m / 110570.0
    dlon = radius_m / (111320.0 * max(0.01, math.cos(math.radians(lat))))
    c0, c1 = _cell(lat - dlat, lon - dlon), _cell(lat + dlat, lon + dlon)
    r0, r1 = c0 // 100000, c1 // 100000
    k0, k1 = c0 % 100000, c1 % 100000
    cells = [r * 100000 + k for r in range(r0, r1 + 1) for k in range(k0, k1 + 1)]
    kx = 111320.0 * math.cos(math.radians(lat))
    dists, heights = [], []
    nearest = None
    for _meta, p in hits:
        with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as con:
            q = f"SELECT lat * 1e-5, lon * 1e-5, dm * 0.1 FROM b WHERE cell IN ({','.join('?' * len(cells))})"
            for la, lo, h in con.execute(q, cells):
                d = math.hypot((la - lat) * 110570.0, (lo - lon) * kx)
                if d <= radius_m:
                    dists.append(d)
                    if h:
                        heights.append(h)
            if not dists:  # widen once to report the nearest building
                wide = [r * 100000 + k for r in range(r0 - 3, r1 + 4) for k in range(k0 - 3, k1 + 4)]
                q_wide = f"SELECT lat * 1e-5, lon * 1e-5, dm FROM b WHERE cell IN ({','.join('?' * len(wide))})"
                for la, lo, _h in con.execute(q_wide, wide):
                    d = math.hypot((la - lat) * 110570.0, (lo - lon) * kx)
                    nearest = d if nearest is None else min(nearest, d)
    area_km2 = math.pi * (radius_m / 1000) ** 2
    heights.sort()
    out = {"covered": True, "radius_m": radius_m, "count": len(dists), "per_km2": round(len(dists) / area_km2),
           "nearest_m": round(min(dists)) if dists else (round(nearest) if nearest is not None else None),
           "tiles": [m["tile"] for m, _ in hits]}
    if heights:
        out["height_m"] = {"median": round(heights[len(heights) // 2], 1), "max": round(heights[-1], 1),
                           "with_height": len(heights)}
    return out


# a rule of thumb of this tool (not from a source): buildings counted within 500 m of the point
SETTINGS = {"city": (1500, None), "town": (300, 1499), "village": (10, 299), "open": (0, 9)}
SETTING_RULE = ("orith's rule of thumb on buildings within 500 m in GlobalBuildingAtlas: city 1500+, town 300-1499, "
                "village 10-299, open land 0-9 (calibrated on three sample points of the first tile tried: two town "
                "centres with 3602 and 2739, a salt flat with 0; the layer misses buildings without a position in "
                "their id)")


SETTING_RULE_SHORT = "city 1500+, town 300-1499, village 10-299, open 0-9 buildings within 500 m (orith rule of thumb)"


def setting_matches(setting, count):
    lo, hi = SETTINGS[setting]
    return count >= lo and (hi is None or count <= hi)
