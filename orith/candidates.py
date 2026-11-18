"""Candidate tests: check each hypothesis point against a measured clue.

Each test returns pass / fail / unknown per hypothesis, with the numbers and assumptions used.
Recording goes through the ledger's own functions only (add_clue + add_link): pass -> "supports",
fail -> "contradicts", unknown -> no link. Nothing is ever deleted. Full details are appended to
cases/<id>/tests.jsonl next to the ledger.
"""
from __future__ import annotations

import datetime as dt
import json
import zoneinfo
from pathlib import Path

from . import facts, geo, ledger, sun


def _angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def sun_test(lat, lon, t, shadow_bearing=None, elevation=None, tolerance=2.0, time_error_min=0.0, why_no_time=None):
    if t is None:
        return {"result": "unknown", "why": why_no_time or "no time given"}
    if shadow_bearing is None and elevation is None:
        return {"result": "unknown", "why": "no shadow bearing or elevation measured"}
    steps = int(time_error_min)
    best = None
    for m in range(-steps, steps + 1):
        tt = t + dt.timedelta(minutes=m)
        p = sun.position(lat, lon, tt)
        if p["elevation"] <= 0:
            continue
        d_az = _angle_diff((p["azimuth"] + 180) % 360, shadow_bearing) if shadow_bearing is not None else 0.0
        d_el = abs(p["elevation"] - elevation) if elevation is not None else 0.0
        worst = max(d_az, d_el)
        if best is None or worst < best[0]:
            best = (worst, tt, p, d_az, d_el)
    base = {"assumed_time_utc": t.astimezone(dt.UTC).isoformat(), "time_error_min": time_error_min,
            "tolerance_deg": tolerance, "measured": {"shadow_bearing": shadow_bearing, "elevation": elevation}}
    if best is None:
        return base | {"result": "fail", "why": "sun below the horizon at this point for the whole time window"}
    worst, tt, p, d_az, d_el = best
    return base | {"result": "pass" if worst <= tolerance else "fail",
                   "closest_time_utc": tt.astimezone(dt.UTC).isoformat(),
                   "computed": {"shadow_bearing": round((p["azimuth"] + 180) % 360, 2), "elevation": round(p["elevation"], 2)},
                   "difference_deg": {"shadow_bearing": round(d_az, 2) if shadow_bearing is not None else None,
                                      "elevation": round(d_el, 2) if elevation is not None else None}}


def bearing_test(lat, lon, landmark, measured_bearing, tolerance=5.0):
    b = geo.bearing(lat, lon, *landmark)
    d = _angle_diff(b, measured_bearing)
    return {"result": "pass" if d <= tolerance else "fail", "landmark": list(landmark),
            "measured_bearing": measured_bearing, "computed_bearing": round(b, 2), "difference_deg": round(d, 2),
            "tolerance_deg": tolerance, "distance_km": round(geo.distance_km(lat, lon, *landmark), 3),
            "assumption": "bearing measured from the camera to the landmark, from true north"}


def distance_test(lat, lon, landmark, min_km=0.0, max_km=None):
    if max_km is None and not min_km:
        return {"result": "unknown", "why": "no distance range given"}
    d = geo.distance_km(lat, lon, *landmark)
    ok = d >= (min_km or 0) and (max_km is None or d <= max_km)
    return {"result": "pass" if ok else "fail", "landmark": list(landmark), "distance_km": round(d, 3),
            "range_km": [min_km or 0, max_km], "model": "sphere R=6371.0088 km"}


