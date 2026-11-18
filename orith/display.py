"""Human-readable output: headings, aligned tables, a verdict banner, plain ANSI colours.

Colours are used only when stdout is a terminal, NO_COLOR is not set, TERM is not "dumb" and --no-color
was not given. Text is plain UTF-8; with --ascii (or on a legacy Windows console without Windows
Terminal) every non-ASCII character is shown as "?" so lines never scramble. Right-to-left text
(Arabic, Hebrew) is wrapped in Unicode isolation marks so it does not reorder the rest of the line.
The JSON output (--json) never goes through this module.
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata

RTL = ((0x0590, 0x08FF), (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF))
# C0/C1 controls (ESC starts terminal escape sequences) and bidi embedding/override/isolate characters
UNSAFE = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f‪-‮⁦-⁩‎‏؜]")
CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34", "cyan": "36"}
WIDTH = 100


class Style:
    def __init__(self, color=False, ascii_only=False):
        self.color, self.ascii_only = color, ascii_only
        self.replaced = 0

    def c(self, text, *names):
        text = str(text)
        if not self.color or not names:
            return text
        return "\x1b[" + ";".join(CODES[n] for n in names) + "m" + text + "\x1b[0m"

    def t(self, text):
        """Make a value safe to print: control and bidi-override characters removed (metadata is untrusted: an
        escape sequence in an EXIF field must not reach the terminal), then ASCII fallback or RTL isolation."""
        s = UNSAFE.sub(" ", str(text))
        if self.ascii_only:
            out = []
            for ch in s:
                if ord(ch) < 128:
                    out.append(ch)
                    continue
                base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
                out.append(base or "?")
                self.replaced += 0 if base else 1
            return "".join(out)
        if any(lo <= ord(ch) <= hi for ch in s for lo, hi in RTL):
            return "⁨" + s + "⁩"
        return s


def make_style(no_color=False, force_ascii=False, stream=None):
    stream = stream or sys.stdout
    tty = bool(getattr(stream, "isatty", lambda: False)())
    color = (tty and not no_color and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb")
    legacy_console = (os.name == "nt" and tty and not os.environ.get("WT_SESSION")
                      and os.environ.get("TERM_PROGRAM") not in ("vscode",))
    ascii_only = force_ascii or bool(os.environ.get("ORITH_ASCII")) or legacy_console
    if color and os.name == "nt":
        color = _enable_vt()
    return Style(color=color, ascii_only=ascii_only)


def _enable_vt():
    """Turn on ANSI processing in a Windows console (Windows 10+). False if it cannot."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(h, mode.value | 0x0004))
    except Exception:  # noqa: BLE001 - no console API: no colour
        return False


# ------------------------------------------------------------------------------- building blocks

def heading(st, title):
    return "\n" + st.c(title.upper(), "bold", "cyan") + "\n" + st.c("-" * min(len(title), WIDTH), "dim")


def kv(st, rows, indent=2):
    rows = [(k, v) for k, v in rows if v not in (None, "", [], {})]
    if not rows:
        return []
    w = max(len(str(k)) for k, _ in rows)
    return [" " * indent + st.c(f"{str(k):<{w}}", "dim") + "  " + st.t(_fmt(v)) for k, v in rows]


def table(st, header, rows, indent=2, limit=None):
    rows = [[st.t(_fmt(x)) for x in r] for r in rows]
    shown = rows if limit is None else rows[:limit]
    widths = [max([len(h)] + [len(r[i]) for r in shown]) for i, h in enumerate(header)]
    widths = [min(w, 48) for w in widths]
    pad = " " * indent
    out = [pad + st.c("  ".join(f"{h:<{w}}" for h, w in zip(header, widths)).rstrip(), "bold")]
    out.append(pad + st.c("  ".join("-" * w for w in widths), "dim"))
    for r in shown:
        out.append(pad + "  ".join(f"{_clip(x, w):<{w}}" for x, w in zip(r, widths)).rstrip())
    if limit is not None and len(rows) > limit:
        out.append(pad + st.c(f"... {len(rows) - limit} more (use --json for all)", "dim"))
    return out


