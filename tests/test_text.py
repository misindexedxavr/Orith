"""Text tools: scripts (pinned UCD), phone numbers (phonenumbers), domains and currencies (fact table)."""
import hashlib
import json

import pytest

from orith import text

pytest.importorskip("phonenumbers")


def test_scripts_nfc_common_and_script_extensions():
    # "e" + combining acute (decomposed) must count like the composed é; digits and spaces are Common;
    # the Arabic comma U+060C is Common in Scripts.txt and resolved to Arabic through ScriptExtensions.
    r = text.scripts("مرحبا، Café 123")
    counts = {c["script"]: c["chars"] for c in r["counts"]}
    assert counts == {"Arabic": 6, "Latin": 4} and r["common_or_inherited"] == 3
    assert r["unicode_version"] == "18.0.0" and r["normalisation"] == "NFC" and r["shared_unresolved"] == []
    alone = text.scripts("،")  # no Arabic letter present: stays shared, with its listed scripts
    assert alone["counts"] == [] and "Arabic" in alone["shared_unresolved"][0]["scripts"]


def test_phone_with_country_code_and_invalid_number_is_not_reported():
    r = text.phones("Tél: +216 71 123 456 / +216 12")
    (n,) = r["numbers"]
    assert n["e164"] == "+21671123456" and n["valid"] and n["region_codes"] == ["TN"]
    assert "VALID" in r["matcher"] and r["source"].startswith("phonenumbers ")


def test_national_number_needs_a_region_or_the_local_all_regions_scan():
    assert text.phones("call 71 123 456")["numbers"] == []
    assert text.phones("call 71 123 456", region="TN")["numbers"][0]["e164"] == "+21671123456"
    r = text.phones("call 71 123 456", all_regions=True)
    regions = [v["region"] for v in r["national_number_regions"][0]["valid_in"]]
    assert "TN" in regions and len(regions) > 1 and r["all_regions_note"].startswith("Local only")


def test_domains_match_only_with_a_fact_row_otherwise_unknown():
    d = {x["text"]: x for x in text.domains("www.example.tn موقع.تونس example.com")}
    tn = d["www.example.tn"]
    assert tn["result"] == "match" and tn["countries"][0]["iso2"] == "TN" and tn["countries"][0]["source_url"]
    idn = d["موقع.تونس"]
    assert idn["result"] == "unknown" and idn["non_ascii"] and idn["tld_ascii"].startswith(".xn--")
    assert d["example.com"]["result"] == "unknown"
    cur, sym = text.currencies("15 TND, 3 € and ABC")
    assert [c["code"] for c in cur] == ["TND"] and sym[0]["name"] == "EURO SIGN"


def test_pinned_unicode_files_match_their_recorded_hashes():
    meta = json.loads((text.unicode_dir() / "SOURCES.json").read_text(encoding="utf-8"))
    assert meta["unicode_version"] == "18.0.0"
    for name, info in meta["files"].items():
        data = (text.unicode_dir() / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == info["sha256"] and info["url"].startswith("https://www.unicode.org/")
