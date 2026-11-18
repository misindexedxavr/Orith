"""Clue-to-query logic: build_query() is pure, so these run without network."""
import pytest

from orith import osm

AROUND = {"around": [33.9197, 8.1335, 5000]}


def test_simple_feature_around_a_point():
    q = osm.build_query({"feature": "mosque", "area": AROUND})
    assert q == ('[out:json][timeout:40];'
                 '(nwr["amenity"="place_of_worship"]["religion"="muslim"](around:5000,33.919700,8.133500););'
                 'out tags bb 400;')


def test_feature_with_alternatives_becomes_a_union():
    q = osm.build_query({"feature": "castle", "area": {"bbox": [36.0, 9.0, 37.0, 10.5]}})
    box = "(36.000000,9.000000,37.000000,10.500000)"
    assert q.count("nwr[") == 3
    for tag in ('["historic"="castle"]', '["historic"="fort"]', '["historic"="citywalls"]'):
        assert f"nwr{tag}{box};" in q


def test_name_is_a_case_insensitive_regex_on_every_alternative():
    q = osm.build_query({"feature": "bridge", "name": "pont|bridge", "area": AROUND})
    assert q.count(f'[~"{osm.NAME_KEY_RE}"~"pont|bridge",i]') == 3


def test_extra_tags_are_anded_and_star_means_key_present():
    q = osm.build_query({"feature": "tower", "tags": {"tower:type": "communication", "height": "*"}, "area": AROUND})
    assert '["man_made"="tower"]["tower:type"="communication"]["height"]' in q


def test_tags_only_and_name_only_queries_are_allowed():
    assert 'nwr["shop"="bakery"]' in osm.build_query({"tags": {"shop": "bakery"}, "area": AROUND})
    assert f'nwr[~"{osm.NAME_KEY_RE}"~"Lycee",i]' in osm.build_query({"name": "Lycee", "area": AROUND})


def test_near_builds_a_two_step_proximity_query():
    q = osm.build_query({"feature": "mosque", "near": {"feature": "roundabout", "within_m": 150}, "area": AROUND})
    assert q == ('[out:json][timeout:40];'
                 '(nwr["amenity"="place_of_worship"]["religion"="muslim"](around:5000,33.919700,8.133500);)->.a;'
                 '(nwr["junction"="roundabout"](around.a:150);)->.b;'
                 'nwr.a(around.b:150);out tags bb 400;')


def test_feature_words_are_normalised():
    a = osm.build_query({"feature": "Water Tower", "area": AROUND})
    b = osm.build_query({"feature": "water-tower", "area": AROUND})
    assert a == b and '["man_made"="water_tower"]' in a


def test_quotes_and_backslashes_cannot_break_out_of_the_string():
    q = osm.build_query({"name": 'x"];node(1);out;//\\', "area": AROUND})
    assert '\\"' in q and '\\\\' in q
    assert q.count(";out tags bb") == 1 and "node(1)" in q  # still inside the quoted regex
    assert q.split(f'[~"{osm.NAME_KEY_RE}"~')[1].startswith('"x\\"];node(1);out;//\\\\",i]')


def test_limit_is_applied_and_bounded():
    assert osm.build_query({"feature": "fuel", "area": AROUND, "limit": 5}).endswith("out tags bb 5;")
    with pytest.raises(osm.SpecError):
        osm.build_query({"feature": "fuel", "area": AROUND, "limit": 5000})


@pytest.mark.parametrize("spec", [
    {"feature": "mosque"},                                                    # no area: unbounded query refused
    {"feature": "mosque", "area": {}},
    {"feature": "mosque", "area": {"around": [33.9, 8.1, 500000]}},           # radius too large
    {"feature": "mosque", "area": {"around": [133.9, 8.1, 500]}},             # bad latitude
    {"feature": "mosque", "area": {"bbox": [30.0, 7.0, 37.5, 11.6]}},         # whole country: too large
    {"feature": "mosque", "area": {"bbox": [37.0, 9.0, 36.0, 10.0]}},         # south > north
    {"feature": "spaceport", "area": AROUND},                                 # not in the vocabulary
    {"area": AROUND},                                                         # nothing to look for
    {"feature": "mosque", "area": AROUND, "radius": 3},                       # unknown key
    {"feature": "mosque", "tags": {"bad key": "x"}, "area": AROUND},
    {"feature": "mosque", "near": {"feature": "roundabout", "within_m": 50000}, "area": AROUND},
    {"feature": "mosque", "near": {"within_m": 100}, "area": AROUND},         # near needs something to be near
    "mosque",
])
def test_unsafe_or_meaningless_specs_are_refused(spec):
    with pytest.raises(osm.SpecError):
        osm.build_query(spec)


def test_every_vocabulary_entry_builds_a_query():
    for word in osm.VOCAB:
        q = osm.build_query({"feature": word, "area": AROUND})
        assert q.startswith("[out:json]") and q.count("(around:5000,") == len(osm.VOCAB[word])


def test_parse_elements_uses_node_position_or_bbox_centre_and_sorts_by_distance():
    payload = {"elements": [
        {"type": "way", "id": 7, "bounds": {"minlat": 33.929, "minlon": 8.139, "maxlat": 33.931, "maxlon": 8.141}, "tags": {"name:fr": "Grande Mosquee", "amenity": "place_of_worship", "junk": "x"}},
        {"type": "node", "id": 3, "lat": 33.9198, "lon": 8.1336, "tags": {"name": "Jamaa"}},
        {"type": "relation", "id": 9},  # no geometry: skipped
    ]}
    out = osm.parse_elements(payload, sort_near=(33.9197, 8.1335))
    assert [o["ref"] for o in out] == ["node/3", "way/7"]
    assert out[0]["precision_m"] == 0 and 140 < out[1]["precision_m"] < 150  # half-diagonal of a ~220 x 185 m box
    assert out[1]["name"] == "Grande Mosquee" and "junk" not in out[1]["tags"]
    assert out[0]["distance_m"] < 30 < out[1]["distance_m"]


def test_parse_elements_handles_an_empty_answer():
    assert osm.parse_elements({"elements": []}) == [] and osm.parse_elements(None) == []
