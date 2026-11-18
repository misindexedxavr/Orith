"""Polite HTTP for the public OSM services: on-disk cache, per-host rate limit, backoff.

Nominatim and Overpass ask every client to identify itself. Each request carries a User-Agent built
from YOUR contact (an e-mail address or a project URL), read from, in order: the environment variable
<APP>_CONTACT, the "contact" key of ~/.config/<APP>/config.json (path: paths.user_config_path()), and
in a source checkout the legacy <APP>.local.json / <APP>_USER_AGENT. Without a contact every online
lookup is refused with an explanation; offline commands and cached answers keep working.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__, paths

ROOT = paths.ROOT
CACHE_DIR = paths.cache_dir()
CONFIG = None if paths.INSTALLED else ROOT / f"{paths.APP}.local.json"  # legacy, source checkout only
MIN_INTERVAL = {"nominatim.openstreetmap.org": 1.1, "overpass-api.de": 2.0}
RETRY_STATUS = (429, 500, 502, 503, 504)


class NetError(RuntimeError):
    pass


def _read(path):
    if path and Path(path).is_file():
        with Path(path).open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def config():
    """Settings (contact, overpass_endpoints) from the user config file, then the legacy local file."""
    return {**_read(CONFIG), **_read(paths.user_config_path())}


def contact_help():
    return (f"Online lookups (Nominatim, Overpass) need a contact so the services can identify you. Set the "
            f"environment variable {paths.ENV}_CONTACT (for example: you@example.org) or write "
            f"{{\"contact\": \"you@example.org\"}} to {paths.user_config_path()}. Offline commands keep working.")


def user_agent():
    """The User-Agent sent with every request: '<app>/<version> (<contact>)', or a full agent string as given."""
    cfg = config()
    contact = (paths.env("CONTACT") or str(cfg.get("contact", "")).strip()
               or paths.env("USER_AGENT") or str(cfg.get("user_agent", "")).strip())
    if not contact:
        raise NetError("no contact set. " + contact_help())
    return contact if "(" in contact else f"{paths.APP}/{__version__} ({contact})"


def cache_key(url, data=None):
    return hashlib.sha256((url + "\n" + (data or "")).encode("utf-8")).hexdigest()[:32]


def _throttle(host, cache_dir):
    """Sleep so that consecutive requests to one host are at least MIN_INTERVAL apart."""
    gap = MIN_INTERVAL.get(host, 1.0)
    stamp = cache_dir / f".last_{host.replace(':', '_')}"
    if stamp.exists():
        wait = gap - (time.time() - stamp.stat().st_mtime)
        if wait > 0:
            time.sleep(wait)
    stamp.write_text(str(time.time()))


def fetch_json(url, data=None, cache_dir=None, timeout=60, tries=4, opener=None, sleep=time.sleep):
    """GET (or POST when data is given) returning parsed JSON, with cache, throttle and backoff.

    Returns (payload, from_cache). `opener` and `sleep` are injectable for tests.
    """
    cache_dir = Path(cache_dir or CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / (cache_key(url, data) + ".json")
    if path.exists():
        with path.open(encoding="utf-8") as f:
            return json.load(f)["payload"], True

    ua = user_agent()
    host = urllib.parse.urlparse(url).netloc
    opener = opener or urllib.request.urlopen
    delay = 3.0
    for attempt in range(1, tries + 1):
        _throttle(host, cache_dir)
        try:
            req = urllib.request.Request(url, data=data.encode("utf-8") if data is not None else None,
                                         headers={"User-Agent": ua, "Accept": "application/json"})
            with opener(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode("utf-8"))
            with path.open("w", encoding="utf-8") as f:
                json.dump({"url": url, "data": data, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "payload": payload}, f, ensure_ascii=False)
            return payload, False
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == tries:
                err = NetError(f"HTTP {e.code} from {host}")
                err.status = e.code
                raise err from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == tries:
                err = NetError(f"{type(e).__name__} contacting {host}: {e}")
                text = f"{type(e).__name__} {e}".lower()
                err.status = "timeout" if ("timeout" in text or "timed out" in text) else "network"
                raise err from e
        sleep(delay)
        delay = min(delay * 2, 60)
    raise NetError(f"gave up on {host}")
