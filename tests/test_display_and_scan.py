"""--json is byte-for-byte the old JSON (golden files captured before the text output existed);
the default is human text, with colour only on a terminal; scan records into the ledger once."""
import io
import json
from pathlib import Path

import pytest

from orith import cli, display, ledger, paths

GOLD = Path(__file__).resolve().parent / "golden"


def _run(capsys, argv):
    code = cli.main(argv)
    return code, capsys.readouterr().out


@pytest.mark.parametrize("name", sorted(p.stem for p in GOLD.glob("*.json")))
def test_json_output_is_identical_to_the_golden_capture(name, capsys, monkeypatch, tmp_path):
    import shutil
    if not (paths.data_dir("packs") / "tn").exists() and name in ("geocode_offline", "where"):
        pytest.skip("Tunisia pack not installed")
    g = json.loads((GOLD / f"{name}.json").read_text(encoding="utf-8"))
    cases = tmp_path / "cases"  # a copy: commands log their calls into the case folder
    shutil.copytree(GOLD / "cases", cases)
    argv = [str(cases) if x == "CASES" else x for x in g["argv"]]
    monkeypatch.delenv(f"{paths.ENV}_JSON", raising=False)
    code, out = _run(capsys, argv + ["--json"])
    # the tzdata package may be newer than when the golden file was made (dependency ranges): its IANA
    # version string is data, not output format, so it is the one value compared loosely
    def norm(s):
        import re
        return re.sub(r"IANA version \d{4}[a-z]", "IANA version X", s)
    assert code == g["exit"] and json.loads(norm(out)) == json.loads(norm(json.dumps(g["output"], ensure_ascii=False)))


def test_default_output_is_text_without_colour_when_not_a_terminal(capsys, monkeypatch, tmp_path):
    import shutil
    shutil.copytree(GOLD / "cases", tmp_path / "cases")
    monkeypatch.delenv(f"{paths.ENV}_JSON", raising=False)
    code, out = _run(capsys, ["report", "--case", "g1", "--case-dir", str(tmp_path / "cases")])
    assert code == 0 and not out.lstrip().startswith("{") and "\x1b[" not in out
    assert "CANDIDATE - not an identification" in out and "A: 0 independent clue(s)" in out
    code, out = _run(capsys, ["tools", "distance", "36.8,10.18", "36.9,10.3"])
    assert code == 0 and "distance_km" in out and "\x1b[" not in out


