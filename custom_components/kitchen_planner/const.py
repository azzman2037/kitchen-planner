"""Constants for Kitchen Planner."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "kitchen_planner"

CONF_MEALIE_ENTRY = "mealie_entry_id"
CONF_SERVES = "serves"
CONF_HORIZON = "horizon_days"
CONF_DATA_DIR = "data_dir"

DEFAULT_SERVES = 4
DEFAULT_HORIZON = 10
DEFAULT_DATA_DIR = "kitchen_planner"

UPDATE_INTERVAL = timedelta(minutes=10)
SERVICE_REFRESH = "refresh"
SERVICE_IMPORT_FILE = "import_file"
SERVICE_SET_ON_HAND = "set_on_hand"
SERVICE_CLEAR_ON_HAND = "clear_on_hand"

# One-tap "in the house" switches: the perishables most shared across the
# recipe library (from recipes.json usage counts, 17 Oct 2026).
QUICK_ITEMS: dict[str, str] = {
    "herb_parsley": "Parsley",
    "herb_coriander": "Coriander",
    "herb_basil": "Basil",
    "cream_thickened": "Thickened cream",
    "yoghurt_greek": "Greek yoghurt",
    "tomato_cherry": "Cherry tomatoes",
    "cheese_feta": "Feta",
    "lemon": "Lemons",
    "mushroom": "Mushrooms",
    "zucchini": "Zucchini",
}
