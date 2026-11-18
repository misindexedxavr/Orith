"""scan: one command that runs every automatic step on an image and records the results in a case.

Steps: file triage (metadata, GPS, camera, capture time, hidden data); text clues from text the analyst
typed (--text) and from location text found in the metadata; country narrowing from sourced facts;
candidate places from the GPS position, the phone-number area and metadata place names (offline
gazetteer only: scan never goes online); the sun, when a time and a measurement allow it; then the
ledger's own ranking and verdict, next steps and links.

What gets recorded (all through the ledger's functions, never twice): one hypothesis per candidate
place, one SOFT clue per observation (GPS position, phone number, script, domain, currency, sun test),
and links where a sourced fact decides them (a fact that matches supports; a contradicting fact of a
field that can exclude contradicts; unknown gives no link). Every automatic clue is SOFT: making a clue
HARD is the analyst's decision. The verdict comes from the ledger only.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from . import candidates, display, facts, geocode, ledger, links, sun, text, triage

PLACE_FIELDS = ("city", "state", "province", "country", "location", "sublocation")


def _time(t, a):
    tm, gps = t.get("time") or {}, t.get("gps") or {}
    out = {"local": None, "utc": None, "source": None, "note": None}
    dto = tm.get("DateTimeOriginal")
    if dto:
        out["local"] = dto.replace(":", "-", 2).replace(" ", "T") + (tm.get("OffsetTimeOriginal") or "")
    try:
        if a.time:
            u, out["source"] = sun.parse_time(a.time, a.utc_offset), "given with --time"
        elif gps.get("gps_utc"):
            u, out["source"] = sun.parse_time(gps["gps_utc"]), "GPS time in the file"
        elif tm.get("utc_from_offset"):
            u, out["source"] = sun.parse_time(tm["utc_from_offset"]), "EXIF capture time with its UTC offset"
        elif dto and a.utc_offset:
            u = sun.parse_time(out["local"], a.utc_offset)
            out["source"] = f"EXIF capture time read in UTC{a.utc_offset} (given with --utc-offset)"
        else:
            u = None
            out["note"] = (tm.get("note") or "no capture time in the file") + \
                ("" if not dto else "; give the zone with --utc-offset +HH:MM to use the sun")
    except ValueError as e:
        u, out["note"] = None, f"time not usable: {e}"
    if u:
        out["utc"] = u.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    if tm.get("other_offsets"):
        out["other_offsets"] = tm["other_offsets"]
        out["offset_conflict"] = tm.get("offset_conflict")
    return out, u


def _metadata_texts(t):
    found, places = [], []
    for item in t.get("xmp", []):
        name = item["name"].lower()
        if any(k in name for k in PLACE_FIELDS):
            places.append((item["name"], item["value"]))
        if any(k in name for k in PLACE_FIELDS + ("description", "title", "headline", "caption", "subject", "keywords")):
            found.append((item["name"], item["value"]))
    for k, v in t.get("iptc", []):
        found.append((f"IPTC {k}", v))
        if any(p in k.lower() for p in PLACE_FIELDS):
            places.append((f"IPTC {k}", v))
    for c in t.get("text_chunks", []):
        found.append((f"PNG {c['key']}", c["value"]))
    for c in t.get("comments", []):
        found.append(("JPEG comment", c))
    return found, places


def _clues_from_text(ta):
    """[(facts option, value, clue text, group, u, p, r)] from text.analyse output."""
    out = []
    for n in ta["phones"]["numbers"]:
        area = f", area: {n['area_description']}" if n.get("area_description") else ""
        out.append(("calling_code", str(n["country_calling_code"]),
                    f"phone number {n['e164']} ({n['region_of_number']}{area})", "phone", 4, 4, 3))
    for c in ta["scripts"]["counts"]:
        out.append(("script", c["code"], f"script seen: {c['script']} ({c['code']}, {c['chars']} characters)",
                    "script", 2, 4, 4))
    for d in ta["domains"]:
        if d.get("result") == "match":
            out.append(("tld", d["tld_ascii"], f"web domain ending {d['tld_ascii']}", "domain", 3, 3, 3))
    for c in ta["currencies"]:
        out.append(("currency", c["code"], f"currency code {c['code']}", "currency", 3, 3, 3))
    return out


def _fact_relation(lat, lon, option, value):
    """'supports' / 'contradicts' / None for a hypothesis point, decided only by sourced fact rows."""
    field, norm = facts.CLUES[option]
    want = norm(value)
    table, _ = facts.load(str(facts.facts_dir()))
    results = [facts.judge(table.get(c["iso2"], []), field, want)[0]
               for c in facts.country_at(lat, lon) if c["iso2"]]
    if "match" in results:
        return "supports"
    if results and all(r == "excluded" for r in results):
        return "contradicts"
    return None


def _regions(t, ta, places, a):
    gps = t.get("gps") or {}
    out = []
    if gps.get("lat") is not None:
        out.append({"label": "EXIF GPS position (can be edited or planted)", "lat": gps["lat"], "lon": gps["lon"],
                    "source": "GPS in the file", "precision_m": None, "evidence": None})
    if a.lat is not None and a.lon is not None:
        out.append({"label": "point given with --lat/--lon", "lat": a.lat, "lon": a.lon, "source": "analyst",
                    "precision_m": None, "evidence": None})
    for n in (ta or {}).get("phones", {}).get("numbers", []):
        for name in re.split(r"\s*(?:/|,|;| and )\s*", n.get("area_description") or ""):
            hit = geocode.offline(name, limit=1, country=n.get("region_of_number")) if name.strip() else []
            if hit:
                h = hit[0]
                out.append({"label": f"phone area: {h['name']}", "lat": h["lat"], "lon": h["lon"],
                            "source": f"offline gazetteer ({h['pack']} pack), from the phone area",
                            "precision_m": h["precision_m"], "evidence": h})
    for field, value in places:
        hit = geocode.offline(str(value), limit=1)
        if hit:
            h = hit[0]
            out.append({"label": f"metadata place: {h['name']}", "lat": h["lat"], "lon": h["lon"],
                        "source": f"offline gazetteer, from {field}", "precision_m": h["precision_m"], "evidence": h})
    for name in getattr(a, "place", None) or []:
        h = _find_place(name, _online(a))
        if h:
            out.append({"label": f"place you read: {h['name'].split(',')[0]}", "lat": h["lat"], "lon": h["lon"],
                        "source": f"{h['source']} gazetteer, from '{name}'", "precision_m": h["precision_m"],
                        "evidence": h})
    seen, uniq = set(), []
    for r in out:
        if r["label"] not in seen:
            seen.add(r["label"])
            uniq.append(r)
    return uniq


def _provenance(t):
    """What the file declares about its own origin: C2PA manifest, or IPTC DigitalSourceType in XMP."""
    from . import c2pa
    cp = t.get("c2pa") or {}
    xmp_types = [c2pa._short(i["value"]) for i in t.get("xmp", []) if "digitalsourcetype" in i["name"].lower()]
    if not cp.get("present") and not xmp_types:
        return None
    types = sorted(set((cp.get("digital_source_types") or []) + xmp_types))
    ai = [x for x in types if x in c2pa.AI_SOURCE_TYPES]
    agents = sorted({a["software_agent"] for a in cp.get("actions", []) if a.get("software_agent")})
    acts = [a["action"].replace("c2pa.", "") for a in cp.get("actions", []) if a.get("action")]
    parts = []
    if ai:
        parts.append(f"AI-GENERATED per the file ({ai[0]}: {c2pa.AI_SOURCE_TYPES[ai[0]]})")
    elif types:
        parts.append("source type " + ", ".join(types))
    if agents:
        parts.append("software " + ", ".join(agents))
    if cp.get("generator"):
        parts.append("generator " + cp["generator"])
    if acts:
        parts.append("actions " + ", ".join(dict.fromkeys(acts)))
    when = next((a["when"] for a in cp.get("actions", []) if a.get("when")), None)
    if when:
        parts.append(f"at {when}")
    return {"ai_generated": bool(ai), "source_types": types, "software": agents, "generator": cp.get("generator"),
            "actions": cp.get("actions", []), "title": cp.get("title"), "signature": cp.get("signature"),
            "from": "C2PA manifest" if cp.get("present") else "XMP Iptc4xmpExt:DigitalSourceType",
            "headline": "; ".join(parts) or "manifest present",
            "meaning": ("The file declares it was made or altered by generative AI: it may show a place that does not "
                        "exist, and no location clue in it can be trusted. The C2PA signature is not verified here; "
                        "a manifest can also be removed, so its absence proves nothing.") if ai else
                       ("Declared provenance (signature not verified)."),
            "note": cp.get("meaning")}


ONLINE_FETCH = {"timeout": 15, "tries": 2}  # a scan never waits minutes for a slow service
SKIPPED = []  # online steps skipped during the current scan, with the reason (shown in the report)


def _online(a):
    """True when the analyst asked for online lookups (--online) and did not also say --no-online."""
    from .cli import STATE
    return bool(getattr(a, "online", False)) and STATE.get("online", True)


def _net_skip(what, err):
    from . import net
    reason = "no contact set (see: orith doctor)" if "no contact" in str(err) else str(err)
    SKIPPED.append(f"{what}: skipped, {reason}")
    if "no contact" in str(err):
        SKIPPED[-1] += ". " + net.contact_help()


def _find_place(name, online=False):
    """First match for a place name: offline country packs, then (only with online=True) Nominatim.
    A network problem never stops the scan: the place is reported as not found and the reason is kept."""
    from . import net
    hit = geocode.offline(name, limit=1)
    if hit:
        return hit[0] | {"source": "offline"}
    if online:
        try:
            results, _ = geocode.online(name, limit=1, **ONLINE_FETCH)
        except (net.NetError, OSError, ValueError) as e:
            _net_skip(f"online search for '{name}'", e)
            return None
        if results:
            return results[0] | {"source": results[0].get("source", "nominatim")}
    return None


def _address(lat, lon, a, log):
    """Readable address for a GPS point: offline country outline and installed pack areas always; Nominatim
    street address only with --online."""
    from . import net
    cc = facts.country_at(lat, lon)
    out = {"country": cc[0] if cc else None, "areas": [], "online": None}
    for p in geocode.reverse(lat, lon):
        out["areas"] += [f"{x['label']} {x['name']}" for x in p["admin"] if x.get("name")]
    if _online(a):
        try:
            res, cached = geocode.reverse_online(lat, lon, **ONLINE_FETCH)
            out["online"] = res
            log("+" if res else "-", "Reverse geocoding (Nominatim)",
                (res["display_name"][:90] + (" (cached)" if cached else "")) if res else "no address there")
        except (net.NetError, OSError, ValueError) as e:
            _net_skip("reverse geocoding (Nominatim)", e)
            log("-", "Reverse geocoding (Nominatim)", "skipped: " + SKIPPED[-1].split("skipped, ", 1)[1][:70])
    parts = [x for x in ([out["online"]["display_name"]] if out["online"] else
                         out["areas"][::-1] + ([out["country"]["name"]] if out["country"] else [])) if x]
    out["text"] = ", ".join(parts) or None
    out["source"] = (out["online"]["source"] if out["online"] else
                     "offline: country outline (Natural Earth)" + (" and installed country pack" if out["areas"] else ""))
    return out


def usable(t, a):
    """True when the file or the analyst gave anything the scan can work with (not just camera facts)."""
    gps = t.get("gps") or {}
    found, _ = _metadata_texts(t)
    given = [a.text, getattr(a, "place", None), getattr(a, "note", None), a.drives, a.plug, a.lat,
             a.shadow_bearing, a.elevation, a.shadow]
    return gps.get("lat") is not None or bool(found) or any(x not in (None, [], "") for x in given)


def command_line(a):
    """The scan as one command with every answer as a flag (for scripts and for repeating it)."""
    import shlex
    parts = [paths_app(), "scan", a.image]
    for flag, vals in (("--drives", a.drives), ("--plug", a.plug), ("--text", a.text),
                       ("--place", getattr(a, "place", None)), ("--note", getattr(a, "note", None))):
        for v in vals or []:
            parts += [flag, v]
    for flag, v in (("--time", a.time), ("--utc-offset", a.utc_offset), ("--shadow-bearing", a.shadow_bearing),
                    ("--elevation", a.elevation), ("--height", a.height), ("--shadow", a.shadow)):
        if v is not None:
            parts += [flag, str(v)]
    return " ".join(shlex.quote(str(p)) if any(ch in str(p) for ch in " '\"|&;<>") else str(p) for p in parts)


def paths_app():
    from . import paths
    return paths.APP


def _narrowing(options):
    fc = facts.filter_countries(options)
    soft = {o for o in facts.CLUES if facts.CLUES[o][0] in facts.SOFT_ONLY}

    def reasons(c):
        out = []
        for r in c["clues"]:
            opt = r["clue"].split("=", 1)[0]
            f = r["facts"][0] if r["facts"] else {}
            out.append({"clue": r["clue"], "result": r["result"], "kind": "soft" if opt in soft else "can exclude",
                        "source": f.get("source"), "retrieved": f.get("retrieved"),
                        "fact": ", ".join(sorted({x["value"] for x in r["facts"]}))[:60] if r["facts"] else None})
        return out
    by_clue = {}
    for e in fc["excluded"]:
        first = e["excluded_by"][0]
        key = first["clue"]
        src = first["facts"][0]["source"] if first["facts"] else None
        by_clue.setdefault(key, {"clue": key, "countries": 0, "source": src})["countries"] += 1
    nxt = facts.best_next_clue([c["iso2"] for c in fc["consistent"]], [o for o, _ in options])
    return {"clues": fc["clues"], "counts": fc["counts"], "note": fc["note"],
            "most_likely": [{k: c[k] for k in ("iso2", "country", "matches", "unknown", "uncertain")} |
                            {"reasons": reasons(c)} for c in fc["consistent"][:15]],
            "excluded_by_clue": sorted(by_clue.values(), key=lambda x: -x["countries"]),
            "excluded_examples": [{"iso2": e["iso2"], "country": e["country"],
                                   "by": [{"clue": w["clue"], "values": sorted({f["value"] for f in w["facts"]}),
                                           "source": w["facts"][0]["source"] if w["facts"] else None}
                                          for w in e["excluded_by"]]} for e in fc["excluded"][:5]],
            "next_clue": nxt}


def run(a, path, log=None, t=None):
    """a: parsed arguments (image, case, text, time, utc_offset, shadow_bearing, elevation, height, shadow,
    lat, lon, drives, plug, place, note, setting, no_record). path: the case.json path.
    log(mark, label, detail): progress lines ("+" found, "-" nothing, "!" warning, "*" info).
    Returns the scan result (JSON-ready)."""
    from . import buildings, fingerprint
    from .cli import _quick_summary, _rank_view  # shared with the quick/rank commands
    log = log or (lambda *x: None)
    SKIPPED.clear()
    img = Path(a.image)
    if not img.is_file():
        raise ledger.LedgerError(f"image not found: {img}")
    created = not path.exists()
    if created:
        ledger.save(ledger.new_case(img, hashlib.sha256(img.read_bytes()).hexdigest()), path)
    if t is None or t.get("thumbnail") or t.get("data_after_end"):  # extraction writes those parts to the case
        t = triage.triage(img, extract_dir=path.parent / "extracted")
    (path.parent / "triage.json").write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = _quick_summary(t)
    gps = t.get("gps") or {}
    found, places = _metadata_texts(t)
    bits = [x for x in ("GPS" if gps.get("lat") is not None else "", "capture time" if (t.get("time") or {}).get(
        "DateTimeOriginal") else "", "camera" if summary.get("camera") else "", f"{len(found)} text field(s)" if found
        else "", "hidden data after the image" if summary.get("data_after_end") else "",
        "thumbnail differs" if (summary.get("thumbnail") or {}).get("differs") else "",
        "C2PA content credentials" if summary.get("c2pa") else "") if x]
    log("+" if bits else "-", "Reading the file (EXIF, GPS, XMP, IPTC, C2PA, hidden data)", ", ".join(bits) or "no metadata")
    prov = _provenance(t)
    if prov:
        log("!" if prov["ai_generated"] else "*", "Content credentials (C2PA)", prov["headline"])

    fname = fingerprint.file_name_time(img.name)
    log("+" if fname else "-", "Looking for a date in the file name",
        f"{fname['local']} ({fname['pattern']})" if fname else "none")
    try:
        fjpeg = fingerprint.jpeg(img)
    except Exception:  # noqa: BLE001 - an unreadable image body still gets the rest of the scan
        fjpeg = None
    if fjpeg and fjpeg.get("quality"):
        log("*", "JPEG encoder fingerprint", f"quality {fjpeg['quality']}"
            f"{' (standard libjpeg table: saved by software)' if fjpeg['standard_table'] else ' (camera/app table)'}, "
            f"{fjpeg['chroma_subsampling']}{', progressive' if fjpeg['progressive'] else ''}")
    try:
        fhash = fingerprint.hashes(img)
    except Exception:  # noqa: BLE001
        fhash = None
    from . import verify
    chk, implied = verify.checks(t, fname, prov)
    if chk:
        bad = [c for c in chk if c["result"] in ("mismatch", "warning")]
        log("!" if bad else "+", "Consistency checks (times, zone, sun, name, editing)",
            f"{sum(c['result'] == 'ok' for c in chk)} ok, {len(bad)} problem(s)" +
            (": " + "; ".join(c["check"] for c in bad) if bad else ""))
    ela_out = None
    if fjpeg and not getattr(a, "no_ela", False):
        from . import ela
        try:
            ela_out = ela.run(img, out=path.parent / "ela.png")
            log("*", "Error level analysis (JPEG)", f"{len(ela_out['hot_cells'])} area(s) stand out; image "
                                                    f"{path.parent.name}/ela.png in the cases folder")
        except Exception:  # noqa: BLE001 - ELA is an extra; the scan goes on
            ela_out = None

    time_info, utc = _time(t, a)
    if not time_info.get("local") and fname and not a.time:
        time_info["local"] = fname["local"]
        if not fname["date_only"] and a.utc_offset:
            try:
                u = sun.parse_time(fname["local"], a.utc_offset)
                utc = u
                time_info.update(utc=u.astimezone(dt.UTC).isoformat().replace("+00:00", "Z"),
                                 source=f"file name, read in UTC{a.utc_offset}", note=None)
            except ValueError:
                pass
        if not utc:
            time_info.update(source="file name (time zone unknown: give --utc-offset to use the sun)", note=None)

    typed = list(a.text or [])
    all_text = "\n".join(typed + [str(v) for _, v in found])
    ta = text.analyse(all_text) if all_text.strip() else None
    tclues = _clues_from_text(ta) if ta else []
    if ta:
        tx = _text_summary(ta)
        log("+", "Reading the text", "; ".join(x for x in (
            "scripts " + ", ".join(s["code"] for s in tx["scripts"]) if tx["scripts"] else "",
            "phone " + ", ".join(p["e164"] for p in tx["phones"]) if tx["phones"] else "",
            "domains " + ", ".join(tx["domains"]) if tx["domains"] else "",
            "currencies " + ", ".join(tx["currencies"]) if tx["currencies"] else "") if x) or "no usable clue")

    options = [(o, v) for o, v, *_ in tclues] + [("drives", d) for d in (a.drives or [])] + \
              [("plug", p) for p in (a.plug or [])]
    options = list(dict.fromkeys(options))
    narrowing = _narrowing(options) if options else None
    if narrowing:
        top = ", ".join(k["iso2"] for k in narrowing["most_likely"] if k["matches"])[:40]
        log("+", f"Narrowing countries with {len(options)} clue(s)",
            f"{narrowing['counts']['consistent']} of {narrowing['counts']['total']} remain" + (f" (best: {top})" if top else ""))
    gps_country = facts.country_at(gps["lat"], gps["lon"]) if gps.get("lat") is not None else None
    address = _address(gps["lat"], gps["lon"], a, log) if gps.get("lat") is not None else None
    if address and not address["online"]:
        log("+" if address["text"] else "-", "Where the GPS point is (offline)", address["text"] or "outside every "
                                                                                                 "country outline")
    regions = _regions(t, ta, places, a)
    if regions:
        log("+", "Turning places into hypotheses", ", ".join(r["label"] for r in regions)[:90])

    recorded = {"hypotheses": [], "clues": [], "evidence": []}
    sun_out = None
    if not a.no_record:
        case = ledger.load(path)
        labels = {h["label"]: h for h in case["hypotheses"]}
        made = {}
        for r in regions:
            if r["label"] in labels:
                made[r["label"]] = labels[r["label"]]
                continue
            if r["evidence"]:
                h = r["evidence"]
                ev = ledger.add_evidence(case, h.get("source") or "offline", h["lat"], h["lon"], name=h["name"],
                                         precision_m=h["precision_m"], ref=h.get("ref", ""), query=r["label"])
                recorded["evidence"].append(ev["id"])
                hyp = ledger.add_hypothesis(case, r["label"], evidence=ev["id"])
            else:
                hyp = ledger.add_hypothesis(case, r["label"], r["lat"], r["lon"])
            made[r["label"]] = hyp
            recorded["hypotheses"].append(hyp["id"])
        made_ev = {lbl: h.get("evidence") for lbl, h in made.items() if h.get("evidence")}
        texts_now = {c["text"]: c for c in case["clues"]}
        new_clues = []
        if gps.get("lat") is not None:
            new_clues.append(("EXIF GPS position in the file (it can be edited or planted)", "gps", 5, 5, 2, None,
                              None, None))
        for opt, val, ctext, group, u, p, r in tclues:
            new_clues.append((ctext, group, u, p, r, None, opt, val))
        for d in a.drives or []:
            new_clues.append((f"cars drive on the {d}", "driving", 2, 5, 4, None, "drives", d))
        for pl in a.plug or []:
            new_clues.append((f"plug or socket type {pl.upper()} seen", "plug", 3, 4, 3, None, "plug", pl))
        for r in regions:
            if r["label"].startswith("place you read:") and r["label"] in made_ev:
                new_clues.append((f"place name read in the photo: {r['label'].split(': ', 1)[1]}", "place", 4, 4, 3,
                                  made_ev[r["label"]], None, None))
        for n in getattr(a, "note", None) or []:
            new_clues.append((f"observed: {n} (no sourced data: not used to narrow countries)", "observation",
                              2, 3, 3, None, None, None))
        if prov:
            new_clues.append((f"file provenance: {prov['headline']}", "provenance", 3, 5, 3, None, None, None))
        for ctext, group, u, p, r, evid, opt, val in new_clues:
            if ctext in texts_now:
                continue
            clue = ledger.add_clue(case, ctext, "SOFT", u, p, r, group, evidence=evid)
            recorded["clues"].append(clue["id"])
            for h in case["hypotheses"]:
                rel = None
                if group == "gps":
                    rel = "supports" if h["label"].startswith("EXIF GPS position") else None
                elif group == "phone" and h["label"].startswith("phone area:"):
                    rel = "supports"
                elif group == "place":
                    rel = "supports" if h.get("evidence") == evid else None
                elif opt:
                    rel = _fact_relation(h["lat"], h["lon"], opt, val)
                if rel:
                    ledger.add_link(case, clue["id"], h["id"], rel, note="scan: decided by a sourced fact"
                                    if opt and group != "phone" else "scan")
        ledger.save(case, path)

    case = ledger.load(path)
    # buildings around every hypothesis (optional offline layer)
    built = None
    if case["hypotheses"] and buildings.tiles():
        built = []
        for h in case["hypotheses"][:12]:
            r = buildings.near(h["lat"], h["lon"], 500)
            built.append({"hypothesis": h["id"], "label": h["label"]} | r)
        cov = [b for b in built if b["covered"]]
        log("+" if cov else "-", "Counting buildings near each hypothesis (GlobalBuildingAtlas)",
            ", ".join(f"{b['hypothesis']} {b['count']}" for b in cov) + " within 500 m" if cov
            else "no installed tile covers the hypotheses")
        setting = getattr(a, "setting", None)
        if setting and cov and not a.no_record:
            ctext = f"setting seen: {setting} (tested on GlobalBuildingAtlas building counts within 500 m)"
            case = ledger.load(path)
            if ctext not in {c["text"] for c in case["clues"]}:
                clue = ledger.add_clue(case, ctext, "SOFT", 2, 4, 2, "setting")
                recorded["clues"].append(clue["id"])
                for b in cov:
                    if buildings.setting_matches(setting, b["count"]):
                        ledger.add_link(case, clue["id"], b["hypothesis"], "supports",
                                        note=f"{b['count']} buildings within 500 m; " + buildings.SETTING_RULE)
                ledger.save(case, path)
            for b in cov:
                b["setting_match"] = buildings.setting_matches(setting, b["count"])
    elif case["hypotheses"]:
        log("-", "Counting buildings near each hypothesis", "no building layer installed (tools/fetch_gba.py)")

    case = ledger.load(path)
    measured = (a.shadow_bearing is not None or a.elevation is not None or (a.height and a.shadow))
    elev = a.elevation
    if elev is None and a.height and a.shadow:
        elev = sun.elevation_from_shadow(a.height, a.shadow)
    if utc and case["hypotheses"]:
        if measured:
            params = {"time": utc, "time_source": time_info["source"], "shadow_bearing": a.shadow_bearing,
                      "elevation": elev}
            desc = candidates._describe("sun", params)
            done = any(c["text"].startswith(f"sun test: {desc}") for c in case["clues"])
            res = candidates.run(path, "sun", params, record=not (done or a.no_record))
            sun_out = {"mode": "test", "results": res["results"], "recorded": res.get("recorded")}
            log("+", "Checking the sun at each hypothesis", ", ".join(f"{r['hypothesis']} {r['result']}"
                                                                     for r in res["results"]))
        else:
            sun_out = {"mode": "expected", "time_utc": time_info["utc"],
                       "results": [{"hypothesis": h["id"], "label": h["label"]} |
                                   {k: round(v, 1) for k, v in sun.shadow(h["lat"], h["lon"], utc).items()
                                    if isinstance(v, float)} for h in case["hypotheses"]]}
            log("*", "Sun position at each hypothesis", "computed (measure a shadow to test it)")
    elif utc and elev:
        sslat, sslon = sun.subsolar_point(utc)
        r_km = (90 - sun.apparent_to_true(elev)) * 111.195
        sun_out = {"mode": "ring", "subsolar_point": [round(sslat, 3), round(sslon, 3)], "radius_km": round(r_km),
                   "note": f"Places where the sun stood at {elev:.1f} deg lie on a circle of about {r_km:.0f} km "
                           "around the subsolar point; draw it with: orith tools sun ring"}
        log("+", "Sun elevation ring", f"{r_km:.0f} km around the subsolar point")
    case = ledger.load(path)
    rank = _rank_view(case, ledger.evaluate(case))
    log("*", "Ledger", f"{rank['verdict']}, {len(rank['ranking'])} hypothesis(es), {len(case['clues'])} clue(s)")

    point = None
    if gps.get("lat") is not None:
        point = (gps["lat"], gps["lon"])
    elif a.lat is not None and a.lon is not None:
        point = (a.lat, a.lon)
    elif rank["top"]:
        top = next(r for r in rank["ranking"] if r["id"] == rank["top"])
        point = (top["lat"], top["lon"])
    lk = links.all_links(*(point or (None, None)), utc_iso=time_info["utc"])

    if a.online and not _online(a):
        SKIPPED.append("online lookups: skipped, --no-online given")
    elif not a.online and (getattr(a, "place", None) or address):
        SKIPPED.append("online lookups (street address, places not in the offline data): off; add --online "
                       "(needs a contact, see orith doctor)")
    res = {"case": path.parent.name, "case_dir": str(path.parent), "case_created": created, "address": address,
            "skipped": list(SKIPPED),
            "image": {"file": str(img), "type": summary.get("type"), "size": summary.get("size"),
                      "notes": summary.get("notes", [])},
            "file": {"name_time": fname, "jpeg": fjpeg, "hashes": fhash, "ela": ela_out}, "provenance": prov,
            "checks": chk, "implied_utc_offset": implied.get("utc_offset"),
            "evidence": _evidence_list(summary, gps, gps_country, typed, found, a),
            "time": time_info, "text": _text_summary(ta) if ta else None, "narrowing": narrowing,
            "regions": [{k: v for k, v in r.items() if k != "evidence"} for r in regions],
            "buildings": built, "setting": getattr(a, "setting", None),
            "sun": sun_out, "recorded": recorded, "ledger": rank,
            "clues": [{"id": c["id"], "kind": c["kind"], "text": c["text"], "u": c["uniqueness"], "p": c["permanence"],
                       "r": c["reliability"], "weight": round(ledger.weight(c), 3),
                       "eliminates": ledger.eliminates(c)} for c in case["clues"]],
            "next_steps": _next_steps(path.parent.name, a, time_info, utc, measured, rank, narrowing, point, ta, case),
            "usable": usable(t, a) or bool(case["hypotheses"]) or any(c["group"] != "provenance" for c in case["clues"]),
            "command": command_line(a), "places_not_found": [p for p in (getattr(a, "place", None) or [])
                                                             if not any(r["label"].startswith("place you read:") and
                                                                        p.lower() in r["source"].lower() for r in regions)],
            "links": lk}
    # the final reasoning step: one best guess, every clue with a confidence level, leads to check by hand
    from . import summary as reasoning
    res["best_guess"] = reasoning.best_guess(res)
    res["evidence_ranked"] = reasoning.evidence(res)
    res["leads"] = reasoning.leads(res, res["best_guess"])
    return res


def _evidence_list(s, gps, gps_country, typed, found, a=None):
    ev = []
    for d in getattr(a, "drives", None) or []:
        ev.append({"item": "you saw: driving side", "value": d, "weight": "soft (narrows countries)"})
    for p in getattr(a, "plug", None) or []:
        ev.append({"item": "you saw: plug type", "value": p.upper(), "weight": "soft (narrows countries)"})
    for p in getattr(a, "place", None) or []:
        ev.append({"item": "you read: place name", "value": p, "weight": "soft (map position)"})
    for n in getattr(a, "note", None) or []:
        ev.append({"item": "you noted", "value": n, "weight": "recorded only (no data to narrow)"})
    if gps.get("lat") is not None:
        where = ", ".join(f"{c['iso2']} {c['name']}" for c in gps_country or []) or "no country outline"
        ev.append({"item": "GPS position", "value": f"{gps['lat']:.6f}, {gps['lon']:.6f} ({where})",
                   "weight": "soft: can be edited or planted"})
        if gps.get("image_direction_deg") is not None:
            ev.append({"item": "camera direction", "value": f"{gps['image_direction_deg']:.1f} deg", "weight": "soft"})
    cam = s.get("camera") or {}
    if cam:
        lens = f", {cam['FocalLengthIn35mmFilm']} mm (35 mm equiv.)" if cam.get("FocalLengthIn35mmFilm") else ""
        fov = f", field of view {cam['fov_long_side_deg']} deg" if cam.get("fov_long_side_deg") else ""
        ev.append({"item": "camera", "value": f"{cam.get('Make', '')} {cam.get('Model', '')}".strip() + lens + fov,
                   "weight": "context"})
    th = s.get("thumbnail") or {}
    if th.get("differs"):
        ev.append({"item": "embedded thumbnail", "value": "differs from the image (edited or cropped?)",
                   "weight": "check"})
    if s.get("data_after_end"):
        d = s["data_after_end"]
        ev.append({"item": "data after the image end", "value": f"{d['bytes']} bytes ({d.get('type') or 'unknown'})",
                   "weight": "check"})
    for name, value in found[:12]:
        ev.append({"item": f"text in file: {name}", "value": str(value)[:120], "weight": "soft"})
    for tx in typed:
        ev.append({"item": "text you typed", "value": tx[:120], "weight": "soft"})
    return ev


def _text_summary(ta):
    return {"scripts": [{k: c[k] for k in ("script", "code", "chars")} for c in ta["scripts"]["counts"]],
            "phones": [{k: n.get(k) for k in ("e164", "region_of_number", "type", "area_description")}
                       for n in ta["phones"]["numbers"]],
            "domains": [d.get("tld_ascii") for d in ta["domains"] if d.get("result") == "match"],
            "currencies": [c["code"] for c in ta["currencies"]], "facts_clues": ta["facts_clues"]}


def _next_steps(cid, a, time_info, utc, measured, rank, narrowing, point, ta, case):
    steps = []
    img = a.image
    has_text = any(c["group"] in ("phone", "script", "domain", "currency") for c in case["clues"])
    measured = measured or any(c["text"].startswith("sun test:") for c in case["clues"])
    if not ta and not a.text and not has_text:
        steps.append(f'Type any text you can read in the photo: orith scan {img} --text "..."')
    if not utc:
        steps.append(f"Capture time has no usable zone: orith scan {img} --utc-offset +HH:MM "
                     "(zones of a country: orith tools tz --country CC)")
    elif not measured:
        steps.append(f"Measure a shadow direction (degrees from north) to test the sun: orith scan {img} "
                     "--shadow-bearing DEG")
    if not rank["ranking"]:
        steps.append(f"Add a hypothesis: orith hyp add --case {cid} --label PLACE --lat LAT --lon LON")
    elif len(rank["ranking"]) < 3:
        steps.append(f"Keep at least 3 rival hypotheses: orith hyp add --case {cid} --label PLACE --lat LAT --lon LON")
    if point:
        steps.append(f'Look for a landmark near the best point: orith tools osm --feature WORD --around '
                     f'"{point[0]:.5f},{point[1]:.5f},1500" --case {cid}')
    steps.append(f'Record what you see: orith clue add --case {cid} --text "..." --kind SOFT --u 3 --p 4 --r 4 '
                 f'--group G --supports h1')
    steps.append(f"Full ledger: orith report --case {cid}")
    return steps


# ----------------------------------------------------------------------------------------- text view

NO_LOOK = "This tool does not look at the picture itself. Tell it what you see:"
FIELD_NAMES = {"drives": "driving side", "plug": "plug type", "calling_code": "calling code", "tld": "web domain",
               "currency": "currency", "script": "script", "language": "language", "continent": "continent",
               "frequency": "mains frequency"}


def template_command(o):
    img = o["image"]["file"]
    return (f'orith {img} --text "TEXT ON SIGNS" --drives left|right --plug C --place "PLACE NAME" '
            f'--note "ANYTHING ELSE"')


def _clue_label(clue):
    opt, val = clue.split("=", 1)
    return f"{FIELD_NAMES.get(opt, opt)} {val}"


def _sec_provenance(st, o):
    p = o.get("provenance")
    if not p:
        return []
    color = "red" if p["ai_generated"] else "cyan"
    title = (" AI-GENERATED (declared by the file itself) " if p["ai_generated"] else " CONTENT CREDENTIALS ")
    line = "!" * 60 if p["ai_generated"] else "-" * 60
    out = ["", st.c(line, color), st.c(title, "bold", color)]
    out.append("   " + st.t(f"from: {p['from']}"))
    out += ["   " + st.t(x) for x in p["headline"].split("; ")]
    if p.get("title"):
        out.append("   " + st.t(f"title in the manifest: {p['title']}"))
    out.append("   " + st.t(p["meaning"]))
    out.append(st.c(line, color))
    return out


def _sec_file(st, o):
    """What the file itself gives away beyond its metadata."""
    f, out = o.get("file") or {}, []
    nt, jp, hs = f.get("name_time"), f.get("jpeg"), f.get("hashes")
    rows = []
    if nt:
        rows.append(("date in the file name", f"{nt['local']}  (pattern '{nt['pattern']}'; zone unknown)"))
    if jp and jp.get("quality"):
        rows.append(("JPEG encoder", f"quality {jp['quality']}, "
                     f"{'standard libjpeg table' if jp['standard_table'] else 'camera/app table'}, "
                     f"{jp['chroma_subsampling']}{', progressive' if jp['progressive'] else ''}"))
    if hs:
        rows.append(("perceptual hash", f"dHash {hs['dhash']}  aHash {hs['ahash']}"))
    el = f.get("ela")
    if el:
        shown = el["image"]
        try:
            shown = str(Path(el["image"]).resolve().relative_to(Path.cwd().resolve()))
        except (ValueError, OSError, TypeError):
            pass
        rows.append(("error level (ELA)", f"image {shown}; mean {el['mean_error']}, max {el['max_error']}"))
        for c in el["hot_cells"][:4]:
            rows.append(("  stands out", f"{c['where']} ({c['times_median']}x the typical cell)"))
    if not rows:
        return []
    out.append(display.heading(st, "The file"))
    out += display.kv(st, rows)
    if jp and jp.get("note"):
        out.append(st.c("  " + jp["note"], "dim"))
    if el:
        out.append(st.c("  ELA: " + el["note"], "dim"))
    return out


def _sec_checks(st, o):
    chk = o.get("checks") or []
    if not chk:
        return []
    style = {"ok": ("green",), "mismatch": ("bold", "red"), "warning": ("yellow",), "info": ("cyan",),
             "unknown": ("dim",)}
    order = {"mismatch": 0, "warning": 1, "ok": 2, "info": 3, "unknown": 4}
    out = [display.heading(st, "Verification (is the file consistent with itself?)")]
    w = max(len(c["check"]) for c in chk)
    for c in sorted(chk, key=lambda c: order.get(c["result"], 9)):
        word = c["result"].upper() if c["result"] == "mismatch" else c["result"]
        out.append("  " + st.c(f"{word:<8}", *style.get(c["result"], ())) + f" {c['check']:<{w}}  " + st.t(c["detail"]))
    return out


def _sec_countries(st, nar, brief):
    out, c = [], nar["counts"]
    out.append(display.heading(st, "Most likely countries"))
    out.append(f"  from {', '.join(_clue_label(x) for x in nar['clues'])}: {c['consistent']} of {c['total']} "
               f"countries remain, {c['excluded']} excluded")
    with_match = [k for k in nar["most_likely"] if k["matches"] > 0]
    no_data = [k for k in nar["most_likely"] if k["matches"] == 0]
    top = with_match[: (3 if brief else 8)]
    for i, k in enumerate(top, 1):
        n_clues = len(k["reasons"])
        out.append(f"  {i}. {k['iso2']}  {st.t(k['country'])}  " +
                   st.c(f"{k['matches']} of {n_clues} clues match", "green" if k["matches"] == n_clues else "yellow"))
        if not brief and i <= 3:
            for r in k["reasons"]:
                src = (r["source"] or "").split(" (")[0]
                if r["result"] == "match":
                    line = f"{_clue_label(r['clue'])}: matches  [{r['kind']}]  {src}"
                elif r["result"] == "uncertain":
                    line = f"{_clue_label(r['clue'])}: the source lists this country as an exception (never excludes)"
                elif r.get("fact"):
                    line = f"{_clue_label(r['clue'])}: not listed here (listed: {r['fact']}); a soft clue never excludes"
                else:
                    line = f"{_clue_label(r['clue'])}: no data for this country (unknown never excludes)"
                out.append(st.c("       " + line, "dim"))
    if not with_match:
        out.append("  no country matches any clue yet; none is excluded by missing data")
    if len(with_match) > len(top):
        out.append(st.c(f"  ... {len(with_match) - len(top)} more countries with a match (--json for all)", "dim"))
    if no_data and not brief:
        out.append(st.c(f"  not excluded only for lack of data ({len(no_data)}): " +
                        ", ".join(k["iso2"] for k in no_data), "dim"))
    if nar["excluded_by_clue"] and not brief:
        parts = [f"{x['countries']} by {_clue_label(x['clue'])} ({(x['source'] or '').split(' (')[0]})"
                 for x in nar["excluded_by_clue"]]
        out.append(f"  excluded: {'; '.join(parts)}")
    nxt = nar.get("next_clue")
    if nxt:
        out.append(st.c("  what to look for next: ", "bold") + f"{nxt['look_for']} - it would leave about "
                   f"{nxt['expected_remaining']:g} of these {nxt['of']} countries (from the fact table)")
    if not brief:
        out.append(st.c("  [can exclude] = a sourced fact of this kind can rule a country out; [soft] = only ranks",
                        "dim"))
    return out


def _sec_hypotheses(st, o, brief):
    led = o["ledger"]
    if not led["ranking"]:
        return []
    built = {b["hypothesis"]: b for b in (o.get("buildings") or []) if b.get("covered")}
    rows = []
    for r in led["ranking"][: (3 if brief else 12)]:
        state = "ELIMINATED " + ",".join(r["eliminated_by"]) if r["status"] == "eliminated" else "alive"
        b = built.get(r["id"])
        bld = "no tile" if not b else f"{b['count']}" + (" ok" if b.get("setting_match") else
                                                           (" no" if b.get("setting_match") is False else ""))
        rows.append([r["id"], r["label"], f"{r['lat']:.5f}, {r['lon']:.5f}", state, f"{r['relative_score']:+.3f}",
                     r["map_anchored_clues"], r["independent_agreeing"]] + ([bld] if built else []))
    hdr = ["id", "label", "lat, lon", "status", "rel.score", "anchored", "agreeing"] + (
        ["buildings 500 m"] if built else [])
    out = [display.heading(st, "Hypotheses (ranked)")] + display.table(st, hdr, rows)
    if built and not brief:
        from . import buildings
        note = "buildings: GlobalBuildingAtlas (ML-derived): a hint, not proof"
        if o.get("setting"):
            note += f"; ok/no = setting '{o['setting']}' by " + buildings.SETTING_RULE_SHORT
        out.append(st.c("  " + note, "dim"))
    return out


CONF_STYLE = {"HIGH": ("bold", "green"), "MEDIUM": ("yellow",), "LOW": ("dim",), "none": ("dim",),
              "PROBLEM": ("bold", "red")}


def _conf(st, level, width=7):
    return st.c(f"{level:<{width}}", *CONF_STYLE.get(level, ()))


def render(st, o, brief=False, verbose=None):
    """Default view: Verdict, Evidence found, Leads to check manually. -v adds the full working."""
    if verbose is None:
        from .cli import STATE
        verbose = bool(STATE.get("verbose"))
    if verbose or "best_guess" not in o:
        return render_full(st, o, brief)
    H, out = display.heading, []
    img = o["image"]
    size = "x".join(map(str, img["size"])) if img.get("size") else "?"
    out.append(st.c("orith", "bold") + f"  {st.t(img['file'])}  ({img.get('type')}, {size} px)")
    if o.get("warning"):
        out.append(st.c("  ! " + st.t(o["warning"]), "yellow"))
    out += _sec_provenance(st, o)
    g = o["best_guess"]

    out.append(H(st, "Verdict"))
    if g["point"]:
        out.append("  best guess   " + st.c(f"{g['point'][0]:.5f}, {g['point'][1]:.5f}", "bold") + "  "
                   + st.t(g["location"]))
        out.append("  confidence   " + _conf(st, g["confidence"]) + " " + st.t(g["why"]))
    else:
        out.append("  best guess   " + st.c("none", "bold"))
        out.append("  " + st.c("why          ", "dim") + st.t(g["why"]))
    ad = o.get("address")
    if ad and ad.get("text"):
        out.append("  address      " + st.t(ad["text"]) + st.c(f"  ({ad['source']})", "dim"))
    if g["country"]:
        out.append("  country      " + st.t(g["country"]) + "  " + _conf(st, g["country_confidence"], 0) + " "
                   + st.c(st.t(g["country_why"]), "dim"))
    led = o["ledger"]
    out.append(st.c("  identified?  " + ("YES: every condition of the evidence rule holds" if led["verdict"] ==
                                         "IDENTIFIED" else "no: a lead, not a fact (that needs 3 independent clues "
                                                          "agreeing on a map within 250 m; -v shows what is missing)"),
                    "dim"))

    ev = o["evidence_ranked"]
    out.append(H(st, "Evidence found"))
    if ev:
        w = max(len(e["clue"]) for e in ev)
        w = min(w, 70)
        for e in ev:
            clue = e["clue"] if len(e["clue"]) <= w else e["clue"][: w - 3] + "..."
            out.append("  " + _conf(st, e["confidence"]) + " " + st.t(f"{clue:<{w}}") + "  " + st.c(st.t(e["source"]), "dim"))
    else:
        out.append("  nothing usable in the file")

    out.append(H(st, "Leads to check manually"))
    if not o.get("usable"):
        out.append(st.c("  " + NO_LOOK, "bold"))
        out.append("    " + st.t(template_command(o)))
        out.append(st.c("    (or answer short questions: add --ask)", "dim"))
    out += display.bullets(st, o["leads"][:6] if brief else o["leads"])
    if o.get("skipped"):
        out.append("")
        out += [st.c("  skipped: " + st.t(s), "dim") for s in o["skipped"]]
    out.append("")
    out.append(st.c(f"  saved to the case '{o['case']}'.  Full working (hypotheses, checks, sun, ledger): add -v.  "
                    "Machine-readable: --json", "dim"))
    return out


def render_full(st, o, brief=False):
    H, out = display.heading, []
    img = o["image"]
    size = "x".join(map(str, img["size"])) if img.get("size") else "?"
    out.append(st.c("orith scan", "bold") + f"  {st.t(img['file'])}  ({img.get('type')}, {size} px)  case "
               + st.c(o["case"], "bold") + ("  (new)" if o["case_created"] else "  (continued)"))
    led = o["ledger"]
    out += _sec_provenance(st, o)
    if not o.get("usable"):
        out.append("")
        out.append(st.c(NO_LOOK, "bold"))
        out.append("  " + st.t(template_command(o)))
        out.append(st.c("  (or answer short questions: add --ask)", "dim"))
        out += _sec_checks(st, o)
        out += _sec_file(st, o)
        out.append("")
        out += display.banner(st, led["verdict"], led["unmet"] or ["all conditions of the rule hold"])
        out += _links(st, o)
        return out

    # 1. the answer first
    out.append("")
    out += display.banner(st, led["verdict"], led["unmet"] or ["all conditions of the rule hold"])
    if led["top"]:
        t = next(r for r in led["ranking"] if r["id"] == led["top"])
        out.append(st.c("  best hypothesis: ", "bold") + st.t(f"{t['id']} {t['label']}  ({t['lat']:.5f}, {t['lon']:.5f})"))
    nar = o.get("narrowing")
    if nar:
        best = [k for k in nar["most_likely"] if k["matches"]][:3]
        if best:
            out.append(st.c("  countries:       ", "bold") + ", ".join(
                f"{k['iso2']} {st.t(k['country'])} ({k['matches']}/{len(k['reasons'])})" for k in best))
    if not brief and o["recorded"] and any(o["recorded"].values()):
        r = o["recorded"]
        out.append(st.c(f"  recorded now: {len(r['hypotheses'])} hypothesis(es), {len(r['clues'])} clue(s) (all soft), "
                        f"{len(r['evidence'])} evidence item(s)", "dim"))
    # 2. is the file what it claims to be, then where, then why
    if not brief:
        out += _sec_checks(st, o)
    out += _sec_hypotheses(st, o, brief)
    if nar:
        out += _sec_countries(st, nar, brief)
    if not brief:
        ti = o["time"]
        if o["evidence"] or ti.get("local") or ti.get("utc") or img.get("notes"):
            out.append(H(st, "Evidence found"))
            out += display.kv(st, [("capture time (local)", ti.get("local")), ("capture time (UTC)", ti.get("utc")),
                                   ("time source", ti.get("source") or (ti.get("note") if ti.get("local") else None))])
            if ti.get("other_offsets"):
                out += display.bullets(st, [f"{x['tag']} {x['value']} (belongs to {x['belongs_to']})"
                                            for x in ti["other_offsets"]] +
                                       (["these offsets disagree: none was used"] if ti.get("offset_conflict") else []), 4)
            if o["evidence"]:
                out += display.table(st, ["item", "value", "weight"], [[e["item"], e["value"], e["weight"]]
                                                                      for e in o["evidence"]])
            tx = o.get("text")
            if tx:
                out += display.kv(st, [("scripts", ", ".join(f"{s['script']} ({s['chars']})" for s in tx["scripts"])),
                                       ("phones", ", ".join(f"{p['e164']} {p['region_of_number']} "
                                                             f"{p.get('area_description') or ''}".strip()
                                                             for p in tx["phones"])),
                                       ("domains", tx["domains"]), ("currencies", tx["currencies"])])
            if o.get("places_not_found"):
                out.append(st.c("  not found in the offline place data: " + ", ".join(o["places_not_found"]) +
                                " (try: orith tools geocode NAME --case " + o["case"] + ")", "dim"))
            out += display.bullets(st, img.get("notes") or [], 2, "!")
        out += _sec_file(st, o)
        if o.get("sun"):
            s = o["sun"]
            out.append(H(st, "Sun"))
            if s["mode"] == "ring":
                out.append("  " + s["note"])
            else:
                hdr = ["hyp", "label", "result"] if s["mode"] == "test" else ["hyp", "label", "sun az", "sun elev", "shadow"]
                rows = ([[r["hypothesis"], r["label"], r.get("result")] for r in s["results"]] if s["mode"] == "test" else
                        [[r["hypothesis"], r["label"], display.deg(r.get("sun_azimuth")),
                          display.deg(r.get("sun_elevation")), display.deg(r.get("shadow_bearing"))]
                         for r in s["results"]])
                out += display.table(st, hdr, rows)
                if s["mode"] == "expected":
                    out.append(st.c(f"  expected at {s['time_utc']}; compare with the shadows in the photo", "dim"))
        if o.get("clues"):
            out.append(H(st, "Clues in the case"))
            out += display.table(st, ["id", "kind", "u p r", "weight", "clue"],
                                 [[c["id"], "HARD (eliminates)" if c["eliminates"] else
                                   ("HARD as soft (r<4)" if c["kind"] == "HARD" else "soft"),
                                   f"{c['u']} {c['p']} {c['r']}", c["weight"], c["text"]] for c in o["clues"]])
    if o["next_steps"]:
        out.append(H(st, "Next steps"))
        out += display.bullets(st, o["next_steps"][:3] if brief else o["next_steps"])
    if not brief:
        out += _links(st, o)
    ad = o.get("address")
    if ad and ad.get("text"):
        out.insert(1, "  address of the GPS point: " + st.t(ad["text"]) + st.c(f"  ({ad['source']})", "dim"))
    out += [st.c("  skipped: " + st.t(s), "dim") for s in o.get("skipped") or []]
    return out


def _links(st, o):
    lk = o["links"]
    rows = []
    for group in ("maps", "street_level", "imagery_and_terrain", "tools", "reverse_image_search"):
        for name, url in (lk.get(group) or {}).items():
            rows.append(f"{name}: {url}")
    return [display.heading(st, "Links (printed only, open them yourself)")] + \
        display.bullets(st, rows[:14] if lk.get("point") else rows)
