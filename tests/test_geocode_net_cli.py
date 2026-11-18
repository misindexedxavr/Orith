"""Offline gazetteer, Nominatim parsing, the polite HTTP layer and the CLI wiring. No network."""
import io
import json
import urllib.error

import pytest

import orith
from orith import cli, geocode, ledger, net, packs

# ---------------------------------------------------------------- offline gazetteer / admin

def test_offline_finds_a_town_and_puts_the_most_precise_entry_first():
    hits = geocode.offline("Sidi Bou Said")
    assert hits and hits[0]["match"] == "exact" and hits[0]["admin1"] == "Tunis" and hits[0]["pack"] == "TN"
    assert abs(hits[0]["lat"] - 36.87) < 0.02 and abs(hits[0]["lon"] - 10.34) < 0.02
    assert hits[0]["precision_m"] <= min(h["precision_m"] for h in hits if h["match"] == "exact")


def test_offline_ignores_case_and_accents():
    assert geocode.offline("beja")[0]["admin1"] == geocode.offline("Béja")[0]["admin1"] == "Béja"


def test_offline_matches_arabic_names():
    hits = geocode.offline("توزر")
    assert hits and hits[0]["admin1"] == "Tozeur"


def test_offline_tolerates_a_small_misspelling_and_says_so():
    hits = geocode.offline("Kairuan")
    assert hits and hits[0]["match"] == "fuzzy" and hits[0]["admin1"] == "Kairouan"


def test_offline_returns_nothing_for_foreign_or_empty_names():
    assert geocode.offline("Reykjavik") == [] and geocode.offline("  ") == []


def test_a_large_area_label_point_is_never_precise():
    gov = [h for h in geocode.offline("Tataouine", limit=20) if h["level"] == "governorate"]
    assert gov and gov[0]["precision_m"] > ledger.AGREE_M_DEFAULT * 100


def test_admin_reverse_geocoding():
    (hit,) = packs.locate(33.9197, 8.1335)
    assert hit["pack"] == "TN" and [a["name"] for a in hit["admin"]] == ["Tozeur", "Tozeur"]
    assert [a["label"] for a in hit["admin"]] == ["governorate", "delegation"]  # labels come from the manifest
    assert packs.locate(48.8584, 2.2945) == [] and packs.locate(35.83, 10.75) == []  # Paris; sea off Sousse


# ---------------------------------------------------------------- nominatim

def test_nominatim_url_is_deterministic_and_bounded():
    u = geocode.nominatim_url("Lycee Pilote", limit=3, countrycodes="TN", viewbox=(36.0, 9.0, 37.0, 10.5))
    assert u.startswith("https://nominatim.openstreetmap.org/search?")
    assert "countrycodes=tn" in u and "viewbox=9.0%2C37.0%2C10.5%2C36.0" in u and "bounded=1" in u and "limit=3" in u
    assert u == geocode.nominatim_url("Lycee Pilote", limit=3, countrycodes="TN", viewbox=(36.0, 9.0, 37.0, 10.5))


def test_parse_nominatim_computes_precision_from_the_bounding_box():
    payload = [{"lat": "36.8", "lon": "10.18", "display_name": "Cafe X, Tunis", "category": "amenity", "type": "cafe",
                "osm_type": "node", "osm_id": 5, "importance": 0.2, "boundingbox": ["36.7999", "36.8001", "10.1799", "10.1801"],
                "address": {"country": "Tunisia", "state": "Tunis"}},
               {"lat": "36.8", "lon": "10.18", "display_name": "Tunis", "boundingbox": ["36.7", "36.9", "10.0", "10.4"]}]
    a, b = geocode.parse_nominatim(payload)
    assert a["ref"] == "node/5" and a["precision_m"] < 30 and a["country"] == "Tunisia"
    assert b["precision_m"] > 10000
    assert geocode.parse_nominatim([]) == [] and geocode.parse_nominatim(None) == []


def test_search_uses_nominatim_only_as_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(geocode, "online", lambda *a, **k: (calls.append(a) or [{"source": "nominatim", "lat": 1, "lon": 2}], False))
    res, note = geocode.search("Tozeur")
    assert calls == [] and all(r["source"] == "offline" for r in res)
    res, note = geocode.search("Meydan Racecourse")
    assert len(calls) == 1 and res[0]["source"] == "nominatim"
    geocode.search("Tozeur", use_online="always")
    assert len(calls) == 2
    geocode.search("Meydan Racecourse", use_online="never")
    assert len(calls) == 2
    geocode.search("Tozeur", countrycodes="dz")  # a country with no installed pack: offline finds nothing
    assert len(calls) == 3


