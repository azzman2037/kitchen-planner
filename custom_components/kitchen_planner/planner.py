"""Pure planning logic: Mealie meal plan in, shopping list and forecasts out.

Nothing here touches Home Assistant, so it can be unit-tested directly and
runs in the executor. The heavy lifting is the vendored engine, which is the
same code the coles-weekly-planner CLI runs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .engine.build_list import compute_list
from .engine.pantrylib import pretty_qty
from .engine.staples import forecast

DATA_FILES = {
    "recipes": ("recipes.json", []),
    "orders": ("orders.json", []),
    "pantry": ("pantry-state.json", {}),
    "catalogue": ("pack-catalogue.json", {}),
    "product_map": ("product-map.json", {}),
    "mealie_map": ("mealie-map.json", {}),
    "rotation": ("rotation-history.json", []),
}
REQUIRED = ("recipes", "catalogue", "mealie_map")
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]


class DataMissing(Exception):
    """The data folder is missing files the planner cannot work without."""


def import_file(folder: Path, filename: str, payload_b64gz: str,
                stamp: str) -> dict[str, Any]:
    """Write one planner data file from a gzip+base64 JSON payload.

    Only the known data file names are accepted, the payload must decode to
    valid JSON, and any existing file is kept as <name>.bak-<stamp> first so
    every import can be rolled back.
    """
    import base64
    import gzip

    allowed = {name for name, _ in DATA_FILES.values()}
    if filename not in allowed:
        raise ValueError(f"{filename} is not a planner data file ({sorted(allowed)})")
    try:
        raw = gzip.decompress(base64.b64decode(payload_b64gz, validate=True))
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, OSError) as err:
        raise ValueError(f"payload for {filename} is not gzip+base64 JSON: {err}") from err
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    backup = None
    if target.exists():
        backup = folder / f"{filename}.bak-{stamp}"
        backup.write_bytes(target.read_bytes())
    tmp = folder / f".{filename}.tmp"
    tmp.write_text(json.dumps(parsed, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    size = len(parsed) if isinstance(parsed, (list, dict)) else 1
    return {"file": filename, "entries": size,
            "backup": backup.name if backup else None}


def load_data(folder: Path) -> dict[str, Any]:
    """Read every planner data file. Optional files fall back to empty."""
    missing = [DATA_FILES[k][0] for k in REQUIRED
               if not (folder / DATA_FILES[k][0]).exists()]
    if missing:
        raise DataMissing(", ".join(missing))
    out: dict[str, Any] = {}
    for key, (name, fallback) in DATA_FILES.items():
        path = folder / name
        out[key] = (json.loads(path.read_text(encoding="utf-8"))
                    if path.exists() else fallback)
    return out


def upcoming_week_start(today: date) -> date:
    """The Monday the family is planning for.

    Shopping happens at the weekend for the week ahead, and during the week
    people are already choosing next week's dinners, so this is always the
    next Monday strictly after today.
    """
    return today + timedelta(days=7 - today.weekday())


@dataclass
class WeekPlan:
    """The week as the engine wants it, plus what could not be mapped."""

    week_start: date
    days: list[dict[str, Any]]
    unmapped: list[str] = field(default_factory=list)

    def as_engine_plan(self) -> dict[str, Any]:
        return {"week_start": self.week_start.isoformat(), "days": self.days}


def build_week(mealplans: list[dict[str, Any]], mealie_map: dict[str, str],
               recipes: dict[str, dict], week_start: date) -> WeekPlan:
    """Turn Mealie dinner entries into the engine's day list.

    Mealie recipes are matched to planner recipes through mealie-map.json
    (planner id -> Mealie slug). A Mealie recipe with no planner match is
    reported as unmapped: it is on the plan, but nothing can be bought for it
    until its ingredients are given canonical ids.
    """
    by_slug = {slug: rid for rid, slug in mealie_map.items()
               if not rid.startswith("_")}
    # A note-only entry titled with a planner recipe's name ("Chicken
    # Cacciatore") counts as that recipe, so a dinner can be planned before
    # its recipe is in Mealie.
    by_name = {str(r.get("name", "")).strip().lower(): rid
               for rid, r in recipes.items() if r.get("name")}
    days: list[dict[str, Any]] = []
    unmapped: list[str] = []
    end = week_start + timedelta(days=6)
    for mp in sorted(mealplans, key=lambda m: str(m.get("mealplan_date"))):
        if str(mp.get("entry_type", "")).lower().split(".")[-1] != "dinner":
            continue
        when = mp.get("mealplan_date")
        if isinstance(when, str):
            when = date.fromisoformat(when[:10])
        if not isinstance(when, date) or not week_start <= when <= end:
            continue
        recipe = mp.get("recipe") or {}
        slug = recipe.get("slug")
        rid = by_slug.get(slug) if slug else None
        name = recipe.get("name") or mp.get("title") or "Unnamed"
        if rid and rid not in recipes:
            rid = None
        if not slug and mp.get("title"):
            rid = by_name.get(str(mp["title"]).strip().lower())
        if slug and not rid:
            unmapped.append(name)
        days.append({
            "date": when.isoformat(),
            "day": DAY_NAMES[when.weekday()],
            "recipe_id": rid,
            "name": name,
            "slug": slug,
            # A note-only entry ("Takeaway") or an unmapped recipe buys nothing.
            "flex": rid is None,
        })
    return WeekPlan(week_start, days, unmapped)


def _surplus_rows(rows: list[dict], catalogue: dict) -> list[dict]:
    """Perishable, unfreezable leftovers the week's packs will create."""
    out = []
    for r in rows:
        entry = catalogue.get(r["canonical"]) or {}
        if not isinstance(entry, dict) or r.get("surplus", 0) <= 0:
            continue
        if not entry.get("perishable") or entry.get("freezable"):
            continue
        out.append({
            "canonical": r["canonical"],
            "label": r["label"],
            "surplus": round(r["surplus"], 1),
            "unit": r["unit"],
            "display": pretty_qty(r["surplus"], r["unit"]),
            "waste_pct": r.get("waste_pct", 0),
        })
    return sorted(out, key=lambda s: -s["waste_pct"])


