"""Shared helpers: unit canonicalisation, ingredient normalisation, pack maths.

All quantities are reduced to one of three canonical units:
    ml   - anything liquid / volumetric
    g    - anything by weight
    each - countable items (eggs, lemons, tins, rolls)

Nothing in this module talks to the network or to Claude. It is deterministic
maths so the weekly plan is reproducible and auditable.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from itertools import product as iterproduct
from pathlib import Path

# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

# Australian metric cup/spoon conventions (AS 1000). US recipes differ - if a
# recipe is sourced from a US site, convert it at ingest time, not here.
VOLUME_ML = {
    "ml": 1.0, "millilitre": 1.0, "milliliter": 1.0, "mls": 1.0,
    "l": 1000.0, "litre": 1000.0, "liter": 1000.0, "lt": 1000.0,
    "tsp": 5.0, "teaspoon": 5.0, "teaspoons": 5.0,
    "tbsp": 20.0, "tablespoon": 20.0, "tablespoons": 20.0,  # AU tbsp = 20ml
    "cup": 250.0, "cups": 250.0,
    "fl oz": 29.57, "floz": 29.57,
}

WEIGHT_G = {
    "g": 1.0, "gram": 1.0, "grams": 1.0, "gm": 1.0,
    "kg": 1000.0, "kilo": 1000.0, "kilos": 1000.0, "kilogram": 1000.0,
    "mg": 0.001,
    "oz": 28.35, "ounce": 28.35, "lb": 453.6, "pound": 453.6,
}

COUNT_EACH = {
    "each", "ea", "unit", "units", "piece", "pieces", "pc",
    "clove", "cloves", "sprig", "sprigs", "stalk", "stalks",
    "rasher", "rashers", "fillet", "fillets", "slice", "slices",
    "tin", "tins", "can", "cans", "jar", "jars", "punnet", "punnets",
    "bunch", "bunches", "head", "heads", "pack", "packs", "pk",
    "roll", "rolls", "sheet", "sheets", "bottle", "bottles",
    "loaf", "loaves", "handful", "pinch",
}

# Rough weights so "2 brown onions" can be reconciled against a 1kg bag.
# Extend this table as your recipes demand - a missing entry is not fatal, the
# item simply stays in `each` and is ordered by count.
EACH_TO_G = {
    "onion": 150, "brown onion": 150, "red onion": 150, "white onion": 150,
    "garlic": 5, "garlic clove": 5,
    "carrot": 100, "potato": 180, "sweet potato": 300,
    "tomato": 120, "capsicum": 180, "zucchini": 200, "eggplant": 350,
    "lemon": 100, "lime": 70, "orange": 180, "apple": 150, "banana": 120,
    "egg": 55, "chicken breast": 220, "chicken thigh": 120,
    "corn cob": 250, "avocado": 200, "cucumber": 200, "celery stalk": 40,
    "broccolini": 175,
}


class UnitError(ValueError):
    pass


def canonical_unit(unit: str) -> str:
    """Return 'ml', 'g' or 'each' for a raw unit string."""
    u = (unit or "each").strip().lower().rstrip(".")
    if u in VOLUME_ML:
        return "ml"
    if u in WEIGHT_G:
        return "g"
    if u in COUNT_EACH or u == "":
        return "each"
    raise UnitError(f"unknown unit: {unit!r}")


def to_canonical(qty: float, unit: str) -> tuple[float, str]:
    """Convert (qty, unit) into (qty, canonical_unit)."""
    u = (unit or "each").strip().lower().rstrip(".")
    if u in VOLUME_ML:
        return qty * VOLUME_ML[u], "ml"
    if u in WEIGHT_G:
        return qty * WEIGHT_G[u], "g"
    if u in COUNT_EACH or u == "":
        return qty, "each"
    raise UnitError(f"unknown unit: {unit!r}")


def each_to_grams(name: str, count: float) -> float | None:
    """Best-effort conversion of a countable item to grams. None if unknown."""
    key = normalise_name(name)
    for candidate, grams in sorted(EACH_TO_G.items(), key=lambda kv: -len(kv[0])):
        if candidate in key:
            return count * grams
    return None


# --------------------------------------------------------------------------
# Ingredient naming
# --------------------------------------------------------------------------

# Words that describe preparation, not identity. Stripping these lets
# "finely chopped brown onion" and "onion, diced" collapse to one key.
PREP_WORDS = {
    "finely", "roughly", "coarsely", "thinly", "thickly", "freshly",
    "chopped", "diced", "sliced", "minced", "crushed", "grated", "shredded",
    "julienned", "cubed", "halved", "quartered", "peeled", "deseeded",
    "trimmed", "drained", "rinsed", "softened", "melted", "beaten", "whisked",
    "cooked", "uncooked", "raw", "fresh", "dried", "frozen", "canned", "tinned",
    "lightly", "whipped", "ripe",
    "optional", "to", "serve", "garnish", "taste", "plus", "extra", "for",
    "large", "small", "medium", "approx", "about", "good", "quality",
}

SIZE_TOKEN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:g|kg|mg|ml|l|lt|litre|liter|pk|pack|each|ea)\b",
    re.IGNORECASE,
)

# Coles brand prefixes that get in the way of matching a shopping-list line
# back to a recipe ingredient.
BRAND_NOISE = re.compile(
    r"^(coles|coles\s+finest|coles\s+simply|coles\s+kitchen|coles\s+perform|"
    r"woolworths|homebrand|community\s+co)\b\s*",
    re.IGNORECASE,
)


def normalise_name(name: str) -> str:
    """Lowercase, de-accent, strip brand/size/prep noise. Order-insensitive."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = BRAND_NOISE.sub("", s)
    s = SIZE_TOKEN.sub(" ", s)
    s = re.sub(r"[^a-z\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in PREP_WORDS]
    # Naive singularisation - good enough for grocery nouns.
    tokens = [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t
              for t in tokens]
    return " ".join(sorted(set(tokens))) if tokens else s.strip()


def load_aliases(path: Path | str) -> dict[str, str]:
    """Load data/aliases.json mapping normalised names -> canonical ingredient id.

    The alias file is the single place where "thickened cream", "pure cream"
    and "Coles Thickened Cream 300mL" are declared to be the same thing.
    """
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    # Keys beginning with '_' are documentation, not data.
    return {normalise_name(k): v for k, v in raw.items()
            if not k.startswith("_")}


def canonical_id(name: str, aliases: dict[str, str]) -> str:
    """Resolve a free-text ingredient or product name to a canonical id.

    Deliberately biased towards precision over recall. Under-merging is
    visible - it shows up as two canonical ids for one thing in the ingest
    report, and you fix it by adding an alias. Over-merging is silent and
    corrupts the shopping list, so it is not allowed to happen by accident.

    Three rules, in order:

    1. Exact match on the normalised name. This is what catches recipe
       ingredients, which are short: "garlic" and "thickened cream" reduce to
       exactly their alias keys.
    2. Multi-token alias whose tokens are all present. Comparing token SETS
       (not substrings) matters because normalise_name sorts tokens, so
       "thickened cream, lightly whipped" no longer contains "cream thickened"
       as a substring. The most specific alias wins: "chicken thigh" beats
       "chicken breast" on a name containing both.
    3. Otherwise the name becomes its own id.

    Single-token aliases are NOT applied by rule 2, and that restriction is
    the whole point. "garlic" as a substring rule would swallow "Green Olives
    with Chilli Garlic"; "milk" would swallow oat and almond milk; "butter"
    would swallow peanut butter. Long product names need an explicit
    multi-word alias, which the ingest report prompts you to write.
    """
    key = normalise_name(name)
    if key in aliases:
        return aliases[key]
    tokens = set(key.split())
    best, best_len = None, 1  # strictly more than one token to qualify
    for alias_key, canonical in aliases.items():
        alias_tokens = set(alias_key.split())
        if len(alias_tokens) <= 1 or not alias_tokens <= tokens:
            continue
        if len(alias_tokens) > best_len:
            best, best_len = canonical, len(alias_tokens)
    if best:
        return best
    return key.replace(" ", "_") or "unknown"


# --------------------------------------------------------------------------
# Pack optimisation
# --------------------------------------------------------------------------


@dataclass
class Pack:
    """One buyable Coles product."""
    product: str
    size: float            # in canonical units
    unit: str              # ml | g | each
    price: float | None = None
    perishable: bool = False
    freezable: bool = False
    note: str = ""

    @property
    def unit_price(self) -> float | None:
        if self.price is None or self.size == 0:
            return None
        return self.price / self.size


@dataclass
class PackSolution:
    counts: dict[str, int] = field(default_factory=dict)   # product -> qty
    total_size: float = 0.0
    required: float = 0.0
    cost: float | None = None
    unit: str = "each"

    @property
    def surplus(self) -> float:
        return self.total_size - self.required

    @property
    def waste_fraction(self) -> float:
        if self.total_size == 0:
            return 0.0
        return self.surplus / self.total_size

    def describe(self) -> str:
        bits = [f"{n} x {p}" for p, n in sorted(self.counts.items()) if n]
        cost = f" (${self.cost:.2f})" if self.cost is not None else ""
        return ", ".join(bits) + cost


def optimise_packs(
    required: float,
    packs: list[Pack],
    max_waste: float = 0.35,
    max_units_per_pack: int = 6,
) -> PackSolution | None:
    """Choose the cheapest combination of packs that covers `required`.

    This is the "100ml of cream here + 300ml there = one 600ml tub" step.

    Objective, in order:
      1. cover the requirement (hard constraint)
      2. respect `max_waste` where the item is perishable and cannot be frozen
      3. minimise cost
      4. minimise surplus

    `max_waste` is applied as a *soft* constraint: if no combination satisfies
    it, the tightest-fitting combination is returned anyway, because being
    short of an ingredient on a Wednesday night is worse than 40% surplus.
    """
    if required <= 0 or not packs:
        return None

    units = {p.unit for p in packs}
    if len(units) > 1:
        raise UnitError(f"packs mix units: {units}")
    unit = units.pop()

    ranges = [range(0, max_units_per_pack + 1) for _ in packs]
    best: PackSolution | None = None
    best_fallback: PackSolution | None = None

    for combo in iterproduct(*ranges):
        total = sum(n * p.size for n, p in zip(combo, packs))
        if total < required - 1e-9:
            continue
        if sum(combo) == 0:
            continue
        prices = [p.price for n, p in zip(combo, packs) if n]
        cost = (sum(n * p.price for n, p in zip(combo, packs))
                if all(pr is not None for pr in prices) else None)
        sol = PackSolution(
            counts={p.product: n for n, p in zip(combo, packs) if n},
            total_size=total,
            required=required,
            cost=cost,
            unit=unit,
        )
        if _within_waste_cap(packs, combo, sol, max_waste):
            if best is None or _better(sol, best):
                best = sol
        if best_fallback is None or _better(sol, best_fallback):
            best_fallback = sol

    return best or best_fallback


def _within_waste_cap(
    packs: list[Pack],
    combo: tuple[int, ...],
    sol: PackSolution,
    max_waste: float,
) -> bool:
    """Waste cap binds only on perishable goods that cannot be frozen.

    A 5kg bag of rice at 80% surplus is fine - it keeps. A 600mL tub of cream
    where the week only needs 150mL is not, so that combination is excluded
    unless nothing else covers the requirement.
    """
    perishable = any(
        p.perishable and not p.freezable for n, p in zip(combo, packs) if n
    )
    if not perishable:
        return True
    return sol.waste_fraction <= max_waste + 1e-9


def _better(a: PackSolution, b: PackSolution) -> bool:
    """Cheaper wins; if cost is unknown or equal, less surplus wins."""
    if a.cost is not None and b.cost is not None:
        if abs(a.cost - b.cost) > 0.005:
            return a.cost < b.cost
    return a.surplus < b.surplus


def round_up_to_pack(required: float, sizes: list[float]) -> float:
    """Cheap fallback when no priced catalogue exists: smallest total >= required
    using the largest sensible pack first, then top up."""
    if not sizes:
        return required
    sizes = sorted(sizes, reverse=True)
    remaining, total = required, 0.0
    for s in sizes:
        while remaining >= s:
            total += s
            remaining -= s
    if remaining > 1e-9:
        total += min(s for s in sizes if s >= remaining) if any(
            s >= remaining for s in sizes) else sizes[0]
    return total


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------


def pretty_qty(qty: float, unit: str) -> str:
    """Human-readable quantity: 1500 g -> '1.5 kg', 106.6667 g -> '107 g'.

    Scaling a serves-6 recipe to serves-4 produces long decimals that are
    noise on a shopping list - nobody weighs 106.667 g of rocket. Grams and
    millilitres round to whole numbers, kilos and litres to two decimals.
    """
    if unit == "g" and qty >= 1000:
        return f"{round(qty / 1000, 2):g} kg"
    if unit == "ml" and qty >= 1000:
        return f"{round(qty / 1000, 2):g} L"
    if unit == "each":
        return f"{round(qty, 1):g}"
    return f"{round(qty):g} {unit}"


def dump(obj) -> str:
    def default(o):
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
        if isinstance(o, set):
            return sorted(o)
        return str(o)
    return json.dumps(obj, indent=2, default=default, ensure_ascii=False)
