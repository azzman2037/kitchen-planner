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
