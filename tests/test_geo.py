"""Geometry and conversions. No external oracle is installed, so UTM is checked against properties
that any correct transverse Mercator must have: an independent numerical meridian-arc integral on the
central meridian, conformality (equal scale east and north) away from it, and forward/inverse round
trips. MGRS, geohash and DMS are checked by round trips and by their published structure."""
import math
import random

import pytest

from orith import cli, geo

A, F = 6378137.0, 1 / 298.257223563
E2 = F * (2 - F)


def meridian_arc(lat_deg, steps=20000):
    """Simpson integral of the meridian radius of curvature M(phi) = a(1-e^2)/(1-e^2 sin^2 phi)^1.5."""
    phi = math.radians(lat_deg)
    h = phi / steps

    def m(p):
        return A * (1 - E2) / (1 - E2 * math.sin(p) ** 2) ** 1.5
    s = m(0) + m(phi) + sum((4 if i % 2 else 2) * m(i * h) for i in range(1, steps))
    return s * h / 3


# ------------------------------------------------------------------ sphere

def test_distance_bearing_destination_round_trip():
    rng = random.Random(3)
    for _ in range(200):
        la, lo = rng.uniform(-80, 80), rng.uniform(-180, 180)
        b, d = rng.uniform(0, 360), rng.uniform(0.01, 3000)
        la2, lo2 = geo.destination(la, lo, b, d)
        assert geo.distance_km(la, lo, la2, lo2) == pytest.approx(d, rel=1e-9, abs=1e-6)
        assert abs((geo.bearing(la, lo, la2, lo2) - b + 180) % 360 - 180) < 1e-6


def test_quarter_meridian_and_cardinal_bearings():
    assert geo.distance_km(0, 0, 90, 0) == pytest.approx(math.pi / 2 * geo.R_KM)
    assert geo.bearing(0, 0, 1, 0) == pytest.approx(0) and geo.bearing(0, 0, 0, 1) == pytest.approx(90)


def test_intersection_of_two_bearing_lines():
    target = (36.85, 10.30)
    a, b = (36.80, 10.20), (36.90, 10.40)
    p = geo.intersection(*a, geo.bearing(*a, *target), *b, geo.bearing(*b, *target))
    assert geo.distance_km(*p, *target) < 1e-6
    assert geo.intersection(*a, 0, *b, 0) is None or geo.distance_km(*geo.intersection(*a, 0, *b, 0), *a) > 1000


def test_resection_with_absolute_bearings_recovers_the_observer():
    obs = (36.8000, 10.1800)
    lms = [(36.8060, 10.1850), (36.7950, 10.1900), (36.8020, 10.1700)]
    r = geo.resection_bearings([(la, lo, geo.bearing(*obs, la, lo)) for la, lo in lms])
    assert geo.distance_km(r["lat"], r["lon"], *obs) * 1000 < 0.5
    assert max(abs(x["residual_deg"]) for x in r["residuals"]) < 0.01


def test_resection_reports_residuals_for_noisy_bearings():
    obs = (48.85, 2.35)
    lms = [(48.855, 2.355), (48.846, 2.359), (48.852, 2.340)]
    noisy = [(la, lo, geo.bearing(*obs, la, lo) + d) for (la, lo), d in zip(lms, (1.5, -1.0, 0.5))]
    r = geo.resection_bearings(noisy)
    assert geo.distance_km(r["lat"], r["lon"], *obs) * 1000 < 60
    assert len(r["residuals"]) == 3 and any(x["residual_deg"] != 0 for x in r["residuals"])


def test_resection_refuses_parallel_bearings():
    with pytest.raises(ValueError):
        geo.resection_bearings([(10, 10, 45), (10.1, 10.1, 45)])


def test_resection_without_compass_recovers_observer_and_offset():
    obs = (25.20, 55.27)
    lms = [(25.212, 55.281), (25.205, 55.290), (25.190, 55.285), (25.215, 55.262)]
    offset = 37.0  # unknown to the solver: relative bearings are true bearings minus this
    rel = [(la, lo, geo.bearing(*obs, la, lo) - offset) for la, lo in lms]
    r = geo.resection_angles(rel)
    assert geo.distance_km(r["lat"], r["lon"], *obs) * 1000 < 1
    assert abs((r["heading_offset_deg"] - offset + 180) % 360 - 180) < 0.05


