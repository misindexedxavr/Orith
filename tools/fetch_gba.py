#!/usr/bin/env python3
"""Data tool: download GlobalBuildingAtlas tiles that cover a bounding box (HuggingFace only).

  python tools/fetch_gba.py --bbox S,W,N,E [--part lod1|odbl] [--max-mb 500] [--dry-run]

Tiles are 5 x 5 degrees named {e|w}LONMIN_{n|s}LATMAX_{e|w}LONMAX_{n|s}LATMIN. Sizes come from the
HuggingFace tree API before anything is downloaded; the run stops when the total is over --max-mb.
Files go to tools/raw/gba/<part>/ with sources.json (URL, bytes, sha256, retrieval time, licence).

Licences (stated by the dataset): lod1 = GBA.LoD1 (CC BY-NC 4.0, non-commercial); odbl = GBA.ODbLPolygon
(ODbL). The data is produced by machine learning and can contain errors. Nothing here is shipped in the
package: the user downloads it for their own use. Citation: Zhu et al., ESSD 17, 6647-6668, 2025,
doi:10.5194/essd-17-6647-2025.
"""
import argparse
import hashlib
import json
import math
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "orith-dev/1.0 (GBA tile fetch)"}
PARTS = {
    "lod1": {"repo": "GBA.LoD1", "prefix": "LoD1/", "ext": ".json", "licence": "CC BY-NC 4.0 (non-commercial)"},
    "odbl": {"repo": "GBA.ODbLPolygon", "prefix": "", "ext": ".geojson", "licence": "ODbL 1.0"},
}
REGIONS = ["africa", "asiaeast", "asiawest", "europe", "northamerica", "oceania", "southamerica"]


def tile_names(s, w, n, e):
    out = []
    for lon in range(int(math.floor(w / 5)) * 5, int(math.ceil(e / 5)) * 5, 5):
        for lat in range(int(math.floor(s / 5)) * 5, int(math.ceil(n / 5)) * 5, 5):
            def ew(x):
                return f"{'e' if x >= 0 else 'w'}{abs(x):03d}"

            def ns(y):
                return f"{'n' if y >= 0 else 's'}{abs(y):02d}"
            out.append(f"{ew(lon)}_{ns(lat + 5)}_{ew(lon + 5)}_{ns(lat)}")
    return out


def listing(repo, path):
    u = f"https://huggingface.co/api/datasets/zhu-xlab/{repo}/tree/main/{path}"
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60).read())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", required=True, help="S,W,N,E in degrees")
    ap.add_argument("--part", choices=PARTS, default="lod1")
    ap.add_argument("--max-mb", type=float, default=500)
    ap.add_argument("--only", help="download only this tile name")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-raw", action="store_true", help="keep the downloaded .json after building the index")
    ap.add_argument("--no-index", action="store_true", help="only download (lod1 part)")
    a = ap.parse_args()
    s, w, n, e = (float(x) for x in a.bbox.split(","))
    part = PARTS[a.part]
    want = set(tile_names(s, w, n, e))
    if a.only:
        want &= {a.only}
    found = []
    for region in REGIONS:
        for item in listing(part["repo"], part["prefix"] + region):
            name = Path(item["path"]).name.removesuffix(part["ext"])
            if name in want:
                found.append(item)
    total = sum(x["size"] for x in found) / 1e6
    for x in found:
        print(f"[+] {x['path']}  {x['size'] / 1e6:,.1f} MB")
    print(f"[+] {len(found)} tile(s), {total:,.1f} MB, licence {part['licence']}")
    if a.dry_run:
        return
    if total > a.max_mb:
        raise SystemExit(f"[-] {total:,.0f} MB is over --max-mb {a.max_mb:g}: ask first, or raise --max-mb")
    out = ROOT / "tools" / "raw" / "gba" / a.part
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "sources.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    for x in found:
        url = f"https://huggingface.co/datasets/zhu-xlab/{part['repo']}/resolve/main/{x['path']}"
        dst = out / Path(x["path"]).name
        h = hashlib.sha256()
        t0 = time.time()
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r, dst.open("wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
                h.update(chunk)
        meta[dst.name] = {"url": url, "bytes": dst.stat().st_size, "sha256": h.hexdigest(),
                          "retrieved": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "licence": part["licence"],
                          "dataset": f"zhu-xlab/{part['repo']}",
                          "citation": "Zhu et al., ESSD 17, 6647-6668, 2025, doi:10.5194/essd-17-6647-2025"}
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        print(f"[+] saved {dst.name}  {dst.stat().st_size / 1e6:,.1f} MB in {time.time() - t0:.0f} s")
        if a.part == "lod1" and not a.no_index:
            import sys
            sys.path.insert(0, str(ROOT))
            from orith import buildings, paths
            t1 = time.time()
            m = buildings.build_index(dst, source=meta[dst.name])
            print(f"[+] indexed {m['counts']['located']:,} buildings into {paths.buildings_dir()} "
                  f"in {time.time() - t1:.0f} s ({m['counts']['no_position_in_id']:,} without a position in their id)")
            if not a.keep_raw:
                dst.unlink()
                print(f"[*] removed the raw {dst.name} (sha256 kept in the index; --keep-raw to keep it)")


if __name__ == "__main__":
    main()
