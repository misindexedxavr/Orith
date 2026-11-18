"""Error level analysis (ELA): save the image again as JPEG at a known quality and amplify the difference.

Areas pasted in or edited after the last save often recompress differently from the rest, so they stand
out in the ELA image. It is a visual aid for a person, not a detector: uniform noise is normal, edges and
textures always show more error, and it says nothing for lossless (PNG) or freshly generated images.
The grid summary lists cells whose average error is far above the image's typical cell (to look at first).
"""
from __future__ import annotations

import io
from pathlib import Path


def run(path, out=None, quality=90, grid=12):
    from PIL import Image, ImageChops
    with Image.open(path) as src:
        fmt = src.format
        im = src.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    re = Image.open(buf).convert("RGB")
    diff = ImageChops.difference(im, re)
    gray = diff.convert("L")
    hi = max(1, gray.getextrema()[1])
    scale = 255.0 / hi
    if out:
        enhanced = diff.point(lambda v: min(255, int(v * scale)))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        enhanced.save(out, "PNG")
    w, h = gray.size
    small = gray.resize((grid, grid), Image.Resampling.BOX)  # mean error per cell
    cells = list(small.tobytes())  # "L" mode: one byte per pixel
    ordered = sorted(cells)
    median = ordered[len(ordered) // 2] or 1
    mean = sum(gray.tobytes()) / (w * h)
    hot = [{"row": i // grid, "col": i % grid, "mean_error": v, "times_median": round(v / median, 1),
            "where": f"{(i % grid) * 100 // grid}-{((i % grid) + 1) * 100 // grid}% from left, "
                     f"{(i // grid) * 100 // grid}-{((i // grid) + 1) * 100 // grid}% from top"}
           for i, v in enumerate(cells) if v >= 3 * median and v >= 4]
    hot.sort(key=lambda c: -c["mean_error"])
    note = ("JPEG: bright, sharply bounded areas that differ from similar surfaces nearby are worth a closer look; "
            "edges and fine texture are always brighter. ELA is a visual aid, not proof.")
    if fmt != "JPEG":
        note = (f"{fmt} file: it was not saved as JPEG, so ELA mainly shows edges and texture; it cannot reveal "
                "edits here.")
    return {"format": fmt, "quality_used": quality, "max_error": hi, "mean_error": round(mean, 2),
            "cell_median_error": median, "hot_cells": hot[:8], "image": str(out) if out else None, "note": note}
