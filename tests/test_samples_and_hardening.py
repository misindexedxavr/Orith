"""End to end on the sample files in tests/samples (made by tools/make_samples.py), and every bad input the CLI
must survive without a traceback: one line on stderr (or {"error"} with --json) and a sensible exit code."""
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from orith import cli, display, inputs, net, paths, scan, triage

SAMPLES = Path(__file__).parent / "samples"


def run(capsys, *argv):
    code = cli.main([*argv, "--json"])
    cap = capsys.readouterr()
    return code, (json.loads(cap.out) if cap.out.strip() else None), cap.err


def text_run(capsys, monkeypatch, *argv):
    monkeypatch.delenv(f"{paths.ENV}_JSON", raising=False)
    code = cli.main([*argv, "--no-color"])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# ------------------------------------------------------------------ the samples, end to end

def test_gps_sample_gives_a_medium_guess_with_its_country(capsys):
    code, o, _ = run(capsys, str(SAMPLES / "gps.jpg"))
    g = o["best_guess"]
    assert code == 0 and g["confidence"] == "MEDIUM" and g["point"] == pytest.approx([48.865667, 2.321], abs=1e-5)
    assert g["country"].startswith("FR") and o["address"]["country"]["iso2"] == "FR"
    assert {c["result"] for c in o["checks"]} <= {"ok", "info"}  # clocks, zone and sun agree
    assert o["ledger"]["verdict"] == "CANDIDATE"  # GPS alone is never an identification
    assert any(e["confidence"] == "MEDIUM" and e["clue"].startswith("GPS 48.865667") for e in o["evidence_ranked"])


def test_stripped_sample_says_so_and_guesses_nothing(capsys):
    code, o, _ = run(capsys, str(SAMPLES / "stripped.jpg"))
    assert code == 0 and o["best_guess"]["point"] is None and o["best_guess"]["confidence"] == "none"
    assert o["usable"] is False and any("no metadata" in e["clue"] for e in o["evidence_ranked"])
    assert any("lens.google.com" in x for x in o["leads"])


def test_text_sample_with_typed_text_narrows_to_one_country(capsys):
    code, o, _ = run(capsys, str(SAMPLES / "text.png"), "--text", "PHARMACIE CENTRALE", "--text",
                     "Tel: +33 1 42 60 00 00", "--text", "www.pharmacie-exemple.fr", "--drives", "right")
    g = o["best_guess"]
    assert code == 0 and g["country"] == "FR France" and g["country_confidence"] == "MEDIUM"
    assert g["point"] is None  # a country is not a position: no point is invented
    conf = {e["clue"].split(" ")[0]: e["confidence"] for e in o["evidence_ranked"]}
    assert conf["phone"] == "MEDIUM" and conf["Latin"] == "LOW"


def test_corrupt_sample_keeps_its_metadata_and_warns(capsys):
    code, o, err = run(capsys, str(SAMPLES / "corrupt.jpg"))
    assert code == 0 and "damaged or truncated" in o["warning"]
    assert o["best_guess"]["point"] == pytest.approx([48.865667, 2.321], abs=1e-5)


def test_non_image_sample_is_one_line_exit_2(capsys, monkeypatch):
    code, out, err = text_run(capsys, monkeypatch, str(SAMPLES / "not_an_image.jpg"))
    assert code == 2 and out == "" and err.startswith("error: not an image orith can read") and err.count("\n") == 1


def test_default_view_has_the_three_sections_and_a_confidence_per_line(capsys, monkeypatch):
    code, out, err = text_run(capsys, monkeypatch, str(SAMPLES / "gps.jpg"))
    assert code == 0 and "[+] Reading the file" in err  # progress on stderr
    assert out.index("VERDICT") < out.index("EVIDENCE FOUND") < out.index("LEADS TO CHECK MANUALLY")
    assert "confidence   MEDIUM" in out and "identified?  no" in out
    assert "openstreetmap.org/?mlat=48.865667" in out and "tineye.com" in out