# ---------------------------------------------------------------- net

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_contact_comes_from_env_or_user_config_and_is_required_online(tmp_path, monkeypatch):
    from orith import paths
    for k in ("CONTACT", "USER_AGENT"):
        monkeypatch.delenv(f"{paths.ENV}_{k}", raising=False)
    monkeypatch.setenv(f"{paths.ENV}_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(net, "CONFIG", None)
    with pytest.raises(net.NetError) as e:
        net.fetch_json("https://example.invalid/x", cache_dir=tmp_path)
    assert f"{paths.ENV}_CONTACT" in str(e.value) and "config.json" in str(e.value) and "Offline" in str(e.value)
    (tmp_path / "config.json").write_text('{"contact": "me@example.org"}', encoding="utf-8")
    assert net.user_agent() == f"{paths.APP}/{orith.__version__} (me@example.org)"
    monkeypatch.setenv(f"{paths.ENV}_CONTACT", "https://example.org/me")
    assert net.user_agent().endswith("(https://example.org/me)")


def test_fetch_json_sends_the_contact_string_caches_and_does_not_refetch(tmp_path, monkeypatch):
    monkeypatch.setenv("ORITH_USER_AGENT", "test-agent (contact)")
    monkeypatch.setitem(net.MIN_INTERVAL, "example.invalid", 0.0)
    seen = []

    def opener(req, timeout):
        seen.append(req.get_header("User-agent"))
        return FakeResponse(json.dumps({"ok": 1}).encode())

    assert net.fetch_json("https://example.invalid/x", cache_dir=tmp_path, opener=opener) == ({"ok": 1}, False)
    assert net.fetch_json("https://example.invalid/x", cache_dir=tmp_path, opener=opener) == ({"ok": 1}, True)
    assert seen == ["test-agent (contact)"]


def test_fetch_json_backs_off_on_429_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("ORITH_USER_AGENT", "test-agent (contact)")
    monkeypatch.setitem(net.MIN_INTERVAL, "example.invalid", 0.0)
    attempts, sleeps = [], []

    def opener(req, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
        return FakeResponse(b'{"ok": 2}')

    assert net.fetch_json("https://example.invalid/y", cache_dir=tmp_path, opener=opener, sleep=sleeps.append) == ({"ok": 2}, False)
    assert len(attempts) == 3 and sleeps == [3.0, 6.0]


def test_fetch_json_gives_up_cleanly_and_does_not_retry_a_400(tmp_path, monkeypatch):
    monkeypatch.setenv("ORITH_USER_AGENT", "test-agent (contact)")
    monkeypatch.setitem(net.MIN_INTERVAL, "example.invalid", 0.0)
    attempts = []

    def opener(req, timeout):
        attempts.append(1)
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)

    with pytest.raises(net.NetError):
        net.fetch_json("https://example.invalid/z", cache_dir=tmp_path, opener=opener, sleep=lambda s: None)
    assert len(attempts) == 1 and not list(tmp_path.glob("*.json"))


def test_throttle_spaces_out_requests_to_one_host(tmp_path, monkeypatch):
    monkeypatch.setitem(net.MIN_INTERVAL, "example.invalid", 30.0)
    slept = []
    monkeypatch.setattr(net.time, "sleep", slept.append)
    net._throttle("example.invalid", tmp_path)
    net._throttle("example.invalid", tmp_path)
    assert len(slept) == 1 and 29.0 < slept[0] <= 30.0


# ---------------------------------------------------------------- CLI

def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_cli_full_flow_cannot_reach_identified_without_evidence(tmp_path, capsys):
    img = tmp_path / "img_test.jpg"
    img.write_bytes(b"not really a jpeg")
    cd = ["--case-dir", str(tmp_path / "cases"), "--case", "img_test"]
    code, o = run(capsys, "case", "new", str(img), "--case-dir", str(tmp_path / "cases"))
    assert code == 0 and o["case"] == "img_test"
    code, o = run(capsys, "geocode", "Tozeur", "--offline", *cd)
    assert code == 0 and o["results"][0]["evidence"] == "e1"
    for label, lat, lon in (("A", 33.92, 8.13), ("B", 34.38, 7.95), ("C", 34.32, 8.40)):
        assert run(capsys, "hyp", "add", "--label", label, "--lat", str(lat), "--lon", str(lon), *cd)[0] == 0
    for i in range(4):
        assert run(capsys, "clue", "add", "--text", f"clue {i}", "--kind", "SOFT", "--u", "5", "--p", "5", "--r", "5",
                   "--group", f"g{i}", *cd)[0] == 0
        assert run(capsys, "link", "--clue", f"c{i + 1}", "--hyp", "h1", "--rel", "supports", *cd)[0] == 0
    code, o = run(capsys, "rank", *cd)
    assert o["verdict"] == "CANDIDATE" and o["top"] == "h1"
    code, o = run(capsys, "report", *cd)
    assert o["verdict"] == "CANDIDATE" and "not an identification" in o["text"]


def test_cli_has_no_verdict_option():
    parser = cli.build_parser()
    for words in (["rank", "--verdict", "identified"], ["report", "--identified"], ["case", "new", "x.jpg", "--verdict", "IDENTIFIED"]):
        with pytest.raises(SystemExit):
            parser.parse_args(words)


def test_cli_reports_errors_as_json_with_exit_code_2(tmp_path, capsys):
    code, o = run(capsys, "rank", "--case", "nope", "--case-dir", str(tmp_path))
    assert code == 2 and "error" in o
    code, o = run(capsys, "osm", "--feature", "mosque", "--dry-run")
    assert code == 2 and "search area" in o["error"]


def test_cli_osm_dry_run_and_pack_area(capsys):
    code, o = run(capsys, "osm", "--feature", "water_tower", "--area", "TN-72", "--dry-run")
    assert code == 0 and '["man_made"="water_tower"]' in o["query"]
    s, w, n, e = o["spec"]["area"]["bbox"]
    assert s < 33.92 < n and w < 8.13 < e


def test_cli_osm_area_ambiguous_name_is_refused(capsys):
    code, o = run(capsys, "osm", "--feature", "mosque", "--area", "Tozeur", "--dry-run")
    assert code == 2 and "ambiguous" in o["error"]  # Tozeur is both a governorate and a delegation


def test_cli_where_accepts_negative_longitude(capsys):
    code, o = run(capsys, "where", "33.585", "-7.69")
    assert code == 0 and o["results"] == [] and "TN" in o["packs_installed"]
