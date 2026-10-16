#!/usr/bin/env python3
"""Predict which non-recipe staples are due before the next delivery.

This is the "toilet paper every 3-4 weeks" problem. It is deliberately NOT
the same algorithm as a naive interval forecast:

  * intervals use the MEDIAN, not the mean, so one panic-buy of 24 rolls
    does not stretch the predicted cycle for the next six months
  * dispersion (median absolute deviation) becomes a confidence band, and
    low-confidence items are surfaced as "check" rather than auto-ordered
  * the model orders on a DUE WINDOW that ends at the next delivery, so an
    item due on Wednesday is bought on Sunday rather than run out midweek
  * a manual pantry override lets you say "we still have 2 packs" and push
    the due date out without corrupting the purchase history

Usage:
    python staples.py --data ../data --as-of 2026-08-16 --horizon 10
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:  # vendored inside the Home Assistant integration package
    from .pantrylib import pretty_qty  # noqa: F401
except ImportError:  # run as a script from scripts/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pantrylib import pretty_qty  # noqa: E402,F401

# An item needs this many purchases before its interval means anything.
MIN_OBSERVATIONS = 3

# Cycles longer than this are treated as one-offs, not staples.
MAX_INTERVAL_DAYS = 120

# An item this many times overdue has not been "forgotten" - it has been
# dropped from the shop. Buying tortillas because a 2023 pattern says they are
# 140 days overdue is worse than not buying them.
LAPSE_FACTOR = 3
LAPSE_MIN_DAYS = 30


def d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def load_overrides(path: Path) -> dict[str, dict]:
    """data/pantry-state.json: {"toilet_paper": {"have_until": "2026-09-01"}}

    Also accepts {"packs_on_hand": 2} which is converted using the item's own
    observed cycle length.
    """
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_history(orders: list[dict]) -> dict[str, dict]:
    hist: dict[str, dict] = {}
    for order in orders:
        if not order.get("date"):
            continue
        when = d(order["date"])
        for item in order["items"]:
            key = item["canonical"]
            rec = hist.setdefault(key, {
                "canonical": key,
                "labels": {},
                "category": item.get("category", ""),
                "is_staple": bool(item.get("is_staple")),
                "events": [],
            })
            rec["labels"][item["product"]] = rec["labels"].get(item["product"], 0) + 1
            rec["is_staple"] = rec["is_staple"] or bool(item.get("is_staple"))
            rec["events"].append({
                "date": when,
                "qty": item.get("qty") or 1,
                "unit_price": item.get("unit_price") or 0.0,
                "pack_size": item.get("pack_size"),
                "pack_unit": item.get("pack_unit", "each"),
            })
    for rec in hist.values():
        rec["events"].sort(key=lambda e: e["date"])
    return hist


def analyse(rec: dict, as_of: date, horizon: int,
            override: dict | None) -> dict | None:
    events = rec["events"]
    dates = sorted({e["date"] for e in events})
    if len(dates) < MIN_OBSERVATIONS:
        return {
            "canonical": rec["canonical"],
            "label": most_common_label(rec),
            "category": rec["category"],
            "status": "insufficient-history",
            "observations": len(dates),
            "note": f"only {len(dates)} purchase(s) on record",
        }

    intervals = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
    if not intervals:
        return None

    median_interval = statistics.median(intervals)
    if median_interval > MAX_INTERVAL_DAYS:
        return None

    # Median absolute deviation -> how regular is this item, really?
    mad = statistics.median([abs(i - median_interval) for i in intervals]) or 0.0
    regularity = 1.0 - min(1.0, mad / median_interval) if median_interval else 0.0

    last = dates[-1]
    days_since = (as_of - last).days
    due_on = last + timedelta(days=round(median_interval))

    reason = "observed purchase cycle"
    if override:
        if override.get("have_until"):
            forced = d(override["have_until"])
            if forced > due_on:
                due_on, reason = forced, "manual pantry override"
        elif override.get("packs_on_hand"):
            forced = as_of + timedelta(
                days=round(median_interval * float(override["packs_on_hand"])))
            if forced > due_on:
                due_on, reason = forced, (
                    f"{override['packs_on_hand']} pack(s) on hand")

    due_in = (due_on - as_of).days
    typical_qty = statistics.median([e["qty"] for e in events]) or 1
    last_price = events[-1]["unit_price"]

    overdue = -due_in
    lapsed = (overdue > median_interval * LAPSE_FACTOR
              and overdue > LAPSE_MIN_DAYS)

    if lapsed:
        # Distinguish "we are due for this" from "we stopped buying this".
        # Both look identical to a pure interval forecast; only the size of
        # the overshoot tells them apart.
        status = "lapsed"
    elif due_in <= horizon:
        status = "order" if regularity >= 0.4 else "check"
    elif due_in <= horizon + 7:
        status = "soon"
    else:
        status = "ok"

    return {
        "canonical": rec["canonical"],
        "label": most_common_label(rec),
        "category": rec["category"],
        "is_staple": rec["is_staple"],
        "status": status,
        "observations": len(dates),
        "median_interval_days": round(median_interval),
        "interval_spread_days": round(mad),
        "regularity": round(regularity, 2),
        "last_purchased": last.isoformat(),
        "days_since_last": days_since,
        "due_on": due_on.isoformat(),
        "due_in_days": due_in,
        "typical_qty": int(typical_qty),
        "last_unit_price": round(last_price, 2),
        "est_cost": round(last_price * typical_qty, 2),
        "reason": reason,
    }


def most_common_label(rec: dict) -> str:
    return max(rec["labels"].items(), key=lambda kv: kv[1])[0] if rec["labels"] \
        else rec["canonical"]


def forecast(orders: list[dict], overrides: dict, as_of: date, horizon: int = 10,
             staples_only: bool = False) -> list[dict]:
    """Pure core of the staples forecast (no file I/O)."""
    hist = build_history(orders)
    results = []
    for key, rec in hist.items():
        if staples_only and not rec["is_staple"]:
            continue
        row = analyse(rec, as_of, horizon, overrides.get(key))
        if row:
            results.append(row)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--as-of", default=date.today().isoformat(),
                    help="the shop date, normally the coming Sunday")
    ap.add_argument("--horizon", type=int, default=10,
                    help="days of cover to buy ahead (delivery day + slack)")
    ap.add_argument("--staples-only", action="store_true",
                    help="restrict to household/personal-care departments")
    ap.add_argument("--verbose", action="store_true",
                    help="print the full forecast rather than a summary")
    args = ap.parse_args()

    data = Path(args.data)
    as_of = d(args.as_of)
    orders = json.loads((data / "orders.json").read_text(encoding="utf-8"))
    overrides = load_overrides(data / "pantry-state.json")

    results = forecast(orders, overrides, as_of, args.horizon, args.staples_only)

    order_now = sorted([r for r in results if r["status"] == "order"],
                       key=lambda r: r["due_in_days"])
    check = sorted([r for r in results if r["status"] == "check"],
                   key=lambda r: r["due_in_days"])
    soon = sorted([r for r in results if r["status"] == "soon"],
                  key=lambda r: r["due_in_days"])
    lapsed = sorted([r for r in results if r["status"] == "lapsed"],
                    key=lambda r: r["days_since_last"])

    (data / "staples-forecast.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")

    thin = [r for r in results if r["status"] == "insufficient-history"]
    lines = [f"# Staples due as at {as_of.isoformat()}",
             "",
             f"Horizon: {args.horizon} days. "
             f"{len(order_now)} to order, {len(check)} to confirm, "
             f"{len(soon)} coming up, {len(lapsed)} lapsed, "
             f"{len(thin)} with too little history to call.", ""]

    def table(title: str, rows: list[dict], note: str) -> None:
        if not rows:
            return
        lines.extend([f"## {title}", "", f"_{note}_", "",
                      "| Item | Canonical | Qty | Cycle | Last bought | Due | ~$ |",
                      "|---|---|---|---|---|---|---|"])
        for r in rows:
            lines.append(
                f"| {r['label']} | `{r['canonical']}` | {r['typical_qty']} | "
                f"{r['median_interval_days']}d ±{r['interval_spread_days']} | "
                f"{r['last_purchased']} ({r['days_since_last']}d ago) | "
                f"{r['due_on']} | {r['est_cost']:.2f} |")
        lines.append("")

    table("Order this week", order_now,
          "Regular cycle, due on or before the horizon.")
    table("Confirm before ordering", check,
          "Irregular buying pattern - the model is not confident. Ask before adding.")
    table("Next week or two", soon, "Not yet due; listed so nothing surprises you.")
    table("Lapsed - probably no longer bought", lapsed,
          "Far past due on their old cycle. Treated as dropped from the shop, "
          "not as overdue. Say so if any of these should come back.")

    est = sum(r["est_cost"] for r in order_now)
    lines.append(f"**Estimated staples spend: ${est:.2f}**")

    (data / "staples-forecast.md").write_text("\n".join(lines) + "\n",
                                              encoding="utf-8")
    if args.verbose:
        print("\n".join(lines))
    else:
        # Printing the whole forecast into the conversation is what makes a
        # session expensive - it is re-read on every later turn. The file has
        # the detail; stdout gets the decision.
        print("Staples as at %s: %d to order, %d to confirm, %d lapsed, "
              "%d too thin to call (est $%.2f)"
              % (as_of.isoformat(), len(order_now), len(check), len(lapsed),
                 len(thin), est))
        if order_now:
            print("Ordering: " + ", ".join(r["label"][:32] for r in order_now))
        print("Detail: staples-forecast.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
