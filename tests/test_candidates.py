"""Candidate tests: pass/fail/unknown per hypothesis, recorded only through the ledger."""
import datetime as dt
import json

from orith import candidates, cli, geo, ledger, sun

UTC = dt.UTC
T = dt.datetime(2024, 6, 21, 15, 0, tzinfo=UTC)
TUNIS, OSLO = (36.80, 10.18), (59.91, 10.75)


def make_case(tmp_path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"x")
    path = tmp_path / "c" / "case.json"
    c = ledger.new_case(img)
    ledger.add_hypothesis(c, "Tunis", *TUNIS)
    ledger.add_hypothesis(c, "Oslo", *OSLO)
    ledger.add_hypothesis(c, "Sea", 35.0, 15.0)
    ledger.save(c, path)
    return path


def test_sun_pass_fail_and_time_error():
    s = sun.shadow(*TUNIS, T)
    assert candidates.sun_test(*TUNIS, T, shadow_bearing=s["shadow_bearing"])["result"] == "pass"
    assert candidates.sun_test(*OSLO, T, shadow_bearing=s["shadow_bearing"], elevation=s["sun_elevation"])["result"] == "fail"
    late = T + dt.timedelta(minutes=20)  # clock 20 min off: fails strictly, passes with a 25-minute time error
    assert candidates.sun_test(*TUNIS, late, shadow_bearing=s["shadow_bearing"], tolerance=1)["result"] == "fail"
    assert candidates.sun_test(*TUNIS, late, shadow_bearing=s["shadow_bearing"], tolerance=1, time_error_min=25)["result"] == "pass"
    assert candidates.sun_test(*TUNIS, None, shadow_bearing=10)["result"] == "unknown"


def test_bearing_and_distance():
    lm = (36.81, 10.19)
    b = geo.bearing(*TUNIS, *lm)
    assert candidates.bearing_test(*TUNIS, lm, b + 3)["result"] == "pass"
    assert candidates.bearing_test(*OSLO, lm, b)["result"] == "fail"
    assert candidates.distance_test(*TUNIS, lm, max_km=2)["result"] == "pass"
    assert candidates.distance_test(*OSLO, lm, max_km=2)["result"] == "fail"
    assert candidates.distance_test(*TUNIS, lm)["result"] == "unknown"


def test_tz_offset_against_the_point_country():
    assert candidates.tz_test(*TUNIS, T, "+01:00")["result"] == "pass"
    r = candidates.tz_test(*TUNIS, T, "+02:00")
    assert r["result"] == "fail" and r["zone_offsets_min"] == [60]
    assert candidates.tz_test(35.0, 15.0, T, "+01:00")["result"] == "unknown"  # open sea: no country outline


def test_recording_soft_fail_keeps_hypothesis_alive_and_blocks_identified(tmp_path):
    path = make_case(tmp_path)
    s = sun.shadow(*TUNIS, T)
    out = candidates.run(path, "sun", {"time": T, "shadow_bearing": s["shadow_bearing"]})
    c = ledger.load(path)
    assert len(c["hypotheses"]) == 3 and len(c["clues"]) == 1
    assert {(l["hypothesis"], l["relation"]) for l in c["links"]} == {("h1", "supports"), ("h2", "contradicts"), ("h3", "contradicts")}
    ev = {r["id"]: r for r in ledger.evaluate(c)["ranking"]}
    assert ev["h2"]["status"] == "alive" and ev["h2"]["score"] < 0 and ev["h1"]["score"] > 0
    assert out["recorded"]["clue"] == "c1" and (tmp_path / "c" / "tests.jsonl").exists()


def test_hard_reliable_fail_eliminates_but_never_deletes(tmp_path):
    path = make_case(tmp_path)
    candidates.run(path, "tz", {"time": T, "utc_offset": "+01:00"}, kind="HARD", u=3, p=5, r=5)
    c = ledger.load(path)
    ev = {r["id"]: r for r in ledger.evaluate(c)["ranking"]}
    assert len(c["hypotheses"]) == 3 and ev["h2"]["status"] == "eliminated" and ev["h1"]["status"] == "alive"


def test_cli_dry_run_records_nothing(tmp_path, capsys):
    path = make_case(tmp_path)
    code = cli.main(["test", "distance", "--case", "c", "--case-dir", str(tmp_path), "--landmark", "36.81,10.19",
                     "--max-km", "2", "--dry-run"])
    o = json.loads(capsys.readouterr().out)
    assert code == 0 and o["recorded"] is None and "contradicts" in o["effect_on_verdict"]
    assert ledger.load(path)["clues"] == []

def test_landmark_from_recorded_evidence_becomes_fixed_geometry(tmp_path):
    path = make_case(tmp_path)
    c = ledger.load(path)
    near = geo.destination(*TUNIS, 45, 0.15)  # a mapped landmark 150 m from h1
    ev = ledger.add_evidence(c, "osm", *near, name="minaret", ref="node/1")
    ledger.save(c, path)
    b = geo.bearing(*TUNIS, *near)
    out = candidates.run(path, "bearing", {"landmark": "e1", "bearing": b}, hyps=["h1"])
    clue = ledger.load(path)["clues"][0]
    assert clue["evidence"] == ev["id"] and clue["geometry"] is True and out["recorded"]["geometry"]
    row = next(r for r in ledger.evaluate(ledger.load(path))["ranking"] if r["id"] == "h1")
    assert row["agreeing_clues"] == ["c1"] and row["geometry_agreeing"] == ["c1"]
    hand = candidates.run(path, "distance", {"landmark": (36.9, 10.3), "max_km": 50}, hyps=["h1"])
    assert hand["recorded"]["evidence"] is None and ledger.load(path)["clues"][1]["geometry"] is False


def test_sun_exif_without_offset_is_unknown_unless_a_zone_is_given(tmp_path, capsys):
    path = make_case(tmp_path)
    s = sun.shadow(*TUNIS, T)
    base = ["test", "sun", "--case", "c", "--case-dir", str(tmp_path), "--shadow-bearing", str(s["shadow_bearing"]),
            "--time", "2024-06-21T16:00:00", "--time-source", "exif-no-offset", "--hyp", "h1"]
    assert cli.main(base) == 0
    o = json.loads(capsys.readouterr().out)
    assert o["results"][0]["result"] == "unknown" and o["time_source"] == "exif-no-offset"
    assert ledger.load(path)["links"] == []  # unknown: no link
    assert cli.main(base + ["--tz", "Africa/Tunis"]) == 0  # 16:00 local = 15:00 UTC
    o = json.loads(capsys.readouterr().out)
    assert o["results"][0]["result"] == "pass" and "Africa/Tunis" in o["time_source"]


def test_sun_requires_a_time_source(tmp_path, capsys):
    make_case(tmp_path)
    code = cli.main(["test", "sun", "--case", "c", "--case-dir", str(tmp_path), "--shadow-bearing", "10",
                     "--time", "2024-06-21T15:00:00Z"])
    assert code == 2 and "time-source" in json.loads(capsys.readouterr().out)["error"]
