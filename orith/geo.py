"""Geometry and coordinate conversion. Pure Python except Plus Codes (openlocationcode).

Distances and bearings are on a sphere of radius 6371.0088 km (mean Earth radius): good to about
0.5 %, far below the error of anything read from a photo. UTM/MGRS use the WGS84 ellipsoid with
Krueger's series (Karney 2011, order 6), accurate to well under a millimetre inside a zone.
Latitude north positive, longitude east positive, bearings degrees clockwise from true north.
"""
from __future__ import annotations

import math
import re

R_KM = 6371.0088

# ------------------------------------------------------------------ sphere


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_KM * math.asin(min(1.0, math.sqrt(h)))


def bearing(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def destination(lat, lon, brg, dist_km):
    d = dist_km / R_KM
    p1, l1, b = math.radians(lat), math.radians(lon), math.radians(brg)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), ((math.degrees(l2) + 540) % 360) - 180


def bearing_line(lat, lon, brg, length_km, step_km=None):
    """Great-circle line from a point along a bearing, as [lon, lat] points (GeoJSON order)."""
    step_km = step_km or max(length_km / 50, 0.01)
    n = max(1, int(math.ceil(length_km / step_km)))
    return [[round(x, 6) for x in destination(lat, lon, brg, length_km * i / n)[::-1]] for i in range(n + 1)]


def intersection(lat1, lon1, brg1, lat2, lon2, brg2):
    """Where a line from point 1 on bearing 1 meets a line from point 2 on bearing 2 (sphere).

    Returns (lat, lon) or None when the lines are parallel or meet behind either start point.
    """
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    t13, t23 = math.radians(brg1), math.radians(brg2)
    d12 = 2 * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    if d12 == 0:
        return None
    ca = (math.sin(p2) - math.sin(p1) * math.cos(d12)) / (math.sin(d12) * math.cos(p1))
    cb = (math.sin(p1) - math.sin(p2) * math.cos(d12)) / (math.sin(d12) * math.cos(p2))
    ta, tb = math.acos(max(-1, min(1, ca))), math.acos(max(-1, min(1, cb)))
    t12, t21 = (ta, 2 * math.pi - tb) if math.sin(l2 - l1) > 0 else (2 * math.pi - ta, tb)
    a1 = (t13 - t12 + math.pi) % (2 * math.pi) - math.pi
    a2 = (t21 - t23 + math.pi) % (2 * math.pi) - math.pi
    if math.sin(a1) == 0 and math.sin(a2) == 0:
        return None
    if math.sin(a1) * math.sin(a2) < 0:
        return None
    a3 = math.acos(-math.cos(a1) * math.cos(a2) + math.sin(a1) * math.sin(a2) * math.cos(d12))
    d13 = math.atan2(math.sin(d12) * math.sin(a1) * math.sin(a2), math.cos(a2) + math.cos(a1) * math.cos(a3))
    if d13 < 0:
        return None
    return destination(lat1, lon1, brg1, d13 * R_KM)


# ------------------------------------------------------------------ resection

def _enu(lat0, lon0, lat, lon):
    """Local east/north metres around (lat0, lon0) (equirectangular; fine over a few km)."""
    k = math.cos(math.radians(lat0))
    return (math.radians(lon - lon0) * R_KM * 1000 * k, math.radians(lat - lat0) * R_KM * 1000)


def _from_enu(lat0, lon0, x, y):
    k = math.cos(math.radians(lat0))
    return lat0 + math.degrees(y / (R_KM * 1000)), lon0 + math.degrees(x / (R_KM * 1000 * k))


