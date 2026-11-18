"""Sun and shadow. Pure Python, after the NOAA solar position equations (Meeus; as used in the NOAA
Solar Calculator). Expected accuracy for 1950-2050 is about 0.01 degrees in position, which is far
below the error of any elevation or bearing measured from a photo; the tolerance parameters below
are there for the photo, not for the formula.

Conventions: latitude north positive, longitude east positive, azimuth/bearing degrees clockwise
from true north, times are timezone-aware datetimes (UTC is used internally). "Apparent" elevation
includes atmospheric refraction (what a photo shows); "true" elevation does not.
"""
from __future__ import annotations

import datetime as dt
import math

SUNRISE_ALTITUDE = -0.833  # apparent upper limb on the horizon: refraction + semi-diameter (NOAA)


def _jd(t):
    t = t.astimezone(dt.UTC)
    return t.timestamp() / 86400.0 + 2440587.5


def solar_coordinates(t):
    """(declination_deg, equation_of_time_minutes) at datetime t."""
    T = (_jd(t) - 2451545.0) / 36525.0
    L0 = (280.46646 + T * (36000.76983 + T * 0.0003032)) % 360
    M = 357.52911 + T * (35999.05029 - 0.0001537 * T)
    e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)
    Mr = math.radians(M)
    C = (math.sin(Mr) * (1.914602 - T * (0.004817 + 0.000014 * T)) + math.sin(2 * Mr) * (0.019993 - 0.000101 * T)
         + math.sin(3 * Mr) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * T)
    app_long = math.radians(L0 + C - 0.00569 - 0.00478 * math.sin(omega))
    eps0 = 23 + (26 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60) / 60
    eps = math.radians(eps0 + 0.00256 * math.cos(omega))
    decl = math.degrees(math.asin(math.sin(eps) * math.sin(app_long)))
    y = math.tan(eps / 2) ** 2
    L0r = math.radians(L0)
    eot = 4 * math.degrees(y * math.sin(2 * L0r) - 2 * e * math.sin(Mr) + 4 * e * y * math.sin(Mr) * math.cos(2 * L0r)
                           - 0.5 * y * y * math.sin(4 * L0r) - 1.25 * e * e * math.sin(2 * Mr))
    return decl, eot


def refraction(true_elev):
    """Atmospheric refraction in degrees for a true elevation (NOAA approximation, standard atmosphere)."""
    h = true_elev
    if h > 85:
        return 0.0
    te = math.tan(math.radians(h))
    if h > 5:
        r = 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5
    elif h > -0.575:
        r = 1735 + h * (-518.2 + h * (103.4 + h * (-12.79 + h * 0.711)))
    else:
        r = -20.772 / te
    return r / 3600.0


def apparent_to_true(apparent):
    """Invert refraction: the true elevation whose apparent elevation is `apparent` (bisection)."""
    lo, hi = apparent - 1.0, apparent + 0.01
    for _ in range(60):
        mid = (lo + hi) / 2
        if mid + refraction(mid) < apparent:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def position(lat, lon, t, apparent=True):
    """Sun azimuth and elevation (degrees) seen from (lat, lon) at datetime t."""
    decl, eot = solar_coordinates(t)
    u = t.astimezone(dt.UTC)
    minutes = u.hour * 60 + u.minute + u.second / 60 + u.microsecond / 6e7
    tst = (minutes + eot + 4 * lon) % 1440
    ha = tst / 4 - 180
    la, de, har = math.radians(lat), math.radians(decl), math.radians(ha)
    cz = max(-1.0, min(1.0, math.sin(la) * math.sin(de) + math.cos(la) * math.cos(de) * math.cos(har)))
    zen = math.degrees(math.acos(cz))
    elev = 90 - zen
    den = math.cos(la) * math.sin(math.radians(zen))
    if abs(den) < 1e-12:
        az = 180.0 if lat > decl else 0.0
    else:
        ca = max(-1.0, min(1.0, (math.sin(la) * cz - math.sin(de)) / den))
        az = math.degrees(math.acos(ca))
        az = (az + 180) % 360 if ha > 0 else (540 - az) % 360
    if apparent:
        elev += refraction(elev)
    return {"azimuth": az, "elevation": elev, "declination": decl, "equation_of_time_min": eot,
            "hour_angle": ha, "apparent": apparent}


def subsolar_point(t):
    decl, eot = solar_coordinates(t)
    u = t.astimezone(dt.UTC)
    minutes = u.hour * 60 + u.minute + u.second / 60 + u.microsecond / 6e7
    lon = -(minutes + eot - 720) / 4
    return decl, ((lon + 180) % 360) - 180


def shadow(lat, lon, t, object_height=None):
    """Bearing the shadow points to and its length per unit height (None when the sun is down)."""
    p = position(lat, lon, t)
    out = {"sun_azimuth": p["azimuth"], "sun_elevation": p["elevation"], "shadow_bearing": (p["azimuth"] + 180) % 360}
    if p["elevation"] > 0:
        ratio = 1 / math.tan(math.radians(p["elevation"]))
        out["shadow_length_per_unit_height"] = ratio
        if object_height is not None:
            out["shadow_length"] = ratio * object_height
    else:
        out["note"] = "sun at or below the horizon: no shadow from direct sunlight"
    return out


