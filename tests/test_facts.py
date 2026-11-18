"""Country fact filter: semantics on a small fixture table, plus integrity of the shipped table."""
import csv
import json

import pytest

from orith import facts

COLS = ["iso2", "country", "field", "value", "status", "note", "source", "source_url", "retrieved"]
FIX = [  # iso2, field, value, status
    ("AA", "driving_side", "left", "normal"), ("AA", "plug_type", "G", "normal"),
    ("BB", "driving_side", "right", "normal"), ("BB", "plug_type", "C", "normal"),
    ("CC", "driving_side", "right", "exception"), ("CC", "plug_type", "G", "normal"),
    ("DD", "plug_type", "G", "normal"),  # no driving_side fact at all
    ("EE", "calling_code", "+1-809 and 1-829", "normal"),
]


@pytest.fixture
def table(tmp_path):
    with (tmp_path / "country_facts.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for iso, field, val, st in FIX:
            w.writerow({"iso2": iso, "country": f"Country {iso}", "field": field, "value": val, "status": st,
                        "note": "mixed" if st == "exception" else "", "source": "fixture", "source_url": "https://x/y",
                        "retrieved": "2026-11-15"})
    square = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}
    (tmp_path / "countries_ne50m.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": square, "properties": {"iso2": "BB", "name": "Country BB"}}]}), encoding="utf-8")
    return tmp_path


def test_match_unknown_uncertain_and_excluded_with_reasons(table):
    r = facts.filter_countries([("drives", "left"), ("plug", "G")], folder=table)
    consistent = {c["iso2"]: {x["clue"]: x["result"] for x in c["clues"]} for c in r["consistent"] if c["iso2"] != "EE"}
    assert consistent == {"AA": {"drives=left": "match", "plug=G": "match"},
                          "CC": {"drives=left": "uncertain", "plug=G": "match"},   # exception never excludes
                          "DD": {"drives=left": "unknown", "plug=G": "match"}}     # missing fact never excludes
    assert [c["iso2"] for c in r["consistent"]][0] == "AA"
    (ex,) = r["excluded"]
    assert ex["iso2"] == "BB" and {w["clue"] for w in ex["excluded_by"]} == {"drives=left", "plug=G"}
    assert ex["excluded_by"][0]["facts"][0]["source_url"] == "https://x/y"
    assert r["counts"] == {"consistent": 4, "excluded": 1, "total": 5}  # EE has neither fact: unknown twice


def test_calling_codes_written_as_ranges_still_match(table):
    r = facts.filter_countries([("calling_code", "+1")], folder=table)
    assert "EE" in [c["iso2"] for c in r["consistent"] if c["matches"] == 1]
    r = facts.filter_countries([("calling_code", "1-829")], folder=table)
    assert "EE" in [c["iso2"] for c in r["consistent"] if c["matches"] == 1]


def test_point_restricts_to_the_containing_country_and_says_why(table):
    r = facts.filter_countries([("plug", "C")], point=(5, 5), folder=table)
    assert [c["iso2"] for c in r["consistent"]] == ["BB"] and r["point_country"][0]["iso2"] == "BB"
    assert all("point" in e["excluded_by"][0]["clue"] for e in r["excluded"])


def test_unknown_clue_and_missing_table_are_errors(table, tmp_path):
    with pytest.raises(facts.FactsError):
        facts.filter_countries([("religion", "muslim")], folder=table)
    with pytest.raises(facts.FactsError):
        facts.load(str(tmp_path / "nowhere"))


def test_shipped_table_integrity():
    path = facts.facts_dir() / "country_facts.csv"
    if not path.exists():
        pytest.skip("fact table not installed")
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    assert len(rows) > 1000 and all(r["source_url"].startswith("https://") and r["retrieved"] for r in rows)
    assert all(r["value"] for r in rows) and {r["status"] for r in rows} <= {"normal", "exception"}
    assert {r["value"] for r in rows if r["field"] == "driving_side"} == {"left", "right"}
    assert {r["value"] for r in rows if r["field"] == "plug_type"} <= set("ABCDEFGHIJKLMN")
    assert all(r["note"] for r in rows if r["status"] == "exception")  # every exception says why
