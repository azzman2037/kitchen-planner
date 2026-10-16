#!/usr/bin/env python3
"""Turn the week's dinner plan into one consolidated Coles order.

This is where "100mL of cream in one recipe and 300mL in another" becomes a
single 600mL tub. The steps are:

  1. scale every recipe to the household serving size
  2. convert all quantities to ml / g / each
  3. sum by canonical ingredient across the whole week
  4. subtract anything already on hand (data/pantry-state.json)
  5. choose the cheapest pack combination that covers the total, capping waste
     on perishables that cannot be frozen
  6. fold in the staples forecast from staples.py, skipping anything the meal
     plan already covers
  7. group by Coles aisle and write the order

    python build_list.py --data ../data --week-start 2026-08-17 --serves 4
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

try:  # vendored inside the Home Assistant integration package
    from .pantrylib import (
        Pack, each_to_grams, optimise_packs, pretty_qty, to_canonical, UnitError,
    )
except ImportError:  # run as a script from scripts/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pantrylib import (  # noqa: E402
        Pack, each_to_grams, optimise_packs, pretty_qty, to_canonical, UnitError,
    )

AISLE_ORDER = [
    "fruit & vegetables", "meat & seafood", "deli", "dairy, eggs & fridge",
    "bakery", "pantry", "frozen", "drinks", "household", "health & beauty",
    "baby", "pet", "other",
]


def load(path, fallback):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def observed_packs(orders):
    """Fall back to whatever you have actually bought before.

    If pack-catalogue.json does not list an ingredient, the sizes and prices
    seen in your own order history are used instead. Most recent price wins,
    because grocery prices move.
    """
    seen = defaultdict(dict)
    for order in sorted(orders, key=lambda o: o.get("date") or ""):
        for item in order["items"]:
            size, unit = item.get("pack_size"), item.get("pack_unit", "each")
            if not size:
                size, unit = 1.0, "each"
            seen[item["canonical"]][(float(size), unit)] = {
                "product": item["product"],
                "price": item.get("unit_price") or None,
            }
    out = {}
    for canonical, packs in seen.items():
        # Mixed-unit history (a 700 mL and a 700 g passata) is kept whole and
        # in a fixed order. Every consumer already filters packs by unit and
        # backfill_prices keeps its rates per unit, so nothing combines across
        # units. Dropping the "less common" unit used to break ties by set
        # order, which made the estimate change from one run to the next.
        packs = dict(sorted(packs.items()))
        out[canonical] = [
            Pack(product=meta["product"], size=size, unit=unit,
                 price=meta["price"], perishable=False, freezable=True,
                 note="from order history")
            for (size, unit), meta in packs.items()
        ]
    return out


def catalogue_packs(raw):
    out = {}
    for canonical, entry in raw.items():
        if canonical.startswith("_") or not isinstance(entry, dict):
            continue  # documentation key
        out[canonical] = [
            Pack(product=p["product"], size=float(p["size"]),
                 unit=p.get("unit", "each"), price=p.get("price"),
                 perishable=bool(entry.get("perishable", False)),
                 freezable=bool(entry.get("freezable", False)),
                 note=p.get("note", ""))
            for p in entry["packs"]
        ]
    return out


def backfill_prices(catalogue, history):
    """Give catalogue packs a price from your own order history.

    The catalogue ships with null prices on purpose - sizes are stable, prices
    are not. An exact size match wins; otherwise the most recent observed
    dollars-per-unit is scaled to the pack size, which is close enough for a
    weekly estimate and is flagged as approximate.
    """
    for canonical, packs in catalogue.items():
        observed = history.get(canonical) or []
        if not observed:
            continue
        exact = {(p.size, p.unit): p.price for p in observed
                 if p.price is not None}
        # Rates MUST be kept per unit. Pooling them lets a "1 each @ $14.00"
        # history line become a rate of 14 dollars per unit, which applied to
        # a 1000 g catalogue pack prices the mince at $14,000.
        rates = {}
        for p in observed:
            if p.price is not None and p.size > 0:
                rates.setdefault(p.unit, []).append(p.price / p.size)
        median_rate = {u: sorted(v)[len(v) // 2] for u, v in rates.items()}
        for pack in packs:
            if pack.price is not None:
                continue
            if (pack.size, pack.unit) in exact:
                pack.price = exact[(pack.size, pack.unit)]
                pack.note = "price from order history"
            elif pack.unit in median_rate:
                pack.price = round(median_rate[pack.unit] * pack.size, 2)
                pack.note = "price estimated from order history"
    return catalogue


def aggregate(plan, recipes, serves):
    """Sum every recipe ingredient across the week, in canonical units."""
    totals, problems = {}, []

    for day in plan["days"]:
        if day.get("flex") or not day.get("recipe_id"):
            continue  # deliberately unplanned night, nothing to buy for it
        recipe = recipes.get(day["recipe_id"])
        if not recipe:
            problems.append("%s missing from recipes.json" % day["recipe_id"])
            continue
        # A day can override the global --serves (e.g. a full batch for leftovers).
        day_serves = day.get("serves_override") or serves
        scale = day_serves / float(recipe.get("serves", day_serves) or day_serves)
        for ing in recipe.get("ingredients", []):
            name = ing["item"]
            key = ing.get("canonical") or name
            qty = float(ing.get("qty") or 0) * scale
            if qty <= 0:
                continue
            try:
                qty, unit = to_canonical(qty, ing.get("unit", "each"))
            except UnitError as exc:
                problems.append("%s: %s" % (recipe["name"], exc))
                continue

            rec = totals.setdefault(key, {
                "canonical": key, "label": name, "qty": 0.0, "unit": unit,
                "aisle": ing.get("aisle", "other"),
                "pantry": bool(ing.get("pantry")), "used_in": [],
            })
            # A canonical ingredient is only "assume it's on hand" if every
            # recipe touching it this week agrees - one recipe genuinely
            # needing a fresh bunch must not be hidden by another recipe's
            # pinch-from-the-pantry flag on the same canonical id.
            rec["pantry"] = rec["pantry"] and bool(ing.get("pantry"))

            # Reconcile 'each' against 'g' where the item has a known weight,
            # so "2 onions" and "500 g onion" can be added together.
            if rec["unit"] != unit:
                converted = None
                if unit == "each" and rec["unit"] == "g":
                    converted = each_to_grams(name, qty)
                elif unit == "g" and rec["unit"] == "each":
                    grams = each_to_grams(name, rec["qty"])
                    if grams is not None:
                        rec["qty"], rec["unit"] = grams, "g"
                        converted = qty
                if converted is None:
                    problems.append(
                        "%s: cannot add %s to %s - use one unit in both "
                        "recipes, or add a weight to EACH_TO_G"
                        % (name, unit, rec["unit"]))
                    continue
                qty, unit = converted, rec["unit"]

            rec["qty"] += qty
            shown = math.ceil(qty - 1e-9) if unit == "each" else round(qty, 1)
            rec["used_in"].append("%s (%s)"
                                  % (recipe["name"], pretty_qty(shown, unit)))

    return totals, problems


def subtract_on_hand(totals, pantry):
    notes = []
    for key, rec in totals.items():
        entry = pantry.get(key)
        have = entry.get("on_hand") if isinstance(entry, dict) else None
        if not have:
            continue
        try:
            hq, hu = to_canonical(float(have["qty"]), have.get("unit", "each"))
        except (UnitError, KeyError, TypeError, ValueError):
            continue
        if hu != rec["unit"] or hq <= 0:
            continue
        used = min(hq, rec["qty"])
        rec["qty"] -= used
        notes.append("%s: %s already on hand"
                     % (rec["label"], pretty_qty(used, hu)))
    return notes


def write_trolley_plan(data, week_start, rows, staples, pmap):
    """Emit an ordered, mechanical shopping plan for the browser step.

    The point is to remove judgement from the trolley. Every line carries the
    exact Coles product name and the search string that finds it, so the
    browser step is "search, match the name, set quantity" with no reading of
    results. Lines where no confirmed product exists are separated out and put
    last, because those are the only ones that need a human eye.
    """
    known, unknown = [], []
    for r in rows:
        entry = pmap.get(r["canonical"]) or {}
        packs = r.get("packs") or []
        line = {
            "canonical": r["canonical"],
            "label": r["label"],
            "need": "%g %s" % (round(r["required"], 1), r["unit"]),
            "product": entry.get("product", ""),
            "search": entry.get("search") or r["label"],
            "qty": sum(packs.values()) if packs else 1,
            "packs": packs,
            "fallback": entry.get("fallback", ""),
            "avoid": entry.get("avoid", []),
            "note": entry.get("note", ""),
            "for": r["used_in"],
        }
        (known if entry.get("confirmed") and entry.get("product")
         else unknown).append(line)

    for s in staples:
        entry = pmap.get(s["canonical"]) or {}
        line = {
            "canonical": s["canonical"],
            "label": s["label"],
            "need": "staple, ~%dd cycle" % s.get("median_interval_days", 0),
            "product": entry.get("product") or s["label"],
            "search": entry.get("search") or s["label"],
            "qty": s.get("typical_qty", 1),
            "packs": {},
            "fallback": entry.get("fallback", ""),
            "avoid": entry.get("avoid", []),
            "note": entry.get("note", ""),
            "for": ["staple replenishment"],
        }
        (known if entry.get("confirmed") else unknown).append(line)

    plan = {
        "week_start": week_start,
        "generated": date.today().isoformat(),
        "total_lines": len(known) + len(unknown),
        "procedure": "references/trolley.md",
        "known": known,
        "needs_choice": unknown,
    }
    (data / ("trolley-plan-%s.json" % week_start)).write_text(
        json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    return len(known), len(unknown)


def compute_list(plan, recipes, orders, pantry, catalogue_raw, staples,
                 serves=4, max_waste=0.35, include_pantry=False):
    """Pure core of build_list: no file I/O, so the Home Assistant
    integration and the CLI share one implementation.

    est_total covers recipe lines only; staples are added by the caller.
    """
    catalogue_packs_raw = catalogue_raw
    history_packs = observed_packs(orders)
    catalogue = backfill_prices(
        catalogue_packs(catalogue_packs_raw), history_packs)
    packs_by_item = dict(history_packs)
    packs_by_item.update(catalogue)

    totals, problems = aggregate(plan, recipes, serves)
    on_hand_notes = subtract_on_hand(totals, pantry)

    lines_by_aisle = defaultdict(list)
    unpriced, no_catalogue = [], []
    est_total = 0.0

    for key, rec in sorted(totals.items()):
        if rec["qty"] <= 0:
            continue
        if rec["pantry"] and not include_pantry:
            continue
        available = packs_by_item.get(key, [])
        need, need_unit = rec["qty"], rec["unit"]
        packs = [p for p in available if p.unit == need_unit]

        # Coles sells onions by weight but recipes count them. If nothing
        # matches the recipe's unit, try the other side of that fence.
        if not packs:
            other = {p.unit for p in available}
            if need_unit == "each" and "g" in other:
                grams = each_to_grams(rec["label"], need)
                if grams is not None:
                    need, need_unit = grams, "g"
                    packs = [p for p in available if p.unit == "g"]
            elif need_unit == "g" and "each" in other:
                per = each_to_grams(rec["label"], 1)
                if per:
                    need, need_unit = need / per, "each"
                    packs = [p for p in available if p.unit == "each"]

        # Countable things round up. Scaling a serves-10 recipe down to 4
        # legitimately produces 0.8 of an onion; the shopping list must not.
        if need_unit == "each":
            need = math.ceil(need - 1e-9)

        row = {"label": rec["label"], "canonical": key,
               "required": need, "unit": need_unit,
               "used_in": rec["used_in"]}
        sol = optimise_packs(need, packs,
                             max_waste=max_waste) if packs else None
        if sol:
            row["packs"] = dict(sol.counts)
            row["buy"] = "%s: %s" % (rec["label"], sol.describe())
            row["surplus"] = sol.surplus
            row["waste_pct"] = round(sol.waste_fraction * 100)
            row["cost"] = sol.cost
            if sol.cost is not None:
                est_total += sol.cost
            else:
                unpriced.append(rec["label"])
        else:
            row["buy"] = "%s - %s (no pack size on record)" % (
                rec["label"], pretty_qty(need, need_unit))
            row["surplus"], row["waste_pct"], row["cost"] = 0.0, 0, None
            no_catalogue.append(rec["label"])
        lines_by_aisle[rec["aisle"]].append(row)

    # A staple that is also an ingredient this week is already covered by the
    # pack optimiser above; listing it again would double-order it.
    planned = {r["canonical"] for rows in lines_by_aisle.values() for r in rows}
    order_now = [s for s in staples
                 if s.get("status") == "order" and s["canonical"] not in planned]
    to_check = [s for s in staples
                if s.get("status") == "check" and s["canonical"] not in planned]
    absorbed = [s["label"] for s in staples
                if s.get("status") in ("order", "check")
                and s["canonical"] in planned]

    return {
        "lines_by_aisle": lines_by_aisle, "order_now": order_now,
        "to_check": to_check, "absorbed": absorbed, "est_total": est_total,
        "unpriced": unpriced, "no_catalogue": no_catalogue,
        "problems": problems, "on_hand_notes": on_hand_notes,
        "packs_by_item": packs_by_item,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--week-start", required=True)
    ap.add_argument("--verbose", action="store_true",
                    help="print the whole list; off by default because dumping "
                         "it into the conversation is what makes a session "
                         "expensive - the file is right there")
    ap.add_argument("--serves", type=int, default=4)
    ap.add_argument("--max-waste", type=float, default=0.35,
                    help="max surplus fraction tolerated on unfreezable "
                         "perishables")
    ap.add_argument("--include-pantry", action="store_true",
                    help="also list salt/oil/spice-type items")
    args = ap.parse_args()

    data = Path(args.data)
    plan_path = data / ("plan-%s.json" % args.week_start)
    if not plan_path.exists():
        raise SystemExit("No plan at %s - run plan_week.py first." % plan_path)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    recipes = {r["id"]: r for r in load(data / "recipes.json", [])}
    orders = load(data / "orders.json", [])
    pantry = load(data / "pantry-state.json", {})
    staples = load(data / "staples-forecast.json", [])

    r = compute_list(plan, recipes, orders, pantry,
                     load(data / "pack-catalogue.json", {}), staples,
                     serves=args.serves, max_waste=args.max_waste,
                     include_pantry=args.include_pantry)
    lines_by_aisle, order_now, to_check = (r["lines_by_aisle"], r["order_now"],
                                           r["to_check"])
    absorbed, est_total, unpriced = r["absorbed"], r["est_total"], r["unpriced"]
    no_catalogue, problems = r["no_catalogue"], r["problems"]
    on_hand_notes = r["on_hand_notes"]

    out_lines = [
        "# Coles order - delivery Sunday, cooking week of %s" % args.week_start,
        "", "## Dinners", "", "| Night | Meal |", "|---|---|",
    ]
    for day in plan["days"]:
        label = ("_%s_" % day["name"]) if day.get("flex") else day["name"]
        note = ""
        if day.get("slow_cooker"):
            note = " (slow cooker - start in the %s)" % day.get("start", "morning")
        out_lines.append("| %s | %s%s |" % (day["day"], label, note))
    out_lines += ["", "## Shopping list", ""]

    def aisle_key(a):
        return AISLE_ORDER.index(a) if a in AISLE_ORDER else len(AISLE_ORDER)

    for aisle in sorted(lines_by_aisle, key=aisle_key):
        rows = sorted(lines_by_aisle[aisle], key=lambda r: r["label"])
        out_lines += ["### %s" % aisle.title(), "",
                      "| Buy | Needed | Surplus | For |", "|---|---|---|---|"]
        for r in rows:
            surplus = ("%s (%d%%)" % (pretty_qty(r["surplus"], r["unit"]),
                                      r["waste_pct"])
                       if r["surplus"] > 0 else "-")
            out_lines.append("| %s | %s | %s | %s |"
                             % (r["buy"],
                                pretty_qty(r["required"], r["unit"]),
                                surplus, "; ".join(r["used_in"])))
        out_lines.append("")

    if order_now:
        out_lines += ["### Staples due (predicted from order history)", "",
                      "| Item | Canonical | Qty | Cycle | Last bought | ~$ |",
                      "|---|---|---|---|---|---|"]
        for s in sorted(order_now, key=lambda s: s["due_in_days"]):
            out_lines.append("| %s | `%s` | %s | %sd | %s | %.2f |"
                             % (s["label"], s["canonical"], s["typical_qty"],
                                s["median_interval_days"],
                                s["last_purchased"], s["est_cost"]))
            est_total += s["est_cost"]
        out_lines.append("")

    if to_check:
        out_lines += ["### Confirm before adding", "",
                      "_Irregular purchase pattern, so the model is not "
                      "confident these are due._", ""]
        for s in to_check:
            out_lines.append(
                "- %s (`%s`) - last bought %s, cycle %sd +/-%s"
                % (s["label"], s["canonical"], s["last_purchased"],
                   s["median_interval_days"], s["interval_spread_days"]))
        out_lines.append("")

    gaps = bool(unpriced or no_catalogue)
    out_lines += ["**Estimated total: $%.2f**%s" % (
        est_total, " - a floor, not a total; see Gaps." if gaps else ""), ""]

    if on_hand_notes:
        out_lines += ["## Deducted from pantry", ""] + \
            ["- %s" % n for n in on_hand_notes] + [""]
    if absorbed:
        out_lines += ["## Staples already covered by a recipe", "",
                      "_Due this cycle, but the meal plan needs them anyway, so "
                      "they are quantified above rather than counted twice._",
                      ""] + ["- %s" % n for n in sorted(set(absorbed))] + [""]
    if gaps or problems:
        out_lines += ["## Gaps", ""]
        for label in sorted(set(no_catalogue)):
            out_lines.append("- No pack size known for **%s** - shown as the "
                             "raw requirement." % label)
        for label in sorted(set(unpriced)):
            out_lines.append("- No price on record for **%s** - excluded from "
                             "the estimate." % label)
        for p in problems:
            out_lines.append("- %s" % p)
        out_lines.append("")

    text = "\n".join(out_lines)
    (data / ("shopping-list-%s.md" % args.week_start)).write_text(
        text, encoding="utf-8")

    all_rows = [r for rows in lines_by_aisle.values() for r in rows]
    n_known, n_choice = write_trolley_plan(
        data, args.week_start, all_rows, order_now,
        {k: v for k, v in load(data / "product-map.json", {}).items()
         if not k.startswith("_")})
    (data / ("shopping-list-%s.json" % args.week_start)).write_text(
        json.dumps({"week_start": args.week_start,
                    "estimated_total": round(est_total, 2),
                    "aisles": lines_by_aisle, "staples": order_now,
                    "confirm": to_check, "absorbed": absorbed,
                    "problems": problems}, indent=2, default=str),
        encoding="utf-8")

    if args.verbose:
        print(text)
    else:
        print("Shopping list: %d ingredient lines + %d staples, est $%.2f%s"
              % (len(all_rows), len(order_now), est_total,
                 " (floor - see Gaps)" if gaps else ""))
        print("Trolley plan: %d lines with a known product, %d needing a choice"
              % (n_known, n_choice))
        if to_check:
            print("Confirm before adding: %s"
                  % ", ".join(s["label"] for s in to_check[:5]))
        if problems:
            print("Problems: %s" % "; ".join(problems[:3]))
        print("Files: shopping-list-%s.md, trolley-plan-%s.json"
              % (args.week_start, args.week_start))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