NOT_BUYING_AFTER = date(2090, 1, 1)


def active_pantry(pantry: dict[str, Any], week_start: date) -> dict[str, Any]:
    """Drop on-hand entries whose `until` date is before the planning week.

    Entries without `until` (written by hand before the dashboard existed)
    are kept, so nothing silently disappears; the dashboard lists them so
    they can be cleared.
    """
    out: dict[str, Any] = {}
    for key, entry in pantry.items():
        if key.startswith("_") or not isinstance(entry, dict):
            out[key] = entry
            continue
        until = (entry.get("on_hand") or {}).get("until")
        if until and date.fromisoformat(until) < week_start:
            entry = {k: v for k, v in entry.items() if k != "on_hand"}
            if not entry or set(entry) <= {"note"}:
                continue
        out[key] = entry
    return out


def pack_for(catalogue: dict[str, Any], canonical: str) -> tuple[float, str, str] | None:
    """Smallest catalogue pack for an item: (size, unit, product)."""
    entry = catalogue.get(canonical)
    if not isinstance(entry, dict) or not entry.get("packs"):
        return None
    p = min(entry["packs"], key=lambda x: float(x["size"]))
    return float(p["size"]), p.get("unit", "each"), p["product"]


def set_on_hand(pantry: dict[str, Any], canonical: str, qty: float, unit: str,
                until: date, today: date, source: str = "dashboard") -> dict[str, Any]:
    """Return a copy of pantry-state with one on-hand entry added or replaced.

    Other keys on the item (have_until, notes) are preserved.
    """
    out = dict(pantry)
    entry = dict(out.get(canonical) or {})
    entry["on_hand"] = {"qty": qty, "unit": unit, "until": until.isoformat(),
                        "added": today.isoformat(), "source": source}
    out[canonical] = entry
    return out


