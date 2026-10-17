"""One-tap "we already have this" switches for the kitchen tablet."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import QUICK_ITEMS
from .coordinator import KitchenPlannerCoordinator
from .entity import KitchenPlannerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator: KitchenPlannerCoordinator = entry.runtime_data
    async_add_entities(HaveItSwitch(coordinator, key, name)
                       for key, name in QUICK_ITEMS.items())


class HaveItSwitch(KitchenPlannerEntity, SwitchEntity):
    """On = one pack is already in the house for the coming week."""

    _attr_icon = "mdi:fridge-outline"

    def __init__(self, coordinator: KitchenPlannerCoordinator, canonical: str,
                 name: str) -> None:
        super().__init__(coordinator, f"have_{canonical}")
        self._canonical = canonical
        self._attr_translation_key = None
        self._attr_name = f"Have {name.lower()}"

    def _entry(self) -> dict[str, Any] | None:
        for item in (self.coordinator.data or {}).get("pantry", {}).get("have", []):
            if item["canonical"] == self._canonical:
                return item
        return None

    @property
    def is_on(self) -> bool:
        return self._entry() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        e = self._entry()
        return {"canonical": self._canonical, **({"amount": e["amount"], "until": e["until"]} if e else {})}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_on_hand(self._canonical, packs=1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_clear_on_hand(self._canonical)
