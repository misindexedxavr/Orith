"""Evidence ledger and verdict rule.

The ledger is a JSON file per case. The analyst adds observations (clues, hypotheses, links);
this module alone decides the ranking and the verdict. There is deliberately no field,
flag or command that sets the verdict: "identified" can only come out of evaluate().

Rule for IDENTIFIED (all must hold for the top-ranked hypothesis):
  A. 3 or more INDEPENDENT clues agree at its point. A clue agrees at a point only if it
     is anchored to recorded map evidence (a geocode or OSM result stored in the case) that
     lies within agree_m of the hypothesis and is itself at least that precise. Clues in
     the same group, or anchored to the same evidence item, count once.
  B. At least one of those clues is fixed geometry.
  C. Nothing contradicts the hypothesis (no contradicting link of any kind).
  D. At least 3 hypotheses were recorded in the case (competing explanations were kept).
  E. No rival: no other surviving hypothesis elsewhere also satisfies A-C.
Otherwise the result is CANDIDATE, with the unmet conditions listed.

HARD clues eliminate the hypotheses they contradict; SOFT clues only move the score.
A HARD clue with reliability below HARD_MIN_RELIABILITY is treated as SOFT (an unreliable
clue must not eliminate). Clue weight = uniqueness x permanence x reliability, each 1-5,
scaled to 0-1.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

VERSION = 1
AGREE_M_DEFAULT = 250.0
HARD_MIN_RELIABILITY = 4
MIN_INDEPENDENT = 3
MIN_HYPOTHESES = 3
KINDS = ("HARD", "SOFT")
RELATIONS = ("supports", "contradicts")


class LedgerError(ValueError):
    """Invalid ledger operation (bad id, bad value, rule violated on input)."""


def hav_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))


def new_case(image, sha256=""):
    return {"version": VERSION, "image": str(image), "image_sha256": sha256,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "evidence": [], "clues": [], "hypotheses": [], "links": []}


def load(path):
    with Path(path).open(encoding="utf-8") as f:
        case = json.load(f)
    if case.get("version") != VERSION:
        raise LedgerError(f"unsupported case version: {case.get('version')}")
    return case


def save(case, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(case, f, indent=1, ensure_ascii=False)
    tmp.replace(path)


def _next_id(items, prefix):
    return f"{prefix}{1 + max([int(x['id'][len(prefix):]) for x in items] or [0])}"


def _get(items, item_id, what):
    for x in items:
        if x["id"] == item_id:
            return x
    raise LedgerError(f"no {what} with id '{item_id}'")


def _check_latlon(lat, lon):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise LedgerError(f"coordinates out of range: {lat}, {lon}")


def add_evidence(case, source, lat, lon, name="", precision_m=0.0, ref="", query="", extra=None):
    """Record one map result (from geocode or osm). Only the CLI's own lookups call this."""
    _check_latlon(lat, lon)
    ev = {"id": _next_id(case["evidence"], "e"), "source": source, "lat": float(lat), "lon": float(lon),
          "name": name, "precision_m": float(precision_m), "ref": ref, "query": query}
    if extra:
        ev["extra"] = extra
    case["evidence"].append(ev)
    return ev


def add_clue(case, text, kind, uniqueness, permanence, reliability, group, geometry=False, evidence=None):
    kind = str(kind).upper()
    if kind not in KINDS:
        raise LedgerError(f"kind must be one of {KINDS}")
    for name, v in (("uniqueness", uniqueness), ("permanence", permanence), ("reliability", reliability)):
        if not isinstance(v, int) or not 1 <= v <= 5:
            raise LedgerError(f"{name} must be an integer 1-5")
    if not str(text).strip() or not str(group).strip():
        raise LedgerError("clue text and group are required")
    if evidence is not None:
        _get(case["evidence"], evidence, "evidence")
    if geometry and evidence is None:
        raise LedgerError("a geometry clue must cite map evidence (--evidence eN): "
                          "fixed geometry means a layout matched against recorded map data")
    clue = {"id": _next_id(case["clues"], "c"), "text": text.strip(), "kind": kind,
            "uniqueness": uniqueness, "permanence": permanence, "reliability": reliability,
            "group": group.strip().lower(), "geometry": bool(geometry), "evidence": evidence}
    case["clues"].append(clue)
    return clue


def add_hypothesis(case, label, lat=None, lon=None, evidence=None):
    """A hypothesis is a point. Its coordinates come from recorded evidence or are typed in ('memory')."""
    if evidence is not None:
        ev = _get(case["evidence"], evidence, "evidence")
        lat, lon, source = ev["lat"], ev["lon"], "evidence"
    elif lat is None or lon is None:
        raise LedgerError("give --lat and --lon, or --evidence eN")
    else:
        source = "memory"
    _check_latlon(lat, lon)
    hyp = {"id": _next_id(case["hypotheses"], "h"), "label": str(label).strip(), "lat": float(lat),
           "lon": float(lon), "source": source, "evidence": evidence}
    case["hypotheses"].append(hyp)
    return hyp


