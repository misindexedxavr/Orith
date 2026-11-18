"""Country packs: optional offline data, one folder per country, same code for every pack.

    packs/<code>/manifest.json   what the files are, which columns to read, sources and licences
    packs/<code>/admin/*.geojson  country outline and administrative levels
    packs/<code>/gazetteer/*.csv  named places with coordinates

Nothing here knows about any particular country: every name, level label and column comes
from the manifest. The packs folder is ROOT/packs, or the ORITH_PACKS environment variable.
"""
from __future__ import annotations

import csv
import json
import math
from functools import cache
from pathlib import Path

from . import admin, paths

ROOT = Path(__file__).resolve().parent.parent


def packs_dir():
    return paths.data_dir("packs")


class PackError(ValueError):
    pass


class Pack:
    def __init__(self, folder):
        self.folder = Path(folder)
        with (self.folder / "manifest.json").open(encoding="utf-8") as f:
            self.manifest = json.load(f)
        m = self.manifest
        for key in ("format", "code", "name", "country", "sources"):
            if key not in m:
                raise PackError(f"{self.folder.name}: manifest lacks '{key}'")
        if m["format"] != 1:
            raise PackError(f"{self.folder.name}: unsupported pack format {m['format']}")
        for s in m["sources"]:
            for key in ("url", "licence", "credit", "retrieved"):
                if not s.get(key):
                    raise PackError(f"{self.folder.name}: a source lacks '{key}'")
        self.code = m["code"].upper()
        self.name = m["name"]

    def _path(self, rel):
        p = (self.folder / rel).resolve()
        if self.folder.resolve() not in p.parents:
            raise PackError(f"{self.code}: file outside the pack: {rel}")
        return p

    def country_shapes(self):
        return admin.load_geojson(str(self._path(self.manifest["country"]["file"])))

    def contains(self, lat, lon):
        return any(s.contains(lat, lon) for s in self.country_shapes())

    def levels(self):
        return sorted(self.manifest.get("levels", []), key=lambda lv: lv["level"])

    def level_shapes(self, level):
        return admin.load_geojson(str(self._path(level["file"])))

    def locate(self, lat, lon):
        """Admin units containing the point, from the coarsest level to the finest."""
        out = []
        for lv in self.levels():
            for s in self.level_shapes(lv):
                if s.contains(lat, lon):
                    out.append({"level": lv["level"], "label": lv["label"],
                                "id": s.props.get(lv["id_prop"]), "name": s.props.get(lv["name_prop"])})
                    break
        return out

    def admin1_names(self):
        lv = next((lv for lv in self.levels() if lv["level"] == 1), None)
        if not lv:
            return {}
        return {s.props.get(lv["id_prop"]): s.props.get(lv["name_prop"]) for s in self.level_shapes(lv)}

    def gazetteer_rows(self):
        """Yield (spec, row) for every gazetteer CSV row listed in the manifest."""
        for spec in self.manifest.get("gazetteer", []):
            with self._path(spec["file"]).open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    yield spec, row

    def info(self):
        return {"code": self.code, "name": self.name, "folder": str(self.folder),
                "levels": [lv["label"] for lv in self.levels()],
                "gazetteer_files": len(self.manifest.get("gazetteer", [])),
                "sources": [{k: s.get(k) for k in ("name", "url", "licence", "credit", "retrieved")}
                            for s in self.manifest["sources"]]}


@cache
def _load_all(folder):
    base = Path(folder)
    if not base.is_dir():
        return ()
    found = []
    for d in sorted(base.iterdir()):
        if (d / "manifest.json").is_file():
            found.append(Pack(d))
    return tuple(found)


def installed():
    return _load_all(str(packs_dir()))


def get(code):
    for p in installed():
        if p.code == str(code).upper():
            return p
    raise PackError(f"no installed pack with code {code}")


def locate(lat, lon):
    """Offline reverse geocoding over every installed pack. [] when no pack covers the point."""
    out = []
    for p in installed():
        if p.contains(lat, lon):
            out.append({"pack": p.code, "country": p.name, "admin": p.locate(lat, lon)})
    return out


def find_area(name, norm):
    """Admin units in any pack whose name or id matches `name` (normalised by `norm`)."""
    want = norm(name)
    hits = []
    for p in installed():
        for lv in p.levels():
            for s in p.level_shapes(lv):
                cand = {norm(str(s.props.get(k, ""))) for k in (lv["name_prop"], lv["id_prop"])}
                cand |= {norm(str(v)) for k, v in s.props.items() if k.startswith("name") and v}
                if want in cand:
                    hits.append({"pack": p.code, "label": lv["label"], "name": s.props.get(lv["name_prop"]),
                                 "id": s.props.get(lv["id_prop"]), "bbox": s.bbox})
    return hits


def area_precision_m(area_km2):
    """Radius of a disc with this area: the honest uncertainty of an area's label point."""
    return round(1000 * math.sqrt(float(area_km2) / math.pi))