def test_samples_carry_no_personal_data():
    for f in SAMPLES.iterdir():
        t = triage.triage(f)
        cam = t.get("camera") or {}
        assert not t.get("xmp") and not t.get("iptc")
        assert set(cam.get(k, "") for k in ("Make", "Model")) <= {"SampleCam", "Model 1", ""}
        assert not [k for k in (t.get("exif") or {}) if k in ("Artist", "Copyright", "OwnerName", "CameraOwnerName",
                                                              "BodySerialNumber", "ImageUniqueID")]


# ------------------------------------------------------------------ bad input: never a traceback

@pytest.mark.parametrize("name,content,expect", [
    ("missing.jpg", None, "file not found"),
    ("empty.png", b"", "empty file"),
    ("note.txt", b"just text", "not an image"),
    ("doc.pdf", b"%PDF-1.4 ...", "looks like pdf"),
])
def test_bad_files_exit_2_with_one_line(tmp_path, capsys, monkeypatch, name, content, expect):
    f = tmp_path / name
    if content is not None:
        f.write_bytes(content)
    code, out, err = text_run(capsys, monkeypatch, str(f))
    assert code == 2 and expect in err and "Traceback" not in err and err.count("\n") == 1


def test_huge_file_is_refused_before_reading(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(f"{paths.ENV}_MAX_MB", "0.01")
    code, o, _ = run(capsys, str(SAMPLES / "gps.jpg"))
    assert code == 2 and "over the 0 MB limit" in o["error"]


def test_a_directory_with_no_images_and_garbage_png(tmp_path, capsys):
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)  # PNG signature, nothing decodable
    code, o, _ = run(capsys, str(fake), "--case-dir", str(tmp_path / "c"))
    assert code == 0 and "damaged" in o["warning"]


def test_unexpected_error_is_one_line_exit_1_and_v_shows_the_traceback(tmp_path, capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("simulated bug")
    monkeypatch.setattr(scan, "run", boom)
    code, out, err = text_run(capsys, monkeypatch, str(SAMPLES / "gps.jpg"), "--case-dir", str(tmp_path))
    assert code == 1 and err.strip() == "error: unexpected RuntimeError: simulated bug (run again with -v for details)"
    code, out, err = text_run(capsys, monkeypatch, str(SAMPLES / "gps.jpg"), "--case-dir", str(tmp_path), "-v")
    assert code == 1 and "Traceback" in err


def test_ctrl_c_exits_130(tmp_path, capsys, monkeypatch):
    def interrupt(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(scan, "run", interrupt)
    code, out, err = text_run(capsys, monkeypatch, str(SAMPLES / "gps.jpg"), "--case-dir", str(tmp_path))
    assert code == 130 and err.strip() == "error: interrupted"


def test_case_name_cannot_escape_the_cases_folder(tmp_path, capsys):
    code, o, _ = run(capsys, str(SAMPLES / "stripped.jpg"), "--case", "../../evil", "--case-dir", str(tmp_path / "c"))
    assert code == 0 and o["case"] == "evil" and (tmp_path / "c" / "evil" / "case.json").exists()
    assert not (tmp_path / "evil").exists() and inputs.safe_case_id("..") == "case"


# ------------------------------------------------------------------ GPS sanity

def _gps_image(path, lat, lon, lat_ref="N", lon_ref="E"):
    ex = Image.Exif()
    g = ex.get_ifd(0x8825)
    g[1], g[2], g[3], g[4] = lat_ref, lat, lon_ref, lon
    Image.new("RGB", (32, 32), (40, 90, 140)).save(path, exif=ex)
    return path


def test_dms_to_decimal_with_south_and_west():
    gps = triage.gps_facts({1: "S", 2: [33.0, 52.0, 4.8], 3: "W", 4: [151.0, 12.0, 36.0]})
    assert gps["lat"] == pytest.approx(-33.868) and gps["lon"] == pytest.approx(-151.21)


@pytest.mark.parametrize("lat,lon,problem", [
    ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), "Null Island"),
    ((95.0, 0.0, 0.0), (10.0, 0.0, 0.0), "outside -90..90"),
    ((10.0, 75.0, 0.0), (10.0, 0.0, 0.0), "not a valid degrees"),
])
def test_impossible_gps_is_dropped_and_reported(tmp_path, capsys, lat, lon, problem):
    img = _gps_image(tmp_path / "g.jpg", lat, lon)
    gps = triage.triage(img)["gps"]
    assert gps["lat"] is None and gps["lon"] is None and any(problem in p for p in gps["problems"])
    code, o, _ = run(capsys, str(img), "--case-dir", str(tmp_path / "c"))
    assert code == 0 and o["best_guess"]["point"] is None
    assert any(e["confidence"] == "PROBLEM" and problem in e["clue"] for e in o["evidence_ranked"])


