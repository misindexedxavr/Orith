"""Generic point-in-polygon geometry for GeoJSON boundaries. No country-specific logic.

Pure Python (ray casting with bounding-box prefilter); no shapely dependency so it
behaves the same on Windows and Linux. Country data lives in packs (see orith/packs.py).
"""
from __future__ import annotations

import json
import math
from functools import cache
from pathlib import Path


def _rings(geometry):
    """Yield polygons as lists of rings; ring = list of (lon, lat). First ring is the outer one."""
    t = geometry["type"]
    if t == "Polygon":
        yield geometry["coordinates"]
    elif t == "MultiPolygon":
        yield from geometry["coordinates"]
    else:
        raise ValueError(f"unsupported geometry type: {t}")


def _in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            if lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


class Shape:
    """One feature: properties + polygons with precomputed bounding boxes."""

    def __init__(self, feature):
        self.props = feature.get("properties") or {}
        self.polys = []
        for rings in _rings(feature["geometry"]):
            xs = [p[0] for p in rings[0]]
            ys = [p[1] for p in rings[0]]
            self.polys.append((min(xs), min(ys), max(xs), max(ys), rings))
        # (west, south, east, north)
        self.bbox = (min(p[0] for p in self.polys), min(p[1] for p in self.polys),
                     max(p[2] for p in self.polys), max(p[3] for p in self.polys))

    def contains(self, lat, lon):
        if not (self.bbox[0] <= lon <= self.bbox[2] and self.bbox[1] <= lat <= self.bbox[3]):
            return False
        for x0, y0, x1, y1, rings in self.polys:
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            if _in_ring(lon, lat, rings[0]) and not any(_in_ring(lon, lat, h) for h in rings[1:]):
                return True
        return False

    def distance_km(self, lat, lon):
        """Approximate distance to the nearest boundary segment (0 if inside)."""
        if self.contains(lat, lon):
            return 0.0
        kx = 111.32 * math.cos(math.radians(lat))
        ky = 110.57
        best = float("inf")
        for _, _, _, _, rings in self.polys:
            for ring in rings:
                for i in range(len(ring) - 1):
                    ax, ay = (ring[i][0] - lon) * kx, (ring[i][1] - lat) * ky
                    bx, by = (ring[i + 1][0] - lon) * kx, (ring[i + 1][1] - lat) * ky
                    dx, dy = bx - ax, by - ay
                    den = dx * dx + dy * dy
                    t = 0.0 if den == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / den))
                    px, py = ax + t * dx, ay + t * dy
                    d = px * px + py * py
                    if d < best:
                        best = d
        return math.sqrt(best)


@cache
def load_geojson(path):
    """Shapes of every polygon feature in a GeoJSON FeatureCollection file (cached in memory and on disk)."""
    from . import paths

    def build():
        with Path(path).open(encoding="utf-8") as f:
            return tuple(Shape(ft) for ft in json.load(f)["features"] if ft.get("geometry"))
    return paths.cached("shapes-" + Path(path).stem, [path], build)
