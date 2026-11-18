"""Where the data, the cases, the cache and the user's settings live.

Installed with pip: the data ships inside the package (PKG/data/facts, unicode, packs), cases go to
./cases in the current folder, the HTTP cache to the user's cache folder.
Source checkout (development): data, cases and cache sit next to the package folder.
Every location can be overridden with an environment variable named after the package
(for example <APP>_CASE_DIR, <APP>_CACHE_DIR, <APP>_FACTS, <APP>_UNICODE, <APP>_PACKS, <APP>_CONFIG).
"""
from __future__ import annotations

import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
APP = PKG.name
ENV = APP.upper()
INSTALLED = (PKG / "data").is_dir()
DATA = PKG / "data" if INSTALLED else ROOT


def env(name):
    v = os.environ.get(f"{ENV}_{name}", "").strip()
    return v or None


def data_dir(kind):
    """kind: facts, unicode or packs."""
    return Path(env(kind.upper()) or DATA / kind)


def cases_dir():
    return Path(env("CASE_DIR") or (Path.cwd() / "cases" if INSTALLED else ROOT / "cases"))


def cache_dir():
    if env("CACHE_DIR"):
        return Path(env("CACHE_DIR"))
    if not INSTALLED:
        return ROOT / "cache"
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CACHE_HOME")
    return Path(base or Path.home() / ".cache") / APP / "cache"


def buildings_dir():
    """Where the optional building indexes live (built by tools/fetch_gba.py; never shipped)."""
    if env("BUILDINGS"):
        return Path(env("BUILDINGS"))
    if not INSTALLED:
        return ROOT / "buildings"
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_DATA_HOME")
    return Path(base or Path.home() / ".local" / "share") / APP / "buildings"


INDEX_VERSION = "1"  # raise when a cached structure changes shape


def cached(name, sources, build):
    """build() once per version of the source files, then load it from the cache folder (pickle of our own
    data only). Any cache problem falls back to build(): the cache can always be deleted."""
    import hashlib
    import pickle
    try:
        stamp = [(str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in map(Path, sources)]
    except OSError:
        return build()
    key = hashlib.sha256(repr((INDEX_VERSION, name, stamp)).encode()).hexdigest()[:24]
    f = cache_dir() / "index" / f"{name}-{key}.pickle"
    try:
        with f.open("rb") as h:
            return pickle.load(h)
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError, ImportError):
        pass
    value = build()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        with tmp.open("wb") as h:
            pickle.dump(value, h, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(f)
    except OSError:
        pass
    return value


def user_config_path():
    return Path(env("CONFIG") or Path.home() / ".config" / APP / "config.json")
