"""Step 9b: one-call recording, evidence hints, rank view, geocode field names, EXIF offset handling."""
import json

from orith import cli, geocode, ledger, osm, triage


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def _case(tmp_path):
    img = tmp_path / "x1.jpg"
    img.write_bytes(b"not really a jpeg")
    cd = ["--case-dir", str(tmp_path / "cases")]
    assert cli.main(["case", "new", str(img), *cd]) == 0
    return cd + ["--case", "x1"], tmp_path / "cases" / "x1" / "case.json"


def test_clue_and_hyp_link_in_one_call_and_bad_id_saves_nothing(tmp_path, capsys):
    cd, path = _case(tmp_path)
    capsys.readouterr()
    for lab, lat in (("A", 10.0), ("B", 11.0), ("C", 12.0)):
        assert run(capsys, "hyp", "add", "--label", lab, "--lat", str(lat), "--lon", "5", *cd)[0] == 0
    code, o = run(capsys, "clue", "add", "--text", "sign", "--kind", "SOFT", "--u", "3", "--p", "4", "--r", "4",
                  "--group", "g", "--supports", "h1,h2", "--contradicts", "h3", *cd)
    assert code == 0 and [(l["hypothesis"], l["relation"]) for l in o["links"]] == [
        ("h1", "supports"), ("h2", "supports"), ("h3", "contradicts")]
    code, o = run(capsys, "hyp", "add", "--label", "D", "--lat", "13", "--lon", "5", "--supported-by", "c1", *cd)
    assert code == 0 and o["links"][0] == {"clue": "c1", "hypothesis": "h4", "relation": "supports", "note": ""}
    before = path.read_text(encoding="utf-8")
    code, o = run(capsys, "clue", "add", "--text", "x", "--kind", "SOFT", "--u", "1", "--p", "1", "--r", "1",
                  "--group", "g2", "--supports", "h1,h9", *cd)
    assert code == 2 and "h9" in o["error"] and path.read_text(encoding="utf-8") == before
    code, o = run(capsys, "hyp", "add", "--label", "E", "--lat", "1", "--lon", "1", "--supported-by", "c1",
                  "--contradicted-by", "c1", *cd)
    assert code == 2 and path.read_text(encoding="utf-8") == before


def test_osm_with_case_prints_evidence_ids_and_next_commands(tmp_path, capsys, monkeypatch):
    cd, _ = _case(tmp_path)
    capsys.readouterr()
    els = [{"lat": 36.8, "lon": 10.18, "name": "Jamaa \"El\" Kebir; old", "precision_m": 10, "ref": "node/1"}]
    monkeypatch.setattr(osm, "run", lambda spec, log=None: (els, "QUERY", False))
    code, o = run(capsys, "osm", "--feature", "mosque", "--around", "36.8,10.18,500", *cd)
    assert code == 0 and o["evidence_ids"] == ["e1"]
    assert o["next"][0] == 'orith hyp add --case x1 --label "Jamaa El Kebir old" --evidence e1'
    assert "--evidence e1" in o["next"][1] and not any(ch in " ".join(o["next"]) for ch in ";|$`")
    code, o = run(capsys, "osm", "--feature", "mosque", "--around", "36.8,10.18,500", "--case-dir", cd[1])
    assert "add --case ID" in o["evidence"] and "next" not in o


def test_rank_shows_relative_score_and_map_anchored_count_verdict_unchanged(tmp_path, capsys):
    cd, path = _case(tmp_path)
    c = ledger.load(path)
    e1 = ledger.add_evidence(c, "osm", 36.8, 10.18, name="mosque", ref="node/1")
    for lab, lat in (("A", 36.8), ("B", 35.0), ("C", 34.0)):
        ledger.add_hypothesis(c, lab, lat, 10.18)
    k1 = ledger.add_clue(c, "minaret", "SOFT", 5, 5, 5, "g1", evidence=e1["id"])
    k2 = ledger.add_clue(c, "palms", "SOFT", 5, 5, 5, "g2")
    for k in (k1, k2):
        ledger.add_link(c, k["id"], "h1", "supports")
    ledger.save(c, path)
    capsys.readouterr()
    code, o = run(capsys, "rank", *cd)
    h1 = o["ranking"][0]
    assert h1["id"] == "h1" and h1["relative_score"] == 2.0 and h1["map_anchored_clues"] == 1 and "score" not in h1
    assert "not a probability" in o["score_note"]
    assert o["verdict"] == ledger.evaluate(ledger.load(path))["verdict"] == "CANDIDATE"
    code, o = run(capsys, "report", *cd)
    assert "relative score +2.000 (map-anchored clues 1)" in o["text"]


def test_geocode_fields_name_what_they_are():
    r = geocode.parse_nominatim([{"lat": "1", "lon": "2", "importance": 0.00001, "display_name": "Campus",
                                  "osm_type": "way", "osm_id": 5}])[0]
    assert r["importance"] == 0.0 and "score" not in r


def test_image_path_alone_runs_quick(tmp_path, capsys, monkeypatch):
    from PIL import Image
    img = tmp_path / "s1.jpg"
    Image.new("RGB", (64, 48), (10, 20, 30)).save(img)
    monkeypatch.setenv(f"{cli.paths.ENV}_CASE_DIR", str(tmp_path / "cases"))
    code, o = run(capsys, str(img))
    assert code == 0 and o["case"] == "s1" and (tmp_path / "cases" / "s1" / "case.json").exists()
    code, o = run(capsys, str(tmp_path / "missing.jpg"))  # one-line error and exit 2, never a traceback
    assert code == 2 and o == {"error": f"file not found: {tmp_path / 'missing.jpg'}"}


def test_quick_next_commands_are_one_per_line_without_chaining(tmp_path, capsys):
    from PIL import Image
    img = tmp_path / "q1.jpg"
    Image.new("RGB", (64, 48), (90, 90, 90)).save(img)
    code, o = run(capsys, "quick", str(img), "--case-dir", str(tmp_path / "cases"))
    assert code == 0 and "orith report --case q1" in o["next"]
    assert not any(ch in n for n in o["next"] for ch in ";|&")


def test_triage_lists_other_offsets_flags_conflict_and_picks_none():
    named = {"Exif": {"DateTimeOriginal": "2025:04:27 16:35:57", "OffsetTime": "-05:00",
                      "OffsetTimeDigitized": "+00:00"}, "IFD0": {"DateTime": "2025:04:28 15:04:14"}}
    t = triage.time_facts(named)
    assert t["utc_from_offset"] is None and t["offset_conflict"] is True
    assert {(o["tag"], o["value"], o["belongs_to"]) for o in t["other_offsets"]} == {
        ("OffsetTimeDigitized", "+00:00", "DateTimeDigitized"), ("OffsetTime", "-05:00", "DateTime (file change)")}
    assert "none was used" in t["note"]
    named["Exif"]["OffsetTimeDigitized"] = "-05:00"
    assert triage.time_facts(named)["offset_conflict"] is False
    named["Exif"]["OffsetTimeOriginal"] = "-05:00"
    assert triage.time_facts(named)["utc_from_offset"] == "2025-04-27T21:35:57Z"