def bullets(st, items, indent=2, mark="-"):
    return [" " * indent + mark + " " + st.t(i) for i in items if i]


def banner(st, verdict, reasons):
    ok = verdict == "IDENTIFIED"
    title = (" IDENTIFIED - every condition of the ledger rule holds " if ok else
             " CANDIDATE - not an identification ")
    color = "green" if ok else "yellow"
    line = "=" * max(len(title) + 2, 60)
    out = [st.c(line, color), st.c(title, "bold", color)]
    out += ["   " + st.t(r) for r in reasons]
    out.append(st.c(line, color))
    return out


def _clip(s, w):
    return s if len(s) <= w else s[: max(1, w - 3)] + "..."


def _fmt(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.6f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.1f}"
    if isinstance(v, (list, tuple)) and all(not isinstance(x, (dict, list)) for x in v):
        return ", ".join(_fmt(x) for x in v)
    return str(v)


def deg(v):
    return "-" if v is None else f"{v:.1f} deg"


def km(v):
    return "-" if v is None else (f"{v:.2f} km" if v < 10 else f"{v:.0f} km")


def meters(v):
    return "-" if v is None else (f"{v:.0f} m" if v < 10000 else f"{v / 1000:.0f} km")


# ------------------------------------------------------------------------------- generic renderer

def generic(st, obj, indent=0, depth=0):
    """Any JSON-like object as indented text: scalars aligned, small lists of dicts as tables."""
    pad = " " * indent
    lines = []
    if isinstance(obj, dict):
        scal = [(k, v) for k, v in obj.items() if not isinstance(v, (dict, list)) or _flat_list(v)]
        lines += kv(st, scal, indent)
        for k, v in obj.items():
            if (k, v) in scal or v in ([], {}, None):
                continue
            lines.append(pad + st.c(str(k), "bold"))
            lines += generic(st, v, indent + 2, depth + 1)
    elif isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            keys = []
            for x in obj:
                keys += [k for k, v in x.items() if k not in keys and not isinstance(v, (dict, list))]
            if keys and len(keys) <= 7:
                lines += table(st, keys, [[x.get(k, "") for k in keys] for x in obj], indent, limit=40)
            else:
                for i, x in enumerate(obj[:40]):
                    lines.append(pad + st.c(f"[{i + 1}]", "dim"))
                    lines += generic(st, x, indent + 2, depth + 1)
                if len(obj) > 40:
                    lines.append(pad + st.c(f"... {len(obj) - 40} more (use --json for all)", "dim"))
        else:
            lines += bullets(st, [_fmt(x) for x in obj[:60]], indent)
    else:
        lines.append(pad + st.t(_fmt(obj)))
    return lines


def _flat_list(v):
    return isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v) and len(v) <= 12


# ------------------------------------------------------------------------------- per-command views

def render(cmd, obj, st, brief=False):
    if isinstance(obj, dict) and set(obj) == {"error"}:
        return st.c("error: ", "bold", "red") + st.t(obj["error"])
    if cmd == "scan":
        from . import scan
        lines = scan.render(st, obj, brief=brief)
    else:
        fn = VIEWS.get(cmd)
        lines = fn(st, obj) if fn else generic(st, obj)
    if st.ascii_only and st.replaced:
        lines.append(st.c(f"({st.replaced} non-ASCII character(s) shown as '?'; use a UTF-8 terminal or --json)", "dim"))
    return "\n".join(lines).lstrip("\n")


def v_report(st, o):
    text = o.get("text", "").splitlines()
    reasons = o.get("unmet") or ["all conditions of the rule hold"]
    lines = ([st.t(text[0])] if text else []) + banner(st, o.get("verdict"), reasons)
    in_banner = {"  - " + u for u in o.get("unmet", [])}
    for ln in text[1:]:  # the verdict and its reasons are already in the banner
        if ln.startswith("VERDICT:") or ln.startswith("This is a candidate") or ln in in_banner:
            continue
        lines.append(st.t(ln))
    return lines


