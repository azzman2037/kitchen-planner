# Kitchen Planner

A Home Assistant integration that turns the week's dinners in **Mealie** into a
Coles-ready shopping list: ingredients merged across recipes and rounded to
whole packs, household staples predicted from past orders, and the perishable
leftovers the week's packs will create.

It runs the same planning engine as the `coles-weekly-planner` scripts, so a
list built in Home Assistant matches one built from the command line.

## What you get

| Entity | What it shows |
|---|---|
| `sensor.kitchen_planner_dinners_planned` | Dinners on the Mealie plan for the coming Monday to Sunday, with the day list and any recipes the planner cannot map yet |
| `sensor.kitchen_planner_estimated_total` | Estimated spend in AUD, recipes plus staples. `is_floor: true` means some lines have no price on record |
| `sensor.kitchen_planner_staples_due` | Staples due for the Sunday pickup, from each item's own purchase cycle |
| `sensor.kitchen_planner_leftovers` | Perishable, unfreezable surplus the chosen packs leave (the half tub of cream) |
| `todo.kitchen_planner_coles_order` | The order as a to-do list. Generated lines rebuild automatically; ticks, removals and hand-added items are kept for the week |
| `sensor.kitchen_planner_in_the_house` | Items marked as already in the house (`have`), plus the items the household has stopped buying (`not_buying`) |
| `sensor.kitchen_planner_weekly_spend` | Last order's total, with every order's total in `history` and `average_last_8` |
| `switch.kitchen_planner_have_*` | One-tap "we have one already" for ten common perishables. On = one pack in the house until the end of the planning week |
| `select.kitchen_planner_week` | Which Monday-to-Sunday everything above shows: `this_week`, `next_week` (default, the week the weekend shop is for), `in_2_weeks`, `in_3_weeks`. Ticks and removals on the order list are kept per week |
| `button.kitchen_planner_refresh` | Rebuild now. Also `kitchen_planner.refresh` |

### Services

- `kitchen_planner.set_on_hand` (`canonical`, `packs` or `qty`+`unit`, optional `until`) and `kitchen_planner.clear_on_hand`: write `pantry-state.json` with a `.bak`. Entries expire at the end of the planning week; older entries without an expiry stay until cleared.
- `kitchen_planner.import_file`: replace one data file from gzip+base64 JSON, keeping `<name>.bak-<timestamp>`.
- `kitchen_planner.refresh`.

Once an order collected on the Friday to Sunday before the planning week is in
`orders.json`, the week counts as ordered: the to-do list shows that order
(every line ticked), the total is the real one and nothing is due. A Mealie
note titled with a planner recipe's name ("Chicken Cacciatore") counts as that
recipe.

The week shown is chosen with `select.kitchen_planner_week`, counted from the
week containing today, so "Next week" on a Saturday is the week the weekend
pickup feeds, and the dates move on by themselves.

## Data

Household data is not in this repository. The integration reads it from a
folder under `/config` (default `/config/kitchen_planner/`):

- required: `recipes.json`, `pack-catalogue.json`, `mealie-map.json`
- optional: `orders.json`, `pantry-state.json`, `product-map.json`,
  `rotation-history.json`

`mealie-map.json` maps each planner recipe id to its Mealie slug. A Mealie
recipe that is not in the map still appears on the plan but buys nothing, and
is listed under `unmapped`.

## Install

1. HACS → Custom repositories → `https://github.com/azzman2037/kitchen-planner` (Integration).
2. Download **Kitchen Planner**, restart Home Assistant.
3. Copy the data files into `/config/kitchen_planner/`.
4. Settings → Devices & services → Add integration → **Kitchen Planner**, pick
   the Mealie instance and the household serving size.

## Development

The files in `custom_components/kitchen_planner/engine/` are vendored verbatim
from `coles-weekly-planner/scripts`. Change them there, check the CLI output,
then copy them across.

```bash
pip install pytest-homeassistant-custom-component
pytest
```