def resection_bearings(landmarks):
    """Observer position from absolute bearings (observer -> landmark), 2 or more landmarks.

    landmarks: [(lat, lon, bearing_deg)]. Least-squares intersection of the back-bearing lines.
    Returns the position, per-landmark residuals in metres and degrees, and a conditioning note.
    """
    if len(landmarks) < 2:
        raise ValueError("need at least 2 landmarks with bearings")
    lat0 = sum(l[0] for l in landmarks) / len(landmarks)
    lon0 = sum(l[1] for l in landmarks) / len(landmarks)
    for _ in range(3):  # re-centre on the solution so the local plane is accurate
        a11 = a12 = a22 = b1 = b2 = 0.0
        for lat, lon, brg in landmarks:
            px, py = _enu(lat0, lon0, lat, lon)
            dx, dy = math.sin(math.radians(brg)), math.cos(math.radians(brg))
            nx, ny = -dy, dx
            a11 += nx * nx
            a12 += nx * ny
            a22 += ny * ny
            c = nx * px + ny * py
            b1 += nx * c
            b2 += ny * c
        det = a11 * a22 - a12 * a12
        if abs(det) < 1e-9:
            raise ValueError("bearings are (nearly) parallel: the lines do not cross")
        x = (b1 * a22 - b2 * a12) / det
        y = (a11 * b2 - a12 * b1) / det
        lat0, lon0 = _from_enu(lat0, lon0, x, y)
    res = []
    for lat, lon, brg in landmarks:
        actual = bearing(lat0, lon0, lat, lon)
        dang = (brg - actual + 180) % 360 - 180
        dist = distance_km(lat0, lon0, lat, lon) * 1000
        res.append({"landmark": [lat, lon], "observed_bearing": brg, "computed_bearing": round(actual, 2),
                    "residual_deg": round(dang, 2), "distance_m": round(dist, 1),
                    "offset_m": round(dist * math.sin(math.radians(dang)), 1)})
    angles = sorted(l[2] % 180 for l in landmarks)
    spread = max(min(abs(a - b), 180 - abs(a - b)) for i, a in enumerate(angles) for b in angles[i + 1:])
    return {"lat": round(lat0, 7), "lon": round(lon0, 7), "residuals": res,
            "max_crossing_angle_deg": round(spread, 1),
            "note": ("lines cross at a shallow angle: small bearing errors move the point a lot"
                     if spread < 30 else "")}


def resection_angles(landmarks, start=None):
    """Observer position from bearings measured with an UNKNOWN zero (no compass), 3+ landmarks.

    landmarks: [(lat, lon, relative_bearing_deg)] where relative bearings share one unknown offset
    (for example angles measured from the left edge of a photo). Solves position and offset by
    Gauss-Newton from several starts. With exactly 3 landmarks the solution is exact and fails when
    the observer is on the circle through them (the "danger circle").
    """
    if len(landmarks) < 3:
        raise ValueError("need at least 3 landmarks when the compass direction is unknown")
    lat0 = sum(l[0] for l in landmarks) / len(landmarks)
    lon0 = sum(l[1] for l in landmarks) / len(landmarks)
    pts = [_enu(lat0, lon0, l[0], l[1]) for l in landmarks]
    rel = [math.radians(l[2]) for l in landmarks]
    spread = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a in pts for b in pts) or 1.0

    def resid(v):
        x, y, th = v
        out = []
        for (px, py), r in zip(pts, rel):
            true = math.atan2(px - x, py - y)
            out.append((r + th - true + math.pi) % (2 * math.pi) - math.pi)
        return out

    def solve(v):
        for _ in range(60):
            r = resid(v)
            J = []
            for k in range(3):
                h = 1e-3 if k < 2 else 1e-7
                v2 = list(v)
                v2[k] += h
                r2 = resid(v2)
                J.append([(r2[i] - r[i]) / h for i in range(len(r))])
            # normal equations (3x3)
            A = [[sum(J[a][i] * J[b][i] for i in range(len(r))) for b in range(3)] for a in range(3)]
            g = [sum(J[a][i] * r[i] for i in range(len(r))) for a in range(3)]
            try:
                step = _solve3(A, [-x for x in g])
            except ZeroDivisionError:
                return v, float("inf")
            v = [v[i] + step[i] for i in range(3)]
            if max(abs(s) for s in step[:2]) < 1e-4:
                break
        return v, math.sqrt(sum(e * e for e in resid(v)) / len(pts))

    starts = []
    for k in range(8):
        ang = 2 * math.pi * k / 8
        for dist in (0.5, 2.0):
            sx, sy = math.sin(ang) * spread * dist, math.cos(ang) * spread * dist
            th = (math.atan2(pts[0][0] - sx, pts[0][1] - sy) - rel[0])
            starts.append([sx, sy, th])
    if start:
        sx, sy = _enu(lat0, lon0, *start)
        starts.insert(0, [sx, sy, math.atan2(pts[0][0] - sx, pts[0][1] - sy) - rel[0]])
    sols = []
    for s0 in starts:
        v, rms = solve(s0)
        if math.isfinite(rms):
            sols.append((rms, v))
    if not sols:
        raise ValueError("no solution found")
    sols.sort(key=lambda t: t[0])
    distinct = []
    for rms, v in sols:
        if rms > math.radians(2):
            continue
        if all(math.hypot(v[0] - w[0], v[1] - w[1]) > spread * 0.05 for _, w in distinct):
            distinct.append((rms, v))
    rms, v = sols[0]
    lat, lon = _from_enu(lat0, lon0, v[0], v[1])
    out = {"lat": round(lat, 7), "lon": round(lon, 7), "heading_offset_deg": round(math.degrees(v[2]) % 360, 2),
           "rms_residual_deg": round(math.degrees(rms), 3), "solutions_found": len(distinct)}
    if len(distinct) > 1:
        out["other_solutions"] = [dict(zip(("lat", "lon"), (round(c, 7) for c in _from_enu(lat0, lon0, w[0], w[1]))))
                                  for _, w in distinct[1:4]]
        out["note"] = "more than one position fits: add a landmark or a compass bearing"
    return out


