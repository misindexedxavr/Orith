"""File triage on synthetic files built here byte by byte (no external samples needed)."""
import io
import json
import struct
import zipfile

import pytest
from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

from orith import cli, triage

# ------------------------------------------------------------------ builders

def tiff_le(ifds):
    """Little-endian TIFF. ifds: list of (name, [(tag, type, value)], next_name). Values: int, str, rationals, bytes.

    Pointers to other IFDs are given as ("@", name); the thumbnail as ("@thumb", bytes)."""
    def enc(typ, v):
        if typ == 2:
            b = v.encode() + b"\x00"
            return b, len(b)
        if typ == 5:
            vals = v if isinstance(v, list) else [v]
            return b"".join(struct.pack("<II", n, d) for n, d in vals), len(vals)
        if typ == 3:
            vals = v if isinstance(v, list) else [v]
            return b"".join(struct.pack("<H", x) for x in vals), len(vals)
        if typ == 4:
            return struct.pack("<I", v), 1
        if typ == 1:
            vals = v if isinstance(v, list) else [v]
            return bytes(vals), len(vals)
        if typ == 7:
            return v, len(v)
        raise ValueError(typ)

    # layout: header 8, then each IFD followed by its overflow data, then thumbnail
    offsets, cursor = {}, 8
    plan = []
    for name, entries, nxt in ifds:
        offsets[name] = cursor
        size = 2 + 12 * len(entries) + 4
        extra = 0
        for _tag, typ, v in entries:
            if isinstance(v, tuple) and v and v[0] in ("@", "@thumb"):
                continue
            b, _ = enc(typ, v)
            if len(b) > 4:
                extra += len(b) + (len(b) % 2)
        plan.append((name, entries, nxt, cursor, size))
        cursor += size + extra
    thumb_off = cursor
    thumb = next((v[1] for _, ents, _ in ifds for _, _, v in ents if isinstance(v, tuple) and v[0] == "@thumb"), b"")
    out = bytearray(b"II*\x00" + struct.pack("<I", 8))
    for _name, entries, nxt, start, size in plan:
        assert len(out) == start
        body, overflow = bytearray(struct.pack("<H", len(entries))), bytearray()
        data_at = start + size
        for tag, typ, v in sorted(entries, key=lambda e: e[0]):
            if isinstance(v, tuple) and v[0] == "@":
                body += struct.pack("<HHII", tag, 4, 1, offsets[v[1]])
                continue
            if isinstance(v, tuple) and v[0] == "@thumb":
                body += struct.pack("<HHII", tag, 4, 1, thumb_off)
                continue
            b, count = enc(typ, v)
            if len(b) <= 4:
                body += struct.pack("<HHI", tag, typ, count) + b.ljust(4, b"\x00")
            else:
                body += struct.pack("<HHII", tag, typ, count, data_at + len(overflow))
                overflow += b + (b"\x00" if len(b) % 2 else b"")
        body += struct.pack("<I", offsets[nxt] if nxt else 0)
        out += body + overflow
    out += thumb
    return bytes(out)


def jpeg_bytes(img, **kw):
    b = io.BytesIO()
    img.save(b, "JPEG", quality=85, **kw)
    return b.getvalue()


def insert_segments(jpeg, segments):
    """Insert (marker, payload) segments right after SOI."""
    seg = b"".join(bytes([0xFF, m]) + struct.pack(">H", len(p) + 2) + p for m, p in segments)
    return jpeg[:2] + seg + jpeg[2:]


