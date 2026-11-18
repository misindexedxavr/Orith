"""The verdict rule. These tests are the specification of when "IDENTIFIED" may appear."""
import pytest

from orith import ledger

P = (36.8000, 10.1800)          # the true point in these scenarios
NEAR = (36.8005, 10.1803)       # about 60 m away
FAR = (36.9000, 10.3000)        # about 15 km away


def base_case():
    """Three hypotheses, three independent anchored clues at P, one of them geometry."""
    c = ledger.new_case("x.jpg")
    e1 = ledger.add_evidence(c, "osm", *P, name="mosque", ref="node/1")
    e2 = ledger.add_evidence(c, "osm", *NEAR, name="fountain", ref="node/2")
    e3 = ledger.add_evidence(c, "nominatim", *NEAR, name="Cafe X", precision_m=20, ref="node/3")
    h1 = ledger.add_hypothesis(c, "square A", *P)
    ledger.add_hypothesis(c, "square B", *FAR)
    ledger.add_hypothesis(c, "square C", 35.0, 9.0)
    k1 = ledger.add_clue(c, "minaret left of dome matches map", "HARD", 5, 5, 5, "layout", geometry=True, evidence=e1["id"])
    k2 = ledger.add_clue(c, "octagonal fountain", "SOFT", 4, 4, 4, "fountain", evidence=e2["id"])
    k3 = ledger.add_clue(c, "shop sign 'Cafe X'", "SOFT", 4, 2, 4, "signage", evidence=e3["id"])
    for k in (k1, k2, k3):
        ledger.add_link(c, k["id"], h1["id"], "supports")
    return c


def test_identified_when_every_condition_holds():
    ev = ledger.evaluate(base_case())
    assert ev["verdict"] == "IDENTIFIED" and ev["top"] == "h1" and ev["unmet"] == []


def test_two_agreeing_clues_are_not_enough():
    c = base_case()
    c["links"] = [l for l in c["links"] if l["clue"] != "c3"]
    ev = ledger.evaluate(c)
    assert ev["verdict"] == "CANDIDATE" and any(u.startswith("A:") for u in ev["unmet"])


def test_no_geometry_means_candidate():
    c = base_case()
    c["clues"][0]["geometry"] = False
    ev = ledger.evaluate(c)
    assert ev["verdict"] == "CANDIDATE" and any(u.startswith("B:") for u in ev["unmet"])


def test_any_contradiction_blocks_identification():
    c = base_case()
    k = ledger.add_clue(c, "sun is on the wrong side for the stated time", "SOFT", 2, 5, 2, "sun")
    ledger.add_link(c, k["id"], "h1", "contradicts")
    ev = ledger.evaluate(c)
    assert ev["verdict"] == "CANDIDATE" and any(u.startswith("C:") for u in ev["unmet"])
    assert ev["ranking"][0]["status"] == "alive"  # a soft contradiction lowers the score, it does not eliminate


def test_fewer_than_three_hypotheses_blocks_identification():
    c = base_case()
    c["hypotheses"] = c["hypotheses"][:2]
    ev = ledger.evaluate(c)
    assert ev["verdict"] == "CANDIDATE" and any(u.startswith("D:") for u in ev["unmet"])


def test_same_group_counts_once():
    c = base_case()
    c["clues"][2]["group"] = c["clues"][1]["group"]
    ev = ledger.evaluate(c)
    assert ev["ranking"][0]["independent_agreeing"] == 2 and ev["verdict"] == "CANDIDATE"


def test_same_evidence_counts_once_even_across_groups():
    c = base_case()
    c["clues"][2]["evidence"] = c["clues"][1]["evidence"]
    ev = ledger.evaluate(c)
    assert ev["ranking"][0]["independent_agreeing"] == 2 and ev["verdict"] == "CANDIDATE"


def test_unanchored_clues_rank_but_never_count_as_agreeing():
    c = ledger.new_case("x.jpg")
    h = ledger.add_hypothesis(c, "A", *P)
    ledger.add_hypothesis(c, "B", *FAR)
    ledger.add_hypothesis(c, "C", 35.0, 9.0)
    for i in range(5):
        k = ledger.add_clue(c, f"looks like it {i}", "SOFT", 3, 3, 3, f"g{i}")
        ledger.add_link(c, k["id"], h["id"], "supports")
    ev = ledger.evaluate(c)
    assert ev["top"] == "h1" and ev["ranking"][0]["score"] > 0
    assert ev["ranking"][0]["independent_agreeing"] == 0 and ev["verdict"] == "CANDIDATE"


def test_evidence_far_from_the_hypothesis_does_not_agree():
    c = base_case()
    c["evidence"][1]["lat"], c["evidence"][1]["lon"] = FAR
    ev = ledger.evaluate(c)
    assert ev["ranking"][0]["independent_agreeing"] == 2 and ev["verdict"] == "CANDIDATE"
    assert any("not counted as agreeing" in w for w in ev["warnings"])


def test_imprecise_evidence_does_not_agree():
    """A delegation's label point (kilometres of uncertainty) cannot pin a point."""
    c = base_case()
    c["evidence"][2]["precision_m"] = 8000
    ev = ledger.evaluate(c)
    assert ev["ranking"][0]["independent_agreeing"] == 2 and ev["verdict"] == "CANDIDATE"