def v_rank(st, o):
    lines = banner(st, o["verdict"], o.get("unmet") or ["all conditions of the rule hold"])
    lines.append(heading(st, "Hypotheses (ranked)"))
    lines += _hyp_table(st, o["ranking"])
    lines.append(st.c("  relative score orders this case only; it is not a probability", "dim"))
    if o.get("warnings"):
        lines.append(heading(st, "Warnings"))
        lines += bullets(st, o["warnings"])
    return lines


def _hyp_table(st, ranking):
    rows = []
    for r in ranking:
        state = "ELIMINATED " + ",".join(r["eliminated_by"]) if r["status"] == "eliminated" else "alive"
        score = r.get("relative_score", r.get("score"))
        rows.append([r["id"], r["label"], f"{r['lat']:.5f}, {r['lon']:.5f}", state, f"{score:+.3f}",
                     r.get("map_anchored_clues", "-"), r["independent_agreeing"], len(r["contradicts"])])
    return table(st, ["id", "label", "lat, lon", "status", "rel.score", "anchored", "agreeing", "contra"], rows)


def v_doctor(st, o):
    lines = [st.c(" OK " if o["ok"] else " PROBLEMS ", "bold", "green" if o["ok"] else "red") + "  " + st.t(o["statement"])]
    lines += kv(st, [("version", o["version"]), ("python", o["python"]), ("platform", o["platform"]),
                     ("virtualenv", o["in_virtualenv"]), ("cases", o["cases_dir"]), ("cache", o["cache_dir"]),
                     ("config", o["config_file"])])
    lines.append(heading(st, "Data"))
    lines += table(st, ["data", "present", "path"], [[k, v["present"], v["path"]] for k, v in o["data"].items()])
    lines += table(st, ["pack", "name", "licences"], [[p["code"], p["name"], ", ".join(p["licences"])] for p in o["packs"]])
    b = o.get("buildings") or {}
    lines += kv(st, [("building tiles", ", ".join(b.get("tiles") or []) or "none (optional: tools/fetch_gba.py)")])
    for title, items in (("Failures", o["failures"]), ("Warnings", o["warnings"])):
        if items:
            lines.append(heading(st, title))
            for f in items:
                lines += bullets(st, [f.get("check") or f.get("note")])
                lines += bullets(st, f.get("items") if isinstance(f.get("items"), list) else
                                 [f"{k}: {v}" for k, v in (f.get("items") or {}).items()], indent=4)
    return lines


def v_facts(st, o):
    if "facts" in o and "iso2" in o:  # --country
        lines = [st.c(f"{o['iso2']}  {o['country']}", "bold")]
        rows = [[field, f["value"], f["status"], f["source"].split(",")[0], f["retrieved"]]
                for field, fs in o["facts"].items() for f in fs]
        return lines + table(st, ["field", "value", "status", "source", "retrieved"], rows)
    lines = kv(st, [("clues", o["clues"]), ("consistent", o["counts"]["consistent"]),
                    ("excluded", o["counts"]["excluded"]), ("total", o["counts"]["total"])])
    lines.append(heading(st, "Consistent countries"))
    rows = [[c["iso2"], c["country"], c["matches"], c["unknown"], c["uncertain"]] for c in o["consistent"]]
    lines += table(st, ["iso2", "country", "match", "unknown", "uncertain"], rows, limit=30)
    ex = o.get("excluded") or []
    if ex:
        lines.append(heading(st, "Excluded (first 10, with the fact that excluded them)"))
        for e in ex[:10]:
            why = e["excluded_by"] if isinstance(e["excluded_by"], str) else "; ".join(
                f"{w['clue']}: " + ", ".join(sorted({f['value'] for f in w['facts']})) for w in e["excluded_by"])
            lines.append(f"  {e['iso2']}  {st.t(e['country'])}: {st.t(why)}")
    lines.append(st.c("  " + o["note"], "dim"))
    return lines


