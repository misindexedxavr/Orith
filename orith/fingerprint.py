"""What the file itself gives away beyond its metadata. Deterministic, no AI, nothing guessed.

  file_name_time(name)  a date and time written in the file name (camera, phone and app naming)
  jpeg(path)            JPEG encoder fingerprint: quality estimated from the quantization tables against the
                        standard libjpeg (IJG, ITU-T T.81 Annex K) tables, progressive or baseline, chroma
                        subsampling
  hashes(path)          perceptual hashes (dHash, aHash, 64 bits) to compare copies or near-duplicates
"""
from __future__ import annotations

import re
from pathlib import Path

# ITU-T T.81 Annex K, table K.1 (luminance), natural (row) order
STD_LUMA = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
            14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
            49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]
PREFIXES = ("WhatsApp Image", "WhatsApp Video", "Screenshot", "Screen Shot", "signal-", "photo_", "IMG_", "IMG-",
            "VID_", "PXL_", "DSC_", "DSCN", "Photo ")
DATE_TIME = re.compile(r"(?<!\d)(20\d\d|19\d\d)[-_.]?(\d\d)[-_.]?(\d\d)(?:[ _T-]+(?:at[ _])?)(\d\d)[-_.:h]?(\d\d)[-_.:m]?(\d\d)(?:\d{3})?(?!\d)")  # noqa: E501
DATE_ONLY = re.compile(r"(?<!\d)(20\d\d)[-_.](\d\d)[-_.](\d\d)(?!\d)")


def _ijg(q):
    s = 5000 / q if q < 50 else 200 - 2 * q
    return [min(255, max(1, int((v * s + 50) // 100))) for v in STD_LUMA]


IJG = {q: _ijg(q) for q in range(1, 101)}


def file_name_time(name):
    """{'pattern', 'local', 'note'} when the file name holds a plausible date (and time), else None."""
    stem = Path(name).stem
    prefix = next((p for p in PREFIXES if stem.startswith(p)), None)
    m = DATE_TIME.search(stem)
    if m:
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
        if 1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 60:
            return {"local": f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}", "date_only": False,
                    "pattern": m.group(0), "prefix": prefix,
                    "note": "local time from the file name; its time zone is unknown and a renamed file can carry "
                            "any name"}
    m = DATE_ONLY.search(stem)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return {"local": f"{y:04d}-{mo:02d}-{d:02d}", "date_only": True, "pattern": m.group(0), "prefix": prefix,
                    "note": "date from the file name (no time); a renamed file can carry any name"}
    return None


def jpeg(path):
    """Encoder fingerprint of a JPEG file, or None for other formats."""
    from PIL import Image, JpegImagePlugin
    with Image.open(path) as im:
        if im.format != "JPEG":
            return None
        q = getattr(im, "quantization", None) or {}
        luma = list(q.get(0, []))
        progressive = bool(im.info.get("progressive") or im.info.get("progression"))
        try:
            sampling = {0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}.get(JpegImagePlugin.get_sampling(im), "other")
        except Exception:  # noqa: BLE001 - unusual component layout
            sampling = "unknown"
    out = {"progressive": progressive, "chroma_subsampling": sampling, "tables": len(q)}
    if len(luma) != 64:
        return out | {"quality": None, "standard_table": None, "note": "no usable quantization table"}
    exact = next((qq for qq, t in IJG.items() if t == luma), None)
    if exact:
        return out | {"quality": exact, "standard_table": True,
                      "note": f"standard libjpeg (IJG) table at quality {exact}: the file was written by software that "
                              "uses libjpeg's tables (editors, apps, websites); camera firmware usually has its own"}
    best = min(IJG, key=lambda qq: sum(abs(a - b) for a, b in zip(IJG[qq], luma)))
    return out | {"quality": best, "standard_table": False,
                  "note": f"non-standard table (closest standard quality {best}): typical of a camera's or an app's "
                          "own encoder"}


def hashes(path):
    """dHash and aHash (64-bit hex) of the image, for comparing copies or near-duplicates."""
    from PIL import Image
    with Image.open(path) as im:
        g = im.convert("L")
        d = g.resize((9, 8))
        px = list(d.tobytes())  # "L" mode: one byte per pixel, row by row
        dh = sum(1 << i for i in range(64) if px[(i // 8) * 9 + i % 8] > px[(i // 8) * 9 + i % 8 + 1])
        a = list(g.resize((8, 8)).tobytes())
        mean = sum(a) / 64
        ah = sum(1 << i for i, v in enumerate(a) if v > mean)
    return {"dhash": f"{dh:016x}", "ahash": f"{ah:016x}",
            "note": "compare with the hash of another copy: a Hamming distance under about 10 bits means the same picture"}
