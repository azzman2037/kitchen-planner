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
