"""Country fact table: filter countries by clues and show the source of every fact used.

Data: facts/country_facts.csv (one row per fact: iso2, field, value, status, note, source, source_url,
retrieved) and facts/countries_ne50m.geojson (Natural Earth outlines for point lookup), built by
tools/build_facts.py from GeoNames countryInfo, Wikipedia and Natural Earth. Nothing is typed in by hand.

For every country and every clue the result is one of:
  match       a fact equals the clue
  unknown     the table has no fact of that kind for the country (never excluded)
  uncertain   no fact equals the clue, but a fact is flagged "exception" by its source (never excluded)
  excluded    facts exist, none equals the clue, none is an exception; the facts are shown
Countries are never dropped silently: every excluded country is listed with the fact that excluded it.

SOFT clues (script): only match or unknown. A sign can be in a tourist, minority or foreign script, so a
country whose listed scripts (Unicode CLDR, threshold in each row's note) lack it is "unknown", never excluded.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from functools import cache
from pathlib import Path

from . import admin, paths

ROOT = Path(__file__).resolve().parent.parent

CLUES = {  # option name -> (table field, how to normalise the clue)
    "drives": ("driving_side", lambda v: v.lower()),
    "plug": ("plug_type", lambda v: v.upper()),
    "language": ("language", lambda v: v.lower().split("-")[0]),
    "currency": ("currency_code", lambda v: v.upper()),
    "tld": ("tld", lambda v: "." + v.lower().lstrip(".")),
    "calling_code": ("calling_code", lambda v: v.lstrip("+").replace(" ", "")),
    "continent": ("continent", lambda v: v.upper()),
    "frequency": ("mains_frequency_hz", lambda v: v.lower().replace("hz", "").strip()),
    "script": ("script", lambda v: script_code(v)),
}
SOFT_ONLY = {"script"}  # fields that can match but never exclude
# ISO 15924 compound codes used by CLDR, and the Unicode Script values they cover
# (ISO 15924: Jpan = Han + Hiragana + Katakana, Kore = Hangul + Han, Hans/Hant = Han variants, Hanb = Han + Bopomofo)
SCRIPT_COMPOUNDS = {"Jpan": {"Hani", "Hira", "Kana"}, "Kore": {"Hang", "Hani"}, "Hans": {"Hani"},
                    "Hant": {"Hani"}, "Hanb": {"Hani", "Bopo"}}


def script_code(value):
    """ISO 15924 code from a code or a Unicode script name (Arab, arab, Arabic, Old_Italic, Jpan)."""
    v = str(value).strip()
    for c in SCRIPT_COMPOUNDS:
        if v.lower() == c.lower():
            return c
    from . import text
    u = text.ucd()
    for code in u["names"]:
        if v.lower() == code.lower():
            return code
    for name, code in u["codes"].items():
        if v.lower().replace(" ", "_") == name.lower():
            return code
    raise FactsError(f"unknown script {value!r}: use an ISO 15924 code (Arab, Latn, Cyrl, Hani...) or a "
                     "Unicode script name (Arabic, Latin, Cyrillic, Han...)")


def facts_dir():
    return paths.data_dir("facts")


class FactsError(ValueError):
    pass


@cache
def load(folder=None):
    path = Path(folder or facts_dir()) / "country_facts.csv"
    if not path.exists():
        raise FactsError(f"fact table not installed: {path}")
    by = defaultdict(list)
    names = {}
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if not r.get("source_url") or not r.get("retrieved"):
                raise FactsError(f"row without source or date: {r}")
            by[r["iso2"]].append(r)
            if r.get("country"):
                names[r["iso2"]] = r["country"]
    return dict(by), names


def _fact(r):
    out = {"value": r["value"], "status": r["status"], "source": r["source"], "source_url": r["source_url"],
           "retrieved": r["retrieved"]}
    if r.get("note"):
        out["note"] = r["note"][:300]
    return out


def _equal(field, value, want):
    if field == "calling_code":  # GeoNames writes e.g. "+1-809 and 1-829": compare each code and its prefix
        codes = re.findall(r"\d+(?:-\d+)?", value)
        return any(c == want or c.split("-")[0] == want or c.replace("-", "") == want.replace("-", "") for c in codes)
    if field == "script":
        return value == want or want in SCRIPT_COMPOUNDS.get(value, ())
    return value.lower() == want.lower()


def judge(rows, field, want):
    have = [r for r in rows if r["field"] == field]
    if not have:
        return "unknown", []
    hit = [r for r in have if _equal(field, r["value"], want)]
    if hit:
        return "match", hit
    if field in SOFT_ONLY:
        return "unknown", have  # listed scripts shown; a SOFT clue never excludes
    if any(r["status"] == "exception" for r in have):
        return "uncertain", have
    return "excluded", have


def country_at(lat, lon, folder=None):
    shapes = admin.load_geojson(str(Path(folder or facts_dir()) / "countries_ne50m.geojson"))
    hits = [s.props for s in shapes if s.contains(lat, lon)]
    return [{"iso2": h.get("iso2"), "name": h.get("name"), "type": h.get("type")} for h in hits]


def filter_countries(clues, point=None, folder=None):
    """clues: [(option, value)]. point: (lat, lon) restricts to the Natural Earth country containing it."""
    table, names = load(str(folder or facts_dir()))
    parsed = []
    for opt, val in clues:
        if opt not in CLUES:
            raise FactsError(f"unknown clue {opt}; known: {', '.join(sorted(CLUES))}")
        field, nm = CLUES[opt]
        parsed.append((opt, field, nm(str(val))))
    at = country_at(*point, folder=folder) if point else None
    keep, excluded = [], []
    for iso2, rows in sorted(table.items()):
        if at is not None and iso2 not in {h["iso2"] for h in at}:
            excluded.append({"iso2": iso2, "country": names.get(iso2, ""),
                             "excluded_by": [{"clue": f"point {point[0]},{point[1]}",
                                              "facts": [{"value": "point not inside this country's Natural Earth outline",
                                                         "source": "Natural Earth 1:50m admin 0 countries"}]}]})
            continue
        res, why = [], []
        for opt, field, want in parsed:
            verdict, used = judge(rows, field, want)
            res.append({"clue": f"{opt}={want}", "result": verdict, "facts": [_fact(r) for r in used]})
            if verdict == "excluded":
                why.append({"clue": f"{opt}={want}", "facts": [_fact(r) for r in used]})
        if why:
            excluded.append({"iso2": iso2, "country": names.get(iso2, ""), "excluded_by": why})
        else:
            score = (sum(r["result"] == "match" for r in res), -sum(r["result"] == "uncertain" for r in res))
            keep.append((score, {"iso2": iso2, "country": names.get(iso2, ""),
                                 "matches": score[0], "uncertain": -score[1],
                                 "unknown": sum(r["result"] == "unknown" for r in res), "clues": res}))
    keep.sort(key=lambda t: (-t[0][0], -t[0][1], t[1]["iso2"]))
    return {"clues": [f"{o}={w}" for o, _, w in parsed], "point_country": at,
            "consistent": [k for _, k in keep], "excluded": excluded,
            "counts": {"consistent": len(keep), "excluded": len(excluded), "total": len(table)},
            "note": ("'consistent' = no clue contradicted by a sourced, non-exception fact; 'unknown' and "
                     "'uncertain' never exclude. Check the facts and notes before trusting a short list.")}


# clues an analyst can SEE in a photo, by table field, with the question to ask. script is left out:
# it is a soft clue that never excludes, so it cannot shrink the list of consistent countries.
VISIBLE_FIELDS = {
    "driving_side": ("drives", "which side cars drive on"),
    "plug_type": ("plug", "the plug or socket type"),
    "calling_code": ("calling_code", "a phone number (its +country code)"),
    "tld": ("tld", "a web address (its .country ending)"),
    "currency_code": ("currency", "a price with its currency"),
}


def best_next_clue(iso2s, used_options=(), folder=None):
    """The visible clue that would leave the fewest of these countries, from the sourced rows only.

    For each field and each country taken as the true one (each of its values equally likely), count the
    countries that would remain: those listing that value, those with no row for the field (unknown never
    excludes) and those whose rows are flagged as exceptions (uncertain never excludes). The field with the
    smallest average is returned, or None when no field can shrink the list.
    """
    table, _ = load(str(folder or facts_dir()))
    used = {CLUES[o][0] for o in used_options if o in CLUES}
    iso2s = list(iso2s)
    n = len(iso2s)
    if n < 2:
        return None
    best = None
    for field, (option, question) in VISIBLE_FIELDS.items():
        if field in used:
            continue
        vals, keep_always = {}, set()
        for c in iso2s:
            rows = [r for r in table.get(c, []) if r["field"] == field]
            vals[c] = {r["value"].lower() for r in rows}
            if not rows or any(r["status"] == "exception" for r in rows):
                keep_always.add(c)
        known = [c for c in iso2s if vals[c]]
        if not known:
            continue
        total = 0.0
        for c in iso2s:
            if not vals[c]:
                total += n  # the true country has no row: the clue would not shrink anything
                continue
            total += sum(sum(1 for d in iso2s if v in vals[d] or d in keep_always) for v in vals[c]) / len(vals[c])
        expected = total / n
        groups = len({v for c in known for v in vals[c]})
        if expected < n - 1e-9 and (best is None or expected < best["expected_remaining"]):
            best = {"field": field, "option": option, "look_for": question, "expected_remaining": round(expected, 1),
                    "of": n, "distinct_values": groups,
                    "how": "average number of these countries left once the clue is seen, from the fact table"}
    return best


def country_facts(iso2, folder=None):
    table, names = load(str(folder or facts_dir()))
    rows = table.get(iso2.upper())
    if not rows:
        raise FactsError(f"no facts for {iso2}")
    by = defaultdict(list)
    for r in rows:
        by[r["field"]].append(_fact(r))
    return {"iso2": iso2.upper(), "country": names.get(iso2.upper(), ""), "facts": dict(by)}
