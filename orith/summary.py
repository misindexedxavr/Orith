"""The final reasoning step: every finding of a scan combined into one best guess, each with a confidence level
and the reason for it. The rules are fixed and written here so they can be read and checked:

  HIGH    the ledger's IDENTIFIED rule holds, or the file itself declares generative AI (C2PA / IPTC)
  MEDIUM  a single strong source: GPS in the file with no consistency problem; a valid phone number, web
          domain, driving side or plug type (sourced facts that can exclude countries); one country that matches
          every such clue when at least two of them were given
  LOW     indirect or weak: GPS with a consistency problem, a town-level point from a place name or a phone
          area, writing script (never excludes), the sun (cannot separate places a few hundred km apart),
          building counts (machine-learning data), error level analysis (a visual aid)

A guess is never shown as a fact: the location line always says where it comes from, and GPS is never above
MEDIUM because metadata can be edited.
"""
from __future__ import annotations

SOFT_GROUPS = {"script"}


def _gps_ok(o):
    return not [c for c in (o.get("checks") or []) if c["result"] in ("mismatch", "warning")]


def evidence(o):
    """[{confidence, clue, source}] for every finding, strongest first; problems are confidence 'PROBLEM'."""
    items = []
    prov = o.get("provenance")
    if prov:
        items.append({"confidence": "HIGH" if prov["ai_generated"] else "MEDIUM",
                      "clue": ("the file declares it was made by generative AI" if prov["ai_generated"] else
                               "content credentials present") + (f" ({', '.join(prov['software'])})" if prov["software"] else ""),
                      "source": prov["from"] + " (signature not verified)"})
    gps = next((e for e in o["evidence"] if e["item"] == "GPS position"), None)
    if gps:
        items.append({"confidence": "MEDIUM" if _gps_ok(o) else "LOW", "clue": f"GPS {gps['value']}",
                      "source": "EXIF" + (" (consistent with the clock and time zone)" if _gps_ok(o) else
                                          " (see problems below)")})
    ti = o.get("time") or {}
    if ti.get("local") or ti.get("utc"):
        src = ti.get("source") or ti.get("note") or ""
        conf = "MEDIUM" if ti.get("utc") and "file name" not in src else "LOW"
        when = ti.get("local") or ""
        utc = str(ti.get("utc") or "").replace("T", " ").replace("Z", " UTC")
        items.append({"confidence": conf, "clue": f"taken {when.replace('T', ' ')}" + (f" = {utc}" if utc else
                                                                                     " (time zone unknown)"),
                      "source": src})
    for e in o["evidence"]:
        if e["item"] == "camera":
            items.append({"confidence": "MEDIUM", "clue": f"camera {e['value']}", "source": "EXIF"})
    tx = o.get("text") or {}
    for p in tx.get("phones", []):
        items.append({"confidence": "MEDIUM", "clue": f"phone number {p['e164']} is valid in {p['region_of_number']}"
                      + (f" ({p['area_description']})" if p.get("area_description") else ""),
                      "source": "text you typed; libphonenumber rules"})
    for d in tx.get("domains", []):
        items.append({"confidence": "MEDIUM", "clue": f"web domain ending {d}", "source": "text you typed; fact table"})
    for c in tx.get("currencies", []):
        items.append({"confidence": "LOW", "clue": f"currency {c}", "source": "text you typed (a currency can be "
                                                                              "shared by several countries)"})
    for s in tx.get("scripts", []):
        items.append({"confidence": "LOW", "clue": f"{s['script']} script on signs", "source": "text you typed "
                                                                                          "(a soft clue)"})
    for e in o["evidence"]:
        if e["item"].startswith("you saw: driving side"):
            items.append({"confidence": "MEDIUM", "clue": f"cars drive on the {e['value']}", "source": "you saw it"})
        elif e["item"].startswith("you saw: plug type"):
            items.append({"confidence": "MEDIUM", "clue": f"plug/socket type {e['value']}", "source": "you saw it"})
        elif e["item"].startswith("you read: place name"):
            found = e["value"] not in (o.get("places_not_found") or [])
            items.append({"confidence": "LOW" if found else "none",
                          "clue": f"place name '{e['value']}'" + ("" if found else " (not found in the place data)"),
                          "source": "you read it; offline place data" if found else "you read it"})
        elif e["item"].startswith("text in file"):
            items.append({"confidence": "LOW", "clue": f"{e['item'][14:]}: {e['value']}", "source": "metadata text"})
    sun = o.get("sun") or {}
    if sun.get("mode") == "test":
        passed = [r["hypothesis"] for r in sun["results"] if r.get("result") == "pass"]
        failed = [r["hypothesis"] for r in sun["results"] if r.get("result") == "fail"]
        if passed:
            items.append({"confidence": "LOW", "clue": f"the sun fits {', '.join(passed)}",
                          "source": "your shadow measurement (cannot separate nearby places)"})
        if failed:
            items.append({"confidence": "MEDIUM", "clue": f"the sun does NOT fit {', '.join(failed)}",
                          "source": "your shadow measurement"})
    for b in o.get("buildings") or []:
        if b.get("covered"):
            items.append({"confidence": "LOW", "clue": f"{b['count']} buildings within 500 m of {b['hypothesis']}",
                          "source": "GlobalBuildingAtlas (machine-learning data)"})
    el = (o.get("file") or {}).get("ela")
    if el and el.get("hot_cells"):
        items.append({"confidence": "LOW", "clue": f"{len(el['hot_cells'])} area(s) stand out in error level analysis",
                      "source": "ELA (a visual aid, not proof)"})
    for c in o.get("checks") or []:
        if c["result"] in ("mismatch", "warning"):
            items.append({"confidence": "PROBLEM", "clue": f"{c['check']}: {c['detail']}", "source": "verification"})
    for n in (o["image"].get("notes") or []):
        if n.startswith("No EXIF"):
            items.append({"confidence": "none", "clue": "no metadata in the file (stripped or never written)",
                          "source": "file"})
    order = {"PROBLEM": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "none": 4}
    return sorted(items, key=lambda i: order.get(i["confidence"], 9))


