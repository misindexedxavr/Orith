#!/usr/bin/env python3
"""Data tool: build the country fact table (facts/) from the raw sources in tools/raw/facts/.

Output (shipped with the package):
  facts/country_facts.csv   long format: iso2, country, field, value, status, note, source, source_url, retrieved
  facts/countries_ne50m.geojson   Natural Earth 1:50m country outlines (iso2, name, continent) for point lookup
  facts/SOURCES.json        what was used, revisions, hashes, licences
  facts/unmatched.csv       source rows whose country name matched no ISO code (left out, listed for review)

Nothing is typed in by hand: every row comes from a parsed source cell. Name-to-code matching
uses only names published in GeoNames and Natural Earth. Status is "exception" when the source
itself shows more than one value or a note that mentions another value or a regional case.
"""
import csv
import json
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "tools" / "raw" / "facts"
OUT = ROOT / "orith" / "data" / "facts"
COLS = ["iso2", "country", "field", "value", "status", "note", "source", "source_url", "retrieved"]
SCRIPT_MIN_PERCENT = 5.0  # CLDR population share at or above which a language's script is listed


def norm(s):
    """Light normalisation: case, accents, punctuation, '&', parentheses and a leading 'the'. No word removal."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"^the ", "", s)


class Tables(HTMLParser):
    """Extract wikitables as lists of rows; honours rowspan/colspan; keeps the first link title per cell."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack, self.cell, self.skip = [], [], None, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and "wikitable" in (a.get("class") or ""):
            self.stack.append({"rows": [], "span": {}})
        elif not self.stack:
            return
        elif tag == "tr":
            self.stack[-1]["rows"].append([])
        elif tag in ("td", "th"):
            self.cell = {"text": "", "link": None, "rowspan": int(re.sub(r"\D", "", a.get("rowspan", "1")) or 1),
                         "colspan": int(re.sub(r"\D", "", a.get("colspan", "1")) or 1)}
        elif tag == "sup" or (tag == "style"):
            self.skip += 1
        elif tag == "a" and self.cell is not None and self.cell["link"] is None and a.get("title"):
            self.cell["link"] = a["title"]
        elif tag == "br" and self.cell is not None:
            self.cell["text"] += " "

    def handle_endtag(self, tag):
        if not self.stack:
            return
        if tag in ("sup", "style") and self.skip:
            self.skip -= 1
        elif tag in ("td", "th") and self.cell is not None:
            t = self.stack[-1]
            row = t["rows"][-1]
            col = len(row)
            while (len(t["rows"]) - 1, col) in t["span"]:
                row.append(t["span"][(len(t["rows"]) - 1, col)])
                col += 1
            c = {"text": re.sub(r"\s+", " ", self.cell["text"]).strip(), "link": self.cell["link"]}
            for k in range(self.cell["colspan"]):
                row.append(c)
                for r in range(1, self.cell["rowspan"]):
                    t["span"][(len(t["rows"]) - 1 + r, col + k)] = c
            self.cell = None
        elif tag == "tr":
            t = self.stack[-1]
            r = len(t["rows"]) - 1
            while (r, len(t["rows"][-1])) in t["span"]:
                t["rows"][-1].append(t["span"][(r, len(t["rows"][-1]))])
        elif tag == "table":
            self.tables.append(self.stack.pop()["rows"])

    def handle_data(self, data):
        if self.cell is not None and not self.skip:
            self.cell["text"] += data


def wiki_tables(name):
    doc = json.loads((RAW / name).read_text(encoding="utf-8"))["parse"]["text"]
    p = Tables()
    p.feed(doc)
    return p.tables


def build_aliases(geonames, ne):
    alias = {}
    for iso2, row in geonames.items():
        alias.setdefault(norm(row["Country"]), set()).add(iso2)
    for f in ne:
        p = f["properties"]
        code = p.get("ISO_A2_EH")
        if not code or code == "-99":
            continue
        for k in ("ADMIN", "NAME", "NAME_LONG", "FORMAL_EN", "NAME_SORT", "NAME_ALT", "BRK_NAME", "NAME_EN",
                  "NAME_CIAWF", "GEOUNIT", "SUBUNIT", "SOVEREIGNT"):
            v = p.get(k)
            if v and (k != "SOVEREIGNT" or p.get("TYPE") == "Sovereign country"):
                alias.setdefault(norm(v), set()).add(code)
    return {k: v for k, v in alias.items() if k and len(v) == 1}