def _solve3(A, b):
    m = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(3):
        p = max(range(c, 3), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-15:
            raise ZeroDivisionError
        m[c], m[p] = m[p], m[c]
        for r in range(3):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [m[r][k] - f * m[c][k] for k in range(4)]
    return [m[i][3] / m[i][i] for i in range(3)]


# ------------------------------------------------------------------ DMS parsing and formatting

_DMS = re.compile(r"""^\s*([NSEW])?\s*([-+]?\d+(?:[.,]\d+)?)\s*(?:[°d:\s]\s*(\d+(?:[.,]\d+)?)\s*(?:['′m:\s]\s*(\d+(?:[.,]\d+)?)\s*(?:["″]|'')?)?['′m]?)?\s*([NSEW])?\s*$""", re.I | re.X)  # noqa: E501


def parse_angle(text):
    """One latitude or longitude: '36°52'12.3"N', '36 52 12.3 N', 'N36.87', '-10.25', '36:52:12'.

    Returns (value, axis) where axis is 'lat', 'lon' or None when no hemisphere letter was given.
    """
    m = _DMS.match(text.replace("''", '"'))
    if not m:
        raise ValueError(f"cannot read an angle from {text!r}")
    h1, d, mi, se, h2 = m.groups()
    hemi = (h1 or h2 or "").upper()
    if h1 and h2:
        raise ValueError(f"two hemisphere letters in {text!r}")
    dv = float(d.replace(",", "."))
    neg = dv < 0 or d.startswith("-")
    v = abs(dv) + (float(mi.replace(",", ".")) / 60 if mi else 0) + (float(se.replace(",", ".")) / 3600 if se else 0)
    if (mi and float(mi.replace(",", ".")) >= 60) or (se and float(se.replace(",", ".")) >= 60):
        raise ValueError(f"minutes and seconds must be below 60 in {text!r}")
    if hemi in ("S", "W"):
        if neg:
            raise ValueError(f"both a minus sign and {hemi} in {text!r}")
        neg = True
    v = -v if neg else v
    axis = "lat" if hemi in ("N", "S") else "lon" if hemi in ("E", "W") else None
    if axis == "lat" and abs(v) > 90 or abs(v) > 180:
        raise ValueError(f"out of range: {text!r}")
    return v, axis


def parse_point(text):
    """A coordinate pair in decimal or DMS: '36.87, 10.34', '36°52'N 10°20'E', '10°20'E 36°52'N'."""
    t = text.strip()
    parts = [p for p in re.split(r"\s*[,;]\s*|\s+(?=[-+]?\d+(?:[.,]\d+)?\s*°)|(?<=[NSEWnsew])\s+|\s+(?=[NSEWnsew]\s*\d)", t) if p]
    if len(parts) != 2:
        nums = re.findall(r"[-+]?\d+(?:\.\d+)?", t)
        if len(nums) == 2 and not re.search(r"[°'\"NSEWnsew]", t):
            parts = nums
        else:
            raise ValueError(f"cannot split {text!r} into two coordinates; separate them with a comma")
    (a, ax_a), (b, ax_b) = parse_angle(parts[0]), parse_angle(parts[1])
    if ax_a is not None and ax_a == ax_b:
        raise ValueError(f"both coordinates in {text!r} are {'latitudes' if ax_a == 'lat' else 'longitudes'}")
    if ax_a == "lon" or ax_b == "lat":
        a, b, ax_a, ax_b = b, a, ax_b, ax_a
    if abs(a) > 90:
        raise ValueError(f"latitude out of range in {text!r} (is the order lon, lat?)")
    return a, b


def to_dms(value, axis):
    hemi = ("N" if value >= 0 else "S") if axis == "lat" else ("E" if value >= 0 else "W")
    v = abs(value)
    d = int(v)
    m = int((v - d) * 60)
    s = (v - d - m / 60) * 3600
    if s >= 59.995:
        s, m = 0.0, m + 1
    if m == 60:
        m, d = 0, d + 1
    return f"{d}°{m:02d}'{s:05.2f}\"{hemi}"


def to_ddm(value, axis):
    hemi = ("N" if value >= 0 else "S") if axis == "lat" else ("E" if value >= 0 else "W")
    v = abs(value)
    d = int(v)
    return f"{d}°{(v - d) * 60:06.3f}'{hemi}"


# ------------------------------------------------------------------ UTM (WGS84, Krueger series)

_A, _F = 6378137.0, 1 / 298.257223563
_K0 = 0.9996
_N = _F / (2 - _F)
_A_RECT = _A / (1 + _N) * (1 + _N ** 2 / 4 + _N ** 4 / 64 + _N ** 6 / 256)
_ALPHA = [None,
          _N / 2 - 2 * _N ** 2 / 3 + 5 * _N ** 3 / 16 + 41 * _N ** 4 / 180 - 127 * _N ** 5 / 288 + 7891 * _N ** 6 / 37800,
          13 * _N ** 2 / 48 - 3 * _N ** 3 / 5 + 557 * _N ** 4 / 1440 + 281 * _N ** 5 / 630 - 1983433 * _N ** 6 / 1935360,
          61 * _N ** 3 / 240 - 103 * _N ** 4 / 140 + 15061 * _N ** 5 / 26880 + 167603 * _N ** 6 / 181440,
          49561 * _N ** 4 / 161280 - 179 * _N ** 5 / 168 + 6601661 * _N ** 6 / 7257600,
          34729 * _N ** 5 / 80640 - 3418889 * _N ** 6 / 1995840,
          212378941 * _N ** 6 / 319334400]
_BETA = [None,
         _N / 2 - 2 * _N ** 2 / 3 + 37 * _N ** 3 / 96 - _N ** 4 / 360 - 81 * _N ** 5 / 512 + 96199 * _N ** 6 / 604800,
         _N ** 2 / 48 + _N ** 3 / 15 - 437 * _N ** 4 / 1440 + 46 * _N ** 5 / 105 - 1118711 * _N ** 6 / 3870720,
         17 * _N ** 3 / 480 - 37 * _N ** 4 / 840 - 209 * _N ** 5 / 4480 + 5569 * _N ** 6 / 90720,
         4397 * _N ** 4 / 161280 - 11 * _N ** 5 / 504 - 830251 * _N ** 6 / 7257600,
         4583 * _N ** 5 / 161280 - 108847 * _N ** 6 / 3991680,
         20648693 * _N ** 6 / 638668800]
_E = math.sqrt(_F * (2 - _F))
_BANDS = "CDEFGHJKLMNPQRSTUVWX"


def utm_zone(lat, lon):
    z = int((lon + 180) // 6) + 1
    if z > 60:
        z = 60
    if 56 <= lat < 64 and 3 <= lon < 12:
        z = 32
    if 72 <= lat < 84:
        for lo_, hi_, zz in ((0, 9, 31), (9, 21, 33), (21, 33, 35), (33, 42, 37)):
            if lo_ <= lon < hi_:
                z = zz
    return z


def latitude_band(lat):
    if not -80 <= lat <= 84:
        return None
    return _BANDS[min(int((lat + 80) // 8), 19)]


def to_utm(lat, lon, zone=None):
    if not -80 <= lat <= 84:
        raise ValueError("UTM covers 80 S to 84 N; polar areas use UPS (not supported)")
    zone = zone or utm_zone(lat, lon)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi, lam = math.radians(lat), math.radians(lon) - lon0
    t = math.sinh(math.atanh(math.sin(phi)) - _E * math.atanh(_E * math.sin(phi)))
    xi_p = math.atan2(t, math.cos(lam))
    eta_p = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    xi = xi_p + sum(_ALPHA[j] * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p) for j in range(1, 7))
    eta = eta_p + sum(_ALPHA[j] * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p) for j in range(1, 7))
    e = 500000 + _K0 * _A_RECT * eta
    n = _K0 * _A_RECT * xi
    if lat < 0:
        n += 10000000
    return {"zone": zone, "band": latitude_band(lat), "hemisphere": "N" if lat >= 0 else "S",
            "easting": e, "northing": n}


def from_utm(zone, hemisphere, easting, northing):
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    n = northing - (10000000 if hemisphere.upper() == "S" else 0)
    xi = n / (_K0 * _A_RECT)
    eta = (easting - 500000) / (_K0 * _A_RECT)
    xi_p = xi - sum(_BETA[j] * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j in range(1, 7))
    eta_p = eta - sum(_BETA[j] * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j in range(1, 7))
    tau_p = math.sin(xi_p) / math.sqrt(math.sinh(eta_p) ** 2 + math.cos(xi_p) ** 2)
    tau = tau_p
    for _ in range(10):  # Newton iteration for tau
        sigma = math.sinh(_E * math.atanh(_E * tau / math.sqrt(1 + tau * tau)))
        tau_i_p = tau * math.sqrt(1 + sigma * sigma) - sigma * math.sqrt(1 + tau * tau)
        dtau = ((tau_p - tau_i_p) / math.sqrt(1 + tau_i_p ** 2) * (1 + (1 - _E * _E) * tau * tau)
                / ((1 - _E * _E) * math.sqrt(1 + tau * tau)))
        tau += dtau
        if abs(dtau) < 1e-14:
            break
    lat = math.degrees(math.atan(tau))
    lon = math.degrees(lon0 + math.atan2(math.sinh(eta_p), math.cos(xi_p)))
    return lat, ((lon + 540) % 360) - 180


# ------------------------------------------------------------------ MGRS (from UTM; UPS polar zones not supported)

_COLS = ["ABCDEFGH", "JKLMNPQR", "STUVWXYZ"]
_ROWS = "ABCDEFGHJKLMNPQRSTUV"


def to_mgrs(lat, lon, precision=5):
    u = to_utm(lat, lon)
    zone, e, n = u["zone"], round(u["easting"], 3), round(u["northing"], 3)  # avoid 617999.99999 -> 17999
    col = _COLS[(zone - 1) % 3][int(e // 100000) - 1]
    row = _ROWS[(int(n // 100000) + (5 if zone % 2 == 0 else 0)) % 20]
    div = 10 ** (5 - precision)
    ee, nn = int(e % 100000) // div, int(n % 100000) // div
    return f"{zone}{u['band']}{col}{row}{ee:0{precision}d}{nn:0{precision}d}"


def from_mgrs(text):
    """Centre-less south-west corner of the MGRS square, as (lat, lon), plus the square size in metres."""
    s = re.sub(r"\s+", "", text).upper()
    m = re.match(r"^(\d{1,2})([C-HJ-NP-X])([A-HJ-NP-Z])([A-HJ-NP-V])(\d*)$", s)
    if not m or len(m.group(5)) % 2:
        raise ValueError(f"not an MGRS reference: {text!r}")
    zone, band, col, row, digits = int(m.group(1)), m.group(2), m.group(3), m.group(4), m.group(5)
    if not 1 <= zone <= 60:
        raise ValueError(f"bad zone in {text!r}")
    k = len(digits) // 2
    size = 10 ** (5 - k)
    e100 = _COLS[(zone - 1) % 3].index(col) + 1 if col in _COLS[(zone - 1) % 3] else None
    if e100 is None:
        raise ValueError(f"column letter {col} is not used in zone {zone}")
    easting = e100 * 100000 + (int(digits[:k]) * size if k else 0)
    row_i = (_ROWS.index(row) - (5 if zone % 2 == 0 else 0)) % 20
    north_in_2000k = row_i * 100000 + (int(digits[k:]) * size if k else 0)
    # choose the multiple of 2,000 km that puts the point in the latitude band
    band_lat = -80 + _BANDS.index(band) * 8
    hemi = "N" if band >= "N" else "S"
    best = None
    for mult in range(0, 10):
        nn = north_in_2000k + mult * 2000000
        try:
            lat, lon = from_utm(zone, hemi, easting, nn)
        except (ValueError, OverflowError):
            continue
        top = band_lat + (12 if band == "X" else 8)
        if band_lat - 0.5 <= lat <= top + 0.5:
            best = (lat, lon)
            break
    if best is None:
        raise ValueError(f"square {text!r} does not fall in latitude band {band}")
    return {"lat": best[0], "lon": best[1], "square_m": size, "corner": "south-west"}


# ------------------------------------------------------------------ geohash and Plus Codes

_GH = "0123456789bcdefghjkmnpqrstuvwxyz"


def to_geohash(lat, lon, precision=9):
    lat_r, lon_r, bits, ch, even, out = [-90.0, 90.0], [-180.0, 180.0], 0, 0, True, []
    while len(out) < precision:
        rng, v = (lon_r, lon) if even else (lat_r, lat)
        mid = (rng[0] + rng[1]) / 2
        ch <<= 1
        if v >= mid:
            ch |= 1
            rng[0] = mid
        else:
            rng[1] = mid
        even = not even
        bits += 1
        if bits == 5:
            out.append(_GH[ch])
            bits, ch = 0, 0
    return "".join(out)


def from_geohash(gh):
    lat_r, lon_r, even = [-90.0, 90.0], [-180.0, 180.0], True
    for c in gh.lower():
        if c not in _GH:
            raise ValueError(f"not a geohash character: {c!r}")
        v = _GH.index(c)
        for k in range(4, -1, -1):
            rng = lon_r if even else lat_r
            mid = (rng[0] + rng[1]) / 2
            if v >> k & 1:
                rng[0] = mid
            else:
                rng[1] = mid
            even = not even
    return {"lat": (lat_r[0] + lat_r[1]) / 2, "lon": (lon_r[0] + lon_r[1]) / 2,
            "lat_error": (lat_r[1] - lat_r[0]) / 2, "lon_error": (lon_r[1] - lon_r[0]) / 2}


def to_pluscode(lat, lon, length=10):
    from openlocationcode import openlocationcode as olc
    return olc.encode(lat, lon, length)


def from_pluscode(code, ref_lat=None, ref_lon=None):
    """Full or short Plus Code. A short code (e.g. 'V8CR+2H') needs a reference point near it."""
    from openlocationcode import openlocationcode as olc
    c = code.strip().upper()
    if olc.isShort(c):
        if ref_lat is None or ref_lon is None:
            raise ValueError("short Plus Code: give a reference point near the place (--ref LAT,LON)")
        c = olc.recoverNearest(c, ref_lat, ref_lon)
    if not olc.isFull(c):
        raise ValueError(f"not a valid Plus Code: {code!r}")
    a = olc.decode(c)
    return {"code": c, "lat": a.latitudeCenter, "lon": a.longitudeCenter,
            "lat_error": (a.latitudeHi - a.latitudeLo) / 2, "lon_error": (a.longitudeHi - a.longitudeLo) / 2}


def all_formats(lat, lon):
    out = {"decimal": f"{lat:.6f}, {lon:.6f}", "dms": f"{to_dms(lat, 'lat')} {to_dms(lon, 'lon')}",
           "ddm": f"{to_ddm(lat, 'lat')} {to_ddm(lon, 'lon')}", "geohash": to_geohash(lat, lon)}
    try:
        u = to_utm(lat, lon)
        out["utm"] = f"{u['zone']}{u['band']} {u['easting']:.1f}E {u['northing']:.1f}N"
        out["mgrs"] = to_mgrs(lat, lon)
    except ValueError as e:
        out["utm"] = out["mgrs"] = f"n/a ({e})"
    try:
        out["pluscode"] = to_pluscode(lat, lon)
    except ImportError:
        out["pluscode"] = "n/a (openlocationcode not installed)"
    return out