def test_hard_clue_eliminates_and_ranking_puts_it_last():
    c = base_case()
    k = ledger.add_clue(c, "drives on the left", "HARD", 3, 5, 5, "traffic")
    ledger.add_link(c, k["id"], "h2", "contradicts")
    ev = ledger.evaluate(c)
    row = {r["id"]: r for r in ev["ranking"]}
    assert row["h2"]["status"] == "eliminated" and row["h2"]["eliminated_by"] == [k["id"]]
    assert ev["ranking"][-1]["id"] == "h2" and ev["verdict"] == "IDENTIFIED"  # elimination of a rival is fine


def test_unreliable_hard_clue_is_downgraded_to_soft():
    c = base_case()
    k = ledger.add_clue(c, "blurry plate looks foreign", "HARD", 3, 2, 2, "plate")
    ledger.add_link(c, k["id"], "h2", "contradicts")
    ev = ledger.evaluate(c)
    row = {r["id"]: r for r in ev["ranking"]}
    assert row["h2"]["status"] == "alive" and row["h2"]["score"] < 0
    assert any("treated as SOFT" in w for w in ev["warnings"])


def test_rival_that_also_satisfies_the_rule_blocks_identification():
    c = base_case()
    for i, grp in enumerate(("layout2", "fountain2", "signage2")):
        e = ledger.add_evidence(c, "osm", *FAR, ref=f"node/9{i}")
        k = ledger.add_clue(c, f"twin {grp}", "SOFT", 4, 4, 4, grp, geometry=(i == 0), evidence=e["id"])
        ledger.add_link(c, k["id"], "h2", "supports")
    ev = ledger.evaluate(c)
    assert ev["verdict"] == "CANDIDATE" and any(u.startswith("E:") for u in ev["unmet"])


def test_empty_case_is_candidate():
    ev = ledger.evaluate(ledger.new_case("x.jpg"))
    assert ev["verdict"] == "CANDIDATE" and ev["top"] is None


def test_all_hypotheses_eliminated_is_candidate():
    c = base_case()
    k = ledger.add_clue(c, "script is Cyrillic", "HARD", 4, 5, 5, "script")
    for h in ("h1", "h2", "h3"):
        ledger.add_link(c, k["id"], h, "contradicts")
    ev = ledger.evaluate(c)
    assert ev["verdict"] == "CANDIDATE" and ev["top"] is None


def test_geometry_clue_requires_evidence():
    c = ledger.new_case("x.jpg")
    with pytest.raises(ledger.LedgerError):
        ledger.add_clue(c, "skyline matches", "HARD", 5, 5, 5, "skyline", geometry=True)


@pytest.mark.parametrize("kwargs", [
    dict(kind="MAYBE"), dict(uniqueness=0), dict(permanence=6), dict(reliability=2.5), dict(group=" "), dict(text=""),
])
def test_bad_clue_input_is_rejected(kwargs):
    c = ledger.new_case("x.jpg")
    args = dict(text="t", kind="SOFT", uniqueness=3, permanence=3, reliability=3, group="g")
    args.update(kwargs)
    with pytest.raises(ledger.LedgerError):
        ledger.add_clue(c, **args)


def test_unknown_ids_and_bad_coordinates_are_rejected():
    c = base_case()
    with pytest.raises(ledger.LedgerError):
        ledger.add_link(c, "c99", "h1", "supports")
    with pytest.raises(ledger.LedgerError):
        ledger.add_link(c, "c1", "h1", "proves")
    with pytest.raises(ledger.LedgerError):
        ledger.add_hypothesis(c, "nowhere", 95.0, 0.0)
    with pytest.raises(ledger.LedgerError):
        ledger.add_clue(c, "t", "SOFT", 3, 3, 3, "g", evidence="e99")


def test_relinking_replaces_the_old_relation():
    c = base_case()
    ledger.add_link(c, "c3", "h1", "contradicts")
    assert [l["relation"] for l in c["links"] if l["clue"] == "c3"] == ["contradicts"]


def test_weight_is_the_product_scaled_to_one():
    c = ledger.new_case("x.jpg")
    assert ledger.weight(ledger.add_clue(c, "t", "SOFT", 5, 5, 5, "g")) == 1.0
    assert ledger.weight(ledger.add_clue(c, "t", "SOFT", 1, 5, 5, "g2")) == pytest.approx(0.2)


def test_there_is_no_way_to_store_a_verdict(tmp_path):
    """A verdict written into the file by hand is ignored: evaluate() recomputes from evidence."""
    c = ledger.new_case("x.jpg")
    c["verdict"] = "IDENTIFIED"
    ledger.save(c, tmp_path / "case.json")
    assert ledger.evaluate(ledger.load(tmp_path / "case.json"))["verdict"] == "CANDIDATE"


def test_save_and_load_round_trip(tmp_path):
    c = base_case()
    ledger.save(c, tmp_path / "a" / "case.json")
    assert ledger.load(tmp_path / "a" / "case.json") == c
