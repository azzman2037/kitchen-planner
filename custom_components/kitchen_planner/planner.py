"""Pure planning logic: Mealie meal plan in, shopping list and forecasts out.

Nothing here touches Home Assistant, so it can be unit-tested directly and
runs in the executor. The heavy lifting is the vendored engine, which is the
same code the coles-weekly-planner CLI runs.
"""
from __future__ import annotations

import json
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


def compute(data: dict[str, Any], plan: WeekPlan, *, serves: int = 4,
            horizon: int = 10) -> dict[str, Any]:
    """Run the engine for one week and shape the result for Home Assistant."""
    recipes = {r["id"]: r for r in data["recipes"]}
    as_of = plan.week_start - timedelta(days=1)  # the Sunday pickup
    staples = forecast(data["orders"], data["pantry"], as_of, horizon)
    res = compute_list(plan.as_engine_plan(), recipes, data["orders"],
                       data["pantry"], data["catalogue"], staples,
                       serves=serves)

    rows = []
    for aisle, aisle_rows in res["lines_by_aisle"].items():
        for r in aisle_rows:
            rows.append({**r, "aisle": aisle})
    rows.sort(key=lambda r: (r["aisle"], r["label"]))

    order_now = sorted(res["order_now"], key=lambda s: s["due_in_days"])
    staples_cost = sum(s.get("est_cost") or 0 for s in order_now)
    total = res["est_total"] + staples_cost
    is_floor = bool(res["unpriced"] or res["no_catalogue"])

    return {
        "week_start": plan.week_start.isoformat(),
        "days": plan.days,
        "unmapped": plan.unmapped,
        "rows": rows,
        "staples": order_now,
        "staples_to_check": res["to_check"],
        "absorbed": res["absorbed"],
        "estimated_total": round(total, 2),
        "recipe_cost": round(res["est_total"], 2),
        "staples_cost": round(staples_cost, 2),
        "is_floor": is_floor,
        "unpriced": sorted(set(res["unpriced"])),
        "no_pack_size": sorted(set(res["no_catalogue"])),
        "on_hand_notes": res["on_hand_notes"],
        "problems": res["problems"],
        "leftovers": _surplus_rows(rows, data["catalogue"]),
    }


def order_items(result: dict[str, Any]) -> list[dict[str, str]]:
    """The generated lines for the Coles order to-do list.

    Each line keeps a stable uid built from its canonical id, so ticking an
    item survives the list being rebuilt every few minutes.
    """
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