# ------------------------------------------------------------------ DMS

@pytest.mark.parametrize("text,lat,lon", [
    ("36.8700, 10.3414", 36.87, 10.3414),
    ("36°52'12\"N 10°20'29\"E", 36 + 52 / 60 + 12 / 3600, 10 + 20 / 60 + 29 / 3600),
    ("10°20'29\"E, 36°52'12\"N", 36 + 52 / 60 + 12 / 3600, 10 + 20 / 60 + 29 / 3600),
    ("33 51 54 S, 151 12 34 E", -(33 + 51 / 60 + 54 / 3600), 151 + 12 / 60 + 34 / 3600),
    ("N36.87 W5.5", 36.87, -5.5),
    ("-22.9068, -43.1729", -22.9068, -43.1729),
    ("40°42.767'N 74°0.360'W", 40 + 42.767 / 60, -(74 + 0.36 / 60)),
])
def test_parse_point(text, lat, lon):
    la, lo = geo.parse_point(text)
    assert la == pytest.approx(lat, abs=1e-9) and lo == pytest.approx(lon, abs=1e-9)


@pytest.mark.parametrize("bad", ["91, 10", "36°75'N 10°E", "N36 S10", "hello"])
def test_parse_point_refuses_bad_input(bad):
    with pytest.raises(ValueError):
        geo.parse_point(bad)


def test_dms_format_round_trip():
    rng = random.Random(5)
    for _ in range(200):
        la, lo = rng.uniform(-90, 90), rng.uniform(-180, 180)
        txt = f"{geo.to_dms(la, 'lat')} {geo.to_dms(lo, 'lon')}"
        la2, lo2 = geo.parse_point(txt)
        assert abs(la2 - la) < 0.01 / 3600 + 1e-9 and abs(lo2 - lo) < 0.01 / 3600 + 1e-9


# ------------------------------------------------------------------ UTM

def test_utm_central_meridian_matches_an_independent_meridian_arc_integral():
    for lat in (0.0, 10.0, 36.8, 52.5, 70.0, 83.9):
        u = geo.to_utm(lat, 9.0, zone=32)  # 9 E is the central meridian of zone 32 (forced: Svalbard rule at 83.9)
        assert u["zone"] == 32 and u["easting"] == pytest.approx(500000.0, abs=1e-6)
        assert u["northing"] == pytest.approx(0.9996 * meridian_arc(lat), abs=0.001)


def test_utm_southern_hemisphere_false_northing():
    u = geo.to_utm(-30.0, 147.0)
    assert u["hemisphere"] == "S" and u["northing"] == pytest.approx(10000000 - 0.9996 * meridian_arc(30.0), abs=0.001)


def test_utm_is_conformal_away_from_the_central_meridian():
    # equal grid scale along the parallel and along the meridian (a property of conformal maps)
    for lat, lon in ((36.8, 10.2), (-33.9, 151.2), (60.0, 5.0), (5.0, -76.0)):
        d = 1e-6
        u0, ue, un = geo.to_utm(lat, lon), geo.to_utm(lat, lon + d, zone=geo.utm_zone(lat, lon)), geo.to_utm(lat + d, lon)
        phi = math.radians(lat)
        nu = A / math.sqrt(1 - E2 * math.sin(phi) ** 2)
        rho = A * (1 - E2) / (1 - E2 * math.sin(phi) ** 2) ** 1.5
        k_east = math.hypot(ue["easting"] - u0["easting"], ue["northing"] - u0["northing"]) / (nu * math.cos(phi) * math.radians(d))
        k_north = math.hypot(un["easting"] - u0["easting"], un["northing"] - u0["northing"]) / (rho * math.radians(d))
        assert k_east == pytest.approx(k_north, rel=1e-6)


def test_utm_round_trip_under_a_millimetre():
    rng = random.Random(7)
    for _ in range(500):
        la, lo = rng.uniform(-79.9, 83.9), rng.uniform(-179.9, 179.9)
        u = geo.to_utm(la, lo)
        la2, lo2 = geo.from_utm(u["zone"], u["hemisphere"], u["easting"], u["northing"])
        assert geo.distance_km(la, lo, la2, lo2) * 1e6 < 1.0  # millimetres


