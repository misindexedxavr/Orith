"""Clue -> Overpass query.

A clue spec is a small dict: a feature word from VOCAB (or raw tags), an optional name
pattern, an optional "near another feature within N metres", and a mandatory search area.
build_query() is a pure function, so the clue-to-query logic is unit-tested without network.

An empty result never eliminates a hypothesis: OpenStreetMap coverage is uneven, and much
much thinner in many rural areas than in large cities. Matches generate and support candidates.
"""
from __future__ import annotations

import math
import re
import urllib.parse

from . import net

OVERPASS = "https://overpass-api.de/api/interpreter"
MAX_RADIUS_M = 100_000
MAX_BBOX_SPAN_DEG = 3.0
MAX_NEAR_M = 2_000
DEFAULT_LIMIT = 400   # elements asked for in ONE request; pages are cut from the cached answer
MAX_LIMIT = 1000      # hard cap
TIMEOUT_S = 40

# feature word -> list of alternative tag filters (OR). Each filter is a list of (key, value);
# value None means "key present".
VOCAB = {
    "mosque": [[("amenity", "place_of_worship"), ("religion", "muslim")]],
    "church": [[("amenity", "place_of_worship"), ("religion", "christian")]],
    "synagogue": [[("amenity", "place_of_worship"), ("religion", "jewish")]],
    "temple": [[("amenity", "place_of_worship"), ("religion", "buddhist")],
               [("amenity", "place_of_worship"), ("religion", "hindu")]],
    "place_of_worship": [[("amenity", "place_of_worship")]],
    "minaret": [[("tower:type", "minaret")], [("man_made", "minaret")]],
    "shrine": [[("historic", "tomb")], [("historic", "wayside_shrine")], [("building", "shrine")]],
    "cemetery": [[("landuse", "cemetery")], [("amenity", "grave_yard")]],
    "castle": [[("historic", "castle")], [("historic", "fort")], [("historic", "citywalls")]],
    "ruins": [[("historic", "ruins")], [("historic", "archaeological_site")]],
    "monument": [[("historic", "monument")], [("historic", "memorial")]],
    "tower": [[("man_made", "tower")]],
    "clock_tower": [[("building", "clock_tower")], [("tower:type", "clock")], [("amenity", "clock")]],
    "water_tower": [[("man_made", "water_tower")]],
    "lighthouse": [[("man_made", "lighthouse")]],
    "wind_turbine": [[("generator:source", "wind")]],
    "power_tower": [[("power", "tower")]],
    "dam": [[("waterway", "dam")]],
    "bridge": [[("man_made", "bridge")], [("bridge", "yes")], [("bridge", "viaduct")]],
    "aqueduct": [[("historic", "aqueduct")], [("bridge", "aqueduct")]],
    "roundabout": [[("junction", "roundabout")]],
    "fountain": [[("amenity", "fountain")]],
    "city_gate": [[("historic", "city_gate")]],
    "railway_station": [[("railway", "station")], [("railway", "halt")]],
    "tram_stop": [[("railway", "tram_stop")]],
    "metro_station": [[("station", "subway")]],
    "airport": [[("aeroway", "aerodrome")]],
    "port": [[("landuse", "port")], [("harbour", "yes")], [("leisure", "marina")]],
    "stadium": [[("leisure", "stadium")]],
    "racecourse": [[("leisure", "track"), ("sport", "horse_racing")], [("leisure", "horse_riding")]],
    "school": [[("amenity", "school")]],
    "university": [[("amenity", "university")]],
    "hospital": [[("amenity", "hospital")]],
    "police": [[("amenity", "police")]],
    "town_hall": [[("amenity", "townhall")]],
    "post_office": [[("amenity", "post_office")]],
    "bank": [[("amenity", "bank")]],
    "fuel": [[("amenity", "fuel")]],
    "pharmacy": [[("amenity", "pharmacy")]],
    "market": [[("amenity", "marketplace")]],
    "mall": [[("shop", "mall")]],
    "hotel": [[("tourism", "hotel")]],
    "museum": [[("tourism", "museum")]],
    "viewpoint": [[("tourism", "viewpoint")]],
    "casino": [[("amenity", "casino")]],
    "beach": [[("natural", "beach")]],
    "peak": [[("natural", "peak")]],
    "oasis": [[("natural", "oasis")], [("landuse", "orchard"), ("trees", "date_palms")]],
    "quarry": [[("landuse", "quarry")]],
    "motorway": [[("highway", "motorway")]],
    "toll_booth": [[("barrier", "toll_booth")]],
    "river": [[("waterway", "river")]],
}
_KEY_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_:\-]*$")


