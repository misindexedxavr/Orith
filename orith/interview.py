"""Guided questions for a photo the file says nothing about. The tool never looks at the picture: the
analyst does, and these questions turn what they see into scan inputs (the same flags a script would pass).

Only things the toolkit can use are asked for: the driving side and plug type (fact table), text of any kind
(scripts, phone numbers, web domains and currencies are found in it), place names (offline place data, then
Nominatim only if the analyst says yes and a contact is set), and the time and a shadow (sun). Anything else
(vegetation, road markings, bollards, poles, plate formats) is recorded as a note: there is no sourced data to
narrow countries with it, and the tool says so instead of guessing.

Enter skips a question; q stops the questions and runs the scan with the answers so far. Prompts go to
stderr so that the result on stdout stays clean (also with --json).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from . import geocode, net

QUIT = object()


class Session:
    def __init__(self, stdin=None, stderr=None):
        self.stdin, self.stderr = stdin or sys.stdin, stderr or sys.stderr
        self.stopped = False

    def say(self, text=""):
        self.stderr.write(text + "\n")
        self.stderr.flush()

    def ask(self, prompt):
        if self.stopped:
            return None
        self.stderr.write(prompt)
        self.stderr.flush()
        line = self.stdin.readline()
        if line == "":  # end of input: behave like q
            self.stopped = True
            return None
        line = line.lstrip("﻿").replace("ï»¿", "").strip()  # a BOM from a piped file
        if line.lower() in ("q", "quit"):
            self.stopped = True
            return None
        return line or None

    def ask_valid(self, prompt, parse, hint):
        for _ in range(2):
            ans = self.ask(prompt)
            if ans is None:
                return None
            try:
                return parse(ans)
            except ValueError:
                self.say(f"  {hint}")
        return None


def _drives(s):
    s = s.lower()
    if s in ("l", "left"):
        return "left"
    if s in ("r", "right"):
        return "right"
    if s in ("n", "no", "not visible", "-"):
        return None
    raise ValueError(s)


def _plugs(s):
    letters = re.findall(r"[A-Na-n]", s)
    if not letters or re.search(r"[^A-Na-n,;/ ]", s):
        raise ValueError(s)
    return sorted({x.upper() for x in letters})


def _offset(s):
    if not re.fullmatch(r"[+-]\d{2}:\d{2}", s):
        raise ValueError(s)
    return s


def _iso_time(s):
    from . import sun
    sun.parse_time(s)  # raises ValueError without a zone
    return s


def _two_numbers(s):
    nums = [float(x) for x in re.findall(r"\d+(?:[.,]\d+)?", s.replace(",", "."))]
    if len(nums) != 2 or min(nums) <= 0:
        raise ValueError(s)
    return nums


def _bearing(s):
    v = float(s.replace(",", "."))
    if not 0 <= v < 360:
        raise ValueError(s)
    return v


def _contact_set():
    try:
        net.user_agent()
        return True
    except net.NetError:
        return False


def run(a, t, session=None):
    """Ask the questions and put the answers into the parsed arguments `a` (in place). Returns a."""
    s = session or Session()
    s.say("This tool does not look at the picture itself. Tell it what you see.")
    s.say(f"Image: {Path(a.image).resolve()}  (open it yourself in any image viewer)")
    s.say("Enter skips a question, q stops the questions and shows the result.\n")

    d = s.ask_valid("Which side do cars drive on? [l]eft / [r]ight / Enter = not visible: ", _drives,
                    "answer l, r, or press Enter")
    if d:
        a.drives = (a.drives or []) + [d]

    texts = []
    line = s.ask("Text you can read (signs, shops, vehicles; any script; Enter = none): ")
    while line:
        texts.append(line)
        line = s.ask("More text? (Enter = no more): ")
    for prompt in ("A phone number, with its +country code if shown (Enter = none): ",
                   "A web address or e-mail, e.g. shop.example.tn (Enter = none): ",
                   "A price with its currency code or symbol, e.g. 12.500 TND (Enter = none): "):
        ans = s.ask(prompt)
        if ans:
            texts.append(ans)
    if texts:
        a.text = (a.text or []) + texts

    p = s.ask_valid("Plug or socket type, letters A-N, e.g. 'C F' (Enter = not visible): ", _plugs,
                    "use the plug letters A to N (Enter to skip)")
    if p:
        a.plug = (a.plug or []) + p

    places = []
    name = s.ask("A place name or landmark you can read (Enter = none): ")
    while name:
        hit = geocode.offline(name, limit=1)
        if hit:
            h = hit[0]
            s.say(f"  found in the offline place data: {h['name']} ({h['lat']:.4f}, {h['lon']:.4f}), "
                  f"precise to about {h['precision_m'] / 1000:.0f} km")
        elif _contact_set():
            if (s.ask("  not in the offline place data. Search it online with Nominatim? [y/N]: ") or "").lower() == "y":
                a.online = True
        else:
            s.say("  not in the offline place data (an online search needs a contact: see orith doctor)")
        places.append(name)
        name = s.ask("Another place name? (Enter = no more): ")
    if places:
        a.place = (getattr(a, "place", None) or []) + places

    tm = (t or {}).get("time") or {}
    gps_utc = ((t or {}).get("gps") or {}).get("gps_utc")
    if a.time is None:
        if gps_utc or tm.get("utc_from_offset"):
            s.say(f"Capture time from the file: {tm.get('DateTimeOriginal') or gps_utc} "
                  f"(UTC {gps_utc or tm.get('utc_from_offset')}).")
        elif tm.get("DateTimeOriginal"):
            off = s.ask_valid(f"The file says {tm['DateTimeOriginal']} without a time zone. Its UTC offset, "
                              "e.g. +01:00 (Enter = unknown): ", _offset, "format +HH:MM or -HH:MM")
            if off:
                a.utc_offset = off
        else:
            when = s.ask_valid("When was the photo taken, with its zone, e.g. 2025-06-21T16:30+01:00 "
                               "(Enter = unknown): ", _iso_time, "add the zone, e.g. +01:00 or Z")
            if when:
                a.time = when
    hs = s.ask_valid("A shadow: height of an object and its shadow length, e.g. '2 1.5' (Enter = skip): ",
                     _two_numbers, "two positive numbers, height then shadow, same unit")
    if hs:
        a.height, a.shadow = hs
    b = s.ask_valid("Direction the shadow points to, degrees from north (Enter = skip): ", _bearing,
                    "a number from 0 to 359")
    if b is not None:
        a.shadow_bearing = b

    note = s.ask("Anything else (vegetation, road markings, bollards, poles, plate format)? It is recorded in the "
                 "case; there is no data to narrow countries with it (Enter = none): ")
    if note:
        a.note = (getattr(a, "note", None) or []) + [note]
    from .scan import command_line
    s.say(f"\nThe same without questions: {command_line(a)}\n")
    return a