def test_utm_zone_exceptions():
    assert geo.utm_zone(60.0, 5.0) == 32   # south-west Norway
    assert geo.utm_zone(78.0, 15.0) == 33  # Svalbard
    assert geo.utm_zone(36.8, 10.2) == 32
    with pytest.raises(ValueError):
        geo.to_utm(85.0, 0.0)


# ------------------------------------------------------------------ MGRS, geohash, Plus Codes

def test_mgrs_structure_and_round_trip():
    rng = random.Random(11)
    for _ in range(300):
        la, lo = rng.uniform(-79.5, 83.5), rng.uniform(-179.9, 179.9)
        m = geo.to_mgrs(la, lo, precision=5)
        u = geo.to_utm(la, lo)
        assert m.startswith(f"{u['zone']}{u['band']}") and len(m) == len(str(u["zone"])) + 13
        assert int(m[-10:-5]) == int(u["easting"] % 100000) and int(m[-5:]) == int(u["northing"] % 100000)
        back = geo.from_mgrs(m)
        assert geo.distance_km(la, lo, back["lat"], back["lon"]) * 1000 < 1.5  # 1 m square, SW corner


def test_mgrs_rejects_bad_references():
    for bad in ("32SXX12", "99SNV1234", "32SNV123", "hello"):
        with pytest.raises(ValueError):
            geo.from_mgrs(bad)


def test_geohash_round_trip_and_known_structure():
    rng = random.Random(13)
    for _ in range(200):
        la, lo = rng.uniform(-90, 90), rng.uniform(-180, 180)
        g = geo.to_geohash(la, lo, 9)
        d = geo.from_geohash(g)
        assert abs(d["lat"] - la) <= d["lat_error"] + 1e-12 and abs(d["lon"] - lo) <= d["lon_error"] + 1e-12
    assert geo.to_geohash(0.0001, 0.0001, 1) == "s"  # the north-east quadrant cell around (0, 0)


def test_pluscode_round_trip_and_short_code():
    pytest.importorskip("openlocationcode")
    code = geo.to_pluscode(36.8700, 10.3414)
    full = geo.from_pluscode(code)
    assert abs(full["lat"] - 36.87) < full["lat_error"] * 1.01 and abs(full["lon"] - 10.3414) < full["lon_error"] * 1.01
    short = code[4:]
    with pytest.raises(ValueError):
        geo.from_pluscode(short)
    rec = geo.from_pluscode(short, 36.8, 10.3)
    assert rec["code"] == code


def test_cli_convert_and_geometry(capsys):
    import json
    assert cli.main(["convert", "36°52'12\"N 10°20'29\"E"]) == 0
    o = json.loads(capsys.readouterr().out)
    assert o["lat"] == pytest.approx(36.87, abs=1e-3) and o["formats"]["mgrs"].startswith("32S")
    assert cli.main(["convert", o["formats"]["mgrs"]]) == 0
    o2 = json.loads(capsys.readouterr().out)
    assert abs(o2["lat"] - o["lat"]) < 1e-4
    assert cli.main(["distance", "36.8,10.18", "36.9,10.3"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["distance_km"] == pytest.approx(geo.distance_km(36.8, 10.18, 36.9, 10.3), abs=1e-3)


def test_utm_against_pyproj_oracle():
    pyproj = pytest.importorskip("pyproj")  # dev-only oracle
    rng = random.Random(17)
    for _ in range(200):
        la, lo = rng.uniform(-79.9, 83.9), rng.uniform(-179.9, 179.9)
        u = geo.to_utm(la, lo)
        epsg = (32600 if u["hemisphere"] == "N" else 32700) + u["zone"]
        e, n = pyproj.Transformer.from_crs(4326, epsg, always_xy=True).transform(lo, la)
        assert u["easting"] == pytest.approx(e, abs=0.001) and u["northing"] == pytest.approx(n, abs=0.001)


def test_tz_country_lists_every_zone(capsys):
    import json
    assert cli.main(["tz", "--country", "us", "--time", "2024-01-15T12:00:00Z"]) == 0
    o = json.loads(capsys.readouterr().out)
    names = {z["zone"] for z in o["zones"]}
    assert {"America/New_York", "America/Los_Angeles", "Pacific/Honolulu"} <= names and len(names) > 20
    assert cli.main(["tz", "--country", "TN"]) == 0
    assert [z["zone"] for z in json.loads(capsys.readouterr().out)["zones"]] == ["Africa/Tunis"]
