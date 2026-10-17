"""Coordinator: reads the Mealie dinner plan and runs the planner engine."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DATA_DIR,
    CONF_HORIZON,
    CONF_MEALIE_ENTRY,
    CONF_SERVES,
    DEFAULT_DATA_DIR,
    DEFAULT_HORIZON,
    DEFAULT_SERVES,
    DOMAIN,
    UPDATE_INTERVAL,
)
from .planner import (
    DataMissing,
    build_week,
    clear_on_hand,
    compute,
    load_data,
    pack_for,
    set_on_hand,
    upcoming_week_start,
)

_LOGGER = logging.getLogger(__name__)

MEALIE = "mealie"


class KitchenPlannerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Rebuilds the week's shopping list on a timer and on demand."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=UPDATE_INTERVAL)
        conf = {**entry.data, **entry.options}
        self.serves = int(conf.get(CONF_SERVES, DEFAULT_SERVES))
        self.horizon = int(conf.get(CONF_HORIZON, DEFAULT_HORIZON))
        self.mealie_entry_id: str | None = conf.get(CONF_MEALIE_ENTRY)
        self.data_dir = Path(hass.config.path(conf.get(CONF_DATA_DIR, DEFAULT_DATA_DIR)))
        self.catalogue: dict[str, Any] = {}

    def _mealie_entry(self) -> str:
        entries = [e for e in self.hass.config_entries.async_entries(MEALIE)
                   if e.state is ConfigEntryState.LOADED]
        for e in entries:
            if e.entry_id == self.mealie_entry_id:
                return e.entry_id
        if entries:
            return entries[0].entry_id
        raise UpdateFailed("Mealie is not set up or not loaded")

    async def _async_update_data(self) -> dict[str, Any]:
        week_start = upcoming_week_start(dt_util.now().date())
        week_end = week_start + timedelta(days=6)

        try:
            data = await self.hass.async_add_executor_job(load_data, self.data_dir)
        except DataMissing as err:
            raise UpdateFailed(
                f"Planner data missing in {self.data_dir}: {err}") from err

        try:
            resp = await self.hass.services.async_call(
                MEALIE, "get_mealplan",
                {"config_entry_id": self._mealie_entry(),
                 "start_date": week_start.isoformat(),
                 "end_date": week_end.isoformat()},
                blocking=True, return_response=True)
        except HomeAssistantError as err:
            raise UpdateFailed(f"Could not read the Mealie meal plan: {err}") from err

        self.catalogue = data["catalogue"]
        mealplans = (resp or {}).get("mealplan") or []
        recipes = {r["id"]: r for r in data["recipes"]}
        plan = build_week(mealplans, data["mealie_map"], recipes, week_start)
        return await self.hass.async_add_executor_job(
            lambda: compute(data, plan, serves=self.serves, horizon=self.horizon))

    # --- pantry writes ---------------------------------------------------

    def _write_pantry(self, change) -> None:
        """Read-modify-write pantry-state.json atomically, keeping a .bak."""
        path = self.data_dir / "pantry-state.json"
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        updated = change(current)
        if path.exists():
            (self.data_dir / "pantry-state.json.bak").write_bytes(path.read_bytes())
        tmp = self.data_dir / ".pantry-state.json.tmp"
        tmp.write_text(json.dumps(updated, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    async def async_set_on_hand(self, canonical: str, *, packs: float | None = None,
                                qty: float | None = None, unit: str | None = None,
                                until: date | None = None) -> None:
        today = dt_util.now().date()
        week_start = upcoming_week_start(today)
        until = until or week_start + timedelta(days=6)
        if qty is None:
            pack = pack_for(self.catalogue, canonical)
            if pack is None:
                raise HomeAssistantError(
                    f"No pack size on record for {canonical}; give qty and unit instead")
            qty, unit = pack[0] * (packs or 1), pack[1]
        await self.hass.async_add_executor_job(
            self._write_pantry,
            lambda p: set_on_hand(p, canonical, float(qty), unit or "each", until, today))
        await self.async_refresh()  # immediate: a tapped switch must flip now

    async def async_clear_on_hand(self, canonical: str) -> None:
        await self.hass.async_add_executor_job(
            self._write_pantry, lambda p: clear_on_hand(p, canonical))
        await self.async_refresh()