def tz_test(lat, lon, t, utc_offset):
    if t is None:
        return {"result": "unknown", "why": "no time given (offsets change with DST)"}
    at = [c for c in facts.country_at(lat, lon) if c["iso2"]]
    if not at:
        return {"result": "unknown", "why": "point is in no Natural Earth country outline with an ISO code"}
    from .cli import _zone_table
    rows, ver = _zone_table()
    sign = -1 if utc_offset.startswith("-") else 1
    hh, mm = utc_offset.lstrip("+-").split(":")
    want = sign * (int(hh) * 60 + int(mm))
    out = {"countries": [c["iso2"] for c in at], "utc_offset": utc_offset,
           "time_utc": t.astimezone(dt.UTC).isoformat(), "source": f"IANA zone.tab (tzdata {ver}); Natural Earth outlines"}
    zones = []
    for iso2 in out["countries"]:
        for cc, _, zone, _ in rows:
            if cc == iso2:
                off = t.astimezone(zoneinfo.ZoneInfo(zone)).utcoffset()
                zones.append({"zone": zone, "offset_min": int(off.total_seconds() // 60)})
    if not zones:
        return out | {"result": "unknown", "why": "no IANA zone listed for this country"}
    match = [z["zone"] for z in zones if z["offset_min"] == want]
    return out | {"result": "pass" if match else "fail", "zones_with_offset": match,
                  "zone_offsets_min": sorted({z["offset_min"] for z in zones})}


# ------------------------------------------------------------------ run + record

def resolve_landmark(case, spec):
    """'eN' or (lat, lon). Returns (lat, lon, evidence_id_or_None). Coordinates within 1 m of a recorded
    osm/geocode evidence item are treated as that evidence."""
    if isinstance(spec, str):
        ev = next((e for e in case["evidence"] if e["id"] == spec), None)
        if ev is None:
            raise ledger.LedgerError(f"no evidence {spec} in this case")
        return ev["lat"], ev["lon"], ev["id"]
    lat, lon = spec
    for e in case["evidence"]:
        if geo.distance_km(lat, lon, e["lat"], e["lon"]) * 1000 <= 1.0:
            return e["lat"], e["lon"], e["id"]
    return lat, lon, None


def run(case_path, test, params, hyps=None, record=True, kind="SOFT", u=3, p=5, r=3, group=None):
    case_path = Path(case_path)
    case = ledger.load(case_path)
    evidence_id = None
    if test in ("bearing", "distance"):
        la_, lo_, evidence_id = resolve_landmark(case, params["landmark"])
        params = dict(params, landmark=(la_, lo_))
    targets = [h for h in case["hypotheses"] if not hyps or h["id"] in hyps]
    if not targets:
        raise ledger.LedgerError("no hypotheses to test (add some with hyp add)")
    results = []
    for h in targets:
        la, lo = h["lat"], h["lon"]
        if test == "sun":
            res = sun_test(la, lo, params.get("time"), params.get("shadow_bearing"), params.get("elevation"),
                           params.get("tolerance", 2.0), params.get("time_error", 0.0), params.get("why_no_time"))
        elif test == "bearing":
            res = bearing_test(la, lo, params["landmark"], params["bearing"], params.get("tolerance", 5.0))
        elif test == "distance":
            res = distance_test(la, lo, params["landmark"], params.get("min_km", 0.0), params.get("max_km"))
        elif test == "tz":
            res = tz_test(la, lo, params.get("time"), params["utc_offset"])
        else:
            raise ledger.LedgerError(f"unknown test {test}")
        results.append({"hypothesis": h["id"], "label": h["label"]} | res)
    out = {"test": test, "results": results, "recorded": None}
    if test == "sun":
        out["time_source"] = params.get("time_source")
    if evidence_id:
        ev = next(e for e in case["evidence"] if e["id"] == evidence_id)
        out["landmark_evidence"] = {"id": evidence_id, "source": ev["source"], "name": ev.get("name"),
                                    "ref": ev.get("ref"), "precision_m": ev["precision_m"]}
        far = [x["hypothesis"] for x in results
               if geo.distance_km(next(h for h in targets if h["id"] == x["hypothesis"])["lat"],
                                  next(h for h in targets if h["id"] == x["hypothesis"])["lon"],
                                  ev["lat"], ev["lon"]) * 1000 > ledger.AGREE_M_DEFAULT]
        if far:
            out["note"] = (f"Anchored to map evidence {evidence_id} as fixed geometry, but it lies more than "
                           f"{ledger.AGREE_M_DEFAULT:.0f} m from {', '.join(far)}: under the current rule it does not "
                           "count as a clue agreeing at those points (it still adds to the score).")
    if record:
        summary = ", ".join(f"{x['hypothesis']}={x['result']}" for x in results)
        clue = ledger.add_clue(case, f"{test} test: {_describe(test, params)} [{summary}]", kind, u, p, r,
                               group or test, geometry=bool(evidence_id), evidence=evidence_id)
        for x in results:
            if x["result"] in ("pass", "fail"):
                ledger.add_link(case, clue["id"], x["hypothesis"], "supports" if x["result"] == "pass" else "contradicts",
                                note=f"{test} test {x['result']}")
        ledger.save(case, case_path)
        with (case_path.parent / "tests.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"clue": clue["id"], "test": test, "params": _jsonable(params), "results": results},
                               ensure_ascii=False) + "\n")
        out["recorded"] = {"clue": clue["id"], "kind": kind, "u": u, "p": p, "r": r, "group": group or test,
                           "evidence": evidence_id, "geometry": bool(evidence_id),
                           "links": "pass -> supports, fail -> contradicts, unknown -> no link"}
    return out


def _describe(test, p):
    if test == "sun":
        parts = [f"{label} {p[key]} deg" for label, key in (("shadow", "shadow_bearing"), ("elevation", "elevation"))
                 if p.get(key) is not None]
        return f"{' / '.join(parts)} at {p.get('time')} (time source: {p.get('time_source')})"
    if test == "bearing":
        return f"bearing {p['bearing']} deg to {p['landmark']}"
    if test == "distance":
        return f"distance to {p['landmark']} in [{p.get('min_km', 0)}, {p.get('max_km')}] km"
    return f"UTC offset {p['utc_offset']} at {p.get('time')}"


def _jsonable(p):
    return {k: (v.isoformat() if hasattr(v, "isoformat") else list(v) if isinstance(v, tuple) else v) for k, v in p.items()}


EFFECT_ON_VERDICT = (
    "A fail is a 'contradicts' link from the test clue to the hypothesis. With a SOFT clue (or a HARD clue "
    "with reliability below 4) the hypothesis stays alive, its score drops by u*p*r/125, and it can no longer "
    "be IDENTIFIED (condition C: contradicted). With a HARD clue of reliability 4 or 5 it is marked eliminated "
    "and ranked last, but it stays in the ledger. A pass adds the same weight as support; it never counts "
    "toward the 3 map-anchored clues IDENTIFIED needs, because a test clue carries no map evidence.")