def elevation_from_shadow(height, shadow_length):
    if height <= 0 or shadow_length <= 0:
        raise ValueError("height and shadow length must be positive")
    return math.degrees(math.atan2(height, shadow_length))


# ------------------------------------------------------------------------- rings and bands

def _destination(lat, lon, bearing, angle):
    """Point at angular distance `angle` (deg) from (lat, lon) along `bearing` on a sphere."""
    la, lo, b, d = map(math.radians, (lat, lon, bearing, angle))
    la2 = math.asin(math.sin(la) * math.cos(d) + math.cos(la) * math.sin(d) * math.cos(b))
    lo2 = lo + math.atan2(math.sin(b) * math.sin(d) * math.cos(la), math.cos(d) - math.sin(la) * math.sin(la2))
    return math.degrees(la2), ((math.degrees(lo2) + 180) % 360) - 180


def _circle(lat, lon, radius_deg, step=1.0):
    """Closed small circle as a list of [lon, lat] parts split at the antimeridian."""
    pts = [_destination(lat, lon, b, radius_deg) for b in [i * step for i in range(int(360 / step) + 1)]]
    parts, cur = [], []
    for la, lo in pts:
        if cur and abs(lo - cur[-1][0]) > 180:
            parts.append(cur)
            cur = []
        cur.append([round(lo, 4), round(la, 4)])
    parts.append(cur)
    if len(parts) > 1 and abs(parts[0][0][0] - parts[-1][-1][0]) < 1e-6:
        parts[0] = parts[-1] + parts[0][1:]
        parts.pop()
    return [p for p in parts if len(p) > 1]


def elevation_ring(elevation, t, tolerance=2.0, time_error_min=0.0, apparent=True, step=1.0):
    """GeoJSON FeatureCollection: every place where the sun had this elevation at time t.

    The ring is centred on the subsolar point with angular radius 90 - true elevation. The band
    edges use elevation +- tolerance; with a time error, the band edges are drawn at t +- error
    too (the subsolar point moves 0.25 degrees of longitude per minute). Never a bare line.
    """
    if not 0 < elevation < 90:
        raise ValueError("elevation must be between 0 and 90 degrees")
    if tolerance < 0 or time_error_min < 0:
        raise ValueError("tolerance and time error must be non-negative")

    def radius(h_app):
        h = apparent_to_true(h_app) if apparent else h_app
        return 90 - h
    t = t.astimezone(dt.UTC)
    times = [t] if time_error_min == 0 else [t - dt.timedelta(minutes=time_error_min), t,
                                             t + dt.timedelta(minutes=time_error_min)]
    feats = []
    sslat, sslon = subsolar_point(t)
    feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(sslon, 4), round(sslat, 4)]},
                  "properties": {"role": "subsolar point", "time_utc": t.isoformat()}})
    for tt in times:
        la, lo = subsolar_point(tt)
        for role, h in (("ring", elevation), ("band edge (elevation - tolerance)", elevation - tolerance),
                        ("band edge (elevation + tolerance)", elevation + tolerance)):
            if not 0 < h < 90 or (role == "ring" and tt != t):
                continue
            feats.append({"type": "Feature",
                          "geometry": {"type": "MultiLineString", "coordinates": _circle(la, lo, radius(h), step)},
                          "properties": {"role": role, "elevation_deg": round(h, 3), "time_utc": tt.isoformat(),
                                         "angular_radius_deg": round(radius(h), 3)}})
    width_km = abs(radius(max(0.01, elevation - tolerance)) - radius(min(89.99, elevation + tolerance))) * 111.195
    return {"type": "FeatureCollection", "features": feats, "properties": {
        "assumed_time_utc": t.isoformat(), "time_error_min": time_error_min, "elevation_deg": elevation,
        "tolerance_deg": tolerance, "elevation_is": "apparent (refraction removed before drawing)" if apparent else "true",
        "band_width_km_approx": round(width_km, 1),
        "note": ("The true place lies inside the band only if the time and the elevation are right within the "
                 "stated errors. A wrong date or time zone moves the whole ring.")}}


