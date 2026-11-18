"""quick, links, timer and the guided checklist. No browser, no network."""
import json

import pytest

from orith import cli, ledger, links, workflow
from tests.test_triage import exif_with, insert_segments, jpeg_bytes, scene


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_links_print_only_and_carry_the_point():
    lk = links.all_links(36.87, 10.3414, utc_iso="2024-06-21T12:30:00Z")
    assert "never opens a browser" in lk["note"]
    urls = [u for group in ("maps", "street_level", "imagery_and_terrain", "tools") for u in lk[group].values()]
    assert all(u.startswith("https://") for u in urls)
    assert all(("36.87" in u and "10.341" in u) for u in urls)
    assert lk["tools"]["SunCalc (sun and shadows here)"].endswith("/2024.06.21/12:30/1/3")
    assert "point" not in links.all_links() and links.all_links()["reverse_image_search"]


def test_links_module_cannot_open_anything():
    import inspect
    src = inspect.getsource(links)
    for banned in ("webbrowser", "urlopen", "subprocess", "os.system", "startfile"):
        assert banned not in src


def test_timer_start_lap_stop_and_elapsed(tmp_path):
    d = tmp_path / "case"
    workflow.timer(d, "start", now=1000.0)
    workflow.timer(d, "lap", "country", now=1065.0)
    s = workflow.timer(d, "stop", now=1200.0)
    assert s["running"] is False and s["elapsed_s"] == 200.0 and s["elapsed"] == "0:03:20"
    assert [l["label"] for l in s["laps"]] == ["country", "stop"] and s["laps"][0]["lap_s"] == 65.0
    with pytest.raises(ledger.LedgerError):
        workflow.timer(d, "lap", now=1300.0)
    workflow.timer(d, "start", now=2000.0)
    assert workflow.timer(d, "show", now=2030.0)["elapsed_s"] == 230.0


def test_checklist_records_through_the_ledger(tmp_path):
    img = tmp_path / "img.jpg"
    img.write_bytes(b"x")
    path = tmp_path / "c" / "case.json"
    ledger.save(ledger.new_case(img), path)
    answers = iter(["Arabic and French road sign", "drives on the right", "",  # inventory
                    "HARD 4 5 5 script", "SOFT 2 5 4 traffic",                 # rate
                    "Tunis | 36.80, 10.18", "Algiers | 36.75, 3.06", "Rabat | 34.02, -6.84", "bad line", "",  # hyps
                    "s h1 h2 h3", "s h1 c h3",                                 # discriminate
                    "checked Overpass for the sign text near h1", ""])          # narrow
    inp = lambda prompt: next(answers)  # noqa: E731
    sink = []
    workflow.run_step(path, "inventory", inp, sink.append)
    r = workflow.run_step(path, "rate", inp, sink.append)
    assert r["recorded"] == ["c1", "c2"]
    r = workflow.run_step(path, "hypotheses", inp, sink.append)
    assert r["recorded"] == ["h1", "h2", "h3"] and any("skipped" in m for m in sink)
    workflow.run_step(path, "discriminate", inp, sink.append)
    r = workflow.run_step(path, "narrow", inp, sink.append)
    case = ledger.load(path)
    assert {(l["clue"], l["hypothesis"], l["relation"]) for l in case["links"]} == {
        ("c1", "h1", "supports"), ("c1", "h2", "supports"), ("c1", "h3", "supports"),
        ("c2", "h1", "supports"), ("c2", "h3", "contradicts")}
    assert r["completed_steps"] == ["inventory", "rate", "hypotheses", "discriminate", "narrow"]
    assert r["next_step"] == "geometry"
    ev = ledger.evaluate(case)
    assert ev["verdict"] == "CANDIDATE" and ev["top"] == "h1"  # appearance clues alone never identify
    notes = json.loads((tmp_path / "c" / "checklist.json").read_text(encoding="utf-8"))
    assert notes["notes"]["narrow"] == ["checked Overpass for the sign text near h1"]


def test_checklist_audit_shows_the_unmet_conditions(tmp_path):
    img = tmp_path / "img.jpg"
    img.write_bytes(b"x")
    path = tmp_path / "c" / "case.json"
    ledger.save(ledger.new_case(img), path)
    sink = []
    workflow.run_step(path, "audit", lambda p: "", sink.append)
    assert any(m.startswith("verdict: CANDIDATE") for m in sink) and any("unmet" in m for m in sink)


def test_quick_on_an_image_with_gps(tmp_path, capsys):
    main = scene((30, 90, 200), (200, 40, 40))
    data = insert_segments(jpeg_bytes(main), [(0xE1, b"Exif\x00\x00" + exif_with(main.resize((160, 120))))])
    img = tmp_path / "photo.jpg"
    img.write_bytes(data)
    code, o = run(capsys, "quick", str(img), "--case-dir", str(tmp_path / "cases"))
    assert code == 0 and o["case"] == "photo" and o["case_created"] is True
    assert o["facts"]["gps"]["lat"] == pytest.approx(36.8700, abs=1e-3) and o["timer"]["running"] is True
    assert "EXIF GPS" in o["links"]["from"] and "Google Street View" in o["links"]["street_level"]
    assert any("destination" in n for n in o["next"])  # GPSImgDirection present -> bearing line suggestion
    assert (tmp_path / "cases" / "photo" / "triage.json").exists()
    assert (tmp_path / "cases" / "photo" / "extracted" / "embedded_thumbnail.jpg").exists()
    code, o2 = run(capsys, "quick", str(img), "--case-dir", str(tmp_path / "cases"))
    assert o2["case_created"] is False and o2["timer"]["running"] is True  # second call continues the case


def test_quick_without_gps_gives_reverse_search_only(tmp_path, capsys):
    img = tmp_path / "plain.jpg"
    img.write_bytes(jpeg_bytes(scene((1, 1, 1), (2, 2, 2))))
    code, o = run(capsys, "quick", str(img), "--case-dir", str(tmp_path / "cases"))
    assert code == 0 and "point" not in o["links"] and o["links"]["reverse_image_search"]


def test_cli_links_and_checklist_plan(capsys):
    code, o = run(capsys, "links", "36°52'12\"N 10°20'29\"E")
    assert code == 0 and o["point"][0] == pytest.approx(36.87, abs=1e-3)
    if not hasattr(cli, "cmd_checklist"):
        return  # public build: the checklist command is a development-only helper
    code, o = run(capsys, "checklist")
    assert code == 0 and [s["step"] for s in o["steps"]] == workflow.STEP_IDS
