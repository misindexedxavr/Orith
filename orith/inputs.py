"""Checks on the file given to `orith` before any work: clear one-line errors instead of tracebacks.

Exit codes used by the CLI: 0 done, 1 unexpected internal error, 2 bad input or usage, 130 interrupted.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import paths

IMAGE_KINDS = {"jpeg", "png", "webp", "tiff", "heif", "gif", "bmp"}
DEFAULT_MAX_MB = 200


class InputError(Exception):
    """A problem with what the user gave: printed as one line, exit code 2."""


def max_bytes():
    try:
        return int(float(paths.env("MAX_MB") or DEFAULT_MAX_MB) * 1_000_000)
    except ValueError:
        return DEFAULT_MAX_MB * 1_000_000


def check_file(path):
    """Fail early for a missing, empty, unreadable or oversized file."""
    p = Path(path)
    if not p.exists():
        raise InputError(f"file not found: {p}")
    if not p.is_file():
        raise InputError(f"not a regular file: {p}")
    size = p.stat().st_size
    if size == 0:
        raise InputError(f"empty file: {p}")
    if size > max_bytes():
        raise InputError(f"{p.name} is {size / 1e6:,.0f} MB, over the {max_bytes() / 1e6:,.0f} MB limit "
                         f"(set {paths.ENV}_MAX_MB to raise it)")
    if not os.access(p, os.R_OK):
        raise InputError(f"cannot read {p}: permission denied")
    return p


def check_triage(t, path):
    """After triage: refuse non-images; return a warning for damaged image data (metadata is still used)."""
    kind = t.get("type")
    if kind not in IMAGE_KINDS:
        label = "no known image signature" if kind == "unknown" else f"looks like {kind}"
        raise InputError(f"not an image orith can read: {Path(path).name} ({label}; JPEG, PNG, WebP, TIFF, HEIC "
                         f"are supported)")
    err = (t.get("image") or {}).get("error")
    if not err:
        return None
    if "DecompressionBomb" in err:
        raise InputError(f"{Path(path).name}: too many pixels to decode safely ({err.split(':', 1)[-1].strip()})")
    if kind == "heif" and not heif_available():
        return ("HEIC/HEIF pixels need the optional decoder: pip install \"orith[heic]\"; the file's own bytes "
                "were still checked")
    return f"the image data is damaged or truncated ({err}); its metadata was still read"


def heif_available():
    try:
        import pillow_heif  # noqa: F401  (optional extra: orith[heic])
        return True
    except ImportError:
        return False


SAFE_CASE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_case_id(name):
    """A case id usable as one folder name: no separators, no '..', no leading dot, at most 80 characters."""
    s = SAFE_CASE.sub("_", str(name)).strip("._") or "case"
    return s[:80]
