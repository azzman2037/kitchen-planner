"""Week picker: which Monday-to-Sunday the dashboard plans and shops for."""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import WEEK_OPTIONS
from .coordinator import KitchenPlannerCoordinator
from .entity import KitchenPlannerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([WeekSelect(entry.runtime_data, "week")])


class WeekSelect(KitchenPlannerEntity, SelectEntity, RestoreEntity):
    """The family's choice survives a restart; the dates move on by themselves."""

    _attr_icon = "mdi:calendar-week"
    _attr_options = list(WEEK_OPTIONS)

    def __init__(self, coordinator: KitchenPlannerCoordinator, key: str) -> None:
        super().__init__(coordinator, key)

    @property
    def available(self) -> bool:
        return True  # switching weeks must work even if the last refresh failed

    @property
    def current_option(self) -> str:
        return self.coordinator.week

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"weeks": {k: v.isoformat() for k, v in self.coordinator.week_starts().items()}}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in WEEK_OPTIONS and last.state != self.coordinator.week:
            await self.coordinator.async_set_week(last.state)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_week(option)
