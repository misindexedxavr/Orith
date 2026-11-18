"""Sun maths: checked against astral (independent implementation; dev-only, skipped when absent)
and against physical invariants that need no reference values."""
import datetime as dt
import math
import random

import pytest

from orith import sun

UTC = dt.UTC
CASES = [  # (lat, lon, utc time)
    (36.80, 10.18, dt.datetime(2024, 6, 21, 11, 0, tzinfo=UTC)),
    (-33.87, 151.21, dt.datetime(2023, 12, 1, 3, 30, tzinfo=UTC)),
    (64.15, -21.94, dt.datetime(2025, 3, 20, 14, 15, tzinfo=UTC)),
    (25.20, 55.27, dt.datetime(2022, 9, 9, 6, 45, tzinfo=UTC)),
    (40.71, -74.01, dt.datetime(2021, 1, 15, 20, 5, tzinfo=UTC)),
    (-1.29, 36.82, dt.datetime(2026, 7, 4, 9, 0, tzinfo=UTC)),
]


def test_against_astral_position():
    astral = pytest.importorskip("astral")
    from astral import Observer
    from astral import sun as asun
    rng = random.Random(1)
    cases = CASES + [(rng.uniform(-60, 60), rng.uniform(-180, 180),
                      dt.datetime(2020, 1, 1, tzinfo=UTC) + dt.timedelta(minutes=rng.randrange(0, 60 * 24 * 365 * 8)))
                     for _ in range(40)]
    checked = 0
    for lat, lon, t in cases:
        ours = sun.position(lat, lon, t)
        o = Observer(latitude=lat, longitude=lon)
        e = asun.elevation(o, t, with_refraction=True)
        if e < 2:  # refraction models differ near the horizon; position is compared above 2 degrees
            continue
        assert ours["elevation"] == pytest.approx(e, abs=0.05), (lat, lon, t)
        a = asun.azimuth(o, t)
        assert abs((ours["azimuth"] - a + 180) % 360 - 180) < 0.1, (lat, lon, t)
        checked += 1
    assert checked >= 20
    assert astral  # keeps the import meaningful


def test_against_astral_sunrise_sunset():
    pytest.importorskip("astral")
    from astral import Observer
    from astral import sun as asun
    for lat, lon, t in CASES:
        d = t.date()
        ours = sun.daylight(lat, lon, d)
        if "sunrise_utc" not in ours:
            continue
        o = Observer(latitude=lat, longitude=lon)
        # astral returns the events of a UTC calendar day: ask for the day each event falls on
        rise = asun.sunrise(o, date=ours["sunrise_utc"].date(), tzinfo=UTC)
        sset = asun.sunset(o, date=ours["sunset_utc"].date(), tzinfo=UTC)
        assert abs((ours["sunrise_utc"] - rise).total_seconds()) < 60, (lat, lon)
        assert abs((ours["sunset_utc"] - sset).total_seconds()) < 60, (lat, lon)


def test_declination_limits_and_equinox():
    decls = [sun.solar_coordinates(dt.datetime(2024, 1, 1, tzinfo=UTC) + dt.timedelta(days=d))[0] for d in range(366)]
    assert max(decls) == pytest.approx(23.44, abs=0.02) and min(decls) == pytest.approx(-23.44, abs=0.02)
    assert abs(sun.solar_coordinates(dt.datetime(2024, 3, 20, 3, 6, tzinfo=UTC))[0]) < 0.05  # March equinox 2024


def test_subsolar_point_has_the_sun_overhead():
    for _, _, t in CASES:
        la, lo = sun.subsolar_point(t)
        assert sun.position(la, lo, t, apparent=False)["elevation"] == pytest.approx(90, abs=0.01)


def test_shadow_points_away_from_the_sun_and_scales_with_elevation():
    lat, lon, t = CASES[0]
    s = sun.shadow(lat, lon, t, object_height=10)
    assert (s["shadow_bearing"] - s["sun_azimuth"]) % 360 == pytest.approx(180)
    assert s["shadow_length"] == pytest.approx(10 / math.tan(math.radians(s["sun_elevation"])))
    assert sun.elevation_from_shadow(10, 10) == pytest.approx(45)