def clear_on_hand(pantry: dict[str, Any], canonical: str) -> dict[str, Any]:
    out = dict(pantry)
    entry = {k: v for k, v in (out.get(canonical) or {}).items() if k != "on_hand"}
    if entry and set(entry) - {"note"}:
        out[canonical] = entry
    else:
        out.pop(canonical, None)
    return out


def pantry_view(pantry: dict[str, Any], catalogue: dict[str, Any]) -> dict[str, list]:
    """What the dashboard shows under "In the house" and "Not buying"."""
    have, not_buying = [], []
    for key, entry in sorted(pantry.items()):
        if key.startswith("_") or not isinstance(entry, dict):
            continue
        label = (pack_for(catalogue, key) or (0, "", key.replace("_", " ")))[2]
        oh = entry.get("on_hand")
        if oh:
            have.append({"canonical": key, "label": label,
                         "amount": pretty_qty(float(oh.get("qty") or 0), oh.get("unit", "each")),
                         "until": oh.get("until"), "source": oh.get("source", "manual")})
        hu = entry.get("have_until")
        if hu and date.fromisoformat(hu) >= NOT_BUYING_AFTER:
            not_buying.append({"canonical": key, "label": label})
    return {"have": have, "not_buying": not_buying}


def spend_history(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"date": o["date"], "total": round(float(o["trolley_total"]), 2)}
            for o in sorted(orders, key=lambda o: o.get("date") or "")
            if o.get("date") and o.get("trolley_total")]


def placed_order(orders: list[dict[str, Any]], week_start: date) -> dict[str, Any] | None:
    """The order already placed for this week, if there is one.

    The shop for a week is collected on the Friday to Sunday before it, so an
    order dated in that window means the week is bought: the dashboard shows
    what was ordered instead of a list still to buy.
    """
    lo, hi = week_start - timedelta(days=3), week_start - timedelta(days=1)
    found = [o for o in orders if o.get("date")
             and lo <= date.fromisoformat(o["date"]) <= hi]
    if not found:
        return None
    o = max(found, key=lambda x: x["date"])
    num = re.search(r"\d{6,}", str(o.get("source", "")))
    items = [{"product": i["product"], "qty": i.get("qty", 0), "ordered": i.get("ordered", 0),
              "total": round(float(i.get("total_price") or 0), 2),
              "aisle": i.get("category") or "other", "canonical": i.get("canonical")}
             for i in o["items"]]
    total = o.get("trolley_total") or sum(i["total"] for i in items)
    return {"date": o["date"], "number": num.group(0) if num else None,
            "total": round(float(total), 2), "items": items}


def _placed_leftovers(leftovers: list[dict], rows: list[dict],
                      orders: list[dict[str, Any]], when: str) -> list[dict]:
    """Leftovers re-measured against the packs that were really ordered.

    The engine assumes the catalogue's best pack; the order may hold a bigger
    one (a 300 g sour cream, not 200 g) or none at all.
    """
    order = next(o for o in orders if o.get("date") == when)
    need = {r["canonical"]: r for r in rows}
    out = []
    for lo in leftovers:
        row = need.get(lo["canonical"])
        lines = [i for i in order["items"]
                 if i.get("canonical") == lo["canonical"] and i.get("qty")]
        if not row or not lines:
            continue
        same = [i for i in lines if (i.get("pack_unit") or "").lower() == lo["unit"]
                and i.get("pack_size")]
        if len(same) != len(lines):
            out.append(lo)  # units differ: keep the engine's figure
            continue
        bought = sum(float(i["pack_size"]) * int(i["qty"]) for i in same)
        spare = bought - float(row.get("required") or 0)
        if spare <= 0:
            continue
        out.append({**lo, "surplus": round(spare, 1),
                    "display": pretty_qty(spare, lo["unit"]),
                    "waste_pct": round(100 * spare / bought)})
    return sorted(out, key=lambda s: -s["waste_pct"])