def best_guess(o):
    """{'location', 'point', 'confidence', 'why', 'country', 'country_confidence', 'country_why', 'address'}."""
    led, prov = o["ledger"], o.get("provenance")
    out = {"location": None, "point": None, "confidence": "none", "why": "", "country": None,
           "country_confidence": "none", "country_why": "", "address": o.get("address")}
    if prov and prov.get("ai_generated"):
        out["why"] = ("no location guess: the file declares it was made by generative AI, so it may show no "
                      "real place")
        return out
    gps = next((e for e in o["evidence"] if e["item"] == "GPS position"), None)
    top = next((r for r in led["ranking"] if r["id"] == led["top"]), None) if led["top"] else None
    if led["verdict"] == "IDENTIFIED" and top:
        out.update(location=top["label"], point=[top["lat"], top["lon"]], confidence="HIGH",
                   why="the ledger's IDENTIFIED rule holds: 3 independent clues anchored to map evidence within "
                       "250 m, a geometry match, no contradiction")
    elif gps and top and top["label"].startswith("EXIF GPS"):
        ok = _gps_ok(o)
        out.update(location="GPS position in the file", point=[top["lat"], top["lon"]],
                   confidence="MEDIUM" if ok else "LOW",
                   why=("GPS from the file, consistent with its clock and time zone; metadata can still be edited"
                        if ok else "GPS from the file, but the file has consistency problems (see Evidence)"))
    elif top:
        prec = ""
        out.update(location=top["label"], point=[top["lat"], top["lon"]], confidence="LOW",
                   why=f"best of {len(led['ranking'])} hypothesis(es) by relative score; a town-level point{prec}, "
                       "nothing anchored to a map feature near it yet")
    else:
        out["why"] = "no position: the file has no GPS and no place could be derived from the clues given"
    # country
    nar = o.get("narrowing")
    gps_cc = None
    if gps and "(" in gps["value"]:
        gps_cc = gps["value"].rsplit("(", 1)[1].rstrip(")").split(",")[0].strip()
    if gps_cc and gps_cc != "no country outline":
        out.update(country=gps_cc, country_confidence=out["confidence"] if out["confidence"] != "none" else "LOW",
                   country_why="the country outline containing the GPS position (Natural Earth)")
        if nar:
            best = [k for k in nar["most_likely"] if k["matches"]]
            if best and best[0]["iso2"] != gps_cc.split()[0]:
                out["country_why"] += f"; NOTE: the typed clues point to {best[0]['iso2']} instead"
                out["country_confidence"] = "LOW"
    elif nar:
        full = [k for k in nar["most_likely"] if k["matches"] == len(k["reasons"]) and k["matches"]]
        hard = sum(1 for c in nar["clues"] if not c.startswith("script="))
        if len(full) == 1:
            k = full[0]
            out.update(country=f"{k['iso2']} {k['country']}", country_confidence="MEDIUM" if hard >= 2 else "LOW",
                       country_why=f"the only country matching all {len(k['reasons'])} clue(s); "
                                   f"{nar['counts']['excluded']} excluded by sourced facts")
        elif full:
            out.update(country=", ".join(f"{k['iso2']} {k['country']}" for k in full[:3]), country_confidence="LOW",
                       country_why=f"{len(full)} countries match every clue")
        else:
            best = [k for k in nar["most_likely"] if k["matches"]][:3]
            if best:
                out.update(country=", ".join(f"{k['iso2']} {k['country']}" for k in best), country_confidence="LOW",
                           country_why="no country matches every clue; these match most")
    if out["confidence"] == "LOW" and top and not gps and out.get("country") and out["country_confidence"] == "MEDIUM":
        out["why"] += f"; country level is stronger ({out['country'].split()[0]})"
    return out