class SpecError(ValueError):
    """The clue spec cannot be turned into a safe, bounded query."""


def _q(s):
    """Quote a string for Overpass QL."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


# every name-like key, searched in ONE filter with a key regex
NAME_KEYS = ("name", "name:ar", "name:fr", "name:en", "alt_name", "old_name", "official_name", "int_name")
NAME_KEY_RE = "^(" + "|".join(NAME_KEYS) + ")$"
_AR = {"ا": "[اأإآ]", "أ": "[اأإآ]", "إ": "[اأإآ]", "آ": "[اأإآ]", "ة": "[ةه]", "ه": "[ةه]", "ى": "[ىي]", "ي": "[ىي]"}


def arabic_classes(pattern):
    """Alef forms, taa marbuta/haa and alef maqsura/yaa become classes, outside [...] only."""
    out, depth = [], 0
    for ch in str(pattern):
        if ch == "[":
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
        out.append(_AR[ch] if depth == 0 and ch in _AR else ch)
    return "".join(out)


# --translit: a loose pattern from a Latin name. Vowels (and w, y), spaces, hyphens and apostrophes
# between consonants are free; doubled letters count once; letter groups that transliterations mix up
# become classes. Unanchored, case-insensitive.
_GAP = "[aeiouwyàâäéèêëîïôöûüù' -]*"
_UNITS = [("kh", "k?h"), ("sh", "[cs]h"), ("ch", "[cs]h"), ("dj", "(d?j|g)"), ("j", "(d?j|g)"),
          ("q", "[qkg]"), ("k", "[qkg]"), ("g", "(d?j|g|k|q)"), ("h", "k?h")]
_DROP = "'’ʼʿʾ`‘ء"  # apostrophes, ayn/hamza marks, Arabic hamza
_VOWELISH = set("aeiouwy")


def translit_pattern(text):
    """(regex, consonant_count, core) for a Latin name; raises SpecError if it has under 3 consonants."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(text)).casefold()
    t = "".join(ch for ch in t if not unicodedata.combining(ch) and ch not in _DROP)
    if not re.fullmatch(r"[a-z \-]+", t.strip()):
        raise SpecError("--translit takes a plain Latin name (letters, spaces, hyphens), not a regex")
    t = re.sub(r"^(ell?|al)[ \-]+", "", t.strip())  # optional leading article
    core = re.sub(r"[ \-]+", " ", t).strip()
    t = re.sub(r"(.)\1+", r"\1", re.sub(r"[ \-]+", "", t))  # doubled letters = single
    units, i = [], 0
    while i < len(t):
        if t[i] in _VOWELISH:
            i += 1
            continue
        for grp, rx in _UNITS:
            if t.startswith(grp, i):
                units.append(rx)
                i += len(grp)
                break
        else:
            units.append(re.escape(t[i]))
            i += 1
    if len(units) < 3:
        raise SpecError(f"--translit needs a name with at least 3 consonants (got {len(units)} in {text!r})")
    body = _GAP.join(f"({u})+" if len(u) > 1 else f"{u}+" for u in units)
    return "((e|a)ll?[ -]?)?" + body, len(units), core


