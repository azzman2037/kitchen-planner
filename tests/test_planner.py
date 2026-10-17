"""Pure planner tests: no Home Assistant needed."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from custom_components.kitchen_planner.planner import (
    DataMissing,
    build_week,
    compute,
    load_data,
    order_items,
    upcoming_week_start,
)

DATA = Path(__file__).parent / "fixtures" / "data"
MON = date(2026, 10, 19)


def _mp(day: int, slug: str | None, name: str, entry_type: str = "dinner") -> dict:
    return {"entry_type": entry_type, "mealplan_date": date(2026, 10, day),
            "title": name, "recipe": {"slug": slug, "name": name} if slug else None}


@pytest.mark.parametrize(("today", "expected"), [
    (date(2026, 10, 17), MON),               # Saturday shop
    (date(2026, 10, 18), MON),               # Sunday pickup
    (date(2026, 10, 19), date(2026, 10, 26)),  # Monday: already planning next week
    (date(2026, 10, 22), date(2026, 10, 26)),
])
def test_upcoming_week_start(today: date, expected: date) -> None:
    assert upcoming_week_start(today) == expected


def test_load_data_reports_missing(tmp_path: Path) -> None:
    with pytest.raises(DataMissing, match="recipes.json"):
        load_data(tmp_path)


def test_build_week_maps_and_flags() -> None:
    data = load_data(DATA)
    recipes = {r["id"]: r for r in data["recipes"]}
    plans = [
        _mp(19, "butter-chicken", "Butter Chicken"),
        _mp(20, "creamy-tuscan-garlic-chicken", "Tuscan Chicken"),
        _mp(21, "chicken-cacciatore", "Chicken Cacciatore"),  # not in planner yet
        _mp(22, None, "Takeaway"),                           # note-only entry
        _mp(23, "butter-chicken", "Butter Chicken", "lunch"),  # not dinner
        _mp(27, "butter-chicken", "Butter Chicken"),          # next week
    ]
    week = build_week(plans, data["mealie_map"], recipes, MON)
    assert [d["day"] for d in week.days] == ["Monday", "Tuesday", "Wednesday", "Thursday"]
    assert [d["recipe_id"] for d in week.days] == ["butter-chicken", "tuscan-chicken", None, None]
    assert week.unmapped == ["Chicken Cacciatore"]


def test_compute_merges_packs_and_finds_leftovers() -> None:
    data = load_data(DATA)
    recipes = {r["id"]: r for r in data["recipes"]}
    week = build_week([_mp(19, "butter-chicken", "Butter Chicken"),
                       _mp(20, "creamy-tuscan-garlic-chicken", "Tuscan Chicken")],
                      data["mealie_map"], recipes, MON)
    res = compute(data, week, serves=4)
    rows = {r["canonical"]: r for r in res["rows"]}

    # 150 + 200 mL cream merged into one purchase, not two separate tubs.
    assert rows["cream_thickened"]["required"] == pytest.approx(350)
    assert sum(rows["cream_thickened"]["packs"].values()) >= 1
    # 800 + 550 g chicken covered by packs adding to at least 1350 g.
    assert rows["chicken_thigh"]["required"] == pytest.approx(1350)
    assert "salt" not in rows  # pantry item

    # Milk is a weekly staple last bought 11 Oct, so it is due for the 18 Oct pickup.
    assert [s["canonical"] for s in res["staples"]] == ["milk"]
    assert res["estimated_total"] == pytest.approx(res["recipe_cost"] + 4.65)
    assert res["is_floor"] is False

    # Cream and spinach are perishable and unfreezable, so their surplus is a
    # leftover worth planning around; frozen-ok chicken surplus is not.
    left = {lo["canonical"] for lo in res["leftovers"]}
    assert "cream_thickened" in left
    assert "spinach" in left
    assert "chicken_thigh" not in left

    items = order_items(res)
    uids = [i["uid"] for i in items]
    assert "kp:cream_thickened" in uids and "kp:staple:milk" in uids
    assert len(uids) == len(set(uids))


def test_mixed_unit_history_is_deterministic_and_priced() -> None:
    """A tie between 700 mL and 700 g passata purchases once made the estimate
    depend on Python's hash seed. Both units must survive, in a fixed order,
    so the catalogue's 700 g jar always picks up the 700 g price."""
    from custom_components.kitchen_planner.engine.build_list import (
        backfill_prices,
        catalogue_packs,
        observed_packs,
    )

    orders = [
        {"date": "2026-03-07", "items": [{"canonical": "passata", "product": "Passata 700mL",
                                          "pack_size": 700, "pack_unit": "ml", "unit_price": 3.5}]},
        {"date": "2026-08-14", "items": [{"canonical": "passata", "product": "Passata 700g",
                                          "pack_size": 700, "pack_unit": "g", "unit_price": 2.0}]},
    ]
    hist = observed_packs(orders)
    assert [(p.size, p.unit) for p in hist["passata"]] == [(700.0, "g"), (700.0, "ml")]
    cat = backfill_prices(catalogue_packs({"passata": {"packs": [
        {"product": "Tomato Passata 700g", "size": 700, "unit": "g", "price": None}]}}), hist)
    assert cat["passata"][0].price == 2.0