def v_text(st, o):
    lines = kv(st, [("input", o["input"])])
    sc = o["scripts"]
    lines.append(heading(st, "Scripts (Unicode " + sc["unicode_version"] + ")"))
    lines += table(st, ["script", "code", "chars", "share"], [[c["script"], c["code"], c["chars"], f"{c['share']:.0%}"]
                                                             for c in sc["counts"]])
    nums = o["phones"]["numbers"]
    if nums:
        lines.append(heading(st, "Phone numbers (valid for their region)"))
        lines += table(st, ["number", "region", "type", "area"],
                       [[n["e164"], n["region_of_number"], n["type"], n.get("area_description") or ""] for n in nums])
    if o["domains"]:
        lines.append(heading(st, "Domains"))
        lines += generic(st, o["domains"], 2)
    if o["currencies"]:
        lines.append(heading(st, "Currencies"))
        lines += table(st, ["code", "countries"], [[c["code"], len(c["countries"])] for c in o["currencies"]])
    if o["facts_clues"]:
        lines.append(heading(st, "Suggested facts clues (not run)"))
        lines += bullets(st, ["orith tools facts " + " ".join(o["facts_clues"])])
    return lines


def v_results(st, o):
    """geocode and osm."""
    lines = []
    if o.get("error"):
        lines += [st.c("error: ", "bold", "red") + st.t(o["error"])]
        lines += kv(st, [("meaning", o.get("meaning")), ("do by hand", o.get("do_by_hand")),
                         ("Overpass turbo", o.get("overpass_turbo_with_query"))])
        return lines
    lines += kv(st, [("query", o.get("query") if isinstance(o.get("query"), str) and len(o.get("query", "")) < 90 else None),
                     ("note", o.get("note"))])
    res = o.get("results") or []
    if res:
        lines += table(st, ["evidence", "name", "lat", "lon", "precision", "match"],
                       [[r.get("evidence", "-"), r.get("name", ""), f"{r['lat']:.5f}", f"{r['lon']:.5f}",
                         meters(r.get("precision_m")), r.get("match", "")] for r in res], limit=30)
    if o.get("next"):
        lines.append(heading(st, "Next"))
        lines += bullets(st, o["next"])
    for k in ("evidence", "fields", "reminder"):
        if isinstance(o.get(k), str):
            lines.append(st.c("  " + o[k], "dim"))
    return lines


def v_added(st, o):
    item = o.get("clue") or o.get("hypothesis")
    lines = []
    if "clue" in o:
        c = o["clue"]
        lines.append(f"  added clue {c['id']}  [{_kind(st, c)}]  u{c['uniqueness']} p{c['permanence']} r{c['reliability']}"
                     f"  weight {o['weight']}  group {c['group']}" + (f"  evidence {c['evidence']}" if c.get("evidence") else ""))
    elif item:
        lines.append(f"  added hypothesis {item['id']}  {st.t(item['label'])}  ({item['lat']:.5f}, {item['lon']:.5f})"
                     f"  from {item['source']}")
    for l in o.get("links", []) or []:
        lines.append(f"    link {l['clue']} {l['relation']} {l['hypothesis']}")
    if "link" in o:
        l = o["link"]
        lines.append(f"  link {l['clue']} {l['relation']} {l['hypothesis']}")
    return lines


def _kind(st, c):
    return st.c("HARD", "bold", "red") if c["kind"] == "HARD" else st.c("soft", "dim")


def v_sun(st, o):
    if "sun_azimuth" in o:
        return kv(st, [("place", f"{o['lat']}, {o['lon']}"), ("time (UTC)", o["time_utc"]),
                       ("sun azimuth", deg(o["sun_azimuth"])), ("sun elevation", deg(o["sun_elevation_apparent"])),
                       ("shadow bearing", deg(o.get("shadow_bearing"))),
                       ("shadow length per unit height", o.get("shadow_length_per_unit_height")),
                       ("note", o.get("note"))])
    return generic(st, o)