def scene(color_a, color_b, size=(320, 240)):
    img = Image.new("RGB", size, color_a)
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, size[0] // 2, size[1] - 20], fill=color_b)
    d.ellipse([size[0] // 2 + 20, 40, size[0] - 20, 160], fill=(240, 240, 20))
    return img


def exif_with(thumb_img):
    th = jpeg_bytes(thumb_img)
    return tiff_le([
        ("IFD0", [(0x010F, 2, "TestCam"), (0x0110, 2, "Model X"), (0x8769, 4, ("@", "Exif")),
                  (0x8825, 4, ("@", "GPS"))], "IFD1"),
        ("Exif", [(0x9003, 2, "2024:06:21 14:30:00"), (0x9011, 2, "+02:00"), (0xA405, 3, 28)], None),
        ("GPS", [(0x0001, 2, "N"), (0x0002, 5, [(36, 1), (52, 1), (1200, 100)]), (0x0003, 2, "E"),
                 (0x0004, 5, [(10, 1), (20, 1), (2940, 100)]), (0x0007, 5, [(12, 1), (30, 1), (0, 1)]),
                 (0x001D, 2, "2024:06:21"), (0x0010, 2, "T"), (0x0011, 5, (2705, 10))], None),
        ("IFD1", [(0x0103, 3, 6), (0x0201, 4, ("@thumb", th)), (0x0202, 4, len(th))], None),
    ])


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ------------------------------------------------------------------ tests

def test_exif_gps_time_camera_and_matching_thumbnail(tmp_path):
    main = scene((30, 90, 200), (200, 40, 40))
    data = insert_segments(jpeg_bytes(main), [(0xE1, b"Exif\x00\x00" + exif_with(main.resize((160, 120))))])
    o = triage.triage(write(tmp_path, "a.jpg", data))
    assert o["type"] == "jpeg" and o["camera"]["Make"] == "TestCam"
    assert o["gps"]["lat"] == pytest.approx(36 + 52 / 60 + 12 / 3600, abs=1e-6)
    assert o["gps"]["lon"] == pytest.approx(10 + 20 / 60 + 29.4 / 3600, abs=1e-6)
    assert o["gps"]["image_direction_deg"] == 270.5 and o["gps"]["image_direction_ref"] == "true north"
    assert o["gps"]["gps_utc"] == "2024-06-21T12:30:00.000Z"
    assert o["time"]["utc_from_offset"] == "2024-06-21T12:30:00Z"  # 14:30 local at +02:00
    assert o["camera"]["fov_long_side_deg"] == pytest.approx(65.5, abs=0.1)  # 2*atan(36/56)
    assert o["thumbnail"]["decodable"] and o["thumbnail"]["differs"] is False
    assert "data_after_end" not in o


def test_mismatched_embedded_thumbnail_is_flagged_and_extracted(tmp_path):
    main = scene((30, 90, 200), (200, 40, 40))
    other = Image.new("RGB", (160, 120), (250, 250, 250))
    ImageDraw.Draw(other).line([0, 0, 160, 120], fill=(0, 0, 0), width=12)
    data = insert_segments(jpeg_bytes(main), [(0xE1, b"Exif\x00\x00" + exif_with(other))])
    o = triage.triage(write(tmp_path, "b.jpg", data), extract_dir=tmp_path / "x")
    assert o["thumbnail"]["differs"] is True
    assert any("thumbnail differs" in n for n in o["notes"])
    assert (tmp_path / "x" / "embedded_thumbnail.jpg").read_bytes()[:3] == b"\xff\xd8\xff"


def test_letterboxed_thumbnail_of_a_wide_image_is_not_a_false_alarm(tmp_path):
    main = scene((30, 90, 200), (200, 40, 40), size=(640, 360))
    th = Image.new("RGB", (160, 120), (0, 0, 0))
    th.paste(main.resize((160, 90)), (0, 15))
    data = insert_segments(jpeg_bytes(main), [(0xE1, b"Exif\x00\x00" + exif_with(th))])
    o = triage.triage(write(tmp_path, "c.jpg", data))
    assert o["thumbnail"]["thumb_size_after_trimming_bars"][1] < 120
    assert o["thumbnail"]["differs"] is False


def test_data_after_end_of_jpeg_is_reported_with_embedded_zip(tmp_path):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("flag.txt", "CTF{hidden}")
    data = jpeg_bytes(scene((10, 10, 10), (90, 90, 90))) + z.getvalue()
    o = triage.triage(write(tmp_path, "d.jpg", data), extract_dir=tmp_path / "x")
    tail = o["data_after_end"]
    assert tail["type"] == "zip" and tail["bytes"] == len(z.getvalue())
    assert any(s["type"] == "zip" for s in tail["signatures_found"])
    assert zipfile.ZipFile(tmp_path / "x" / "data_after_end.bin").read("flag.txt") == b"CTF{hidden}"


def test_jpeg_comment_xmp_and_iptc(tmp_path):
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" '
           b'xmlns:GPano="http://ns.google.com/photos/1.0/panorama/" photoshop:City="Kairouan" '
           b'GPano:PoseHeadingDegrees="123.4"><dc:subject xmlns:dc="http://purl.org/dc/elements/1.1/"><rdf:Bag>'
           b'<rdf:li>mosque</rdf:li><rdf:li>minaret</rdf:li></rdf:Bag></dc:subject></rdf:Description>'
           b'</rdf:RDF></x:xmpmeta>')
    iim = b"".join(b"\x1c\x02" + bytes([ds]) + struct.pack(">H", len(v)) + v
                   for ds, v in ((90, b"Tozeur"), (101, b"Tunisia"), (120, b"Old town")))
    res = b"8BIM" + struct.pack(">H", 0x0404) + b"\x00\x00" + struct.pack(">I", len(iim)) + iim
    data = insert_segments(jpeg_bytes(scene((5, 5, 5), (80, 80, 80))), [
        (0xFE, b"made with love in 2019"), (0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp),
        (0xED, b"Photoshop 3.0\x00" + res)])
    o = triage.triage(write(tmp_path, "e.jpg", data))
    assert o["comments"] == ["made with love in 2019"]
    xmp_items = {(i["name"], i["value"]) for i in o["xmp"]}
    assert ("photoshop:City", "Kairouan") in xmp_items and ("GPano:PoseHeadingDegrees", "123.4") in xmp_items
    assert ("dc:subject", "mosque") in xmp_items and ("dc:subject", "minaret") in xmp_items
    assert ("City", "Tozeur") in o["iptc"] and ("Country-PrimaryLocationName", "Tunisia") in o["iptc"]


def test_icc_profile_description_is_read(tmp_path):
    from PIL import ImageCms
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    data = jpeg_bytes(scene((1, 2, 3), (4, 5, 6)), icc_profile=icc)
    o = triage.triage(write(tmp_path, "f.jpg", data))
    assert o["icc"]["bytes"] == len(icc) and o["icc"]["colour_space"] == "RGB"
    assert "srgb" in o["icc"].get("description", "").lower()


def test_png_text_chunks_private_chunk_and_trailing_data(tmp_path):
    info = PngInfo()
    info.add_text("Comment", "look closer")
    info.add_text("Location", "near the old port", zip=True)
    info.add_itxt("Description", "مرحبا", lang="ar")
    info.add(b"flAg", b"secret-ish")
    b = io.BytesIO()
    scene((0, 128, 0), (0, 0, 128)).save(b, "PNG", pnginfo=info)
    data = b.getvalue() + b"EXTRA BYTES AFTER IEND"
    o = triage.triage(write(tmp_path, "g.png", data))
    texts = {(t["type"], t["key"], t["value"]) for t in o["text_chunks"]}
    assert ("tEXt", "Comment", "look closer") in texts and ("zTXt", "Location", "near the old port") in texts
    assert ("iTXt", "Description", "مرحبا") in texts
    flag = [c for c in o["chunks"] if c["type"] == "flAg"]
    assert flag and flag[0]["note"] == "non-standard ancillary chunk" and flag[0]["crc_ok"]
    assert o["data_after_end"]["bytes"] == len(b"EXTRA BYTES AFTER IEND")


def test_png_bad_crc_is_reported(tmp_path):
    b = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(b, "PNG")
    data = bytearray(b.getvalue())
    data[29] ^= 0xFF  # inside the IHDR CRC
    o = triage.triage(write(tmp_path, "h.png", bytes(data)))
    assert any(not c["crc_ok"] for c in o["chunks"]) and any("bad CRC" in n for n in o["notes"])


def test_extension_mismatch_and_missing_metadata(tmp_path):
    b = io.BytesIO()
    Image.new("RGB", (8, 8)).save(b, "PNG")
    o = triage.triage(write(tmp_path, "looks_like.jpg", b.getvalue()))
    assert o["type"] == "png" and "extension_mismatch" in o
    assert any("No EXIF" in n for n in o["notes"])


def test_local_time_without_offset_says_timezone_unknown(tmp_path):
    t = tiff_le([("IFD0", [(0x8769, 4, ("@", "Exif"))], None), ("Exif", [(0x9003, 2, "2020:01:02 03:04:05")], None)])
    data = insert_segments(jpeg_bytes(scene((9, 9, 9), (99, 99, 99))), [(0xE1, b"Exif\x00\x00" + t)])
    o = triage.triage(write(tmp_path, "i.jpg", data))
    assert o["time"]["utc_from_offset"] is None and "time zone is unknown" in o["time"]["note"]


def test_garbage_file_does_not_crash(tmp_path):
    o = triage.triage(write(tmp_path, "j.jpg", b"\xff\xd8\xff\xe1\x00\x10Exif\x00\x00II*\x00garbage"))
    assert o["type"] == "jpeg" and ("parse_error" in o or "exif" in o or "segments" in o)


def test_cli_triage_prints_json(tmp_path, capsys):
    p = write(tmp_path, "k.jpg", jpeg_bytes(scene((1, 1, 1), (2, 2, 2))))
    assert cli.main(["triage", str(p)]) == 0
    o = json.loads(capsys.readouterr().out)
    assert o["type"] == "jpeg" and o["image"]["size"] == [320, 240]