def leads(o, guess):
    """Short list of things to check by hand, most useful first."""
    out = []
    nar = o.get("narrowing")
    if nar and nar.get("next_clue"):
        n = nar["next_clue"]
        out.append(f"look for {n['look_for']}: it would leave about {n['expected_remaining']:g} of {n['of']} countries")
    if o.get("places_not_found"):
        out.append("place names not in the offline data: " + ", ".join(o["places_not_found"]) +
                   " (search them on a map, or: orith tools geocode NAME --online)")
    el = (o.get("file") or {}).get("ela")
    if el and el.get("hot_cells"):
        out.append(f"open the ELA image ({o['case']}/ela.png in the cases folder) and look at: "
                   + "; ".join(c["where"] for c in el["hot_cells"][:2]))
    lk = o.get("links") or {}
    if guess.get("point"):
        la, lo = guess["point"]
        maps = lk.get("maps") or {}
        sl = lk.get("street_level") or {}
        img = lk.get("imagery_and_terrain") or {}
        for name in ("Google Maps (satellite)", "OpenStreetMap"):
            if maps.get(name):
                out.append(f"{name}: {maps[name]}")
        if img.get("Google Earth (web)"):
            out.append(f"Google Earth: {img['Google Earth (web)']}")
        if sl.get("Google Street View"):
            out.append(f"Street View: {sl['Google Street View']}")
    for name, url in (lk.get("reverse_image_search") or {}).items():
        out.append(f"{name.replace(' (upload by hand)', '')}: {url}  (upload the image yourself)")
    placed = {r["label"].split(": ", 1)[-1].lower() for r in o.get("regions") or []}
    for p in (o.get("text") or {}).get("phones", []):
        area = p.get("area_description")
        if area and area.lower() not in placed:
            out.insert(0, f"the phone number belongs to the area '{area}' ({p['region_of_number']}): no offline place "
                          "data for it here; search it on a map, or rerun with --online")
    return out
