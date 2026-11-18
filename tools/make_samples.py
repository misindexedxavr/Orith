"""Generate the sample files in tests/samples/ (all synthetic: drawn by Pillow, no real photo, no personal data).

    python tools/make_samples.py   (the samples are committed; run this only to remake them)

gps.jpg          a drawn street scene with EXIF: camera, capture time with UTC offset, GPS (a public square in
                 Paris, Place de la Concorde) and a GPS clock that agrees with the camera clock
stripped.jpg     the same picture with no metadata at all
text.png         a drawn shop sign with readable text (orith does not read pixels: pass the text with --text)
corrupt.jpg      gps.jpg cut in half: the metadata survives, the image data is truncated
not_an_image.jpg a text file with a .jpg name
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "tests" / "samples"


def scene(w=640, h=420):
    im = Image.new("RGB", (w, h), (150, 190, 230))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 260, w, h], fill=(90, 90, 95))                       # road
    for x in range(0, w, 80):
        d.rectangle([x + 10, 335, x + 50, 342], fill=(235, 235, 235))     # lane marks
    for x, hh, c in [(20, 170, (200, 170, 140)), (150, 210, (180, 160, 150)),
                     (300, 150, (210, 200, 180)), (450, 190, (170, 150, 130))]:
        d.rectangle([x, 260 - hh, x + 120, 260], fill=c)
        for wy in range(260 - hh + 15, 250, 35):
            for wx in range(x + 12, x + 110, 30):
                d.rectangle([wx, wy, wx + 14, wy + 18], fill=(60, 80, 110))
    d.ellipse([540, 30, 600, 90], fill=(255, 235, 150))                 # sun
    return im


def gps_exif():
    ex = Image.Exif()
    ex[0x010F] = "SampleCam"           # Make
    ex[0x0110] = "Model 1"             # Model
    ex[0x0131] = "orith sample generator"  # Software
    ex[0x0132] = "2024:06:15 14:30:00"
    sub = ex.get_ifd(0x8769)
    sub[0x9003] = "2024:06:15 14:30:00"   # DateTimeOriginal (local time)
    sub[0x9011] = "+02:00"                # OffsetTimeOriginal (Paris, summer)
    gps = ex.get_ifd(0x8825)
    gps[0x0000] = b"\x02\x03\x00\x00"
    gps[0x0001] = "N"
    gps[0x0002] = (48.0, 51.0, 56.4)      # 48.865667 N
    gps[0x0003] = "E"
    gps[0x0004] = (2.0, 19.0, 15.6)       # 2.321000 E
    gps[0x0007] = (12.0, 30.0, 0.0)       # GPS time 12:30:00 UTC = 14:30 at +02:00
    gps[0x001D] = "2024:06:15"
    return ex


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    im = scene()
    im.save(OUT / "gps.jpg", quality=88, exif=gps_exif())
    im.save(OUT / "stripped.jpg", quality=88)

    sign = Image.new("RGB", (640, 300), (240, 240, 235))
    d = ImageDraw.Draw(sign)
    d.rectangle([20, 20, 620, 280], fill=(20, 110, 60))
    d.rectangle([30, 30, 610, 270], outline=(255, 255, 255), width=3)
    for i, line in enumerate(["PHARMACIE CENTRALE", "Tel: +33 1 42 60 00 00", "www.pharmacie-exemple.fr"]):
        d.text((60, 70 + i * 60), line, fill=(255, 255, 255), font_size=34)
    sign.save(OUT / "text.png")

    raw = (OUT / "gps.jpg").read_bytes()
    (OUT / "corrupt.jpg").write_bytes(raw[: len(raw) // 2])
    (OUT / "not_an_image.jpg").write_bytes(b"This is a plain text file with an image extension.\n")
    for f in sorted(OUT.iterdir()):
        print(f"{f.name:18} {f.stat().st_size:>7} bytes")


if __name__ == "__main__":
    main()
