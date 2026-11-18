"""Case timer and the guided checklist. Both write next to the case ledger (cases/<id>/), never
into it: the ledger format and the verdict rule are unchanged; the checklist adds clues,
hypotheses and links only through the ledger's own functions.

    timer.json      [{"t": epoch seconds, "event": start|lap|stop, "label": "..."}]
    checklist.json  {"inventory": [...], "notes": {step: [...]}, "completed": [step, ...]}
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import geo, ledger

# ------------------------------------------------------------------ timer


def _load(path, default):
    if Path(path).exists():
        with Path(path).open(encoding="utf-8") as f:
            return json.load(f)
    return default


def _save(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(path).with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    tmp.replace(path)


def timer(case_dir, event, label="", now=None):
    """Record start/lap/stop, or just report ("show"). Returns the elapsed summary."""
    path = Path(case_dir) / "timer.json"
    events = _load(path, [])
    now = time.time() if now is None else now
    if event == "start":
        if events and events[-1]["event"] != "stop":
            raise ledger.LedgerError("timer already running for this case (use lap or stop)")
        events.append({"t": now, "event": "start", "label": label or "start"})
    elif event in ("lap", "stop"):
        if not events or events[-1]["event"] == "stop":
            raise ledger.LedgerError("timer is not running (use start)")
        events.append({"t": now, "event": event, "label": label or event})
    elif event != "show":
        raise ledger.LedgerError(f"unknown timer event {event!r}")
    if event != "show":
        _save(path, events)
    return summary(events, now)


def summary(events, now=None):
    now = time.time() if now is None else now
    total, laps, run_start, prev = 0.0, [], None, None
    for e in events:
        if e["event"] == "start":
            run_start = prev = e["t"]
        elif run_start is not None:
            laps.append({"label": e["label"], "lap_s": round(e["t"] - prev, 1), "at_s": round(total + e["t"] - run_start, 1)})
            prev = e["t"]
            if e["event"] == "stop":
                total += e["t"] - run_start
                run_start = None
    running = run_start is not None
    if running:
        total += now - run_start
    return {"running": running, "elapsed_s": round(total, 1), "elapsed": _fmt(total), "laps": laps}


def _fmt(s):
    s = int(s)
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ------------------------------------------------------------------ checklist

STEPS = [
    ("inventory", "Inventory. List every visible clue, one per line: text, script, signs, road markings, "
                  "vehicles, plates, poles, vegetation, terrain, architecture, sun and shadows. No interpretation yet."),
    ("rate", "Rate each clue: HARD (eliminates places) or SOFT (only ranks); uniqueness, permanence, "
             "reliability 1-5; an independence group (clues from the same observation share a group)."),
    ("hypotheses", "Write at least 3 rival hypotheses, each as a point (label | coordinates)."),
    ("discriminate", "For each clue, say which hypotheses it supports and which it contradicts. "
                     "Prefer clues that could prove a hypothesis wrong."),
    ("narrow", "Narrow by map evidence: geocode names, `orith osm` feature queries near the leading "
               "hypothesis, links to street-level imagery. Record what you searched and what came back."),
    ("geometry", "Switch from appearance to geometry: bearings to landmarks (`orith resection`), shadow "
                 "direction and length (`orith sun`), skyline and layout matched against the map."),
    ("audit", "Self-audit: run rank; read the unmet conditions; write what single observation would prove "
              "the leading hypothesis wrong, and check it."),
]
STEP_IDS = [s for s, _ in STEPS]


def _ask(prompt, input_fn):
    try:
        return input_fn(prompt).strip()
    except EOFError:
        return ""


def _lines(prompt, input_fn):
    out = []
    while True:
        line = _ask(prompt, input_fn)
        if not line:
            return out
        out.append(line)


def run_step(case_path, step, input_fn=input, print_fn=print):
    """Walk one checklist step interactively. Returns a summary of what was recorded."""
    if step not in STEP_IDS:
        raise ledger.LedgerError(f"unknown step {step!r}; steps: {', '.join(STEP_IDS)}")
    case_path = Path(case_path)
    notes_path = case_path.parent / "checklist.json"
    notes = _load(notes_path, {"inventory": [], "notes": {}, "completed": []})
    case = ledger.load(case_path)
    print_fn(dict(STEPS)[step])
    recorded = []

    if step == "inventory":
        items = _lines("clue (blank line to finish)> ", input_fn)
        notes["inventory"] += items
        recorded = items

    elif step == "rate":
        pending = list(notes["inventory"])
        if not pending:
            print_fn("No inventory yet: type clues directly.")
        print_fn("Format: HARD|SOFT u p r group [eN] [geometry]   (blank = skip this clue)")
        for text in pending:
            ans = _ask(f"[{text}]> ", input_fn)
            if not ans:
                continue
            parts = ans.split()
            if len(parts) < 5:
                print_fn("  need: KIND u p r group; skipped")
                continue
            kind, u, p, r, group = parts[:5]
            ev = next((x for x in parts[5:] if x.startswith("e")), None)
            geom = "geometry" in parts[5:]
            try:
                c = ledger.add_clue(case, text, kind, int(u), int(p), int(r), group, geometry=geom, evidence=ev)
                recorded.append(c["id"])
                print_fn(f"  recorded {c['id']}")
            except (ledger.LedgerError, ValueError) as e:
                print_fn(f"  not recorded: {e}")
        notes["inventory"] = []

    elif step == "hypotheses":
        print_fn("Format: label | coordinates (decimal, DMS, MGRS...). At least 3. Blank line to finish.")
        for line in _lines("hypothesis> ", input_fn):
            if "|" not in line:
                print_fn("  need 'label | coordinates'; skipped")
                continue
            label, coords = (x.strip() for x in line.split("|", 1))
            try:
                lat, lon = geo.parse_point(coords)
                h = ledger.add_hypothesis(case, label, lat, lon)
                recorded.append(h["id"])
                print_fn(f"  recorded {h['id']} at {lat:.5f}, {lon:.5f}")
            except (ledger.LedgerError, ValueError) as e:
                print_fn(f"  not recorded: {e}")
        if len(case["hypotheses"]) < ledger.MIN_HYPOTHESES:
            print_fn(f"  only {len(case['hypotheses'])} hypotheses: the method asks for at least {ledger.MIN_HYPOTHESES}")

    elif step == "discriminate":
        hyps = [h["id"] for h in case["hypotheses"]]
        print_fn("For each clue: 's h1 h3' supports, 'c h2' contradicts, both allowed: 's h1 c h2'. Blank = none.")
        for c in case["clues"]:
            ans = _ask(f"{c['id']} [{c['text'][:60]}] hyps {' '.join(hyps)}> ", input_fn).split()
            rel = None
            for tok in ans:
                if tok in ("s", "c"):
                    rel = "supports" if tok == "s" else "contradicts"
                elif rel and tok in hyps:
                    ledger.add_link(case, c["id"], tok, rel)
                    recorded.append(f"{c['id']} {rel} {tok}")
                elif tok:
                    print_fn(f"  ignored {tok!r}")

    else:  # narrow, geometry, audit: free notes (commands are run separately)
        if step == "audit":
            ev = ledger.evaluate(case)
            print_fn(f"verdict: {ev['verdict']}  top: {ev['top']}")
            for u in ev["unmet"]:
                print_fn(f"  unmet: {u}")
        items = _lines("note (blank line to finish)> ", input_fn)
        notes["notes"].setdefault(step, []).extend(items)
        recorded = items

    ledger.save(case, case_path)
    if step not in notes["completed"]:
        notes["completed"].append(step)
    _save(notes_path, notes)
    return {"step": step, "recorded": recorded, "completed_steps": notes["completed"],
            "next_step": next((s for s in STEP_IDS if s not in notes["completed"]), None)}


def plan():
    return [{"step": s, "do": d} for s, d in STEPS]