def test_colour_rules_tty_no_color_and_flag(monkeypatch):
    class TTY(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(display, "_enable_vt", lambda: True)
    monkeypatch.setenv("WT_SESSION", "1")
    assert display.make_style(stream=TTY()).color is True
    assert display.make_style(no_color=True, stream=TTY()).color is False
    monkeypatch.setenv("NO_COLOR", "1")
    assert display.make_style(stream=TTY()).color is False
    assert display.make_style(stream=io.StringIO()).color is False
    st = display.Style(ascii_only=True)
    assert st.t("Tozeur توزر") == "Tozeur ????" and st.replaced == 4
    assert display.Style().t("توزر").startswith("⁨")  # RTL isolated, not reordering the line


def test_scan_records_once_and_its_verdict_comes_from_the_ledger(tmp_path, capsys, monkeypatch):
    from PIL import Image
    e = Image.Exif()
    sub = e.get_ifd(0x8769)
    sub[0x9003], sub[0x9011] = "2025:06:21 16:30:00", "+01:00"
    img = tmp_path / "demo.jpg"
    Image.new("RGB", (64, 48), (200, 170, 120)).save(img, exif=e.tobytes())
    argv = [str(img), "--text", "+216 76 452 100", "--shadow-bearing", "95", "--case-dir", str(tmp_path / "c")]
    code, out = _run(capsys, argv + ["--json"])
    o = json.loads(out)
    assert code == 0 and o["case"] == "demo" and o["time"]["utc"] == "2025-06-21T15:30:00Z"
    assert any(r["label"].startswith("phone area:") for r in o["regions"]) or not (paths.data_dir("packs") / "tn").exists()
    case = ledger.load(tmp_path / "c" / "demo" / "case.json")
    assert all(c["kind"] == "SOFT" for c in case["clues"])
    assert o["ledger"]["verdict"] == ledger.evaluate(case)["verdict"]
    n = (len(case["clues"]), len(case["hypotheses"]))
    _run(capsys, argv + ["--json"])  # a second scan adds nothing
    case2 = ledger.load(tmp_path / "c" / "demo" / "case.json")
    assert (len(case2["clues"]), len(case2["hypotheses"])) == n
    monkeypatch.delenv(f"{paths.ENV}_JSON", raising=False)
    code, out = _run(capsys, argv)  # default view: three sections, a confidence on every line
    for title in ("VERDICT", "best guess", "confidence", "EVIDENCE FOUND", "LEADS TO CHECK MANUALLY"):
        assert title in out, title
    assert "MEDIUM  phone number +21676452100 is valid in TN" in out and "HYPOTHESES (RANKED)" not in out
    code, out = _run(capsys, argv + ["-v"])  # the full working
    for title in ("CANDIDATE", "best hypothesis:", "HYPOTHESES (RANKED)", "MOST LIKELY COUNTRIES", "EVIDENCE FOUND",
                  "THE FILE", "NEXT STEPS", "LINKS"):
        assert title in out, title


def _blank(tmp_path, name="street.jpg"):
    from PIL import Image
    img = tmp_path / name
    Image.new("RGB", (64, 48), (90, 120, 90)).save(img)  # no metadata at all
    return img


def test_nothing_found_prints_one_line_and_hides_empty_sections(tmp_path, capsys, monkeypatch):
    from orith import scan
    monkeypatch.delenv(f"{paths.ENV}_JSON", raising=False)
    img = _blank(tmp_path)
    code, out = _run(capsys, [str(img), "--no-interactive", "--case-dir", str(tmp_path / "c")])
    assert code == 0 and scan.NO_LOOK in out and "--drives left|right" in out
    for hidden in ("MOST LIKELY COUNTRIES", "HYPOTHESES", "NEXT STEPS", "SUN"):
        assert hidden not in out, hidden
    assert "best guess   none" in out and "no metadata in the file" in out and "lens.google.com" in out
    code, out = _run(capsys, [str(img), "--no-interactive", "-v", "--case-dir", str(tmp_path / "c")])
    for hidden in ("EVIDENCE FOUND", "MOST LIKELY COUNTRIES", "HYPOTHESES", "NEXT STEPS", "SUN"):
        assert hidden not in out, hidden
    assert "CANDIDATE" in out and "LINKS" in out


def test_guided_questions_from_stdin_become_scan_inputs(tmp_path, capsys, monkeypatch):
    img = _blank(tmp_path)
    answers = "﻿r\nشارع الحبيب بورقيبة\n\n+216 76 452 100\n\n\nC\nTozeur\n\n2025-06-21T16:30+01:00\n1 1.33\n95\n" \
              "palm trees\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(answers))
    code, out = _run(capsys, [str(img), "--interactive", "--json", "--case-dir", str(tmp_path / "c")])
    o = json.loads(out)  # prompts went to stderr: stdout is still pure JSON
    assert code == 0 and o["usable"] is True and o["time"]["utc"] == "2025-06-21T15:30:00Z"
    texts = [c["text"] for c in o["clues"]]
    assert "cars drive on the right" in texts and "plug or socket type C seen" in texts
    assert any(t.startswith("observed: palm trees") for t in texts)
    assert any(t.startswith("script seen: Arabic") for t in texts)
    assert o["narrowing"]["most_likely"][0]["iso2"] == "TN" or not (paths.data_dir("packs") / "tn").exists()
    assert "--drives right" in o["command"] and "--plug C" in o["command"]


def test_q_stops_the_questions_and_when_questions_are_asked(tmp_path, capsys, monkeypatch):
    import argparse
    img = _blank(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("l\nq\n"))
    code, out = _run(capsys, [str(img), "--interactive", "--json", "--case-dir", str(tmp_path / "c")])
    o = json.loads(out)
    assert [c["text"] for c in o["clues"]] == ["cars drive on the left"]

    class TTY(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr("sys.stdin", TTY())
    monkeypatch.setattr("sys.stdout", TTY())
    cli.STATE["json"] = False
    ns = dict(interactive=False, no_interactive=False, yes=False)
    assert cli._can_ask(argparse.Namespace(**ns)) is False  # the default scan never asks, even on a terminal
    assert cli._can_ask(argparse.Namespace(**(ns | {"interactive": True}))) is True  # --ask
    assert cli._can_ask(argparse.Namespace(**(ns | {"interactive": True, "yes": True}))) is False
    cli.STATE["json"] = True


def test_next_clue_comes_from_the_fact_table():
    from orith import facts
    if not (facts.facts_dir() / "country_facts.csv").exists():
        pytest.skip("fact table not installed")
    best = facts.best_next_clue(["GB", "FR", "DE", "JP"])
    assert best["field"] in facts.VISIBLE_FIELDS and best["expected_remaining"] < 4
    assert facts.best_next_clue(["GB"]) is None
    other = facts.best_next_clue(["GB", "FR", "DE", "JP"], ["drives", "plug"])
    assert other is None or other["field"] not in ("driving_side", "plug_type")
