"""Tests that read the Tunisia country pack are skipped when that pack is not installed
(a build can ship with any list of packs)."""
import pytest

from orith import packs

NEEDS_TN_PACK = {
    "test_offline_finds_a_town_and_puts_the_most_precise_entry_first",
    "test_offline_ignores_case_and_accents",
    "test_offline_matches_arabic_names",
    "test_offline_tolerates_a_small_misspelling_and_says_so",
    "test_a_large_area_label_point_is_never_precise",
    "test_admin_reverse_geocoding",
    "test_search_uses_nominatim_only_as_fallback",
    "test_cli_full_flow_cannot_reach_identified_without_evidence",
    "test_cli_osm_dry_run_and_pack_area",
    "test_cli_osm_area_ambiguous_name_is_refused",
    "test_cli_where_accepts_negative_longitude",
}


@pytest.fixture(autouse=True)
def _cases_in_tmp(tmp_path_factory, monkeypatch):
    """Commands run by tests never write cases or call logs into the working folder."""
    from orith import paths
    monkeypatch.setenv(f"{paths.ENV}_CASE_DIR", str(tmp_path_factory.mktemp("run") / "cases"))
    # tests read the machine output; the human (default) output has its own tests in test_display_and_scan.py
    monkeypatch.setenv(f"{paths.ENV}_JSON", "1")
    # the optional building layer of the developer's machine never leaks into tests
    monkeypatch.setenv(f"{paths.ENV}_BUILDINGS", str(tmp_path_factory.mktemp("buildings")))


def pytest_collection_modifyitems(config, items):
    if any(p.code == "TN" for p in packs.installed()):
        return
    skip = pytest.mark.skip(reason="Tunisia country pack not installed")
    for item in items:
        if item.name.split("[")[0] in NEEDS_TN_PACK:
            item.add_marker(skip)
