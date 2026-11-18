"""Report's informational name-match line (never changes the verdict) and logging of --help calls."""
import json

import pytest

from orith import cli, ledger


def test_report_prints_name_match_line_and_verdict_is_unchanged(tmp_path, capsys):
    path = tmp_path / "cases" / "n1" / "case.json"
    c = ledger.new_case(tmp_path / "n1.jpg")
    e1 = ledger.add_evidence(c, "nominatim", 18.8119, 98.9841, name="Khuang Sing Intersection, Chang Phueak, Chiang Mai")
    e2 = ledger.add_evidence(c, "osm", 18.90, 98.90, name="Khuang Sing Market")  # same words, ~12 km away
    ledger.add_hypothesis(c, "monument", evidence=e1["id"])
    ledger.add_hypothesis(c, "far", 18.90, 98.90)
    ledger.add_hypothesis(c, "other", 13.75, 100.50)
    k = ledger.add_clue(c, "Sign reads KHUANG SING intersection, Chiang Mai", "SOFT", 5, 4, 4, "sign", evidence=e1["id"])
    ledger.add_link(c, k["id"], "h1", "supports")
    ledger.add_link(c, k["id"], "h3", "supports")
    ledger.save(c, path)
    before = ledger.evaluate(ledger.load(path))
    assert cli.main(["report", "--case", "n1", "--case-dir", str(tmp_path / "cases")]) == 0
    o = json.loads(capsys.readouterr().out)
    match_lines = [ln for ln in o["text"].splitlines() if cli.NAME_MATCH_NOTE in ln]
    assert len(match_lines) == 1 and match_lines[0].strip().startswith("h1: clue c1") and "e1" in match_lines[0]
    assert o["verdict"] == before["verdict"] == "CANDIDATE" and o["unmet"] == before["unmet"]
    assert e2["id"] not in match_lines[0]


def test_help_calls_are_logged(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["clue", "add", "--help", "--case-dir", str(tmp_path), "--case", "z9"])
    rows = [json.loads(x) for x in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["cmd"] == "help" and rows[-1]["exit"] == 0 and rows[-1]["argv"][:3] == ["clue", "add", "--help"]
