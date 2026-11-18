"""osm name search: one key-regex filter over 8 name keys, Arabic letter classes, --translit (dry-run only)."""
import json
import re

from orith import cli, osm


def dry(capsys, *argv):
    code = cli.main(["osm", *argv, "--dry-run"])
    return code, json.loads(capsys.readouterr().out)


def test_name_uses_one_key_regex_filter_with_arabic_classes(capsys):
    code, o = dry(capsys, "--feature", "mosque", "--name", "جامع أبي", "--around", "33.7,10.8,2000")
    assert code == 0
    q = o["query"]
    keys = '"^(name|name:ar|name:fr|name:en|alt_name|old_name|official_name|int_name)$"'
    assert q.count("[~" + keys + "~") == 1 and ",i]" in q and '["name"~' not in q
    assert "ج[اأإآ]مع [اأإآ]ب[ىي]" in q


def test_translit_needs_bbox_and_three_consonants_and_matches_variants(capsys):
    code, o = dry(capsys, "--name", "Boumasour", "--translit", "--around", "33.7,10.8,2000")
    assert code == 2 and "--bbox" in o["error"]
    code, o = dry(capsys, "--name", "El Djem", "--translit", "--bbox", "33.6,10.7,33.9,11.0")
    assert code == 2 and "3 consonants" in o["error"]
    code, o = dry(capsys, "--name", "Boumasour", "--translit", "--bbox", "33.6,10.7,33.9,11.0")
    assert code == 0
    rx = re.compile(osm.translit_pattern("Boumasour")[0], re.I)
    assert all(rx.search(s) for s in ("Bou Messouer", "Abi Masur", "Jamaa Boumasour"))
    assert not rx.search("Monastir")
    k = re.compile(osm.translit_pattern("Khalil")[0], re.I)  # kh/h, l-i-l kept as two consonants
    assert k.search("Halil") and k.search("Khelil") and not k.search("Hal")


def test_limit_default_400_hard_cap_1000():
    q = {"feature": "fuel", "area": {"around": [36.8, 10.18, 1000]}}
    assert osm.build_query(q).endswith("out tags bb 400;")
    assert osm.build_query({**q, "limit": 1000}).endswith("out tags bb 1000;")
    try:
        osm.build_query({**q, "limit": 1001})
        raise AssertionError("1001 accepted")
    except osm.SpecError as e:
        assert "1..1000" in str(e)


def test_pages_come_from_one_cached_request(tmp_path, monkeypatch, capsys):
    import io

    from orith import net
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        els = [{"type": "node", "id": i, "lat": 36.8, "lon": 10.18, "tags": {"name": f"n{i}"}} for i in range(450)]
        return Resp(json.dumps({"elements": els}).encode())
    monkeypatch.setattr(net, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(net, "user_agent", lambda: "orith-test/0 (test)")
    monkeypatch.setattr(osm, "endpoints", lambda: [osm.OVERPASS])
    monkeypatch.setattr(net.urllib.request, "urlopen", fake_urlopen)
    base = ["osm", "--feature", "fuel", "--around", "36.8,10.18,1000", "--limit", "1000", "--show", "200",
            "--case-dir", str(tmp_path)]
    assert cli.main(base) == 0
    o1 = json.loads(capsys.readouterr().out)
    assert cli.main(base + ["--page", "3"]) == 0
    o3 = json.loads(capsys.readouterr().out)
    assert len(calls) == 1 and len(o1["results"]) == 200 and len(o3["results"]) == 50
    assert "page 3 of 3" in o3["note"] and "(cached)" in o3["note"] and "--page 2" in o1["note"]
    assert cli.main(base + ["--page", "4"]) == 2


def test_osm_unavailable_prints_fallback_links(monkeypatch, capsys, tmp_path):
    from orith import net

    def boom(*a, **k):
        e = net.NetError("HTTP 502 from overpass-api.de")
        e.status = 502
        raise e
    monkeypatch.setattr(osm, "run", boom)
    code = cli.main(["osm", "--feature", "mosque", "--around", "36.8,10.18,1000", "--case-dir", str(tmp_path)])
    o = json.loads(capsys.readouterr().out)
    assert code == 2 and o["overpass_unavailable"] is True and "says nothing about the place" in o["meaning"]
    assert o["overpass_turbo_with_query"].startswith("https://overpass-turbo.eu/?Q=") and o["map_links"]


def test_translit_hits_are_labelled_fuzzy_unless_the_typed_name_is_there():
    els = [{"all_names": {"name": "Jamaa Boumasour"}}, {"all_names": {"name:fr": "Mosquée Bou Messouer"}}]
    osm.label_matches(els, {"name": "Boumasour", "translit": True})
    assert [e["match"] for e in els] == ["exact", "fuzzy"] and els[1]["matched_key"] == "name:fr"
    els = [{"all_names": {"name": "Khuang Sing"}}]
    assert osm.label_matches(els, {"name": "khuang"})[0]["match"] == "exact"