def test_northern_midlatitude_noon_sun_is_due_south():
    d = sun.daylight(48.85, 2.35, dt.date(2024, 6, 21))
    p = sun.position(48.85, 2.35, d["solar_noon_utc"])
    assert abs((p["azimuth"] - 180 + 180) % 360 - 180) < 0.5
    assert p["elevation"] == pytest.approx(90 - 48.85 + 23.44, abs=0.1)


def test_every_point_on_the_ring_sees_the_requested_elevation():
    t = dt.datetime(2024, 6, 21, 11, 0, tzinfo=UTC)
    fc = sun.elevation_ring(40.0, t, tolerance=2.0)
    ring = next(f for f in fc["features"] if f["properties"]["role"] == "ring")
    pts = [p for part in ring["geometry"]["coordinates"] for p in part][::7]
    for lon, lat in pts:
        assert sun.position(lat, lon, t)["elevation"] == pytest.approx(40.0, abs=0.05)
    edges = [f for f in fc["features"] if f["properties"]["role"].startswith("band edge")]
    assert {f["properties"]["elevation_deg"] for f in edges} == {38.0, 42.0}
    assert fc["properties"]["tolerance_deg"] == 2.0 and fc["properties"]["assumed_time_utc"].startswith("2024-06-21T11:00")
    assert 400 < fc["properties"]["band_width_km_approx"] < 500  # 4 degrees of arc


def test_time_error_adds_shifted_band_edges():
    t = dt.datetime(2024, 6, 21, 11, 0, tzinfo=UTC)
    fc = sun.elevation_ring(40.0, t, tolerance=1.0, time_error_min=30)
    times = {f["properties"]["time_utc"] for f in fc["features"] if f["properties"]["role"].startswith("band edge")}
    assert len(times) == 3 and fc["properties"]["time_error_min"] == 30


def test_ring_input_validation():
    t = dt.datetime(2024, 6, 21, 11, 0, tzinfo=UTC)
    for bad in (0, 90, -5):
        with pytest.raises(ValueError):
            sun.elevation_ring(bad, t)
    with pytest.raises(ValueError):
        sun.elevation_ring(30, t, tolerance=-1)


def test_day_length_bands_contain_the_true_latitude():
    for lat, lon, t in CASES:
        d = sun.daylight(lat, lon, t.date())
        if "sunrise_utc" not in d:
            continue
        res = sun.latitudes_for_day_length(d["day_length_h"], t.date(), tolerance_h=0.1)
        assert any(b["from_lat"] - 0.5 <= lat <= b["to_lat"] + 0.5 for b in res["bands"]), (lat, res)


def test_polar_day_and_night():
    assert sun.daylight(78.2, 15.6, dt.date(2024, 6, 21))["polar"] == "day"
    assert sun.daylight(78.2, 15.6, dt.date(2024, 12, 21))["polar"] == "night"


def test_times_matching_recovers_the_time_from_a_shadow_bearing():
    lat, lon, t = CASES[0]
    s = sun.shadow(lat, lon, t)
    runs = sun.times_matching(lat, lon, t.date(), shadow_bearing=s["shadow_bearing"], tolerance=0.5)
    assert any(dt.datetime.fromisoformat(r["from_utc"]) <= t <= dt.datetime.fromisoformat(r["to_utc"]) for r in runs)
    both = sun.times_matching(lat, lon, t.date(), shadow_bearing=s["shadow_bearing"],
                              elevation=s["sun_elevation"], tolerance=0.5)
    assert len(both) == 1


def test_parse_time_requires_a_zone():
    with pytest.raises(ValueError):
        sun.parse_time("2024-06-21T12:00:00")
    assert sun.parse_time("2024-06-21T12:00:00", utc_offset="+02:00").utcoffset() == dt.timedelta(hours=2)
    assert sun.parse_time("2024-06-21T12:00:00Z").utcoffset() == dt.timedelta(0)


def test_refraction_inversion_round_trips():
    for h in (1, 5, 10, 30, 60):
        assert sun.apparent_to_true(h) + sun.refraction(sun.apparent_to_true(h)) == pytest.approx(h, abs=1e-6)