def test_active_pantry_drops_expired_and_keeps_legacy() -> None:
    from custom_components.kitchen_planner.planner import active_pantry

    pantry = {
        "_readme": ["x"],
        "cream_thickened": {"on_hand": {"qty": 300, "unit": "ml", "until": "2026-10-18"}},
        "spinach": {"on_hand": {"qty": 120, "unit": "g", "until": "2026-10-25"}},
        "beef_rump": {"on_hand": {"qty": 450, "unit": "g"}, "note": "legacy, no until"},
        "orange": {"have_until": "2099-01-01",
                   "on_hand": {"qty": 1, "unit": "each", "until": "2026-01-01"}},
    }
    out = active_pantry(pantry, MON)
    assert "cream_thickened" not in out          # expired before the week
    assert out["spinach"]["on_hand"]["qty"] == 120
    assert out["beef_rump"]["on_hand"]["qty"] == 450   # legacy kept, shown on dashboard
    assert out["orange"] == {"have_until": "2099-01-01"}  # stop-buying flag survives


def test_set_and_clear_on_hand_preserve_other_keys() -> None:
    from custom_components.kitchen_planner.planner import clear_on_hand, set_on_hand

    p = {"orange": {"have_until": "2099-01-01", "note": "stopped"}}
    p2 = set_on_hand(p, "orange", 2, "each", date(2026, 10, 25), date(2026, 10, 17))
    assert p2["orange"]["have_until"] == "2099-01-01"
    assert p2["orange"]["on_hand"]["until"] == "2026-10-25"
    assert "on_hand" not in p  # input untouched
    p3 = clear_on_hand(p2, "orange")
    assert p3["orange"] == {"have_until": "2099-01-01", "note": "stopped"}
    assert clear_on_hand(set_on_hand({}, "x", 1, "each", MON, MON), "x") == {}


def test_note_titled_with_a_recipe_name_counts_as_that_recipe() -> None:
    data = load_data(DATA)
    recipes = {r["id"]: r for r in data["recipes"]}
    week = build_week([_mp(19, None, "butter chicken "), _mp(20, None, "Takeaway")],
                      data["mealie_map"], recipes, MON)
    assert [d["recipe_id"] for d in week.days] == ["butter-chicken", None]
    assert week.unmapped == []


def test_placed_order_replaces_the_list_to_buy() -> None:
    import copy

    from custom_components.kitchen_planner.planner import placed_order

    data = copy.deepcopy(load_data(DATA))
    recipes = {r["id"]: r for r in data["recipes"]}
    week = build_week([_mp(19, "butter-chicken", "Butter Chicken")],
                      data["mealie_map"], recipes, MON)
    assert placed_order(data["orders"], MON) is None

    data["orders"].append({
        "source": "coles-order-email-282559396 (placed order, pickup 2026-10-17)",
        "date": "2026-10-17", "trolley_total": 266.58, "items": [
            {"product": "Coles Thickened Cream", "category": "dairy, eggs & fridge",
             "ordered": 1, "qty": 1, "total_price": 3.2, "canonical": "cream_thickened",
             "pack_size": 600, "pack_unit": "ml"},
            {"product": "Coles Herb Punnets Oregano", "category": "fruit & vegetables",
             "ordered": 1, "qty": 0, "total_price": 0.0, "canonical": "herb_oregano"}]})
    # An order collected the weekend before a later week is not this week's.
    assert placed_order(data["orders"], date(2026, 10, 26)) is None

    res = compute(data, week, serves=4)
    assert res["placed"]["number"] == "282559396"
    assert res["estimated_total"] == 266.58 and res["is_floor"] is False
    assert res["staples"] == [] and res["staples_to_check"] == []
    assert res["rows"]  # the recipe maths still runs, for the leftovers
    # Butter chicken needs 150 ml; the ordered 600 mL tub leaves 450 ml, and
    # spinach was not ordered, so it cannot be left over.
    left = {lo["canonical"]: lo for lo in res["leftovers"]}
    assert left["cream_thickened"]["display"] == "450 ml"
    assert "spinach" not in left

    items = order_items(res)
    assert [i["uid"] for i in items] == ["kp:order:0:cream_thickened", "kp:order:1:herb_oregano"]
    assert all(i["ordered"] for i in items)
    assert items[0]["summary"] == "Coles Thickened Cream x1"
    assert items[1]["description"].endswith("not supplied")


@pytest.mark.parametrize(("today", "offset", "expected"), [
    (date(2026, 10, 17), 0, date(2026, 10, 12)),   # Saturday: this week began Monday 12th
    (date(2026, 10, 17), 1, date(2026, 10, 19)),   # next week = the weekend shop's week
    (date(2026, 10, 19), 0, date(2026, 10, 19)),   # Monday is its own week
    (date(2026, 10, 25), 2, date(2026, 11, 2)),
])
def test_week_start_for(today: date, offset: int, expected: date) -> None:
    from custom_components.kitchen_planner.planner import week_start_for
    assert week_start_for(today, offset) == expected
    assert week_start_for(today, 1) == upcoming_week_start(today)
