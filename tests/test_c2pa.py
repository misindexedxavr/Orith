"""C2PA content credentials: JUMBF + CBOR decoding in PNG (caBX) and JPEG (APP11), and the scan warning."""
import io
import json
import struct
import zlib

from orith import c2pa, cli, paths, triage


def cbor(v):
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | ai]) + n.to_bytes(size, "big")
    if isinstance(v, bool):
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return head(3, len(b)) + b
    if isinstance(v, list):
        return head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(v)


def box(t, payload):
    return struct.pack(">I4s", 8 + len(payload), t.encode()) + payload


def jumb(label, *content):
    jumd = box("jumd", b"\x00" * 16 + b"\x03" + label.encode() + b"\x00")
    return box("jumb", jumd + b"".join(content))


def manifest(source_type="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"):
    actions = {"actions": [{"action": "c2pa.created", "when": "2026-11-18T22:10:51Z",
                            "softwareAgent": {"name": "ChatGPT", "version": "gpt-image"},
                            "digitalSourceType": source_type},
                           {"action": "c2pa.converted"}]}
    claim = {"claim_generator_info": [{"name": "OpenAI Media Service API"}], "dc:title": "image.png",
             "instanceID": "xmp:iid:468fdbb3"}
    m = jumb("urn:c2pa:ab88f624", jumb("c2pa.assertions", jumb("c2pa.actions.v2", box("cbor", cbor(actions)))),
             jumb("c2pa.claim.v2", box("cbor", cbor(claim))), jumb("c2pa.signature", box("cbor", cbor(b"sig"))))
    return jumb("c2pa", m)


def png_with(chunk_type, body):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (90, 120, 90)).save(buf, "PNG")
    data = buf.getvalue()
    i = 8 + 8 + 13 + 4  # after the IHDR chunk
    ch = struct.pack(">I4s", len(body), chunk_type) + body
    ch += struct.pack(">I", zlib.crc32(ch[4:]) & 0xFFFFFFFF)
    return data[:i] + ch + data[i:]


def test_png_cabx_manifest_is_decoded_and_flagged_as_ai():
    s = c2pa.parse(manifest())
    assert s["present"] and s["ai_generated"] and s["generator"] == "OpenAI Media Service API"
    assert s["actions"][0]["software_agent"] == "ChatGPT gpt-image" and s["signature"].startswith("present, NOT verified")
    assert c2pa.parse(manifest("http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture"))["ai_generated"] is False


def test_triage_and_scan_report_the_manifest_in_png_and_jpeg(tmp_path, capsys, monkeypatch):
    p = tmp_path / "img.png"
    p.write_bytes(png_with(b"caBX", manifest()))
    t = triage.triage(p)
    assert t["c2pa"]["ai_generated"] and any("C2PA" in n for n in t["notes"])
    assert not any("No EXIF" in n for n in t["notes"])
    # JPEG: the same store split over two APP11 segments
    from PIL import Image
    j = io.BytesIO()
    Image.new("RGB", (32, 24)).save(j, "JPEG")
    jb, store = j.getvalue(), manifest()
    half = len(store) // 2
    seg1 = b"JP" + struct.pack(">HI", 1, 1) + store[:half]
    seg2 = b"JP" + struct.pack(">HI", 1, 2) + store[:8] + store[half:]
    app = b"".join(b"\xff\xeb" + struct.pack(">H", len(s) + 2) + s for s in (seg1, seg2))
    jp = tmp_path / "img.jpg"
    jp.write_bytes(jb[:2] + app + jb[2:])
    assert triage.triage(jp)["c2pa"]["generator"] == "OpenAI Media Service API"
    monkeypatch.delenv(f"{paths.ENV}_JSON", raising=False)
    assert cli.main([str(p), "--case-dir", str(tmp_path / "c"), "--no-color"]) == 0
    cap = capsys.readouterr()
    assert "AI-GENERATED (declared by the file itself)" in cap.out and "ChatGPT gpt-image" in cap.out
    assert "[!] Content credentials (C2PA)" in cap.err
    assert cli.main([str(p), "--case-dir", str(tmp_path / "c2"), "--json"]) == 0
    o = json.loads(capsys.readouterr().out)
    assert o["provenance"]["ai_generated"] is True and o["usable"] is False
    assert any(c["text"].startswith("file provenance: AI-GENERATED") for c in o["clues"])