def add_link(case, clue, hypothesis, relation, note=""):
    if relation not in RELATIONS:
        raise LedgerError(f"relation must be one of {RELATIONS}")
    _get(case["clues"], clue, "clue")
    _get(case["hypotheses"], hypothesis, "hypothesis")
    case["links"] = [l for l in case["links"] if not (l["clue"] == clue and l["hypothesis"] == hypothesis)]
    link = {"clue": clue, "hypothesis": hypothesis, "relation": relation, "note": note}
    case["links"].append(link)
    return link


def weight(clue):
    return clue["uniqueness"] * clue["permanence"] * clue["reliability"] / 125.0


def eliminates(clue):
    return clue["kind"] == "HARD" and clue["reliability"] >= HARD_MIN_RELIABILITY


def _independent_count(clues):
    """Number of independent clues: same group or same evidence item collapses into one."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for c in clues:
        a = find(("g", c["group"]))
        if c.get("evidence"):
            parent[find(("e", c["evidence"]))] = a
    return len({find(("g", c["group"])) for c in clues})


def evaluate(case, agree_m=AGREE_M_DEFAULT):
    """Rank hypotheses and compute the verdict. Pure function of the case."""
    clues = {c["id"]: c for c in case["clues"]}
    evidence = {e["id"]: e for e in case["evidence"]}
    warnings, rows = [], []
    for c in case["clues"]:
        if c["kind"] == "HARD" and not eliminates(c):
            warnings.append(f"{c['id']} is HARD but reliability {c['reliability']} < {HARD_MIN_RELIABILITY}: treated as SOFT")

    for h in case["hypotheses"]:
        sup = [clues[l["clue"]] for l in case["links"] if l["hypothesis"] == h["id"] and l["relation"] == "supports"]
        con = [clues[l["clue"]] for l in case["links"] if l["hypothesis"] == h["id"] and l["relation"] == "contradicts"]
        killers = [c["id"] for c in con if eliminates(c)]
        agreeing = []
        for c in sup:
            ev = evidence.get(c.get("evidence"))
            if not ev:
                continue
            d = hav_m((h["lat"], h["lon"]), (ev["lat"], ev["lon"]))
            if d <= agree_m and ev["precision_m"] <= agree_m:
                agreeing.append(c)
            elif d > agree_m:
                warnings.append(f"{c['id']} supports {h['id']} but its evidence {ev['id']} is {d:.0f} m away: not counted as agreeing")  # noqa: E501
            else:
                warnings.append(f"{c['id']}: evidence {ev['id']} is only precise to {ev['precision_m']:.0f} m: not counted as agreeing")  # noqa: E501
        rows.append({
            "id": h["id"], "label": h["label"], "lat": h["lat"], "lon": h["lon"], "source": h["source"],
            "status": "eliminated" if killers else "alive", "eliminated_by": killers,
            "score": round(sum(weight(c) for c in sup) - sum(weight(c) for c in con if not eliminates(c)), 4),
            "supports": [c["id"] for c in sup], "contradicts": [c["id"] for c in con],
            "agreeing_clues": [c["id"] for c in agreeing],
            "independent_agreeing": _independent_count(agreeing),
            "geometry_agreeing": [c["id"] for c in agreeing if c["geometry"]],
        })

    rows.sort(key=lambda r: (r["status"] != "alive", -r["score"], r["id"]))
    alive = [r for r in rows if r["status"] == "alive"]

    def meets_abc(r):
        return (r["independent_agreeing"] >= MIN_INDEPENDENT and bool(r["geometry_agreeing"])
                and not r["contradicts"])

    unmet = []
    top = alive[0] if alive else None
    if top is None:
        unmet.append("no surviving hypothesis" if rows else "no hypothesis recorded")
    else:
        if top["independent_agreeing"] < MIN_INDEPENDENT:
            unmet.append(f"A: {top['independent_agreeing']} independent clue(s) agree within {agree_m:.0f} m of "
                         f"{top['id']}; {MIN_INDEPENDENT} needed (each anchored to recorded map evidence)")
        if not top["geometry_agreeing"]:
            unmet.append("B: none of the agreeing clues is fixed geometry")
        if top["contradicts"]:
            unmet.append(f"C: contradicted by {', '.join(top['contradicts'])}")
        if len(rows) < MIN_HYPOTHESES:
            unmet.append(f"D: {len(rows)} hypothesis(es) recorded; at least {MIN_HYPOTHESES} competing ones are required")
        rivals = [r["id"] for r in alive[1:]
                  if meets_abc(r) and hav_m((r["lat"], r["lon"]), (top["lat"], top["lon"])) > agree_m]
        if rivals:
            unmet.append(f"E: rival hypothesis also satisfies the rule elsewhere: {', '.join(rivals)}")
    if 0 < len(alive) < MIN_HYPOTHESES:
        warnings.append(f"only {len(alive)} hypothesis(es) still alive; the method asks for at least {MIN_HYPOTHESES} "
                        "until hard evidence removes them")

    return {
        "verdict": "IDENTIFIED" if top is not None and not unmet else "CANDIDATE",
        "top": top["id"] if top else None,
        "unmet": unmet,
        "agree_m": agree_m,
        "ranking": rows,
        "warnings": warnings,
        "counts": {"clues": len(case["clues"]), "hypotheses": len(rows), "alive": len(alive),
                   "evidence": len(case["evidence"])},
    }
