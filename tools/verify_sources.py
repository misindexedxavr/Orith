#!/usr/bin/env python3
"""Check the sha256 values recorded in the SOURCES.json files.

- Unicode files are shipped as downloaded: checked directly in orith/data/unicode/.
- Raw sources of the fact table (GeoNames, Natural Earth, Wikipedia, CLDR) are not shipped: they are
  checked in tools/raw/ after you run the fetch scripts. A missing raw file is reported, not failed.
Exit code 1 if any present file differs.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "orith" / "data"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    rows, bad = [], 0
    uni = json.loads((DATA / "unicode" / "SOURCES.json").read_text(encoding="utf-8"))
    checks = [(DATA / "unicode" / k, v["sha256"]) for k, v in uni["files"].items()]
    facts = json.loads((DATA / "facts" / "SOURCES.json").read_text(encoding="utf-8"))
    checks += [(ROOT / "tools" / "raw" / "facts" / k, v["sha256"]) for k, v in facts.items()
               if isinstance(v, dict) and "sha256" in v]
    checks += [(ROOT / "tools" / "raw" / "cldr" / k, v["sha256"]) for k, v in facts.get("cldr", {}).items()
               if isinstance(v, dict) and "sha256" in v]
    for path, want in checks:
        if not path.exists():
            rows.append((path, "not downloaded"))
            continue
        ok = sha(path) == want
        bad += not ok
        rows.append((path, "OK" if ok else "DIFFERENT (a newer revision, or a changed file)"))
    for path, status in rows:
        print(f"{status:<16} {path.relative_to(ROOT)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
