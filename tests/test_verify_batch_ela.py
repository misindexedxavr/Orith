"""Consistency checks, folder triage (duplicates, flags, GeoJSON), ELA and case export. Synthetic images only."""
import json

from orith import batch, cli, ela, paths, triage, verify


def make_jpeg(p, dto="2025:06:21 16:30:00", off="+01:00", gpstime=((15.0, 30.0, 0.0), "2025:06:21"), sw=None,
              q=92, lat=(33.0, 55.0, 0.0), lon=(8.0, 7.0, 0.0), patch=None):
    from PIL import Image
    e = Image.Exif()
    e[0x010F], e[0x0110] = "DemoCam", "X1"
    if sw:
        e[0x0131] = sw
    i = e.get_ifd(0x8769)
    i[0x9003] = dto
    if off:
        i[0x9011] = off
    g = e.get_ifd(0x8825)
    g[1], g[2], g[3], g[4] = "N", lat, "E", lon
    if gpstime:
        g[7], g[29] = gpstime
    im = Image.new("RGB", (240, 180), (120, 140, 90))
    for x in range(60, 140):
        for y in range(40, 110):
            im.putpixel((x, y), (200, 60, 60))
    if patch:
        patch(im)
    im.save(p, exif=e.tobytes(), quality=q)
    return p


def test_consistent_file_passes_and_tampered_file_is_caught(tmp_path):
    good, _ = verify.checks(triage.triage(make_jpeg(tmp_path / "good.jpg")))
    assert {c["check"]: c["result"] for c in good}["GPS time vs capture time"] == "ok"
    assert {c["check"]: c["result"] for c in good}["time zone at the GPS point"] == "ok"
    bad, _ = verify.checks(triage.triage(make_jpeg(tmp_path / "bad.jpg", dto="2025:06:21 03:00:00", off="+05:00",
                                                   gpstime=((22.0, 0.0, 0.0), "2025:06:21"), sw="Adobe Photoshop 25.0")))
    res = {c["check"]: c["result"] for c in bad}
    assert res["GPS time vs capture time"] == "mismatch" and res["time zone at the GPS point"] == "mismatch"
    assert res["daylight at the GPS point"] == "warning" and res["editing software"] == "warning"


def test_gps_clock_reveals_the_utc_offset(tmp_path):
    chk, implied = verify.checks(triage.triage(make_jpeg(tmp_path / "x.jpg", off=None)))
    assert implied == {"utc_offset": "+01:00", "from": "GPS time vs local capture time"}
    assert any(c["check"] == "UTC offset from the GPS clock" for c in chk)


def test_folder_scan_groups_duplicates_ignores_flat_images_and_writes_geojson(tmp_path, capsys, monkeypatch):
    from PIL import Image
    d = tmp_path / "imgs"
    d.mkdir()
    make_jpeg(d / "a.jpg")
    make_jpeg(d / "b_resaved.jpg", q=60)
    make_jpeg(d / "c.jpg", patch=lambda im: [im.putpixel((x, y), ((x * 7 + y * 3) % 256, (y * 5) % 256, (x * 11) % 256))
                                             for x in range(240) for y in range(180)])  # a different picture
    Image.new("RGB", (64, 48), (10, 200, 30)).save(d / "flat1.png")
    Image.new("RGB", (64, 48), (200, 10, 30)).save(d / "flat2.png")
    r = batch.run(d)
    assert r["counts"]["images"] == 5 and r["counts"]["with_gps"] == 3
    assert r["duplicates"] == [["a.jpg", "b_resaved.jpg"]]
    monkeypatch.delenv(f"{paths.ENV}_JSON", raising=False)
    gj = tmp_path / "pts.geojson"
    assert cli.main([str(d), "--geojson", str(gj), "--no-color", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "NEAR-DUPLICATES" in out and len(json.loads(gj.read_text(encoding="utf-8"))["features"]) == 3


def test_ela_points_at_an_area_saved_at_a_different_quality(tmp_path):
    from PIL import Image
    p = tmp_path / "e.jpg"
    base = Image.new("RGB", (240, 240), (128, 128, 128))
    for x in range(0, 240, 2):  # textured background saved once at low quality
        for y in range(240):
            base.putpixel((x, y), (140, 140, 140))
    base.save(p, quality=60)
    im = Image.open(p).convert("RGB")
    for x in range(160, 220):  # a fresh, noisy patch pasted afterwards
        for y in range(160, 220):
            im.putpixel((x, y), ((x * 37) % 255, (y * 91) % 255, (x * y) % 255))
    im.save(p, quality=95)
    r = ela.run(p, out=tmp_path / "e_ela.png")
    assert (tmp_path / "e_ela.png").exists() and r["format"] == "JPEG"
    assert r["hot_cells"] and r["hot_cells"][0]["row"] >= 6 and r["hot_cells"][0]["col"] >= 6


def test_case_export_writes_hypotheses_and_file_gps(tmp_path, capsys):
    img = make_jpeg(tmp_path / "g.jpg")
    cases = tmp_path / "c"
    assert cli.main([str(img), "--case-dir", str(cases), "--json", "--no-ela"]) == 0
    capsys.readouterr()
    dst = tmp_path / "g.geojson"
    assert cli.main(["tools", "export", "--case", "g", "--case-dir", str(cases), "--out", str(dst), "--json"]) == 0
    kinds = [f["properties"]["kind"] for f in json.loads(dst.read_text(encoding="utf-8"))["features"]]
    assert "hypothesis" in kinds and "file GPS" in kinds