def label_matches(elements, spec):
    """Mark each element 'exact' (the typed name, or a regex match without --translit) or 'fuzzy'
    (only the loose --translit pattern matched), and which name key matched."""
    import unicodedata

    def fold(s):
        s = unicodedata.normalize("NFKD", str(s)).casefold()
        return re.sub(r"[\W_]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()
    name = spec.get("name")
    if not name:
        return elements
    if spec.get("translit"):
        rx, _, core = translit_pattern(name)
    else:
        rx, core = arabic_classes(name), None
    try:
        crx = re.compile(rx, re.I)
    except re.error:
        crx = None
    for el in elements:
        tags = el.get("all_names") or {}
        keys = [k for k, v in tags.items() if crx and crx.search(v)]
        el["matched_key"] = keys[0] if keys else None
        if core is None:
            el["match"] = "exact"
        else:
            el["match"] = "exact" if any(core in fold(v) for v in tags.values()) else "fuzzy"
    return elements


def _filters(feature=None, tags=None, name=None, translit=False):
    """List of filter strings (alternatives), each like ["amenity"="x"][~"^(name|...)$"~"y",i]."""
    alts = [[]]
    if feature:
        key = str(feature).strip().lower().replace(" ", "_").replace("-", "_")
        if key not in VOCAB:
            raise SpecError(f"unknown feature '{feature}'. Known: {', '.join(sorted(VOCAB))}. Or use tags.")
        alts = [list(a) for a in VOCAB[key]]
    extra = []
    for k, v in (tags or {}).items():
        if not _KEY_OK.match(str(k)):
            raise SpecError(f"bad tag key: {k!r}")
        extra.append((k, None if v in (None, "", "*") else v))
    out = []
    for alt in alts:
        s = "".join(f"[{_q(k)}]" if v is None else f"[{_q(k)}={_q(v)}]" for k, v in alt + extra)
        if name:
            pattern = translit_pattern(name)[0] if translit else arabic_classes(name)
            s += f"[~{_q(NAME_KEY_RE)}~{_q(pattern)},i]"
        out.append(s)
    if out == [""]:
        raise SpecError("a query needs a feature, tags or a name")
    return out


def _area(area):
    """Overpass spatial filter from {'around': [lat, lon, radius_m]} or {'bbox': [s, w, n, e]}."""
    if not isinstance(area, dict) or len(area) != 1:
        raise SpecError("area is required: {'around': [lat, lon, radius_m]} or {'bbox': [s, w, n, e]}")
    if "around" in area:
        lat, lon, r = (float(x) for x in area["around"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise SpecError("around: coordinates out of range")
        if not 0 < r <= MAX_RADIUS_M:
            raise SpecError(f"around: radius must be 1..{MAX_RADIUS_M} m")
        return f"(around:{r:.0f},{lat:.6f},{lon:.6f})"
    if "bbox" in area:
        s, w, n, e = (float(x) for x in area["bbox"])
        if not (-90 <= s < n <= 90 and -180 <= w < e <= 180):
            raise SpecError("bbox must be south,west,north,east with south<north and west<east")
        if n - s > MAX_BBOX_SPAN_DEG or e - w > MAX_BBOX_SPAN_DEG:
            raise SpecError(f"bbox too large: at most {MAX_BBOX_SPAN_DEG} degrees per side")
        return f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    raise SpecError("area must have 'around' or 'bbox'")


def build_query(spec):
    """Overpass QL for a clue spec.

    spec = {
      "feature": "mosque",                # word from VOCAB (optional if tags or name given)
      "tags": {"building": "yes"},        # extra raw tags, ANDed (value "*" = key present)
      "name": "sidi|saint",               # case-insensitive regex on name
      "near": {"feature": "roundabout", "within_m": 150},   # optional second feature
      "area": {"around": [lat, lon, radius_m]} | {"bbox": [s, w, n, e]},
      "limit": 60,
    }
    """
    if not isinstance(spec, dict):
        raise SpecError("spec must be a dict")
    unknown = set(spec) - {"feature", "tags", "name", "translit", "near", "area", "limit"}
    if unknown:
        raise SpecError(f"unknown spec keys: {sorted(unknown)}")
    if spec.get("translit"):
        if not spec.get("name"):
            raise SpecError("--translit needs --name")
        if not isinstance(spec.get("area"), dict) or "bbox" not in spec["area"]:
            raise SpecError("--translit needs --bbox S,W,N,E (a loose pattern must stay in a small box)")
    area = _area(spec.get("area"))
    main = _filters(spec.get("feature"), spec.get("tags"), spec.get("name"), bool(spec.get("translit")))
    limit = int(spec.get("limit") or DEFAULT_LIMIT)
    if not 1 <= limit <= MAX_LIMIT:
        raise SpecError(f"limit must be 1..{MAX_LIMIT}")
    head = f"[out:json][timeout:{TIMEOUT_S}];"
    union = "(" + "".join(f"nwr{f}{area};" for f in main) + ")"
    near = spec.get("near")
    if not near:
        return f"{head}{union};out tags bb {limit};"
    within = float(near.get("within_m", 0))
    if not 0 < within <= MAX_NEAR_M:
        raise SpecError(f"near.within_m must be 1..{MAX_NEAR_M}")
    other = _filters(near.get("feature"), near.get("tags"), near.get("name"))
    # .a = main feature in the area; .b = the other feature close to any of them;
    # result = members of .a close to .b
    return (f"{head}{union}->.a;"
            "(" + "".join(f"nwr{f}(around.a:{within:.0f});" for f in other) + ")->.b;"
            f"nwr.a(around.b:{within:.0f});out tags bb {limit};")


KEEP_TAGS = ("name", "name:fr", "name:ar", "name:en", "amenity", "religion", "historic", "man_made", "tourism",
             "leisure", "building", "railway", "natural", "junction", "highway", "denomination", "ele", "wikidata")


def parse_elements(payload, sort_near=None):
    """Normalise Overpass elements (from `out tags bb`) to {ref, lat, lon, name, tags, precision_m}."""
    out = []
    for el in (payload or {}).get("elements", []):
        if "lat" in el:
            lat, lon, prec = el["lat"], el["lon"], 0.0
        elif "bounds" in el:
            # ways and relations: centre of the bounding box, precise only to half its diagonal
            b = el["bounds"]
            lat, lon = (b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2
            prec = 0.5 * math.hypot((b["maxlat"] - b["minlat"]) * 110570.0,
                                    (b["maxlon"] - b["minlon"]) * 111320.0 * math.cos(math.radians(lat)))
        else:
            continue
        tags = el.get("tags") or {}
        out.append({"ref": f"{el['type']}/{el['id']}", "lat": round(lat, 7), "lon": round(lon, 7),
                    "name": tags.get("name") or tags.get("name:fr") or tags.get("name:en") or tags.get("name:ar") or "",
                    "tags": {k: tags[k] for k in KEEP_TAGS if k in tags}, "precision_m": round(prec),
                    "all_names": {k: tags[k] for k in NAME_KEYS if k in tags}})
    if sort_near:
        la0, lo0 = sort_near
        k = math.cos(math.radians(la0))
        for o in out:
            o["distance_m"] = round(math.hypot((o["lat"] - la0) * 110570.0, (o["lon"] - lo0) * 111320.0 * k))
        out.sort(key=lambda o: o["distance_m"])
    return out


RETRY_HTTP = (429, 502, 503, 504)
RETRY_WAITS = (5, 15)  # seconds before the 2nd and 3rd attempt; timeouts are never retried


def endpoints():
    """Overpass endpoints tried in turn: "overpass_endpoints" in the user config, else the main instance.

    Only add an endpoint after checking it from your own network. Every endpoint gets the same
    User-Agent. Failover is for one query the user asked for; nothing here loops over searches.
    """
    cfg = net.config()
    eps = cfg.get("overpass_endpoints") or [OVERPASS]
    out = []
    for e in eps:
        if not isinstance(e, str) or not e.startswith("https://"):
            raise SpecError(f"overpass endpoint must be an https URL: {e!r}")
        out.append(e)
    return out


def run(spec, sort_near=None, log=None, sleep=None, endpoint_list=None, **fetch_kw):
    """Execute a clue spec. Returns (elements, query, from_cache).

    For each endpoint in turn: each attempt is one request of at most 60 s; a fast server error
    (429/502/503/504) is retried up to twice, after 5 s and 15 s; a timeout or network error is
    not retried. If an endpoint fails, the next one is tried; if all fail, the last error is raised.
    `log(status, seconds, detail)` is called once per attempt with status one of
    success, retry-success, success_cached, retryable-error, timeout, error.
    """
    import time as _time
    sleep = sleep or _time.sleep
    log = log or (lambda *a: None)
    last = None
    for ep in (endpoint_list or endpoints()):
        host = urllib.parse.urlparse(ep).netloc
        for attempt in range(len(RETRY_WAITS) + 1):
            t0 = _time.time()
            try:
                out = _run_once(spec, sort_near, endpoint=ep, **fetch_kw)
            except net.NetError as e:
                last = e
                status = getattr(e, "status", None)
                if status in RETRY_HTTP and attempt < len(RETRY_WAITS):
                    log("retryable-error", _time.time() - t0, f"{host} attempt {attempt + 1}: {e}")
                    sleep(RETRY_WAITS[attempt])
                    continue
                log("timeout" if status == "timeout" else "error", _time.time() - t0, f"{host} attempt {attempt + 1}: {e}")
                break
            cached = out[2]
            log("success_cached" if cached else ("retry-success" if attempt else "success"), _time.time() - t0,
                f"{host} attempt {attempt + 1}: {len(out[0])} elements")
            return out
    raise last if last else net.NetError("no Overpass endpoint configured")


def page(elements, number, size):
    """Slice `size` elements for page `number` (1-based) of an already fetched result. No network."""
    if size < 1 or number < 1:
        raise SpecError("--page and --show must be 1 or more")
    pages = max(1, math.ceil(len(elements) / size))
    if number > pages:
        raise SpecError(f"page {number} does not exist: {len(elements)} element(s) make {pages} page(s) of {size}")
    return elements[(number - 1) * size: number * size], pages


def _run_once(spec, sort_near=None, endpoint=OVERPASS, **fetch_kw):
    query = build_query(spec)
    # fail fast: a hung Overpass call must not eat a caller's time budget (1 try, 60 s)
    fetch_kw.setdefault("tries", 1)
    fetch_kw.setdefault("timeout", 60)
    payload, cached = net.fetch_json(endpoint, data="data=" + urllib.parse.quote(query), **fetch_kw)
    if sort_near is None and "around" in spec["area"]:
        sort_near = tuple(spec["area"]["around"][:2])
    return label_matches(parse_elements(payload, sort_near), spec), query, cached