def match(cell, alias):
    for cand in (cell["text"], cell["link"] or ""):
        n = norm(cand)
        if n in alias:
            return next(iter(alias[n]))
    return None


def main():
    src = json.loads((RAW / "sources.json").read_text(encoding="utf-8"))
    ne_version = (RAW / "natural_earth_VERSION.txt").read_text().strip()
    rows, unmatched = [], []

    # GeoNames countryInfo
    gi = src["countryInfo.txt"]
    header = None
    geonames = {}
    for line in (RAW / "countryInfo.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("#ISO\t"):
            header = line[1:].split("\t")
        elif line and not line.startswith("#") and header:
            vals = line.split("\t")
            geonames[vals[0]] = dict(zip(header, vals + [""] * (len(header) - len(vals))))

    def add(iso2, field, value, status="normal", note="", source="", url="", retrieved=""):
        if value in (None, ""):
            return
        rows.append({"iso2": iso2, "country": geonames.get(iso2, {}).get("Country", ""), "field": field,
                     "value": str(value), "status": status, "note": note, "source": source, "source_url": url,
                     "retrieved": retrieved[:10]})
    gsrc = "GeoNames countryInfo (CC BY 4.0)"
    for iso2, r in geonames.items():
        for field, col in (("iso3", "ISO3"), ("capital", "Capital"), ("continent", "Continent"), ("tld", "tld"),
                           ("currency_code", "CurrencyCode"), ("currency_name", "CurrencyName"),
                           ("calling_code", "Phone"), ("postal_code_format", "Postal Code Format"),
                           ("population", "Population"), ("area_km2", "Area(in sq km)")):
            add(iso2, field, r.get(col, "").strip(), source=gsrc, url=gi["url"], retrieved=gi["retrieved"])
        for lang in [x for x in r.get("Languages", "").split(",") if x]:
            add(iso2, "language", lang.split("-")[0].lower(), note=f"listed as {lang}", source=gsrc, url=gi["url"],
                retrieved=gi["retrieved"])
        for nb in [x for x in r.get("neighbours", "").split(",") if x]:
            add(iso2, "neighbour", nb, source=gsrc, url=gi["url"], retrieved=gi["retrieved"])

    # Natural Earth
    ne_feats = json.loads((RAW / "ne_50m_admin_0_countries.geojson").read_text(encoding="utf-8"))["features"]
    ne_src = src["ne_50m_admin_0_countries.geojson"]
    nsrc = f"Natural Earth 1:50m admin 0 countries, {ne_version} (public domain)"
    out_feats = []
    for f in ne_feats:
        p = f["properties"]
        code = p.get("ISO_A2_EH") if p.get("ISO_A2_EH") not in (None, "-99") else None
        out_feats.append({"type": "Feature", "geometry": f["geometry"],
                          "properties": {"iso2": code, "name": p.get("ADMIN"), "type": p.get("TYPE"),
                                         "continent": p.get("CONTINENT"), "subregion": p.get("SUBREGION")}})
        if code:
            add(code, "ne_continent", p.get("CONTINENT"), source=nsrc, url=ne_src["url"], retrieved=ne_src["retrieved"])
            add(code, "ne_subregion", p.get("SUBREGION"), source=nsrc, url=ne_src["url"], retrieved=ne_src["retrieved"])
    alias = build_aliases(geonames, ne_feats)

    # Unicode CLDR 49.0.0: scripts per territory (SOFT clue: facts.py never excludes on it)
    craw = ROOT / "tools" / "raw" / "cldr"
    csrc = json.loads((craw / "sources.json").read_text(encoding="utf-8"))
    ti = json.loads((craw / "territoryInfo.json").read_text(encoding="utf-8"))["supplemental"]["territoryInfo"]
    ld = json.loads((craw / "languageData.json").read_text(encoding="utf-8"))["supplemental"]["languageData"]
    ls = json.loads((craw / "likelySubtags.json").read_text(encoding="utf-8"))["supplemental"]["likelySubtags"]
    cver = csrc["_version"]
    threshold = (f"threshold: a language with CLDR status official or de_facto_official, or spoken by >= "
                 f"{SCRIPT_MIN_PERCENT:g} % of the population")
    t_src = f"Unicode CLDR {cver} territoryInfo + languageData (Unicode License V3)"
    l_src = f"Unicode CLDR {cver} likelySubtags (Unicode License V3)"
    for terr, info in ti.items():
        if not re.fullmatch(r"[A-Z]{2}", terr):
            continue
        if terr not in geonames:
            unmatched.append({"source": "cldr_script", "name": terr, "link": "", "value": "",
                              "reason": "CLDR territory not in GeoNames countryInfo"})
            continue
        per_script = {}
        for lang, d in (info.get("languagePopulation") or {}).items():
            pct = float(d.get("_populationPercent", 0) or 0)
            status = d.get("_officialStatus", "")
            if not (status in ("official", "de_facto_official") or pct >= SCRIPT_MIN_PERCENT):
                continue
            parts = re.split(r"[_-]", lang)
            if len(parts) > 1 and re.fullmatch(r"[A-Z][a-z]{3}", parts[1]):
                scripts_ = [parts[1]]
            else:
                scripts_ = (ld.get(parts[0]) or {}).get("_scripts") or []
            if not scripts_:
                unmatched.append({"source": "cldr_script", "name": f"{terr}/{lang}", "link": "",
                                  "value": "", "reason": "language has no primary script in languageData"})
            for sc in scripts_:
                per_script.setdefault(sc, []).append(f"{lang} {pct:g}%" + (f" {status}" if status else ""))
        likely = ls.get(f"und-{terr}", "")
        lparts = likely.split("-")
        main = lparts[1] if len(lparts) > 2 and re.fullmatch(r"[A-Z][a-z]{3}", lparts[1]) else ""
        for sc, langs in sorted(per_script.items()):
            note = f"languages: {', '.join(langs)}; {threshold}" + (
                f"; main script by likelySubtags: und-{terr} -> {likely}" if sc == main else "")
            add(terr, "script", sc, note=note, source=t_src, url=csrc["territoryInfo.json"]["url"],
                retrieved=csrc["territoryInfo.json"]["retrieved"])
        if main and main not in per_script:
            add(terr, "script", main, note=f"main script by likelySubtags: und-{terr} -> {likely}; {threshold} "
                                           "(no language passed it)",
                source=l_src, url=csrc["likelySubtags.json"]["url"], retrieved=csrc["likelySubtags.json"]["retrieved"])

    # Wikipedia: driving side
    w = src["wiki_driving_side.json"]
    wsrc = f"Wikipedia: {w['title']} (CC BY-SA 4.0), revision {w['revid']}"
    table = wiki_tables("wiki_driving_side.json")[0]
    head = [c["text"] for c in table[0]]
    i_side, i_note = head.index("Road traffic"), next(i for i, h in enumerate(head) if h.startswith("Notes"))
    # The header's "Country" cell spans two columns: ordinary rows fill both with one cell (colspan=2);
    # a country with parts (UK, US, ...) has a row-spanning country cell followed by a part cell.
    for r in table[1:]:
        if len(r) != len(head):
            continue
        side_raw, note = r[i_side]["text"], r[i_note]["text"]
        code, sub = match(r[0], alias), ""
        if r[1] is not r[0]:
            sub = r[1]["text"]
            sub_code = match(r[1], alias)
            if sub_code:
                code, sub = sub_code, ""  # the sub-entity has its own ISO code (e.g. Gibraltar, Guam)
            elif not code:
                unmatched.append({"source": "driving_side", "name": f"{r[0]['text']} / {sub}", "link": r[1]["link"],
                                  "value": side_raw})
                continue
            # otherwise: a part of the parent with no own code (e.g. Alaska): kept under the parent, noted
        if not code:
            unmatched.append({"source": "driving_side", "name": r[0]["text"], "link": r[0]["link"], "value": side_raw})
            continue
        if sub:
            note = f"[part: {sub}] " + note
        sides = sorted(set(re.findall(r"\b(LHT|RHT)\b", side_raw)))
        if not sides:
            unmatched.append({"source": "driving_side", "name": r[0]["text"], "link": r[0]["link"], "value": side_raw,
                              "reason": "no LHT/RHT value"})
            continue
        other = {"LHT": "RHT", "RHT": "LHT"}
        exc = len(sides) > 1 or any(re.search(rf"\b{other[s]}\b|left|right", note, re.I) for s in sides) \
            or re.search(r"\bexcept|\bexception", note, re.I)
        for s in sides:
            add(code, "driving_side", "left" if s == "LHT" else "right", "exception" if exc else "normal",
                f"source cell: {side_raw}" + (f"; notes: {note}" if note else ""), wsrc, w["url"], w["retrieved"])

    # Wikipedia: mains electricity
    w = src["wiki_mains_electricity.json"]
    wsrc = f"Wikipedia: {w['title']} (CC BY-SA 4.0), revision {w['revid']}"
    table = wiki_tables("wiki_mains_electricity.json")[0]
    head = [c["text"] for c in table[0]]
    ip, iv = head.index("Plug type"), head.index("Nominal supply voltage")
    ifq, inote = head.index("Supply frequency"), head.index("Notes")
    for r in table[1:]:
        if len(r) <= inote:
            continue
        code = match(r[0], alias)
        if not code:
            unmatched.append({"source": "mains", "name": r[0]["text"], "link": r[0]["link"], "value": r[ip]["text"]})
            continue
        note = r[inote]["text"]
        plugs = re.findall(r"\b([A-N])\b", r[ip]["text"])
        freqs = sorted(set(re.findall(r"\b(50|60)\s*Hz", r[ifq]["text"])))
        volts = r[iv]["text"]
        regional = bool(re.search(r"some|region|area|part|older|except|also|mix|varies|various", note, re.I))
        for pl in plugs:
            add(code, "plug_type", pl, "exception" if regional else "normal",
                f"source cell: {r[ip]['text']}" + (f"; notes: {note}" if note else ""), wsrc, w["url"], w["retrieved"])
        for fq in freqs:
            add(code, "mains_frequency_hz", fq, "exception" if (len(freqs) > 1 or regional) else "normal",
                f"source cell: {r[ifq]['text']}", wsrc, w["url"], w["retrieved"])
        if volts:
            add(code, "mains_voltage", volts, "exception" if regional else "normal",
                (f"notes: {note}" if note else ""), wsrc, w["url"], w["retrieved"])

    OUT.mkdir(exist_ok=True)
    rows.sort(key=lambda r: (r["iso2"], r["field"], r["value"]))
    with (OUT / "country_facts.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=COLS)
        wr.writeheader()
        wr.writerows(rows)
    (OUT / "countries_ne50m.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": out_feats}),
                                                 encoding="utf-8")
    with (OUT / "unmatched.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=["source", "name", "link", "value", "reason"])
        wr.writeheader()
        wr.writerows(unmatched)
    meta = {k: v for k, v in src.items()}
    meta["natural_earth_VERSION.txt"]["value"] = ne_version
    meta["cldr"] = csrc
    meta["_licences"] = {"Unicode CLDR": "Unicode License V3, Copyright Unicode, Inc.; notice in facts/LICENSE-CLDR.txt",
                         "GeoNames": "CC BY 4.0, credit GeoNames (geonames.org)",
                         "Wikipedia": "CC BY-SA 4.0, credit Wikipedia contributors; derived table shared alike",
                         "Natural Earth": "public domain (naturalearthdata.com)"}
    (OUT / "SOURCES.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    by_field = {}
    for r in rows:
        by_field.setdefault(r["field"], [0, 0])[0] += 1
        by_field[r["field"]][1] += r["status"] == "exception"
    print(f"{len(rows)} rows, {len({r['iso2'] for r in rows})} countries; unmatched source rows: {len(unmatched)}")
    for k, (n, e) in sorted(by_field.items()):
        print(f"  {k:<20} {n:>5} rows  {e:>4} exceptions")


if __name__ == "__main__":
    main()
