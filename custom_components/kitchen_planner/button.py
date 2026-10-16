"""Refresh button for the kitchen tablet."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import KitchenPlannerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([RefreshButton(entry.runtime_data, "refresh")])


class RefreshButton(KitchenPlannerEntity, ButtonEntity):
    _attr_icon = "mdi:refresh"

    @property
    def available(self) -> bool:
        return True  # must work even when the last refresh failed

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