def v_buildings(st, o):
    if "buildings_dir" in o:  # --list
        lines = kv(st, [("folder", o["buildings_dir"])])
        if o["tiles"]:
            lines += table(st, ["tile", "bbox S,W,N,E", "buildings located", "without position", "licence"],
                           [[t["tile"], ",".join(map(str, t["bbox"])), f"{t['located']:,}",
                             f"{t['no_position_in_id']:,}", t["licence"]] for t in o["tiles"]])
        lines.append(st.c("  " + o["note"], "dim"))
        return lines
    if "tile" in o and "counts" in o:  # --index
        return kv(st, [("built", o["tile"]), ("located", f"{o['counts']['located']:,}"),
                       ("without position", f"{o['counts']['no_position_in_id']:,}"), ("licence", o["licence"])])
    if not o.get("covered"):
        return [st.c("[-] ", "bold") + "no installed building tile covers this point "
                "(install: python tools/fetch_gba.py --bbox S,W,N,E)"]
    h = o.get("height_m") or {}
    lines = kv(st, [("buildings", f"{o['count']:,} within {o['radius_m']:.0f} m ({o['per_km2']:,} per km2)"),
                    ("nearest", meters(o["nearest_m"])),
                    ("height (ML)", f"median {h['median']} m, max {h['max']} m" if h else None),
                    ("looks like", o.get("setting_by_rule")), ("tiles", o["tiles"])])
    lines.append(st.c("  " + o["meaning"], "dim"))
    return lines


def v_batch(st, o):
    c = o["counts"]
    lines = [st.c(f"orith folder scan  {o['folder']}", "bold"),
             f"  {c['images']} image(s): {c['with_gps']} with GPS, {c['with_time']} with a time, "
             f"{c['ai_declared']} declared AI, {c['with_problems']} with problems, "
             f"{c['duplicate_groups']} duplicate group(s)" + (f"; {c['errors']} file(s) skipped" if c["errors"] else "")]
    lines.append(heading(st, "Images"))
    rows = []
    for r in o["images"]:
        if r.get("error"):
            rows.append([r["file"], "-", "-", "-", "skipped: " + r["error"][:48], "-"])
            continue
        gps = f"{r['gps'][0]:.5f},{r['gps'][1]:.5f} {r.get('country') or ''}" if r.get("gps") else "-"
        rows.append([r["file"], gps, r.get("time_local") or "-", r.get("camera") or "-",
                     ", ".join(r.get("flags") or []) or "-", r.get("duplicate_group") or "-"])
    lines += table(st, ["file", "GPS", "time (local)", "camera", "flags", "dup"], rows, limit=200)
    if o["duplicates"]:
        lines.append(heading(st, "Near-duplicates (same picture)"))
        lines += bullets(st, [f"group {i}: " + ", ".join(g) for i, g in enumerate(o["duplicates"], 1)])
    if len(o["timeline"]) > 1:
        lines.append(heading(st, "Timeline"))
        lines += bullets(st, [f"{t['time']}  {t['file']}  ({t['from']})" for t in o["timeline"][:60]])
    probs = [(r["file"], ch) for r in o["images"] for ch in r.get("checks", []) if ch["result"] in ("mismatch", "warning")]
    if probs:
        lines.append(heading(st, "Problems"))
        lines += bullets(st, [f"{f}: {ch['check']}: {ch['detail']}" for f, ch in probs[:60]])
    if o.get("geojson"):
        lines.append(f"  GPS points written to {o['geojson']}")
    lines.append(st.c("  " + o["note"], "dim"))
    return lines


VIEWS = {"batch": v_batch, "buildings": v_buildings, "report": v_report, "rank": v_rank, "doctor": v_doctor,
         "facts": v_facts, "text": v_text,
         "geocode": v_results, "osm": v_results, "clue": v_added, "hyp": v_added, "link": v_added,
         "sun": v_sun}