# ------------------------------------------------------------------ untrusted metadata, network

def test_escape_sequences_in_metadata_never_reach_the_terminal():
    st = display.Style()
    assert st.t("Canon\x1b]0;pwned\x07 EOS‮") == "Canon ]0;pwned  EOS "


def test_online_failure_is_reported_as_skipped_not_a_crash(tmp_path, capsys, monkeypatch):
    def offline(*a, **k):
        raise net.NetError("URLError contacting nominatim.openstreetmap.org: no network")
    monkeypatch.setattr(net, "fetch_json", offline)
    code, o, _ = run(capsys, str(SAMPLES / "gps.jpg"), "--online", "--place", "Nowhereville",
                     "--case-dir", str(tmp_path / "c"))
    assert code == 0 and o["address"]["online"] is None and o["address"]["country"]["iso2"] == "FR"
    assert any("reverse geocoding" in s and "no network" in s for s in o["skipped"])
    assert any("Nowhereville" in s for s in o["skipped"]) and o["places_not_found"] == ["Nowhereville"]


def test_online_without_contact_says_how_to_set_it(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(f"{paths.ENV}_CONTACT", raising=False)
    monkeypatch.setattr(net, "config", lambda: {})
    code, o, _ = run(capsys, str(SAMPLES / "gps.jpg"), "--online", "--case-dir", str(tmp_path / "c"))
    assert code == 0 and any("no contact set" in s and f"{paths.ENV}_CONTACT" in s for s in o["skipped"])


def test_no_online_wins_over_online(tmp_path, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(net, "fetch_json", lambda *a, **k: called.append(1))
    code, o, _ = run(capsys, str(SAMPLES / "gps.jpg"), "--online", "--no-online", "--case-dir", str(tmp_path / "c"))
    assert code == 0 and not called and any("--no-online" in s for s in o["skipped"])


def test_reverse_geocoding_reads_a_nominatim_answer(tmp_path, monkeypatch):
    from orith import geocode
    answer = {"display_name": "Place de la Concorde, Paris, France", "osm_type": "way", "osm_id": 1,
              "address": {"road": "Place de la Concorde", "city": "Paris", "country": "France", "country_code": "fr"}}
    seen = {}

    def fake(url, **kw):
        seen.update(url=url, **kw)
        return answer, False
    monkeypatch.setattr(net, "fetch_json", fake)
    r, cached = geocode.reverse_online(48.865667, 2.321, timeout=15, tries=2)
    assert r["city"] == "Paris" and r["country_code"] == "FR" and "/reverse?" in seen["url"] and seen["timeout"] == 15


def test_heic_without_the_optional_decoder_says_how_to_get_it(tmp_path, capsys, monkeypatch):
    if inputs.heif_available():
        pytest.skip("pillow-heif is installed")
    f = tmp_path / "IMG_0001.HEIC"
    f.write_bytes(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64)
    code, o, _ = run(capsys, str(f), "--case-dir", str(tmp_path / "c"))
    assert code == 0 and 'orith[heic]' in o["warning"]


def test_folder_of_samples_is_triaged(tmp_path, capsys):
    d = tmp_path / "photos"
    shutil.copytree(SAMPLES, d)
    code, o, _ = run(capsys, str(d))
    by = {r["file"]: r for r in o["images"]}
    assert code == 0 and o["counts"]["images"] == 4 and o["counts"]["errors"] == 1
    assert by["not_an_image.jpg"]["error"] == "not an image orith can read"
    assert "damaged image data" in by["corrupt.jpg"]["flags"] and by["gps.jpg"]["country"] == "FR"
    assert ["gps.jpg", "stripped.jpg"] in o["duplicates"]  # the same picture with and without metadata
