"""Place name -> coordinates. Offline gazetteers of the installed country packs first,
Nominatim as fallback.

Every result carries precision_m, an honest radius of uncertainty: an administrative area's
label point is only as precise as the area is large, so it can rank a region but can never
count as a clue "agreeing at one point". Which files and columns to read comes from each
pack's manifest; nothing here is specific to a country.
"""
from __future__ import annotations

import difflib
import math
import re
import unicodedata
import urllib.parse
from functools import cache

from . import net, packs

NOMINATIM = "https://nominatim.openstreetmap.org/search"
DEFAULT_POINT_PRECISION_M = 3000.0  # a populated-place point marks a settlement, not a spot
# script-level folding (not country-specific): Arabic diacritics and tatweel, alef variants
_AR_DROP = re.compile(r"[\u064B-\u065F\u0670\u0640]")


def norm(s):
    """Fold case, accents, Arabic diacritics and punctuation so spellings compare equal."""
    s = _AR_DROP.sub("", s or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("\u0623", "\u0627").replace("\u0625", "\u0627").replace("\u0622", "\u0627").replace("\u0629", "\u0647")
    return re.sub(r"[^\w]+", " ", s, flags=re.UNICODE).replace("_", " ").strip()


@cache
def gazetteer(packs_folder=None):
    """Entries from every installed pack: {pack, country, kind, id, name, names, lat, lon, precision_m, admin1}."""
    from . import paths
    files = [f for p in packs.installed() for f in sorted(p.folder.rglob("*")) if f.is_file()]
    return paths.cached("gazetteer", files, _build_gazetteer)


def _build_gazetteer():
    entries = []
    for p in packs.installed():
        admin1 = p.admin1_names()
        for spec, r in p.gazetteer_rows():
            try:
                lat, lon = float(r[spec["lat"]]), float(r[spec["lon"]])
            except (KeyError, TypeError, ValueError):
                continue
            names = {norm(r.get(c, "")) for c in spec["names"]} - {""}
            if not names:
                continue
            area = r.get(spec.get("area_km2", ""), "") if spec.get("area_km2") else ""
            prec = packs.area_precision_m(area) if area else float(spec.get("point_precision_m", DEFAULT_POINT_PRECISION_M))
            pop = r.get(spec.get("population", ""), "") if spec.get("population") else ""
            a1 = r.get(spec.get("admin1", ""), "") if spec.get("admin1") else ""
            display = next((r[c] for c in spec["names"] if r.get(c)), "")
            entries.append({"pack": p.code, "country": p.name, "kind": spec["kind"], "id": r.get(spec.get("id", ""), ""),
                            "name": display, "names": frozenset(names), "lat": lat, "lon": lon, "precision_m": prec,
                            "admin1": admin1.get(a1, ""), "population": int(float(pop)) if pop else 0})
    return tuple(entries)


def offline(query, limit=8, country=None):
    """Places in the installed packs matching the query. Exact name > all query words present > close spelling."""
    q = norm(query)
    if not q:
        return []
    q_tokens = set(q.split())
    hits = []
    for e in gazetteer(str(packs.packs_dir())):
        if country and e["pack"] != country.upper():
            continue
        if q in e["names"]:
            tier, score = 0, 1.0
        elif any(q_tokens <= set(n.split()) for n in e["names"]):
            tier, score = 1, 0.9
        else:
            best = max(difflib.SequenceMatcher(None, q, n).ratio() for n in e["names"])
            if best < 0.86:
                continue
            tier, score = 2, round(best, 3)
        hits.append((tier, e["precision_m"], -e["population"], e, score))
    hits.sort(key=lambda h: h[:3])
    out, seen = [], set()
    for tier, _, _, e, score in hits:
        key = (round(e["lat"], 3), round(e["lon"], 3))
        if key in seen:  # the same place often appears at several admin levels
            continue
        seen.add(key)
        out.append({"source": "offline", "pack": e["pack"], "match": ("exact", "words", "fuzzy")[tier], "match_score": score,
                    "name": e["name"], "level": e["kind"], "ref": e["id"],
                    "lat": e["lat"], "lon": e["lon"], "precision_m": e["precision_m"],
                    "admin1": e["admin1"], "country": e["country"]})
        if len(out) >= limit:
            break
    return out


def nominatim_url(query, limit=8, countrycodes=None, viewbox=None):
    """Search URL. viewbox = (south, west, north, east) restricts results to that box."""
    params = {"q": query, "format": "jsonv2", "limit": int(limit), "addressdetails": 1, "accept-language": "en,fr,ar"}
    if countrycodes:
        params["countrycodes"] = countrycodes.lower()
    if viewbox:
        s, w, n, e = viewbox
        params["viewbox"] = f"{w},{n},{e},{s}"
        params["bounded"] = 1
    return NOMINATIM + "?" + urllib.parse.urlencode(sorted(params.items()))


def parse_nominatim(payload):
    out = []
    for r in payload or []:
        lat, lon = float(r["lat"]), float(r["lon"])
        prec = 0.0
        bb = r.get("boundingbox")
        if bb and len(bb) == 4:
            s, n, w, e = map(float, bb)
            prec = 0.5 * math.hypot((n - s) * 110570.0, (e - w) * 111320.0 * math.cos(math.radians(lat)))
        addr = r.get("address") or {}
        # Nominatim gives no match-quality number; "importance" is how prominent the place is (Wikipedia/OSM based)
        out.append({"source": "nominatim", "match": "search", "importance": round(float(r.get("importance") or 0), 3),
                    "name": r.get("display_name", ""), "level": f"{r.get('category', '')}/{r.get('type', '')}",
                    "ref": f"{r.get('osm_type', '')}/{r.get('osm_id', '')}", "lat": lat, "lon": lon,
                    "precision_m": round(prec), "country": addr.get("country", ""),
                    "admin1": addr.get("state", "")})
    return out


def online(query, limit=8, countrycodes=None, viewbox=None, **fetch_kw):
    payload, cached = net.fetch_json(nominatim_url(query, limit, countrycodes, viewbox), **fetch_kw)
    return parse_nominatim(payload), cached


def search(query, limit=8, countrycodes=None, viewbox=None, use_online="fallback", **fetch_kw):
    """Offline first. use_online: 'fallback' (only when offline finds nothing), 'always', 'never'."""
    # countrycodes is the Nominatim parameter (comma-separated ISO codes); with a single code the
    # offline search is limited to that pack, with several it searches every pack
    one = countrycodes.upper() if countrycodes and "," not in countrycodes else None
    results = offline(query, limit, country=one)
    note = f"offline gazetteer: {len(results)} match(es)"
    if use_online == "always" or (use_online == "fallback" and not results):
        more, cached = online(query, limit, countrycodes, viewbox, **fetch_kw)
        results += more
        note += f"; nominatim: {len(more)} result(s){' (cached)' if cached else ''}"
    return results[: limit * 2], note


def reverse_online(lat, lon, **fetch_kw):
    """Nominatim reverse geocoding: (address dict, from_cache). Raises net.NetError (no contact, network, HTTP)."""
    params = {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "jsonv2", "zoom": 18, "addressdetails": 1,
              "accept-language": "en"}
    url = NOMINATIM.replace("/search", "/reverse") + "?" + urllib.parse.urlencode(sorted(params.items()))
    payload, cached = net.fetch_json(url, **fetch_kw)
    if not payload or "error" in payload:
        return None, cached
    addr = payload.get("address") or {}
    return {"display_name": payload.get("display_name", ""), "country": addr.get("country"),
            "country_code": (addr.get("country_code") or "").upper(),
            "state": addr.get("state") or addr.get("region"),
            "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality"),
            "road": addr.get("road"), "ref": f"{payload.get('osm_type', '')}/{payload.get('osm_id', '')}",
            "source": "Nominatim reverse geocoding (OpenStreetMap, ODbL)"}, cached


def reverse(lat, lon):
    """Offline reverse geocoding over the installed packs. [] when no pack covers the point."""
    return packs.locate(lat, lon)
