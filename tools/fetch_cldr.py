#!/usr/bin/env python3
"""Data tool: download the pinned Unicode CLDR 49.0.0 supplemental files used for the `script` facts rows.

Into tools/raw/cldr/ (ignored by git): likelySubtags.json, territoryInfo.json, languageData.json and the repo
LICENSE, plus sources.json with URL, bytes, sha256 and retrieval time per file. Content is data only.
The LICENSE text is also copied to facts/LICENSE-CLDR.txt (the Unicode License V3 asks that the notice
travel with the data).
"""
import hashlib
import json
import shutil
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tools" / "raw" / "cldr"
VERSION = "49.0.0"
BASE = f"https://raw.githubusercontent.com/unicode-org/cldr-json/{VERSION}/"
FILES = {name: BASE + "cldr-json/cldr-core/supplemental/" + name
         for name in ("likelySubtags.json", "territoryInfo.json", "languageData.json")}
FILES["LICENSE"] = BASE + "LICENSE"
UA = "orith-dev/1.0 (CLDR data fetch)"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"_version": VERSION, "_licence": "Unicode License V3 (see LICENSE)"}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for name, url in FILES.items():
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
            data = r.read()
        (OUT / name).write_bytes(data)
        meta[name] = {"url": url, "retrieved": now, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        print(f"{len(data):>9,}  {name}  sha256 {meta[name]['sha256'][:16]}...")
        time.sleep(1)
    (OUT / "sources.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    shutil.copyfile(OUT / "LICENSE", ROOT / "orith" / "data" / "facts" / "LICENSE-CLDR.txt")


if __name__ == "__main__":
    main()
