#!/usr/bin/env python3
"""Data tool: fetch the pinned Unicode Character Database files used for script detection into
unicode/ (exported), and record URL, version, retrieval time and sha256 in unicode/SOURCES.json."""
import gzip
import hashlib
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "18.0.0"
FILES = ["Scripts.txt", "ScriptExtensions.txt", "PropertyValueAliases.txt"]
OUT = ROOT / "orith" / "data" / "unicode"


def main():
    OUT.mkdir(exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta = {"unicode_version": VERSION, "licence": "Unicode License v3 (https://www.unicode.org/license.txt)",
            "credit": "Unicode Character Database, Unicode, Inc.", "files": {}}
    for f in FILES:
        url = f"https://www.unicode.org/Public/{VERSION}/ucd/{f}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "python-urllib"}), timeout=60) as r:
            raw = r.read()
            enc = r.headers.get("Content-Encoding")
        data = gzip.decompress(raw) if enc == "gzip" else raw
        head = data[:200].decode("utf-8", "replace")
        if f"-{VERSION}.txt" not in head and VERSION.rsplit(".0", 1)[0] not in head:
            raise SystemExit(f"{f}: header does not name version {VERSION}: {head[:80]!r}")
        (OUT / f).write_bytes(data)
        meta["files"][f] = {"url": url, "retrieved": now, "bytes": len(data), "bytes_on_wire": len(raw),
                            "content_encoding": enc, "sha256": hashlib.sha256(data).hexdigest(),
                            "first_line": head.splitlines()[0]}
        print(f"{len(data):>9,} bytes ({len(raw):,} on the wire, encoding {enc})  {f}  | {head.splitlines()[0]}")
        time.sleep(1)
    (OUT / "SOURCES.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