def daylight(lat, lon, date):
    """Sunrise, solar noon and sunset (UTC datetimes) for a UTC calendar date, by root finding."""
    start = dt.datetime(date.year, date.month, date.day, tzinfo=dt.UTC) - dt.timedelta(hours=lon / 15)
    noon = None
    best = -1e9
    for m in range(0, 1440, 10):
        e = position(lat, lon, start + dt.timedelta(minutes=m), apparent=False)["elevation"]
        if e > best:
            best, noon = e, start + dt.timedelta(minutes=m)
    lo, hi = noon - dt.timedelta(minutes=15), noon + dt.timedelta(minutes=15)
    for _ in range(40):  # golden-section-like refinement of the maximum
        m1 = lo + (hi - lo) / 3
        m2 = hi - (hi - lo) / 3
        if position(lat, lon, m1, apparent=False)["elevation"] < position(lat, lon, m2, apparent=False)["elevation"]:
            lo = m1
        else:
            hi = m2
    noon = lo + (hi - lo) / 2

    def f(tt):
        return position(lat, lon, tt, apparent=False)["elevation"] - SUNRISE_ALTITUDE

    def root(a, b):
        fa = f(a)
        for _ in range(50):
            m = a + (b - a) / 2
            fm = f(m)
            if (fm > 0) == (fa > 0):
                a, fa = m, fm
            else:
                b = m
        return a + (b - a) / 2
    out = {"solar_noon_utc": noon, "noon_elevation": position(lat, lon, noon)["elevation"]}
    if f(noon) <= 0:
        out.update(polar="night", day_length_h=0.0)
        return out
    if f(noon - dt.timedelta(hours=12)) > 0 and f(noon + dt.timedelta(hours=12)) > 0:
        out.update(polar="day", day_length_h=24.0)
        return out
    rise = root(noon - dt.timedelta(hours=12), noon)
    sset = root(noon + dt.timedelta(hours=12), noon)
    out.update(sunrise_utc=rise, sunset_utc=sset, day_length_h=(sset - rise).total_seconds() / 3600)
    return out


def latitudes_for_day_length(hours, date, tolerance_h=0.25):
    """Latitude bands where the day length is hours +- tolerance on that date (one per hemisphere)."""
    noon = dt.datetime(date.year, date.month, date.day, 12, tzinfo=dt.UTC)
    decl, _ = solar_coordinates(noon)

    def length(lat):
        la, de, h0 = map(math.radians, (lat, decl, SUNRISE_ALTITUDE))
        c = (math.sin(h0) - math.sin(la) * math.sin(de)) / (math.cos(la) * math.cos(de))
        if c <= -1:
            return 24.0
        if c >= 1:
            return 0.0
        return 2 * math.degrees(math.acos(c)) / 15

    # scan latitudes and group the runs that match (correct near the equator, where the day length
    # hardly changes with latitude, and near the poles)
    lats = [round(-89.9 + i * 0.05, 2) for i in range(int(179.8 / 0.05) + 1)]
    bands, run = [], []
    for la in lats:
        if abs(length(la) - hours) <= tolerance_h:
            run.append(la)
        elif run:
            bands.append({"from_lat": run[0], "to_lat": run[-1]})
            run = []
    if run:
        bands.append({"from_lat": run[0], "to_lat": run[-1]})
    return {"date": date.isoformat(), "day_length_h": hours, "tolerance_h": tolerance_h,
            "declination_deg": round(decl, 3), "bands": bands,
            "note": "Uses sunrise/sunset at -0.833 deg; local horizon (hills, buildings) shortens the visible day."}


def times_matching(lat, lon, date, shadow_bearing=None, elevation=None, tolerance=2.0):
    """UTC times on a local date when the shadow points along `shadow_bearing` and/or the sun has `elevation`.

    Scans the day at 1-minute steps and returns each stretch of matching minutes with its centre.
    """
    if shadow_bearing is None and elevation is None:
        raise ValueError("give a shadow bearing, an elevation, or both")
    start = dt.datetime(date.year, date.month, date.day, tzinfo=dt.UTC) - dt.timedelta(hours=lon / 15)
    start = start.replace(second=0, microsecond=0)  # whole UTC minutes
    runs, cur = [], []
    for m in range(1440):
        tt = start + dt.timedelta(minutes=m)
        p = position(lat, lon, tt)
        if p["elevation"] <= 0:
            ok = False
        else:
            ok = True
            if shadow_bearing is not None:
                d = abs(((p["azimuth"] + 180) % 360 - shadow_bearing + 180) % 360 - 180)
                ok &= d <= tolerance
            if elevation is not None:
                ok &= abs(p["elevation"] - elevation) <= tolerance
        if ok:
            cur.append((tt, p))
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    out = []
    for r in runs:
        mid = r[len(r) // 2]
        out.append({"from_utc": r[0][0].isoformat(), "to_utc": r[-1][0].isoformat(), "centre_utc": mid[0].isoformat(),
                    "sun_azimuth": round(mid[1]["azimuth"], 2), "sun_elevation": round(mid[1]["elevation"], 2)})
    return out


def parse_time(text, utc_offset=None):
    """ISO datetime. A time without a zone is refused unless utc_offset ("+01:00") is given."""
    t = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if t.tzinfo is None:
        if utc_offset is None:
            raise ValueError("the time has no time zone: add Z or +HH:MM, or pass --utc-offset")
        sign = -1 if utc_offset.startswith("-") else 1
        hh, mm = utc_offset.lstrip("+-").split(":")
        t = t.replace(tzinfo=dt.timezone(sign * dt.timedelta(hours=int(hh), minutes=int(mm))))
    return t
