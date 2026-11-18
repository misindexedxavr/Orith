"""Script as a SOFT facts clue (Unicode CLDR 49.0.0 rows): match or unknown only, never excluded."""
import csv

import pytest

from orith import facts, text


def _shipped():
    path = facts.facts_dir() / "country_facts.csv"
    if not path.exists():
        pytest.skip("fact table not installed")
    return path


def test_script_rows_are_sourced_pinned_and_print_the_threshold():
    rows = [r for r in csv.DictReader(_shipped().open(encoding="utf-8")) if r["field"] == "script"]
    assert len(rows) > 300
    for r in rows:
        assert "cldr-json/49.0.0/" in r["source_url"] and r["retrieved"] and "threshold:" in r["note"]
        assert len(r["value"]) == 4 and r["value"][0].isupper()
    tn = {r["value"] for r in rows if r["iso2"] == "TN"}
    assert tn == {"Arab", "Latn"}


def test_script_clue_never_excludes_and_matches_by_code_or_name():
    _shipped()
    r = facts.filter_countries([("script", "Arabic")])
    assert r["counts"]["excluded"] == 0 and r["clues"] == ["script=Arab"]
    by = {c["iso2"]: c["clues"][0]["result"] for c in r["consistent"]}
    assert by["TN"] == "match" and by["FR"] == "unknown"
    with pytest.raises(facts.FactsError):
        facts.filter_countries([("script", "Klingon")])


def test_han_and_kana_match_cldr_compound_codes():
    _shipped()
    for sc in ("Hani", "Hira", "Kana"):
        by = {c["iso2"]: c["clues"][0]["result"] for c in facts.filter_countries([("script", sc)])["consistent"]}
        assert by["JP"] == "match"
    assert {c["iso2"]: c["clues"][0]["result"] for c in
            facts.filter_countries([("script", "Hang")])["consistent"]}["KR"] == "match"


def test_text_suggests_script_clue_and_does_not_run_it():
    r = text.analyse("شارع Avenue")
    assert {"--script Arab", "--script Latn"} <= set(r["facts_clues"])
    assert "never run" in r["notes"][0] and "never excludes" in r["notes"][0]
