"""Rule-based tools for text the analyst types in from a photo. No AI, no OCR, no network.

  scripts     which writing systems the characters belong to (Unicode Scripts.txt, with
              ScriptExtensions.txt for characters shared by several scripts), after NFC normalisation
  phones      phone numbers found by phonenumbers.PhoneNumberMatcher (leniency VALID)
  domains     host names and their top-level domain, looked up in the country fact table
  currencies  ISO 4217 codes looked up in the country fact table; currency symbols named
Every country association printed comes from a sourced fact-table row; anything without a row is
"unknown", never a guess. Suggested `facts` clues are printed, never run.
"""
from __future__ import annotations

import bisect
import json
import re
import unicodedata
from collections import Counter, defaultdict
from functools import cache
from pathlib import Path

from . import facts, paths

ROOT = Path(__file__).resolve().parent.parent
SHARED = {"Common", "Inherited"}


def unicode_dir():
    return paths.data_dir("unicode")


class TextError(ValueError):
    pass


def _ranges(path):
    """Parse a UCD 'code..code ; value # comment' file into sorted (start, end, value)."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        cp, val = (x.strip() for x in line.split(";", 1))
        a, _, b = cp.partition("..")
        out.append((int(a, 16), int(b or a, 16), val))
    out.sort()
    return out


@cache
def ucd(folder=None):
    d = Path(folder or unicode_dir())
    if not (d / "Scripts.txt").exists():
        raise TextError(f"Unicode data not installed in {d}")
    meta = json.loads((d / "SOURCES.json").read_text(encoding="utf-8"))
    scripts = _ranges(d / "Scripts.txt")
    ext = _ranges(d / "ScriptExtensions.txt")
    short_to_long = {}
    for line in (d / "PropertyValueAliases.txt").read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.split("#", 1)[0].split(";")]
        if len(parts) >= 3 and parts[0] == "sc":
            short_to_long[parts[1]] = parts[2]
    return {"meta": meta, "scripts": scripts, "starts": [r[0] for r in scripts], "ext": ext,
            "ext_starts": [r[0] for r in ext], "names": short_to_long,
            "codes": {v: k for k, v in short_to_long.items()}}


def _lookup(table, starts, cp):
    i = bisect.bisect_right(starts, cp) - 1
    if i >= 0 and table[i][0] <= cp <= table[i][1]:
        return table[i][2]
    return None


def scripts(text, folder=None):
    u = ucd(str(folder or unicode_dir()))
    s = unicodedata.normalize("NFC", text)
    counts, examples, shared = Counter(), defaultdict(list), []
    unassigned = common = 0
    for ch in s:
        if ch.isspace():
            continue
        cp = ord(ch)
        sc = _lookup(u["scripts"], u["starts"], cp) or "Unknown"
        if sc == "Unknown":
            unassigned += 1
            continue
        if sc in SHARED:
            ext = _lookup(u["ext"], u["ext_starts"], cp)
            if ext:
                shared.append((ch, [u["names"].get(c, c) for c in ext.split()]))
            else:
                common += 1
            continue
        counts[sc] += 1
        if ch not in examples[sc] and len(examples[sc]) < 8:
            examples[sc].append(ch)
    # a shared character goes to a listed script already present in the text; otherwise it stays shared
    unresolved = []
    for ch, cands in shared:
        present = [c for c in cands if counts[c]]
        if present:
            counts[max(present, key=lambda c: counts[c])] += 1
        else:
            unresolved.append({"char": ch, "codepoint": f"U+{ord(ch):04X}", "scripts": cands})
    total = sum(counts.values())
    meta = u["meta"]
    return {
        "unicode_version": meta["unicode_version"],
        "source": "Unicode Character Database: Scripts.txt, ScriptExtensions.txt, PropertyValueAliases.txt",
        "source_url": meta["files"]["Scripts.txt"]["url"], "retrieved": meta["files"]["Scripts.txt"]["retrieved"][:10],
        "normalisation": "NFC",
        "counts": [{"script": sc, "code": u["codes"].get(sc, ""), "chars": n, "share": round(n / total, 3),
                    "examples": "".join(examples[sc])} for sc, n in counts.most_common()],
        "common_or_inherited": common, "shared_unresolved": unresolved, "unassigned": unassigned,
    }


# ------------------------------------------------------------------ phone numbers

def phones(text, region=None, all_regions=False):
    if len(re.findall(r"\d", text)) < 5:  # no phone number possible: skip loading the phone library
        import importlib.metadata as md
        return {"source": f"phonenumbers {md.version('phonenumbers')} (port of Google libphonenumber metadata)",
                "matcher": "PhoneNumberMatcher, leniency VALID (only numbers valid for their region are reported)",
                "default_region": region, "numbers": []} | (
            {"national_number_regions": [], "all_regions_note": "no digits that could form a phone number"}
            if all_regions else {})
    import phonenumbers
    from phonenumbers import carrier, geocoder, timezone

    def describe(num, raw):
        valid = phonenumbers.is_valid_number(num)
        return {"raw": raw, "e164": phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164),
                "international": phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
                "valid": valid, "possible": phonenumbers.is_possible_number(num),
                "country_calling_code": num.country_code,
                "region_codes": list(phonenumbers.region_codes_for_country_code(num.country_code)),
                "region_of_number": phonenumbers.region_code_for_number(num),
                "type": _type_name(phonenumbers, phonenumbers.number_type(num)),
                "area_description": geocoder.description_for_number(num, "en") or None,
                "time_zones": list(timezone.time_zones_for_number(num)),
                "carrier_at_allocation": carrier.name_for_number(num, "en") or None}
    leniency = phonenumbers.Leniency.VALID
    found, seen = [], set()
    for m in phonenumbers.PhoneNumberMatcher(text, region, leniency=leniency):
        key = phonenumbers.format_number(m.number, phonenumbers.PhoneNumberFormat.E164)
        if key not in seen:
            seen.add(key)
            found.append(describe(m.number, m.raw_string))
    out = {"source": f"phonenumbers {phonenumbers.__version__} (port of Google libphonenumber metadata)",
           "matcher": "PhoneNumberMatcher, leniency VALID (only numbers valid for their region are reported)",
           "default_region": region, "numbers": found}
    if all_regions:
        regs = defaultdict(list)
        for reg in sorted(phonenumbers.SUPPORTED_REGIONS):
            for m in phonenumbers.PhoneNumberMatcher(text, reg, leniency=leniency):
                if m.raw_string.lstrip().startswith("+"):
                    continue  # already international: not region-dependent
                regs[m.raw_string].append({"region": reg, "e164": phonenumbers.format_number(
                    m.number, phonenumbers.PhoneNumberFormat.E164)})
        out["national_number_regions"] = [{"raw": raw, "valid_in": lst} for raw, lst in regs.items()]
        out["all_regions_note"] = ("Local only: the text was parsed against the phonenumbers rules of every "
                                   f"supported region ({len(phonenumbers.SUPPORTED_REGIONS)}) on this machine; "
                                   "nothing was sent anywhere.")
    return out


def _type_name(phonenumbers, t):
    for k, v in vars(phonenumbers.PhoneNumberType).items():
        if v == t and k.isupper():
            return k
    return str(t)


# ------------------------------------------------------------------ domains and currencies

_HOST = re.compile(r"(?<![\w@.-])((?:[^\W_](?:[\w-]*[^\W_])?\.)+([^\W\d_][\w-]*[^\W_]|xn--[a-z0-9-]+))(?![\w.-])", re.UNICODE)


def _fact_index(field):
    table, names = facts.load()
    idx = defaultdict(list)
    for iso2, rows in table.items():
        for r in rows:
            if r["field"] == field:
                idx[r["value"].lower()].append({"iso2": iso2, "country": names.get(iso2, ""), "value": r["value"],
                                                "status": r["status"], "source": r["source"],
                                                "source_url": r["source_url"], "retrieved": r["retrieved"]})
    return idx


def domains(text):
    idx = _fact_index("tld")
    out = []
    for m in _HOST.finditer(unicodedata.normalize("NFC", text)):
        host, tld = m.group(1), m.group(2)
        try:
            puny = "xn--" + tld.encode("punycode").decode("ascii") if not tld.isascii() else tld.lower()
        except UnicodeError:
            puny = None
        rows = idx.get("." + tld.lower(), []) + (idx.get("." + puny, []) if puny and puny != tld.lower() else [])
        out.append({"text": host, "tld_raw": "." + tld, "tld_ascii": "." + puny if puny else None,
                    "non_ascii": not tld.isascii(), "countries": rows,
                    "result": "match" if rows else "unknown",
                    **({} if rows else {"note": "no fact-table row for this TLD (generic or internationalised TLDs "
                                                "are not in the table): unknown, not a country clue"})})
    return out


def currencies(text):
    idx = _fact_index("currency_code")
    out = []
    for code in sorted(set(re.findall(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])", text))):
        rows = idx.get(code.lower(), [])
        if rows:
            out.append({"code": code, "countries": rows})
    symbols = sorted({ch for ch in text if unicodedata.category(ch) == "Sc"})
    return out, [{"symbol": ch, "name": unicodedata.name(ch, "?"), "codepoint": f"U+{ord(ch):04X}",
                  "note": "symbol named from Python's Unicode data; no country mapping"} for ch in symbols]


def analyse(text, region=None, all_regions=False):
    sc = scripts(text)
    ph = phones(text, region=region, all_regions=all_regions)
    dom = domains(text)
    cur, sym = currencies(text)
    clues = []
    for d in dom:
        if d["result"] == "match":
            clues.append(f"--tld {d['tld_ascii']}")
    for c in cur:
        clues.append(f"--currency {c['code']}")
    for n in ph["numbers"]:
        clues.append(f"--calling-code {n['country_calling_code']}")
    for c in sc.get("counts", []):
        clues.append(f"--script {c['code']}")
    notes = ["Suggested facts clues are printed, never run. --script is a SOFT clue (Unicode CLDR): match or "
             "unknown only, it never excludes a country.",
             "Script counting: Common/Inherited characters are not counted. A character shared by several scripts "
             "(ScriptExtensions.txt) is credited to the listed script with the most characters already in the text "
             "(this tie-break is the toolkit's rule, not Unicode's); if none of its scripts is present it is "
             "reported as shared_unresolved."]
    if all_regions:
        notes.append(ph["all_regions_note"])
    if region:
        notes.append(f"Numbers without a + were read as numbers of region {region} (given by the analyst).")
    return {"input": text, "scripts": sc, "phones": ph, "domains": dom, "currencies": cur, "currency_symbols": sym,
            "facts_clues": sorted(set(clues)), "notes": notes}
