"""File fingerprint (name time, JPEG tables, hashes) and the optional building layer (synthetic tile)."""
import json

from orith import buildings, cli, fingerprint, ledger, paths


def test_file_name_time_patterns():
    cases = {"IMG_20250621_163000.jpg": "2025-06-21T16:30:00", "PXL_20250621_163000123.jpg": "2025-06-21T16:30:00",
             "WhatsApp Image 2025-06-21 at 16.30.00.jpeg": "2025-06-21T16:30:00",
             "Screenshot_2025-06-21-16-30-00.png": "2025-06-21T16:30:00", "scan_2025-06-21.jpg": "2025-06-21"}
    for name, want in cases.items():
        assert fingerprint.file_name_time(name)["local"] == want, name
    assert fingerprint.file_name_time("IMG_9999.jpg") is None and fingerprint.file_name_time("2025_13_45.jpg") is None


def test_jpeg_quality_is_read_from_the_standard_tables(tmp_path):
    from PIL import Image
    p = tmp_path / "a.jpg"
    Image.new("RGB", (64, 48), (200, 170, 120)).save(p, quality=83, progressive=True)
    j = fingerprint.jpeg(p)
    assert j["quality"] == 83 and j["standard_table"] is True and j["progressive"] is True
    assert len(fingerprint.hashes(p)["dhash"]) == 16


def _tile(tmp_path):
    # Tozeur area, inside tile e005_n35_e010_n30: two Google ids with Plus Codes, one id without a position
    recs = {"google8F5FWVF7+M8XXTUN": {"height": 4.2, "var": 0.1}, "google8F5FWVF7+M9XXTUN": {"height": -999.0, "var": -999.0},
            "msTunisia_1_2TUN": {"height": 3.0, "var": 0.2}}
    src = tmp_path / "e005_n35_e010_n30.json"
    src.write_text(json.dumps(recs), encoding="utf-8")
    return buildings.build_index(src, out_dir=tmp_path / "idx", log=lambda s: None)


def test_index_decodes_plus_codes_and_counts_records_without_position(tmp_path):
    m = _tile(tmp_path)
    assert m["counts"] == {"records": 3, "located": 2, "no_position_in_id": 1, "outside_tile": 0, "with_height": 1}
    lat, lon = buildings.decode10("8F5FWVF7", "M8")
    r = buildings.near(lat, lon, 100, folder=tmp_path / "idx")
    assert r["covered"] and r["count"] == 2 and r["nearest_m"] < 20 and r["height_m"]["median"] == 4.2
    assert buildings.near(36.8, 10.18, 500, folder=tmp_path / "idx") == {"covered": False, "radius_m": 500}


def test_buildings_tool_prints_text_for_a_point_and_the_list(tmp_path, capsys, monkeypatch):
    _tile(tmp_path)
    monkeypatch.setenv(f"{paths.ENV}_BUILDINGS", str(tmp_path / "idx"))
    monkeypatch.delenv(f"{paths.ENV}_JSON", raising=False)
    lat, lon = buildings.decode10("8F5FWVF7", "M8")
    assert cli.main(["tools", "buildings", f"{lat},{lon}", "--radius", "100"]) == 0
    out = capsys.readouterr().out
    assert "2 within 100 m" in out and "ML" in out
    assert cli.main(["tools", "buildings", "--list"]) == 0
    assert "e005_n35_e010_n30" in capsys.readouterr().out


def test_scan_uses_the_building_layer_and_progress_stays_out_of_json(tmp_path, capsys, monkeypatch):
    from PIL import Image
    _tile(tmp_path)
    monkeypatch.setenv(f"{paths.ENV}_BUILDINGS", str(tmp_path / "idx"))
    monkeypatch.delenv(f"{paths.ENV}_JSON", raising=False)
    img = tmp_path / "IMG_20250621_163000.jpg"
    Image.new("RGB", (64, 48), (90, 120, 90)).save(img)
    lat, lon = buildings.decode10("8F5FWVF7", "M8")
    argv = [str(img), "--lat", str(lat), "--lon", str(lon), "--setting", "village", "--case-dir", str(tmp_path / "c")]
    assert cli.main(argv + ["--json"]) == 0
    cap = capsys.readouterr()
    o = json.loads(cap.out)  # stdout is pure JSON; no progress lines in --json mode
    assert "[+]" not in cap.out and "[+]" not in cap.err
    assert o["buildings"][0]["count"] == 2 and o["buildings"][0]["setting_match"] is False  # 2 < 10: not a village
    assert o["file"]["name_time"]["local"] == "2025-06-21T16:30:00"
    case = ledger.load(tmp_path / "c" / img.stem / "case.json")
    setting = [c for c in case["clues"] if c["group"] == "setting"]
    assert setting and setting[0]["kind"] == "SOFT" and not [l for l in case["links"] if l["clue"] == setting[0]["id"]]
    assert cli.main(argv + ["--no-color", "-v"]) == 0  # text mode: progress on stderr, report on stdout
    cap = capsys.readouterr()
    assert "[+] Counting buildings near each hypothesis" in cap.err and "HYPOTHESES (RANKED)" in cap.out