def compute(data: dict[str, Any], plan: WeekPlan, *, serves: int = 4,
            horizon: int = 10) -> dict[str, Any]:
    """Run the engine for one week and shape the result for Home Assistant."""
    recipes = {r["id"]: r for r in data["recipes"]}
    as_of = plan.week_start - timedelta(days=1)  # the Sunday pickup
    pantry = active_pantry(data["pantry"], plan.week_start)
    placed = placed_order(data["orders"], plan.week_start)
    staples = forecast(data["orders"], pantry, as_of, horizon)
    res = compute_list(plan.as_engine_plan(), recipes, data["orders"],
                       pantry, data["catalogue"], staples,
                       serves=serves)

    rows = []
    for aisle, aisle_rows in res["lines_by_aisle"].items():
        for r in aisle_rows:
            rows.append({**r, "aisle": aisle})
    rows.sort(key=lambda r: (r["aisle"], r["label"]))

    order_now = sorted(res["order_now"], key=lambda s: s["due_in_days"])
    to_check = res["to_check"]
    staples_cost = sum(s.get("est_cost") or 0 for s in order_now)
    total = res["est_total"] + staples_cost
    is_floor = bool(res["unpriced"] or res["no_catalogue"])
    leftovers = _surplus_rows(rows, data["catalogue"])
    if placed:
        # The week is bought: nothing is due, and the total is the real one.
        # Only packs that were actually ordered can be left over.
        order_now, to_check, staples_cost = [], [], 0.0
        total, is_floor = placed["total"], False
        leftovers = _placed_leftovers(leftovers, rows, data["orders"], placed["date"])

    return {
        "placed": placed,
        "week_start": plan.week_start.isoformat(),
        "days": plan.days,
        "unmapped": plan.unmapped,
        "rows": rows,
        "staples": order_now,
        "staples_to_check": to_check,
        "absorbed": res["absorbed"],
        "estimated_total": round(total, 2),
        "recipe_cost": round(res["est_total"], 2),
        "staples_cost": round(staples_cost, 2),
        "is_floor": is_floor,
        "unpriced": sorted(set(res["unpriced"])),
        "no_pack_size": sorted(set(res["no_catalogue"])),
        "on_hand_notes": res["on_hand_notes"],
        "problems": res["problems"],
        "leftovers": leftovers,
        "aisles": _by_aisle(rows),
        "pantry": pantry_view(pantry, data["catalogue"]),
        "spend": spend_history(data["orders"]),
    }


def _by_aisle(rows: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["aisle"], []).append(r["buy"])
    return out


def order_items(result: dict[str, Any]) -> list[dict[str, str]]:
    """The generated lines for the Coles order to-do list.

    Each line keeps a stable uid built from its canonical id, so ticking an
    item survives the list being rebuilt every few minutes. Once the week's
    order is placed, the list is that order, every line marked as ordered.
    """
    placed = result.get("placed")
    if placed:
        return [{"uid": "kp:order:%d:%s" % (n, i.get("canonical") or "item"),
                 "summary": "%s x%d" % (i["product"], i["ordered"] or i["qty"]),
                 "description": ("Ordered %s, $%.2f" % (placed["date"], i["total"])
                                 if i["qty"] else "Ordered %s, not supplied" % placed["date"]),
                 "aisle": i["aisle"], "ordered": True}
                for n, i in enumerate(placed["items"])]
    items = []
    for r in result["rows"]:
        qty = sum((r.get("packs") or {}).values()) or None
        summary = r["buy"]
        desc = "For: " + "; ".join(r.get("used_in") or [])
        if r.get("surplus", 0) > 0:
            desc += " | Leftover: %s" % pretty_qty(r["surplus"], r["unit"])
        if qty is None:
            desc += " | No pack size on record - choose in the trolley"
        items.append({"uid": "kp:" + r["canonical"], "summary": summary,
                      "description": desc, "aisle": r["aisle"]})
    for s in result["staples"]:
        qty = s.get("typical_qty") or 1
        items.append({
            "uid": "kp:staple:" + s["canonical"],
            "summary": "%s x%s" % (s["label"], qty),
            "description": "Staple, about every %sd, last bought %s, ~$%.2f"
                           % (s.get("median_interval_days"),
                              s.get("last_purchased"), s.get("est_cost") or 0),
            "aisle": s.get("category") or "staples",
        })
    return items
