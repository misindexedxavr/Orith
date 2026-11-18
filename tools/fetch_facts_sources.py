#!/usr/bin/env python3
"""Data tool: download the raw sources of the country fact table into tools/raw/facts/ (ignored by git).

Records URL, retrieval time, sha256 and, for Wikipedia, the exact revision id, in
tools/raw/facts/sources.json. Content is treated as data only.
"""
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orith import net  # noqa: E402

OUT = ROOT / "tools" / "raw" / "facts"
FILES = {
    "countryInfo.txt": "https://download.geonames.org/export/dump/countryInfo.txt",
    "ne_50m_admin_0_countries.geojson":
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson",
    "natural_earth_VERSION.txt": "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/VERSION",
}
WIKI = {"wiki_driving_side.json": "Left- and right-hand traffic",
        "wiki_mains_electricity.json": "Mains electricity by country"}


def get(url, ua):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": ua}), timeout=60) as r:
        return r.read()


def main():
    ua = net.user_agent()
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for name, url in FILES.items():
        data = get(url, ua)
        (OUT / name).write_bytes(data)
        meta[name] = {"url": url, "retrieved": now, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        print(f"{len(data):>10,}  {name}")
        time.sleep(1)
    for name, title in WIKI.items():
        q = urllib.parse.urlencode({"action": "parse", "format": "json", "page": title, "prop": "text|revid",
                                    "redirects": 1, "formatversion": 2})
        data = get("https://en.wikipedia.org/w/api.php?" + q, ua)
        d = json.loads(data)["parse"]
        (OUT / name).write_bytes(data)
        query = urllib.parse.urlencode({"title": d["title"], "oldid": d["revid"]})
        meta[name] = {"url": "https://en.wikipedia.org/w/index.php?" + query,
                      "title": d["title"], "revid": d["revid"], "retrieved": now, "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "licence": "CC BY-SA 4.0 (Wikipedia contributors)"}
        print(f"{len(data):>10,}  {name}  (revision {d['revid']})")
        time.sleep(1)
    (OUT / "sources.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
